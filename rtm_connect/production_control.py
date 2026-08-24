"""Plano de control inerte de RTM CONNECT C8.

Este módulo persiste admisiones humanas y una outbox de *dry-run*.  No hay
transporte, conector, intento de ejecución, resolución de secretos ni efecto
externo.  Todos los cambios vuelven a validar el candidato y la autoridad
CORE congelada antes de ejecutar DML.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, replace
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
from rtm_connect.idempotency import canonical_json, payload_sha256, sha256_hex
from rtm_connect.production_contracts import (
    ProductionAdmissionCandidate,
    ProductionApprovalRole,
    ProductionReleaseApproval,
    SimulatedOutboxIntent,
    SimulatedOutboxStatus,
    candidate_sha256,
    expected_c8_admission_payload,
)
from rtm_connect.production_policy import (
    ProductionLiveActivationUnavailable,
    assert_live_activation_unavailable,
    assess_c8_candidate,
    validate_c8_admission_authority,
    validate_c8_release_approvals,
)


RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION = (
    "rtm_connect_c8_production_control_v1_0"
)
C8_HUMAN_GATE_PHRASE = "HUMAN_PRODUCTION_ACTIVATION_REQUIRED"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_TABLE = "public.rtm_connect_production_releases"
_RELEASE_EVENT_TABLE = "public.rtm_connect_production_release_events"
_DISPATCH_TABLE = "public.rtm_connect_dispatch_outbox"
_DISPATCH_EVENT_TABLE = "public.rtm_connect_dispatch_events"

_RELEASE_STATUSES = frozenset(
    {
        "proposed",
        "security_approved",
        "operations_approved",
        "ready",
        "simulated_active",
        "halted",
        "rejected",
        "expired",
    }
)
_DISPATCH_STATUSES = frozenset(status.value for status in SimulatedOutboxStatus)
_TERMINAL_DISPATCH_STATUSES = frozenset(
    {
        SimulatedOutboxStatus.DRY_RUN_CONFIRMED.value,
        SimulatedOutboxStatus.MANUAL_REVIEW.value,
        SimulatedOutboxStatus.CANCELLED.value,
    }
)


class ProductionControlError(RuntimeError):
    """Fallo fail-closed del plano de control C8."""


class ProductionReleaseConflict(ProductionControlError):
    """La misma identidad de release se ha presentado con otro cuerpo."""


class ProductionReleaseStateError(ProductionControlError):
    """Transición de release no permitida."""


class ProductionDispatchReplayConflict(ProductionControlError):
    """La misma identidad de outbox se ha presentado con otro cuerpo."""


class ProductionDispatchStateError(ProductionControlError):
    """Transición de outbox no permitida."""


class ProductionOptimisticLockError(ProductionControlError):
    """La versión persistida ya no coincide con la observada."""


class ProductionClaimFenceError(ProductionControlError):
    """El propietario, token o fence del claim ya no es vigente."""


def _trusted_policy_values(
    values: Mapping[str, str] | None,
) -> Mapping[str, str]:
    """El DML C8 solo puede evaluar el entorno real del proceso.

    La inyección de mappings se conserva en la política para pruebas unitarias,
    pero nunca se acepta en el plano de control persistente.
    """

    if values is not None and values is not os.environ:
        raise ProductionControlError(
            "C8 control plane rejects injected environment mappings"
        )
    return os.environ


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = _utcnow()
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProductionControlError("C8 exige timestamps con zona horaria")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime | str | None) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _row_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return _timestamp(value)
    return _timestamp(str(value))


def _uuid(value: str, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProductionControlError(f"{field_name} debe ser UUID") from exc


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ProductionControlError(f"{field_name} debe ser SHA-256")
    return normalized


def _json(value: Any) -> str:
    return canonical_json(value)


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, Mapping):
            raise ProductionControlError("JSON persistido fuera de contrato")
        return dict(decoded)
    raise ProductionControlError("Valor persistido fuera de contrato")


def _first_mapping(result: Any) -> Mapping[str, Any] | None:
    return result.mappings().first()


def _assert_changed(result: Any) -> None:
    rowcount = getattr(result, "rowcount", None)
    if rowcount == 0:
        raise ProductionOptimisticLockError("actualización C8 concurrente")


def _candidate_from_release(row: Mapping[str, Any]) -> ProductionAdmissionCandidate:
    candidate_data = _mapping(row.get("metadata")).get("candidate")
    if not isinstance(candidate_data, Mapping):
        raise ProductionControlError("Release C8 sin candidato sellado")
    candidate = ProductionAdmissionCandidate(**dict(candidate_data))
    digest = candidate_sha256(candidate)
    if digest != str(row["release_binding_sha256"]):
        raise ProductionReleaseConflict("Binding del candidato C8 alterado")
    if candidate.candidate_id != str(row["id"]):
        raise ProductionReleaseConflict("Identidad del candidato C8 alterada")
    return candidate


def _assess_candidate(
    candidate: ProductionAdmissionCandidate,
    *,
    now: datetime | str | None,
    policy_values: Mapping[str, str] | None,
) -> Any:
    evaluated_at = _timestamp(now)
    trusted_values = _trusted_policy_values(policy_values)
    assessment = assess_c8_candidate(
        candidate,
        values=trusted_values,
        evaluated_at=evaluated_at,
    )
    if (
        assessment.candidate_sha256 != candidate_sha256(candidate)
        or assessment.verdict != "no_go"
        or not assessment.simulation_admitted
        or assessment.live_production_admitted
        or assessment.production_effects_available
    ):
        raise ProductionControlError("Assessment C8 amplía el plano inerte")
    return assessment


def _prove_live_activation_unavailable(
    candidate: ProductionAdmissionCandidate,
    *,
    policy_values: Mapping[str, str] | None,
) -> None:
    trusted_values = _trusted_policy_values(policy_values)
    try:
        assert_live_activation_unavailable(
            candidate=candidate,
            values=trusted_values,
        )
    except ProductionLiveActivationUnavailable:
        return
    raise ProductionControlError("La barrera de activación live no bloqueó")


def _assert_candidate_current(
    candidate: ProductionAdmissionCandidate,
    now: datetime | str | None,
) -> None:
    current = _as_utc(now)
    created = _as_utc(candidate.created_at)
    expires = _as_utc(candidate.expires_at)
    if not created <= current < expires:
        raise ProductionReleaseStateError("Candidato C8 fuera de vigencia")


def _approval_sha256(
    release_id: str,
    candidate_digest: str,
    approval: ProductionReleaseApproval,
) -> str:
    """Hash role-bound reutilizado para replay y doble control."""

    if type(approval) is not ProductionReleaseApproval:
        raise ProductionControlError("C8 exige aprobación sellada exacta")
    return sha256_hex(
        canonical_json(
            {
                "version": RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION,
                "release_id": _uuid(release_id, "release_id"),
                "candidate_sha256": _sha256(
                    candidate_digest, "candidate_sha256"
                ),
                "approval": asdict(approval),
            }
        )
    )


def _dispatch_binding_sha256(
    release_id: str,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    request: SimulatedOutboxIntent,
) -> str:
    """Liga release, solicitud y grant sin depender del UUID del intent."""

    return sha256_hex(
        canonical_json(
            {
                "version": RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION,
                "release_id": _uuid(release_id, "release_id"),
                "intent_contract": {
                    "contract_version": request.contract_version,
                    "candidate_id": request.candidate_id,
                    "candidate_sha256": request.candidate_sha256,
                    "request_sha256": request.request_sha256,
                    "idempotency_key": request.idempotency_key,
                    "simulation_only": request.simulation_only,
                    "external_effects_allowed": request.external_effects_allowed,
                    "network_call_performed": request.network_call_performed,
                    "secret_resolution_performed": (
                        request.secret_resolution_performed
                    ),
                    "blind_retry_allowed": request.blind_retry_allowed,
                },
                "action": {
                    "action_id": action.action_id,
                    "requested_by_operator_id": action.requested_by_operator_id,
                    "request_sha256": payload_sha256(action),
                },
                "authorization": {
                    "authorization_id": grant.authorization_id,
                    "action_id": grant.action_id,
                    "payload_sha256": grant.payload_sha256,
                    "idempotency_key": grant.idempotency_key,
                    "approved_by_operator_ids": list(
                        grant.approved_by_operator_ids
                    ),
                    "legal_effect_authorized": grant.legal_effect_authorized,
                },
                "inert_flags": {
                    "dry_run_only": True,
                    "network_allowed": False,
                    "provider_contacted": False,
                    "external_effects_allowed": False,
                },
            }
        )
    )


def _production_effect_sha256(
    release_id: str,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    request: SimulatedOutboxIntent,
) -> str:
    """Clave semántica independiente de identidades recreables de C1."""

    return sha256_hex(
        canonical_json(
            {
                "version": RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION,
                "release_id": _uuid(release_id, "release_id"),
                "candidate_sha256": request.candidate_sha256,
                "capability": action.capability,
                "satellite": action.satellite,
                "target_type": action.target_type,
                "target_ref": action.target_ref,
                "admission_payload": expected_c8_admission_payload(
                    request.candidate_sha256
                ),
                "risk_class": action.risk_class.value,
                "required_evidence_level": (
                    grant.required_evidence_level.value
                ),
                "authorized_connector_modes": [
                    mode.value for mode in grant.authorized_connector_modes
                ],
                "legal_effect_authorized": (
                    grant.legal_effect_authorized
                ),
                "dry_run_only": True,
                "external_effects_allowed": False,
            }
        )
    )


def _release_row(conn: Any, release_id: str, *, for_update: bool) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT * FROM {_RELEASE_TABLE}
                WHERE id=CAST(:release_id AS UUID){suffix}
                """
            ),
            {"release_id": _uuid(release_id, "release_id")},
        )
    )
    if not row:
        raise LookupError("Release C8 no encontrada")
    if str(row["status"]) not in _RELEASE_STATUSES:
        raise ProductionReleaseStateError("Estado de release C8 no admitido")
    return row


