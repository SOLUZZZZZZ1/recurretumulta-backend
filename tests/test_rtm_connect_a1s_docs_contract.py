from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_A1S_HUMAN_FILING.md"
ADR = ROOT / "docs" / "rtm_connect" / "adrs" / "0018-a1s-human-filing.md"


class ConnectA1SDocsContractTest(unittest.TestCase):
    def test_docs_freeze_base_and_no_go_scope(self):
        combined = GATE.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for required in (
            "b0bc7ddfad9278e601dce8dd69083472662874b5",
            "4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e",
            "NO-GO",
            "synthetic_only=true",
            "production_authorized=false",
        ):
            self.assertIn(required, combined)

    def test_gate_documents_identity_tenant_case_and_human_separation(self):
        source = GATE.read_text(encoding="utf-8")
        for required in (
            "sesión bearer individual",
            "membership",
            "tenant",
            "expediente sintético",
            "dos aprobaciones individuales",
            "solicitante",
            "tres identidades distintas",
            "releaser",
            "ejecutor",
            "verificador",
            "rtm_connect_a1s_approvals",
            "append-only",
        ):
            self.assertIn(required, source)

    def test_gate_documents_no_blind_retry_and_evidence_semantics(self):
        source = GATE.read_text(encoding="utf-8")
        for required in (
            "outcome_unknown",
            "reconcilia",
            "sin reenvío",
            "E3 sintética",
            "E4 sintética",
            "recibo real",
        ):
            self.assertIn(required, source)

    def test_frontend_http_contract_is_explicit_and_non_authoritative(self):
        source = GATE.read_text(encoding="utf-8")
        for required in (
            "## Contrato HTTP para el frontend",
            "GET /tenants",
            "GET /context?tenant_id=...",
            "GET /preparation-options?tenant_id=...",
            "GET /{task_id}/receipt-options?tenant_id=...",
            "POST /{task_id}/assignments",
            "POST /{task_id}/verification-preapprovals",
            "POST /{task_id}/reconciliations/resolve",
            "POST /{task_id}/manual-reviews",
            "Idempotency-Key",
            "If-Match",
            "ETag",
            "allowed_actions_authoritative=false",
            "commands_revalidate=true",
            "A1S_SYNTHETIC_RECEIPT_VERIFIED",
            "receipt_summary",
            "envelope `detail`",
        ):
            self.assertIn(required, source)

    def test_docs_are_honest_about_core_writes_and_static_verification(self):
        combined = GATE.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for required in (
            "tablas CORE",
            "EXECUTING",
            "database_constraints_executed=false",
            "workflow_scenario_executed=false",
            "no recalcula de forma independiente",
            "verification_preapproval_attestation",
            "salida posterior",
        ):
            self.assertIn(required, combined)

    def test_docs_explicitly_forbid_all_external_and_real_surfaces(self):
        combined = GATE.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for required in (
            "provider_contacted=false",
            "administration_contacted=false",
            "provider_network_used=false",
            "administration_network_used=false",
            "b2_used=false",
            "b2b_enabled=false",
            "real_data_used=false",
            "external_effects_executed=false",
        ):
            self.assertIn(required, combined)

    def test_commands_are_isolated_and_schema_apply_is_confirmed(self):
        source = GATE.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("python -I -S -B"), 2)
        self.assertGreaterEqual(source.count("python -I -B"), 2)
        self.assertIn("STAGING_CONNECT_A1S_SCHEMA_ONLY", source)


if __name__ == "__main__":
    unittest.main()
