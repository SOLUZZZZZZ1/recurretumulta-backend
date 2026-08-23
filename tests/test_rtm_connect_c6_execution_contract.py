from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "rtm_connect" / "provider_sandbox.py"
APP = ROOT / "app.py"


class ConnectC6ExecutionContractTest(unittest.TestCase):
    def test_authority_is_revalidated_immediately_before_network(self):
        source = EXECUTION.read_text(encoding="utf-8")
        marker = "result = connector.execute_authorized"
        before = source[:source.index(marker)]
        self.assertIn("validate_execution_authority", before[-900:])
        self.assertIn("validate_c6_probe_authority", before[-900:])

    def test_idempotency_and_attempt_precede_network(self):
        source = EXECUTION.read_text(encoding="utf-8")
        execute = source.split("def execute_controlled_sandbox_probe", 1)[1]
        execute = execute.split("def reconcile_controlled_sandbox_probe", 1)[0]
        self.assertNotIn("create_action(", execute)
        self.assertNotIn("authorize_action(", execute)
        self.assertLess(
            execute.index("_persisted_authorization("),
            execute.index("start_attempt("),
        )
        self.assertLess(
            execute.index("start_attempt("),
            execute.index("connector.execute_authorized"),
        )
        self.assertIn(
            "CORE debe persistir acción y autorización antes de CONNECT C6",
            source,
        )

    def test_replay_unknown_block_and_get_only_reconciliation(self):
        source = EXECUTION.read_text(encoding="utf-8")
        for required in (
            "ControlledSandboxReplayBlocked",
            "La acción C6 no terminal no puede repetir el POST",
            "begin_reconciliation(",
            "attempt_id=attempt_id",
            '"method": "get_only"',
            "_assert_exact_c6_e2_scope",
            "_close_c6_reconciled_attempt",
            "evidence_id=evidence_id",
        ):
            self.assertIn(required, source)

    def test_staging_database_and_persisted_grant_gate_both_paths(self):
        source = EXECUTION.read_text(encoding="utf-8")
        execute = source.split("def execute_controlled_sandbox_probe", 1)[1]
        reconcile = source.split("def reconcile_controlled_sandbox_probe", 1)[1]
        for section in (execute, reconcile):
            self.assertIn("_assert_execution_boundary(conn, connector)", section)
            self.assertIn("_persisted_authorization(", section)
        self.assertLess(
            reconcile.index("_persisted_authorization("),
            reconcile.index("begin_reconciliation("),
        )
        self.assertIn("if persisted != supplied", source)
        self.assertIn("type(connector) is not ControlledSandboxConnector", source)
        self.assertIn("connector.assert_runtime_sealed()", source)
        self.assertIn("def _persisted_action_contract", source)
        self.assertIn("persisted != supplied", source)
        for field in (
            "requested_by_operator_id",
            "requested_at",
            "correlation_id",
            "contract_version",
            "document_hashes",
        ):
            self.assertIn(field, source)
        self.assertGreaterEqual(
            source.count("_persisted_action_contract("),
            3,
        )

    def test_result_scope_and_status_are_resolved_before_ledger_write(self):
        source = EXECUTION.read_text(encoding="utf-8")
        execute = source.split("def execute_controlled_sandbox_probe", 1)[1]
        reconcile = source.split("def reconcile_controlled_sandbox_probe", 1)[1]
        self.assertLess(
            execute.index("_validate_normalized_result_scope("),
            execute.index("record_attempt_outcome("),
        )
        self.assertLess(
            reconcile.index("target_map ="),
            reconcile.index("record_evidence("),
        )
        self.assertIn("result.attempt_id != attempt_id", source)
        for required in (
            "result.evidence.receipt_sha256",
            "result.evidence.receipt_storage_ref",
            "result.evidence.verification_method",
            "dict(result.metadata) != expected_metadata",
            "result.failure_class != expected_failure",
            "result.reconciliation_required is not expected_reconciliation",
            "result_metadata=result_metadata",
        ):
            self.assertIn(required, source)

    def test_runtime_is_not_wired_to_app(self):
        source = APP.read_text(encoding="utf-8")
        for forbidden in (
            "provider_sandbox",
            "controlled_sandbox",
            "sandbox.http.probe",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
