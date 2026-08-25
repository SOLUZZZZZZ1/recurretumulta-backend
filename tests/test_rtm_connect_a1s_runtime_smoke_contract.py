from __future__ import annotations

import ast
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import rtm_connect_a1s_runtime_smoke as smoke


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_connect_a1s_runtime_smoke.py"


class ConnectA1SRuntimeSmokeContractTest(unittest.TestCase):
    def test_script_exists_and_compiles(self):
        source = SCRIPT.read_text(encoding="utf-8")
        ast.parse(source, filename=str(SCRIPT))
        self.assertEqual(
            smoke.RTM_CONNECT_A1S_RUNTIME_SMOKE_VERSION,
            "rtm_connect_a1s_runtime_smoke_v1_0",
        )
        self.assertEqual(
            smoke.DEFAULT_RUNTIME_FIXTURE_KEY,
            "runtime-a94dcd3-v1",
        )
        args = smoke._parser().parse_args(["--compact"])
        self.assertEqual(args.fixture_key, "runtime-a94dcd3-v1")

    def test_full_asgi_happy_path_is_present(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "from fastapi.testclient import TestClient",
            "from app import app as asgi_app",
            "raise_server_exceptions=False",
            "/preparation-options",
            "/assignments",
            "/reviews/start",
            "/reviews/attest",
            "/verification-preapprovals",
            "/releases",
            "/executions/start",
            "/outcomes",
            "/receipt-options",
            "/receipts",
            "/verifications",
            'expected_status="completed"',
        ):
            self.assertIn(required, source)

    def test_unknown_reconciliation_branch_is_complete_and_single_attempt(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            'scenario="unknown_manual_review"',
            '"outcome": "unknown"',
            '"/reconciliations/start"',
            '"/reconciliations/resolve"',
            'body={"resolution": "remains_unknown"}',
            'body={"resolution": "manual_review"}',
            'expected_status="manual_review"',
            '"full_http_unknown_reconciliation_branch"',
            '"unknown_branch_closes_manual_review"',
            '"unknown_branch_never_blind_retries"',
            "attempt_number=1",
            "retryable=FALSE",
            "CAST(:safe_attempt_metadata AS JSONB)",
            "CAST(:blind_retry_metadata AS JSONB)",
            'int(unknown_attempt_row["total"]) == 1',
            'int(unknown_event_row["blind_retry_blocked_events"]) == 5',
        ):
            self.assertIn(required, source)

    def test_jsonb_predicates_use_explicit_bind_parameters(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("@> '{\"blind_retry_allowed\":false", source)
        for required in (
            '"safe_attempt_metadata": json.dumps(',
            '"blind_retry_metadata": json.dumps(',
            '"blind_retry_allowed": False',
        ):
            self.assertIn(required, source)

    def test_both_baselines_precede_provisioning_and_are_verified_fresh(self):
        source = SCRIPT.read_text(encoding="utf-8")
        baseline = source.index("baselines = {")
        provision = source.index("provisioned = provision_runtime_fixture(")
        self.assertLess(baseline, provision)
        for required in (
            '"completed": _fixture_snapshot(connection, plan)',
            '"unknown_manual_review": _fixture_snapshot(',
            '"completed": _fixture_snapshot(verification, plan)',
            "after == baselines and sessions_remaining == 0",
            '"fixture_snapshots_equal_to_baselines"',
            "FROM rtm_connect_transitions",
            "FROM rtm_connect_idempotency_claims",
        ):
            self.assertIn(required, source)

    def test_socket_egress_is_blocked_and_zero_attempts_are_required(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            '"socket.socket.connect"',
            '"socket.socket.connect_ex"',
            '"socket.socket.sendto"',
            '"socket.create_connection"',
            '"socket.getaddrinfo"',
            "patch(target, side_effect=block_egress(target))",
            '"external_socket_attempt_blocked:',
            '"zero_external_socket_attempts"',
            "len(egress_attempts) == 0",
        ):
            self.assertIn(required, source)

    def test_runtime_uses_real_postgresql_and_transactional_cleanup(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "get_engine()",
            "connection.begin()",
            "begin_nested()",
            "transaction.rollback()",
            "with engine.connect() as verification",
            "_fixture_snapshot(verification, plan)",
            '"fresh_connection_observes_baseline_restored_and_ephemeral_zero_residue"',
            '"fixture_baseline_restored"',
            '"database_rolled_back"',
        ):
            self.assertIn(required, source)

    def test_provisioning_sessions_and_database_identity_are_real(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "provision_runtime_fixture",
            "audit_runtime_fixture",
            "_runtime_operators_from_fixture(connection, plan)",
            '"persistent_runtime_fixture_not_ready"',
            "schema_snapshot(connection)",
            "assert_a1s_database_identity(",
            "create_operator_session(",
            "hash_session_token(",
            '"sessions_store_only_sha256"',
            '"raw_session_tokens_persisted": False',
            '"raw_session_tokens_reported": False',
        ):
            self.assertIn(required, source)

    def test_default_off_and_three_person_separation_are_exercised(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            'f"{name}_must_start_explicitly_false"',
            'HUMAN_FILING_FEATURE_FLAG: "false"',
            '"RTM_ENABLE_OPERATOR_AUTH_V1": "false"',
            'os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "true"',
            'os.environ[HUMAN_FILING_FEATURE_FLAG] = "true"',
            'status_code=404',
            'status_code=401',
            "operators.requester_executor_id",
            "operators.releaser_id",
            "operators.verifier_id",
            '"two_preoperation_principals_distinct"',
            '"temporary_runtime_flags_restored"',
        ):
            self.assertIn(required, source)

    def test_e4_is_exact_and_bound_to_preapproved_verifier(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "e.evidence_level='E4_receipt_verified'",
            "e.verified_by_operator_id=",
            "e.request_sha256=a.payload_sha256",
            "e.receipt_sha256=:receipt_sha256",
            "e.external_reference=:external_reference",
            "'verification_preapproval'",
            "approval.operator_id=",
            "task.verified_by_principal_id",
            '"e4_exactly_bound_to_preapproved_verifier"',
            'int(evidence_row["exact_e4"]) == 1',
        ):
            self.assertIn(required, source)

    def test_report_distinguishes_database_from_forbidden_network(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            '"http_in_process_asgi": False',
            '"database_connection_used": False',
            '"provider_network_used": False',
            '"administration_network_used": False',
            '"provider_contacted": False',
            '"administration_contacted": False',
            '"b2_used": False',
            '"real_data_used": False',
            '"external_effects_executed": False',
            '"legal_submission_executed": False',
            '"production_authorized": False',
            '"live_verdict": "no_go"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("network_used", smoke._report())

    def test_no_external_http_or_socket_transport_is_imported(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertTrue({"requests", "httpx", "socket", "urllib"}.isdisjoint(imports))

    def test_missing_explicitly_false_flags_fail_before_runtime_execution(self):
        argv = ["--compact"]
        with patch.object(smoke, "_execute_runtime") as execute:
            with patch.dict("os.environ", {}, clear=True):
                with redirect_stdout(io.StringIO()) as output:
                    code = smoke.main(argv)
        self.assertEqual(code, 2)
        execute.assert_not_called()
        rendered = output.getvalue()
        self.assertIn(
            "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING_must_start_explicitly_false",
            rendered,
        )
        self.assertIn(
            "RTM_ENABLE_OPERATOR_AUTH_V1_must_start_explicitly_false",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
