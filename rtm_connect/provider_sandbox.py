"""Orquestación transaccional del probe HTTP controlado de C6.

El módulo no publica rutas ni workers. El llamador debe abrir una transacción;
el smoke registra el conector dentro de ella y finalmente ejecuta rollback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from rtm_connect.authority import validate_execution_authority
from rtm_connect.connectors.controlled_sandbox import (
    CONTROLLED_SANDBOX_MANIFEST_SHA256,
    ControlledSandboxConnector,
    assert_controlled_sandbox_manifest_frozen,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import payload_sha256
from rtm_connect.kernel import (
    action_snapshot,
    begin_reconciliation,
    confirm_action,
    queue_action,
    record_attempt_outcome,
    record_evidence,
    record_reconciliation_outcome,
    register_synthetic_connector,
    start_attempt,
)
from rtm_connect.provider_sandbox_policy import (
    CONTROLLED_SANDBOX_CAPABILITY,
    CONTROLLED_SANDBOX_CODE,
    CONTROLLED_SANDBOX_CONNECTOR_VERSION,
    CONTROLLED_SANDBOX_CONTRACT_VERSION,
    assert_c6_database_identity,
    assert_c6_staging_boundary,
    validate_c6_probe_authority,
)
from rtm_connect.state_machine import ActionStatus, is_terminal


RTM_CONNECT_C6_PROVIDER_EXECUTION_VERSION = (
    "rtm_connect_c6_provider_execution_v1_0"
)


class ControlledSandboxExecutionError(RuntimeError):
    pass


class ControlledSandboxReplayBlocked(ControlledSandboxExecutionError):
    pass


@dataclass(frozen=True)
class ControlledSandboxExecutionOutcome:
    action_id: str
    connector_id: str
    attempt_id: str | None
    status: str
    replayed: bool
    confirmed: bool
    evidence_level: str | None
    external_reference: str | None
    attempts: int
    evidence_rows: int
    transitions: int
    replay_count: int
    network_call_performed: bool


def _register_controlled_sandbox_connector(conn):
    assert_controlled_sandbox_manifest_frozen()
    return register_synthetic_connector(
        conn,
        code=CONTROLLED_SANDBOX_CODE,
        version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
        mode=ConnectorMode.API,
        capabilities=(CONTROLLED_SANDBOX_CAPABILITY,),
        risk_ceiling=RiskClass.R1_LOW_REVERSIBLE,
        supports_reconciliation=True,
        configuration={
            "runtime_version": RTM_CONNECT_C6_PROVIDER_EXECUTION_VERSION,
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "manifest_sha256": CONTROLLED_SANDBOX_MANIFEST_SHA256,
            "network_used": True,
            "external_effects": False,
            "credential_value_persisted": False,
            "credential_reference_persisted": False,
        },
    )


def _outcome(
    conn,
    *,
    action_id: str,
    connector_id: str,
    attempt_id: str | None,
    replayed: bool,
    evidence_level: EvidenceLevel | None,
    network_call_performed: bool,
) -> ControlledSandboxExecutionOutcome:
    snapshot = action_snapshot(conn, action_id=action_id)
    return ControlledSandboxExecutionOutcome(
        action_id=str(snapshot["id"]),
        connector_id=connector_id,
        attempt_id=attempt_id,
        status=str(snapshot["status"]),
        replayed=replayed,
        confirmed=str(snapshot["status"]) == ActionStatus.CONFIRMED.value,
        evidence_level=evidence_level.value if evidence_level else None,
        external_reference=(
            str(snapshot["external_reference"])
            if snapshot.get("external_reference") else None
        ),
        attempts=int(snapshot["attempts"]),
        evidence_rows=int(snapshot["evidence_rows"]),
        transitions=int(snapshot["transitions"]),
        replay_count=int(snapshot.get("replay_count") or 0),
        network_call_performed=network_call_performed,
    )


def _assert_exact_c6_e2_scope(
    conn,
    *,
    action: ConnectActionRequest,
    attempt_id: str,
    evidence_id: str,
) -> None:
    """Vincula E2 al action/attempt exacto antes de invocar CORE."""

    row = conn.execute(
        text(
            """
            SELECT e.action_id AS evidence_action_id,
                   e.attempt_id AS evidence_attempt_id,
                   e.evidence_level, e.request_sha256 AS evidence_request_sha256,
                   e.external_reference AS evidence_external_reference,
                   x.action_id AS attempt_action_id,
                   x.request_sha256 AS attempt_request_sha256,
                   x.external_reference AS attempt_external_reference,
                   a.payload_sha256 AS action_request_sha256,
                   a.external_reference AS action_external_reference
            FROM rtm_connect_evidence e
            JOIN rtm_connect_attempts x ON x.id=e.attempt_id
            JOIN rtm_connect_actions a ON a.id=e.action_id
            WHERE e.id=CAST(:evidence_id AS UUID)
              AND e.action_id=CAST(:action_id AS UUID)
              AND e.attempt_id=CAST(:attempt_id AS UUID)
            FOR UPDATE OF a, x
            """
        ),
        {
            "evidence_id": evidence_id,
            "action_id": action.action_id,
            "attempt_id": attempt_id,
        },
    ).mappings().first()
    expected_hash = payload_sha256(action)
    expected_reference = f"c6probe-{action.action_id}"
    if not row or any(
        (
            str(row["evidence_action_id"]) != action.action_id,
            str(row["evidence_attempt_id"]) != attempt_id,
            str(row["attempt_action_id"]) != action.action_id,
            str(row["evidence_level"])
            != EvidenceLevel.E2_EXTERNAL_REFERENCE.value,
            str(row["evidence_request_sha256"]) != expected_hash,
            str(row["attempt_request_sha256"]) != expected_hash,
            str(row["action_request_sha256"]) != expected_hash,
            str(row["evidence_external_reference"]) != expected_reference,
            str(row["attempt_external_reference"]) != expected_reference,
            str(row["action_external_reference"]) != expected_reference,
        )
    ):
        raise ControlledSandboxExecutionError(
            "La E2 C6 no pertenece al alcance exacto action/attempt"
        )


def _validate_normalized_result_scope(
    action: ConnectActionRequest,
    result,
    *,
    attempt_id: str,
    operation: str,
) -> dict[str, Any]:
    """Bloquea un resultado inyectado antes de mutar cualquier ledger."""

    expected_hash = payload_sha256(action)
    expected_reference = f"c6probe-{action.action_id}"
    if (
        result.action_id != action.action_id
        or result.attempt_id != attempt_id
        or result.connector_code != CONTROLLED_SANDBOX_CODE
        or result.connector_version != CONTROLLED_SANDBOX_CONNECTOR_VERSION
        or result.evidence.request_sha256 != expected_hash
        or result.external_reference != expected_reference
    ):
        raise ControlledSandboxExecutionError(
            "Resultado C6 fuera del alcance action/hash/reference"
        )
    if result.evidence.level is EvidenceLevel.E2_EXTERNAL_REFERENCE:
        if result.evidence.external_reference != expected_reference:
            raise ControlledSandboxExecutionError(
                "La E2 C6 no contiene la referencia exacta"
            )
    elif result.evidence.level is EvidenceLevel.E1_REQUEST_RECORDED:
        if result.evidence.external_reference is not None:
            raise ControlledSandboxExecutionError(
                "E1 C6 no puede fingir referencia observada"
            )
    else:
        raise ControlledSandboxExecutionError(
            "C6 solo admite evidencia E1 o E2"
        )
    if any(
        value is not None
        for value in (
            result.evidence.receipt_sha256,
            result.evidence.receipt_storage_ref,
            result.evidence.verified_at,
            result.evidence.verification_method,
        )
    ):
        raise ControlledSandboxExecutionError(
            "C6 no admite campos de recibo E3/E4"
        )
    if operation not in {"submit", "reconcile"}:
        raise ControlledSandboxExecutionError("Operación C6 no reconocida")
    observed = result.evidence.level is EvidenceLevel.E2_EXTERNAL_REFERENCE
    if not observed:
        expected_status = "unknown"
        expected_failure = (
            "ambiguous_transport"
            if operation == "submit" else "ambiguous_reconciliation"
        )
        expected_error = (
            "c6_sandbox_result_unknown"
            if operation == "submit"
            else "c6_sandbox_reconciliation_unknown"
        )
        expected_reconciliation = True
        provider_outcome = None
    else:
        status_to_outcome = (
            {
                "external_accepted": "accepted",
                "unknown": "unknown",
                "permanent_failed": "rejected",
            }
            if operation == "submit"
            else {
                "confirmed": "accepted",
                "unknown": "unknown",
                "permanent_failed": "rejected",
            }
        )
        try:
            provider_outcome = status_to_outcome[result.status]
        except KeyError as exc:
            raise ControlledSandboxExecutionError(
                "Estado C6 incompatible con la operación y evidencia"
            ) from exc
        expected_status = result.status
        rejected = provider_outcome == "rejected"
        expected_failure = "provider_rejected" if rejected else None
        expected_error = "c6_sandbox_rejected" if rejected else None
        expected_reconciliation = provider_outcome == "unknown"
    if (
        result.status != expected_status
        or result.failure_class != expected_failure
        or result.error_code != expected_error
        or result.reconciliation_required is not expected_reconciliation
    ):
        raise ControlledSandboxExecutionError(
            "Resultado C6 contiene clasificación o flags incoherentes"
        )
    expected_metadata: dict[str, Any] = {
        "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
        "network_used": True,
        "network_call_performed": True,
        "sandbox_only": True,
        "provider_observed": observed,
    }
    if operation == "reconcile":
        expected_metadata["reconciliation_method"] = "get_only"
    if provider_outcome is not None:
        expected_metadata["provider_outcome"] = provider_outcome
    if dict(result.metadata) != expected_metadata:
        raise ControlledSandboxExecutionError(
            "Metadata C6 fuera de la allowlist exacta"
        )
    return expected_metadata


def _assert_execution_boundary(conn, connector: ControlledSandboxConnector) -> None:
    """Exige staging real y transporte loopback antes de cualquier DML C6."""

    if type(connector) is not ControlledSandboxConnector:
        raise ControlledSandboxExecutionError(
            "C6 exige el conector controlled.sandbox exacto"
        )
    connector.assert_runtime_sealed()
    boundary = assert_c6_staging_boundary()
    assert_c6_database_identity(
        conn,
        expected_database_name=boundary.database_name,
        expected_database_role=boundary.database_role,
    )
    if not connector.loopback_test_only:
        raise ControlledSandboxExecutionError(
            "C6 v1 solo puede ejecutarse dentro del smoke loopback"
        )


def _persisted_authorization(
    conn,
    *,
    action: ConnectActionRequest,
    supplied: AuthorizationGrant,
) -> AuthorizationGrant:
    """Carga y compara el grant inmutable más reciente de PostgreSQL."""

    row = conn.execute(
        text(
            """
            SELECT id, action_id, authority_code, authority_version,
                   decision, payload_sha256, idempotency_key,
                   required_evidence_level, authorized_connector_modes,
                   approved_by_operator_ids, authorized_at, expires_at,
                   revoked_at, legal_effect_authorized, frozen
            FROM rtm_connect_authorizations
            WHERE action_id=CAST(:action_id AS UUID)
            ORDER BY authorization_version DESC
            LIMIT 1
            FOR SHARE
            """
        ),
        {"action_id": action.action_id},
    ).mappings().first()
    if not row:
        raise ControlledSandboxExecutionError(
            "La acción C6 no conserva autorización persistida"
        )
    persisted = AuthorizationGrant(
        authorization_id=str(row["id"]),
        action_id=str(row["action_id"]),
        authority_code=str(row["authority_code"]),
        authority_version=str(row["authority_version"]),
        decision=str(row["decision"]),
        payload_sha256=str(row["payload_sha256"]),
        idempotency_key=str(row["idempotency_key"]),
        required_evidence_level=EvidenceLevel(
            str(row["required_evidence_level"])
        ),
        authorized_connector_modes=tuple(
            ConnectorMode(str(value))
            for value in (row["authorized_connector_modes"] or [])
        ),
        approved_by_operator_ids=tuple(
            str(value) for value in (row["approved_by_operator_ids"] or [])
        ),
        authorized_at=row["authorized_at"].isoformat(),
        expires_at=(
            row["expires_at"].isoformat() if row["expires_at"] else None
        ),
        revoked_at=(
            row["revoked_at"].isoformat() if row["revoked_at"] else None
        ),
        legal_effect_authorized=bool(row["legal_effect_authorized"]),
        frozen=bool(row["frozen"]),
    )
    if persisted != supplied:
        raise ControlledSandboxExecutionError(
            "El grant recibido no coincide exactamente con el grant persistido"
        )
    validate_execution_authority(
        action,
        persisted,
        connector_mode=ConnectorMode.API,
    )
    validate_c6_probe_authority(action, persisted)
    return persisted


def _persisted_action_contract(
    conn,
    *,
    supplied: ConnectActionRequest,
) -> tuple[ConnectActionRequest, ActionStatus]:
    """Carga la acción CORE completa y exige igualdad campo por campo."""

    row = conn.execute(
        text(
            """
            SELECT id, case_id, capability, satellite, target_type,
                   target_ref, payload, payload_sha256, document_hashes,
                   risk_class, requires_dual_control,
                   requested_by_operator_id, requested_at,
                   contract_version, correlation_id, status
            FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            FOR UPDATE
            """
        ),
        {"action_id": supplied.action_id},
    ).mappings().first()
    if row is None:
        raise ControlledSandboxExecutionError(
            "CORE debe persistir acción y autorización antes de CONNECT C6"
        )
    persisted = ConnectActionRequest(
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
    if (
        persisted != supplied
        or str(row["payload_sha256"]) != payload_sha256(persisted)
    ):
        raise ControlledSandboxExecutionError(
            "La acción C6 no coincide exactamente con la acción CORE persistida"
        )
    return persisted, ActionStatus(str(row["status"]))


def _close_c6_reconciled_attempt(
    conn,
    *,
    action_id: str,
    attempt_id: str,
    result,
) -> None:
    """Actualiza el intento original con la clasificación C6 más reciente."""

    status_map = {
        "confirmed": ("succeeded", False),
        "unknown": ("unknown", True),
        "permanent_failed": ("failed", False),
    }
    try:
        attempt_status, reconciliation_required = status_map[result.status]
    except KeyError as exc:
        raise ControlledSandboxExecutionError(
            "Resultado C6 no puede cerrar el intento reconciliado"
        ) from exc
    updated = conn.execute(
        text(
            """
            UPDATE rtm_connect_attempts
            SET status=:status,
                finished_at=CASE
                    WHEN :reconciliation_required THEN finished_at
                    ELSE COALESCE(finished_at, NOW())
                END,
                retryable=FALSE,
                reconciliation_required=:reconciliation_required,
                failure_class=:failure_class,
                error_code=:error_code,
                result_metadata=(
                    COALESCE(result_metadata, '{}'::jsonb)
                    || CAST(:metadata AS JSONB)
                ),
                updated_at=NOW()
            WHERE id=CAST(:attempt_id AS UUID)
              AND action_id=CAST(:action_id AS UUID)
            RETURNING id
            """
        ),
        {
            "status": attempt_status,
            "reconciliation_required": reconciliation_required,
            "failure_class": result.failure_class,
            "error_code": result.error_code,
            "metadata": json.dumps(
                {
                    "c6_reconciliation_status": result.status,
                    "c6_reconciliation_method": "get_only",
                    "c6_provider_observed": bool(
                        result.metadata.get("provider_observed")
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "attempt_id": attempt_id,
            "action_id": action_id,
        },
    ).scalar_one_or_none()
    if updated is None:
        raise ControlledSandboxExecutionError(
            "No se pudo cerrar el intento C6 reconciliado"
        )


def execute_controlled_sandbox_probe(
    conn,
    *,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    connector: ControlledSandboxConnector,
    operator_id: str | None = None,
) -> ControlledSandboxExecutionOutcome:
    validate_c6_probe_authority(action, grant)
    validate_execution_authority(
        action,
        grant,
        connector_mode=ConnectorMode.API,
    )
    _assert_execution_boundary(conn, connector)
    action, persisted_status = _persisted_action_contract(
        conn,
        supplied=action,
    )
    grant = _persisted_authorization(
        conn,
        action=action,
        supplied=grant,
    )
    registration = _register_controlled_sandbox_connector(conn)
    if persisted_status is not ActionStatus.AUTHORIZED:
        if not is_terminal(persisted_status):
            raise ControlledSandboxReplayBlocked(
                "La acción C6 no terminal no puede repetir el POST"
            )
        return _outcome(
            conn,
            action_id=action.action_id,
            connector_id=registration.connector_id,
            attempt_id=None,
            replayed=True,
            evidence_level=None,
            network_call_performed=False,
        )

    queue_action(conn, action_id=action.action_id, operator_id=operator_id)
    attempt = start_attempt(
        conn,
        action_id=action.action_id,
        connector_id=registration.connector_id,
        request_metadata={
            "runtime_version": RTM_CONNECT_C6_PROVIDER_EXECUTION_VERSION,
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "network_used": True,
            "sandbox_only": True,
        },
    )

    # Barrera temporal: se repite inmediatamente antes del borde de red.
    validate_execution_authority(
        action,
        grant,
        connector_mode=ConnectorMode.API,
    )
    validate_c6_probe_authority(action, grant)
    result = connector.execute_authorized(
        action,
        grant,
        attempt_id=attempt.attempt_id,
    )
    result_metadata = _validate_normalized_result_scope(
        action,
        result,
        attempt_id=attempt.attempt_id,
        operation="submit",
    )
    status_map = {
        "external_accepted": ActionStatus.EXTERNAL_ACCEPTED,
        "unknown": ActionStatus.UNKNOWN,
        "permanent_failed": ActionStatus.PERMANENT_FAILED,
    }
    try:
        target = status_map[result.status]
    except KeyError as exc:
        raise ControlledSandboxExecutionError(
            "Resultado C6 no reconocido por el kernel"
        ) from exc
    record_attempt_outcome(
        conn,
        attempt_id=attempt.attempt_id,
        target_status=target,
        external_reference=result.external_reference,
        failure_class=result.failure_class,
        error_code=result.error_code,
        result_metadata=result_metadata,
    )
    evidence_id = record_evidence(
        conn,
        action_id=action.action_id,
        attempt_id=attempt.attempt_id,
        evidence=result.evidence,
        metadata={
            "connector_code": result.connector_code,
            "connector_version": result.connector_version,
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "network_used": True,
            "network_call_performed": bool(
                result_metadata["network_call_performed"]
            ),
            "sandbox_only": True,
            "provider_observed": bool(
                result_metadata["provider_observed"]
            ),
        },
    )
    if target is ActionStatus.EXTERNAL_ACCEPTED:
        if result.evidence.level is not EvidenceLevel.E2_EXTERNAL_REFERENCE:
            raise ControlledSandboxExecutionError(
                "C6 accepted exige E2 exacta"
            )
        _assert_exact_c6_e2_scope(
            conn,
            action=action,
            attempt_id=attempt.attempt_id,
            evidence_id=evidence_id,
        )
        confirm_action(
            conn,
            action_id=action.action_id,
            operator_id=operator_id,
            evidence_id=evidence_id,
        )
    return _outcome(
        conn,
        action_id=action.action_id,
        connector_id=registration.connector_id,
        attempt_id=attempt.attempt_id,
        replayed=False,
        evidence_level=result.evidence.level,
        network_call_performed=bool(
            result_metadata["network_call_performed"]
        ),
    )


def reconcile_controlled_sandbox_probe(
    conn,
    *,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    connector: ControlledSandboxConnector,
    operator_id: str | None = None,
) -> ControlledSandboxExecutionOutcome:
    validate_c6_probe_authority(action, grant)
    validate_execution_authority(
        action,
        grant,
        connector_mode=ConnectorMode.API,
    )
    _assert_execution_boundary(conn, connector)
    action, _persisted_status = _persisted_action_contract(
        conn,
        supplied=action,
    )
    row = conn.execute(
        text(
            """
            SELECT a.status, a.payload_sha256, a.external_reference,
                   x.id AS attempt_id, x.status AS attempt_status,
                   x.request_sha256, x.reconciliation_required,
                   x.connector_id, c.code, c.version, c.synthetic_only,
                   c.supports_reconciliation
            FROM rtm_connect_actions a
            JOIN rtm_connect_attempts x ON x.action_id=a.id
            JOIN rtm_connect_connectors c ON c.id=x.connector_id
            WHERE a.id=CAST(:action_id AS UUID)
            ORDER BY x.attempt_number DESC
            LIMIT 1
            FOR UPDATE OF a, x
            """
        ),
        {"action_id": action.action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción C6 no encontrada")
    expected_reference = f"c6probe-{action.action_id}"
    if (
        str(row["status"]) != ActionStatus.UNKNOWN.value
        or str(row["attempt_status"]) != "unknown"
        or not bool(row["reconciliation_required"])
        or str(row["payload_sha256"]) != payload_sha256(action)
        or str(row["request_sha256"]) != payload_sha256(action)
        or str(row["external_reference"]) != expected_reference
        or str(row["code"]) != CONTROLLED_SANDBOX_CODE
        or str(row["version"]) != CONTROLLED_SANDBOX_CONNECTOR_VERSION
        or not bool(row["synthetic_only"])
        or not bool(row["supports_reconciliation"])
    ):
        raise ControlledSandboxExecutionError(
            "El intento UNKNOWN no coincide con el contrato C6"
        )
    attempt_id = str(row["attempt_id"])
    connector_id = str(row["connector_id"])
    grant = _persisted_authorization(
        conn,
        action=action,
        supplied=grant,
    )
    begin_reconciliation(
        conn,
        action_id=action.action_id,
        attempt_id=attempt_id,
        metadata={
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "method": "get_only",
        },
    )

    validate_execution_authority(
        action,
        grant,
        connector_mode=ConnectorMode.API,
    )
    validate_c6_probe_authority(action, grant)
    result = connector.reconcile_authorized(
        action,
        grant,
        attempt_id=attempt_id,
    )
    result_metadata = _validate_normalized_result_scope(
        action,
        result,
        attempt_id=attempt_id,
        operation="reconcile",
    )
    if result.status == "confirmed":
        target = None
    else:
        target_map = {
            "unknown": ActionStatus.UNKNOWN,
            "permanent_failed": ActionStatus.PERMANENT_FAILED,
        }
        try:
            target = target_map[result.status]
        except KeyError as exc:
            raise ControlledSandboxExecutionError(
                "Resultado de reconciliación C6 no admitido"
            ) from exc
    evidence_id = record_evidence(
        conn,
        action_id=action.action_id,
        attempt_id=attempt_id,
        evidence=result.evidence,
        metadata={
            "connector_code": result.connector_code,
            "connector_version": result.connector_version,
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "network_used": True,
            "network_call_performed": bool(
                result_metadata["network_call_performed"]
            ),
            "sandbox_only": True,
            "reconciliation_method": "get_only",
            "provider_observed": bool(
                result_metadata["provider_observed"]
            ),
        },
    )
    if result.status == "confirmed":
        if (
            result.evidence.level is not EvidenceLevel.E2_EXTERNAL_REFERENCE
            or result.evidence.external_reference != expected_reference
        ):
            raise ControlledSandboxExecutionError(
                "La reconciliación C6 no aportó E2 exacta"
            )
        _assert_exact_c6_e2_scope(
            conn,
            action=action,
            attempt_id=attempt_id,
            evidence_id=evidence_id,
        )
        confirm_action(
            conn,
            action_id=action.action_id,
            operator_id=operator_id,
            evidence_id=evidence_id,
        )
        _close_c6_reconciled_attempt(
            conn,
            action_id=action.action_id,
            attempt_id=attempt_id,
            result=result,
        )
        level = result.evidence.level
    else:
        if target is None:
            raise ControlledSandboxExecutionError(
                "Estado C6 de reconciliación incoherente"
            )
        record_reconciliation_outcome(
            conn,
            action_id=action.action_id,
            attempt_id=attempt_id,
            target_status=target,
            operator_id=operator_id,
            reason_code="c6_sandbox_reconciliation_resolved",
            metadata={
                "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
                "method": "get_only",
                "provider_observed": bool(
                    result_metadata["provider_observed"]
                ),
            },
        )
        _close_c6_reconciled_attempt(
            conn,
            action_id=action.action_id,
            attempt_id=attempt_id,
            result=result,
        )
        level = result.evidence.level
    return _outcome(
        conn,
        action_id=action.action_id,
        connector_id=connector_id,
        attempt_id=attempt_id,
        replayed=False,
        evidence_level=level,
        network_call_performed=bool(
            result_metadata["network_call_performed"]
        ),
    )


__all__ = [
    "RTM_CONNECT_C6_PROVIDER_EXECUTION_VERSION",
    "ControlledSandboxExecutionError",
    "ControlledSandboxExecutionOutcome",
    "ControlledSandboxReplayBlocked",
    "execute_controlled_sandbox_probe",
    "reconcile_controlled_sandbox_probe",
]
