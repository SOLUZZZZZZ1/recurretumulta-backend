"""Provisioning and audit helpers for the synthetic A1-S runtime.

This module materialises only local PostgreSQL fixtures.  It never creates
operator credentials or sessions, opens network connections, accesses B2, or
contacts a provider or public administration.  The caller must supply three
already-existing, distinct synthetic operators: requester/executor, releaser,
and verifier.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.human_filing_contracts import (
    HUMAN_FILING_AUTHORITY_CODE,
    HUMAN_FILING_AUTHORITY_VERSION,
    HUMAN_FILING_CAPABILITY,
    HUMAN_FILING_CONTRACT_VERSION,
    HUMAN_FILING_MARKER,
    HUMAN_FILING_SATELLITE,
    HUMAN_FILING_TARGET_REF,
    HUMAN_FILING_TARGET_TYPE,
    RepresentationKind,
    canonical_sha256,
)
from rtm_connect.human_filing_policy import (
    expected_a1s_action_payload,
    validate_a1s_action_authority,
)
from rtm_connect.human_filing_repository import load_action_and_grant
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.kernel import authorize_action, create_action
from rtm_connect.schema import CONNECT_C1_REQUIRED_COLUMNS
from rtm_connect.human_filing_schema import CONNECT_A1S_REQUIRED_COLUMNS


RTM_CONNECT_A1S_RUNTIME_VERSION = "rtm_connect_a1s_runtime_v1_0"
RUNTIME_FIXTURE_CODE = "a1s-synthetic-runtime-v1"
RUNTIME_FIXTURE_CONFIRMATION = "STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY"

_RUNTIME_NAMESPACE = uuid.UUID("f4ca2e9a-8820-5b45-b74c-42a17fbc78e8")
_RUNTIME_EPOCH = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
_RUNTIME_DUE_DAYS = 365
_RUNTIME_AUTHORITY_DAYS = 730
_RUNTIME_REQUIRED_COLUMNS: dict[str, set[str]] = {
    table: set(columns)
    for table, columns in {
        **CONNECT_C1_REQUIRED_COLUMNS,
        **CONNECT_A1S_REQUIRED_COLUMNS,
    }.items()
}
_RUNTIME_REQUIRED_COLUMNS.update({
    "cases": {"id", "status", "test_mode", "created_at", "updated_at"},
    "documents": {
        "id", "case_id", "kind", "b2_bucket", "b2_key", "sha256",
        "mime", "size_bytes", "created_at",
    },
    "rtm_operator_roles": {"id", "code", "permissions", "active"},
    "rtm_operators": {
        "id", "status", "primary_role_id", "must_change_password",
        "mfa_required", "locked_until", "profile", "auth_epoch",
    },
})
_REQUIRED_TABLES = tuple(sorted(_RUNTIME_REQUIRED_COLUMNS))


class HumanFilingRuntimeError(RuntimeError):
    """The requested runtime fixture does not satisfy the A1-S boundary."""


@dataclass(frozen=True)
class RuntimeOperators:
    requester_executor_id: str
    releaser_id: str
    verifier_id: str

    def __post_init__(self) -> None:
        normalized = tuple(
            str(uuid.UUID(value))
            for value in (
                self.requester_executor_id,
                self.releaser_id,
                self.verifier_id,
            )
        )
        if len(set(normalized)) != 3:
            raise HumanFilingRuntimeError(
                "A1-S Runtime exige tres operadores sintéticos distintos"
            )
        object.__setattr__(self, "requester_executor_id", normalized[0])
        object.__setattr__(self, "releaser_id", normalized[1])
        object.__setattr__(self, "verifier_id", normalized[2])


@dataclass(frozen=True)
class RuntimeFixturePlan:
    fixture_key: str
    tenant_id: str
    requester_membership_id: str
    requester_principal_id: str
    releaser_membership_id: str
    releaser_principal_id: str
    verifier_membership_id: str
    verifier_principal_id: str
    case_id: str
    input_document_id: str
    input_document_sha256: str
    input_document_size: int
    receipt_document_id: str
    receipt_document_sha256: str
    receipt_document_size: int
    case_binding_id: str
    case_binding_code: str
    case_snapshot_sha256: str
    representation_id: str
    representation_code: str
    representation_evidence_sha256: str
    representation_subject_sha256: str
    action_id: str
    authorization_id: str
    correlation_id: str
    requested_at: str
    authorized_at: str
    expires_at: str
    due_at: str
    representation_valid_from: str
    representation_expires_at: str

    def public_manifest(self) -> dict[str, Any]:
        manifest = asdict(self)
        manifest.update(
            {
                "runtime_version": RTM_CONNECT_A1S_RUNTIME_VERSION,
                "contract_version": HUMAN_FILING_CONTRACT_VERSION,
                "synthetic_marker": HUMAN_FILING_MARKER,
                "synthetic_only": True,
                "network_used": False,
                "b2_used": False,
                "provider_contacted": False,
                "administration_contacted": False,
                "real_data_used": False,
                "external_effects_executed": False,
                "production_authorized": False,
            }
        )
        return manifest


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(fixture_key: str, label: str) -> str:
    return str(uuid.uuid5(_RUNTIME_NAMESPACE, f"{fixture_key}:{label}"))


def _bytes(fixture_key: str, label: str) -> bytes:
    return (
        "RTM CONNECT A1-S RUNTIME SYNTHETIC FIXTURE\n"
        f"fixture={fixture_key}\n"
        f"kind={label}\n"
        "NO REAL CUSTOMER DATA\n"
        "NO PROVIDER OR ADMINISTRATION CONTACT\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _metadata(fixture_key: str, **extra: Any) -> dict[str, Any]:
    return {
        "synthetic_marker": HUMAN_FILING_MARKER,
        "synthetic_only": True,
        "runtime_version": RTM_CONNECT_A1S_RUNTIME_VERSION,
        "fixture_key": fixture_key,
        "network_used": False,
        "b2_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "real_data_used": False,
        "external_effects_executed": False,
        **extra,
    }


def runtime_tenant_code(fixture_key: str) -> str:
    return f"a1s-synthetic-{fixture_key}"


def build_runtime_fixture_plan(
    *,
    fixture_key: str,
    now: datetime | None = None,
) -> RuntimeFixturePlan:
    """Builds an in-memory, hash-bound plan; it performs no I/O."""

    clean_key = str(fixture_key or "").strip().lower()
    if len(clean_key) < 3 or len(clean_key) > 48 or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in clean_key
    ):
        raise HumanFilingRuntimeError("fixture_key A1-S no válida")
    if now is not None and now.tzinfo is None:
        raise HumanFilingRuntimeError("now A1-S debe incluir zona horaria")
    # The persistent fixture is deliberately reproducible.  A successor must
    # freeze a new runtime version, epoch and fixture key instead of silently
    # rewriting these authority timestamps or UUIDs.
    current = (now or _RUNTIME_EPOCH).astimezone(timezone.utc)
    input_bytes = _bytes(clean_key, "filing-input")
    receipt_bytes = _bytes(clean_key, "receipt-output")
    input_sha = _sha256_bytes(input_bytes)
    receipt_sha = _sha256_bytes(receipt_bytes)
    if input_sha == receipt_sha:
        raise HumanFilingRuntimeError("El recibo debe ser disjunto del paquete")

    tenant_id = _uuid(clean_key, "tenant")
    case_id = _uuid(clean_key, "case")
    binding_id = _uuid(clean_key, "case-binding")
    representation_id = _uuid(clean_key, "representation")
    case_snapshot = canonical_sha256(
        {
            "case_id": case_id,
            "document_hashes": [input_sha],
            "fixture_key": clean_key,
            "synthetic_marker": HUMAN_FILING_MARKER,
            "synthetic_only": True,
            "test_mode": True,
        }
    )
    representation_payload = {
        "format": "rtm.a1s.synthetic_representation.v1",
        "case_binding_id": binding_id,
        "fixture_key": clean_key,
        "kind": RepresentationKind.SYNTHETIC_SIGNED_AUTHORIZATION.value,
        "synthetic_marker": HUMAN_FILING_MARKER,
        "synthetic_only": True,
    }
    representation_sha = canonical_sha256(representation_payload)
    subject_sha = canonical_sha256(
        {
            "fixture_key": clean_key,
            "subject": "RTM A1-S SYNTHETIC SUBJECT",
            "synthetic_only": True,
        }
    )
    return RuntimeFixturePlan(
        fixture_key=clean_key,
        tenant_id=tenant_id,
        requester_membership_id=_uuid(clean_key, "membership-requester"),
        requester_principal_id=_uuid(clean_key, "principal-requester"),
        releaser_membership_id=_uuid(clean_key, "membership-releaser"),
        releaser_principal_id=_uuid(clean_key, "principal-releaser"),
        verifier_membership_id=_uuid(clean_key, "membership-verifier"),
        verifier_principal_id=_uuid(clean_key, "principal-verifier"),
        case_id=case_id,
        input_document_id=_uuid(clean_key, "document-input"),
        input_document_sha256=input_sha,
        input_document_size=len(input_bytes),
        receipt_document_id=_uuid(clean_key, "document-receipt"),
        receipt_document_sha256=receipt_sha,
        receipt_document_size=len(receipt_bytes),
        case_binding_id=binding_id,
        case_binding_code=f"rtm-a1s-binding-{case_snapshot[:24]}",
        case_snapshot_sha256=case_snapshot,
        representation_id=representation_id,
        representation_code=(
            f"rtm-a1s-representation-{representation_sha[:24]}"
        ),
        representation_evidence_sha256=representation_sha,
        representation_subject_sha256=subject_sha,
        action_id=_uuid(clean_key, "action"),
        authorization_id=_uuid(clean_key, "authorization"),
        correlation_id=f"rtm-a1s-runtime-{clean_key}",
        requested_at=_stamp(current - timedelta(minutes=5)),
        authorized_at=_stamp(current - timedelta(minutes=4)),
        expires_at=_stamp(current + timedelta(days=_RUNTIME_AUTHORITY_DAYS)),
        due_at=_stamp(current + timedelta(days=_RUNTIME_DUE_DAYS)),
        representation_valid_from=_stamp(current - timedelta(hours=1)),
        representation_expires_at=_stamp(
            current + timedelta(days=_RUNTIME_AUTHORITY_DAYS)
        ),
    )


def runtime_action_and_grant(
    plan: RuntimeFixturePlan,
    operators: RuntimeOperators,
) -> tuple[ConnectActionRequest, AuthorizationGrant]:
    action = ConnectActionRequest(
        action_id=plan.action_id,
        case_id=plan.case_id,
        capability=HUMAN_FILING_CAPABILITY,
        satellite=HUMAN_FILING_SATELLITE,
        target_type=HUMAN_FILING_TARGET_TYPE,
        target_ref=HUMAN_FILING_TARGET_REF,
        payload=expected_a1s_action_payload(
            case_binding_id=plan.case_binding_id,
            representation_evidence_id=plan.representation_id,
            case_snapshot_sha256=plan.case_snapshot_sha256,
        ),
        document_hashes=(plan.input_document_sha256,),
        requested_by_operator_id=operators.requester_executor_id,
        requested_at=plan.requested_at,
        risk_class=RiskClass.R4_CRITICAL_REGULATED,
        correlation_id=plan.correlation_id,
        requires_dual_control=True,
    )
    grant = AuthorizationGrant(
        authorization_id=plan.authorization_id,
        action_id=plan.action_id,
        authority_code=HUMAN_FILING_AUTHORITY_CODE,
        authority_version=HUMAN_FILING_AUTHORITY_VERSION,
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope=HUMAN_FILING_AUTHORITY_CODE,
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.ASSISTED,),
        approved_by_operator_ids=(operators.releaser_id, operators.verifier_id),
        authorized_at=plan.authorized_at,
        expires_at=plan.expires_at,
        legal_effect_authorized=True,
    )
    validate_a1s_action_authority(action, grant)
    return action, grant


def missing_runtime_tables(conn: Any) -> list[str]:
    missing: list[str] = []
    for table_name in _REQUIRED_TABLES:
        exists = conn.execute(
            text("SELECT to_regclass(:name)"),
            {"name": f"public.{table_name}"},
        ).scalar_one()
        if not exists:
            missing.append(table_name)
    return missing


def missing_runtime_columns(conn: Any) -> list[str]:
    """Returns every required Runtime column absent from ``public``."""

    missing: list[str] = []
    for table_name, required in _RUNTIME_REQUIRED_COLUMNS.items():
        present = {
            str(row[0])
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:table_name"
                ),
                {"table_name": table_name},
            ).fetchall()
        }
        missing.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(required - present)
        )
    return missing


def _load_synthetic_operators(conn: Any, operators: RuntimeOperators) -> None:
    rows = conn.execute(
        text(
            """
            SELECT id, status, must_change_password, mfa_required,
                   (locked_until IS NULL OR locked_until <= NOW()) AS unlocked,
                   profile
            FROM rtm_operators
            WHERE id IN (
                CAST(:requester AS UUID), CAST(:releaser AS UUID),
                CAST(:verifier AS UUID)
            )
            """
        ),
        {
            "requester": operators.requester_executor_id,
            "releaser": operators.releaser_id,
            "verifier": operators.verifier_id,
        },
    ).mappings().all()
    by_id = {str(row["id"]): row for row in rows}
    for operator_id in asdict(operators).values():
        row = by_id.get(operator_id)
        if not row:
            raise HumanFilingRuntimeError("Operador A1-S no encontrado")
        profile = dict(row["profile"] or {})
        if (
            str(row["status"]) != "active"
            or bool(row["must_change_password"])
            or bool(row["mfa_required"])
            or profile.get("synthetic") is not True
            or profile.get("environment") != "staging"
            or not bool(row["unlocked"])
        ):
            raise HumanFilingRuntimeError(
                "Los operadores A1-S deben estar activos, operativos y ser sintéticos"
            )


def _insert_tenant_and_memberships(
    conn: Any,
    *,
    plan: RuntimeFixturePlan,
    operators: RuntimeOperators,
) -> None:
    metadata = json.dumps(_metadata(plan.fixture_key), sort_keys=True)
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_a1s_tenants(
                id, tenant_code, display_name, status, synthetic_only,
                metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), :code, :name, 'active', TRUE,
                CAST(:metadata AS JSONB), NOW(), NOW()
            ) ON CONFLICT (tenant_code) DO NOTHING
            """
        ),
        {
            "id": plan.tenant_id,
            "code": runtime_tenant_code(plan.fixture_key),
            "name": "RTM A1-S SYNTHETIC RUNTIME",
            "metadata": metadata,
        },
    )
    memberships = (
        (
            plan.requester_membership_id,
            plan.requester_principal_id,
            operators.requester_executor_id,
            "supervisor",
        ),
        (
            plan.releaser_membership_id,
            plan.releaser_principal_id,
            operators.releaser_id,
            "releaser",
        ),
        (
            plan.verifier_membership_id,
            plan.verifier_principal_id,
            operators.verifier_id,
            "verifier",
        ),
    )
    for membership_id, principal_id, operator_id, role in memberships:
        conn.execute(
            text(
                """
                INSERT INTO rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id, role, status,
                    synthetic_only, granted_by_operator_id, granted_at,
                    version, metadata
                ) VALUES (
                    CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                    CAST(:principal_id AS UUID), CAST(:operator_id AS UUID),
                    :role, 'active', TRUE, CAST(:granted_by AS UUID), NOW(),
                    1, CAST(:metadata AS JSONB)
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": membership_id,
                "tenant_id": plan.tenant_id,
                "principal_id": principal_id,
                "operator_id": operator_id,
                "role": role,
                "granted_by": operators.requester_executor_id,
                "metadata": metadata,
            },
        )


def _insert_case_material(
    conn: Any,
    *,
    plan: RuntimeFixturePlan,
    operators: RuntimeOperators,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO cases(id, status, test_mode, created_at, updated_at)
            VALUES (
                CAST(:case_id AS UUID), 'core_review_pending', TRUE,
                NOW(), NOW()
            ) ON CONFLICT (id) DO NOTHING
            """
        ),
        {"case_id": plan.case_id},
    )
    documents = (
        (
            plan.input_document_id,
            "rtm_connect_a1s_synthetic_input_fixture",
            plan.input_document_sha256,
            "text/plain",
            plan.input_document_size,
        ),
        (
            plan.receipt_document_id,
            "rtm_connect_a1s_synthetic_receipt_fixture",
            plan.receipt_document_sha256,
            "application/json",
            plan.receipt_document_size,
        ),
    )
    for document_id, kind, digest, media_type, size in documents:
        conn.execute(
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
            {
                "id": document_id,
                "case_id": plan.case_id,
                "kind": kind,
                "sha256": digest,
                "mime": media_type,
                "size_bytes": size,
            },
        )

    binding_metadata = _metadata(plan.fixture_key, test_mode=True)
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_a1s_case_bindings(
                id, tenant_id, case_id, binding_code, status,
                synthetic_only, case_snapshot_sha256,
                bound_by_operator_id, bound_at, version, metadata
            ) VALUES (
                CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                CAST(:case_id AS UUID), :code, 'active', TRUE, :snapshot,
                CAST(:operator_id AS UUID), NOW(), 1,
                CAST(:metadata AS JSONB)
            ) ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": plan.case_binding_id,
            "tenant_id": plan.tenant_id,
            "case_id": plan.case_id,
            "code": plan.case_binding_code,
            "snapshot": plan.case_snapshot_sha256,
            "operator_id": operators.requester_executor_id,
            "metadata": json.dumps(binding_metadata, sort_keys=True),
        },
    )
    representation = {
        "format": "rtm.a1s.synthetic_representation.v1",
        "case_binding_id": plan.case_binding_id,
        "fixture_key": plan.fixture_key,
        "kind": RepresentationKind.SYNTHETIC_SIGNED_AUTHORIZATION.value,
        "synthetic_marker": HUMAN_FILING_MARKER,
        "synthetic_only": True,
    }
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_a1s_representation_evidence(
                id, tenant_id, case_binding_id, representation_code, kind,
                subject_ref_sha256, evidence_sha256, canonical_evidence,
                status, synthetic_only, recorded_by_membership_id,
                recorded_by_principal_id, recorded_by_operator_id,
                valid_from, expires_at, version, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                CAST(:binding_id AS UUID), :code, :kind, :subject_sha,
                :evidence_sha, CAST(:evidence AS JSONB), 'active', TRUE,
                CAST(:membership_id AS UUID), CAST(:principal_id AS UUID),
                CAST(:operator_id AS UUID), CAST(:valid_from AS TIMESTAMPTZ),
                CAST(:expires_at AS TIMESTAMPTZ), 1, NOW()
            ) ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": plan.representation_id,
            "tenant_id": plan.tenant_id,
            "binding_id": plan.case_binding_id,
            "code": plan.representation_code,
            "kind": RepresentationKind.SYNTHETIC_SIGNED_AUTHORIZATION.value,
            "subject_sha": plan.representation_subject_sha256,
            "evidence_sha": plan.representation_evidence_sha256,
            "evidence": json.dumps(representation, sort_keys=True),
            "membership_id": plan.requester_membership_id,
            "principal_id": plan.requester_principal_id,
            "operator_id": operators.requester_executor_id,
            "valid_from": plan.representation_valid_from,
            "expires_at": plan.representation_expires_at,
        },
    )


