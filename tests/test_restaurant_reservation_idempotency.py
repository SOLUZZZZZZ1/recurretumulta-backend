from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException

import ops_restaurant_reservations as reservations


class _Result:
    def __init__(self, scalar):
        self.scalar = scalar

    def scalar_one_or_none(self):
        return self.scalar


class _Connection:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        if not self.results:
            raise AssertionError("SQL inesperado")
        return _Result(self.results.pop(0))


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def _body():
    return reservations.ReservationCreate(
        reservation_date="2026-09-04",
        reservation_time="14:30",
        shift="comida",
        table_name="T-1",
        party_size=2,
        customer_name="Cliente de prueba",
        phone="+34 600 000 000",
        extras_notes="Sin exposición",
    )


class RestaurantReservationIdempotencyTest(unittest.TestCase):
    KEY = "3f04a66a-2daa-4214-86e0-b80cc966e713"

    def _create(self, connection):
        with (
            patch.object(reservations, "_need_pin", return_value="rest_001"),
            patch.object(reservations, "get_engine", return_value=_Engine(connection)),
        ):
            return reservations.create_reservation(
                _body(),
                "rest_001",
                x_reservas_pin="synthetic-pin",
                idempotency_key=self.KEY,
            )

    def test_first_attempt_inserts_deterministic_id_once(self):
        expected_id = reservations._reservation_id_from_idempotency(
            "rest_001", self.KEY
        )
        connection = _Connection(expected_id)

        result = self._create(connection)

        self.assertEqual(result, {"ok": True, "id": expected_id, "replayed": False})
        self.assertEqual(len(connection.calls), 1)
        self.assertIn("ON CONFLICT (id) DO NOTHING", connection.calls[0][0])
        self.assertEqual(connection.calls[0][1]["reservation_id"], expected_id)

    def test_exact_retry_returns_original_id_without_second_insert(self):
        expected_id = reservations._reservation_id_from_idempotency(
            "rest_001", self.KEY
        )
        connection = _Connection(None, True)

        result = self._create(connection)

        self.assertEqual(result, {"ok": True, "id": expected_id, "replayed": True})
        self.assertEqual(len(connection.calls), 2)
        self.assertIn("reservation_date = CAST(:d AS date)", connection.calls[1][0])

    def test_reusing_key_for_different_payload_fails_closed(self):
        connection = _Connection(None, False)
        with self.assertRaises(HTTPException) as rejected:
            self._create(connection)

        self.assertEqual(rejected.exception.status_code, 409)

    def test_key_is_strict_and_scoped_to_restaurant(self):
        with self.assertRaises(HTTPException) as rejected:
            reservations._reservation_id_from_idempotency("rest_001", "short")
        self.assertEqual(rejected.exception.status_code, 400)
        self.assertNotEqual(
            reservations._reservation_id_from_idempotency("rest_001", self.KEY),
            reservations._reservation_id_from_idempotency("rest_002", self.KEY),
        )


if __name__ == "__main__":
    unittest.main()
