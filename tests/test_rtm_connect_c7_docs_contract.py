from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = (
    ROOT / "docs" / "rtm_connect" /
    "RTM_CONNECT_C7_ASSISTED_LEGAL.md"
)
ADR = (
    ROOT / "docs" / "rtm_connect" / "adrs" /
    "0014-c7-assisted-legal-handoff.md"
)


class ConnectC7DocsContractTest(unittest.TestCase):
    def test_docs_exist_and_freeze_exact_tuple(self):
        for path in (DOC, ADR):
            self.assertTrue(path.exists(), path.name)
        source = (
            DOC.read_text(encoding="utf-8")
            + "\n"
            + ADR.read_text(encoding="utf-8")
        )
        for required in (
            "assisted.legal/v1.0",
            "administration.submit.legal.assisted",
            "administration.synthetic.filing",
            "R4_critical_regulated",
            "E4_receipt_verified",
            "HUMAN_FINAL_SUBMIT_REQUIRED",
            "rtm_connect_assisted_tasks",
            "rtm_connect_assisted_events",
        ):
            self.assertIn(required, source)

    def test_docs_preserve_core_authority_and_human_final_gate(self):
        source = " ".join(
            DOC.read_text(encoding="utf-8").lower().split()
        )
        for required in (
            "core autoriza",
            "presentación final sigue siendo humana",
            "no elige administración",
            "no autoemite una autorización",
            "doble aprobación core",
            "tres funciones humanas separadas",
            "solo entonces core puede cambiar el estado jurídico",
        ):
            self.assertIn(required, source)

    def test_docs_cover_normal_and_unknown_without_blind_retry(self):
        source = " ".join((
            DOC.read_text(encoding="utf-8")
            + "\n"
            + ADR.read_text(encoding="utf-8")
        ).lower().split())
        for required in (
            "flujo normal",
            "rama unknown",
            "no se crea otro intento",
            "no existe retry automático",
            "reutiliza el intento original",
            "receipt_submitted",
            "verified",
            "completed",
            "e3",
            "e4",
        ):
            self.assertIn(required, source)

    def test_docs_freeze_default_off_no_routes_network_or_effects(self):
        source = (
            DOC.read_text(encoding="utf-8")
            + "\n"
            + ADR.read_text(encoding="utf-8")
        ).lower()
        for required in (
            "default-off",
            "no modifica `app.py`",
            "no publica ruta",
            "sin red",
            "no conserva endpoint, secreto",
            "rollback",
            "no destructivo",
            "staging sintético",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
