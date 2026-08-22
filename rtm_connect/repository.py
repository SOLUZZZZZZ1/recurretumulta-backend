"""Repositorio transaccional del Kernel RTM CONNECT C1.

No ejecuta conectores ni usa red. Persiste acciones, autorizaciones, intentos,
evidencia, transiciones e idempotencia bajo los contratos congelados en C0.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.authority import validate_execution_authority
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.evidence import confirmation_gate, validate_evidence_record
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.state_machine import ActionStatus, assert_transition


RTM_CONNECT_C1_REPOSITORY_VERSION = "rtm_connect_c1_repository_v1_0"

_RISK_ORDER = {
    RiskClass.R0_OBSERVATION.value: 0,
    RiskClass.R1_LOW_REVERSIBLE.value: 1,
    RiskClass.R2_BUSINESS_EFFECT.value: 2,
    RiskClass.R3_LEGAL_OR_FINANCIAL.value: 3,
    RiskClass.R4_CRITICAL_REGULATED.value: 4,
}

_FORBIDDEN_CONFIGURATION_KEYS = {
    "password", "raw_token", "access_token", "refresh_token", "api_key",
    "private_key", "client_secret", "cookie", "authorization_header",
    "apikey", "clientsecret", "authorization", "secret", "token",
}


class ConnectKernelError(RuntimeError):
    pass


class IdempotencyConflict(ConnectKernelError):
    pass


class ConnectorNotEligible(ConnectKernelError):
    pass


class EvidenceGateError(ConnectKernelError):
    pass


@dataclass(frozen=True)
class ConnectorRegistration:
    connector_id: str
    code: str
    version: str
    mode: str
    status: str
    created: bool


@dataclass(frozen=True)
class ActionCreateOutcome:
    action_id: str
    idempotency_key: str
    payload_sha256: str
    created: bool
    replayed: bool


@dataclass(frozen=True)
class AttemptStart:
    attempt_id: str
    action_id: str
    connector_id: str
    attempt_number: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _assert_no_secrets(value: Any, *, path: str = "configuration") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_CONFIGURATION_KEYS:
                raise ValueError(f"{path}.{key} no puede contener secretos")
            _assert_no_secrets(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secrets(child, path=f"{path}[{index}]")


def _append_transition(
    conn,
    *,
    action_id: str,
    from_status: str | None,
    to_status: str,
    actor_type: str,
    reason_code: str,
    operator_id: str | None = None,
    attempt_id: str | None = None,
    reason_detail: str | None = None,
    request_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    conn.execute(
        text(
            """
            SELECT id FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            FOR UPDATE
            """
        ),
        {"action_id": action_id},
    ).one()
    sequence_number = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM rtm_connect_transitions
                WHERE action_id=CAST(:action_id AS UUID)
                """
            ),
            {"action_id": action_id},
        ).scalar_one()
    )
    transition_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_transitions(
                id, action_id, attempt_id, sequence_number,
                from_status, to_status, actor_type, operator_id,
                reason_code, reason_detail, request_id, metadata, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:attempt_id AS UUID), :sequence_number,
                :from_status, :to_status, :actor_type,
                CAST(:operator_id AS UUID), :reason_code,
                :reason_detail, :request_id, CAST(:metadata AS JSONB), NOW()
            )
            """
        ),
        {
            "id": transition_id,
            "sequence_number": sequence_number,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "from_status": from_status,
            "to_status": to_status,
            "actor_type": actor_type,
            "operator_id": operator_id,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "request_id": request_id,
            "metadata": _json(dict(metadata or {})),
        },
    )
    return transition_id


def _transition_action(
    conn,
    *,
    action_id: str,
    target: ActionStatus,
    actor_type: str,
    reason_code: str,
    operator_id: str | None = None,
    attempt_id: str | None = None,
    reason_detail: str | None = None,
    request_id: str | None = None,
    external_reference: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    row = conn.execute(
        text(
            """
            SELECT status
            FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            FOR UPDATE
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción RTM CONNECT no encontrada")
    current = ActionStatus(str(row["status"]))
    if current is target:
        return False
    assert_transition(current, target)
    conn.execute(
        text(
            """
            UPDATE rtm_connect_actions
            SET status=:status,
                external_reference=COALESCE(:external_reference, external_reference)
            WHERE id=CAST(:action_id AS UUID)
            """
        ),
        {
            "action_id": action_id,
            "status": target.value,
            "external_reference": external_reference,
        },
    )
    _append_transition(
        conn,
        action_id=action_id,
        attempt_id=attempt_id,
        from_status=current.value,
        to_status=target.value,
        actor_type=actor_type,
        operator_id=operator_id,
        reason_code=reason_code,
        reason_detail=reason_detail,
        request_id=request_id,
        metadata=metadata,
    )
    return True


