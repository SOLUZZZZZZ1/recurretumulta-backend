from __future__ import annotations

from pathlib import Path
import unittest

import ops


ROOT = Path(__file__).resolve().parents[1]


class PublicServiceFamilyTest(unittest.TestCase):
    def resolve(
        self,
        *,
        department="claims",
        case_type="consumer",
        interested=None,
        customer_comment=None,
    ):
        return ops._public_service_family(
            department=department,
            case_type=case_type,
            interested=interested or {},
            customer_comment=customer_comment,
        )

    def test_structured_family_has_priority(self):
        self.assertEqual(
            self.resolve(
                interested={"public_service_family": "seguros"},
                customer_comment="Área pública seleccionada: Bancos",
            ),
            "seguros",
        )

    def test_legacy_public_marker_is_recovered(self):
        examples = {
            "Área pública seleccionada: Tráfico": "trafico",
            "Área pública seleccionada: Deudas y ASNEF": "morosidad",
            "ÁREA PÚBLICA SELECCIONADA: ENERGÍA": "energia",
            "Área pública seleccionada: Telecomunicaciones": "telecomunicaciones",
            "Área pública seleccionada: Vivienda": "vivienda",
        }
        for comment, expected in examples.items():
            with self.subTest(comment=comment):
                self.assertEqual(self.resolve(customer_comment=comment), expected)

    def test_safe_department_fallbacks_do_not_invent_consumer_family(self):
        self.assertEqual(self.resolve(department="traffic", case_type="fine"), "trafico")
        self.assertEqual(self.resolve(department="debt", case_type="other"), "morosidad")
        self.assertEqual(self.resolve(department="administration", case_type="aeat"), "administracion")
        self.assertEqual(self.resolve(department="claims", case_type="airline"), "viajes")
        self.assertEqual(self.resolve(department="claims", case_type="consumer"), "other")

    def test_intake_validates_and_persists_structured_family(self):
        source = (ROOT / "cases.py").read_text(encoding="utf-8")
        self.assertIn('public_service_family: str = Form("")', source)
        self.assertIn("public_service_family not in PUBLIC_SERVICE_FAMILY_CODES", source)
        self.assertIn('interested["public_service_family"] = public_service_family', source)
        self.assertIn('"public_service_family": public_service_family or None', source)

    def test_queue_and_followups_both_expose_public_family(self):
        source = (ROOT / "ops.py").read_text(encoding="utf-8")
        self.assertEqual(
            source.count('"public_service_family": _public_service_family('),
            2,
        )


if __name__ == "__main__":
    unittest.main()
