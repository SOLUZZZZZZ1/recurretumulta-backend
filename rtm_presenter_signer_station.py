"""Cola y toma exclusiva para el puesto local de firma RTM Presenter.

El contrato es deliberadamente metadata-only. No abre la sede, no entrega
bytes, no accede al certificado, no comparte una sesión de navegador y no
declara una presentación. Una toma únicamente reserva durante un intervalo
una tarea sintética ya preparada y asignada al firmante.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from rtm_presenter_contracts import canonical_sha256, normalize_origin, safe_filename
from rtm_presenter_delivery import (
    RTM_PRESENTER_DELIVERY_VERSION,
    PresenterDeliveryChannel,
    PresenterDeliveryState,
)
from rtm_presenter_policy import (
    PresenterActorContext,
    PresenterRuntimeConfiguration,
    authorize_signing_claim,
    authorize_signing_queue,
    require_presenter_runtime,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterNotFound,
    PresenterSchemaNotReady,
    PresenterServiceError,
)


RTM_PRESENTER_SIGNER_STATION_VERSION = "rtm_presenter_signer_station_v1_0"
RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS = 30 * 60
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$"
)
_CLAIM_NAMESPACE = uuid.UUID("db0be224-e22e-4914-9a0e-a48c3504827a")
_RELEASE_NAMESPACE = uuid.UUID("ed4bc90c-a2bf-43a7-a83b-c3463c2a8687")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _uuid(value: Any, *, code: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PresenterConflict(
            code,
            "La tarea local contiene un identificador no verificable",
        ) from exc


def _aware(value: Any, *, code: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise PresenterConflict(
                code,
                "La tarea local contiene una fecha no verificable",
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresenterConflict(
            code,
            "La tarea local contiene una fecha sin zona horaria",
        )
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class PresenterSignerStationService:
    """Expone una cola mínima y un lease por entrega asignada."""

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

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise PresenterServiceError(
                "presenter.signer_station_clock_invalid",
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
    def _delivery_task(event: Mapping[str, Any]) -> dict[str, Any]:
        code = "presenter.signer_station_task_invalid"
        payload = _json_object(event.get("payload"))
        if (
            payload.get("delivery_contract_version")
            != RTM_PRESENTER_DELIVERY_VERSION
            or payload.get("channel") != PresenterDeliveryChannel.PORTAL.value
            or payload.get("state")
            != PresenterDeliveryState.AWAITING_SIGNATURE.value
            or payload.get("signature_queue_ready") is not True
            or payload.get("synthetic_only") is not True
            or payload.get("authoritative_submission") is not False
            or payload.get("external_effects_allowed") is not False
        ):
            raise PresenterConflict(code, "La tarea local no es verificable")

        delivery_id = _uuid(payload.get("delivery_id"), code=code)
        case_id = _uuid(payload.get("case_id"), code=code)
        package_id = _uuid(payload.get("package_id"), code=code)
        prepared_by = _uuid(payload.get("prepared_by_operator_id"), code=code)
        destination_profile_id = _uuid(
            payload.get("destination_profile_id"), code=code
        )
        package_manifest_sha256 = str(
            payload.get("package_manifest_sha256") or ""
        ).lower()
        destination_profile_sha256 = str(
            payload.get("destination_profile_sha256") or ""
        ).lower()
        destination_profile_code = str(
            payload.get("destination_profile_code") or ""
        ).strip().lower()
        destination_profile_version = payload.get("destination_profile_version")
        if (
            not _SHA256_RE.fullmatch(package_manifest_sha256)
            or not _SHA256_RE.fullmatch(destination_profile_sha256)
            or not _CODE_RE.fullmatch(destination_profile_code)
            or type(destination_profile_version) is not int
            or destination_profile_version < 1
        ):
            raise PresenterConflict(code, "El perfil de la tarea no es verificable")
        if str(event.get("case_id")) != case_id or str(event.get("package_id")) != package_id:
            raise PresenterConflict(code, "La tarea local no coincide con su ledger")

        representation_mode = str(payload.get("representation_mode") or "").strip()
        if representation_mode not in {"self", "representative"}:
            raise PresenterConflict(code, "La representación de la tarea no es válida")

        destination_name = " ".join(
            str(payload.get("destination_display_name") or "").split()
        )
        destination = _json_object(payload.get("destination"))
        try:
            portal_origin = normalize_origin(destination.get("portal_origin"))
        except ValueError as exc:
            raise PresenterConflict(code, "El origen de sede no es verificable") from exc
        if (
            not 2 <= len(destination_name) <= 240
            or destination.get("kind") != "verified_portal_origin"
        ):
            raise PresenterConflict(code, "El destino de la tarea no es verificable")

        raw_preparation = payload.get("portal_preparation")
        if not isinstance(raw_preparation, Mapping):
            raise PresenterConflict(code, "La hoja de sede no es verificable")
        form_code = str(raw_preparation.get("form_code") or "").strip().lower()
        raw_fields = raw_preparation.get("fields")
        raw_values = raw_preparation.get("values")
        if (
            not _CODE_RE.fullmatch(form_code)
            or not isinstance(raw_fields, Sequence)
            or isinstance(raw_fields, (str, bytes))
            or not 1 <= len(raw_fields) <= 32
            or not isinstance(raw_values, Mapping)
        ):
            raise PresenterConflict(code, "La hoja de sede no es verificable")
        fields: list[dict[str, Any]] = []
        field_codes: set[str] = set()
        for expected_order, raw_field in enumerate(raw_fields, start=1):
            if not isinstance(raw_field, Mapping):
                raise PresenterConflict(code, "La hoja de sede no es verificable")
            field_code = str(raw_field.get("field_code") or "").strip().lower()
            label = " ".join(str(raw_field.get("label") or "").split())
            required = raw_field.get("required")
            multiline = raw_field.get("multiline")
            max_length = raw_field.get("max_length")
            if (
                not _CODE_RE.fullmatch(field_code)
                or field_code in field_codes
                or not 2 <= len(label) <= 120
                or type(required) is not bool
                or type(multiline) is not bool
                or type(max_length) is not int
                or not 1 <= max_length <= 12000
                or raw_field.get("step_order") != expected_order
            ):
                raise PresenterConflict(code, "La hoja de sede no es verificable")
            value = raw_values.get(field_code)
            if (
                not isinstance(value, str)
                or len(value) > max_length
                or (required and not value.strip())
                or (not multiline and "\n" in value)
                or any(
                    ord(character) < 32 and character not in {"\n", "\t"}
                    for character in value
                )
            ):
                raise PresenterConflict(code, "La hoja de sede no es verificable")
            fields.append(
                {
                    "field_code": field_code,
                    "label": label,
                    "required": required,
                    "multiline": multiline,
                    "max_length": max_length,
                    "step_order": expected_order,
                    "value": value,
                }
            )
            field_codes.add(field_code)
        if set(raw_values) != field_codes:
            raise PresenterConflict(code, "La hoja de sede no es verificable")

        raw_items = payload.get("items")
        if (
            not isinstance(raw_items, Sequence)
            or isinstance(raw_items, (str, bytes))
            or not 1 <= len(raw_items) <= 32
        ):
            raise PresenterConflict(code, "Los documentos de la tarea no son verificables")
        items: list[dict[str, Any]] = []
        seen_versions: set[str] = set()
        for expected_order, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, Mapping):
                raise PresenterConflict(code, "Los documentos no son verificables")
            package_item_id = _uuid(raw_item.get("package_item_id"), code=code)
            document_version_id = _uuid(raw_item.get("document_version_id"), code=code)
            document_sha256 = str(raw_item.get("document_sha256") or "").lower()
            field_code = str(raw_item.get("field_code") or "").strip().lower()
            filename = str(raw_item.get("portal_filename") or "")
            media_type = str(raw_item.get("media_type") or "").strip().lower()
            size_bytes = raw_item.get("size_bytes")
            try:
                filename_is_safe = safe_filename(filename) == filename
            except ValueError:
                filename_is_safe = False
            if (
                raw_item.get("item_order") != expected_order
                or raw_item.get("state") != "pending"
                or document_version_id in seen_versions
                or not _SHA256_RE.fullmatch(document_sha256)
                or not _CODE_RE.fullmatch(field_code)
                or not filename_is_safe
                or not _MEDIA_TYPE_RE.fullmatch(media_type)
                or type(size_bytes) is not int
                or not 1 <= size_bytes <= 25 * 1024 * 1024
            ):
                raise PresenterConflict(code, "Los documentos no son verificables")
            items.append(
                {
                    "package_item_id": package_item_id,
                    "document_version_id": document_version_id,
                    "document_sha256": document_sha256,
                    "item_order": expected_order,
                    "field_code": field_code,
                    "portal_filename": filename,
                    "media_type": media_type,
                    "size_bytes": size_bytes,
                }
            )
            seen_versions.add(document_version_id)

        controls = payload.get("signing_controls")
        if (
            not isinstance(controls, Mapping)
            or controls.get("certificate_stored_by_rtm") is not False
            or controls.get("certificate_secret_allowed") is not False
            or controls.get("browser_session_shared_with_operator") is not False
            or controls.get("local_signer_activation_required") is not True
            or controls.get("signature_automated") is not False
            or controls.get("final_submit_automated") is not False
        ):
            raise PresenterConflict(code, "Los controles de firma no son verificables")

        prepared_at = _aware(payload.get("prepared_at"), code=code)
        task = {
            "delivery_id": delivery_id,
            "case_id": case_id,
            "package_id": package_id,
            "package_manifest_sha256": package_manifest_sha256,
            "destination_profile_id": destination_profile_id,
            "destination_profile_code": destination_profile_code,
            "destination_profile_version": destination_profile_version,
            "destination_profile_sha256": destination_profile_sha256,
            "prepared_by_operator_id": prepared_by,
            "prepared_at": _iso(prepared_at),
            "destination_display_name": destination_name,
            "portal_origin": portal_origin,
            "representation_mode": representation_mode,
            "portal_preparation": {
                "form_code": form_code,
                "fields": fields,
            },
            "items": items,
            "document_count": len(items),
        }
        task["task_fingerprint_sha256"] = canonical_sha256(task)
        return task

    @staticmethod
    def _claim_records(events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        code = "presenter.signer_station_history_invalid"
        claims: dict[str, dict[str, Any]] = {}
        for event in events:
            event_type = str(event.get("event_type") or "")
            payload = _json_object(event.get("payload"))
            if payload.get("claim_contract_version") != RTM_PRESENTER_SIGNER_STATION_VERSION:
                raise PresenterConflict(code, "El historial de toma no es verificable")
            claim_id = _uuid(payload.get("claim_id"), code=code)
            actor_id = _uuid(payload.get("signer_operator_id"), code=code)
            session_id = _uuid(payload.get("signer_session_id"), code=code)
            delivery_id = _uuid(payload.get("delivery_id"), code=code)
            if str(event.get("actor_operator_id")) != actor_id:
                raise PresenterConflict(code, "El historial de toma no es verificable")
            if event_type == "presenter.signer_station.claimed":
                claimed_at = _aware(payload.get("claimed_at"), code=code)
                expires_at = _aware(payload.get("expires_at"), code=code)
                task_fingerprint_sha256 = str(
                    payload.get("task_fingerprint_sha256") or ""
                ).lower()
                if (
                    claim_id in claims
                    or expires_at <= claimed_at
                    or payload.get("state") != "active"
                    or payload.get("certificate_stored_by_rtm") is not False
                    or payload.get("browser_opened") is not False
                    or payload.get("external_effects_executed") is not False
                    or not _SHA256_RE.fullmatch(task_fingerprint_sha256)
                ):
                    raise PresenterConflict(code, "El historial de toma no es verificable")
                claims[claim_id] = {
                    "claim_id": claim_id,
                    "delivery_id": delivery_id,
                    "task_fingerprint_sha256": task_fingerprint_sha256,
                    "signer_operator_id": actor_id,
                    "signer_session_id": session_id,
                    "claimed_at": claimed_at,
                    "expires_at": expires_at,
                    "state": "active",
                }
            elif event_type == "presenter.signer_station.released":
                claim = claims.get(claim_id)
                released_at = _aware(payload.get("released_at"), code=code)
                if (
                    claim is None
                    or claim["state"] != "active"
                    or claim["signer_operator_id"] != actor_id
                    or claim["signer_session_id"] != session_id
                    or claim["delivery_id"] != delivery_id
                    or released_at < claim["claimed_at"]
                    or payload.get("state") != "released"
                ):
                    raise PresenterConflict(code, "El historial de toma no es verificable")
                claim["state"] = "released"
                claim["released_at"] = released_at
                claim["release_command_id"] = _uuid(
                    payload.get("release_command_id"), code=code
                )
            elif event_type == "presenter.signer_station.superseded":
                claim = claims.get(claim_id)
                superseded_at = _aware(payload.get("superseded_at"), code=code)
                superseded_by_session_id = _uuid(
                    payload.get("superseded_by_session_id"), code=code
                )
                source_workspace_id = _uuid(
                    payload.get("source_workspace_id"), code=code
                )
                operator_device_id = _uuid(
                    payload.get("operator_device_id"), code=code
                )
                installation_id = _uuid(
                    payload.get("installation_id"), code=code
                )
                if (
                    claim is None
                    or claim["state"] != "active"
                    or claim["signer_operator_id"] != actor_id
                    or claim["signer_session_id"] != session_id
                    or claim["delivery_id"] != delivery_id
                    or superseded_at < claim["claimed_at"]
                    or superseded_at > claim["expires_at"]
                    or payload.get("state") != "superseded"
                    or payload.get("supersession_reason")
                    != "exact_station_workspace_recovery"
                    or superseded_by_session_id == session_id
                    or not _SHA256_RE.fullmatch(
                        str(payload.get("task_fingerprint_sha256") or "")
                    )
                    or str(payload.get("task_fingerprint_sha256") or "")
                    != claim["task_fingerprint_sha256"]
                    or payload.get("certificate_stored_by_rtm") is not False
                    or payload.get("browser_opened") is not False
                    or payload.get("external_effects_executed") is not False
                ):
                    raise PresenterConflict(code, "El historial de toma no es verificable")
                claim["state"] = "superseded"
                claim["superseded_at"] = superseded_at
                claim["superseded_by_session_id"] = superseded_by_session_id
                claim["source_workspace_id"] = source_workspace_id
                claim["operator_device_id"] = operator_device_id
                claim["installation_id"] = installation_id
                claim["task_fingerprint_sha256"] = str(
                    payload["task_fingerprint_sha256"]
                )
            else:
                raise PresenterConflict(code, "El historial de toma no es verificable")
        return claims

    @staticmethod
    def _active_claims(
        claims: Mapping[str, Mapping[str, Any]], now: datetime
    ) -> list[Mapping[str, Any]]:
        return [
            claim
            for claim in claims.values()
            if claim.get("state") == "active" and claim.get("expires_at") > now
        ]

    @staticmethod
    def _validate_active_claims(
        claims: Mapping[str, Mapping[str, Any]], now: datetime
    ) -> list[Mapping[str, Any]]:
        active = PresenterSignerStationService._active_claims(claims, now)
        if len(active) > 1:
            raise PresenterConflict(
                "presenter.signer_station_history_invalid",
                "La tarea contiene más de una toma activa",
            )
        return active

    @staticmethod
    def _queue_entry(
        task: Mapping[str, Any],
        active_claims: Sequence[Mapping[str, Any]],
        actor: PresenterActorContext,
    ) -> dict[str, Any]:
        own = next(
            (
                claim
                for claim in active_claims
                if claim["signer_operator_id"] == actor.operator_id
                and claim["signer_session_id"] == actor.operator_session_id
            ),
            None,
        )
        status = "claimed_by_you" if own else ("busy" if active_claims else "available")
        entry = {
            "delivery_id": task["delivery_id"],
            "case_id": task["case_id"],
            "package_id": task["package_id"],
            "destination_display_name": task["destination_display_name"],
            "portal_origin": task["portal_origin"],
            "representation_mode": task["representation_mode"],
            "prepared_at": task["prepared_at"],
            "document_count": task["document_count"],
            "task_fingerprint_sha256": task["task_fingerprint_sha256"],
            "claim_status": status,
            "claim_available": not active_claims,
            "local_activation_available": False,
            "certificate_stored_by_rtm": False,
            "browser_session_shared": False,
        }
        if own is not None:
            entry.update(
                {
                    "claim_id": own["claim_id"],
                    "claim_expires_at": _iso(own["expires_at"]),
                }
            )
        return entry

    @staticmethod
    def _claim_response(
        *,
        task: Mapping[str, Any],
        claim: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "claim_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
            "claim_id": claim["claim_id"],
            "state": claim["state"],
            "claimed_at": _iso(claim["claimed_at"]),
            "expires_at": _iso(claim["expires_at"]),
            "replayed": replayed,
            "task": dict(task),
            "local_activation_available": False,
            "browser_open_available": False,
            "certificate_stored_by_rtm": False,
            "certificate_secret_allowed": False,
            "browser_session_shared": False,
            "signature_automated": False,
            "final_submit_automated": False,
            "external_effects_executed": False,
            "next_action": "install_and_attest_local_signer_station",
        }

    def queue(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_signing_queue(actor)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PresenterConflict(
                "presenter.signer_station_queue_limit_invalid",
                "El límite de la cola local no es válido",
            )
        now = self._now()
        try:
            events = self.repository.list_signature_queue_events(
                conn, operator_id=actor.operator_id, limit=limit
            )
        except Exception as exc:
            raise PresenterServiceError(
                "presenter.signer_station_queue_unavailable",
                "No se pudo consultar la cola del puesto local",
                status_code=503,
            ) from exc
        entries: list[dict[str, Any]] = []
        for event in events:
            task = self._delivery_task(event)
            try:
                claim_events = self.repository.list_signature_claim_events(
                    conn,
                    case_id=task["case_id"],
                    package_id=task["package_id"],
                    delivery_id=task["delivery_id"],
                )
            except Exception as exc:
                raise PresenterServiceError(
                    "presenter.signer_station_queue_unavailable",
                    "No se pudo consultar la cola del puesto local",
                    status_code=503,
                ) from exc
            claims = self._claim_records(claim_events)
            active = self._validate_active_claims(claims, now)
            entries.append(self._queue_entry(task, active, actor))
        return {
            "station_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
            "state": PresenterDeliveryState.AWAITING_SIGNATURE.value,
            "items": entries,
            "item_count": len(entries),
            "claim_ttl_seconds": RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS,
            "local_activation_available": False,
            "certificate_stored_by_rtm": False,
            "browser_session_shared": False,
            "external_effects_executed": False,
        }

    def claim(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        delivery_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_signing_claim(actor)
        exact_delivery_id = _uuid(
            delivery_id, code="presenter.signer_station_task_invalid"
        )
        command_key = str(idempotency_key or "").strip()
        if not _IDEMPOTENCY_KEY_RE.fullmatch(command_key):
            raise PresenterConflict(
                "presenter.signer_station_idempotency_key_required",
                "La toma local exige una clave idempotente válida",
            )
        now = self._now()
        self.repository.lock_signature_claim(conn, delivery_id=exact_delivery_id)
        event = self.repository.load_signature_queue_event(
            conn,
            operator_id=actor.operator_id,
            delivery_id=exact_delivery_id,
        )
        if event is None:
            raise PresenterNotFound("Tarea de firma no encontrada")
        task = self._delivery_task(event)
        claim_events = self.repository.list_signature_claim_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
        )
        claims = self._claim_records(claim_events)
        active = self._validate_active_claims(claims, now)
        claim_id = str(
            uuid.uuid5(
                _CLAIM_NAMESPACE,
                f"{actor.operator_id}:{actor.operator_session_id}:"
                f"{exact_delivery_id}:{command_key}",
            )
        )
        existing = claims.get(claim_id)
        if existing is not None:
            if (
                existing["state"] == "active"
                and existing["expires_at"] > now
                and existing["signer_operator_id"] == actor.operator_id
                and existing["signer_session_id"] == actor.operator_session_id
            ):
                return self._claim_response(
                    task=task, claim=existing, replayed=True
                )
            raise PresenterConflict(
                "presenter.signer_station_idempotency_key_reused",
                "La clave pertenece a una toma que ya no está activa",
            )
        if active:
            raise PresenterConflict(
                "presenter.signer_station_task_busy",
                "La tarea ya está reservada por otra sesión",
            )
        expires_at = now + timedelta(
            seconds=RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS
        )
        claim = {
            "claim_id": claim_id,
            "signer_operator_id": actor.operator_id,
            "signer_session_id": actor.operator_session_id,
            "claimed_at": now,
            "expires_at": expires_at,
            "state": "active",
        }
        self.repository.append_audit(
            conn,
            event_type="presenter.signer_station.claimed",
            reason_code="exclusive_local_lease_created",
            actor=actor,
            case_id=task["case_id"],
            package_id=task["package_id"],
            payload={
                "claim_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
                "claim_id": claim_id,
                "delivery_id": task["delivery_id"],
                "task_fingerprint_sha256": task["task_fingerprint_sha256"],
                "signer_operator_id": actor.operator_id,
                "signer_session_id": actor.operator_session_id,
                "claimed_at": _iso(now),
                "expires_at": _iso(expires_at),
                "state": "active",
                "certificate_stored_by_rtm": False,
                "browser_opened": False,
                "external_effects_executed": False,
            },
        )
        return self._claim_response(task=task, claim=claim, replayed=False)

    def current_claim(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        delivery_id: str,
    ) -> dict[str, Any]:
        """Recupera únicamente la toma activa de esta misma sesión local."""

        self._open(conn)
        authorize_signing_claim(actor)
        exact_delivery_id = _uuid(
            delivery_id, code="presenter.signer_station_task_invalid"
        )
        now = self._now()
        event = self.repository.load_signature_queue_event(
            conn,
            operator_id=actor.operator_id,
            delivery_id=exact_delivery_id,
        )
        if event is None:
            raise PresenterNotFound("Tarea de firma no encontrada")
        task = self._delivery_task(event)
        claim_events = self.repository.list_signature_claim_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
        )
        claims = self._claim_records(claim_events)
        active = self._validate_active_claims(claims, now)
        own = next(
            (
                claim
                for claim in active
                if claim["signer_operator_id"] == actor.operator_id
                and claim["signer_session_id"] == actor.operator_session_id
            ),
            None,
        )
        if own is None:
            raise PresenterNotFound("Toma local activa no encontrada")
        response = self._claim_response(task=task, claim=own, replayed=True)
        response["recovered"] = True
        return response

    def release(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        delivery_id: str,
        claim_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_signing_claim(actor)
        exact_delivery_id = _uuid(
            delivery_id, code="presenter.signer_station_task_invalid"
        )
        exact_claim_id = _uuid(
            claim_id, code="presenter.signer_station_claim_invalid"
        )
        command_key = str(idempotency_key or "").strip()
        if not _IDEMPOTENCY_KEY_RE.fullmatch(command_key):
            raise PresenterConflict(
                "presenter.signer_station_idempotency_key_required",
                "La liberación exige una clave idempotente válida",
            )
        now = self._now()
        self.repository.lock_signature_claim(conn, delivery_id=exact_delivery_id)
        event = self.repository.load_signature_queue_event(
            conn,
            operator_id=actor.operator_id,
            delivery_id=exact_delivery_id,
        )
        if event is None:
            raise PresenterNotFound("Tarea de firma no encontrada")
        task = self._delivery_task(event)
        claim_events = self.repository.list_signature_claim_events(
            conn,
            case_id=task["case_id"],
            package_id=task["package_id"],
            delivery_id=task["delivery_id"],
        )
        claims = self._claim_records(claim_events)
        self._validate_active_claims(claims, now)
        claim = claims.get(exact_claim_id)
        if (
            claim is None
            or claim["signer_operator_id"] != actor.operator_id
            or claim["signer_session_id"] != actor.operator_session_id
        ):
            raise PresenterNotFound("Toma local no encontrada")
        release_command_id = str(
            uuid.uuid5(
                _RELEASE_NAMESPACE,
                f"{exact_claim_id}:{actor.operator_id}:"
                f"{actor.operator_session_id}:{command_key}",
            )
        )
        if claim["state"] == "released":
            if claim.get("release_command_id") == release_command_id:
                return {
                    "claim_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
                    "claim_id": exact_claim_id,
                    "delivery_id": exact_delivery_id,
                    "state": "released",
                    "released_at": _iso(claim["released_at"]),
                    "replayed": True,
                    "external_effects_executed": False,
                }
            raise PresenterConflict(
                "presenter.signer_station_release_already_recorded",
                "La toma ya fue liberada",
            )
        if claim["state"] != "active":
            raise PresenterConflict(
                "presenter.signer_station_claim_not_active",
                "La toma local ya no esta activa",
            )
        if claim["expires_at"] <= now:
            raise PresenterConflict(
                "presenter.signer_station_claim_expired",
                "La toma local ha caducado",
            )
        self.repository.append_audit(
            conn,
            event_type="presenter.signer_station.released",
            reason_code="exclusive_local_lease_released",
            actor=actor,
            case_id=task["case_id"],
            package_id=task["package_id"],
            payload={
                "claim_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
                "claim_id": exact_claim_id,
                "delivery_id": exact_delivery_id,
                "release_command_id": release_command_id,
                "signer_operator_id": actor.operator_id,
                "signer_session_id": actor.operator_session_id,
                "released_at": _iso(now),
                "state": "released",
                "certificate_stored_by_rtm": False,
                "browser_opened": False,
                "external_effects_executed": False,
            },
        )
        return {
            "claim_contract_version": RTM_PRESENTER_SIGNER_STATION_VERSION,
            "claim_id": exact_claim_id,
            "delivery_id": exact_delivery_id,
            "state": "released",
            "released_at": _iso(now),
            "replayed": False,
            "external_effects_executed": False,
        }


__all__ = [
    "RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS",
    "RTM_PRESENTER_SIGNER_STATION_VERSION",
    "PresenterSignerStationService",
]