def register_synthetic_connector(
    conn,
    *,
    code: str,
    version: str,
    mode: ConnectorMode,
    capabilities: tuple[str, ...],
    risk_ceiling: RiskClass,
    supports_reconciliation: bool,
    configuration: Mapping[str, Any] | None = None,
) -> ConnectorRegistration:
    normalized_code = str(code or "").strip().lower()
    normalized_version = str(version or "").strip().lower()
    clean_capabilities = tuple(sorted({str(v).strip().lower() for v in capabilities}))
    if not clean_capabilities or any(not value for value in clean_capabilities):
        raise ValueError("El conector debe declarar capacidades")
    config = dict(configuration or {})
    _assert_no_secrets(config)
    connector_id = str(uuid.uuid4())
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_connect_connectors(
                id, code, version, mode, status, environment, synthetic_only,
                capabilities, risk_ceiling, supports_idempotency,
                supports_reconciliation, credential_ref, configuration,
                created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), :code, :version, :mode, 'active',
                'staging', TRUE, CAST(:capabilities AS JSONB), :risk_ceiling,
                TRUE, :supports_reconciliation, NULL,
                CAST(:configuration AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (code, version) DO NOTHING
            RETURNING id, code, version, mode, status
            """
        ),
        {
            "id": connector_id,
            "code": normalized_code,
            "version": normalized_version,
            "mode": mode.value,
            "capabilities": _json(list(clean_capabilities)),
            "risk_ceiling": risk_ceiling.value,
            "supports_reconciliation": bool(supports_reconciliation),
            "configuration": _json(config),
        },
    ).mappings().first()
    if row:
        return ConnectorRegistration(
            connector_id=str(row["id"]),
            code=str(row["code"]),
            version=str(row["version"]),
            mode=str(row["mode"]),
            status=str(row["status"]),
            created=True,
        )
    existing = conn.execute(
        text(
            """
            SELECT id, code, version, mode, status, synthetic_only,
                   capabilities, risk_ceiling, supports_reconciliation
            FROM rtm_connect_connectors
            WHERE code=:code AND version=:version
            """
        ),
        {"code": normalized_code, "version": normalized_version},
    ).mappings().one()
    if (
        not bool(existing["synthetic_only"])
        or str(existing["mode"]) != mode.value
        or set(existing["capabilities"] or []) != set(clean_capabilities)
        or str(existing["risk_ceiling"]) != risk_ceiling.value
        or bool(existing["supports_reconciliation"])
        != bool(supports_reconciliation)
    ):
        raise ConnectorNotEligible(
            "La versión existente del conector no coincide con el contrato"
        )
    return ConnectorRegistration(
        connector_id=str(existing["id"]),
        code=str(existing["code"]),
        version=str(existing["version"]),
        mode=str(existing["mode"]),
        status=str(existing["status"]),
        created=False,
    )


def create_action(
    conn,
    *,
    action: ConnectActionRequest,
    authority_scope: str,
) -> ActionCreateOutcome:
    scope = str(authority_scope or "").strip().lower()
    key = derive_idempotency_key(action, authority_scope=scope)
    digest = payload_sha256(action)
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_connect_actions(
                id, case_id, capability, satellite, target_type, target_ref,
                payload, payload_sha256, document_hashes, risk_class,
                requires_dual_control, requested_by_operator_id, requested_at,
                contract_version, correlation_id, status, status_version,
                idempotency_key, metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:case_id AS UUID), :capability,
                :satellite, :target_type, :target_ref, CAST(:payload AS JSONB),
                :payload_sha256, CAST(:document_hashes AS JSONB), :risk_class,
                :requires_dual_control, CAST(:requested_by AS UUID),
                :requested_at, :contract_version, :correlation_id,
                'draft', 1, :idempotency_key, CAST(:metadata AS JSONB),
                NOW(), NOW()
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": action.action_id,
            "case_id": action.case_id,
            "capability": action.capability,
            "satellite": action.satellite,
            "target_type": action.target_type,
            "target_ref": action.target_ref,
            "payload": _json(dict(action.payload)),
            "payload_sha256": digest,
            "document_hashes": _json(list(action.document_hashes)),
            "risk_class": action.risk_class.value,
            "requires_dual_control": action.requires_dual_control,
            "requested_by": action.requested_by_operator_id,
            "requested_at": action.requested_at,
            "contract_version": action.contract_version,
            "correlation_id": action.correlation_id,
            "idempotency_key": key,
            "metadata": _json({"repository_version": RTM_CONNECT_C1_REPOSITORY_VERSION}),
        },
    ).first()
    if row:
        conn.execute(
            text(
                """
                INSERT INTO rtm_connect_idempotency_claims(
                    idempotency_key, action_id, payload_sha256,
                    authority_scope, claimed_at, last_seen_at,
                    replay_count, metadata
                ) VALUES (
                    :key, CAST(:action_id AS UUID), :payload_sha256,
                    :authority_scope, NOW(), NOW(), 0,
                    CAST(:metadata AS JSONB)
                )
                """
            ),
            {
                "key": key,
                "action_id": action.action_id,
                "payload_sha256": digest,
                "authority_scope": scope,
                "metadata": _json({"version": RTM_CONNECT_C1_REPOSITORY_VERSION}),
            },
        )
        _append_transition(
            conn,
            action_id=action.action_id,
            from_status=None,
            to_status=ActionStatus.DRAFT.value,
            actor_type="core",
            operator_id=action.requested_by_operator_id,
            reason_code="action_created",
            metadata={"idempotency_key": key},
        )
        return ActionCreateOutcome(action.action_id, key, digest, True, False)

    existing = conn.execute(
        text(
            """
            SELECT id, payload_sha256, capability, target_type, target_ref
            FROM rtm_connect_actions
            WHERE idempotency_key=:key
            """
        ),
        {"key": key},
    ).mappings().one()
    if str(existing["payload_sha256"]) != digest:
        raise IdempotencyConflict("La clave idempotente pertenece a otro payload")
    conn.execute(
        text(
            """
            UPDATE rtm_connect_idempotency_claims
            SET replay_count=replay_count+1, last_seen_at=NOW()
            WHERE idempotency_key=:key
            """
        ),
        {"key": key},
    )
    return ActionCreateOutcome(str(existing["id"]), key, digest, False, True)


def _load_action_contract(conn, action_id: str) -> ConnectActionRequest:
    row = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción RTM CONNECT no encontrada")
    return ConnectActionRequest(
        action_id=str(row["id"]),
        case_id=str(row["case_id"]) if row["case_id"] else None,
        capability=str(row["capability"]),
        satellite=str(row["satellite"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        payload=dict(row["payload"]),
        document_hashes=tuple(row["document_hashes"] or []),
        requested_by_operator_id=str(row["requested_by_operator_id"]),
        requested_at=row["requested_at"].isoformat(),
        risk_class=RiskClass(str(row["risk_class"])),
        correlation_id=row["correlation_id"],
        requires_dual_control=bool(row["requires_dual_control"]),
        contract_version=str(row["contract_version"]),
    )


def authorize_action(
    conn,
    *,
    grant: AuthorizationGrant,
) -> str:
    action = _load_action_contract(conn, grant.action_id)
    validate_execution_authority(
        action,
        grant,
        connector_mode=grant.authorized_connector_modes[0],
    )
    previous = conn.execute(
        text(
            """
            SELECT id, authorization_version
            FROM rtm_connect_authorizations
            WHERE action_id=CAST(:action_id AS UUID)
            ORDER BY authorization_version DESC
            LIMIT 1
            """
        ),
        {"action_id": grant.action_id},
    ).mappings().first()
    authorization_version = (
        int(previous["authorization_version"]) + 1 if previous else 1
    )
    supersedes_id = str(previous["id"]) if previous else None
    auth_id = str(grant.authorization_id)
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_authorizations(
                id, action_id, authorization_version, supersedes_id,
                authority_code, authority_version, decision, payload_sha256,
                idempotency_key, required_evidence_level,
                authorized_connector_modes, approved_by_operator_ids,
                authorized_at, expires_at, revoked_at,
                legal_effect_authorized, frozen, metadata, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                :authorization_version, CAST(:supersedes_id AS UUID),
                :authority_code, :authority_version, :decision,
                :payload_sha256, :idempotency_key, :required_evidence_level,
                CAST(:authorized_modes AS JSONB), CAST(:approvers AS JSONB),
                :authorized_at, :expires_at, :revoked_at,
                :legal_effect_authorized, TRUE, CAST(:metadata AS JSONB), NOW()
            )
            """
        ),
        {
            "id": auth_id,
            "authorization_version": authorization_version,
            "supersedes_id": supersedes_id,
            "action_id": grant.action_id,
            "authority_code": grant.authority_code,
            "authority_version": grant.authority_version,
            "decision": grant.decision,
            "payload_sha256": grant.payload_sha256,
            "idempotency_key": grant.idempotency_key,
            "required_evidence_level": grant.required_evidence_level.value,
            "authorized_modes": _json([m.value for m in grant.authorized_connector_modes]),
            "approvers": _json(list(grant.approved_by_operator_ids)),
            "authorized_at": grant.authorized_at,
            "expires_at": grant.expires_at,
            "revoked_at": grant.revoked_at,
            "legal_effect_authorized": grant.legal_effect_authorized,
            "metadata": _json({"repository_version": RTM_CONNECT_C1_REPOSITORY_VERSION}),
        },
    )
    _transition_action(
        conn,
        action_id=grant.action_id,
        target=ActionStatus.AUTHORIZED,
        actor_type="core",
        operator_id=grant.approved_by_operator_ids[0],
        reason_code="authorization_frozen",
        metadata={"authorization_id": auth_id},
    )
    return auth_id


