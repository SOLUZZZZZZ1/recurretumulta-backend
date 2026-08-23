from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "RTM_CONNECT_C4_WEBHOOK_RECONCILIATION.md"
)
ADR = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "adrs"
    / "0011-c4-webhook-unknown-reconciliation.md"
)
MANIFEST = (
    ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C0_MANIFEST.json"
)


class ConnectC4DocsContractTest(unittest.TestCase):
    def _combined(self) -> str:
        return (
            DOC.read_text(encoding="utf-8")
            + "\n"
            + ADR.read_text(encoding="utf-8")
        )

    def test_c0_planned_c4_exactly_after_c3(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        order = manifest["first_implementation_order"]
        self.assertEqual(
            order.index("C4_webhooks_unknown_reconciliation"),
            order.index("C3_manual_handoff") + 1,
        )

    def test_docs_freeze_synthetic_only_scope(self):
        combined = self._combined().lower()
        for required in (
            "synthetic-only",
            "sin datos reales",
            "sin efectos externos",
            "no publica rutas",
            "no modifica `app.py`",
            "no usa red",
        ):
            self.assertIn(required, combined)

    def test_docs_freeze_unknown_without_blind_retry(self):
        combined = self._combined()
        self.assertIn("UNKNOWN` nunca se reintenta a ciegas", combined)
        self.assertIn("no llama a `queue_action`", combined)
        self.assertIn("no llama a `start_attempt`", combined)

    def test_docs_freeze_exact_deduplication_and_match(self):
        combined = self._combined()
        self.assertIn(
            "`ingress_connector_id + source_event_id`",
            combined,
        )
        for required in (
            "`action_id`",
            "`attempt_id`",
            "`request_sha256`",
            "`external_reference`",
            "conector de origen",
        ):
            self.assertIn(required, combined)

    def test_docs_distinguish_ingress_from_origin(self):
        combined = self._combined().lower()
        self.assertIn(
            "el conector de ingreso del webhook es distinto del conector de origen",
            combined,
        )
        self.assertIn(
            "el conector de ingreso no puede ser el conector de origen",
            combined,
        )

    def test_docs_freeze_exact_e4(self):
        combined = self._combined()
        self.assertIn("Evidencia E4 exacta", combined)
        self.assertIn("`evidence_id` concreto", combined)
        self.assertIn("`synthetic://webhook/`", combined)

    def test_docs_do_not_claim_synthetic_hash_is_provider_signature(self):
        combined = self._combined().lower()
        self.assertIn("no es una firma criptográfica de proveedor", combined)
        self.assertIn(
            "la prueba de integridad sintética no se presenta como firma real",
            combined,
        )

    def test_app_runtime_remains_unwired_if_present_in_full_repository(self):
        app = ROOT / "app.py"
        if not app.exists():
            return
        source = app.read_text(encoding="utf-8")
        for forbidden in (
            "rtm_connect.webhooks",
            "rtm_connect.reconciliation",
            "synthetic_webhook",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
