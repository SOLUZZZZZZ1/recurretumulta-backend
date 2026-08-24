from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C8_CONTROLLED_PRODUCTION.md"
ADR = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "adrs"
    / "0015-c8-controlled-production-admission.md"
)
MANIFEST = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C0_MANIFEST.json"


class ConnectC8DocsContractTest(unittest.TestCase):
    def test_docs_exist_and_keep_real_production_no_go(self):
        self.assertTrue(DOC.is_file())
        self.assertTrue(ADR.is_file())
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "NO-GO",
            "plano inerte",
            "pack específico",
            "assert_live_activation_unavailable",
            "no activa producción",
        ):
            self.assertIn(literal, combined)

    def test_docs_freeze_exact_inert_flags_and_limits(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "simulation_only=true",
            "external_effects_allowed=false",
            "live_activation_allowed=false",
            "human_activation_required=true",
            "canary_percent<=5",
            "concurrency=1",
        ):
            self.assertIn(literal, combined.replace(" ", ""))

    def test_docs_preserve_r4_e4_dual_control_and_core_authority(self):
        source = DOC.read_text(encoding="utf-8")
        for literal in (
            "R4_critical_regulated",
            "E4_receipt_verified",
            "exactamente dos aprobadores",
            "rtm.core.authorization/rtm_core_authority_v1",
            "legal_effect_authorized=false",
            "CORE autoriza",
        ):
            self.assertIn(literal, source)

    def test_docs_align_simulated_outbox_unknown_and_no_blind_retry(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "prepared",
            "claimed",
            "dry_run_confirmed",
            "unknown",
            "manual_review",
            "cancelled",
            "blind_retry_allowed=false",
            "reconciliation_required=true",
        ):
            self.assertIn(literal, combined)

    def test_docs_cover_release_evidence_egress_secret_and_rollback_gates(self):
        source = DOC.read_text(encoding="utf-8").lower()
        for literal in (
            "puertas de release fail-closed",
            "evidencia",
            "egress",
            "secretos",
            "idempotencia",
            "reconciliación",
            "rollback",
            "cero dns, sockets",
        ):
            self.assertIn(literal, source)

    def test_c0_orders_c8_after_c7_without_rewriting_frozen_state(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        order = manifest["first_implementation_order"]
        self.assertLess(
            order.index("C7_assisted_legal_connector"),
            order.index("C8_controlled_production"),
        )
        self.assertFalse(manifest["runtime_published"])
        self.assertFalse(manifest["external_effects_enabled"])


if __name__ == "__main__":
    unittest.main()