def queue_action(conn, *, action_id: str, operator_id: str | None = None) -> bool:
    return _transition_action(
        conn,
        action_id=action_id,
        target=ActionStatus.QUEUED,
        actor_type="core",
        operator_id=operator_id,
        reason_code="action_queued",
    )


def start_attempt(
    conn,
    *,
    action_id: str,
    connector_id: str,
    request_metadata: Mapping[str, Any] | None = None,
) -> AttemptStart:
    action = conn.execute(
        text(
            """
            SELECT id, status, capability, risk_class, payload_sha256
            FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            FOR UPDATE
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not action:
        raise LookupError("Acción RTM CONNECT no encontrada")
    if str(action["status"]) != ActionStatus.QUEUED.value:
        raise ConnectKernelError("Solo una acción queued puede iniciar intento")

    authorization = conn.execute(
        text(
            """
            SELECT authorized_connector_modes
            FROM rtm_connect_authorizations
            WHERE action_id=CAST(:action_id AS UUID)
              AND revoked_at IS NULL
            ORDER BY authorization_version DESC
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not authorization:
        raise ConnectKernelError("La acción carece de autorización activa")

    connector = conn.execute(
        text(
            """
            SELECT id, mode, status, synthetic_only, capabilities,
                   risk_ceiling, supports_idempotency
            FROM rtm_connect_connectors
            WHERE id=CAST(:connector_id AS UUID)
            """
        ),
        {"connector_id": connector_id},
    ).mappings().first()
    if not connector:
        raise LookupError("Conector no encontrado")
    if str(connector["status"]) != "active" or not bool(connector["synthetic_only"]):
        raise ConnectorNotEligible("C1 solo admite conector sintético activo")
    if str(action["capability"]) not in set(connector["capabilities"] or []):
        raise ConnectorNotEligible("El conector no declara la capacidad")
    if str(connector["mode"]) not in set(authorization["authorized_connector_modes"] or []):
        raise ConnectorNotEligible("Modo de conector no autorizado")
    if _RISK_ORDER[str(action["risk_class"])] > _RISK_ORDER[str(connector["risk_ceiling"])]:
        raise ConnectorNotEligible("El riesgo excede el techo del conector")
    if not bool(connector["supports_idempotency"]):
        raise ConnectorNotEligible("C1 exige conector idempotente")

    number = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM rtm_connect_attempts
                WHERE action_id=CAST(:action_id AS UUID)
                """
            ),
            {"action_id": action_id},
        ).scalar_one()
    )
    attempt_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_attempts(
                id, action_id, connector_id, attempt_number, status,
                started_at, request_sha256, request_metadata,
                result_metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:connector_id AS UUID), :attempt_number, 'started',
                NOW(), :request_sha256, CAST(:request_metadata AS JSONB),
                '{}'::jsonb, NOW(), NOW()
            )
            """
        ),
        {
            "id": attempt_id,
            "action_id": action_id,
            "connector_id": connector_id,
            "attempt_number": number,
            "request_sha256": str(action["payload_sha256"]),
            "request_metadata": _json(dict(request_metadata or {})),
        },
    )
    conn.execute(
        text(
            """
            UPDATE rtm_connect_actions
            SET current_connector_id=CAST(:connector_id AS UUID),
                updated_at=NOW()
            WHERE id=CAST(:action_id AS UUID)
            """
        ),
        {"action_id": action_id, "connector_id": connector_id},
    )
    _transition_action(
        conn,
        action_id=action_id,
        target=ActionStatus.EXECUTING,
        actor_type="connect",
        attempt_id=attempt_id,
        reason_code="attempt_started",
    )
    return AttemptStart(attempt_id, action_id, connector_id, number)


