from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "RTM_CONNECT_C5_SUPERVISOR_PANEL.md"
)
ADR = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "adrs"
    / "0012-c5-supervisor-observability.md"
)
MANIFEST = (
    ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C0_MANIFEST.json"
)


class ConnectC5DocsContractTest(unittest.TestCase):
    def _combined(self) -> str:
        self.assertTrue(DOC.exists(), DOC.name)
        self.assertTrue(ADR.exists(), ADR.name)
        return (
            DOC.read_text(encoding="utf-8")
            + "\n"
            + ADR.read_text(encoding="utf-8")
        )

    def _assert_any(self, text: str, *alternatives: str) -> None:
        self.assertTrue(
            any(value in text for value in alternatives),
            f"none of {alternatives!r} found",
        )

    def test_c0_plans_c5_between_c4_and_c6(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        order = manifest["first_implementation_order"]
        c5 = order.index("C5_supervisor_panel")
        self.assertEqual(c5, order.index("C4_webhooks_unknown_reconciliation") + 1)
        self.assertEqual(order.index("C6_first_provider_sandbox"), c5 + 1)

    def test_docs_freeze_get_only_read_model_scope(self):
        combined = self._combined().lower()
        self._assert_any(combined, "get-only", "solo admiten lectura")
        self._assert_any(combined, "read-only", "solo lectura")
        for required in (
            "staging", "synthetic", "ops.supervise", "/ops/connect/supervisor"
        ):
            self.assertIn(required, combined)

    def test_docs_freeze_zero_ddl_seed_network_and_external_effects(self):
        combined = self._combined().lower()
        self._assert_any(combined, "sin ddl", "no hay ddl", "cero sentencias ddl")
        self._assert_any(combined, "sin migraci", "no hay ddl ni migración")
        self._assert_any(combined, "sin seed", "no introduce ddl, migración, seed")
        self._assert_any(combined, "sin red", "no hay llamadas de red", "cero llamadas de red")
        self._assert_any(combined, "sin efectos externos", "cero efectos externos")

    def test_docs_keep_core_authority_and_forbid_execution_controls(self):
        combined = self._combined().lower()
        self.assertIn("core autoriza", combined)
        self.assertIn("unknown", combined)
        self.assertIn("reintento ciego", combined)
        for forbidden_operation_stem in (
            "autoriz",
            "encol",
            "reintent",
            "reconcil",
            "confirm",
            "complet",
        ):
            self.assertIn(forbidden_operation_stem, combined)

    def test_docs_define_redaction_and_append_only_audit(self):
        combined = self._combined().lower()
        self.assertIn("append-only", combined)
        self.assertIn("auditor", combined)
        for protected in (
            "payload",
            "target_ref",
            "credential_ref",
            "receipt_storage_ref",
            "metadata",
        ):
            self.assertIn(protected, combined)

    def test_docs_define_preflight_smoke_rollback_and_health_closure(self):
        combined = self._combined().lower()
        for required in (
            "preflight",
            "smoke",
            "rollback",
            "/health",
        ):
            self.assertIn(required, combined)


if __name__ == "__main__":
    unittest.main()
