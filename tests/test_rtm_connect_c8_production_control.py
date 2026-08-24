from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import os
import sys
import types
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.production_contracts import (
    ProductionAdmissionCandidate,
    ProductionApprovalRole,
    ProductionReleaseApproval,
    SimulatedOutboxIntent,
    SimulatedOutboxStatus,
    candidate_sha256,
    expected_c8_admission_payload,
)
from rtm_connect.production_schema import (
    DISPATCH_OUTBOX_STATUSES,
    PRODUCTION_RELEASE_STATUSES,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "rtm_connect" / "production_control.py"


def _load_control_module():
    previous = sys.modules.get("sqlalchemy")
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.text = lambda statement: statement
    sys.modules["sqlalchemy"] = sqlalchemy
    module_name = "_rtm_connect_c8_production_control_under_test"
    spec = importlib.util.spec_from_file_location(module_name, CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar production_control.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("sqlalchemy", None)
        else:
            sys.modules["sqlalchemy"] = previous
    return module


def _now(offset: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + offset).isoformat()


def _candidate() -> ProductionAdmissionCandidate:
    return ProductionAdmissionCandidate(
        candidate_id=str(uuid.uuid4()),
        requested_by_operator_id=str(uuid.uuid4()),
        source_commit_sha40="1" * 40,
        build_artifact_sha256="2" * 64,
        connector_manifest_sha256="3" * 64,
        provider_contract_sha256="4" * 64,
        egress_policy_sha256="5" * 64,
        credential_reference_sha256="6" * 64,
        schema_snapshot_sha256="7" * 64,
        test_report_sha256="8" * 64,
        created_at=_now(timedelta(minutes=-5)),
        expires_at=_now(timedelta(hours=1)),
        canary_percent=1,
        concurrency=1,
        max_simulated_actions_total=1,
        max_simulated_actions_per_day=1,
        max_payload_bytes=4096,
        admission_ttl_seconds=7200,
    )


def _action(candidate: ProductionAdmissionCandidate) -> ConnectActionRequest:
    return ConnectActionRequest(
        action_id=str(uuid.uuid4()),
        capability="connect.production.admission.simulate",
        satellite="rtm.connect.production.admission",
        target_type="production.admission.candidate",
        target_ref="synthetic-c8-admission",
        payload=expected_c8_admission_payload(candidate_sha256(candidate)),
        requested_by_operator_id=candidate.requested_by_operator_id,
        requested_at=_now(timedelta(minutes=-4)),
        risk_class=RiskClass.R4_CRITICAL_REGULATED,
        requires_dual_control=True,
    )


def _grant(
    action: ConnectActionRequest, approvers: tuple[str, str]
) -> AuthorizationGrant:
    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action, authority_scope="rtm.core.authorization"
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.ASSISTED,),
        approved_by_operator_ids=approvers,
        authorized_at=_now(timedelta(minutes=-3)),
        expires_at=_now(timedelta(minutes=30)),
        legal_effect_authorized=False,
    )