def record_attempt_outcome(
    conn,
    *,
    attempt_id: str,
    target_status: ActionStatus,
    external_reference: str | None = None,
    failure_class: str | None = None,
    error_code: str | None = None,
    result_metadata: Mapping[str, Any] | None = None,
) -> bool:
    if target_status not in {
        ActionStatus.EXTERNAL_ACCEPTED,
        ActionStatus.RETRYABLE_FAILED,
        ActionStatus.UNKNOWN,
        ActionStatus.MANUAL_REVIEW,
        ActionStatus.PERMANENT_FAILED,
    }:
        raise ValueError("Resultado de intento no admitido en C1")
    attempt = conn.execute(
        text(
            """
            SELECT id, action_id, status
            FROM rtm_connect_attempts
            WHERE id=CAST(:attempt_id AS UUID)
            FOR UPDATE
            """
        ),
        {"attempt_id": attempt_id},
    ).mappings().first()
    if not attempt:
        raise LookupError("Intento RTM CONNECT no encontrado")
    if str(attempt["status"]) != "started":
        raise ConnectKernelError("El intento ya fue finalizado")

    attempt_status = {
        ActionStatus.EXTERNAL_ACCEPTED: "external_accepted",
        ActionStatus.RETRYABLE_FAILED: "failed",
        ActionStatus.UNKNOWN: "unknown",
        ActionStatus.MANUAL_REVIEW: "failed",
        ActionStatus.PERMANENT_FAILED: "failed",
    }[target_status]
    conn.execute(
        text(
            """
            UPDATE rtm_connect_attempts
            SET status=:status, finished_at=NOW(),
                external_reference=:external_reference,
                failure_class=:failure_class, error_code=:error_code,
                retryable=:retryable,
                reconciliation_required=:reconciliation_required,
                result_metadata=CAST(:result_metadata AS JSONB),
                updated_at=NOW()
            WHERE id=CAST(:attempt_id AS UUID)
            """
        ),
        {
            "attempt_id": attempt_id,
            "status": attempt_status,
            "external_reference": external_reference,
            "failure_class": failure_class,
            "error_code": error_code,
            "retryable": target_status is ActionStatus.RETRYABLE_FAILED,
            "reconciliation_required": target_status is ActionStatus.UNKNOWN,
            "result_metadata": _json(dict(result_metadata or {})),
        },
    )
    return _transition_action(
        conn,
        action_id=str(attempt["action_id"]),
        target=target_status,
        actor_type="connect",
        attempt_id=attempt_id,
        reason_code=f"attempt_{target_status.value}",
        external_reference=external_reference,
        metadata=result_metadata,
    )


