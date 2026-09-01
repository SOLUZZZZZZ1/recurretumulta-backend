#!/usr/bin/env python3
"""Create one isolated Presenter training case for staging operator 02.

The command is read-only by default. ``--apply`` requires a literal
confirmation and performs only deterministic, insert-only mutations inside a
single transaction.  It never changes the original Ramon Presenter case or
its assignments, stores document bytes, resolves storage coordinates, or
contacts an external system.

The target identity and fixture are intentionally frozen.  This is not a
generic case-provisioning command.
"""

from __future__ import annotations

import argparse
import hashlib
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


SCRIPT_VERSION = "rtm_staging_presenter_training_fixture_v1_0"
APPLY_CONFIRMATION = "STAGING_PRESENTER_OPERATOR_02_TRAINING_FIXTURE_ONLY"
SOURCE_FIXTURE_KEY = "runtime-a94dcd3-v1"
TRAINING_FIXTURE_KEY = "presenter-training-operator-02-v1"
TARGET_OPERATOR_EMAIL = "rtm-staging-operador-02@example.com"
A1S_MARKER = "RTM_A1S_SYNTHETIC_ONLY"
PRESENTER_MARKER = "RTM_PRESENTER_SYNTHETIC_ONLY"
PROFILE_CODE = "synthetic.example"
PROFILE_VERSION = 4
MEMBERSHIP_ROLE = "executor"
ASSIGNMENT_ROLE = "responsible"

_NAMESPACE = uuid.UUID("f57ad946-4119-44b3-a04f-98e103cae51f")
_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"", "0", "false", "no", "off", "disabled"})


