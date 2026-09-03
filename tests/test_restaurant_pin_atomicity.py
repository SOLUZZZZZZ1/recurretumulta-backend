from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

import ops_restaurant_reservations as reservations


class _Result:
    def __init__(self, *, row=None, scalar=None):
        self._row = row
        self._scalar = scalar

    def fetchone(self):
        return self._row

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class _RestaurantState:
    def __init__(self, *, active: bool = True, pin: str = "old-pin-123"):
        self.restaurant_id = "rest_001"
        self.active = active
        self.pin = pin


class _Connection:
    def __init__(self, state: _RestaurantState):
        self.state = state
        self.calls: list[tuple[str, dict, int]] = []
        self.transaction_number = 0
        self.share_lock = False

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        params = dict(parameters or {})
        self.calls.append((sql, params, self.transaction_number))

        if "UPDATE restaurants AS target" in sql:
            valid = (
                self.state.active
                and params.get("rid") == self.state.restaurant_id
                and params.get("current_pin") == self.state.pin
            )
            if not valid:
                return _Result(row=None)
            self.state.pin = params["new_pin"]
            return _Result(row=(self.state.restaurant_id,))

        if "SELECT pin_hash FROM restaurants" in sql:
            self.share_lock = True
            if (
                self.state.active
                and params.get("rid") == self.state.restaurant_id
            ):
                return _Result(row=(self.state.pin,))
            return _Result(row=None)

        if "SELECT crypt(:pin, :hash) = :hash" in sql:
            return _Result(scalar=params.get("pin") == params.get("hash"))

        if "SELECT crypt(:pin, gen_salt('bf', 12))" in sql:
            return _Result(scalar="dummy-hash")

        if "UPDATE restaurant_reservations" in sql:
            if not self.share_lock:
                raise AssertionError("La mutación ocurrió sin mantener el lock del PIN")
            return _Result(scalar=str(RESERVATION_ID))

        raise AssertionError(f"SQL inesperado: {sql}")


class _Engine:
    def __init__(self, connection: _Connection):
        self.connection = connection
        self.begin_count = 0

    @contextmanager
    def begin(self):
        self.begin_count += 1
        self.connection.transaction_number = self.begin_count
        self.connection.share_lock = False
        try:
            yield self.connection
        finally:
            self.connection.share_lock = False


RESERVATION_ID = UUID("3f04a66a-2daa-4214-86e0-b80cc966e713")


class RestaurantPinAtomicityTest(unittest.TestCase):
    def test_status_mutation_verifies_pin_and_writes_in_one_transaction(self):
        state = _RestaurantState()
        connection = _Connection(state)
        engine = _Engine(connection)

        with patch.object(reservations, "get_engine", return_value=engine):
            result = reservations.mark_cancel(
                RESERVATION_ID,
                "rest_001",
                x_reservas_pin="old-pin-123",
            )

        self.assertEqual(result["status"], "cancelada")
        self.assertEqual(engine.begin_count, 1)
        self.assertEqual({call[2] for call in connection.calls}, {1})
        self.assertIn("FOR SHARE", connection.calls[0][0])
        self.assertIn("SELECT crypt(:pin, :hash) = :hash", connection.calls[1][0])
        self.assertIn("UPDATE restaurant_reservations", connection.calls[2][0])

    def test_revoked_restaurant_cannot_mutate_after_pin_check(self):
        state = _RestaurantState(active=False)
        connection = _Connection(state)
        engine = _Engine(connection)

        with (
            patch.object(reservations, "get_engine", return_value=engine),
            self.assertRaises(HTTPException) as rejected,
        ):
            reservations.mark_cancel(
                RESERVATION_ID,
                "rest_001",
                x_reservas_pin="old-pin-123",
            )

        self.assertEqual(rejected.exception.status_code, 401)
        self.assertEqual(
            rejected.exception.detail,
            reservations._INVALID_RESTAURANT_CREDENTIALS,
        )
        self.assertFalse(
            any("UPDATE restaurant_reservations" in call[0] for call in connection.calls)
        )

    def test_wrong_pin_inactive_and_unknown_restaurant_are_indistinguishable(self):
        failures = []
        for state, restaurant_id, supplied_pin in (
            (_RestaurantState(active=True), "rest_001", "wrong-pin"),
            (_RestaurantState(active=False), "rest_001", "old-pin-123"),
            (_RestaurantState(active=True), "rest_999", "old-pin-123"),
        ):
            connection = _Connection(state)
            engine = _Engine(connection)
            with patch.object(reservations, "get_engine", return_value=engine):
                with self.assertRaises(HTTPException) as rejected:
                    reservations.mark_cancel(
                        RESERVATION_ID,
                        restaurant_id,
                        x_reservas_pin=supplied_pin,
                    )
            failures.append(
                (rejected.exception.status_code, rejected.exception.detail)
            )

        self.assertEqual(failures, [failures[0]] * len(failures))

    def test_same_old_pin_can_win_rotation_only_once(self):
        state = _RestaurantState()
        connection = _Connection(state)
        engine = _Engine(connection)

        with patch.object(reservations, "get_engine", return_value=engine):
            first = reservations.change_restaurant_pin(
                reservations.ChangePinBody(
                    restaurant_id="rest_001",
                    current_pin="old-pin-123",
                    new_pin="first-pin-456",
                )
            )
            with self.assertRaises(HTTPException) as stale:
                reservations.change_restaurant_pin(
                    reservations.ChangePinBody(
                        restaurant_id="rest_001",
                        current_pin="old-pin-123",
                        new_pin="second-pin-789",
                    )
                )

        self.assertTrue(first["ok"])
        self.assertEqual(state.pin, "first-pin-456")
        self.assertEqual(stale.exception.status_code, 401)
        rotation_sql = [
            call[0]
            for call in connection.calls
            if "UPDATE restaurants AS target" in call[0]
        ]
        self.assertEqual(len(rotation_sql), 2)
        self.assertTrue(
            all(
                "crypt(:current_pin, target.pin_hash) = target.pin_hash" in sql
                for sql in rotation_sql
            )
        )
        self.assertTrue(all("WITH credential AS MATERIALIZED" in sql for sql in rotation_sql))
        self.assertTrue(all("timing_probe IS NOT NULL" in sql for sql in rotation_sql))
        self.assertTrue(all("target.pin_hash = credential.pin_hash" in sql for sql in rotation_sql))
        self.assertTrue(all("target.active = TRUE" in sql for sql in rotation_sql))
        self.assertTrue(all("RETURNING target.id" in sql for sql in rotation_sql))


if __name__ == "__main__":
    unittest.main()