def record_evidence(
    conn,
    *,
    action_id: str,
    attempt_id: str | None,
    evidence: EvidenceRecord,
    verified_by_operator_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    validate_evidence_record(evidence)
    conn.execute(
        text(
            """
            SELECT id FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            FOR UPDATE
            """
        ),
        {"action_id": action_id},
    ).one()
    sequence_number = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM rtm_connect_evidence
                WHERE action_id=CAST(:action_id AS UUID)
                """
            ),
            {"action_id": action_id},
        ).scalar_one()
    )
    evidence_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_evidence(
                id, action_id, attempt_id, sequence_number, evidence_level,
                request_sha256, external_reference, receipt_sha256,
                receipt_storage_ref, verified_at, verification_method,
                verified_by_operator_id, metadata, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:attempt_id AS UUID), :sequence_number,
                :evidence_level, :request_sha256, :external_reference,
                :receipt_sha256, :receipt_storage_ref, :verified_at,
                :verification_method, CAST(:verified_by AS UUID),
                CAST(:metadata AS JSONB), NOW()
            )
            """
        ),
        {
            "id": evidence_id,
            "sequence_number": sequence_number,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "evidence_level": evidence.level.value,
            "request_sha256": evidence.request_sha256,
            "external_reference": evidence.external_reference,
            "receipt_sha256": evidence.receipt_sha256,
            "receipt_storage_ref": evidence.receipt_storage_ref,
            "verified_at": evidence.verified_at,
            "verification_method": evidence.verification_method,
            "verified_by": verified_by_operator_id,
            "metadata": _json(dict(metadata or {})),
        },
    )
    current = conn.execute(
        text("SELECT status FROM rtm_connect_actions WHERE id=CAST(:id AS UUID)"),
        {"id": action_id},
    ).scalar_one()
    if str(current) == ActionStatus.EXTERNAL_ACCEPTED.value:
        _transition_action(
            conn,
            action_id=action_id,
            target=ActionStatus.EVIDENCE_PENDING,
            actor_type="connect",
            attempt_id=attempt_id,
            reason_code="evidence_recorded",
            metadata={"evidence_id": evidence_id, "level": evidence.level.value},
        )
    return evidence_id


