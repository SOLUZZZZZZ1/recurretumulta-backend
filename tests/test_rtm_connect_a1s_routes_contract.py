from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "rtm_connect" / "human_filing_router.py"
SERVICE = ROOT / "rtm_connect" / "human_filing_service.py"
REPOSITORY = ROOT / "rtm_connect" / "human_filing_repository.py"


class ConnectA1SRoutesContractTest(unittest.TestCase):
    def test_new_runtime_files_exist_and_compile(self):
        for path in (ROUTER, SERVICE, REPOSITORY):
            self.assertTrue(path.is_file(), path.name)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_router_is_internal_feature_gated_and_not_openapi_public(self):
        source = ROUTER.read_text(encoding="utf-8")
        for required in (
            "/ops/connect/human-filings",
            "human_filing_gate_middleware",
            "load_a1s_runtime_configuration",
            "include_in_schema=False",
        ):
            self.assertIn(required, source)

    def test_router_uses_individual_operator_session_not_shared_credentials(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn("Authorization", source)
        self.assertIn("extract_bearer_token", source)
        self.assertIn("load_operator_session_with_device_possession", source)
        self.assertIn('alias="X-RTM-Device"', source)
        self.assertIn('alias="__Host-rtm_presenter_device"', source)
        self.assertIn("x_rtm_device=gate.x_rtm_device", source)
        self.assertIn(
            "rtm_presenter_device=gate.rtm_presenter_device",
            source,
        )
        self.assertNotIn(
            "from rtm_core.operator_auth_service import load_operator_session",
            source,
        )
        self.assertGreaterEqual(source.count("field(repr=False)"), 5)
        self.assertIn("operator", source.lower())
        for forbidden in (
            "OPERATOR_TOKEN",
            "RTM_OPERATOR_PIN",
            "X-Operator-Pin",
            "x-operator-id",
            "actor_id: str = Header",
        ):
            self.assertNotIn(forbidden, source)

    def test_service_requires_membership_case_binding_and_separation(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in (SERVICE, REPOSITORY)
        )
        for required in (
            "tenant_id",
            "case_binding",
            "membership",
            "principal_id",
            "package_sha256",
            "idempotency",
            "status_version",
        ):
            self.assertIn(required, combined)
        self.assertIn("approval", combined.lower())
        self.assertIn("verifier", combined.lower())
        self.assertIn("executor", combined.lower())
        self.assertTrue(
            "connect.human_filing." in combined
            or "HUMAN_FILING_READ_PERMISSION" in combined
        )
        self.assertNotIn("connect.human.", combined)

    def test_post_prepare_and_later_commands_require_latest_authorization(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        load_start = repository.index("def load_action_and_grant(")
        load_end = repository.index("\ndef list_preparation_candidates(", load_start)
        load = repository[load_start:load_end]
        self.assertIn("rtm_connect_authorizations newer", load)
        self.assertIn(
            "newer.authorization_version > g.authorization_version",
            load,
        )

    def test_preparation_and_assignment_are_separate_permissioned_commands(self):
        router_source = ROUTER.read_text(encoding="utf-8")
        service_source = SERVICE.read_text(encoding="utf-8")
        repository_source = REPOSITORY.read_text(encoding="utf-8")

        tree = ast.parse(router_source, filename=str(ROUTER))
        prepare_fields: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "PrepareHumanFilingBody":
                prepare_fields.update(
                    child.target.id
                    for child in node.body
                    if isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                )
        self.assertTrue(prepare_fields)
        self.assertNotIn("assignee_operator_id", prepare_fields)
        self.assertIn('"/{task_id}/assignments"', router_source)
        self.assertIn("AssignmentBody", router_source)
        self.assertIn("assign_human_filing", router_source + service_source)
        self.assertIn("HUMAN_FILING_ASSIGN_PERMISSION", service_source)
        self.assertIn("'prepared'", repository_source)
        self.assertIn(
            'target_status="assigned"',
            "".join(service_source.split()),
        )

    def test_frontend_read_routes_are_static_bounded_and_precede_task_route(self):
        source = ROUTER.read_text(encoding="utf-8")
        context = source.index('@router.get("/context")')
        tenants = source.index('@router.get("/tenants")')
        options = source.index('@router.get("/preparation-options")')
        dynamic = source.index('@router.get("/{task_id}")')
        self.assertLess(context, dynamic)
        self.assertLess(tenants, dynamic)
        self.assertLess(options, dynamic)
        self.assertEqual(source.count('@router.get("/context")'), 1)
        self.assertEqual(
            source.count('@router.get("/preparation-options")'), 1
        )
        self.assertEqual(source.count('@router.get("/{task_id}")'), 1)
        self.assertIn("get_human_filing_context", source)
        self.assertIn("list_human_filing_preparation_options", source)
        self.assertIn("list_human_filing_tenants", source)
        self.assertIn('@router.get("/{task_id}/receipt-options")', source)
        self.assertIn("list_human_filing_receipt_options", source)

    def test_context_and_preparation_options_are_safe_tenant_read_models(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        for required in (
            "HUMAN_FILING_PARTICIPANT_LIMIT = 100",
            "HUMAN_FILING_PREPARATION_OPTION_LIMIT = 100",
            "HUMAN_FILING_PREPARATION_SCAN_LIMIT = 200",
            "list_active_tenant_participants",
            "list_preparation_candidates",
            "display_name",
            "eligible_for",
            "NOT EXISTS",
            "a.status='authorized'",
        ):
            self.assertIn(required, repository)
        self.assertNotIn("email", repository.lower())
        self.assertIn("HUMAN_FILING_READ_PERMISSION", service)
        self.assertIn("HUMAN_FILING_PREPARE_PERMISSION", service)
        self.assertIn("validate_a1s_action_authority", service)
        self.assertIn("a1s_policy.HumanFilingPolicyError", service)
        self.assertIn("repository.HumanFilingScopeError", service)
        self.assertIn('"read_only": True', service)

    def test_repository_jsonb_predicates_use_explicit_bind_parameters(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        self.assertNotIn(":true", repository)
        self.assertNotIn("@> '{", repository)
        self.assertEqual(
            repository.count("@> CAST(:test_mode_metadata AS JSONB)"),
            4,
        )
        self.assertEqual(
            repository.count(
                '"test_mode_metadata": _json({"test_mode": True})'
            ),
            4,
        )
        self.assertEqual(
            repository.count("@> CAST(:synthetic_metadata AS JSONB)"),
            1,
        )
        self.assertEqual(
            repository.count(
                '"synthetic_metadata": _json({"synthetic_only": True})'
            ),
            1,
        )

    def test_session_can_discover_only_its_own_active_a1s_tenants(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        router = ROUTER.read_text(encoding="utf-8")
        self.assertIn("HUMAN_FILING_TENANT_OPTION_LIMIT = 100", repository)
        self.assertIn("def list_active_operator_tenants(", repository)
        self.assertIn("m.operator_id=CAST(:operator_id AS UUID)", repository)
        self.assertIn("t.status='active' AND t.synthetic_only=TRUE", repository)
        self.assertIn('@router.get("/tenants")', router)
        self.assertNotIn("email", repository.lower())

    def test_task_detail_is_bounded_sanitized_and_hints_are_not_authority(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        detail_start = repository.index("def task_read_detail(")
        detail_end = repository.index("\ndef load_fixture_document(", detail_start)
        detail = repository[detail_start:detail_end]
        self.assertIn("HUMAN_FILING_DETAIL_SUMMARY_LIMIT = 200", repository)
        self.assertIn('detail["package_manifest"]', detail)
        self.assertIn('detail["approvals"]', detail)
        self.assertIn('detail["artifacts"]', detail)
        self.assertIn('detail["receipt_summary"]', detail)
        self.assertIn('detail["events"]', detail)
        self.assertNotIn('detail["canonical_payload"]', detail)
        self.assertNotIn("SELECT *", detail)
        self.assertNotIn("payload,", detail)
        self.assertIn("canonical_payload->>'document_sha256'", detail)
        self.assertNotIn("SELECT canonical_payload", detail)
        for required in (
            'detail["allowed_actions"]',
            'detail["allowed_actions_authoritative"] = False',
            'detail["commands_revalidate"] = True',
            "_allowed_actions_hint",
            "verification_preapproval",
            "requester_principal_id",
            "assignee_principal_id",
        ):
            self.assertIn(required, service)

    def test_frozen_documents_and_receipt_fixture_are_hash_only_local_gates(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        for required in (
            "assert_frozen_case_document_hashes",
            "jsonb_array_elements_text",
            "frozen_document_hash_missing_from_bound_synthetic_case",
            "rtm_connect_a1s_synthetic_receipt_fixture",
            "d.mime='application/json'",
            "d.size_bytes BETWEEN 1 AND 65536",
            "d.b2_bucket IS NULL AND d.b2_key IS NULL",
            "t.package_manifest->'document_hashes'",
            "frozen_input.document_sha256=d.sha256",
        ):
            self.assertIn(required, repository)
        prepare_start = service.index("def prepare_human_filing(")
        prepare_end = service.index("\ndef list_human_filings(", prepare_start)
        prepare = service[prepare_start:prepare_end]
        self.assertLess(
            prepare.index("assert_frozen_case_document_hashes"),
            prepare.index("register_synthetic_connector"),
        )
        options_start = service.index(
            "def list_human_filing_preparation_options("
        )
        options_end = service.index("\ndef _allowed_actions_hint(", options_start)
        self.assertIn("assert_frozen_case_document_hashes", service[
            options_start:options_end
        ])

    def test_frontend_can_discover_bounded_eligible_receipt_fixtures(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("HUMAN_FILING_RECEIPT_OPTION_LIMIT = 100", repository)
        self.assertIn("def list_receipt_fixture_options(", repository)
        self.assertIn("def list_human_filing_receipt_options(", service)
        for required in (
            "rtm_connect_a1s_synthetic_receipt_fixture",
            "d.mime='application/json'",
            "d.size_bytes BETWEEN 1 AND 65536",
            "d.b2_bucket IS NULL AND d.b2_key IS NULL",
            "frozen_input.document_sha256=d.sha256",
            '"document_id"',
            '"document_sha256"',
        ):
            self.assertIn(required, repository)

    def test_repository_recomputes_canonical_package_and_artifact_hashes(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn(
            '_canonical_sha256(package_manifest) != str(package_sha256)',
            repository,
        )

    def test_idempotency_expiry_uses_the_same_postgres_clock_as_created_at(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        claim_start = repository.index("def claim_idempotency(")
        claim_end = repository.index("\ndef complete_idempotency(", claim_start)
        claim = repository[claim_start:claim_end]
        self.assertIn("NOW() + INTERVAL '24 hours'", claim)
        self.assertNotIn("CAST(:expires_at AS TIMESTAMPTZ)", claim)
        self.assertNotIn("def _expiry(", service)
        self.assertIn(
            '_canonical_sha256(canonical_payload) != str(sha256)',
            repository,
        )

    def test_e4_revalidates_the_exact_receipt_fixture_and_payload_allowlist(self):
        service = SERVICE.read_text(encoding="utf-8")
        verify_start = service.index("def verify_receipt_and_complete(")
        verify_end = service.index(
            "\ndef begin_human_reconciliation(", verify_start
        )
        verify = service[verify_start:verify_end]
        for required in (
            "expected_receipt_fields",
            "set(receipt_payload) != expected_receipt_fields",
            '_digest(receipt_payload) != str(receipt["sha256"])',
            'receipt_payload.get("request_sha256")',
            "load_fixture_document",
            'receipt_payload.get("storage_backend")',
            'receipt_payload.get("legal_submission_executed")',
        ):
            self.assertIn(required, verify)

    def test_release_event_ids_are_canonical_uuid_text(self):
        service = SERVICE.read_text(encoding="utf-8")
        release_start = service.index("def release_human_filing(")
        release_end = service.index("\ndef begin_execution(", release_start)
        release = service[release_start:release_end]
        for required in (
            '"release_approval_id": str(',
            'approvals["release"]["id"]',
            '"verification_preapproval_id": str(',
            'approvals["verification_preapproval"]["id"]',
        ):
            self.assertIn(required, release)
        self.assertNotIn(
            '"release_approval_id": approvals["release"]["id"]',
            release,
        )

    def test_receipt_is_separate_output_bound_to_frozen_task_identity(self):
        service = SERVICE.read_text(encoding="utf-8")
        receipt_start = service.index("def submit_receipt_fixture(")
        receipt_end = service.index(
            "\ndef verify_receipt_and_complete(", receipt_start
        )
        receipt = service[receipt_start:receipt_end]
        for required in (
            '"case_binding_id": str(row["case_binding_id"])',
            '"case_id": fixture["case_id"]',
            '"action_id": str(row["action_id"])',
            '"attempt_id": str(row["attempt_id"])',
            '"authorization_id": str(row["authorization_id"])',
            '"request_sha256": grant.payload_sha256',
            '"package_sha256": str(row["package_sha256"])',
            '"external_reference": external_reference',
        ):
            self.assertIn(required, receipt)

        verification = service[receipt_end:]
        for required in (
            'receipt_payload.get("task_id")',
            'receipt_payload.get("case_binding_id")',
            'receipt_payload.get("action_id")',
            'receipt_payload.get("attempt_id")',
            'receipt_payload.get("authorization_id")',
            'receipt_payload.get("package_sha256")',
            'receipt_payload.get("external_reference")',
        ):
            self.assertIn(required, verification)

    def test_unknown_outcome_reconciles_before_synthetic_e4_completion(self):
        service = SERVICE.read_text(encoding="utf-8")
        compact = "".join(service.split())
        for required in (
            'target="outcome_unknown"',
            'target_status="reconciling"',
            '"only_preapproved_verifier_may_reconcile"',
            '"only_preapproved_verifier_may_resolve_reconciliation"',
            '"e4_verifier_must_match_frozen_preapproval"',
            'reason_code="a1s_reconciliation_e4_confirmed"',
        ):
            self.assertIn(required, compact)
        self.assertIn(
            'outcome=="unknown"andexternal_referenceisnotNone',
            compact,
        )
        self.assertIn(
            "a1s_unknown_outcome_must_omit_external_reference",
            service,
        )
        self.assertNotIn('target_status="released"', compact[
            compact.find("defbegin_human_reconciliation"):
        ])

    def test_task_detail_only_degrades_expected_authority_errors(self):
        service = SERVICE.read_text(encoding="utf-8")
        detail_start = service.index("def get_human_filing(")
        detail_end = service.index(
            "\ndef list_human_filing_receipt_options(", detail_start
        )
        detail = service[detail_start:detail_end]
        self.assertIn("AuthorityValidationError", detail)
        self.assertIn("HumanFilingRepositoryError", detail)
        self.assertNotIn("except Exception", detail)

    def test_supervisor_can_close_supported_states_to_manual_review(self):
        router = ROUTER.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn('@router.post("/{task_id}/manual-reviews")', router)
        self.assertIn("ManualReviewBody", router)
        self.assertIn("escalate_to_manual_review", router + service)
        self.assertIn("HUMAN_FILING_SUPERVISE_PERMISSION", service)
        self.assertIn('target_status="manual_review"', service)
        self.assertIn("_MANUAL_REVIEW_SOURCE_STATUSES", service)
        self.assertIn("blind_retry_allowed", service)
        escalation = service[service.index("def escalate_to_manual_review("):]
        self.assertNotIn("_task_authority(", escalation)
        for required in (
            "_escalate_core_to_manual_review",
            "record_attempt_outcome",
            "begin_reconciliation",
            "record_reconciliation_outcome",
            "_transition_core_action",
            'target=ActionStatus.MANUAL_REVIEW',
            '"core_action_manual_review": True',
        ):
            self.assertIn(required, escalation)

    def test_repository_uses_only_a1s_namespaced_workflow_tables(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        for required in (
            "rtm_connect_a1s_memberships",
            "rtm_connect_a1s_case_bindings",
            "rtm_connect_a1s_representation_evidence",
            "rtm_connect_a1s_human_tasks",
            "rtm_connect_a1s_approvals",
            "rtm_connect_a1s_artifacts",
            "rtm_connect_a1s_events",
            "rtm_connect_a1s_idempotency",
        ):
            self.assertIn(required, source)
        for legacy in (
            "rtm_tenants",
            "rtm_tenant_operator_memberships",
            "rtm_case_tenant_bindings",
            "rtm_case_representation_evidence",
            "rtm_connect_human_tasks",
            "rtm_connect_human_approvals",
            "rtm_connect_human_artifacts",
            "rtm_connect_human_events",
            "rtm_connect_human_idempotency",
        ):
            self.assertNotIn(legacy, source)

    def test_runtime_has_no_provider_network_b2_or_b2b_transport(self):
        imported: set[str] = set()
        for path in (ROUTER, SERVICE, REPOSITORY):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
        forbidden = {
            "boto3", "dgt_client", "httpx", "requests", "socket",
            "submitter_dgt", "submitters.registro", "urllib.request",
        }
        self.assertFalse(forbidden & imported)

    def test_runtime_material_is_schema_compatible_and_actor_attributed(self):
        repository = REPOSITORY.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        combined = repository + service
        self.assertIn("validate_human_filing_idempotency_key", combined)
        self.assertIn("HUMAN_FILING_MARKER", repository)
        self.assertIn("synthetic_only", repository)
        self.assertIn("rtm-a1s-artifact-", service)
        self.assertNotIn('actor_type="reconciliation"', service)


if __name__ == "__main__":
    unittest.main()