def _dispatch_row(conn: Any, dispatch_id: str, *, for_update: bool) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT * FROM {_DISPATCH_TABLE}
                WHERE id=CAST(:dispatch_id AS UUID){suffix}
                """
            ),
            {"dispatch_id": _uuid(dispatch_id, "dispatch_id")},
        )
    )
    if not row:
        raise LookupError("Outbox C8 no encontrada")
    if str(row["status"]) not in _DISPATCH_STATUSES:
        raise ProductionDispatchStateError("Estado de outbox C8 no admitido")
    return row


def _append_release_event(
    conn: Any,
    *,
    row: Mapping[str, Any],
    event_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str,
    reason_code: str,
    payload: Mapping[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        text(
            f"""
            SELECT id FROM {_RELEASE_TABLE}
            WHERE id=CAST(:release_id AS UUID) FOR UPDATE
            """
        ),
        {"release_id": str(row["id"])},
    ).one()
    sequence = int(
        conn.execute(
            text(
                f"""
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM {_RELEASE_EVENT_TABLE}
                WHERE release_id=CAST(:release_id AS UUID)
                """
            ),
            {"release_id": str(row["id"])},
        ).scalar_one()
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {_RELEASE_EVENT_TABLE}(
                id, release_id, release_binding_sha256, sequence_number,
                event_type, actor_type, operator_id, from_status, to_status,
                reason_code, payload, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:release_id AS UUID),
                :release_binding_sha256, :sequence_number, :event_type,
                :actor_type, CAST(:operator_id AS UUID), :from_status,
                :to_status, :reason_code, CAST(:payload AS JSONB),
                CAST(:created_at AS TIMESTAMPTZ)
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "release_id": str(row["id"]),
            "release_binding_sha256": str(row["release_binding_sha256"]),
            "sequence_number": sequence,
            "event_type": event_type,
            "actor_type": (
                "requester"
                if event_type == "release_proposed"
                else "security"
                if event_type == "security_approval_recorded"
                else "operations"
                if event_type in {
                    "operations_approval_recorded",
                    "simulation_release_ready",
                }
                else "system"
            ),
            "operator_id": operator_id,
            "from_status": from_status,
            "to_status": to_status,
            "reason_code": reason_code,
            "payload": _json(payload),
            "created_at": created_at,
        },
    )


def _append_dispatch_event(
    conn: Any,
    *,
    row: Mapping[str, Any],
    event_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str,
    reason_code: str,
    payload: Mapping[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        text(
            f"""
            SELECT id FROM {_DISPATCH_TABLE}
            WHERE id=CAST(:dispatch_id AS UUID) FOR UPDATE
            """
        ),
        {"dispatch_id": str(row["id"])},
    ).one()
    sequence = int(
        conn.execute(
            text(
                f"""
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM {_DISPATCH_EVENT_TABLE}
                WHERE outbox_id=CAST(:dispatch_id AS UUID)
                """
            ),
            {"dispatch_id": str(row["id"])},
        ).scalar_one()
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {_DISPATCH_EVENT_TABLE}(
                id, outbox_id, action_id, authorization_id, release_id,
                release_binding_sha256, sequence_number, event_type,
                actor_type, operator_id,
                from_status, to_status, reason_code, payload, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:dispatch_id AS UUID),
                CAST(:action_id AS UUID), CAST(:authorization_id AS UUID),
                CAST(:release_id AS UUID), :release_binding_sha256,
                :sequence_number, :event_type,
                :actor_type, CAST(:operator_id AS UUID), :from_status,
                :to_status, :reason_code, CAST(:payload AS JSONB),
                CAST(:created_at AS TIMESTAMPTZ)
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "dispatch_id": str(row["id"]),
            "action_id": str(row["action_id"]),
            "authorization_id": str(row["authorization_id"]),
            "release_id": str(row["release_id"]),
            "release_binding_sha256": str(row["release_binding_sha256"]),
            "sequence_number": sequence,
            "event_type": event_type,
            "actor_type": "operator" if operator_id else "connect",
            "operator_id": operator_id,
            "from_status": from_status,
            "to_status": to_status,
            "reason_code": reason_code,
            "payload": _json(payload),
            "created_at": created_at,
        },
    )


def release_snapshot(conn: Any, release_id: str) -> dict[str, Any]:
    row = dict(_release_row(conn, release_id, for_update=False))
    row["metadata"] = _mapping(row.get("metadata"))
    return row


def propose_production_release(
    conn: Any,
    candidate: ProductionAdmissionCandidate,
    *,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Registra un candidato NO-GO; nunca habilita activación live."""

    if type(candidate) is not ProductionAdmissionCandidate:
        raise ProductionControlError("C8 exige ProductionAdmissionCandidate exacto")
    current = _timestamp(now)
    assessment = _assess_candidate(
        candidate, now=current, policy_values=policy_values
    )
    digest = candidate_sha256(candidate)
    existing = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT * FROM {_RELEASE_TABLE}
                WHERE id=CAST(:release_id AS UUID) FOR UPDATE
                """
            ),
            {"release_id": candidate.candidate_id},
        )
    )
    if existing:
        persisted = _candidate_from_release(existing)
        if persisted == candidate and str(existing["release_binding_sha256"]) == digest:
            return release_snapshot(conn, candidate.candidate_id)
        raise ProductionReleaseConflict("Replay de release C8 con otro cuerpo")

    metadata = {
        "candidate": asdict(candidate),
        "assessment": asdict(assessment),
        "expected_admission_payload": expected_c8_admission_payload(digest),
        "control_version": RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION,
    }
    result = conn.execute(
        text(
            f"""
            INSERT INTO {_RELEASE_TABLE}(
                id, release_code, status, connector_code, connector_version,
                source_commit_sha, manifest_sha256, policy_sha256,
                schema_sha256, build_artifact_sha256,
                release_binding_sha256, requested_by_operator_id,
                requested_at, valid_until, simulation_only,
                external_effects_allowed, live_activation_allowed,
                human_activation_required, provider_pack_present,
                canary_percent, max_concurrency, daily_action_limit,
                version, metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), :release_code, 'proposed',
                'c8.inert.simulation', 'v1.0',
                :source_commit_sha, :manifest_sha256, :policy_sha256,
                :schema_sha256, :build_artifact_sha256,
                :release_binding_sha256, CAST(:requested_by AS UUID),
                CAST(:requested_at AS TIMESTAMPTZ),
                CAST(:valid_until AS TIMESTAMPTZ), TRUE, FALSE, FALSE, TRUE,
                FALSE, :canary_percent, :max_concurrency,
                :daily_action_limit, 1, CAST(:metadata AS JSONB),
                CAST(:created_at AS TIMESTAMPTZ),
                CAST(:created_at AS TIMESTAMPTZ)
            )
            """
        ),
        {
            "id": candidate.candidate_id,
            "release_code": f"rtmc8-release-{digest[:24]}",
            "source_commit_sha": candidate.source_commit_sha40,
            "manifest_sha256": candidate.connector_manifest_sha256,
            "policy_sha256": candidate.egress_policy_sha256,
            "schema_sha256": candidate.schema_snapshot_sha256,
            "build_artifact_sha256": candidate.build_artifact_sha256,
            "release_binding_sha256": digest,
            "requested_by": candidate.requested_by_operator_id,
            "requested_at": candidate.created_at,
            "valid_until": candidate.expires_at,
            "canary_percent": candidate.canary_percent,
            "max_concurrency": candidate.concurrency,
            "daily_action_limit": 1,
            "metadata": _json(metadata),
            "created_at": current,
        },
    )
    _assert_changed(result)
    event_row = {
        "id": candidate.candidate_id,
        "release_binding_sha256": digest,
    }
    _append_release_event(
        conn,
        row=event_row,
        event_type="release_proposed",
        operator_id=candidate.requested_by_operator_id,
        from_status=None,
        to_status="proposed",
        reason_code="simulation_candidate_recorded",
        payload={"candidate_sha256": digest, "assessment": asdict(assessment)},
        created_at=current,
    )
    return release_snapshot(conn, candidate.candidate_id)


def _approval_event(
    conn: Any, release_id: str, role: ProductionApprovalRole
) -> Mapping[str, Any]:
    row = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT payload, operator_id, created_at
                FROM {_RELEASE_EVENT_TABLE}
                WHERE release_id=CAST(:release_id AS UUID)
                  AND event_type=:event_type
                ORDER BY sequence_number DESC LIMIT 1
                """
            ),
            {
                "release_id": release_id,
                "event_type": f"{role.value}_approval_recorded",
            },
        )
    )
    if not row:
        raise ProductionReleaseStateError(f"Falta aprobación {role.value} C8")
    return _mapping(row["payload"])