def _load_authorization(conn, action_id: str) -> AuthorizationGrant:
    row = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_authorizations
            WHERE action_id=CAST(:action_id AS UUID)
              AND revoked_at IS NULL
            ORDER BY authorization_version DESC
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Autorización RTM CONNECT no encontrada")
    return AuthorizationGrant(
        authorization_id=str(row["id"]),
        action_id=str(row["action_id"]),
        authority_code=str(row["authority_code"]),
        authority_version=str(row["authority_version"]),
        decision=str(row["decision"]),
        payload_sha256=str(row["payload_sha256"]),
        idempotency_key=str(row["idempotency_key"]),
        required_evidence_level=EvidenceLevel(str(row["required_evidence_level"])),
        authorized_connector_modes=tuple(
            ConnectorMode(str(value)) for value in (row["authorized_connector_modes"] or [])
        ),
        approved_by_operator_ids=tuple(str(v) for v in (row["approved_by_operator_ids"] or [])),
        authorized_at=row["authorized_at"].isoformat(),
        expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
        revoked_at=row["revoked_at"].isoformat() if row["revoked_at"] else None,
        legal_effect_authorized=bool(row["legal_effect_authorized"]),
        frozen=bool(row["frozen"]),
    )


def _load_latest_evidence(conn, action_id: str) -> tuple[str, str | None, EvidenceRecord]:
    row = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_evidence
            WHERE action_id=CAST(:action_id AS UUID)
            ORDER BY sequence_number DESC
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise EvidenceGateError("La acción no tiene evidencia")
    evidence = EvidenceRecord(
        level=EvidenceLevel(str(row["evidence_level"])),
        request_sha256=row["request_sha256"],
        external_reference=row["external_reference"],
        receipt_sha256=row["receipt_sha256"],
        receipt_storage_ref=row["receipt_storage_ref"],
        verified_at=row["verified_at"].isoformat() if row["verified_at"] else None,
        verification_method=row["verification_method"],
    )
    return str(row["id"]), str(row["attempt_id"]) if row["attempt_id"] else None, evidence


