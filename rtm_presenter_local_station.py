"""Candidato de puesto local y borrador recuperable de RTM Presenter.

Este corte registra solo identidad tecnica declarada y metadatos de trabajo.
No constituye atestacion gestionada, no abre REG, no entrega bytes, no accede
al certificado y no automatiza firma ni envio. La hoja y las huellas siguen en
RTM para poder reconstruir el tramite cuando la sesion temporal de REG caduca.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
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
from rtm_presenter_signer_station import (
    RTM_PRESENTER_SIGNER_STATION_VERSION,
    PresenterSignerStationService,
)


RTM_PRESENTER_LOCAL_STATION_VERSION = "rtm_presenter_local_station_v1_0"
RTM_PRESENTER_SIGNER_WORKSPACE_VERSION = "rtm_presenter_signer_workspace_v1_0"
RTM_PRESENTER_WORKSPACE_RECOVERY_VERSION = (
    "rtm_presenter_workspace_recovery_v1_0"
)

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
        previous_sequence = 0
        for position, event in enumerate(events):
            sequence_number = event.get("sequence_number")
            if (
                type(sequence_number) is not int
                or sequence_number <= previous_sequence
            ):
                raise PresenterConflict(code, "Secuencia de recuperacion no valida")
            previous_sequence = sequence_number
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
                prepared = (
                    event_type == "presenter.signer_workspace.prepared"
                    and payload.get("state") == "ready"
                    and attempt_number == 1
                )
                recovered = (
                    event_type == "presenter.signer_workspace.recovered"
                    and payload.get("state") == "ready"
                    and attempt_number >= 2
                    and payload.get("recovery_contract_version")
                    == RTM_PRESENTER_WORKSPACE_RECOVERY_VERSION
                    and payload.get("source_attempt_number")
                    == attempt_number - 1
                    and payload.get("expected_task_fingerprint_sha256")
                    == task_fingerprint_sha256
                    and payload.get("browser_storage_required") is False
                    and payload.get("cookie_material_persisted") is False
                    and payload.get("certificate_material_persisted") is False
                )
                if recovered:
                    source_workspace_id = _uuid(
                        payload.get("source_workspace_id"), code=code
                    )
                    source_claim_id = _uuid(
                        payload.get("source_claim_id"), code=code
                    )
                    recovered = (
                        source_workspace_id != workspace_id
                        and source_claim_id != claim_id
                    )
                if not prepared and not recovered:
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
            inherited_source = (
                {
                    "source_workspace_id": state["source_workspace_id"],
                    "source_claim_id": state["source_claim_id"],
                    "source_attempt_number": state["source_attempt_number"],
                }
                if state is not None and state.get("source_workspace_id")
                else {}
            )
            state = {
                "state": str(payload.get("state")),
                "attempt_number": attempt_number,
                "command_id": command_id,
                "occurred_at": occurred_at,
                "event_type": event_type,
                "sequence_number": sequence_number,
                **inherited_source,
            }
            if event_type == "presenter.signer_workspace.recovered":
                state["source_workspace_id"] = source_workspace_id
                state["source_claim_id"] = source_claim_id
                state["source_attempt_number"] = payload[
                    "source_attempt_number"
                ]
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
        response = {
            "workspace_contract_version": RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
            "workspace_id": workspace_id,
            "state": state["state"],
            "attempt_number": state["attempt_number"],
            "updated_at": _stamp(state["occurred_at"]),
            "replayed": replayed,
            "claim_id": claim["claim_id"],
            "claim_expires_at": _stamp(
                _aware(
                    claim["expires_at"],
                    code="presenter.signer_workspace_claim_invalid",
                )
            ),
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
        if state.get("source_workspace_id"):
            response.update(
                {
                    "recovery_adopted": True,
                    "recovered_from": {
                        "workspace_id": state["source_workspace_id"],
                        "claim_id": state["source_claim_id"],
                        "attempt_number": state["source_attempt_number"],
                    },
                }
            )
        else:
            response["recovery_adopted"] = False
        return response

    def _workspace_ledger(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        actor: PresenterActorContext,
        device_id: str,
        installation_id: str,
        expected_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Valida un ledger durable sin ligarlo a la sesion HTTP actual."""

        if not events:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        payload = events[0].get("payload")
        if not isinstance(payload, Mapping):
            raise PresenterConflict(
                "presenter.signer_workspace_history_invalid",
                "Historial de recuperacion no valido",
            )
        code = "presenter.signer_workspace_history_invalid"
        workspace_id = _uuid(payload.get("workspace_id"), code=code)
        if expected_workspace_id is not None and workspace_id != expected_workspace_id:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        claim_id = _uuid(payload.get("claim_id"), code=code)
        delivery_id = _uuid(payload.get("delivery_id"), code=code)
        signer_operator_id = _uuid(payload.get("signer_operator_id"), code=code)
        signer_session_id = _uuid(payload.get("signer_session_id"), code=code)
        exact_device_id = _uuid(payload.get("operator_device_id"), code=code)
        exact_installation_id = _uuid(payload.get("installation_id"), code=code)
        fingerprint = str(payload.get("task_fingerprint_sha256") or "").lower()
        if (
            signer_operator_id != actor.operator_id
            or exact_device_id != device_id
            or exact_installation_id != installation_id
            or not _SHA256_RE.fullmatch(fingerprint)
        ):
            raise PresenterConflict(code, "Historial de recuperacion fuera de actor")
        source_actor = replace(actor, operator_session_id=signer_session_id)
        state = self._workspace_history(
            events,
            actor=source_actor,
            device_id=device_id,
            installation_id=installation_id,
            claim_id=claim_id,
            delivery_id=delivery_id,
            workspace_id=workspace_id,
            task_fingerprint_sha256=fingerprint,
        )
        if state is None:
            raise PresenterNotFound("Tarea recuperable no encontrada")
        first = events[0]
        case_id = _uuid(first.get("case_id"), code=code)
        package_id = _uuid(first.get("package_id"), code=code)
        for event in events:
            if (
                _uuid(event.get("case_id"), code=code) != case_id
                or _uuid(event.get("package_id"), code=code) != package_id
            ):
                raise PresenterConflict(code, "Historial de recuperacion fuera de ledger")
        return {
            **state,
            "first_sequence_number": events[0]["sequence_number"],
            "latest_sequence_number": state["sequence_number"],
            "workspace_id": workspace_id,
            "claim_id": claim_id,
            "delivery_id": delivery_id,
            "case_id": case_id,
            "package_id": package_id,
            "signer_operator_id": signer_operator_id,
            "signer_session_id": signer_session_id,
            "operator_device_id": exact_device_id,
            "installation_id": exact_installation_id,
            "task_fingerprint_sha256": fingerprint,
        }

    def _verify_recovery_provenance(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        device_id: str,
        installation_id: str,
        ledger: Mapping[str, Any],
        lock_sources: bool,
        seen: set[str] | None = None,
    ) -> set[str]:
        """Verifica de forma recursiva que cada recovery nace de otro ledger."""

        source_workspace_id = ledger.get("source_workspace_id")
        if not source_workspace_id:
            return {ledger["signer_session_id"]}
        visited = set(seen or ())
        if len(visited) >= 64:
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_provenance_invalid",
                "La cadena de recuperacion supera el limite verificable",
            )
        if ledger["workspace_id"] in visited or source_workspace_id in visited:
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_provenance_invalid",
                "La cadena de recuperacion contiene un ciclo",
            )
        visited.add(ledger["workspace_id"])
        if lock_sources:
            self.repository.lock_signer_workspace(
                conn,
                delivery_id=ledger["delivery_id"],
                workspace_id=source_workspace_id,
            )
        events = self.repository.list_signer_workspace_events(
            conn,
            case_id=ledger["case_id"],
            package_id=ledger["package_id"],
            delivery_id=ledger["delivery_id"],
            workspace_id=source_workspace_id,
        )
        source = self._workspace_ledger(
            events,
            actor=actor,
            device_id=device_id,
            installation_id=installation_id,
            expected_workspace_id=source_workspace_id,
        )
        if (
            source["claim_id"] != ledger["source_claim_id"]
            or source["attempt_number"] != ledger["source_attempt_number"]
            or source["delivery_id"] != ledger["delivery_id"]
            or source["case_id"] != ledger["case_id"]
            or source["package_id"] != ledger["package_id"]
            or source["task_fingerprint_sha256"]
            != ledger["task_fingerprint_sha256"]
            or source["latest_sequence_number"]
            >= ledger["first_sequence_number"]
        ):
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_provenance_invalid",
                "La procedencia del borrador recuperado no es verificable",
            )
        sessions = self._verify_recovery_provenance(
            conn,
            actor=actor,
            device_id=device_id,
            installation_id=installation_id,
            ledger=source,
            lock_sources=lock_sources,
            seen=visited,
        )
        sessions.add(ledger["signer_session_id"])
        return sessions

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

    def _recovery_task(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        ledger: Mapping[str, Any],
    ) -> dict[str, Any]:
        event = self.repository.load_signature_queue_event(
            conn,
            operator_id=actor.operator_id,
            delivery_id=ledger["delivery_id"],
        )
        if event is None:
            raise PresenterNotFound("Tarea de firma no encontrada")
        task = self.signer._delivery_task(event)
        if any(
            task[key] != ledger[key]
            for key in (
                "delivery_id",
                "case_id",
                "package_id",
                "task_fingerprint_sha256",
            )
        ):
            raise PresenterConflict(
                "presenter.signer_workspace_task_changed",
                "La entrega durable ya no coincide con el borrador recuperable",
            )
        return task

    def discover_workspaces(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Descubre borradores del mismo puesto sin depender del navegador."""

        self._open(conn)
        authorize_signing_claim(actor)
        device_id = self._device_id(operator_device_id)
        exact_installation_id = _uuid(
            installation_id, code="presenter.local_station_installation_invalid"
        )
        installation_payload = self.installation(
            conn,
            actor=actor,
            operator_device_id=device_id,
            installation_id=exact_installation_id,
        )
        if type(limit) is not int or not 1 <= limit <= 50:
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_limit_invalid",
                "El limite de recuperacion no es valido",
            )
        events = self.repository.list_signer_workspace_recovery_events(
            conn,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
            installation_id=exact_installation_id,
            limit=limit,
        )
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        code = "presenter.signer_workspace_history_invalid"
        for event in events:
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise PresenterConflict(code, "Historial de recuperacion no valido")
            workspace_id = _uuid(payload.get("workspace_id"), code=code)
            grouped.setdefault(workspace_id, []).append(event)

        now = self._now()
        latest_by_delivery: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for workspace_events in grouped.values():
            ledger = self._workspace_ledger(
                workspace_events,
                actor=actor,
                device_id=device_id,
                installation_id=exact_installation_id,
            )
            try:
                task = self._recovery_task(conn, actor=actor, ledger=ledger)
            except PresenterNotFound:
                continue
            provenance_sessions = self._verify_recovery_provenance(
                conn,
                actor=actor,
                device_id=device_id,
                installation_id=exact_installation_id,
                ledger=ledger,
                lock_sources=False,
            )
            ledger["provenance_session_ids"] = provenance_sessions
            previous = latest_by_delivery.get(ledger["delivery_id"])
            if (
                previous is None
                or ledger["latest_sequence_number"]
                > previous[0]["latest_sequence_number"]
            ):
                latest_by_delivery[ledger["delivery_id"]] = (ledger, task)

        items: list[dict[str, Any]] = []
        for ledger, task in sorted(
            latest_by_delivery.values(),
            key=lambda value: value[0]["latest_sequence_number"],
            reverse=True,
        ):
            claim_events = self.repository.list_signature_claim_events(
                conn,
                case_id=task["case_id"],
                package_id=task["package_id"],
                delivery_id=task["delivery_id"],
            )
            claims = self.signer._claim_records(claim_events)
            active = self.signer._validate_active_claims(claims, now)
            status = "adoptable"
            active_expires_at: str | None = None
            if active:
                current = active[0]
                active_expires_at = _stamp(current["expires_at"])
                if (
                    current["claim_id"] == ledger["claim_id"]
                    and current["signer_operator_id"] == actor.operator_id
                    and current["signer_session_id"] == actor.operator_session_id
                ):
                    status = "current_session"
                elif (
                    current["claim_id"] == ledger["claim_id"]
                    and current["signer_operator_id"] == actor.operator_id
                    and current["signer_session_id"] == ledger["signer_session_id"]
                ):
                    status = "adoptable_supersession"
                else:
                    status = "blocked_active_claim"
            if (
                actor.operator_session_id != ledger["signer_session_id"]
                and actor.operator_session_id
                in ledger["provenance_session_ids"]
            ):
                status = "blocked_session_rollback"
            item = {
                "workspace_id": ledger["workspace_id"],
                "delivery_id": task["delivery_id"],
                "case_id": task["case_id"],
                "package_id": task["package_id"],
                "claim_id": ledger["claim_id"],
                "state": ledger["state"],
                "attempt_number": ledger["attempt_number"],
                "updated_at": _stamp(ledger["occurred_at"]),
                "destination_display_name": task["destination_display_name"],
                "document_count": task["document_count"],
                "task_fingerprint_sha256": task["task_fingerprint_sha256"],
                "recovery_status": status,
                "adoption_available": status in {
                    "adoptable",
                    "adoptable_supersession",
                },
                "rtm_draft_persisted": True,
                "reg_draft_persisted": False,
                "browser_storage_required": False,
                "document_bytes_available": False,
                "external_effects_executed": False,
            }
            if active_expires_at is not None:
                item["active_claim_expires_at"] = active_expires_at
            items.append(item)
        return {
            "recovery_contract_version": RTM_PRESENTER_WORKSPACE_RECOVERY_VERSION,
            "installation_id": installation_payload["installation"][
                "installation_id"
            ],
            "items": items,
            "item_count": len(items),
            "metadata_only": True,
            "browser_storage_required": False,
            "document_bytes_available": False,
            "cookie_material_available": False,
            "certificate_material_available": False,
            "external_effects_executed": False,
        }

    def recover_workspace(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        operator_device_id: str | None,
        installation_id: str,
        delivery_id: str,
        source_workspace_id: str,
        expected_task_fingerprint_sha256: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Reabre o sustituye de forma exacta una toma del mismo puesto."""

        key = _command_key(idempotency_key)
        self._open(conn)
        authorize_signing_claim(actor)
        device_id = self._device_id(operator_device_id)
        exact_installation_id = _uuid(
            installation_id, code="presenter.local_station_installation_invalid"
        )
        installation = self.installation(
            conn,
            actor=actor,
            operator_device_id=device_id,
            installation_id=exact_installation_id,
        )["installation"]
        exact_delivery_id = _uuid(
            delivery_id, code="presenter.signer_workspace_delivery_invalid"
        )
        exact_source_workspace_id = _uuid(
            source_workspace_id, code="presenter.signer_workspace_invalid"
        )
        expected_fingerprint = str(
            expected_task_fingerprint_sha256 or ""
        ).strip().lower()
        if not _SHA256_RE.fullmatch(expected_fingerprint):
            raise PresenterConflict(
                "presenter.signer_workspace_fingerprint_required",
                "La recuperacion exige la huella exacta de la tarea",
            )

        self.repository.lock_signature_claim(conn, delivery_id=exact_delivery_id)
        event = self.repository.load_signature_queue_event(
            conn,
            operator_id=actor.operator_id,
            delivery_id=exact_delivery_id,
        )
        if event is None:
            raise PresenterNotFound("Tarea de firma no encontrada")
        task = self.signer._delivery_task(event)
        if task["task_fingerprint_sha256"] != expected_fingerprint:
            raise PresenterConflict(
                "presenter.signer_workspace_fingerprint_mismatch",
                "La entrega durable no coincide con la huella esperada",
            )
        self.repository.lock_signer_workspace(
            conn,
            delivery_id=exact_delivery_id,
            workspace_id=exact_source_workspace_id,
        )
        source_events = self.repository.list_signer_workspace_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
            workspace_id=exact_source_workspace_id,
        )
        source = self._workspace_ledger(
            source_events,
            actor=actor,
            device_id=device_id,
            installation_id=exact_installation_id,
            expected_workspace_id=exact_source_workspace_id,
        )
        task = self._recovery_task(conn, actor=actor, ledger=source)
        provenance_sessions = self._verify_recovery_provenance(
            conn,
            actor=actor,
            device_id=device_id,
            installation_id=exact_installation_id,
            ledger=source,
            lock_sources=True,
        )
        latest_events = self.repository.list_latest_signer_delivery_workspace_events(
            conn,
            operator_id=actor.operator_id,
            operator_device_id=device_id,
            installation_id=exact_installation_id,
            delivery_id=exact_delivery_id,
        )
        latest = self._workspace_ledger(
            latest_events,
            actor=actor,
            device_id=device_id,
            installation_id=exact_installation_id,
        )
        if latest["task_fingerprint_sha256"] != expected_fingerprint:
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_source_stale",
                "El borrador indicado ya no es el ultimo intento durable",
            )
        source_is_latest = latest["workspace_id"] == source["workspace_id"]
        if source["task_fingerprint_sha256"] != expected_fingerprint:
            raise PresenterConflict(
                "presenter.signer_workspace_fingerprint_mismatch",
                "El borrador durable no coincide con la huella esperada",
            )
        if (
            actor.operator_session_id != source["signer_session_id"]
            and actor.operator_session_id in provenance_sessions
        ):
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_session_rollback",
                "Una sesion historica no puede recuperar un intento posterior",
            )

        now = self._now()
        claim_events = self.repository.list_signature_claim_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
        )
        claims = self.signer._claim_records(claim_events)
        active = self.signer._validate_active_claims(claims, now)
        if active:
            current = active[0]
            if (
                current["claim_id"] == source["claim_id"]
                and current["signer_operator_id"] == actor.operator_id
                and current["signer_session_id"] == actor.operator_session_id
            ):
                if not source_is_latest:
                    raise PresenterConflict(
                        "presenter.signer_workspace_recovery_source_stale",
                        "El borrador indicado ya no es el ultimo intento durable",
                    )
                return self._workspace_response(
                    task=task,
                    claim=current,
                    installation=installation,
                    workspace_id=source["workspace_id"],
                    state=source,
                    replayed=True,
                )
            if (
                current["signer_operator_id"] == actor.operator_id
                and current["signer_session_id"] == actor.operator_session_id
            ):
                target_workspace_id = self._workspace_id(
                    claim_id=current["claim_id"],
                    installation_id=exact_installation_id,
                    fingerprint=expected_fingerprint,
                )
                target = self._read_workspace(
                    conn,
                    actor=actor,
                    device_id=device_id,
                    installation=installation,
                    claim=current,
                    task=task,
                    workspace_id=target_workspace_id,
                )
                expected_command_id = str(
                    uuid.uuid5(
                        _COMMAND_NAMESPACE,
                        f"recover:{target_workspace_id}:"
                        f"{exact_source_workspace_id}:{key}",
                    )
                )
                if (
                    target is not None
                    and latest["workspace_id"] == target_workspace_id
                    and target.get("source_workspace_id")
                    == exact_source_workspace_id
                    and target.get("command_id") == expected_command_id
                ):
                    return self._workspace_response(
                        task=task,
                        claim=current,
                        installation=installation,
                        workspace_id=target_workspace_id,
                        state=target,
                        replayed=True,
                    )
                if not source_is_latest:
                    raise PresenterConflict(
                        "presenter.signer_workspace_recovery_source_stale",
                        "El borrador indicado ya no es el ultimo intento durable",
                    )
                raise PresenterConflict(
                    "presenter.signer_workspace_recovery_claim_active",
                    "La sesion actual ya mantiene otra toma activa",
                )
            if (
                current["claim_id"] != source["claim_id"]
                or current["signer_operator_id"] != actor.operator_id
                or current["signer_session_id"] != source["signer_session_id"]
            ):
                raise PresenterConflict(
                    "presenter.signer_workspace_recovery_claim_active",
                    "Otra sesion mantiene una toma activa",
                )
            if not source_is_latest:
                raise PresenterConflict(
                    "presenter.signer_workspace_recovery_source_stale",
                    "El borrador indicado ya no es el ultimo intento durable",
                )
            self.repository.append_audit(
                conn,
                event_type="presenter.signer_station.superseded",
                reason_code="exact_station_workspace_claim_superseded",
                actor=actor,
                case_id=task["case_id"],
                package_id=task["package_id"],
                payload={
                    "claim_contract_version": current.get(
                        "claim_contract_version",
                        RTM_PRESENTER_SIGNER_STATION_VERSION,
                    ),
                    "claim_id": current["claim_id"],
                    "delivery_id": task["delivery_id"],
                    "signer_operator_id": current["signer_operator_id"],
                    "signer_session_id": current["signer_session_id"],
                    "superseded_by_session_id": actor.operator_session_id,
                    "superseded_at": _stamp(now),
                    "supersession_reason": "exact_station_workspace_recovery",
                    "source_workspace_id": source["workspace_id"],
                    "operator_device_id": device_id,
                    "installation_id": exact_installation_id,
                    "task_fingerprint_sha256": expected_fingerprint,
                    "state": "superseded",
                    "certificate_stored_by_rtm": False,
                    "browser_opened": False,
                    "external_effects_executed": False,
                },
            )

        if not source_is_latest:
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_source_stale",
                "El borrador indicado ya no es el ultimo intento durable",
            )

        claim = self.signer.claim(
            conn,
            actor=actor,
            delivery_id=task["delivery_id"],
            idempotency_key=key,
        )
        target_workspace_id = self._workspace_id(
            claim_id=claim["claim_id"],
            installation_id=exact_installation_id,
            fingerprint=expected_fingerprint,
        )
        self.repository.lock_signer_workspace(
            conn,
            delivery_id=task["delivery_id"],
            workspace_id=target_workspace_id,
        )
        existing = self._read_workspace(
            conn,
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=target_workspace_id,
        )
        command_id = str(
            uuid.uuid5(
                _COMMAND_NAMESPACE,
                f"recover:{target_workspace_id}:{exact_source_workspace_id}:{key}",
            )
        )
        if existing is not None:
            if (
                existing.get("source_workspace_id") == exact_source_workspace_id
                and existing.get("command_id") == command_id
            ):
                return self._workspace_response(
                    task=task,
                    claim=claim,
                    installation=installation,
                    workspace_id=target_workspace_id,
                    state=existing,
                    replayed=True,
                )
            raise PresenterConflict(
                "presenter.signer_workspace_recovery_already_recorded",
                "La toma actual ya pertenece a otra recuperacion",
            )
        attempt_number = source["attempt_number"] + 1
        payload = self._workspace_payload(
            actor=actor,
            device_id=device_id,
            installation=installation,
            claim=claim,
            task=task,
            workspace_id=target_workspace_id,
            command_id=command_id,
            occurred_at=now,
            state="ready",
            attempt_number=attempt_number,
        )
        payload.update(
            {
                "recovery_contract_version": RTM_PRESENTER_WORKSPACE_RECOVERY_VERSION,
                "source_workspace_id": exact_source_workspace_id,
                "source_claim_id": source["claim_id"],
                "source_attempt_number": source["attempt_number"],
                "expected_task_fingerprint_sha256": expected_fingerprint,
                "browser_storage_required": False,
                "cookie_material_persisted": False,
                "certificate_material_persisted": False,
            }
        )
        self.repository.append_audit(
            conn,
            event_type="presenter.signer_workspace.recovered",
            reason_code="durable_snapshot_adopted_by_exact_station",
            actor=actor,
            case_id=task["case_id"],
            package_id=task["package_id"],
            payload=payload,
        )
        state = {
            "state": "ready",
            "attempt_number": attempt_number,
            "command_id": command_id,
            "occurred_at": now,
            "event_type": "presenter.signer_workspace.recovered",
            "source_workspace_id": exact_source_workspace_id,
            "source_claim_id": source["claim_id"],
            "source_attempt_number": source["attempt_number"],
        }
        return self._workspace_response(
            task=task,
            claim=claim,
            installation=installation,
            workspace_id=target_workspace_id,
            state=state,
            replayed=False,
        )

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