def approve_production_release(
    conn: Any,
    release_id: str,
    approval: ProductionReleaseApproval,
    *,
    expected_version: int | None = None,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if type(approval) is not ProductionReleaseApproval:
        raise ProductionControlError("C8 exige ProductionReleaseApproval exacta")
    current = _timestamp(now)
    row = _release_row(conn, release_id, for_update=True)
    candidate = _candidate_from_release(row)
    _assess_candidate(candidate, now=current, policy_values=policy_values)
    _assert_candidate_current(candidate, current)
    digest = candidate_sha256(candidate)
    if (
        approval.candidate_id != candidate.candidate_id
        or approval.candidate_sha256 != digest
        or approval.requested_by_operator_id != candidate.requested_by_operator_id
    ):
        raise ProductionReleaseConflict("Aprobación C8 no pertenece al release")
    if not (
        _as_utc(approval.approved_at) <= _as_utc(current) < _as_utc(approval.expires_at)
        <= _as_utc(candidate.expires_at)
    ):
        raise ProductionReleaseStateError("Aprobación C8 fuera de vigencia")

    role = approval.approval_role
    approval_digest = _approval_sha256(release_id, digest, approval)
    if role is ProductionApprovalRole.SECURITY:
        from_status = "proposed"
        to_status = "security_approved"
        operator_column = "security_approved_by_operator_id"
        hash_column = "security_approval_sha256"
        timestamp_column = "security_approved_at"
    else:
        from_status = "security_approved"
        to_status = "operations_approved"
        operator_column = "operations_approved_by_operator_id"
        hash_column = "operations_approval_sha256"
        timestamp_column = "operations_approved_at"
        if str(row.get("security_approved_by_operator_id")) in {
            approval.approver_operator_id,
            "None",
        }:
            raise ProductionReleaseStateError(
                "Security y operations deben ser operadores distintos"
            )

    status = str(row["status"])
    persisted_hash = row.get(hash_column)
    persisted_operator = row.get(operator_column)
    if status != from_status:
        if (
            persisted_hash is not None
            and str(persisted_hash) == approval_digest
            and str(persisted_operator) == approval.approver_operator_id
        ):
            return release_snapshot(conn, release_id)
        raise ProductionReleaseStateError(
            f"Aprobación {role.value} no permitida desde {status}"
        )
    version = int(row["version"])
    if expected_version is not None and int(expected_version) != version:
        raise ProductionOptimisticLockError("Versión de release C8 obsoleta")
    result = conn.execute(
        text(
            f"""
            UPDATE {_RELEASE_TABLE}
            SET status=:to_status,
                {operator_column}=CAST(:operator_id AS UUID),
                {hash_column}=:approval_sha256,
                {timestamp_column}=CAST(:approved_at AS TIMESTAMPTZ),
                version=version + 1,
                updated_at=CAST(:updated_at AS TIMESTAMPTZ)
            WHERE id=CAST(:release_id AS UUID)
              AND status=:from_status AND version=:expected_version
              AND simulation_only=TRUE
              AND external_effects_allowed=FALSE
              AND live_activation_allowed=FALSE
            """
        ),
        {
            "release_id": release_id,
            "from_status": from_status,
            "to_status": to_status,
            "operator_id": approval.approver_operator_id,
            "approval_sha256": approval_digest,
            "approved_at": approval.approved_at,
            "updated_at": current,
            "expected_version": version,
        },
    )
    _assert_changed(result)
    _append_release_event(
        conn,
        row=row,
        event_type=f"{role.value}_approval_recorded",
        operator_id=approval.approver_operator_id,
        from_status=from_status,
        to_status=to_status,
        reason_code="simulation_admission_approved",
        payload={
            "approval_id": approval.approval_id,
            "approval_sha256": approval_digest,
            "candidate_sha256": digest,
            "approval": asdict(approval),
        },
        created_at=current,
    )
    return release_snapshot(conn, release_id)


def _assert_dual_approval(
    conn: Any,
    row: Mapping[str, Any],
    candidate: ProductionAdmissionCandidate,
    now: datetime | str | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    security = _approval_event(conn, str(row["id"]), ProductionApprovalRole.SECURITY)
    operations = _approval_event(
        conn, str(row["id"]), ProductionApprovalRole.OPERATIONS
    )
    digest = candidate_sha256(candidate)
    sealed: dict[ProductionApprovalRole, ProductionReleaseApproval] = {}
    for role, event, hash_column, operator_column in (
        (
            ProductionApprovalRole.SECURITY,
            security,
            "security_approval_sha256",
            "security_approved_by_operator_id",
        ),
        (
            ProductionApprovalRole.OPERATIONS,
            operations,
            "operations_approval_sha256",
            "operations_approved_by_operator_id",
        ),
    ):
        approval = ProductionReleaseApproval(**dict(event["approval"]))
        sealed[role] = approval
        expected_hash = _approval_sha256(str(row["id"]), digest, approval)
        if (
            approval.approval_role is not role
            or str(row.get(hash_column)) != expected_hash
            or str(row.get(operator_column)) != approval.approver_operator_id
            or not _as_utc(approval.approved_at) <= _as_utc(now) < _as_utc(
                approval.expires_at
            )
        ):
            raise ProductionReleaseStateError("Doble aprobación C8 inválida")
    validate_c8_release_approvals(
        candidate,
        sealed[ProductionApprovalRole.SECURITY],
        sealed[ProductionApprovalRole.OPERATIONS],
        now=_as_utc(now),
    )
    actors = {
        candidate.requested_by_operator_id,
        str(row["security_approved_by_operator_id"]),
        str(row["operations_approved_by_operator_id"]),
    }
    if len(actors) != 3:
        raise ProductionReleaseStateError("Separación de funciones C8 inválida")
    return security, operations


def mark_production_release_ready(
    conn: Any,
    release_id: str,
    *,
    operator_id: str,
    expected_version: int,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    current = _timestamp(now)
    operator = _uuid(operator_id, "operator_id")
    row = _release_row(conn, release_id, for_update=True)
    candidate = _candidate_from_release(row)
    _assess_candidate(candidate, now=current, policy_values=policy_values)
    _assert_candidate_current(candidate, current)
    _assert_dual_approval(conn, row, candidate, current)
    if operator != str(row["operations_approved_by_operator_id"]):
        raise ProductionReleaseStateError("Solo operations puede marcar ready")
    return _transition_release(
        conn,
        row=row,
        from_status="operations_approved",
        to_status="ready",
        operator_id=operator,
        expected_version=expected_version,
        timestamp_column="ready_at",
        reason_code="simulation_release_ready",
        payload={"candidate_sha256": candidate_sha256(candidate)},
        now=current,
    )


def _transition_release(
    conn: Any,
    *,
    row: Mapping[str, Any],
    from_status: str,
    to_status: str,
    operator_id: str,
    expected_version: int,
    timestamp_column: str,
    reason_code: str,
    payload: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    if str(row["status"]) == to_status:
        replay = _first_mapping(
            conn.execute(
                text(
                    f"""
                    SELECT operator_id, reason_code, payload
                    FROM {_RELEASE_EVENT_TABLE}
                    WHERE release_id=CAST(:release_id AS UUID)
                      AND event_type=:event_type AND to_status=:to_status
                    ORDER BY sequence_number DESC LIMIT 1
                    """
                ),
                {
                    "release_id": str(row["id"]),
                    "event_type": reason_code,
                    "to_status": to_status,
                },
            )
        )
        if (
            replay
            and str(replay.get("operator_id")) == operator_id
            and str(replay["reason_code"]) == reason_code
            and _mapping(replay["payload"]) == dict(payload)
        ):
            return release_snapshot(conn, str(row["id"]))
        raise ProductionReleaseConflict(
            "Replay de transición C8 con otro actor o cuerpo"
        )
    if str(row["status"]) != from_status:
        raise ProductionReleaseStateError(
            f"Transición C8 {row['status']} -> {to_status} no permitida"
        )
    if int(row["version"]) != int(expected_version):
        raise ProductionOptimisticLockError("Versión de release C8 obsoleta")
    result = conn.execute(
        text(
            f"""
            UPDATE {_RELEASE_TABLE}
            SET status=:to_status,
                {timestamp_column}=CAST(:changed_at AS TIMESTAMPTZ),
                version=version + 1,
                updated_at=CAST(:changed_at AS TIMESTAMPTZ)
            WHERE id=CAST(:release_id AS UUID)
              AND status=:from_status AND version=:expected_version
              AND simulation_only=TRUE
              AND external_effects_allowed=FALSE
              AND live_activation_allowed=FALSE
              AND provider_pack_present=FALSE
            """
        ),
        {
            "release_id": str(row["id"]),
            "from_status": from_status,
            "to_status": to_status,
            "expected_version": int(expected_version),
            "changed_at": now,
        },
    )
    _assert_changed(result)
    _append_release_event(
        conn,
        row=row,
        event_type=reason_code,
        operator_id=operator_id,
        from_status=from_status,
        to_status=to_status,
        reason_code=reason_code,
        payload=payload,
        created_at=now,
    )
    return release_snapshot(conn, str(row["id"]))


def simulate_production_release_activation(
    conn: Any,
    release_id: str,
    *,
    operator_id: str,
    expected_version: int,
    human_gate_phrase: str = C8_HUMAN_GATE_PHRASE,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Activa únicamente el estado simulado tras probar que live está bloqueado."""

    if human_gate_phrase != C8_HUMAN_GATE_PHRASE:
        raise ProductionReleaseStateError("Frase humana C8 no coincide")
    current = _timestamp(now)
    operator = _uuid(operator_id, "operator_id")
    row = _release_row(conn, release_id, for_update=True)
    candidate = _candidate_from_release(row)
    _assess_candidate(candidate, now=current, policy_values=policy_values)
    _prove_live_activation_unavailable(candidate, policy_values=policy_values)
    _assert_dual_approval(conn, row, candidate, current)
    if operator in {
        candidate.requested_by_operator_id,
        str(row["security_approved_by_operator_id"]),
        str(row["operations_approved_by_operator_id"]),
    }:
        raise ProductionReleaseStateError(
            "La activación simulada exige un cuarto operador"
        )
    gate_sha256 = sha256_hex(
        canonical_json(
            {
                "release_id": str(row["id"]),
                "candidate_sha256": candidate_sha256(candidate),
                "operator_id": operator,
                "human_gate_phrase": human_gate_phrase,
                "live_activation_allowed": False,
            }
        )
    )
    return _transition_release(
        conn,
        row=row,
        from_status="ready",
        to_status="simulated_active",
        operator_id=operator,
        expected_version=expected_version,
        timestamp_column="simulated_active_at",
        reason_code="simulation_activation_recorded",
        payload={
            "candidate_sha256": candidate_sha256(candidate),
            "human_gate_sha256": gate_sha256,
            "live_activation_allowed": False,
            "external_effects_allowed": False,
        },
        now=current,
    )


def emergency_halt_production_release(
    conn: Any,
    release_id: str,
    *,
    operator_id: str,
    reason_code: str,
    expected_version: int,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    current = _timestamp(now)
    operator = _uuid(operator_id, "operator_id")
    normalized_reason = str(reason_code or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", normalized_reason):
        raise ProductionReleaseStateError("reason_code C8 no válido")
    row = _release_row(conn, release_id, for_update=True)
    candidate = _candidate_from_release(row)
    # El apagado debe seguir disponible incluso si el entorno se degrada o
    # queda mal configurado. Solo confía en el release inerte ya persistido.
    if (
        not candidate.simulation_only
        or candidate.external_effects_allowed
        or candidate.live_activation_allowed
        or not candidate.human_activation_required
        or not bool(row["simulation_only"])
        or bool(row["external_effects_allowed"])
        or bool(row["live_activation_allowed"])
        or not bool(row["human_activation_required"])
        or bool(row["provider_pack_present"])
    ):
        raise ProductionReleaseStateError(
            "Release C8 perdió su frontera inerte persistida"
        )
    if str(row["status"]) == "halted":
        if (
            str(row.get("halted_by_operator_id")) == operator
            and str(row.get("halt_reason_code")) == normalized_reason
        ):
            return release_snapshot(conn, release_id)
        raise ProductionReleaseConflict("Replay de halt C8 con otro cuerpo")
    if str(row["status"]) not in {
        "proposed",
        "security_approved",
        "operations_approved",
        "ready",
        "simulated_active",
    }:
        raise ProductionReleaseStateError("Release C8 no puede detenerse")
    if int(row["version"]) != int(expected_version):
        raise ProductionOptimisticLockError("Versión de release C8 obsoleta")
    from_status = str(row["status"])
    result = conn.execute(
        text(
            f"""
            UPDATE {_RELEASE_TABLE}
            SET status='halted', emergency_halt=TRUE,
                halted_at=CAST(:halted_at AS TIMESTAMPTZ),
                halted_by_operator_id=CAST(:operator_id AS UUID),
                halt_reason_code=:reason_code,
                version=version + 1,
                updated_at=CAST(:halted_at AS TIMESTAMPTZ)
            WHERE id=CAST(:release_id AS UUID)
              AND status=:from_status AND version=:expected_version
              AND simulation_only=TRUE
              AND external_effects_allowed=FALSE
              AND live_activation_allowed=FALSE
            """
        ),
        {
            "release_id": release_id,
            "operator_id": operator,
            "reason_code": normalized_reason,
            "halted_at": current,
            "from_status": from_status,
            "expected_version": int(expected_version),
        },
    )
    _assert_changed(result)
    _append_release_event(
        conn,
        row=row,
        event_type="emergency_halt_recorded",
        operator_id=operator,
        from_status=from_status,
        to_status="halted",
        reason_code=normalized_reason,
        payload={"candidate_sha256": candidate_sha256(candidate)},
        created_at=current,
    )
    return release_snapshot(conn, release_id)


def _load_action(conn: Any, action_id: str) -> ConnectActionRequest:
    row = _first_mapping(
        conn.execute(
            text(
                """
                SELECT id, case_id, capability, satellite, target_type,
                       target_ref, payload, document_hashes,
                       requested_by_operator_id, requested_at, risk_class,
                       correlation_id, requires_dual_control, contract_version
                FROM public.rtm_connect_actions
                WHERE id=CAST(:action_id AS UUID)
                FOR SHARE
                """
            ),
            {"action_id": _uuid(action_id, "action_id")},
        )
    )
    if not row:
        raise LookupError("Acción C8 no encontrada")
    return ConnectActionRequest(
        action_id=str(row["id"]),
        case_id=str(row["case_id"]) if row.get("case_id") else None,
        capability=str(row["capability"]),
        satellite=str(row["satellite"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        payload=_mapping(row["payload"]),
        document_hashes=tuple(str(item) for item in (row["document_hashes"] or ())),
        requested_by_operator_id=str(row["requested_by_operator_id"]),
        requested_at=_row_timestamp(row["requested_at"]),
        risk_class=RiskClass(str(row["risk_class"])),
        correlation_id=(
            str(row["correlation_id"]) if row.get("correlation_id") else None
        ),
        requires_dual_control=bool(row["requires_dual_control"]),
        contract_version=str(row["contract_version"]),
    )


def _load_grant(
    conn: Any, authorization_id: str
) -> tuple[AuthorizationGrant, int]:
    row = _first_mapping(
        conn.execute(
            text(
                """
                SELECT id, action_id, authorization_version, authority_code,
                       authority_version, decision, payload_sha256,
                       idempotency_key, required_evidence_level,
                       authorized_connector_modes, approved_by_operator_ids,
                       authorized_at, expires_at, revoked_at,
                       legal_effect_authorized, frozen
                FROM public.rtm_connect_authorizations
                WHERE id=CAST(:authorization_id AS UUID)
                FOR SHARE
                """
            ),
            {"authorization_id": _uuid(authorization_id, "authorization_id")},
        )
    )
    if not row:
        raise LookupError("Autorización C8 no encontrada")
    grant = AuthorizationGrant(
        authorization_id=str(row["id"]),
        action_id=str(row["action_id"]),
        authority_code=str(row["authority_code"]),
        authority_version=str(row["authority_version"]),
        decision=str(row["decision"]),
        payload_sha256=str(row["payload_sha256"]),
        idempotency_key=str(row["idempotency_key"]),
        required_evidence_level=EvidenceLevel(str(row["required_evidence_level"])),
        authorized_connector_modes=tuple(
            ConnectorMode(str(item))
            for item in (row["authorized_connector_modes"] or ())
        ),
        approved_by_operator_ids=tuple(
            str(item) for item in (row["approved_by_operator_ids"] or ())
        ),
        authorized_at=_row_timestamp(row["authorized_at"]),
        expires_at=(
            _row_timestamp(row["expires_at"]) if row.get("expires_at") else None
        ),
        revoked_at=(
            _row_timestamp(row["revoked_at"]) if row.get("revoked_at") else None
        ),
        legal_effect_authorized=bool(row["legal_effect_authorized"]),
        frozen=bool(row["frozen"]),
    )
    return grant, int(row["authorization_version"])


def _revalidate_dispatch_authority(
    conn: Any,
    row: Mapping[str, Any],
    *,
    now: datetime | str | None,
    policy_values: Mapping[str, str] | None,
    release: Mapping[str, Any] | None = None,
    historical_claim_outcome: bool = False,
) -> tuple[
    ConnectActionRequest,
    AuthorizationGrant,
    ProductionAdmissionCandidate,
    Mapping[str, Any],
]:
    locked_release = release or _release_row(
        conn, str(row["release_id"]), for_update=True
    )
    if str(locked_release["id"]) != str(row["release_id"]):
        raise ProductionDispatchReplayConflict("Release de outbox C8 alterado")
    release = locked_release
    candidate = _candidate_from_release(release)
    if historical_claim_outcome and row.get("claimed_at") is None:
        raise ProductionDispatchStateError("Outcome C8 sin instante de claim")
    decision_time = (
        _as_utc(row["claimed_at"])
        if historical_claim_outcome
        else _as_utc(now)
    )
    if historical_claim_outcome:
        # Clasificar un claim ya nacido no puede depender de que el entorno
        # actual siga sano. La decisión se ata al snapshot y al instante del
        # claim; así UNKNOWN/manual_review siguen disponibles tras un halt o
        # una degradación de configuración, sin permitir un nuevo claim.
        _assert_candidate_current(candidate, decision_time)
        simulated_at = release.get("simulated_active_at")
        halted_at = release.get("halted_at")
        if (
            simulated_at is None
            or _as_utc(simulated_at) > decision_time
            or (halted_at is not None and _as_utc(halted_at) < decision_time)
            or str(row["status"]) not in {
                "claimed",
                "dry_run_confirmed",
                "unknown",
                "manual_review",
            }
        ):
            raise ProductionDispatchStateError(
                "Claim C8 no nació dentro de release simulado-activo"
            )
    else:
        _assess_candidate(
            candidate, now=decision_time, policy_values=policy_values
        )
        if str(release["status"]) != "simulated_active" or bool(
            release["emergency_halt"]
        ):
            raise ProductionDispatchStateError(
                "Release C8 no está simulado-activo"
            )
    _assert_dual_approval(conn, release, candidate, decision_time)
    action = _load_action(conn, str(row["action_id"]))
    grant, authorization_version = _load_grant(
        conn, str(row["authorization_id"])
    )
    policy_grant = grant
    if historical_claim_outcome and grant.revoked_at is not None:
        if _as_utc(grant.revoked_at) <= decision_time:
            raise ProductionDispatchStateError(
                "Grant C8 ya estaba revocado al reclamar"
            )
        policy_grant = replace(grant, revoked_at=None)
    validate_c8_admission_authority(
        action,
        policy_grant,
        candidate=candidate,
        now=decision_time,
    )
    if (
        authorization_version != int(row["authorization_version"])
        or grant.payload_sha256 != str(row["payload_sha256"])
        or payload_sha256(action) != str(row["request_sha256"])
        or candidate_sha256(candidate) != str(row["release_binding_sha256"])
        or candidate.connector_manifest_sha256
        != str(row["release_manifest_sha256"])
    ):
        raise ProductionDispatchReplayConflict("Scope persistido C8 alterado")
    if (
        not bool(row["dry_run_only"])
        or bool(row["network_allowed"])
        or bool(row["provider_contacted"])
        or bool(row["external_effects_allowed"])
        or grant.legal_effect_authorized
    ):
        raise ProductionControlError("Outbox C8 amplía los efectos permitidos")
    return action, grant, candidate, release


def _lock_dispatch_scope(
    conn: Any, dispatch_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Orden global de locks: release padre y después outbox hija."""

    observed = _dispatch_row(conn, dispatch_id, for_update=False)
    release = _release_row(conn, str(observed["release_id"]), for_update=True)
    row = _dispatch_row(conn, dispatch_id, for_update=True)
    if str(row["release_id"]) != str(release["id"]):
        raise ProductionDispatchReplayConflict("Scope C8 cambió durante el lock")
    return row, release


def _assert_dispatch_quota(
    conn: Any,
    release: Mapping[str, Any],
    candidate: ProductionAdmissionCandidate,
    *,
    now: datetime | str | None,
) -> None:
    current = _as_utc(now)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    counts = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*) AS total_count,
                       COUNT(*) FILTER (
                           WHERE created_at >= CAST(
                               :day_start AS TIMESTAMPTZ
                           )
                             AND created_at < CAST(
                               :day_end AS TIMESTAMPTZ
                           )
                       ) AS daily_count
                FROM {_DISPATCH_TABLE}
                WHERE release_id=CAST(:release_id AS UUID)
                """
            ),
            {
                "release_id": str(release["id"]),
                "day_start": _timestamp(day_start),
                "day_end": _timestamp(day_end),
            },
        )
    )
    if counts is None:
        raise ProductionControlError("No se pudo evaluar cuota C8")
    daily_limit = min(
        int(release["daily_action_limit"]),
        candidate.max_simulated_actions_per_day,
    )
    if int(counts["daily_count"]) >= daily_limit:
        raise ProductionDispatchStateError("Cuota diaria C8 agotada")
    if int(counts["total_count"]) >= candidate.max_simulated_actions_total:
        raise ProductionDispatchStateError("Cuota total C8 agotada")


def _assert_dispatch_payload_size(
    action: ConnectActionRequest,
    candidate: ProductionAdmissionCandidate,
) -> None:
    payload_bytes = len(canonical_json(action.payload).encode("utf-8"))
    if payload_bytes > candidate.max_payload_bytes:
        raise ProductionDispatchStateError("Payload C8 supera el límite sellado")


def _assert_claim_capacity(
    conn: Any,
    row: Mapping[str, Any],
    release: Mapping[str, Any],
    *,
    now: datetime | str | None,
) -> None:
    claimed = int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*) FROM {_DISPATCH_TABLE}
                WHERE release_id=CAST(:release_id AS UUID)
                  AND status='claimed'
                  AND claim_expires_at > CAST(:now AS TIMESTAMPTZ)
                  AND id<>CAST(:dispatch_id AS UUID)
                """
            ),
            {
                "release_id": str(release["id"]),
                "dispatch_id": str(row["id"]),
                "now": _timestamp(now),
            },
        ).scalar_one()
    )
    if claimed >= int(release["max_concurrency"]):
        raise ProductionDispatchStateError("Concurrencia C8 agotada")


def _stored_intent_from_dispatch(row: Mapping[str, Any]) -> SimulatedOutboxIntent:
    metadata = _mapping(row.get("metadata"))
    raw = metadata.get("intent")
    if not isinstance(raw, Mapping):
        raise ProductionDispatchReplayConflict("Outbox C8 sin intención sellada")
    return SimulatedOutboxIntent(**dict(raw))


def _intent_from_dispatch(row: Mapping[str, Any]) -> SimulatedOutboxIntent:
    stored = _stored_intent_from_dispatch(row)
    material = dict(stored.__dict__)
    material["status"] = str(row["status"])
    material["reconciliation_required"] = str(row["status"]) in {
        SimulatedOutboxStatus.UNKNOWN.value,
        SimulatedOutboxStatus.MANUAL_REVIEW.value,
    }
    intent = SimulatedOutboxIntent(**material)
    if (
        intent.intent_id != str(row["id"])
        or intent.action_id != str(row["action_id"])
        or intent.authorization_id != str(row["authorization_id"])
        or intent.candidate_id != str(row["release_id"])
        or intent.candidate_sha256 != str(row["release_binding_sha256"])
        or intent.request_sha256 != str(row["request_sha256"])
    ):
        raise ProductionDispatchReplayConflict("Identidad de outbox C8 alterada")
    return intent


def dispatch_snapshot(conn: Any, dispatch_id: str) -> SimulatedOutboxIntent:
    return _intent_from_dispatch(_dispatch_row(conn, dispatch_id, for_update=False))


def prepare_dispatch_dry_run(
    conn: Any,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    request: SimulatedOutboxIntent,
    release_id: str,
    *,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> SimulatedOutboxIntent:
    """Prepara una intención persistida; no existe ruta de entrega."""

    if type(action) is not ConnectActionRequest:
        raise ProductionControlError("Acción C8 no sellada")
    if type(grant) is not AuthorizationGrant:
        raise ProductionControlError("Grant C8 no sellado")
    if type(request) is not SimulatedOutboxIntent:
        raise ProductionControlError("Intención C8 no sellada")
    current = _timestamp(now)
    release = _release_row(conn, release_id, for_update=True)
    candidate = _candidate_from_release(release)
    _assess_candidate(candidate, now=current, policy_values=policy_values)
    _prove_live_activation_unavailable(candidate, policy_values=policy_values)
    _assert_dual_approval(conn, release, candidate, current)
    validate_c8_admission_authority(
        action, grant, candidate=candidate, now=_as_utc(current)
    )
    persisted_action = _load_action(conn, action.action_id)
    persisted_grant, authorization_version = _load_grant(
        conn, grant.authorization_id
    )
    if persisted_action != action or persisted_grant != grant:
        raise ProductionDispatchReplayConflict(
            "Acción o grant C8 no coincide con el ledger congelado"
        )
    if str(release["status"]) != "simulated_active" or bool(
        release["emergency_halt"]
    ):
        raise ProductionDispatchStateError("Release C8 no está simulado-activo")
    if (
        request.status is not SimulatedOutboxStatus.PREPARED
        or request.reconciliation_required
        or request.candidate_id != candidate.candidate_id
        or request.candidate_sha256 != candidate_sha256(candidate)
        or request.action_id != action.action_id
        or request.authorization_id != grant.authorization_id
        or request.request_sha256 != payload_sha256(action)
        or request.idempotency_key != grant.idempotency_key
        or action.requested_by_operator_id != candidate.requested_by_operator_id
    ):
        raise ProductionDispatchReplayConflict("Intención C8 fuera del scope")
    release_approvers = {
        str(release["security_approved_by_operator_id"]),
        str(release["operations_approved_by_operator_id"]),
    }
    if set(grant.approved_by_operator_ids) != release_approvers:
        raise ProductionDispatchReplayConflict(
            "Grant y doble aprobación de release C8 no coinciden"
        )
    binding = _dispatch_binding_sha256(release_id, action, grant, request)
    effect_digest = _production_effect_sha256(
        release_id, action, grant, request
    )
    business_command_id = f"rtmc8:command:{effect_digest}"
    production_effect_key = f"rtmc8:dry-run:{effect_digest}"
    existing = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT * FROM {_DISPATCH_TABLE}
                WHERE id=CAST(:dispatch_id AS UUID)
                   OR business_command_id=:business_command_id
                   OR production_effect_key=:production_effect_key
                ORDER BY id
                LIMIT 1 FOR UPDATE
                """
            ),
            {
                "dispatch_id": request.intent_id,
                "business_command_id": business_command_id,
                "production_effect_key": production_effect_key,
            },
        )
    )
    if existing:
        stored_binding = _mapping(existing.get("metadata")).get(
            "dispatch_binding_sha256"
        )
        stored_effect = _mapping(existing.get("metadata")).get(
            "production_effect_sha256"
        )
        if (
            str(existing["id"]) == request.intent_id
            and str(stored_binding) == binding
            and _stored_intent_from_dispatch(existing) == request
        ):
            return dispatch_snapshot(conn, request.intent_id)
        if (
            str(existing["id"]) != request.intent_id
            and str(existing["production_effect_key"])
            == production_effect_key
            and str(stored_effect) == effect_digest
        ):
            return dispatch_snapshot(conn, str(existing["id"]))
        raise ProductionDispatchReplayConflict(
            "Replay de outbox C8 con identidad o cuerpo distinto"
        )
    if _timestamp(request.created_at) != current:
        raise ProductionDispatchReplayConflict(
            "La creación de una intención nueva C8 debe coincidir con now"
        )
    _assert_dispatch_payload_size(action, candidate)
    _assert_dispatch_quota(conn, release, candidate, now=current)
    metadata = {
        "intent": asdict(request),
        "dispatch_binding_sha256": binding,
        "production_effect_sha256": effect_digest,
        "expected_admission_payload": expected_c8_admission_payload(
            candidate_sha256(candidate)
        ),
        "network_call_performed": False,
        "secret_resolution_performed": False,
        "blind_retry_allowed": False,
    }
    result = conn.execute(
        text(
            f"""
            INSERT INTO {_DISPATCH_TABLE}(
                id, action_id, authorization_id, authorization_version,
                release_id, status, business_command_id,
                production_effect_key, payload_sha256, request_sha256,
                release_manifest_sha256, release_binding_sha256,
                dry_run_only, network_allowed, provider_contacted,
                external_effects_allowed, claim_fence, version, metadata,
                created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:authorization_id AS UUID), :authorization_version,
                CAST(:release_id AS UUID), 'prepared', :business_command_id,
                :production_effect_key, :payload_sha256, :request_sha256,
                :release_manifest_sha256, :release_binding_sha256,
                TRUE, FALSE, FALSE, FALSE, 0, 1,
                CAST(:metadata AS JSONB), CAST(:created_at AS TIMESTAMPTZ),
                CAST(:created_at AS TIMESTAMPTZ)
            )
            """
        ),
        {
            "id": request.intent_id,
            "action_id": action.action_id,
            "authorization_id": grant.authorization_id,
            "authorization_version": authorization_version,
            "release_id": release_id,
            "business_command_id": business_command_id,
            "production_effect_key": production_effect_key,
            "payload_sha256": grant.payload_sha256,
            "request_sha256": request.request_sha256,
            "release_manifest_sha256": candidate.connector_manifest_sha256,
            "release_binding_sha256": candidate_sha256(candidate),
            "metadata": _json(metadata),
            "created_at": current,
        },
    )
    _assert_changed(result)
    event_row = {
        "id": request.intent_id,
        "action_id": action.action_id,
        "authorization_id": grant.authorization_id,
        "release_id": release_id,
        "release_binding_sha256": candidate_sha256(candidate),
    }
    _append_dispatch_event(
        conn,
        row=event_row,
        event_type="dispatch_dry_run_prepared",
        operator_id=None,
        from_status=None,
        to_status="prepared",
        reason_code="simulation_only_recorded",
        payload={
            "dispatch_binding_sha256": binding,
            "production_effect_sha256": effect_digest,
            "dry_run_only": True,
            "network_allowed": False,
            "provider_contacted": False,
            "external_effects_allowed": False,
        },
        created_at=current,
    )
    return dispatch_snapshot(conn, request.intent_id)


def _claim_token_uuid(claim_token_sha256: str) -> str:
    digest = _sha256(claim_token_sha256, "claim_token_sha256")
    return str(uuid.UUID(hex=digest[:32]))


def _claim_event_payload(conn: Any, dispatch_id: str) -> Mapping[str, Any] | None:
    row = _first_mapping(
        conn.execute(
            text(
                f"""
                SELECT payload FROM {_DISPATCH_EVENT_TABLE}
                WHERE outbox_id=CAST(:dispatch_id AS UUID)
                  AND event_type='dispatch_dry_run_claimed'
                ORDER BY sequence_number DESC LIMIT 1
                """
            ),
            {"dispatch_id": dispatch_id},
        )
    )
    if not row:
        return None
    return _mapping(row["payload"])


def _claim_event_sha256(conn: Any, dispatch_id: str) -> str | None:
    payload = _claim_event_payload(conn, dispatch_id)
    if payload is None:
        return None
    return str(payload.get("claim_token_sha256") or "")


def _assert_claim(
    conn: Any,
    row: Mapping[str, Any],
    *,
    claim_owner: str,
    claim_token_sha256: str,
    claim_fence: int,
    now: datetime | str | None,
    require_current: bool = True,
) -> None:
    owner = str(claim_owner or "").strip()
    digest = _sha256(claim_token_sha256, "claim_token_sha256")
    if (
        str(row.get("claim_owner")) != owner
        or str(row.get("claim_token")) != _claim_token_uuid(digest)
        or int(row.get("claim_fence") or 0) != int(claim_fence)
        or _claim_event_sha256(conn, str(row["id"])) != digest
    ):
        raise ProductionClaimFenceError("Claim C8 no coincide")
    if require_current and _as_utc(row["claim_expires_at"]) <= _as_utc(now):
        raise ProductionClaimFenceError("Claim C8 expirado")


def claim_dispatch_dry_run(
    conn: Any,
    dispatch_id: str,
    *,
    expected_version: int,
    claim_token_sha256: str,
    claim_owner: str,
    claim_ttl_seconds: int = 300,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> SimulatedOutboxIntent:
    current = _timestamp(now)
    owner = str(claim_owner or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{2,95}", owner):
        raise ProductionClaimFenceError("claim_owner C8 no válido")
    if isinstance(claim_ttl_seconds, bool) or not 1 <= int(claim_ttl_seconds) <= 300:
        raise ProductionClaimFenceError("TTL de claim C8 fuera de límite")
    digest = _sha256(claim_token_sha256, "claim_token_sha256")
    token_uuid = _claim_token_uuid(digest)
    row, release = _lock_dispatch_scope(conn, dispatch_id)
    _, grant, candidate, _ = _revalidate_dispatch_authority(
        conn,
        row,
        now=current,
        policy_values=policy_values,
        release=release,
    )
    if str(row["status"]) == "claimed":
        claim_event = _claim_event_payload(conn, dispatch_id) or {}
        if (
            str(row.get("claim_owner")) == owner
            and str(row.get("claim_token")) == token_uuid
            and str(claim_event.get("claim_token_sha256")) == digest
            and str(claim_event.get("claim_owner")) == owner
            and int(claim_event.get("claim_ttl_seconds") or 0)
            == int(claim_ttl_seconds)
        ):
            return dispatch_snapshot(conn, dispatch_id)
        raise ProductionClaimFenceError("Claim C8 ya pertenece a otro token")
    if str(row["status"]) != "prepared":
        raise ProductionDispatchStateError("Solo prepared puede reclamarse")
    if int(row["version"]) != int(expected_version):
        raise ProductionOptimisticLockError("Versión de outbox C8 obsoleta")
    expires_at = _as_utc(current) + timedelta(seconds=int(claim_ttl_seconds))
    if (
        grant.expires_at is None
        or expires_at >= _as_utc(grant.expires_at)
        or expires_at >= _as_utc(candidate.expires_at)
    ):
        raise ProductionClaimFenceError(
            "TTL del claim C8 excede grant o candidato"
        )
    _assert_claim_capacity(conn, row, release, now=current)
    expires = _timestamp(expires_at)
    result = conn.execute(
        text(
            f"""
            UPDATE {_DISPATCH_TABLE}
            SET status='claimed', claim_owner=:claim_owner,
                claim_token=CAST(:claim_token AS UUID),
                claim_fence=claim_fence + 1,
                claimed_at=CAST(:claimed_at AS TIMESTAMPTZ),
                claim_expires_at=CAST(:claim_expires_at AS TIMESTAMPTZ),
                version=version + 1,
                updated_at=CAST(:claimed_at AS TIMESTAMPTZ)
            WHERE id=CAST(:dispatch_id AS UUID)
              AND status='prepared' AND version=:expected_version
              AND claim_fence=0 AND dry_run_only=TRUE
              AND network_allowed=FALSE AND provider_contacted=FALSE
              AND external_effects_allowed=FALSE
            """
        ),
        {
            "dispatch_id": dispatch_id,
            "claim_owner": owner,
            "claim_token": token_uuid,
            "claimed_at": current,
            "claim_expires_at": expires,
            "expected_version": int(expected_version),
        },
    )
    _assert_changed(result)
    event_row = dict(row)
    event_row["status"] = "claimed"
    _append_dispatch_event(
        conn,
        row=event_row,
        event_type="dispatch_dry_run_claimed",
        operator_id=None,
        from_status="prepared",
        to_status="claimed",
        reason_code="simulation_claim_fenced",
        payload={
            "claim_owner": owner,
            "claim_token_sha256": digest,
            "claim_fence": int(row["claim_fence"]) + 1,
            "claim_ttl_seconds": int(claim_ttl_seconds),
            "claim_expires_at": expires,
        },
        created_at=current,
    )
    return dispatch_snapshot(conn, dispatch_id)


def _finish_claimed_dispatch(
    conn: Any,
    dispatch_id: str,
    *,
    target_status: SimulatedOutboxStatus,
    timestamp_column: str,
    event_type: str,
    reason_code: str,
    expected_version: int,
    claim_owner: str,
    claim_token_sha256: str,
    claim_fence: int,
    now: datetime | str | None,
    policy_values: Mapping[str, str] | None,
) -> SimulatedOutboxIntent:
    current = _timestamp(now)
    row, release = _lock_dispatch_scope(conn, dispatch_id)
    _revalidate_dispatch_authority(
        conn,
        row,
        now=current,
        policy_values=policy_values,
        release=release,
        historical_claim_outcome=True,
    )
    status = str(row["status"])
    if status == target_status.value:
        _assert_claim(
            conn,
            row,
            claim_owner=claim_owner,
            claim_token_sha256=claim_token_sha256,
            claim_fence=claim_fence,
            now=current,
            require_current=False,
        )
        return dispatch_snapshot(conn, dispatch_id)
    if status != "claimed":
        raise ProductionDispatchStateError(
            f"Outbox C8 {status} no puede pasar a {target_status.value}"
        )
    if int(row["version"]) != int(expected_version):
        raise ProductionOptimisticLockError("Versión de outbox C8 obsoleta")
    _assert_claim(
        conn,
        row,
        claim_owner=claim_owner,
        claim_token_sha256=claim_token_sha256,
        claim_fence=claim_fence,
        now=current,
        require_current=(
            target_status is SimulatedOutboxStatus.DRY_RUN_CONFIRMED
        ),
    )
    result = conn.execute(
        text(
            f"""
            UPDATE {_DISPATCH_TABLE}
            SET status=:target_status,
                {timestamp_column}=CAST(:changed_at AS TIMESTAMPTZ),
                version=version + 1,
                updated_at=CAST(:changed_at AS TIMESTAMPTZ)
            WHERE id=CAST(:dispatch_id AS UUID)
              AND status='claimed' AND version=:expected_version
              AND claim_owner=:claim_owner
              AND claim_token=CAST(:claim_token AS UUID)
              AND claim_fence=:claim_fence
              AND dry_run_only=TRUE AND network_allowed=FALSE
              AND provider_contacted=FALSE
              AND external_effects_allowed=FALSE
            """
        ),
        {
            "dispatch_id": dispatch_id,
            "target_status": target_status.value,
            "changed_at": current,
            "expected_version": int(expected_version),
            "claim_owner": str(claim_owner).strip(),
            "claim_token": _claim_token_uuid(claim_token_sha256),
            "claim_fence": int(claim_fence),
        },
    )
    _assert_changed(result)
    _append_dispatch_event(
        conn,
        row=row,
        event_type=event_type,
        operator_id=None,
        from_status="claimed",
        to_status=target_status.value,
        reason_code=reason_code,
        payload={
            "claim_token_sha256": _sha256(
                claim_token_sha256, "claim_token_sha256"
            ),
            "claim_fence": int(claim_fence),
            "network_call_performed": False,
            "external_effects_allowed": False,
            "reconciliation_required": (
                target_status is SimulatedOutboxStatus.UNKNOWN
            ),
        },
        created_at=current,
    )
    return dispatch_snapshot(conn, dispatch_id)


def confirm_dispatch_dry_run(
    conn: Any,
    dispatch_id: str,
    *,
    expected_version: int,
    claim_owner: str,
    claim_token_sha256: str,
    claim_fence: int,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> SimulatedOutboxIntent:
    return _finish_claimed_dispatch(
        conn,
        dispatch_id,
        target_status=SimulatedOutboxStatus.DRY_RUN_CONFIRMED,
        timestamp_column="dry_run_confirmed_at",
        event_type="dispatch_dry_run_confirmed",
        reason_code="simulation_completed_without_effect",
        expected_version=expected_version,
        claim_owner=claim_owner,
        claim_token_sha256=claim_token_sha256,
        claim_fence=claim_fence,
        now=now,
        policy_values=policy_values,
    )


def mark_dispatch_unknown(
    conn: Any,
    dispatch_id: str,
    *,
    expected_version: int,
    claim_owner: str,
    claim_token_sha256: str,
    claim_fence: int,
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> SimulatedOutboxIntent:
    return _finish_claimed_dispatch(
        conn,
        dispatch_id,
        target_status=SimulatedOutboxStatus.UNKNOWN,
        timestamp_column="unknown_at",
        event_type="dispatch_simulation_unknown",
        reason_code="manual_reconciliation_required",
        expected_version=expected_version,
        claim_owner=claim_owner,
        claim_token_sha256=claim_token_sha256,
        claim_fence=claim_fence,
        now=now,
        policy_values=policy_values,
    )


def move_dispatch_manual_review(
    conn: Any,
    dispatch_id: str,
    *,
    operator_id: str,
    expected_version: int,
    claim_owner: str,
    claim_token_sha256: str,
    claim_fence: int,
    reason_code: str = "simulation_unknown_manual_review",
    now: datetime | str | None = None,
    policy_values: Mapping[str, str] | None = None,
) -> SimulatedOutboxIntent:
    current = _timestamp(now)
    operator = _uuid(operator_id, "operator_id")
    reason = str(reason_code or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", reason):
        raise ProductionDispatchStateError("reason_code C8 no válido")
    row, release = _lock_dispatch_scope(conn, dispatch_id)
    _revalidate_dispatch_authority(
        conn,
        row,
        now=current,
        policy_values=policy_values,
        release=release,
        historical_claim_outcome=True,
    )
    if str(row["status"]) == "manual_review":
        replay = _first_mapping(
            conn.execute(
                text(
                    f"""
                    SELECT operator_id, reason_code, payload
                    FROM {_DISPATCH_EVENT_TABLE}
                    WHERE outbox_id=CAST(:dispatch_id AS UUID)
                      AND event_type='dispatch_manual_review_recorded'
                    ORDER BY sequence_number DESC LIMIT 1
                    """
                ),
                {"dispatch_id": dispatch_id},
            )
        )
        if (
            replay
            and str(replay.get("operator_id")) == operator
            and str(replay["reason_code"]) == reason
            and str(_mapping(replay["payload"]).get("claim_token_sha256"))
            == _sha256(claim_token_sha256, "claim_token_sha256")
            and int(_mapping(replay["payload"]).get("claim_fence") or 0)
            == int(claim_fence)
        ):
            return dispatch_snapshot(conn, dispatch_id)
        raise ProductionDispatchReplayConflict(
            "Replay de revisión manual C8 con otro cuerpo"
        )
    if str(row["status"]) != "unknown":
        raise ProductionDispatchStateError(
            "Solo UNKNOWN puede pasar a revisión manual; nunca se reintenta"
        )
    if int(row["version"]) != int(expected_version):
        raise ProductionOptimisticLockError("Versión de outbox C8 obsoleta")
    _assert_claim(
        conn,
        row,
        claim_owner=claim_owner,
        claim_token_sha256=claim_token_sha256,
        claim_fence=claim_fence,
        now=current,
        require_current=False,
    )
    result = conn.execute(
        text(
            f"""
            UPDATE {_DISPATCH_TABLE}
            SET status='manual_review',
                manual_review_at=CAST(:changed_at AS TIMESTAMPTZ),
                version=version + 1,
                updated_at=CAST(:changed_at AS TIMESTAMPTZ)
            WHERE id=CAST(:dispatch_id AS UUID)
              AND status='unknown' AND version=:expected_version
              AND claim_owner=:claim_owner
              AND claim_token=CAST(:claim_token AS UUID)
              AND claim_fence=:claim_fence
              AND dry_run_only=TRUE AND network_allowed=FALSE
              AND provider_contacted=FALSE
              AND external_effects_allowed=FALSE
            """
        ),
        {
            "dispatch_id": dispatch_id,
            "changed_at": current,
            "expected_version": int(expected_version),
            "claim_owner": str(claim_owner).strip(),
            "claim_token": _claim_token_uuid(claim_token_sha256),
            "claim_fence": int(claim_fence),
        },
    )
    _assert_changed(result)
    _append_dispatch_event(
        conn,
        row=row,
        event_type="dispatch_manual_review_recorded",
        operator_id=operator,
        from_status="unknown",
        to_status="manual_review",
        reason_code=reason,
        payload={
            "claim_token_sha256": _sha256(
                claim_token_sha256, "claim_token_sha256"
            ),
            "claim_fence": int(claim_fence),
            "reconciliation_required": True,
            "blind_retry_allowed": False,
        },
        created_at=current,
    )
    return dispatch_snapshot(conn, dispatch_id)


__all__ = [
    "RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION",
    "C8_HUMAN_GATE_PHRASE",
    "ProductionControlError",
    "ProductionReleaseConflict",
    "ProductionReleaseStateError",
    "ProductionDispatchReplayConflict",
    "ProductionDispatchStateError",
    "ProductionOptimisticLockError",
    "ProductionClaimFenceError",
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
]
