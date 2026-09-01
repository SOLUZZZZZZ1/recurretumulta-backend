"""Candidato de puesto local y borrador recuperable de RTM Presenter.

Este corte registra solo identidad tecnica declarada y metadatos de trabajo.
No constituye atestacion gestionada, no abre REG, no entrega bytes, no accede
al certificado y no automatiza firma ni envio. La hoja y las huellas siguen en
RTM para poder reconstruir el tramite cuando la sesion temporal de REG caduca.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from rtm_presenter_policy import (
    PresenterActorContext,
    PresenterRuntimeConfiguration,
    authorize_signing_claim,
    require_presenter_runtime,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterNotFound,
    PresenterSchemaNotReady,
    PresenterServiceError,
)
from rtm_presenter_signer_station import PresenterSignerStationService


RTM_PRESENTER_LOCAL_STATION_VERSION = "rtm_presenter_local_station_v1_0"
RTM_PRESENTER_SIGNER_WORKSPACE_VERSION = "rtm_presenter_signer_workspace_v1_0"

_INSTALLATION_NAMESPACE = uuid.UUID("1d7687ac-b13f-4c7d-8f03-efb4e87f470e")
_WORKSPACE_NAMESPACE = uuid.UUID("2b5ad4e0-4ea1-4ae8-9fc3-4440865ebbe2")
_COMMAND_NAMESPACE = uuid.UUID("5260ee9b-c264-4d4c-8bb9-37745f0efed4")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")


def _uuid(value: Any, *, code: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PresenterConflict(code, "Identificador de puesto local no valido") from exc


def _aware(value: Any, *, code: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise PresenterConflict(code, "Fecha de puesto local no valida") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresenterConflict(code, "Fecha de puesto local sin zona")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _command_key(value: str | None) -> str:
    exact = str(value or "").strip()
    if not _IDEMPOTENCY_KEY_RE.fullmatch(exact):
        raise PresenterConflict(
            "presenter.local_station_idempotency_key_required",
            "La operacion local exige una clave idempotente valida",
        )
    return exact


class PresenterLocalStationService:
    """Registra un candidato no atestado y conserva el ciclo recuperable."""

    def __init__(
        self,
        *,
        repository: Any,
        runtime: PresenterRuntimeConfiguration,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.signer = PresenterSignerStationService(
            repository=repository,
            runtime=runtime,
            clock=self.clock,
        )

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise PresenterServiceError(
                "presenter.local_station_clock_invalid",
                "Reloj del puesto local sin zona",
                status_code=500,
            )
        return current.astimezone(timezone.utc)

    def _open(self, conn: Any) -> None:
        require_presenter_runtime(self.runtime)
        try:
            ready = self.repository.presenter_schema_ready(conn)
        except Exception:
            ready = False
        if ready is not True:
            raise PresenterSchemaNotReady()

    @staticmethod
    def _device_id(value: Any) -> str:
        if value is None:
            raise PresenterConflict(
                "presenter.local_station_device_required",
                "El puesto local exige el dispositivo ligado a la sesion",
            )
        return _uuid(value, code="presenter.local_station_device_invalid")

    @staticmethod
    def _installation(row: Mapping[str, Any]) -> dict[str, Any]:
        code = "presenter.local_station_installation_invalid"
        installation = {
            "installation_id": _uuid(row.get("id"), code=code),
            "operator_id": _uuid(row.get("operator_id"), code=code),
            "operator_device_id": _uuid(
                row.get("operator_device_id"), code=code
            ),
            "client_instance_id": _uuid(row.get("client_instance_id"), code=code),
            "client_binding_sha256": str(
                row.get("client_binding_sha256") or ""
            ).lower(),
            "station_label": " ".join(str(row.get("station_label") or "").split()),
            "platform": str(row.get("platform") or "").strip().lower(),
            "client_version": str(row.get("client_version") or "").strip(),
            "status": str(row.get("status") or "").strip().lower(),
            "registered_at": _stamp(_aware(row.get("registered_at"), code=code)),
        }
        if (
            not _SHA256_RE.fullmatch(installation["client_binding_sha256"])
            or not 3 <= len(installation["station_label"]) <= 80
            or installation["platform"] != "windows"
            or not _VERSION_RE.fullmatch(installation["client_version"])
            or len(installation["client_version"]) > 48
            or installation["status"] != "candidate"
        ):
            raise PresenterConflict(code, "Candidato de puesto local no verificable")
        return installation

    @staticmethod
    def _installation_response(
        installation: Mapping[str, Any], *, replayed: bool
    ) -> dict[str, Any]:
        return {
            "station_contract_version": RTM_PRESENTER_LOCAL_STATION_VERSION,
            "installation": dict(installation),
            "replayed": replayed,
            "candidate_registered": True,
            "managed_attestation_verified": False,
            "local_activation_available": False,
            "browser_open_available": False,
            "document_bytes_available": False,
            "certificate_stored_by_rtm": False,
            "certificate_secret_allowed": False,
            "signature_automated": False,
            "final_submit_automated": False,
            "external_effects_executed": False,
            "next_action": "independent_managed_station_attestation_required",
        }

    def register_candidate(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        client_instance_id: str,
        client_binding_sha256: str,
        station_label: str,
        platform: str,
        client_version: str,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_signing_claim(actor)
        device_id = self._device_id(operator_device_id)
        instance_id = _uuid(
            client_instance_id, code="presenter.local_station_instance_invalid"
        )
        binding = str(client_binding_sha256 or "").strip().lower()
        label = " ".join(str(station_label or "").split())
        exact_platform = str(platform or "").strip().lower()
        version = str(client_version or "").strip()
        if not _SHA256_RE.fullmatch(binding):
            raise PresenterConflict(
                "presenter.local_station_binding_invalid",
                "La huella declarada del cliente local no es valida",
            )
        if not 3 <= len(label) <= 80 or any(ord(char) < 32 for char in label):
            raise PresenterConflict(
                "presenter.local_station_label_invalid",
                "El nombre del puesto local no es valido",
            )
        if exact_platform != "windows":
            raise PresenterConflict(
                "presenter.local_station_platform_invalid",
                "Este corte solo admite el puesto Windows controlado",
            )
        if not _VERSION_RE.fullmatch(version) or len(version) > 48:
            raise PresenterConflict(
                "presenter.local_station_version_invalid",
                "La version del cliente local no es valida",
            )

        self.repository.lock_signer_installation(
            conn,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
            client_instance_id=instance_id,
            client_binding_sha256=binding,
        )
        existing = self.repository.load_signer_installation_by_instance(
            conn,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
            client_instance_id=instance_id,
        )
        if existing is not None:
            installation = self._installation(existing)
            expected = {
                "client_binding_sha256": binding,
                "station_label": label,
                "platform": exact_platform,
                "client_version": version,
            }
            if any(installation[key] != value for key, value in expected.items()):
                raise PresenterConflict(
                    "presenter.local_station_instance_reused",
                    "La instancia local ya pertenece a otro contrato",
                )
            return self._installation_response(installation, replayed=True)

        collision = self.repository.load_signer_installation_by_binding(
            conn, client_binding_sha256=binding
        )
        if collision is not None:
            raise PresenterConflict(
                "presenter.local_station_binding_reused",
                "La huella declarada ya pertenece a otra instalacion",
            )

        installation_id = str(
            uuid.uuid5(
                _INSTALLATION_NAMESPACE,
                f"{actor.operator_id}:{device_id}:{instance_id}",
            )
        )
        registered_at = self._now()
        row = self.repository.insert_signer_installation(
            conn,
            installation_id=installation_id,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
            client_instance_id=instance_id,
            client_binding_sha256=binding,
            station_label=label,
            platform=exact_platform,
            client_version=version,
            registered_at=registered_at,
            metadata={
                "contract_version": RTM_PRESENTER_LOCAL_STATION_VERSION,
                "synthetic_only": True,
                "managed_attestation_verified": False,
                "external_effects_allowed": False,
                "document_bytes_allowed": False,
                "certificate_access_allowed": False,
                "portal_open_allowed": False,
            },
        )
        return self._installation_response(self._installation(row), replayed=False)

    def installation(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_signing_claim(actor)
        device_id = self._device_id(operator_device_id)
        exact_id = _uuid(
            installation_id, code="presenter.local_station_installation_invalid"
        )
        row = self.repository.load_signer_installation(
            conn,
            installation_id=exact_id,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
        )
        if row is None:
            raise PresenterNotFound("Puesto local candidato no encontrado")
        return self._installation_response(self._installation(row), replayed=True)

    @staticmethod
    def _workspace_id(*, claim_id: str, installation_id: str, fingerprint: str) -> str:
        return str(
            uuid.uuid5(
                _WORKSPACE_NAMESPACE,
                f"{claim_id}:{installation_id}:{fingerprint}",
            )
        )

    @staticmethod
    def _workspace_history(
        events: Sequence[Mapping[str, Any]],
        *,
        actor: PresenterActorContext,
        device_id: str,
        installation_id: str,
        claim_id: str,
        delivery_id: str,
        workspace_id: str,
        task_fingerprint_sha256: str,
    ) -> dict[str, Any] | None:
        if not events:
            return None
        code = "presenter.signer_workspace_history_invalid"
        state: dict[str, Any] | None = None
        for position, event in enumerate(events):
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise PresenterConflict(code, "Historial de recuperacion no valido")
            event_type = str(event.get("event_type") or "")
            fixed = {
                "workspace_contract_version": RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
                "workspace_id": workspace_id,
                "delivery_id": delivery_id,
                "claim_id": claim_id,
                "signer_operator_id": actor.operator_id,
                "signer_session_id": actor.operator_session_id,
                "operator_device_id": device_id,
                "installation_id": installation_id,
                "task_fingerprint_sha256": task_fingerprint_sha256,
                "rtm_draft_persisted": True,
                "reg_draft_persisted": False,
                "portal_opened": False,
                "document_bytes_delivered": False,
                "external_effects_executed": False,
            }
            if any(payload.get(key) != value for key, value in fixed.items()):
                raise PresenterConflict(code, "Historial de recuperacion no valido")
            if str(event.get("actor_operator_id")) != actor.operator_id:
                raise PresenterConflict(code, "Historial de recuperacion fuera de actor")
            command_id = _uuid(payload.get("command_id"), code=code)
            occurred_at = _aware(payload.get("occurred_at"), code=code)
            attempt_number = payload.get("attempt_number")
            if type(attempt_number) is not int or attempt_number < 1:
                raise PresenterConflict(code, "Intento de recuperacion no valido")
            if position == 0:
                if (
                    event_type != "presenter.signer_workspace.prepared"
                    or payload.get("state") != "ready"
                    or attempt_number != 1
                ):
                    raise PresenterConflict(code, "Inicio de recuperacion no valido")
            else:
                assert state is not None
                if occurred_at < state["occurred_at"]:
                    raise PresenterConflict(code, "Cronologia de recuperacion no valida")
                if event_type == "presenter.signer_workspace.portal_session_expired":
                    if (
                        state["state"] != "ready"
                        or payload.get("state") != "reg_session_expired"
                        or attempt_number != state["attempt_number"]
                    ):
                        raise PresenterConflict(code, "Caducidad REG no valida")
                elif event_type == "presenter.signer_workspace.resumed":
                    if (
                        state["state"] != "reg_session_expired"
                        or payload.get("state") != "ready"
                        or attempt_number != state["attempt_number"] + 1
                    ):
                        raise PresenterConflict(code, "Reanudacion REG no valida")
                else:
                    raise PresenterConflict(code, "Evento de recuperacion no admitido")
            state = {
                "state": str(payload.get("state")),
                "attempt_number": attempt_number,
                "command_id": command_id,
                "occurred_at": occurred_at,
                "event_type": event_type,
            }
        return state

    @staticmethod
    def _workspace_response(
        *,
        task: Mapping[str, Any],
        claim: Mapping[str, Any],
        installation: Mapping[str, Any],
        workspace_id: str,
        state: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        expired = state["state"] == "reg_session_expired"
        return {
            "workspace_contract_version": RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
            "workspace_id": workspace_id,
            "state": state["state"],
            "attempt_number": state["attempt_number"],
            "updated_at": _stamp(state["occurred_at"]),
            "replayed": replayed,
            "claim_id": claim["claim_id"],
            "claim_expires_at": claim["expires_at"],
            "installation": dict(installation),
            "task": dict(task),
            "rtm_draft_persisted": True,
            "reg_draft_persisted": False,
            "reg_session_recovery_available": True,
            "reg_session_expired": expired,
            "managed_attestation_verified": False,
            "local_activation_available": False,
            "browser_open_available": False,
            "document_bytes_available": False,
            "certificate_stored_by_rtm": False,
            "signature_automated": False,
            "final_submit_automated": False,
            "external_effects_executed": False,
            "next_action": (
                "reauthenticate_reg_then_resume_from_rtm"
                if expired
                else "authenticate_reg_manually_when_local_bridge_is_authorized"
            ),
        }

    def _bound_context(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        delivery_id: str,
        claim_id: str,
    ) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], str]:
        self._open(conn)
        authorize_signing_claim(actor)
        device_id = self._device_id(operator_device_id)
        installation_payload = self.installation(
            conn,
            actor=actor,
            operator_device_id=device_id,
            installation_id=installation_id,
        )
        installation = installation_payload["installation"]
        exact_delivery_id = _uuid(
            delivery_id, code="presenter.signer_workspace_delivery_invalid"
        )
        self.repository.lock_signature_claim(
            conn,
            delivery_id=exact_delivery_id,
        )
        exact_claim_id = _uuid(
            claim_id, code="presenter.signer_workspace_claim_invalid"
        )
        claim = self.signer.current_claim(
            conn,
            actor=actor,
            delivery_id=exact_delivery_id,
        )
        if claim.get("claim_id") != exact_claim_id:
            raise PresenterConflict(
                "presenter.signer_workspace_claim_mismatch",
                "La tarea recuperable no coincide con la toma activa",
            )
        task = claim.get("task")
        if not isinstance(task, Mapping):
            raise PresenterConflict(
                "presenter.signer_workspace_task_invalid",
                "La tarea recuperable no es verificable",
            )
        fingerprint = str(task.get("task_fingerprint_sha256") or "").lower()
        if not _SHA256_RE.fullmatch(fingerprint):
            raise PresenterConflict(
                "presenter.signer_workspace_task_invalid",
                "La huella de la tarea recuperable no es valida",
            )
        workspace_id = self._workspace_id(
            claim_id=exact_claim_id,
            installation_id=installation["installation_id"],
            fingerprint=fingerprint,
        )
        return device_id, installation, claim, dict(task), workspace_id

    def _read_workspace(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        device_id: str,
        installation: Mapping[str, Any],
        claim: Mapping[str, Any],
        task: Mapping[str, Any],
        workspace_id: str,
    ) -> dict[str, Any] | None:
        events = self.repository.list_signer_workspace_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
            workspace_id=workspace_id,
        )
        return self._workspace_history(
            events,
            actor=actor,
            device_id=device_id,
            installation_id=installation["installation_id"],
            claim_id=claim["claim_id"],
            delivery_id=task["delivery_id"],
            workspace_id=workspace_id,
            task_fingerprint_sha256=task["task_fingerprint_sha256"],
        )

    def prepare_workspace(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        delivery_id: str,
        claim_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        key = _command_key(idempotency_key)
        device_id, installation, claim, task, workspace_id = self._bound_context(
            conn,
            actor=actor,
            operator_device_id=operator_device_id,
            installation_id=installation_id,
            delivery_id=delivery_id,
            claim_id=claim_id,
        )
        self.repository.lock_signer_workspace(
            conn, delivery_id=task["delivery_id"], workspace_id=workspace_id
        )
        current = self._read_workspace(
            conn,
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=workspace_id,
        )
        if current is not None:
            return self._workspace_response(
                task=task,
                claim=claim,
                installation=installation,
                workspace_id=workspace_id,
                state=current,
                replayed=True,
            )
        now = self._now()
        command_id = str(uuid.uuid5(_COMMAND_NAMESPACE, f"prepare:{workspace_id}:{key}"))
        payload = self._workspace_payload(
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=workspace_id,
            command_id=command_id,
            occurred_at=now,
            state="ready",
            attempt_number=1,
        )
        self.repository.append_audit(
            conn,
            event_type="presenter.signer_workspace.prepared",
            reason_code="rtm_draft_snapshot_ready_no_portal_effect",
            actor=actor,
            case_id=task["case_id"],
            package_id=task["package_id"],
            payload=payload,
        )
        state = {
            "state": "ready",
            "attempt_number": 1,
            "command_id": command_id,
            "occurred_at": now,
            "event_type": "presenter.signer_workspace.prepared",
        }
        return self._workspace_response(
            task=task,
            claim=claim,
            installation=installation,
            workspace_id=workspace_id,
            state=state,
            replayed=False,
        )

    @staticmethod
    def _workspace_payload(
        *,
        actor: PresenterActorContext,
        device_id: str,
        installation: Mapping[str, Any],
        claim: Mapping[str, Any],
        task: Mapping[str, Any],
        workspace_id: str,
        command_id: str,
        occurred_at: datetime,
        state: str,
        attempt_number: int,
    ) -> dict[str, Any]:
        return {
            "workspace_contract_version": RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
            "workspace_id": workspace_id,
            "delivery_id": task["delivery_id"],
            "claim_id": claim["claim_id"],
            "signer_operator_id": actor.operator_id,
            "signer_session_id": actor.operator_session_id,
            "operator_device_id": device_id,
            "installation_id": installation["installation_id"],
            "task_fingerprint_sha256": task["task_fingerprint_sha256"],
            "command_id": command_id,
            "occurred_at": _stamp(occurred_at),
            "state": state,
            "attempt_number": attempt_number,
            "rtm_draft_persisted": True,
            "reg_draft_persisted": False,
            "portal_opened": False,
            "document_bytes_delivered": False,
            "external_effects_executed": False,
        }

    def current_workspace(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        delivery_id: str,
        claim_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        device_id, installation, claim, task, expected_workspace = self._bound_context(
            conn,
            actor=actor,
            operator_device_id=operator_device_id,
            installation_id=installation_id,
            delivery_id=delivery_id,
            claim_id=claim_id,
        )
        exact_workspace = _uuid(
            workspace_id, code="presenter.signer_workspace_invalid"
        )
        if exact_workspace != expected_workspace:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        current = self._read_workspace(
            conn,
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=exact_workspace,
        )
        if current is None:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        return self._workspace_response(
            task=task,
            claim=claim,
            installation=installation,
            workspace_id=exact_workspace,
            state=current,
            replayed=True,
        )

    def transition_workspace(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        delivery_id: str,
        claim_id: str,
        workspace_id: str,
        action: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        key = _command_key(idempotency_key)
        if action not in {"portal_session_expired", "resume"}:
            raise PresenterConflict(
                "presenter.signer_workspace_action_invalid",
                "Transicion de recuperacion no admitida",
            )
        device_id, installation, claim, task, expected_workspace = self._bound_context(
            conn,
            actor=actor,
            operator_device_id=operator_device_id,
            installation_id=installation_id,
            delivery_id=delivery_id,
            claim_id=claim_id,
        )
        exact_workspace = _uuid(
            workspace_id, code="presenter.signer_workspace_invalid"
        )
        if exact_workspace != expected_workspace:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        self.repository.lock_signer_workspace(
            conn, delivery_id=task["delivery_id"], workspace_id=exact_workspace
        )
        current = self._read_workspace(
            conn,
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=exact_workspace,
        )
        if current is None:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        target_state = (
            "reg_session_expired"
            if action == "portal_session_expired"
            else "ready"
        )
        event_type = (
            "presenter.signer_workspace.portal_session_expired"
            if action == "portal_session_expired"
            else "presenter.signer_workspace.resumed"
        )
        if current["state"] == target_state:
            replay_command_id = str(
                uuid.uuid5(
                    _COMMAND_NAMESPACE,
                    f"{action}:{exact_workspace}:"
                    f"{current['attempt_number']}:{key}",
                )
            )
            if (
                current["event_type"] == event_type
                and current["command_id"] == replay_command_id
            ):
                return self._workspace_response(
                    task=task,
                    claim=claim,
                    installation=installation,
                    workspace_id=exact_workspace,
                    state=current,
                    replayed=True,
                )
            raise PresenterConflict(
                "presenter.signer_workspace_transition_invalid",
                "La tarea no admite esa transicion de recuperacion",
            )
        required_state = "ready" if action == "portal_session_expired" else "reg_session_expired"
        if current["state"] != required_state:
            raise PresenterConflict(
                "presenter.signer_workspace_transition_invalid",
                "La tarea no admite esa transicion de recuperacion",
            )
        attempt_number = current["attempt_number"] + (1 if action == "resume" else 0)
        now = self._now()
        command_id = str(
            uuid.uuid5(
                _COMMAND_NAMESPACE,
                f"{action}:{exact_workspace}:{attempt_number}:{key}",
            )
        )
        payload = self._workspace_payload(
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=exact_workspace,
            command_id=command_id,
            occurred_at=now,
            state=target_state,
            attempt_number=attempt_number,
        )
        self.repository.append_audit(
            conn,
            event_type=event_type,
            reason_code=(
                "reg_inactivity_timeout_draft_retained_by_rtm"
                if action == "portal_session_expired"
                else "reg_reauthentication_requested_from_rtm_snapshot"
            ),
            actor=actor,
            case_id=task["case_id"],
            package_id=task["package_id"],
            payload=payload,
        )
        state = {
            "state": target_state,
            "attempt_number": attempt_number,
            "command_id": command_id,
            "occurred_at": now,
            "event_type": event_type,
        }
        return self._workspace_response(
            task=task,
            claim=claim,
            installation=installation,
            workspace_id=exact_workspace,
            state=state,
            replayed=False,
        )


__all__ = [
    "RTM_PRESENTER_LOCAL_STATION_VERSION",
    "RTM_PRESENTER_SIGNER_WORKSPACE_VERSION",
    "PresenterLocalStationService",
]
