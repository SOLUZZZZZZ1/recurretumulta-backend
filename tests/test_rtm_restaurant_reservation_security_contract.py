from __future__ import annotations

import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from ops_restaurant_reservations import ReservationCreate, ReservationUpdate


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "ops_restaurant_reservations.py"


class RestaurantReservationSecurityContractTest(unittest.TestCase):
    def test_admin_returns_canonical_browser_route_without_hash_credentials(self):
        source = Path("ops_restaurant_reservations.py").read_text(encoding="utf-8")
        self.assertIn('f"/__reservas-restaurante?r={new_id}"', source)
        self.assertNotIn('/#__reservas-restaurante', source)

    @classmethod
    def setUpClass(cls):
        cls.source = ROUTER.read_text(encoding="utf-8")
        ast.parse(cls.source, filename=str(ROUTER))

    def test_update_binds_reservation_to_authenticated_restaurant(self):
        start = self.source.index("def update_reservation(")
        end = self.source.index("\ndef _set_status(", start)
        section = self.source[start:end]
        self.assertIn(
            "rid = _need_pin(conn, restaurant_id, x_reservas_pin)",
            section,
        )
        self.assertLess(
            section.index("with engine.begin() as conn:"),
            section.index("rid = _need_pin(conn,"),
        )
        self.assertIn('params["rid"] = rid', section)
        self.assertIn("WHERE id = CAST(:id AS uuid)", section)
        self.assertIn("AND restaurant_id = :rid", section)

    def test_status_mutations_bind_tenant_and_derive_actor_server_side(self):
        start = self.source.index("def _set_status(")
        section = self.source[start:]
        self.assertIn(
            "def _set_status(conn, res_id: str, restaurant_id: str, status: str):",
            section,
        )
        self.assertIn('actor = f"restaurant:{restaurant_id}"', section)
        self.assertIn("AND restaurant_id = :rid", section)
        self.assertEqual(
            section.count("rid = _need_pin(conn, restaurant_id, x_reservas_pin)"),
            3,
        )
        self.assertEqual(
            section.count("_set_status(conn, str(reservation_id), rid,"),
            3,
        )
        self.assertNotIn('alias="x-actor"', section)

        create_start = self.source.index("def create_reservation(")
        create_end = self.source.index("\ndef update_reservation(", create_start)
        create_section = self.source[create_start:create_end]
        self.assertIn('"by": f"restaurant:{rid}"', create_section)
        self.assertNotIn('"by": body.created_by', create_section)

    def test_create_is_idempotent_and_binds_replay_to_exact_payload(self):
        create_start = self.source.index("def create_reservation(")
        create_end = self.source.index("\ndef update_reservation(", create_start)
        create_section = self.source[create_start:create_end]
        self.assertIn('alias="Idempotency-Key"', create_section)
        self.assertIn("_reservation_id_from_idempotency", create_section)
        self.assertIn("ON CONFLICT (id) DO NOTHING", create_section)
        self.assertIn("payload_matches is not True", create_section)
        self.assertIn('"replayed": True', create_section)

    def test_new_restaurant_credentials_have_a_minimum_length(self):
        self.assertIn(
            "new_pin: str = Field(..., min_length=8, max_length=64)",
            self.source,
        )
        self.assertIn(
            "pin: str = Field(..., min_length=8, max_length=64)",
            self.source,
        )
        self.assertIn(
            "restaurant_id: str = Field(..., min_length=1, max_length=64)",
            self.source,
        )
        self.assertIn("def _validated_new_pin(value: str) -> str:", self.source)
        self.assertGreaterEqual(
            self.source.count('len(pin.encode("utf-8")) > 72'),
            2,
        )
        self.assertIn(
            'len(current_pin.encode("utf-8")) > 72',
            self.source,
        )
        self.assertEqual(self.source.count("_validated_new_pin(body."), 2)
        self.assertGreaterEqual(self.source.count("gen_salt('bf', 12)"), 3)

    def test_invalid_restaurant_and_pin_share_the_same_public_error(self):
        self.assertIn("_INVALID_RESTAURANT_CREDENTIALS", self.source)
        self.assertIn("def _dummy_pin_check(conn, pin: str) -> None:", self.source)
        self.assertIn("def _invalid_restaurant_credentials() -> None:", self.source)
        self.assertGreaterEqual(self.source.count("_dummy_pin_check("), 6)
        self.assertNotIn("PIN incorrecto.", self.source)
        self.assertNotIn("Restaurante no válido o inactivo.", self.source)

    def test_pin_checks_and_operations_share_one_transaction(self):
        self.assertIn("FOR SHARE", self.source)
        self.assertNotIn("def _need_pin(restaurant_id", self.source)

        for function_name in (
            "list_reservations",
            "create_reservation",
            "update_reservation",
            "mark_arrived",
            "mark_no_show",
            "mark_cancel",
        ):
            with self.subTest(function=function_name):
                start = self.source.index(f"def {function_name}(")
                next_route = self.source.find("\n@router.", start)
                section = self.source[start:] if next_route < 0 else self.source[start:next_route]
                self.assertIn("with engine.begin() as conn:", section)
                self.assertIn(
                    "_need_pin(conn, restaurant_id, x_reservas_pin)",
                    section,
                )

    def test_pin_rotation_is_one_current_hash_cas(self):
        start = self.source.index("def change_restaurant_pin(")
        end = self.source.index("\n# ============================================================\n# GET:", start)
        section = self.source[start:end]
        self.assertEqual(section.count("UPDATE restaurants AS target"), 1)
        self.assertIn(
            "crypt(:current_pin, target.pin_hash) = target.pin_hash",
            section,
        )
        self.assertIn("WITH credential AS MATERIALIZED", section)
        self.assertIn("timing_probe IS NOT NULL", section)
        self.assertIn("target.pin_hash = credential.pin_hash", section)
        self.assertIn("target.active = TRUE", section)
        self.assertIn("RETURNING target.id", section)
        self.assertEqual(section.count("with engine.begin() as conn:"), 1)

    def test_legacy_admin_secret_uses_constant_time_comparison(self):
        self.assertIn("hmac.compare_digest(", self.source)
        self.assertNotIn("x_admin_token.strip() != expected", self.source)

    def test_reservation_inputs_are_bounded_strict_and_typed(self):
        valid = {
            "reservation_date": "2026-09-03",
            "reservation_time": "14:30",
            "shift": "comida",
            "party_size": 2,
            "customer_name": "Cliente válido",
        }
        ReservationCreate(**valid)
        for changes in (
            {"customer_name": "x" * 161},
            {"party_size": 0},
            {"party_size": 51},
            {"shift": "madrugada"},
            {"phone": "<script>"},
            {"unexpected": "value"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ReservationCreate(**{**valid, **changes})
        with self.assertRaises(ValidationError):
            ReservationUpdate(extras_notes="x" * 501)


if __name__ == "__main__":
    unittest.main()