def provision_runtime_fixture(
    conn: Any,
    *,
    operators: RuntimeOperators,
    fixture_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Creates or validates the minimum persistent A1-S synthetic fixture."""

    missing = missing_runtime_tables(conn)
    missing_columns = missing_runtime_columns(conn)
    if missing or missing_columns:
        raise HumanFilingRuntimeError(
            "Schema A1-S Runtime incompleto: tables="
            + ",".join(missing)
            + ";columns="
            + ",".join(missing_columns)
        )
    _load_synthetic_operators(conn, operators)
    plan = build_runtime_fixture_plan(fixture_key=fixture_key, now=now)
    _insert_tenant_and_memberships(conn, plan=plan, operators=operators)
    _insert_case_material(conn, plan=plan, operators=operators)
    action, grant = runtime_action_and_grant(plan, operators)

    existing = conn.execute(
        text("SELECT id FROM rtm_connect_actions WHERE id=CAST(:id AS UUID)"),
        {"id": plan.action_id},
    ).first()
    created = False
    if existing is None:
        outcome = create_action(
            conn,
            action=action,
            authority_scope=HUMAN_FILING_AUTHORITY_CODE,
        )
        if not outcome.created:
            raise HumanFilingRuntimeError("No se creó la acción A1-S exacta")
        authorize_action(conn, grant=grant)
        created = True
    persisted_action, version, persisted_grant, status = load_action_and_grant(
        conn,
        action_id=plan.action_id,
        authorization_id=plan.authorization_id,
    )
    validate_a1s_action_authority(persisted_action, persisted_grant)
    if persisted_action != action or persisted_grant != grant:
        raise HumanFilingRuntimeError(
            "Colisión con autoridad A1-S Runtime de contenido distinto"
        )
    if version != 1 or status != "authorized":
        raise HumanFilingRuntimeError(
            "La fixture A1-S ya no está disponible para preparación"
        )
    audit = audit_runtime_fixture(
        conn, fixture_key=plan.fixture_key, operators=operators
    )
    if not audit["ready"]:
        raise HumanFilingRuntimeError(
            "La fixture persistida no supera la auditoría A1-S Runtime"
        )
    return {
        "created": created,
        "ready": True,
        "operators": asdict(operators),
        "fixture": plan.public_manifest(),
        "audit": audit,
    }


def audit_runtime_fixture(
    conn: Any,
    *,
    fixture_key: str,
    operators: RuntimeOperators | None = None,
) -> dict[str, Any]:
    """Reads the fixture manifest without returning personal data or secrets."""

    plan = build_runtime_fixture_plan(fixture_key=fixture_key)
    missing = missing_runtime_tables(conn)
    missing_columns = missing_runtime_columns(conn)
    if missing or missing_columns:
        return {
            "ready": False,
            "missing_tables": missing,
            "missing_columns": missing_columns,
            "checks": {},
        }
    row = conn.execute(
        text(
            """
            SELECT
              t.id AS tenant_id, t.status AS tenant_status,
              t.display_name AS tenant_display_name,
              t.synthetic_only AS tenant_synthetic,
              t.metadata AS tenant_metadata,
              (SELECT COUNT(*) FROM rtm_connect_a1s_memberships m
               JOIN rtm_operators o ON o.id=m.operator_id
               WHERE m.tenant_id=t.id
                 AND ((m.id=CAST(:requester_membership_id AS UUID)
                       AND m.principal_id=CAST(:requester_principal_id AS UUID)
                       AND m.role='supervisor')
                   OR (m.id=CAST(:releaser_membership_id AS UUID)
                       AND m.principal_id=CAST(:releaser_principal_id AS UUID)
                       AND m.role='releaser')
                   OR (m.id=CAST(:verifier_membership_id AS UUID)
                       AND m.principal_id=CAST(:verifier_principal_id AS UUID)
                       AND m.role='verifier'))
                 AND m.status='active'
                 AND m.synthetic_only=TRUE AND o.status='active'
                 AND o.must_change_password=FALSE
                 AND o.mfa_required=FALSE
                 AND (o.locked_until IS NULL OR o.locked_until <= NOW())
                 AND COALESCE((o.profile->>'synthetic')::boolean,FALSE)=TRUE
                 AND COALESCE(o.profile->>'environment','')='staging'
              ) AS active_memberships,
              (SELECT COUNT(*) FROM rtm_connect_a1s_memberships m
               WHERE m.tenant_id=t.id) AS total_memberships,
              (SELECT COUNT(*) FROM cases c
               WHERE c.id=CAST(:case_id AS UUID)
                 AND c.status='core_review_pending'
                 AND COALESCE(c.test_mode,FALSE)=TRUE) AS synthetic_cases,
              (SELECT COUNT(*) FROM documents d
               WHERE d.case_id=CAST(:case_id AS UUID)
                 AND ((d.id=CAST(:input_id AS UUID)
                       AND d.kind='rtm_connect_a1s_synthetic_input_fixture'
                       AND d.sha256=:input_sha AND d.mime='text/plain'
                       AND d.size_bytes=:input_size)
                   OR (d.id=CAST(:receipt_id AS UUID)
                       AND d.kind='rtm_connect_a1s_synthetic_receipt_fixture'
                       AND d.sha256=:receipt_sha AND d.mime='application/json'
                       AND d.size_bytes=:receipt_size))
                 AND d.b2_bucket IS NULL AND d.b2_key IS NULL) AS local_documents,
              (SELECT COUNT(*) FROM documents d
               WHERE d.case_id=CAST(:case_id AS UUID)) AS total_documents,
              (SELECT COUNT(*) FROM rtm_connect_a1s_case_bindings b
               WHERE b.id=CAST(:binding_id AS UUID) AND b.tenant_id=t.id
                 AND b.case_id=CAST(:case_id AS UUID) AND b.status='active'
                 AND b.binding_code=:binding_code
                 AND b.synthetic_only=TRUE AND b.case_snapshot_sha256=:snapshot
                 AND b.metadata @>
                     CAST(:test_mode_metadata AS JSONB)) AS bindings,
              (SELECT COUNT(*) FROM rtm_connect_a1s_representation_evidence r
               WHERE r.id=CAST(:representation_id AS UUID)
                 AND r.tenant_id=t.id AND r.status='active'
                 AND r.synthetic_only=TRUE AND r.revoked_at IS NULL
                 AND r.case_binding_id=CAST(:binding_id AS UUID)
                 AND r.representation_code=:representation_code
                 AND r.kind='synthetic_signed_authorization'
                 AND r.subject_ref_sha256=:subject_sha
                 AND r.evidence_sha256=:representation_sha
                 AND r.recorded_by_membership_id=
                     CAST(:requester_membership_id AS UUID)
                 AND r.recorded_by_principal_id=
                     CAST(:requester_principal_id AS UUID)
                 AND r.expires_at>NOW()) AS representations,
              (SELECT COUNT(*) FROM rtm_connect_actions a
               JOIN rtm_connect_authorizations g ON g.action_id=a.id
               WHERE a.id=CAST(:action_id AS UUID)
                 AND a.case_id=CAST(:case_id AS UUID)
                 AND a.status='authorized'
                 AND a.capability=:capability
                 AND a.document_hashes=CAST(:document_hashes AS JSONB)
                 AND g.id=CAST(:authorization_id AS UUID)
                 AND g.authorization_version=1
                 AND g.decision='approved_frozen' AND g.frozen=TRUE
                 AND g.revoked_at IS NULL AND g.expires_at>NOW()
                 AND g.required_evidence_level='E4_receipt_verified'
                 AND g.authorized_connector_modes='["assisted"]'::jsonb
              ) AS authorities,
              (SELECT COUNT(*) FROM rtm_connect_a1s_human_tasks task
               WHERE task.action_id=CAST(:action_id AS UUID)) AS tasks
            FROM rtm_connect_a1s_tenants t
            WHERE t.id=CAST(:tenant_id AS UUID) AND t.tenant_code=:tenant_code
            """
        ),
        {
            "tenant_id": plan.tenant_id,
            "tenant_code": runtime_tenant_code(plan.fixture_key),
            "case_id": plan.case_id,
            "requester_membership_id": plan.requester_membership_id,
            "requester_principal_id": plan.requester_principal_id,
            "releaser_membership_id": plan.releaser_membership_id,
            "releaser_principal_id": plan.releaser_principal_id,
            "verifier_membership_id": plan.verifier_membership_id,
            "verifier_principal_id": plan.verifier_principal_id,
            "input_id": plan.input_document_id,
            "receipt_id": plan.receipt_document_id,
            "input_sha": plan.input_document_sha256,
            "receipt_sha": plan.receipt_document_sha256,
            "input_size": plan.input_document_size,
            "receipt_size": plan.receipt_document_size,
            "binding_id": plan.case_binding_id,
            "binding_code": plan.case_binding_code,
            "snapshot": plan.case_snapshot_sha256,
            "test_mode_metadata": json.dumps(
                {"test_mode": True}, sort_keys=True
            ),
            "representation_id": plan.representation_id,
            "representation_code": plan.representation_code,
            "subject_sha": plan.representation_subject_sha256,
            "representation_sha": plan.representation_evidence_sha256,
            "action_id": plan.action_id,
            "authorization_id": plan.authorization_id,
            "capability": HUMAN_FILING_CAPABILITY,
            "document_hashes": json.dumps([plan.input_document_sha256]),
        },
    ).mappings().first()
    checks = {
        "tenant_active_synthetic": bool(
            row
            and str(row["tenant_status"]) == "active"
            and str(row["tenant_display_name"])
            == "RTM A1-S SYNTHETIC RUNTIME"
            and bool(row["tenant_synthetic"])
            and isinstance(row["tenant_metadata"], dict)
            and row["tenant_metadata"].get("fixture_key") == plan.fixture_key
            and row["tenant_metadata"].get("runtime_version")
            == RTM_CONNECT_A1S_RUNTIME_VERSION
        ),
        "three_distinct_active_memberships": bool(
            row
            and int(row["active_memberships"]) == 3
            and int(row["total_memberships"]) == 3
        ),
        "case_test_mode": bool(row and int(row["synthetic_cases"]) == 1),
        "two_local_hash_only_documents": bool(
            row
            and int(row["local_documents"]) == 2
            and int(row["total_documents"]) == 2
        ),
        "case_binding_exact": bool(row and int(row["bindings"]) == 1),
        "representation_active": bool(
            row and int(row["representations"]) == 1
        ),
        "r4_e4_authority_frozen": bool(row and int(row["authorities"]) == 1),
        "action_not_already_claimed": bool(row and int(row["tasks"]) == 0),
        "receipt_disjoint_from_input": (
            plan.receipt_document_sha256 != plan.input_document_sha256
        ),
        "operators_supplied_for_exact_authority_audit": operators is not None,
    }
    if operators is not None:
        expected_operator_rows = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM rtm_connect_a1s_memberships
                WHERE tenant_id=CAST(:tenant_id AS UUID)
                  AND ((id=CAST(:requester_membership_id AS UUID)
                        AND operator_id=CAST(:requester_operator_id AS UUID))
                    OR (id=CAST(:releaser_membership_id AS UUID)
                        AND operator_id=CAST(:releaser_operator_id AS UUID))
                    OR (id=CAST(:verifier_membership_id AS UUID)
                        AND operator_id=CAST(:verifier_operator_id AS UUID)))
                """
            ),
            {
                "tenant_id": plan.tenant_id,
                "requester_membership_id": plan.requester_membership_id,
                "requester_operator_id": operators.requester_executor_id,
                "releaser_membership_id": plan.releaser_membership_id,
                "releaser_operator_id": operators.releaser_id,
                "verifier_membership_id": plan.verifier_membership_id,
                "verifier_operator_id": operators.verifier_id,
            },
        ).scalar_one()
        checks["operators_exactly_bound"] = int(expected_operator_rows) == 3
        try:
            expected_action, expected_grant = runtime_action_and_grant(
                plan, operators
            )
            (
                persisted_action,
                persisted_version,
                persisted_grant,
                persisted_status,
            ) = load_action_and_grant(
                conn,
                action_id=plan.action_id,
                authorization_id=plan.authorization_id,
            )
            checks["action_and_grant_exact"] = bool(
                persisted_action == expected_action
                and persisted_grant == expected_grant
                and persisted_version == 1
                and persisted_status == "authorized"
            )
        except Exception:
            checks["action_and_grant_exact"] = False
    return {
        "ready": all(checks.values()),
        "missing_tables": [],
        "missing_columns": [],
        "checks": checks,
        "fixture": plan.public_manifest(),
        "read_only": True,
        "synthetic_only": True,
        "network_used": False,
        "b2_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "production_authorized": False,
    }


__all__ = [
    "RTM_CONNECT_A1S_RUNTIME_VERSION",
    "RUNTIME_FIXTURE_CODE",
    "RUNTIME_FIXTURE_CONFIRMATION",
    "HumanFilingRuntimeError",
    "RuntimeFixturePlan",
    "RuntimeOperators",
    "audit_runtime_fixture",
    "build_runtime_fixture_plan",
    "missing_runtime_tables",
    "missing_runtime_columns",
    "provision_runtime_fixture",
    "runtime_action_and_grant",
    "runtime_tenant_code",
]