class PresenterTrainingFixtureError(RuntimeError):
    """The requested training fixture violates the staging boundary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audita o crea el caso Presenter sintético y separado del "
            "operador 02 en staging."
        )
    )
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
    """Fail closed before importing the database engine."""

    from scripts.rtm_staging_presenter_schema import (
        safety_blockers as presenter_schema_blockers,
    )

    env = values if values is not None else os.environ
    blockers = list(presenter_schema_blockers(values=env))
    required_flags = {
        "RTM_ENABLE_OPERATOR_AUTH_V1": True,
        "RTM_ENABLE_OPERATOR_ADMIN_V1": True,
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1": True,
        "RTM_ENABLE_PRESENTER_MVP": True,
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
    return list(dict.fromkeys(blockers))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _same_uuid(actual: Any, expected: Any) -> bool:
    try:
        return str(uuid.UUID(str(actual))) == str(uuid.UUID(str(expected)))
    except (TypeError, ValueError, AttributeError):
        return False


def _json_object(value: Any, *, error_code: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PresenterTrainingFixtureError(error_code) from exc
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise PresenterTrainingFixtureError(error_code)


def _mappings(result: Any) -> list[Mapping[str, Any]]:
    mappings = getattr(result, "mappings", None)
    if not callable(mappings):
        raise PresenterTrainingFixtureError("database_mapping_result_required")
    return list(mappings().all())


def _one_or_none(rows: Sequence[Mapping[str, Any]], error_code: str):
    if len(rows) > 1:
        raise PresenterTrainingFixtureError(error_code)
    return rows[0] if rows else None


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
        raise PresenterTrainingFixtureError(error_code)
    if payload.get("synthetic_only") is not True:
        raise PresenterTrainingFixtureError(error_code)
    if fixture_key is not None and payload.get("fixture_key") != fixture_key:
        raise PresenterTrainingFixtureError(error_code)
    if test_mode is not None and payload.get("test_mode") is not test_mode:
        raise PresenterTrainingFixtureError(error_code)
    return payload


def _training_bytes(kind: str) -> bytes:
    return (
        "RTM PRESENTER SYNTHETIC TRAINING FIXTURE\n"
        f"fixture={TRAINING_FIXTURE_KEY}\n"
        f"kind={kind}\n"
        "NO REAL CUSTOMER DATA\n"
        "NO EXTERNAL EFFECTS\n"
    ).encode("utf-8")


def _scope_metadata(*, test_mode: bool = False) -> dict[str, Any]:
    payload = {
        "synthetic_marker": A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": TRAINING_FIXTURE_KEY,
        "source_fixture_key": SOURCE_FIXTURE_KEY,
        "purpose": "presenter_operator_02_training",
        "training_only": True,
        "real_data_used": False,
        "network_used": False,
        "b2_used": False,
        "external_effects_executed": False,
    }
    if test_mode:
        payload["test_mode"] = True
    return payload


def _assignment_metadata() -> dict[str, Any]:
    return {
        "synthetic_marker": PRESENTER_MARKER,
        "source_synthetic_marker": A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": TRAINING_FIXTURE_KEY,
        "source_fixture_key": SOURCE_FIXTURE_KEY,
        "accepted_for": "rtm_presenter_operator_02_training",
        "training_only": True,
        "real_data_used": False,
        "external_effects_executed": False,
    }


def _document_metadata(*, purpose: str) -> dict[str, Any]:
    return {
        "synthetic_marker": PRESENTER_MARKER,
        "source_synthetic_marker": A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": TRAINING_FIXTURE_KEY,
        "purpose": purpose,
        "training_only": True,
        "bytes_stored": False,
        "storage_coordinates_stored": False,
        "real_data_used": False,
        "network_used": False,
        "b2_used": False,
        "external_effects_executed": False,
    }


def build_training_plan(
    *,
    operator_id: str,
    grantor_operator_id: str,
) -> dict[str, Any]:
    """Build a deterministic insert-only plan without performing I/O."""

    normalized = []
    for value in (operator_id, grantor_operator_id):
        try:
            normalized.append(str(uuid.UUID(str(value))))
        except (TypeError, ValueError, AttributeError) as exc:
            raise PresenterTrainingFixtureError(
                "training_fixture_authority_uuid_invalid"
            ) from exc
    operator_id, grantor_operator_id = normalized
    if operator_id == grantor_operator_id:
        raise PresenterTrainingFixtureError(
            "training_operator_must_not_be_grantor"
        )

    tenant_id = _stable_uuid(f"{TRAINING_FIXTURE_KEY}:tenant:v1")
    tenant_code = f"a1s-synthetic-{TRAINING_FIXTURE_KEY}"
    case_id = _stable_uuid(f"{TRAINING_FIXTURE_KEY}:case:v1")
    document_specs = (
        (
            "main",
            "rtm_presenter_synthetic_training_input_fixture",
            "main_filing",
            "synthetic_training_filing_input.txt",
            "text/plain",
        ),
        (
            "receipt",
            "rtm_presenter_synthetic_training_receipt_fixture",
            "submission_receipt",
            "synthetic_training_submission_receipt.json",
            "application/json",
        ),
    )
    documents: list[dict[str, Any]] = []
    document_versions: list[dict[str, Any]] = []
    for label, kind, purpose, filename, mime in document_specs:
        material = _training_bytes(label)
        document_id = _stable_uuid(
            f"{TRAINING_FIXTURE_KEY}:source-document:{label}:v1"
        )
        digest = _sha256_bytes(material)
        size_bytes = len(material)
        documents.append(
            {
                "id": document_id,
                "case_id": case_id,
                "kind": kind,
                "b2_bucket": None,
                "b2_key": None,
                "sha256": digest,
                "mime": mime,
                "size_bytes": size_bytes,
            }
        )
        document_versions.append(
            {
                "id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:document-version:{label}:v1"
                ),
                "case_id": case_id,
                "logical_document_id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:logical-document:{label}:v1"
                ),
                "version_number": 1,
                "supersedes_version_id": None,
                "source_document_id": document_id,
                "sha256": digest,
                "purpose": purpose,
                "state": "active",
                "scan_status": "clean",
                "original_filename": filename,
                "detected_mime": mime,
                "size_bytes": size_bytes,
                "source_kind": "generated",
                "created_by_operator_id": grantor_operator_id,
                "metadata": _document_metadata(purpose=purpose),
            }
        )

    snapshot = _canonical_sha256(
        {
            "case_id": case_id,
            "document_hashes": [row["sha256"] for row in documents],
            "fixture_key": TRAINING_FIXTURE_KEY,
            "source_fixture_key": SOURCE_FIXTURE_KEY,
            "synthetic_marker": A1S_MARKER,
            "synthetic_only": True,
            "test_mode": True,
            "training_only": True,
        }
    )
    return {
        "fixture_key": TRAINING_FIXTURE_KEY,
        "source_fixture_key": SOURCE_FIXTURE_KEY,
        "target_email": TARGET_OPERATOR_EMAIL,
        "tenant": {
            "id": tenant_id,
            "tenant_code": tenant_code,
            "display_name": "RTM PRESENTER SYNTHETIC TRAINING 02",
            "status": "active",
            "synthetic_only": True,
            "metadata": _scope_metadata(),
        },
        "case": {
            "id": case_id,
            "status": "core_review_pending",
            "test_mode": True,
        },
        "documents": tuple(documents),
        "document_versions": tuple(document_versions),
        "binding": {
            "id": _stable_uuid(f"{TRAINING_FIXTURE_KEY}:case-binding:v1"),
            "tenant_id": tenant_id,
            "case_id": case_id,
            "binding_code": f"rtm-a1s-binding-{snapshot[:24]}",
            "status": "active",
            "synthetic_only": True,
            "case_snapshot_sha256": snapshot,
            "bound_by_operator_id": grantor_operator_id,
            "version": 1,
            "metadata": _scope_metadata(test_mode=True),
        },
        "memberships": (
            {
                "id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:membership:grantor:v1"
                ),
                "tenant_id": tenant_id,
                "principal_id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:principal:grantor:v1"
                ),
                "operator_id": grantor_operator_id,
                "role": "supervisor",
                "status": "active",
                "synthetic_only": True,
                "granted_by_operator_id": grantor_operator_id,
                "version": 1,
                "metadata": {
                    **_scope_metadata(),
                    "purpose": "presenter_operator_02_training_supervision",
                },
            },
            {
                "id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:membership:{operator_id}:v1"
                ),
                "tenant_id": tenant_id,
                "principal_id": _stable_uuid(
                    f"{TRAINING_FIXTURE_KEY}:principal:{operator_id}:v1"
                ),
                "operator_id": operator_id,
                "role": MEMBERSHIP_ROLE,
                "status": "active",
                "synthetic_only": True,
                "granted_by_operator_id": grantor_operator_id,
                "version": 1,
                "metadata": _scope_metadata(),
            },
        ),
        "assignment": {
            "id": _stable_uuid(
                f"{TRAINING_FIXTURE_KEY}:assignment:{operator_id}:v1"
            ),
            "case_id": case_id,
            "attention_item_id": None,
            "operator_id": operator_id,
            "assignment_role": ASSIGNMENT_ROLE,
            "status": "active",
            "team_code": None,
            "assigned_by": grantor_operator_id,
            "release_reason": None,
            "metadata": _assignment_metadata(),
        },
    }


def load_training_authority(conn: Any) -> dict[str, Any]:
    """Load only the exact operator and the canonical synthetic source scope."""

    from sqlalchemy import text
    from rtm_connect.human_filing_runtime import build_runtime_fixture_plan

    operator_rows = _mappings(
        conn.execute(
            text(
                """
                SELECT o.id, o.email, o.status, o.must_change_password,
                       o.mfa_required,
                       (o.locked_until IS NULL OR o.locked_until<=NOW())
                         AS unlocked,
                       o.profile, r.code AS role_code
                FROM rtm_operators o
                JOIN rtm_operator_roles r ON r.id=o.primary_role_id
                WHERE lower(btrim(o.email))=:email
                LIMIT 2
                """
            ),
            {"email": TARGET_OPERATOR_EMAIL},
        )
    )
    operator = _one_or_none(
        operator_rows, "training_operator_not_unique"
    )
    if operator is None:
        raise PresenterTrainingFixtureError("training_operator_not_found")
    profile = _json_object(
        operator.get("profile"), error_code="training_operator_profile_invalid"
    )
    if (
        str(operator.get("email") or "").strip().lower()
        != TARGET_OPERATOR_EMAIL
        or str(operator.get("status") or "") != "active"
        or str(operator.get("role_code") or "") != "rtm.operator"
        or operator.get("mfa_required") is not False
        or operator.get("unlocked") is not True
        or profile.get("synthetic") is not True
        or profile.get("environment") != "staging"
        or profile.get("purpose") != "controlled_operator_lifecycle"
    ):
        raise PresenterTrainingFixtureError("training_operator_not_eligible")

    source_plan = build_runtime_fixture_plan(fixture_key=SOURCE_FIXTURE_KEY)
    scope_rows = _mappings(
        conn.execute(
            text(
                """
                SELECT c.id AS case_id,
                       COALESCE(c.test_mode,FALSE) AS case_test_mode,
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
                  AND b.id=CAST(:binding_id AS UUID)
                  AND t.id=CAST(:tenant_id AS UUID)
                  AND b.binding_code=:binding_code
                  AND b.case_snapshot_sha256=:snapshot
                LIMIT 2
                """
            ),
            {
                "case_id": source_plan.case_id,
                "binding_id": source_plan.case_binding_id,
                "tenant_id": source_plan.tenant_id,
                "binding_code": source_plan.case_binding_code,
                "snapshot": source_plan.case_snapshot_sha256,
            },
        )
    )
    scope = _one_or_none(scope_rows, "source_fixture_scope_not_unique")
    if scope is None:
        raise PresenterTrainingFixtureError("source_fixture_scope_not_found")
    if (
        str(scope.get("case_id")) != str(source_plan.case_id)
        or str(scope.get("binding_id")) != str(source_plan.case_binding_id)
        or str(scope.get("tenant_id")) != str(source_plan.tenant_id)
        or scope.get("case_test_mode") is not True
        or str(scope.get("binding_status") or "") != "active"
        or scope.get("binding_synthetic_only") is not True
        or scope.get("binding_revoked_at") is not None
        or str(scope.get("tenant_status") or "") != "active"
        or scope.get("tenant_synthetic_only") is not True
    ):
        raise PresenterTrainingFixtureError("source_fixture_scope_not_eligible")
    _require_marker(
        scope.get("binding_metadata"),
        marker=A1S_MARKER,
        fixture_key=SOURCE_FIXTURE_KEY,
        test_mode=True,
        error_code="source_fixture_binding_marker_invalid",
    )
    _require_marker(
        scope.get("tenant_metadata"),
        marker=A1S_MARKER,
        fixture_key=SOURCE_FIXTURE_KEY,
        error_code="source_fixture_tenant_marker_invalid",
    )

    grantors = _mappings(
        conn.execute(
            text(
                """
                SELECT m.operator_id, m.metadata AS membership_metadata,
                       o.status AS operator_status,
                       o.must_change_password, o.mfa_required,
                       (o.locked_until IS NULL OR o.locked_until<=NOW())
                         AS unlocked,
                       o.profile AS operator_profile
                FROM rtm_connect_a1s_memberships m
                JOIN rtm_operators o ON o.id=m.operator_id
                WHERE m.tenant_id=CAST(:tenant_id AS UUID)
                  AND m.role='supervisor'
                  AND m.status='active'
                  AND m.synthetic_only=TRUE
                  AND m.revoked_at IS NULL
                ORDER BY m.granted_at, m.id
                """
            ),
            {"tenant_id": str(scope["tenant_id"])},
        )
    )
    if len(grantors) != 1:
        raise PresenterTrainingFixtureError(
            "source_fixture_supervisor_not_unique"
        )
    grantor = grantors[0]
    grantor_profile = _json_object(
        grantor.get("operator_profile"),
        error_code="source_fixture_supervisor_profile_invalid",
    )
    if (
        str(grantor.get("operator_status") or "") != "active"
        or grantor.get("must_change_password") is not False
        or grantor.get("mfa_required") is not False
        or grantor.get("unlocked") is not True
        or grantor_profile.get("synthetic") is not True
        or grantor_profile.get("environment") != "staging"
    ):
        raise PresenterTrainingFixtureError(
            "source_fixture_supervisor_not_eligible"
        )
    _require_marker(
        grantor.get("membership_metadata"),
        marker=A1S_MARKER,
        fixture_key=SOURCE_FIXTURE_KEY,
        error_code="source_fixture_supervisor_membership_marker_invalid",
    )

    profile_count = int(
        conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM rtm_presenter_destination_profiles
                WHERE profile_code=:profile_code
                  AND version_number=:profile_version
                  AND status='active'
                  AND portal_origin='https://synthetic.example'
                  AND metadata @> CAST(:marker AS JSONB)
                """
            ),
            {
                "profile_code": PROFILE_CODE,
                "profile_version": PROFILE_VERSION,
                "marker": _canonical_json(
                    {
                        "synthetic_marker": PRESENTER_MARKER,
                        "synthetic_only": True,
                    }
                ),
            },
        ).scalar_one()
    )
    if profile_count != 1:
        raise PresenterTrainingFixtureError(
            "synthetic_destination_profile_not_unique"
        )

    return {
        "tenant_id": str(scope["tenant_id"]),
        "operator_id": str(operator["id"]),
        "grantor_operator_id": str(grantor["operator_id"]),
        "operator_password_change_required": bool(
            operator.get("must_change_password")
        ),
    }