class ConnectC8ProductionControlContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CONTROL.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(CONTROL))
        cls.control = _load_control_module()

    def test_module_compiles_and_exports_exact_control_surface(self):
        expected = {
            "release_snapshot",
            "propose_production_release",
            "approve_production_release",
            "mark_production_release_ready",
            "simulate_production_release_activation",
            "emergency_halt_production_release",
            "dispatch_snapshot",
            "prepare_dispatch_dry_run",
            "claim_dispatch_dry_run",
            "confirm_dispatch_dry_run",
            "mark_dispatch_unknown",
            "move_dispatch_manual_review",
        }
        self.assertTrue(expected.issubset(set(self.control.__all__)))
        for name in expected:
            self.assertTrue(callable(getattr(self.control, name)), name)

    def test_state_sets_are_exactly_the_schema_state_sets(self):
        self.assertEqual(
            self.control._RELEASE_STATUSES,
            frozenset(PRODUCTION_RELEASE_STATUSES),
        )
        self.assertEqual(
            self.control._DISPATCH_STATUSES,
            frozenset(DISPATCH_OUTBOX_STATUSES),
        )
        self.assertEqual(
            self.control._TERMINAL_DISPATCH_STATUSES,
            frozenset({"dry_run_confirmed", "manual_review", "cancelled"}),
        )

    def test_release_insert_matches_schema_and_is_structurally_inert(self):
        source = inspect.getsource(self.control.propose_production_release)
        for required in (
            "rtmc8-release-",
            "'c8.inert.simulation', 'v1.0'",
            "TRUE, FALSE, FALSE, TRUE",
            "provider_pack_present",
            '"daily_action_limit": 1',
            "candidate_sha256(candidate)",
            "expected_c8_admission_payload(digest)",
        ):
            self.assertIn(required, source)
        self.assertLess(source.index("_assess_candidate"), source.index("INSERT INTO"))

    def test_release_events_use_schema_actor_vocabulary(self):
        source = inspect.getsource(self.control._append_release_event)
        for actor in ('"requester"', '"security"', '"operations"', '"system"'):
            self.assertIn(actor, source)
        self.assertNotIn('"operator" if operator_id', source)
        self.assertIn("release_binding_sha256", source)
        self.assertIn("FOR UPDATE", source)
        self.assertIn("MAX(sequence_number)", source)

    def test_dispatch_events_bind_exact_parent_scope(self):
        source = inspect.getsource(self.control._append_dispatch_event)
        for required in (
            "outbox_id",
            "action_id",
            "authorization_id",
            "release_id",
            "release_binding_sha256",
            "MAX(sequence_number)",
            "FOR UPDATE",
        ):
            self.assertIn(required, source)
        self.assertNotIn("id, dispatch_id, action_id", source)

    def test_policy_and_authority_are_revalidated_before_every_dml_family(self):
        checks = (
            ("propose_production_release", "_assess_candidate", "INSERT INTO"),
            ("approve_production_release", "_assess_candidate", "UPDATE"),
            ("mark_production_release_ready", "_assess_candidate", "_transition_release"),
            ("simulate_production_release_activation", "_assess_candidate", "_transition_release"),
            ("emergency_halt_production_release", "_candidate_from_release", "UPDATE"),
            ("prepare_dispatch_dry_run", "validate_c8_admission_authority", "INSERT INTO"),
            ("claim_dispatch_dry_run", "_revalidate_dispatch_authority", "UPDATE"),
            ("_finish_claimed_dispatch", "_revalidate_dispatch_authority", "UPDATE"),
            ("move_dispatch_manual_review", "_revalidate_dispatch_authority", "UPDATE"),
        )
        for function_name, guard, mutation in checks:
            with self.subTest(function=function_name):
                source = inspect.getsource(getattr(self.control, function_name))
                self.assertIn(guard, source)
                self.assertIn(mutation, source)
                self.assertLess(source.index(guard), source.index(mutation))

    def test_admission_revalidation_is_exact_r4_e4_assisted_no_legal_effect(self):
        source = inspect.getsource(self.control._revalidate_dispatch_authority)
        self.assertIn("validate_c8_admission_authority", source)
        self.assertIn("grant.legal_effect_authorized", source)
        policy = (ROOT / "rtm_connect" / "production_policy.py").read_text(
            encoding="utf-8"
        )
        for required in (
            "RiskClass.R4_CRITICAL_REGULATED",
            "EvidenceLevel.E4_RECEIPT_VERIFIED",
            "authorized_connector_modes != (C8_ADMISSION_MODE,)",
            "if grant.legal_effect_authorized",
        ):
            self.assertIn(required, policy)

    def test_no_execution_transport_connector_attempt_or_core_confirmation_surface(self):
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"requests", "httpx", "urllib", "socket", "ssl", "ftplib"}
            )
        )
        called_names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            called_names.isdisjoint(
                {
                    "confirm_action",
                    "start_attempt",
                    "record_attempt_outcome",
                    "register_synthetic_connector",
                    "resolve_secret",
                    "send",
                    "submit",
                }
            )
        )
        lowered = self.source.lower()
        self.assertNotIn("rtm_connect_attempts", lowered)
        self.assertNotIn("rtm_connect_connectors", lowered)

    def test_live_guard_is_a_proof_for_simulation_not_a_live_path(self):
        source = inspect.getsource(
            self.control.simulate_production_release_activation
        )
        self.assertIn("_prove_live_activation_unavailable", source)
        helper = inspect.getsource(self.control._prove_live_activation_unavailable)
        self.assertIn("assert_live_activation_unavailable", helper)
        self.assertIn("ProductionLiveActivationUnavailable", helper)
        self.assertIn("to_status=\"simulated_active\"", source)
        self.assertNotIn("live_active", source)

    def test_approval_hash_is_role_release_candidate_and_operator_bound(self):
        candidate = _candidate()
        base = ProductionReleaseApproval(
            approval_id=str(uuid.uuid4()),
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate_sha256(candidate),
            requested_by_operator_id=candidate.requested_by_operator_id,
            approver_operator_id=str(uuid.uuid4()),
            approval_role=ProductionApprovalRole.SECURITY,
            approved_at=_now(timedelta(minutes=-1)),
            expires_at=_now(timedelta(minutes=20)),
        )
        digest = self.control._approval_sha256(
            candidate.candidate_id, candidate_sha256(candidate), base
        )
        self.assertEqual(
            digest,
            self.control._approval_sha256(
                candidate.candidate_id, candidate_sha256(candidate), base
            ),
        )
        other_release = str(uuid.uuid4())
        self.assertNotEqual(
            digest,
            self.control._approval_sha256(
                other_release, candidate_sha256(candidate), base
            ),
        )
        changed = ProductionReleaseApproval(
            **{
                **base.__dict__,
                "approval_id": str(uuid.uuid4()),
                "approver_operator_id": str(uuid.uuid4()),
            }
        )
        self.assertNotEqual(
            digest,
            self.control._approval_sha256(
                candidate.candidate_id, candidate_sha256(candidate), changed
            ),
        )

    def test_dispatch_hash_ignores_random_intent_id_but_binds_changed_body(self):
        candidate = _candidate()
        action = _action(candidate)
        approvers = (str(uuid.uuid4()), str(uuid.uuid4()))
        grant = _grant(action, approvers)
        request = SimulatedOutboxIntent(
            intent_id=str(uuid.uuid4()),
            candidate_id=candidate.candidate_id,
            action_id=action.action_id,
            authorization_id=grant.authorization_id,
            candidate_sha256=candidate_sha256(candidate),
            request_sha256=payload_sha256(action),
            idempotency_key=grant.idempotency_key,
            status=SimulatedOutboxStatus.PREPARED,
            created_at=_now(),
            reconciliation_required=False,
        )
        first = self.control._dispatch_binding_sha256(
            candidate.candidate_id, action, grant, request
        )
        self.assertEqual(
            first,
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, action, grant, request
            ),
        )
        changed_identity = SimulatedOutboxIntent(
            **{**request.__dict__, "intent_id": str(uuid.uuid4())}
        )
        self.assertEqual(
            first,
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, action, grant, changed_identity
            ),
        )
        self.assertEqual(
            self.control._production_effect_sha256(
                candidate.candidate_id, action, grant, request
            ),
            self.control._production_effect_sha256(
                candidate.candidate_id, action, grant, changed_identity
            ),
        )
        changed_arbitrary_time = SimulatedOutboxIntent(
            **{**request.__dict__, "created_at": _now(timedelta(minutes=1))}
        )
        self.assertEqual(
            first,
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, action, grant, changed_arbitrary_time
            ),
        )
        self.assertEqual(
            self.control._production_effect_sha256(
                candidate.candidate_id, action, grant, request
            ),
            self.control._production_effect_sha256(
                candidate.candidate_id, action, grant, changed_arbitrary_time
            ),
        )
        changed_body = SimulatedOutboxIntent(
            **{**request.__dict__, "idempotency_key": "rtmc1:" + "f" * 64}
        )
        self.assertNotEqual(
            first,
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, action, grant, changed_body
            ),
        )
        prepare = inspect.getsource(self.control.prepare_dispatch_dry_run)
        self.assertIn("stored_binding", prepare)
        self.assertIn("ProductionDispatchReplayConflict", prepare)
        self.assertIn("grant.idempotency_key", prepare)

    def test_effect_key_deduplicates_recreated_c1_identities(self):
        candidate = _candidate()
        first_action = _action(candidate)
        approvers = (str(uuid.uuid4()), str(uuid.uuid4()))
        first_grant = _grant(first_action, approvers)
        first = SimulatedOutboxIntent(
            intent_id=str(uuid.uuid4()),
            candidate_id=candidate.candidate_id,
            action_id=first_action.action_id,
            authorization_id=first_grant.authorization_id,
            candidate_sha256=candidate_sha256(candidate),
            request_sha256=payload_sha256(first_action),
            idempotency_key=first_grant.idempotency_key,
            status=SimulatedOutboxStatus.PREPARED,
            created_at=_now(),
            reconciliation_required=False,
        )
        second_action = _action(candidate)
        second_grant = _grant(second_action, approvers)
        second = SimulatedOutboxIntent(
            intent_id=str(uuid.uuid4()),
            candidate_id=candidate.candidate_id,
            action_id=second_action.action_id,
            authorization_id=second_grant.authorization_id,
            candidate_sha256=candidate_sha256(candidate),
            request_sha256=payload_sha256(second_action),
            idempotency_key=second_grant.idempotency_key,
            status=SimulatedOutboxStatus.PREPARED,
            created_at=_now(timedelta(seconds=1)),
            reconciliation_required=False,
        )
        self.assertNotEqual(first_action.action_id, second_action.action_id)
        self.assertNotEqual(first_grant.idempotency_key, second_grant.idempotency_key)
        self.assertEqual(
            self.control._production_effect_sha256(
                candidate.candidate_id, first_action, first_grant, first
            ),
            self.control._production_effect_sha256(
                candidate.candidate_id, second_action, second_grant, second
            ),
        )
        self.assertNotEqual(
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, first_action, first_grant, first
            ),
            self.control._dispatch_binding_sha256(
                candidate.candidate_id, second_action, second_grant, second
            ),
        )
        prepare = inspect.getsource(self.control.prepare_dispatch_dry_run)
        self.assertIn("stored_effect", prepare)
        self.assertIn('dispatch_snapshot(conn, str(existing["id"]))', prepare)

    def test_prepare_persists_all_inert_flags_and_no_attempt_identity(self):
        source = inspect.getsource(self.control.prepare_dispatch_dry_run)
        for required in (
            "TRUE, FALSE, FALSE, FALSE",
            '"network_call_performed": False',
            '"secret_resolution_performed": False',
            '"blind_retry_allowed": False',
            "release_binding_sha256",
            "authorization_version",
            "business_command_id",
            "production_effect_key",
        ):
            self.assertIn(required, source)
        self.assertNotIn("attempt_id", source)

    def test_prepare_enforces_daily_and_total_quota_before_insert(self):
        source = inspect.getsource(self.control.prepare_dispatch_dry_run)
        self.assertIn("_assert_dispatch_quota", source)
        self.assertLess(source.index("_assert_dispatch_quota"), source.index("INSERT INTO"))
        guard = inspect.getsource(self.control._assert_dispatch_quota)
        for required in (
            "COUNT(*) AS total_count",
            "COUNT(*) FILTER",
            ":day_start",
            ":day_end",
            "current.replace(hour=0, minute=0, second=0, microsecond=0)",
            "daily_action_limit",
            "max_simulated_actions_per_day",
            "max_simulated_actions_total",
        ):
            self.assertIn(required, guard)

        class Result:
            def __init__(self, daily: int, total: int):
                self.row = {"daily_count": daily, "total_count": total}

            def mappings(self):
                return self

            def first(self):
                return self.row

        class Connection:
            def __init__(self, daily: int, total: int):
                self.result = Result(daily, total)
                self.parameters = None

            def execute(self, statement, parameters=None):
                self.parameters = dict(parameters or {})
                return self.result

        candidate = _candidate()
        release = {"id": candidate.candidate_id, "daily_action_limit": 1}
        connection = Connection(0, 0)
        self.control._assert_dispatch_quota(
            connection, release, candidate, now="2026-08-24T23:59:59Z"
        )
        self.assertEqual(connection.parameters["day_start"], "2026-08-24T00:00:00Z")
        self.assertEqual(connection.parameters["day_end"], "2026-08-25T00:00:00Z")
        with self.assertRaises(self.control.ProductionDispatchStateError):
            self.control._assert_dispatch_quota(
                Connection(1, 1), release, candidate, now=_now()
            )
        with self.assertRaises(self.control.ProductionDispatchStateError):
            self.control._assert_dispatch_quota(
                Connection(0, candidate.max_simulated_actions_total),
                release,
                candidate,
                now=_now(),
            )

    def test_prepare_enforces_candidate_payload_byte_limit(self):
        candidate = _candidate()
        action = _action(candidate)
        self.control._assert_dispatch_payload_size(action, candidate)
        too_small = ProductionAdmissionCandidate(
            **{**candidate.__dict__, "max_payload_bytes": 1}
        )
        with self.assertRaises(self.control.ProductionDispatchStateError):
            self.control._assert_dispatch_payload_size(action, too_small)
        source = inspect.getsource(self.control.prepare_dispatch_dry_run)
        self.assertIn("_assert_dispatch_payload_size", source)
        self.assertLess(
            source.index("_assert_dispatch_payload_size"),
            source.index("INSERT INTO"),
        )

    def test_claim_enforces_capacity_and_ttl_inside_frozen_authority_window(self):
        source = inspect.getsource(self.control.claim_dispatch_dry_run)
        for required in (
            "_assert_claim_capacity",
            "grant.expires_at",
            "candidate.expires_at",
            "expires_at >=",
            "_lock_dispatch_scope",
        ):
            self.assertIn(required, source)
        self.assertLess(source.index("_assert_claim_capacity"), source.index("UPDATE"))
        capacity = inspect.getsource(self.control._assert_claim_capacity)
        self.assertIn("status='claimed'", capacity)
        self.assertIn("claim_expires_at > CAST(:now AS TIMESTAMPTZ)", capacity)
        self.assertIn("id<>CAST(:dispatch_id AS UUID)", capacity)
        self.assertIn('release["max_concurrency"]', capacity)

    def test_claimed_outcomes_revalidate_at_claim_time_after_halt_or_revocation(self):
        guard = inspect.getsource(self.control._revalidate_dispatch_authority)
        for required in (
            'historical_claim_outcome: bool = False',
            '_as_utc(row["claimed_at"])',
            '_as_utc(simulated_at) > decision_time',
            '_as_utc(halted_at) < decision_time',
            '_as_utc(grant.revoked_at) <= decision_time',
            'replace(grant, revoked_at=None)',
            'now=decision_time',
        ):
            self.assertIn(required, guard)
        finish = inspect.getsource(self.control._finish_claimed_dispatch)
        manual = inspect.getsource(self.control.move_dispatch_manual_review)
        claim = inspect.getsource(self.control.claim_dispatch_dry_run)
        self.assertIn("historical_claim_outcome=True", finish)
        self.assertIn("historical_claim_outcome=True", manual)
        self.assertNotIn("historical_claim_outcome=True", claim)

    def test_historical_outcome_survives_environment_degradation(self):
        source = inspect.getsource(
            self.control._revalidate_dispatch_authority
        )
        start = source.index("if historical_claim_outcome:")
        end = source.index("    else:", start)
        historical = source[start:end]
        current = source[end:]
        self.assertIn("_assert_candidate_current", historical)
        self.assertNotIn("_assess_candidate", historical)
        self.assertIn("_assess_candidate", current)

    def test_confirmation_needs_current_lease_but_unknown_and_review_do_not(self):
        finish = inspect.getsource(self.control._finish_claimed_dispatch)
        self.assertIn(
            "target_status is SimulatedOutboxStatus.DRY_RUN_CONFIRMED",
            finish,
        )
        unknown = inspect.getsource(self.control.mark_dispatch_unknown)
        self.assertIn("SimulatedOutboxStatus.UNKNOWN", unknown)
        manual = inspect.getsource(self.control.move_dispatch_manual_review)
        self.assertIn("require_current=False", manual)

    def test_lock_order_is_release_then_outbox_to_avoid_halt_deadlock(self):
        source = inspect.getsource(self.control._lock_dispatch_scope)
        release_lock = source.index("_release_row")
        child_lock = source.rindex("_dispatch_row")
        self.assertLess(release_lock, child_lock)
        self.assertIn("for_update=True", source[release_lock:])

    def test_emergency_halt_is_independent_of_candidate_and_approval_expiry(self):
        source = inspect.getsource(self.control.emergency_halt_production_release)
        self.assertIn("_candidate_from_release", source)
        self.assertIn("Release C8 perdió su frontera inerte persistida", source)
        self.assertNotIn("assert_c8_staging_boundary", source)
        self.assertNotIn("_prove_live_activation_unavailable", source)
        self.assertNotIn("_assess_candidate", source)
        self.assertNotIn("_assert_candidate_current", source)
        self.assertNotIn("_assert_dual_approval", source)
        self.assertLess(source.index("_candidate_from_release"), source.index("UPDATE"))

    def test_persistent_control_rejects_injected_environment_mappings(self):
        source = inspect.getsource(self.control._trusted_policy_values)
        self.assertIn("values is not os.environ", source)
        self.assertIn("rejects injected environment mappings", source)
        self.assertIs(self.control._trusted_policy_values(None), os.environ)
        self.assertIs(
            self.control._trusted_policy_values(os.environ), os.environ
        )
        with self.assertRaises(self.control.ProductionControlError):
            self.control._trusted_policy_values(dict(os.environ))

    def test_optimistic_version_and_claim_fence_are_in_every_claimed_update(self):
        claim = inspect.getsource(self.control.claim_dispatch_dry_run)
        finish = inspect.getsource(self.control._finish_claimed_dispatch)
        manual = inspect.getsource(self.control.move_dispatch_manual_review)
        self.assertIn("version=:expected_version", claim)
        self.assertIn("claim_fence=claim_fence + 1", claim)
        self.assertIn("claim_fence=0", claim)
        for source in (finish, manual):
            self.assertIn("version=:expected_version", source)
            self.assertIn("claim_token=CAST(:claim_token AS UUID)", source)
            self.assertIn("claim_fence=:claim_fence", source)
            self.assertIn("_assert_claim", source)

    def test_claim_token_projection_is_hash_only_deterministic_and_validated(self):
        digest = hashlib.sha256(b"c8 opaque claim token").hexdigest()
        projected = self.control._claim_token_uuid(digest)
        self.assertEqual(projected, self.control._claim_token_uuid(digest))
        self.assertEqual(projected, str(uuid.UUID(hex=digest[:32])))
        with self.assertRaises(self.control.ProductionControlError):
            self.control._claim_token_uuid("raw-secret-token")

    def test_unknown_has_one_way_manual_review_and_never_retry_or_confirm(self):
        unknown = inspect.getsource(self.control.mark_dispatch_unknown)
        manual = inspect.getsource(self.control.move_dispatch_manual_review)
        self.assertIn("SimulatedOutboxStatus.UNKNOWN", unknown)
        self.assertIn("status='unknown'", manual)
        self.assertIn("status='manual_review'", manual)
        self.assertNotIn("status='prepared'", manual)
        self.assertNotIn("status='claimed'", manual)
        self.assertNotIn("dry_run_confirmed_at", manual)
        self.assertIn("blind_retry_allowed", manual)
        self.assertNotIn("claim_owner=NULL", manual.replace(" ", ""))
        self.assertNotIn("claim_fence=0", manual.replace(" ", ""))

    def test_manual_review_retains_claim_and_no_cancellation_api_exists(self):
        manual = inspect.getsource(self.control.move_dispatch_manual_review)
        for required in (
            "claim_owner=:claim_owner",
            "claim_token=CAST(:claim_token AS UUID)",
            "claim_fence=:claim_fence",
        ):
            self.assertIn(required, manual)
        self.assertFalse(
            any(name.startswith("cancel_dispatch") for name in self.control.__all__)
        )

    def test_sqlalchemy_binds_json_with_cast_not_postgres_double_colon(self):
        self.assertIn("CAST(:metadata AS JSONB)", self.source)
        self.assertIn("CAST(:payload AS JSONB)", self.source)
        self.assertNotIn(":metadata::jsonb", self.source)
        self.assertNotIn(":payload::jsonb", self.source)

    def test_all_mutation_helpers_lock_and_event_ledgers_are_append_only(self):
        for name in (
            "approve_production_release",
            "mark_production_release_ready",
            "simulate_production_release_activation",
            "emergency_halt_production_release",
        ):
            source = inspect.getsource(getattr(self.control, name))
            self.assertIn("_release_row(", source)
            self.assertIn("for_update=True", source)
        for name in (
            "claim_dispatch_dry_run",
            "_finish_claimed_dispatch",
            "move_dispatch_manual_review",
        ):
            source = inspect.getsource(getattr(self.control, name))
            self.assertIn("_lock_dispatch_scope", source)
        self.assertNotRegex(
            self.source.upper(),
            r"\b(?:UPDATE|DELETE\s+FROM)\s+RTM_CONNECT_(?:PRODUCTION_RELEASE_EVENTS|DISPATCH_EVENTS)\b",
        )


if __name__ == "__main__":
    unittest.main()
