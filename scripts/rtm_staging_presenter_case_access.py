#!/usr/bin/env python3
"""Grant one synthetic operator access to the exact Presenter staging fixture.

The command is read-only by default. ``--apply`` requires a literal
confirmation and performs at most two insert-only mutations in one database
transaction: one A1-S tenant membership and one accepted case assignment.
It never creates users, changes role permissions, reads document bytes,
contacts portals, or exposes storage coordinates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCRIPT_VERSION = "rtm_staging_presenter_case_access_v1_0"
APPLY_CONFIRMATION = "STAGING_PRESENTER_SYNTHETIC_CASE_ACCESS_ONLY"
DEFAULT_FIXTURE_KEY = "runtime-a94dcd3-v1"
DEFAULT_OPERATOR_EMAIL = "rtm-staging-presenter-ramon@example.com"
A1S_MARKER = "RTM_A1S_SYNTHETIC_ONLY"
PRESENTER_MARKER = "RTM_PRESENTER_SYNTHETIC_ONLY"
MEMBERSHIP_ROLE = "executor"
ASSIGNMENT_ROLE = "reviewer"

_NAMESPACE = uuid.UUID("9a59059c-1f04-48f6-9018-f8302bd4904a")
_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"", "0", "false", "no", "off", "disabled"})


class PresenterCaseAccessError(RuntimeError):
    """The requested access grant does not satisfy the synthetic boundary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Vincula un operador sintético al caso Presenter A1-S de staging."
        )
    )
    parser.add_argument("--email", default=DEFAULT_OPERATOR_EMAIL)
    parser.add_argument("--fixture-key", default=DEFAULT_FIXTURE_KEY)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def _strict_flag(values: Mapping[str, str], name: str) -> bool | None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def safety_blockers(
    args: argparse.Namespace,
    values: Mapping[str, str] | None = None,
) -> list[str]:
    env = values if values is not None else os.environ
    blockers: list[str] = []
    if str(args.email or "").strip().lower() != DEFAULT_OPERATOR_EMAIL:
        blockers.append("operator_email_must_match_presenter_fixture")
    if str(args.fixture_key or "").strip() != DEFAULT_FIXTURE_KEY:
        blockers.append("fixture_key_must_match_presenter_fixture")
    if str(env.get("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in str(env.get("RTM_DATA_NAMESPACE") or "").lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if str(env.get("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")

    required_flags = {
        "RTM_ENABLE_OPERATOR_AUTH_V1": True,
        "RTM_ENABLE_PRESENTER_MVP": True,
        "RTM_PRESENTER_SYNTHETIC_ONLY": True,
        "RTM_ALLOW_REAL_CUSTOMER_DATA": False,
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": False,
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": False,
        "RTM_PRESENTER_MANAGED_EXTENSION_ATTESTATION_ENABLED": False,
        "RTM_ENABLE_EXTERNAL_SUBMISSION": False,
    }
    for name, required in required_flags.items():
        value = _strict_flag(env, name)
        if value is None:
            blockers.append(f"{name}_must_be_boolean")
        elif value is not required:
            blockers.append(f"{name}_must_be_{str(required).lower()}")

    if args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    return blockers


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _json_object(value: Any, *, error_code: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PresenterCaseAccessError(error_code) from exc
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise PresenterCaseAccessError(error_code)


def _require_marker(
    value: Any,
    *,
    marker: str,
    fixture_key: str | None = None,
    test_mode: bool | None = None,
    error_code: str,
) -> dict[str, Any]:
    payload = _json_object(value, error_code=error_code)
    if payload.get("synthetic_marker") != marker:
        raise PresenterCaseAccessError(error_code)
    if payload.get("synthetic_only") is not True:
        raise PresenterCaseAccessError(error_code)
    if fixture_key is not None and payload.get("fixture_key") != fixture_key:
        raise PresenterCaseAccessError(error_code)
    if test_mode is not None and payload.get("test_mode") is not test_mode:
        raise PresenterCaseAccessError(error_code)
    return payload


def expected_membership_metadata(
    *, fixture_key: str, case_id: str
) -> dict[str, Any]:
    return {
        "synthetic_marker": A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": fixture_key,
        "purpose": "rtm_presenter_case_access",
        "case_id": case_id,
        "real_data_used": False,
        "external_effects_executed": False,
    }


def expected_assignment_metadata(
    *, fixture_key: str
) -> dict[str, Any]:
    return {
        "synthetic_marker": PRESENTER_MARKER,
        "source_synthetic_marker": A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": fixture_key,
        "accepted_for": "rtm_presenter_synthetic_operator_access",
        "real_data_used": False,
        "external_effects_executed": False,
    }


def _mappings(result: Any) -> list[Mapping[str, Any]]:
    mappings = getattr(result, "mappings", None)
    if not callable(mappings):
        raise PresenterCaseAccessError("database_mapping_result_required")
    return list(mappings().all())


def _one_or_none(rows: Sequence[Mapping[str, Any]], error_code: str):
    if len(rows) > 1:
        raise PresenterCaseAccessError(error_code)
    return rows[0] if rows else None


def _load_access_plan(
    conn: Any,
    *,
    email: str,
    fixture_key: str,
) -> dict[str, Any]:
    from sqlalchemy import text
    from rtm_connect.human_filing_runtime import build_runtime_fixture_plan
    from rtm_core.operator_provisioning import normalize_synthetic_operator_email

    runtime_plan = build_runtime_fixture_plan(fixture_key=fixture_key)
    case_id = str(runtime_plan.case_id)
    normalized_email = normalize_synthetic_operator_email(email)

    operator = _one_or_none(
        _mappings(
            conn.execute(
                text(
                    """
                    SELECT o.id, o.email, o.status, o.profile,
                           r.code AS role_code
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r ON r.id=o.primary_role_id
                    WHERE lower(btrim(o.email))=:email
                    LIMIT 2
                    """
                ),
                {"email": normalized_email},
            )
        ),
        "synthetic_operator_not_unique",
    )
    if operator is None:
        raise PresenterCaseAccessError("synthetic_operator_not_found")
    profile = _json_object(
        operator.get("profile"), error_code="synthetic_operator_profile_invalid"
    )
    if (
        str(operator.get("status") or "") != "active"
        or str(operator.get("role_code") or "") != "rtm.operator"
        or profile.get("synthetic") is not True
        or profile.get("environment") != "staging"
    ):
        raise PresenterCaseAccessError("synthetic_operator_not_eligible")

    scope = _one_or_none(
        _mappings(
            conn.execute(
                text(
                    """
                    SELECT c.id AS case_id, COALESCE(c.test_mode,FALSE) AS test_mode,
                           b.id AS binding_id, b.tenant_id,
                           b.status AS binding_status,
                           b.synthetic_only AS binding_synthetic_only,
                           b.revoked_at AS binding_revoked_at,
                           b.metadata AS binding_metadata,
                           t.status AS tenant_status,
                           t.synthetic_only AS tenant_synthetic_only,
                           t.metadata AS tenant_metadata
                    FROM cases c
                    JOIN rtm_connect_a1s_case_bindings b ON b.case_id=c.id
                    JOIN rtm_connect_a1s_tenants t ON t.id=b.tenant_id
                    WHERE c.id=CAST(:case_id AS UUID)
                    LIMIT 2
                    """
                ),
                {"case_id": case_id},
            )
        ),
        "synthetic_case_scope_not_unique",
    )
    if scope is None:
        raise PresenterCaseAccessError("synthetic_case_scope_not_found")
    if (
        str(scope.get("case_id")) != case_id
        or str(scope.get("binding_id")) != str(runtime_plan.case_binding_id)
        or str(scope.get("tenant_id")) != str(runtime_plan.tenant_id)
        or scope.get("test_mode") is not True
        or str(scope.get("binding_status") or "") != "active"
        or scope.get("binding_synthetic_only") is not True
        or scope.get("binding_revoked_at") is not None
        or str(scope.get("tenant_status") or "") != "active"
        or scope.get("tenant_synthetic_only") is not True
    ):
        raise PresenterCaseAccessError("synthetic_case_scope_not_eligible")
    _require_marker(
        scope.get("binding_metadata"),
        marker=A1S_MARKER,
        fixture_key=fixture_key,
        test_mode=True,
        error_code="synthetic_case_binding_marker_invalid",
    )
    _require_marker(
        scope.get("tenant_metadata"),
        marker=A1S_MARKER,
        fixture_key=fixture_key,
        error_code="synthetic_tenant_marker_invalid",
    )

    grantors = _mappings(
        conn.execute(
            text(
                """
                SELECT m.id AS membership_id, m.operator_id, m.role,
                       m.status AS membership_status,
                       m.synthetic_only AS membership_synthetic_only,
                       m.revoked_at, m.metadata AS membership_metadata,
                       o.status AS operator_status,
                       o.profile AS operator_profile
                FROM rtm_connect_a1s_memberships m
                JOIN rtm_operators o ON o.id=m.operator_id
                WHERE m.tenant_id=CAST(:tenant_id AS UUID)
                  AND m.role='supervisor'
                  AND m.status='active'
                  AND m.synthetic_only=TRUE
                  AND m.revoked_at IS NULL
                  AND o.status='active'
                ORDER BY m.granted_at, m.id
                """
            ),
            {"tenant_id": str(scope["tenant_id"])},
        )
    )
    if len(grantors) != 1:
        raise PresenterCaseAccessError("synthetic_supervisor_grantor_not_unique")
    grantor = grantors[0]
    grantor_profile = _json_object(
        grantor.get("operator_profile"),
        error_code="synthetic_supervisor_profile_invalid",
    )
    if (
        grantor_profile.get("synthetic") is not True
        or grantor_profile.get("environment") != "staging"
    ):
        raise PresenterCaseAccessError("synthetic_supervisor_not_eligible")
    _require_marker(
        grantor.get("membership_metadata"),
        marker=A1S_MARKER,
        fixture_key=fixture_key,
        error_code="synthetic_supervisor_membership_marker_invalid",
    )

    operator_id = str(operator["id"])
    tenant_id = str(scope["tenant_id"])
    expected_membership = {
        "id": _stable_uuid(
            f"{fixture_key}:presenter-membership:{operator_id}:v1"
        ),
        "tenant_id": tenant_id,
        "principal_id": _stable_uuid(
            f"{fixture_key}:presenter-principal:{operator_id}:v1"
        ),
        "operator_id": operator_id,
        "role": MEMBERSHIP_ROLE,
        "status": "active",
        "synthetic_only": True,
        "granted_by_operator_id": str(grantor["operator_id"]),
        "metadata": expected_membership_metadata(
            fixture_key=fixture_key, case_id=case_id
        ),
    }
    expected_assignment = {
        "id": _stable_uuid(
            f"{fixture_key}:presenter-assignment:{operator_id}:{ASSIGNMENT_ROLE}:v1"
        ),
        "case_id": case_id,
        "operator_id": operator_id,
        "assignment_role": ASSIGNMENT_ROLE,
        "status": "active",
        "assigned_by": str(grantor["operator_id"]),
        "metadata": expected_assignment_metadata(fixture_key=fixture_key),
    }

    membership_rows = _mappings(
        conn.execute(
            text(
                """
                SELECT id, tenant_id, principal_id, operator_id, role, status,
                       synthetic_only, granted_by_operator_id,
                       revoked_by_operator_id, revoked_at, metadata
                FROM rtm_connect_a1s_memberships
                WHERE tenant_id=CAST(:tenant_id AS UUID)
                  AND operator_id=CAST(:operator_id AS UUID)
                LIMIT 2
                """
            ),
            {"tenant_id": tenant_id, "operator_id": operator_id},
        )
    )
    membership = _one_or_none(
        membership_rows, "presenter_operator_membership_not_unique"
    )

    assignments = _mappings(
        conn.execute(
            text(
                """
                SELECT id, case_id, attention_item_id, operator_id,
                       assignment_role, status, assigned_by, accepted_at,
                       released_at, metadata
                FROM rtm_work_assignments
                WHERE case_id=CAST(:case_id AS UUID)
                  AND operator_id=CAST(:operator_id AS UUID)
                  AND assignment_role IN ('responsible','reviewer','supervisor')
                ORDER BY created_at, id
                """
            ),
            {"case_id": case_id, "operator_id": operator_id},
        )
    )
    reviewer_slot = _mappings(
        conn.execute(
            text(
                """
                SELECT id, operator_id, status, accepted_at, released_at
                FROM rtm_work_assignments
                WHERE case_id=CAST(:case_id AS UUID)
                  AND assignment_role=:assignment_role
                  AND attention_item_id IS NULL
                  AND status='active'
                LIMIT 2
                """
            ),
            {"case_id": case_id, "assignment_role": ASSIGNMENT_ROLE},
        )
    )
    if len(reviewer_slot) > 1:
        raise PresenterCaseAccessError("presenter_reviewer_slot_not_unique")

    return {
        "fixture_key": fixture_key,
        "case_id": case_id,
        "email": normalized_email,
        "operator_id": operator_id,
        "tenant_id": tenant_id,
        "grantor_operator_id": str(grantor["operator_id"]),
        "expected_membership": expected_membership,
        "expected_assignment": expected_assignment,
        "membership": membership,
        "assignments": assignments,
        "reviewer_slot": reviewer_slot[0] if reviewer_slot else None,
    }


def _same_uuid(actual: Any, expected: Any) -> bool:
    try:
        return str(uuid.UUID(str(actual))) == str(uuid.UUID(str(expected)))
    except (TypeError, ValueError, AttributeError):
        return False


def _membership_ready(plan: Mapping[str, Any]) -> bool:
    actual = plan.get("membership")
    expected = plan["expected_membership"]
    if not isinstance(actual, Mapping):
        return False
    scalar_keys = ("id", "tenant_id", "principal_id", "operator_id")
    if any(not _same_uuid(actual.get(k), expected[k]) for k in scalar_keys):
        raise PresenterCaseAccessError("presenter_membership_collision")
    if (
        str(actual.get("role") or "") != expected["role"]
        or str(actual.get("status") or "") != "active"
        or actual.get("synthetic_only") is not True
        or not _same_uuid(
            actual.get("granted_by_operator_id"),
            expected["granted_by_operator_id"],
        )
        or actual.get("revoked_by_operator_id") is not None
        or actual.get("revoked_at") is not None
        or _json_object(
            actual.get("metadata"), error_code="presenter_membership_metadata_invalid"
        )
        != expected["metadata"]
    ):
        raise PresenterCaseAccessError("presenter_membership_collision")
    return True


def _assignment_ready(plan: Mapping[str, Any]) -> bool:
    expected = plan["expected_assignment"]
    rows = list(plan.get("assignments") or [])
    if len(rows) > 1:
        raise PresenterCaseAccessError("presenter_operator_assignment_not_unique")
    if not rows:
        slot = plan.get("reviewer_slot")
        if isinstance(slot, Mapping):
            raise PresenterCaseAccessError("presenter_reviewer_slot_in_use")
        return False
    actual = rows[0]
    if (
        not _same_uuid(actual.get("id"), expected["id"])
        or not _same_uuid(actual.get("case_id"), expected["case_id"])
        or not _same_uuid(actual.get("operator_id"), expected["operator_id"])
        or str(actual.get("assignment_role") or "") != expected["assignment_role"]
        or str(actual.get("status") or "") != "active"
        or not _same_uuid(actual.get("assigned_by"), expected["assigned_by"])
        or actual.get("attention_item_id") is not None
        or actual.get("accepted_at") is None
        or actual.get("released_at") is not None
        or _json_object(
            actual.get("metadata"), error_code="presenter_assignment_metadata_invalid"
        )
        != expected["metadata"]
    ):
        raise PresenterCaseAccessError("presenter_assignment_collision")
    return True


def audit_access(conn: Any, *, email: str, fixture_key: str) -> dict[str, Any]:
    from rtm_presenter_service import SqlPresenterRepository

    plan = _load_access_plan(conn, email=email, fixture_key=fixture_key)
    membership_ready = _membership_ready(plan)
    assignment_ready = _assignment_ready(plan)
    case_access = SqlPresenterRepository().has_active_synthetic_case_access(
        conn,
        case_id=plan["case_id"],
        operator_id=plan["operator_id"],
    )
    if membership_ready and assignment_ready and case_access is not True:
        raise PresenterCaseAccessError("presenter_case_access_not_effective")
    return {
        **plan,
        "membership_ready": membership_ready,
        "assignment_ready": assignment_ready,
        "case_access": case_access is True,
        "ready": membership_ready and assignment_ready and case_access is True,
        "would_insert_memberships": int(not membership_ready),
        "would_insert_work_assignments": int(not assignment_ready),
    }


def apply_access(conn: Any, *, email: str, fixture_key: str) -> dict[str, Any]:
    from sqlalchemy import text

    before = audit_access(conn, email=email, fixture_key=fixture_key)
    inserted = 0
    if not before["membership_ready"]:
        membership = dict(before["expected_membership"])
        membership["metadata"] = json.dumps(
            membership["metadata"], sort_keys=True, separators=(",", ":")
        )
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id, role, status,
                    synthetic_only, granted_by_operator_id, granted_at,
                    revoked_by_operator_id, revoked_at, version, metadata
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                    CAST(:principal_id AS UUID), CAST(:operator_id AS UUID),
                    :role, :status, TRUE,
                    CAST(:granted_by_operator_id AS UUID), NOW(),
                    NULL, NULL, 1, CAST(:metadata AS JSONB)
                ) ON CONFLICT DO NOTHING
                """
            ),
            membership,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if not before["assignment_ready"]:
        assignment = dict(before["expected_assignment"])
        assignment["metadata"] = json.dumps(
            assignment["metadata"], sort_keys=True, separators=(",", ":")
        )
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_work_assignments(
                    id, case_id, attention_item_id, operator_id,
                    assignment_role, status, assigned_by, assigned_at,
                    accepted_at, released_at, metadata, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID), NULL,
                    CAST(:operator_id AS UUID), :assignment_role, :status,
                    CAST(:assigned_by AS UUID), NOW(), NOW(), NULL,
                    CAST(:metadata AS JSONB), NOW(), NOW()
                ) ON CONFLICT DO NOTHING
                """
            ),
            assignment,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    after = audit_access(conn, email=email, fixture_key=fixture_key)
    if not after["ready"]:
        raise PresenterCaseAccessError("presenter_case_access_not_ready_after_insert")
    return {**after, "inserted_rows": inserted}


def _public_report(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "fixture_key": state["fixture_key"],
        "case_id": state["case_id"],
        "email": state["email"],
        "membership_role": MEMBERSHIP_ROLE,
        "assignment_role": ASSIGNMENT_ROLE,
        "membership_ready": bool(state["membership_ready"]),
        "assignment_ready": bool(state["assignment_ready"]),
        "case_access": bool(state["case_access"]),
        "ready": bool(state["ready"]),
        "would_insert_memberships": int(state["would_insert_memberships"]),
        "would_insert_work_assignments": int(
            state["would_insert_work_assignments"]
        ),
        "inserted_rows": int(state.get("inserted_rows", 0)),
        "synthetic_only": True,
        "real_data_used": False,
        "document_bytes_read": False,
        "storage_coordinates_read": False,
        "network_used": False,
        "external_effects_executed": False,
        "operator_role_changed": False,
        "operator_created": False,
    }


def _print(report: Mapping[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            dict(report),
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            default=str,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_presenter_case_access",
        "version": SCRIPT_VERSION,
        "environment": str(os.getenv("RTM_ENV") or "").strip().lower()
        or "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "blockers": [],
    }
    blockers = safety_blockers(args)
    if blockers:
        report["blockers"] = blockers
        _print(report, compact=args.compact)
        return 2

    try:
        from database import get_engine
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_presenter_policy import (
            load_presenter_runtime_configuration,
            require_presenter_runtime,
        )

        assert_environment_ready()
        require_presenter_runtime(
            load_presenter_runtime_configuration(require_enabled=True)
        )
        engine = get_engine()
        if args.apply:
            with engine.begin() as conn:
                state = apply_access(
                    conn, email=args.email, fixture_key=args.fixture_key
                )
        else:
            with engine.connect() as conn:
                state = audit_access(
                    conn, email=args.email, fixture_key=args.fixture_key
                )
        report.update(_public_report(state))
        report["ok"] = True
        report["safe"] = True
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