def confirm_action(
    conn,
    *,
    action_id: str,
    operator_id: str | None = None,
) -> bool:
    action = _load_action_contract(conn, action_id)
    grant = _load_authorization(conn, action_id)
    evidence_id, attempt_id, evidence = _load_latest_evidence(conn, action_id)
    gate = confirmation_gate(action, grant, evidence)
    if not gate.allowed:
        raise EvidenceGateError(gate.reason)
    changed = _transition_action(
        conn,
        action_id=action_id,
        target=ActionStatus.CONFIRMED,
        actor_type="core",
        operator_id=operator_id,
        attempt_id=attempt_id,
        reason_code="evidence_confirmed",
        metadata={"evidence_id": evidence_id, "minimum": gate.minimum_required.value},
    )
    if attempt_id:
        conn.execute(
            text(
                """
                UPDATE rtm_connect_attempts
                SET status='succeeded', finished_at=COALESCE(finished_at, NOW()),
                    updated_at=NOW()
                WHERE id=CAST(:attempt_id AS UUID)
                """
            ),
            {"attempt_id": attempt_id},
        )
    return changed


def begin_reconciliation(conn, *, action_id: str) -> bool:
    return _transition_action(
        conn,
        action_id=action_id,
        target=ActionStatus.RECONCILING,
        actor_type="reconciliation",
        reason_code="reconciliation_started",
    )


def action_snapshot(conn, *, action_id: str) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT
                a.id, a.status, a.status_version, a.capability, a.risk_class,
                a.idempotency_key, a.external_reference, a.created_at,
                (SELECT COUNT(*) FROM rtm_connect_attempts x
                 WHERE x.action_id=a.id) AS attempts,
                (SELECT COUNT(*) FROM rtm_connect_evidence e
                 WHERE e.action_id=a.id) AS evidence_rows,
                (SELECT COUNT(*) FROM rtm_connect_transitions t
                 WHERE t.action_id=a.id) AS transitions,
                (SELECT replay_count FROM rtm_connect_idempotency_claims i
                 WHERE i.action_id=a.id) AS replay_count
            FROM rtm_connect_actions a
            WHERE a.id=CAST(:action_id AS UUID)
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción RTM CONNECT no encontrada")
    return dict(row)


__all__ = [
    "RTM_CONNECT_C1_REPOSITORY_VERSION",
    "ActionCreateOutcome",
    "AttemptStart",
    "ConnectKernelError",
    "ConnectorNotEligible",
    "ConnectorRegistration",
    "EvidenceGateError",
    "IdempotencyConflict",
    "action_snapshot",
    "authorize_action",
    "begin_reconciliation",
    "confirm_action",
    "create_action",
    "queue_action",
    "record_attempt_outcome",
    "record_evidence",
    "register_synthetic_connector",
    "start_attempt",
]