def _row_json(result: Any) -> list[Mapping[str, Any]]:
    rows = _mappings(result)
    decoded: list[Mapping[str, Any]] = []
    for row in rows:
        payload = row.get("row_data")
        decoded.append(
            _json_object(payload, error_code="database_row_json_invalid")
        )
    return decoded


def load_fixture_state(conn: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Load exact candidate rows and detect cross-scope identity collisions."""

    from sqlalchemy import bindparam, text

    case_id = str(plan["case"]["id"])
    document_ids = [str(row["id"]) for row in plan["documents"]]
    version_ids = [str(row["id"]) for row in plan["document_versions"]]
    membership_ids = [str(row["id"]) for row in plan["memberships"]]
    principal_ids = [str(row["principal_id"]) for row in plan["memberships"]]

    tenant_rows = _row_json(
        conn.execute(
            text(
                """
                SELECT to_jsonb(t) AS row_data
                FROM rtm_connect_a1s_tenants t
                WHERE t.id=CAST(:tenant_id AS UUID)
                   OR t.tenant_code=:tenant_code
                ORDER BY t.id
                """
            ),
            {
                "tenant_id": plan["tenant"]["id"],
                "tenant_code": plan["tenant"]["tenant_code"],
            },
        )
    )

    case_rows = _row_json(
        conn.execute(
            text(
                "SELECT to_jsonb(c) AS row_data FROM cases c "
                "WHERE c.id=CAST(:case_id AS UUID)"
            ),
            {"case_id": case_id},
        )
    )
    documents_statement = text(
        """
        SELECT to_jsonb(d) AS row_data
        FROM documents d
        WHERE d.case_id=CAST(:case_id AS UUID) OR d.id IN :document_ids
        ORDER BY d.id
        """
    ).bindparams(bindparam("document_ids", expanding=True))
    document_rows = _row_json(
        conn.execute(
            documents_statement,
            {"case_id": case_id, "document_ids": document_ids},
        )
    )
    versions_statement = text(
        """
        SELECT to_jsonb(v) AS row_data
        FROM rtm_presenter_document_versions v
        WHERE v.case_id=CAST(:case_id AS UUID) OR v.id IN :version_ids
        ORDER BY v.id
        """
    ).bindparams(bindparam("version_ids", expanding=True))
    version_rows = _row_json(
        conn.execute(
            versions_statement,
            {"case_id": case_id, "version_ids": version_ids},
        )
    )
    binding_rows = _row_json(
        conn.execute(
            text(
                """
                SELECT to_jsonb(b) AS row_data
                FROM rtm_connect_a1s_case_bindings b
                WHERE b.case_id=CAST(:case_id AS UUID)
                   OR b.id=CAST(:binding_id AS UUID)
                   OR b.binding_code=:binding_code
                ORDER BY b.id
                """
            ),
            {
                "case_id": case_id,
                "binding_id": plan["binding"]["id"],
                "binding_code": plan["binding"]["binding_code"],
            },
        )
    )
    memberships_statement = text(
        """
        SELECT to_jsonb(m) AS row_data
        FROM rtm_connect_a1s_memberships m
        WHERE m.tenant_id=CAST(:tenant_id AS UUID)
           OR m.id IN :membership_ids
           OR m.principal_id IN :principal_ids
        ORDER BY m.id
        """
    ).bindparams(
        bindparam("membership_ids", expanding=True),
        bindparam("principal_ids", expanding=True),
    )
    membership_rows = _row_json(
        conn.execute(
            memberships_statement,
            {
                "tenant_id": plan["tenant"]["id"],
                "membership_ids": membership_ids,
                "principal_ids": principal_ids,
            },
        )
    )
    assignment_rows = _row_json(
        conn.execute(
            text(
                """
                SELECT to_jsonb(w) AS row_data
                FROM rtm_work_assignments w
                WHERE w.case_id=CAST(:case_id AS UUID)
                   OR w.id=CAST(:assignment_id AS UUID)
                ORDER BY w.id
                """
            ),
            {
                "case_id": case_id,
                "assignment_id": plan["assignment"]["id"],
            },
        )
    )
    return {
        "tenant_rows": tenant_rows,
        "case_rows": case_rows,
        "document_rows": document_rows,
        "document_version_rows": version_rows,
        "binding_rows": binding_rows,
        "membership_rows": membership_rows,
        "assignment_rows": assignment_rows,
    }


def _compare_scalar(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, int):
        try:
            return int(actual) == expected
        except (TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def _row_matches(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    uuid_keys: Sequence[str] = (),
    json_keys: Sequence[str] = (),
) -> bool:
    for key, value in expected.items():
        if value is None:
            if actual.get(key) is not None:
                return False
        elif key in uuid_keys:
            if not _same_uuid(actual.get(key), value):
                return False
        elif key in json_keys:
            try:
                candidate = _json_object(
                    actual.get(key), error_code="fixture_metadata_invalid"
                )
            except PresenterTrainingFixtureError:
                return False
            if candidate != value:
                return False
        elif not _compare_scalar(actual.get(key), value):
            return False
    return True


def reconcile_fixture_state(
    *,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate every existing row; never repair or reinterpret collisions."""

    tenant_rows = list(state.get("tenant_rows") or ())
    if len(tenant_rows) > 1:
        raise PresenterTrainingFixtureError("training_tenant_not_unique")
    tenant_ready = False
    if tenant_rows:
        if not _row_matches(
            tenant_rows[0],
            plan["tenant"],
            uuid_keys=("id",),
            json_keys=("metadata",),
        ):
            raise PresenterTrainingFixtureError("training_tenant_collision")
        tenant_ready = True

    case_rows = list(state.get("case_rows") or ())
    if len(case_rows) > 1:
        raise PresenterTrainingFixtureError("training_case_not_unique")
    case_ready = False
    if case_rows:
        actual = case_rows[0]
        if not _row_matches(
            actual,
            plan["case"],
            uuid_keys=("id",),
        ):
            raise PresenterTrainingFixtureError("training_case_collision")
        ignored = {
            "id",
            "status",
            "test_mode",
            "created_at",
            "updated_at",
            "authorized",
            "channel",
            "override_deadlines",
        }
        unsafe_values = {
            key: value
            for key, value in actual.items()
            if key not in ignored and value is not None
        }
        if unsafe_values:
            raise PresenterTrainingFixtureError(
                "training_case_contains_unexpected_data"
            )
        if actual.get("authorized") not in (None, False):
            raise PresenterTrainingFixtureError(
                "training_case_authorization_forbidden"
            )
        if actual.get("channel") not in (None, "direct"):
            raise PresenterTrainingFixtureError(
                "training_case_channel_collision"
            )
        if actual.get("override_deadlines") not in (None, False):
            raise PresenterTrainingFixtureError(
                "training_case_deadline_override_forbidden"
            )
        case_ready = True

    expected_documents = {str(row["id"]): row for row in plan["documents"]}
    actual_documents = {
        str(row.get("id")): row for row in state.get("document_rows") or ()
    }
    if len(actual_documents) != len(state.get("document_rows") or ()):
        raise PresenterTrainingFixtureError("training_documents_not_unique")
    unexpected_documents = set(actual_documents) - set(expected_documents)
    if unexpected_documents:
        raise PresenterTrainingFixtureError("training_document_collision")
    document_ready: dict[str, bool] = {}
    for row_id, expected in expected_documents.items():
        actual = actual_documents.get(row_id)
        if actual is None:
            document_ready[row_id] = False
            continue
        if not _row_matches(
            actual,
            expected,
            uuid_keys=("id", "case_id"),
        ):
            raise PresenterTrainingFixtureError("training_document_collision")
        document_ready[row_id] = True

    expected_versions = {
        str(row["id"]): row for row in plan["document_versions"]
    }
    actual_versions = {
        str(row.get("id")): row
        for row in state.get("document_version_rows") or ()
    }
    if len(actual_versions) != len(state.get("document_version_rows") or ()):
        raise PresenterTrainingFixtureError(
            "training_document_versions_not_unique"
        )
    if set(actual_versions) - set(expected_versions):
        raise PresenterTrainingFixtureError(
            "training_document_version_collision"
        )
    version_ready: dict[str, bool] = {}
    for row_id, expected in expected_versions.items():
        actual = actual_versions.get(row_id)
        if actual is None:
            version_ready[row_id] = False
            continue
        if not _row_matches(
            actual,
            expected,
            uuid_keys=(
                "id",
                "case_id",
                "logical_document_id",
                "supersedes_version_id",
                "source_document_id",
                "created_by_operator_id",
            ),
            json_keys=("metadata",),
        ):
            raise PresenterTrainingFixtureError(
                "training_document_version_collision"
            )
        version_ready[row_id] = True

    singular_specs = (
        (
            "binding",
            "binding_rows",
            (
                "id",
                "tenant_id",
                "case_id",
                "bound_by_operator_id",
            ),
            ("metadata",),
        ),
        (
            "assignment",
            "assignment_rows",
            (
                "id",
                "case_id",
                "attention_item_id",
                "operator_id",
                "assigned_by",
            ),
            ("metadata",),
        ),
    )
    singular_ready: dict[str, bool] = {}
    for plan_key, state_key, uuid_keys, json_keys in singular_specs:
        rows = list(state.get(state_key) or ())
        if len(rows) > 1:
            raise PresenterTrainingFixtureError(
                f"training_{plan_key}_not_unique"
            )
        if not rows:
            singular_ready[plan_key] = False
            continue
        if not _row_matches(
            rows[0],
            plan[plan_key],
            uuid_keys=uuid_keys,
            json_keys=json_keys,
        ):
            raise PresenterTrainingFixtureError(
                f"training_{plan_key}_collision"
            )
        if plan_key == "binding" and (
            rows[0].get("revoked_at") is not None
            or rows[0].get("revoked_by_operator_id") is not None
        ):
            raise PresenterTrainingFixtureError("training_binding_revoked")
        if plan_key == "assignment" and (
            rows[0].get("accepted_at") is None
            or rows[0].get("released_at") is not None
        ):
            raise PresenterTrainingFixtureError(
                "training_assignment_not_active_accepted"
            )
        singular_ready[plan_key] = True

    expected_memberships = {
        str(row["id"]): row for row in plan["memberships"]
    }
    actual_memberships = {
        str(row.get("id")): row
        for row in state.get("membership_rows") or ()
    }
    if len(actual_memberships) != len(state.get("membership_rows") or ()):
        raise PresenterTrainingFixtureError("training_memberships_not_unique")
    if set(actual_memberships) - set(expected_memberships):
        raise PresenterTrainingFixtureError("training_membership_collision")
    membership_ready: dict[str, bool] = {}
    for row_id, expected in expected_memberships.items():
        actual = actual_memberships.get(row_id)
        if actual is None:
            membership_ready[row_id] = False
            continue
        if not _row_matches(
            actual,
            expected,
            uuid_keys=(
                "id",
                "tenant_id",
                "principal_id",
                "operator_id",
                "granted_by_operator_id",
            ),
            json_keys=("metadata",),
        ):
            raise PresenterTrainingFixtureError("training_membership_collision")
        if (
            actual.get("revoked_at") is not None
            or actual.get("revoked_by_operator_id") is not None
        ):
            raise PresenterTrainingFixtureError("training_membership_revoked")
        membership_ready[row_id] = True

    ready = bool(
        tenant_ready
        and case_ready
        and all(document_ready.values())
        and len(document_ready) == len(expected_documents)
        and all(version_ready.values())
        and len(version_ready) == len(expected_versions)
        and all(singular_ready.values())
        and all(membership_ready.values())
        and len(membership_ready) == len(expected_memberships)
    )
    return {
        "ready": ready,
        "tenant_ready": tenant_ready,
        "case_ready": case_ready,
        "missing_document_ids": sorted(
            row_id for row_id, value in document_ready.items() if not value
        ),
        "missing_document_version_ids": sorted(
            row_id for row_id, value in version_ready.items() if not value
        ),
        "missing_membership_ids": sorted(
            row_id for row_id, value in membership_ready.items() if not value
        ),
        "binding_ready": singular_ready["binding"],
        "assignment_ready": singular_ready["assignment"],
        "would_insert_tenants": int(not tenant_ready),
        "would_insert_cases": int(not case_ready),
        "would_insert_documents": sum(not value for value in document_ready.values()),
        "would_insert_document_versions": sum(
            not value for value in version_ready.values()
        ),
        "would_insert_case_bindings": int(not singular_ready["binding"]),
        "would_insert_memberships": sum(
            not value for value in membership_ready.values()
        ),
        "would_insert_work_assignments": int(not singular_ready["assignment"]),
    }


def audit_fixture(conn: Any) -> dict[str, Any]:
    from rtm_presenter_service import SqlPresenterRepository

    authority = load_training_authority(conn)
    plan = build_training_plan(
        operator_id=authority["operator_id"],
        grantor_operator_id=authority["grantor_operator_id"],
    )
    state = reconcile_fixture_state(
        plan=plan,
        state=load_fixture_state(conn, plan=plan),
    )
    access = SqlPresenterRepository().has_active_synthetic_case_access(
        conn,
        case_id=plan["case"]["id"],
        operator_id=authority["operator_id"],
    )
    if state["ready"] and access is not True:
        raise PresenterTrainingFixtureError(
            "training_case_access_not_effective"
        )
    return {
        **state,
        "fixture_key": TRAINING_FIXTURE_KEY,
        "source_fixture_key": SOURCE_FIXTURE_KEY,
        "case_id": plan["case"]["id"],
        "operator_email": TARGET_OPERATOR_EMAIL,
        "operator_password_change_required": authority[
            "operator_password_change_required"
        ],
        "case_access": access is True,
        "ready": bool(state["ready"] and access is True),
        "synthetic_destination_profile_ready": True,
        "original_case_touched": False,
        "original_assignments_touched": False,
        "real_data_used": False,
        "document_bytes_stored": False,
        "storage_coordinates_stored": False,
        "network_used": False,
        "external_effects_executed": False,
        "plan": plan,
    }


def insert_fixture(conn: Any, *, audit: Mapping[str, Any]) -> int:
    """Insert only absent deterministic rows; the caller owns the transaction."""

    from sqlalchemy import text

    plan = audit["plan"]
    inserted = 0

    if not audit["tenant_ready"]:
        values = dict(plan["tenant"])
        values["metadata"] = _canonical_json(values["metadata"])
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_connect_a1s_tenants(
                    id, tenant_code, display_name, status, synthetic_only,
                    metadata, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :tenant_code, :display_name, :status,
                    TRUE, CAST(:metadata AS JSONB), NOW(), NOW()
                ) ON CONFLICT DO NOTHING
                """
            ),
            values,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if not audit["case_ready"]:
        result = conn.execute(
            text(
                """
                INSERT INTO cases(id, status, test_mode, created_at, updated_at)
                VALUES (
                    CAST(:id AS UUID), :status, TRUE, NOW(), NOW()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            plan["case"],
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    missing_documents = set(audit["missing_document_ids"])
    for document in plan["documents"]:
        if str(document["id"]) not in missing_documents:
            continue
        result = conn.execute(
            text(
                """
                INSERT INTO documents(
                    id, case_id, kind, b2_bucket, b2_key, sha256, mime,
                    size_bytes, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID), :kind,
                    NULL, NULL, :sha256, :mime, :size_bytes, NOW()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            document,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    missing_versions = set(audit["missing_document_version_ids"])
    for document_version in plan["document_versions"]:
        if str(document_version["id"]) not in missing_versions:
            continue
        values = dict(document_version)
        values["metadata"] = _canonical_json(values["metadata"])
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_document_versions(
                    id, case_id, logical_document_id, version_number,
                    supersedes_version_id, source_document_id, sha256,
                    purpose, state, scan_status, original_filename,
                    detected_mime, size_bytes, source_kind,
                    created_by_operator_id, metadata
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID),
                    CAST(:logical_document_id AS UUID), :version_number,
                    CAST(:supersedes_version_id AS UUID),
                    CAST(:source_document_id AS UUID), :sha256, :purpose,
                    :state, :scan_status, :original_filename, :detected_mime,
                    :size_bytes, :source_kind,
                    CAST(:created_by_operator_id AS UUID),
                    CAST(:metadata AS JSONB)
                ) ON CONFLICT DO NOTHING
                """
            ),
            values,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if not audit["binding_ready"]:
        values = dict(plan["binding"])
        values["metadata"] = _canonical_json(values["metadata"])
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_connect_a1s_case_bindings(
                    id, tenant_id, case_id, binding_code, status,
                    synthetic_only, case_snapshot_sha256,
                    bound_by_operator_id, bound_at, version, metadata
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                    CAST(:case_id AS UUID), :binding_code, :status, TRUE,
                    :case_snapshot_sha256,
                    CAST(:bound_by_operator_id AS UUID), NOW(), :version,
                    CAST(:metadata AS JSONB)
                ) ON CONFLICT DO NOTHING
                """
            ),
            values,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    missing_memberships = set(audit["missing_membership_ids"])
    for membership in plan["memberships"]:
        if str(membership["id"]) not in missing_memberships:
            continue
        values = dict(membership)
        values["metadata"] = _canonical_json(values["metadata"])
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
                    NULL, NULL, :version, CAST(:metadata AS JSONB)
                ) ON CONFLICT DO NOTHING
                """
            ),
            values,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if not audit["assignment_ready"]:
        values = dict(plan["assignment"])
        values["metadata"] = _canonical_json(values["metadata"])
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
            values,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    return inserted


def _public_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in audit.items()
        if key != "plan"
    }


def _base_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_presenter_training_fixture",
        "version": SCRIPT_VERSION,
        "environment": str(os.getenv("RTM_ENV") or "").strip().lower()
        or "unset",
        "fixture_key": TRAINING_FIXTURE_KEY,
        "source_fixture_key": SOURCE_FIXTURE_KEY,
        "operator_email": TARGET_OPERATOR_EMAIL,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "read_only": not bool(args.apply),
        "transactional": True,
        "idempotent_insert_only": True,
        "separate_case": True,
        "original_case_touched": False,
        "original_assignments_touched": False,
        "operators_created": False,
        "credentials_created": False,
        "sessions_created": False,
        "document_bytes_stored": False,
        "storage_coordinates_stored": False,
        "network_used": False,
        "b2_used": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "production_authorized": False,
        "database_configuration_loaded": False,
        "database_connection_used": False,
        "database_identity_verified": False,
        "database_mutated": False,
        "transaction_committed": False,
        "blockers": [],
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
    report = _base_report(args)
    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        _print(report, compact=args.compact)
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine
        from scripts.rtm_staging_presenter_schema import (
            _database_identity_from_url,
            assert_database_identity,
            schema_snapshot,
        )

        expected_database, expected_role = _database_identity_from_url(os.environ)
        report["database_configuration_loaded"] = True
        engine = get_engine()

        if args.apply:
            with engine.begin() as conn:
                report["database_connection_used"] = True
                report["connected_database"] = assert_database_identity(
                    conn,
                    expected_database_name=expected_database,
                    expected_database_role=expected_role,
                )
                report["database_identity_verified"] = True
                snapshot = schema_snapshot(conn)
                report["presenter_schema_ready"] = bool(snapshot["ready"])
                if not snapshot["ready"]:
                    raise PresenterTrainingFixtureError(
                        "presenter_schema_not_ready"
                    )
                before = audit_fixture(conn)
                report["before"] = _public_audit(before)
                inserted = insert_fixture(conn, audit=before)
                report["inserted_rows"] = inserted
                report["database_mutated"] = inserted > 0
                inside = audit_fixture(conn)
                if not inside["ready"]:
                    raise PresenterTrainingFixtureError(
                        "training_fixture_not_ready_before_commit"
                    )
            report["transaction_committed"] = True
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    assert_database_identity(
                        conn,
                        expected_database_name=expected_database,
                        expected_database_role=expected_role,
                    )
                    audit = audit_fixture(conn)
                finally:
                    transaction.rollback()
        else:
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    report["database_connection_used"] = True
                    report["connected_database"] = assert_database_identity(
                        conn,
                        expected_database_name=expected_database,
                        expected_database_role=expected_role,
                    )
                    report["database_identity_verified"] = True
                    snapshot = schema_snapshot(conn)
                    report["presenter_schema_ready"] = bool(snapshot["ready"])
                    if not snapshot["ready"]:
                        raise PresenterTrainingFixtureError(
                            "presenter_schema_not_ready"
                        )
                    audit = audit_fixture(conn)
                finally:
                    transaction.rollback()

        report["audit"] = _public_audit(audit)
        report["fixture_ready"] = bool(audit["ready"])
        report["safe"] = True
        report["ok"] = True
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["blockers"].append("presenter_training_fixture_operation_failed")
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
