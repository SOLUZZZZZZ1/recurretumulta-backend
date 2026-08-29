"""Sesiones sintéticas de sede con documentos individuales desde OPS.

Este corte no crea paquetes, ZIP ni descargas para el operador. Cada intención
vincula exactamente una versión documental a un campo de una sede verificada.
El canal remoto de extensión sigue cerrado hasta que exista atestación
gestionada real; un header autodeclarado nunca es prueba suficiente.

Preparar una sesión o registrar un adjunto sintético no equivale a presentar.
Capturar el fichero que la sede ofrece para descargar solo lo deja en
``receipt_pending`` dentro de la custodia; tampoco acredita el envío.
``sent_at`` solo nace al verificar un justificante sintético inmutable que
incluye número de registro y fecha/hora. En ese momento se emite un evento
explícito para el motor de seguimiento operativo, sin inventar un plazo legal.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from rtm_presenter_contracts import (
    PresenterClientKind,
    PresenterDocumentState,
    canonical_sha256,
    normalize_origin,
    safe_filename,
)
from rtm_presenter_policy import (
    PresenterActorContext,
    PresenterRuntimeConfiguration,
    authorize_delivery_prepare,
    authorize_handoff_exchange_client,
    authorize_receipt_verification,
    require_presenter_runtime,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterForbidden,
    PresenterNotFound,
    PresenterSchemaNotReady,
    PresenterServiceError,
    PresenterExternalDocumentUpload,
    _destination_workspace_projection,
    _document_from_row,
    _requirements_fields,
    _validate_selection_against_field,
    validate_external_document_upload,
)


RTM_PRESENTER_PORTAL_SESSION_VERSION = "rtm_presenter_portal_session_v1_0"
RTM_PRESENTER_RECEIPT_CAPTURE_VERSION = "rtm_presenter_receipt_capture_v1_0"
RTM_PRESENTER_DEADLINE_SOURCE_EVENT = "presenter_followup_activation_ready"

_SESSION_NAMESPACE = uuid.UUID("c00d4b93-cc36-41ae-8836-24d15d312f46")
_INTENT_NAMESPACE = uuid.UUID("e39767ac-137d-4d7c-919c-11cc7a50da36")
_RECEIPT_CAPTURE_NAMESPACE = uuid.UUID("f4ac9f2e-0451-431c-b47b-405f53fc65a8")
_SESSION_TTL = timedelta(hours=2)
_INTENT_TTL = timedelta(minutes=5)
_RECEIPT_CAPTURE_TTL = timedelta(hours=24)
_CLOCK_SKEW = timedelta(minutes=5)
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FIELD_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_REGISTRATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./_:\-]{2,119}$")
_RECEIPT_FORMAT = "rtm.presenter.synthetic_submission_receipt.v1"
_RECEIPT_METADATA_KEY = "synthetic_submission_receipt"
# El fixture declara SHA-256 y alcance attachment_manifest. Un adaptador real
# debe declarar y verificar algoritmo, canonicalización y alcance; una cadena
# hexadecimal de 64 caracteres no basta para inferirlos.
_RECEIPT_MATERIAL_KEYS = frozenset(
    {
        "format",
        "case_id",
        "portal_session_id",
        "destination_profile_id",
        "portal_origin",
        "registration_number",
        "submitted_at",
        "verification_reference",
        "receipt_document_version_id",
        "receipt_sha256",
        "receipt_capture_id",
        "captured_document_version_id",
        "captured_document_sha256",
        "attachment_manifest_sha256",
        "authority_hash_algorithm",
        "authority_hash_scope",
        "authority_hash_value",
        "synthetic_only",
        "legal_submission_executed",
    }
)


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


def _aware(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise PresenterConflict(
                "presenter.portal_timestamp_invalid",
                f"{name} no es verificable",
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresenterConflict(
            "presenter.portal_timestamp_invalid", f"{name} exige zona horaria"
        )
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(value: Any, message: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PresenterNotFound(message) from exc


def _sha256(value: Any, code: str, message: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise PresenterConflict(code, message)
    return normalized


def _idempotency_key(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not _IDEMPOTENCY_KEY_RE.fullmatch(normalized):
        raise PresenterConflict(
            "presenter.portal_idempotency_key_required",
            "La operación exige una clave idempotente válida",
        )
    return normalized


def _clean_reference(value: Any, name: str) -> str:
    normalized = " ".join(str(value or "").split())
    if not _REGISTRATION_RE.fullmatch(normalized):
        raise PresenterConflict(
            "presenter.receipt_reference_invalid", f"{name} no es verificable"
        )
    return normalized


def _portal_adapter_binding(field: Mapping[str, Any]) -> dict[str, Any]:
    adapter = _json_object(field.get("portal_adapter"))
    if set(adapter) != {
        "adapter_id",
        "adapter_version",
        "adapter_sha256",
        "input_selector",
        "input_fingerprint_sha256",
    }:
        raise PresenterConflict(
            "presenter.portal_adapter_unverified",
            "El campo no tiene un adaptador de sede cerrado y verificado",
        )
    adapter_id = str(adapter.get("adapter_id") or "").strip().lower()
    adapter_version = adapter.get("adapter_version")
    adapter_sha256 = str(adapter.get("adapter_sha256") or "").strip().lower()
    input_selector = str(adapter.get("input_selector") or "").strip()
    input_fingerprint = str(
        adapter.get("input_fingerprint_sha256") or ""
    ).strip().lower()
    if (
        not _FIELD_CODE_RE.fullmatch(adapter_id)
        or type(adapter_version) is not int
        or adapter_version < 1
        or not _SHA256_RE.fullmatch(adapter_sha256)
        or not 1 <= len(input_selector) <= 500
        or any(ord(character) < 32 for character in input_selector)
        or not _SHA256_RE.fullmatch(input_fingerprint)
    ):
        raise PresenterConflict(
            "presenter.portal_adapter_unverified",
            "El adaptador del campo no supera el contrato cerrado",
        )
    return {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "adapter_sha256": adapter_sha256,
        "input_selector": input_selector,
        "input_fingerprint_sha256": input_fingerprint,
    }


def _attachment_manifest(
    attachments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    items = sorted(
        (
            {
                "attachment_intent_id": str(item["attachment_intent_id"]),
                "field_code": str(item["field_code"]),
                "portal_field_fingerprint_sha256": str(
                    item["portal_field_fingerprint_sha256"]
                ),
                "document_version_id": str(item["document_version_id"]),
                "document_version": int(item["document_version"]),
                "document_sha256": str(item["document_sha256"]),
            }
            for item in attachments
        ),
        key=lambda item: item["attachment_intent_id"],
    )
    return items, canonical_sha256({"items": items})


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    return _json_object(event.get("payload"))


def _events_of_type(
    events: Sequence[Mapping[str, Any]], event_type: str
) -> list[dict[str, Any]]:
    return [_payload(event) for event in events if event.get("event_type") == event_type]


class PresenterPortalSessionService:
    """Orquesta sede-campo-documento sobre el ledger existente de Presenter."""

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
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise PresenterServiceError(
                "presenter.portal_clock_invalid",
                "Reloj de Presenter sin zona",
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

    def _authorize_case_scope(
        self, conn: Any, *, actor: PresenterActorContext, case_id: str
    ) -> None:
        try:
            allowed = self.repository.has_active_synthetic_case_access(
                conn, case_id=case_id, operator_id=actor.operator_id
            )
        except Exception:
            allowed = False
        if allowed is not True:
            raise PresenterForbidden()

    def _events(
        self, conn: Any, *, case_id: str, portal_session_id: str
    ) -> Sequence[Mapping[str, Any]]:
        return self.repository.list_portal_session_events(
            conn,
            case_id=case_id,
            portal_session_id=portal_session_id,
        )

    @staticmethod
    def _opened(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        opened = _events_of_type(events, "presenter.portal_session.opened")
        if len(opened) != 1:
            raise PresenterNotFound("Sesión de sede no encontrada")
        snapshot = opened[0]
        if (
            snapshot.get("portal_session_contract_version")
            != RTM_PRESENTER_PORTAL_SESSION_VERSION
        ):
            raise PresenterConflict(
                "presenter.portal_history_invalid",
                "El historial de la sesión no es verificable",
            )
        return snapshot

    @staticmethod
    def _assert_actor_binding(
        opened: Mapping[str, Any], actor: PresenterActorContext
    ) -> None:
        if (
            opened.get("operator_id") != actor.operator_id
            or opened.get("operator_session_id") != actor.operator_session_id
        ):
            raise PresenterForbidden()

    def _assert_open(
        self,
        opened: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        *,
        now: datetime,
    ) -> None:
        if _events_of_type(events, "presenter.portal_session.receipt_verified"):
            raise PresenterConflict(
                "presenter.portal_session_closed",
                "La sesión ya quedó cerrada por justificante verificado",
            )
        if _aware(opened.get("expires_at"), "expires_at") <= now:
            raise PresenterConflict(
                "presenter.portal_session_expired", "La sesión de sede ha caducado"
            )

    def _load_profile(
        self,
        conn: Any,
        *,
        destination_profile_id: str,
        portal_origin: str,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        profile = self.repository.load_destination_profile(
            conn, profile_id=destination_profile_id
        )
        if not profile:
            raise PresenterNotFound("Perfil de destino no encontrado")
        projection = _destination_workspace_projection(profile)
        exact_origin = normalize_origin(portal_origin)
        if projection["portal_origin"] != exact_origin:
            raise PresenterConflict(
                "presenter.origin_mismatch", "El origen no coincide con el perfil"
            )
        if "portal" not in projection.get("delivery_channels", ()):
            raise PresenterConflict(
                "presenter.portal_channel_unavailable",
                "El perfil no admite presentación por sede",
            )
        return profile, projection

    def open_session(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        destination_profile_id: str,
        portal_origin: str,
        representation_mode: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_delivery_prepare(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        command_key = _idempotency_key(idempotency_key)
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_profile_id = _uuid(
            destination_profile_id, "Perfil de destino no encontrado"
        )
        exact_origin = normalize_origin(portal_origin)
        mode = str(representation_mode or "").strip().lower()
        portal_session_id = str(
            uuid.uuid5(
                _SESSION_NAMESPACE,
                ":".join(
                    (
                        actor.operator_id,
                        actor.operator_session_id,
                        exact_case_id,
                        exact_profile_id,
                        command_key,
                    )
                ),
            )
        )
        self.repository.lock_portal_session(
            conn, case_id=exact_case_id, portal_session_id=portal_session_id
        )
        request_sha256 = canonical_sha256(
            {
                "case_id": exact_case_id,
                "destination_profile_id": exact_profile_id,
                "portal_origin": exact_origin,
                "representation_mode": mode,
                "operator_id": actor.operator_id,
                "operator_session_id": actor.operator_session_id,
            }
        )
        existing = self._events(
            conn, case_id=exact_case_id, portal_session_id=portal_session_id
        )
        if existing:
            opened = self._opened(existing)
            if opened.get("request_sha256") != request_sha256:
                raise PresenterConflict(
                    "presenter.portal_idempotency_key_reused",
                    "La clave idempotente pertenece a otra sesión",
                )
            self._assert_actor_binding(opened, actor)
            return opened

        profile, projection = self._load_profile(
            conn,
            destination_profile_id=exact_profile_id,
            portal_origin=exact_origin,
        )
        allowed_modes = set(projection.get("representation_modes", ()))
        if mode not in allowed_modes:
            raise PresenterConflict(
                "presenter.representation_mode_rejected",
                "Modo de representación no admitido por el destino",
            )
        current = self._now()
        snapshot = {
            "portal_session_contract_version": (
                RTM_PRESENTER_PORTAL_SESSION_VERSION
            ),
            "portal_session_id": portal_session_id,
            "case_id": exact_case_id,
            "operator_id": actor.operator_id,
            "operator_session_id": actor.operator_session_id,
            "state": "open",
            "destination": {
                "destination_profile_id": exact_profile_id,
                "profile_code": projection["profile_code"],
                "profile_version": projection["profile_version"],
                "profile_sha256": projection["profile_sha256"],
                "display_name": projection["display_name"],
                "authority_code": projection["authority_code"],
                "portal_origin": exact_origin,
            },
            "representation_mode": mode,
            "fields": list(projection["fields"]),
            "document_selection_mode": "individual_on_demand",
            "container_documents_remain_individual": True,
            "one_document_per_intent": True,
            "managed_extension_attestation_required": True,
            "managed_extension_attestation_verified": False,
            "operator_download_available": False,
            "archive_created": False,
            "local_files_created": False,
            "external_effects_allowed": False,
            "legal_submission_executed": False,
            "human_controls": {
                "clave": True,
                "signature": True,
                "captcha": True,
                "final_submit": True,
            },
            "sent_at": None,
            "receipt_verified": False,
            "deadline_tracking": {
                "status": "not_started",
                "deadline_kind": "operational_followup",
                "anchor_at": None,
                "legal_due_at": None,
                "legal_deadline_calculated": False,
                "validated_legal_rule_required": True,
            },
            "opened_at": _stamp(current),
            "expires_at": _stamp(current + _SESSION_TTL),
            "request_sha256": request_sha256,
            "synthetic_only": True,
        }
        del profile
        self.repository.append_audit(
            conn,
            event_type="presenter.portal_session.opened",
            reason_code="individual_documents_no_external_effect",
            actor=actor,
            case_id=exact_case_id,
            payload=snapshot,
        )
        return snapshot

    def prepare_attachment_intent(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        portal_session_id: str,
        field_code: str,
        portal_field_fingerprint_sha256: str,
        document_version_id: str,
        portal_filename: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_delivery_prepare(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_session_id = _uuid(
            portal_session_id, "Sesión de sede no encontrada"
        )
        exact_document_id = _uuid(
            document_version_id, "Versión documental no encontrada"
        )
        command_key = _idempotency_key(idempotency_key)
        code = str(field_code or "").strip().lower()
        if not _FIELD_CODE_RE.fullmatch(code):
            raise PresenterConflict(
                "presenter.portal_field_invalid", "Campo de sede no válido"
            )
        field_fingerprint = _sha256(
            portal_field_fingerprint_sha256,
            "presenter.portal_field_fingerprint_invalid",
            "La huella del campo de sede no es válida",
        )
        self.repository.lock_portal_session(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        events = self._events(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        opened = self._opened(events)
        self._assert_actor_binding(opened, actor)
        current = self._now()
        self._assert_open(opened, events, now=current)

        attachment_intent_id = str(
            uuid.uuid5(
                _INTENT_NAMESPACE,
                ":".join(
                    (
                        exact_session_id,
                        code,
                        field_fingerprint,
                        exact_document_id,
                        command_key,
                    )
                ),
            )
        )
        requested_filename = (
            safe_filename(portal_filename) if portal_filename else None
        )
        request_sha256 = canonical_sha256(
            {
                "portal_session_id": exact_session_id,
                "field_code": code,
                "portal_field_fingerprint_sha256": field_fingerprint,
                "document_version_id": exact_document_id,
                "portal_filename": requested_filename,
                "operator_id": actor.operator_id,
                "operator_session_id": actor.operator_session_id,
            }
        )
        prior_intents = _events_of_type(
            events, "presenter.portal_session.attachment_intent_prepared"
        )
        replay = next(
            (
                item
                for item in prior_intents
                if item.get("attachment_intent_id") == attachment_intent_id
            ),
            None,
        )
        if replay:
            if replay.get("request_sha256") != request_sha256:
                raise PresenterConflict(
                    "presenter.portal_idempotency_key_reused",
                    "La clave idempotente pertenece a otro documento o campo",
                )
            return replay

        destination = _json_object(opened.get("destination"))
        profile, projection = self._load_profile(
            conn,
            destination_profile_id=str(destination.get("destination_profile_id")),
            portal_origin=str(destination.get("portal_origin")),
        )
        if (
            projection.get("profile_version") != destination.get("profile_version")
            or projection.get("profile_sha256") != destination.get("profile_sha256")
        ):
            raise PresenterConflict(
                "presenter.destination_profile_changed",
                "El perfil de sede cambió durante la sesión",
            )
        fields, _ = _requirements_fields(profile.get("requirements"))
        field_contract = fields.get(code)
        if not field_contract:
            raise PresenterConflict(
                "presenter.destination_field_unknown",
                "Campo de destino no reconocido",
            )
        adapter = _portal_adapter_binding(field_contract)
        if field_fingerprint != adapter["input_fingerprint_sha256"]:
            raise PresenterConflict(
                "presenter.portal_field_fingerprint_mismatch",
                "La huella observada no coincide con el campo verificado",
            )
        document_row = self.repository.load_document_version(
            conn,
            case_id=exact_case_id,
            document_version_id=exact_document_id,
            for_update=True,
        )
        if not document_row:
            raise PresenterNotFound("Versión documental no encontrada")
        document = _document_from_row(document_row)
        if document.state is not PresenterDocumentState.ACTIVE:
            raise PresenterConflict(
                "presenter.document_not_approved", "Documento no aprobado"
            )
        _validate_selection_against_field(document, field_contract)

        recorded_intent_ids = {
            str(item.get("attachment_intent_id"))
            for item in _events_of_type(
                events, "presenter.portal_session.synthetic_attachment_recorded"
            )
        }
        active_prior_intents = [
            item
            for item in prior_intents
            if str(item.get("attachment_intent_id")) in recorded_intent_ids
            or _aware(item.get("expires_at"), "intent.expires_at") > current
        ]
        if any(
            item.get("document_version_id") == exact_document_id
            for item in active_prior_intents
        ):
            raise PresenterConflict(
                "presenter.portal_document_already_selected",
                "La versión documental ya está vinculada en esta sesión",
            )
        field_count = sum(
            1 for item in active_prior_intents if item.get("field_code") == code
        )
        if field_count >= int(field_contract.get("max_files") or 1):
            raise PresenterConflict(
                "presenter.destination_field_overflow",
                "El campo ya alcanzó su máximo de documentos",
            )

        exact_filename = requested_filename or document.original_filename
        expires_at = min(
            _aware(opened.get("expires_at"), "expires_at"),
            current + _INTENT_TTL,
        )
        intent = {
            "portal_session_contract_version": (
                RTM_PRESENTER_PORTAL_SESSION_VERSION
            ),
            "portal_session_id": exact_session_id,
            "attachment_intent_id": attachment_intent_id,
            "case_id": exact_case_id,
            "operator_id": actor.operator_id,
            "operator_session_id": actor.operator_session_id,
            "destination_profile_id": destination["destination_profile_id"],
            "destination_profile_version": destination["profile_version"],
            "destination_profile_sha256": destination["profile_sha256"],
            "portal_origin": destination["portal_origin"],
            "field_code": code,
            "portal_field_fingerprint_sha256": field_fingerprint,
            "portal_adapter_id": adapter["adapter_id"],
            "portal_adapter_version": adapter["adapter_version"],
            "portal_adapter_sha256": adapter["adapter_sha256"],
            "portal_input_selector": adapter["input_selector"],
            "extension_client_id": "rtm.presenter.browser_extension.v1",
            "document_count": 1,
            "document_version_id": document.document_version_id,
            "logical_document_id": document.logical_document_id,
            "document_version": document.version_number,
            "document_sha256": document.sha256,
            "document_purpose": document.purpose,
            "portal_filename": exact_filename,
            "media_type": document.media_type,
            "size_bytes": document.size_bytes,
            "state": "prepared",
            "managed_extension_attestation_required": True,
            "document_bytes_read": False,
            "local_file_created": False,
            "archive_created": False,
            "external_effects_executed": False,
            "legal_submission_executed": False,
            "sent_at": None,
            "receipt_verified": False,
            "deadline_tracking_started": False,
            "prepared_at": _stamp(current),
            "expires_at": _stamp(expires_at),
            "request_sha256": request_sha256,
            "synthetic_only": True,
        }
        self.repository.append_audit(
            conn,
            event_type="presenter.portal_session.attachment_intent_prepared",
            reason_code="one_document_bound_to_one_verified_field",
            actor=actor,
            case_id=exact_case_id,
            payload=intent,
        )
        return intent

    def record_synthetic_attachment(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        portal_session_id: str,
        attachment_intent_id: str,
        request_origin: str,
        portal_field_fingerprint_sha256: str,
        observed_document_sha256: str,
    ) -> dict[str, Any]:
        """Registra la prueba sintética; nunca entrega bytes ni pulsa la sede."""

        self._open(conn)
        if self.runtime.managed_extension_attestation_enabled is not True:
            raise PresenterForbidden("Canal Presenter no disponible")
        authorize_handoff_exchange_client(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_session_id = _uuid(
            portal_session_id, "Sesión de sede no encontrada"
        )
        exact_intent_id = _uuid(
            attachment_intent_id, "Intención de adjunto no encontrada"
        )
        exact_origin = normalize_origin(request_origin)
        field_fingerprint = _sha256(
            portal_field_fingerprint_sha256,
            "presenter.portal_field_fingerprint_invalid",
            "La huella del campo de sede no es válida",
        )
        document_sha256 = _sha256(
            observed_document_sha256,
            "presenter.portal_document_hash_invalid",
            "La huella documental observada no es válida",
        )
        self.repository.lock_portal_session(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        events = self._events(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        opened = self._opened(events)
        self._assert_actor_binding(opened, actor)
        current = self._now()
        self._assert_open(opened, events, now=current)
        intents = _events_of_type(
            events, "presenter.portal_session.attachment_intent_prepared"
        )
        intent = next(
            (
                item
                for item in intents
                if item.get("attachment_intent_id") == exact_intent_id
            ),
            None,
        )
        if not intent:
            raise PresenterNotFound("Intención de adjunto no encontrada")
        if _aware(intent.get("expires_at"), "intent.expires_at") <= current:
            raise PresenterConflict(
                "presenter.portal_attachment_intent_expired",
                "La intención de adjunto ha caducado",
            )
        if (
            intent.get("portal_origin") != exact_origin
            or intent.get("extension_client_id") != actor.extension_client_id
            or intent.get("portal_field_fingerprint_sha256") != field_fingerprint
            or intent.get("document_sha256") != document_sha256
        ):
            raise PresenterForbidden("Adjunto fuera de origen, campo o versión")
        prior = next(
            (
                item
                for item in _events_of_type(
                    events,
                    "presenter.portal_session.synthetic_attachment_recorded",
                )
                if item.get("attachment_intent_id") == exact_intent_id
            ),
            None,
        )
        if prior:
            return prior

        row = self.repository.load_document_version(
            conn,
            case_id=exact_case_id,
            document_version_id=str(intent["document_version_id"]),
            for_update=True,
        )
        document = _document_from_row(row) if row else None
        if (
            document is None
            or document.state is not PresenterDocumentState.ACTIVE
            or document.sha256 != document_sha256
            or document.version_number != intent.get("document_version")
        ):
            raise PresenterConflict(
                "presenter.document_revalidation_failed",
                "La versión documental ya no coincide",
            )
        recorded = {
            **{
                key: intent[key]
                for key in (
                    "portal_session_contract_version",
                    "portal_session_id",
                    "attachment_intent_id",
                    "case_id",
                    "operator_id",
                    "operator_session_id",
                    "destination_profile_id",
                    "destination_profile_version",
                    "destination_profile_sha256",
                    "portal_origin",
                    "field_code",
                    "portal_field_fingerprint_sha256",
                    "portal_adapter_id",
                    "portal_adapter_version",
                    "portal_adapter_sha256",
                    "portal_input_selector",
                    "extension_client_id",
                    "document_version_id",
                    "document_version",
                    "document_sha256",
                    "portal_filename",
                )
            },
            "state": "synthetic_attachment_recorded",
            "managed_extension_attestation_id": actor.extension_attestation_id,
            "attached_at": _stamp(current),
            "document_bytes_read": False,
            "portal_bytes_injected": False,
            "local_file_created": False,
            "external_effects_executed": False,
            "legal_submission_executed": False,
            "sent_at": None,
            "receipt_verified": False,
            "deadline_tracking_started": False,
            "human_final_submit_required": True,
            "synthetic_only": True,
        }
        self.repository.append_audit(
            conn,
            event_type=(
                "presenter.portal_session.synthetic_attachment_recorded"
            ),
            reason_code="attested_binding_revalidated_no_external_effect",
            actor=actor,
            case_id=exact_case_id,
            payload=recorded,
        )
        return recorded

    def capture_receipt_pending(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        portal_session_id: str,
        request_origin: str,
        capture_source: str,
        attachment_manifest_sha256: str,
        content: bytes,
        original_filename: str,
        declared_mime: str,
        synthetic_confirmed: bool,
        idempotency_key: str | None,
        storage_writer: Callable[
            [PresenterExternalDocumentUpload, Callable[[str, str], None]],
            tuple[str, str],
        ],
        register_rollback_cleanup: Callable[[str, str], None],
    ) -> dict[str, Any]:
        """Custodia un justificante descargado sin inferir que hubo envío."""

        self._open(conn)
        if self.runtime.managed_extension_attestation_enabled is not True:
            raise PresenterForbidden("Canal Presenter no disponible")
        authorize_handoff_exchange_client(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        source = str(capture_source or "").strip().lower()
        if source == "email_attachment":
            raise PresenterConflict(
                "presenter.receipt_email_capture_not_ready",
                "La fuente email está reservada pero todavía no tiene actor confiable",
            )
        if source != "portal_download":
            raise PresenterConflict(
                "presenter.receipt_capture_source_invalid",
                "Fuente de justificante no admitida",
            )
        if synthetic_confirmed is not True:
            raise PresenterConflict(
                "presenter.synthetic_confirmation_required",
                "Confirmación sintética obligatoria",
            )
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_session_id = _uuid(
            portal_session_id, "Sesión de sede no encontrada"
        )
        exact_origin = normalize_origin(request_origin)
        expected_manifest_sha = _sha256(
            attachment_manifest_sha256,
            "presenter.receipt_attachment_manifest_invalid",
            "La huella del manifiesto de adjuntos no es válida",
        )
        command_key = _idempotency_key(idempotency_key)
        upload = validate_external_document_upload(
            content=content,
            original_filename=original_filename,
            declared_mime=declared_mime,
            purpose="submission_receipt",
        )
        receipt_capture_id = str(
            uuid.uuid5(
                _RECEIPT_CAPTURE_NAMESPACE,
                f"{exact_session_id}:{command_key}",
            )
        )
        request_sha256 = canonical_sha256(
            {
                "portal_session_id": exact_session_id,
                "receipt_capture_id": receipt_capture_id,
                "capture_source": source,
                "portal_origin": exact_origin,
                "attachment_manifest_sha256": expected_manifest_sha,
                "document_sha256": upload.sha256,
                "original_filename": upload.original_filename,
                "media_type": upload.media_type,
                "size_bytes": upload.size_bytes,
                "operator_id": actor.operator_id,
                "operator_session_id": actor.operator_session_id,
            }
        )
        self.repository.lock_portal_session(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        events = self._events(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        opened = self._opened(events)
        self._assert_actor_binding(opened, actor)
        destination = _json_object(opened.get("destination"))
        if destination.get("portal_origin") != exact_origin:
            raise PresenterForbidden("Justificante fuera del origen de la sesión")
        if _events_of_type(events, "presenter.portal_session.receipt_verified"):
            raise PresenterConflict(
                "presenter.portal_session_closed",
                "La sesión ya tiene justificante verificado",
            )
        current = self._now()
        if current > _aware(opened.get("opened_at"), "opened_at") + _RECEIPT_CAPTURE_TTL:
            raise PresenterConflict(
                "presenter.receipt_capture_window_expired",
                "La ventana de captura del justificante ha caducado",
            )
        prior = next(
            (
                item
                for item in _events_of_type(
                    events, "presenter.portal_session.receipt_captured"
                )
                if item.get("receipt_capture_id") == receipt_capture_id
            ),
            None,
        )
        if prior:
            if prior.get("request_sha256") != request_sha256:
                raise PresenterConflict(
                    "presenter.portal_idempotency_key_reused",
                    "La clave idempotente pertenece a otro justificante",
                )
            return prior
        if _events_of_type(
            events, "presenter.portal_session.receipt_captured"
        ):
            raise PresenterConflict(
                "presenter.receipt_pending_already_exists",
                (
                    "La sesión ya tiene un justificante pendiente; "
                    "debe abrirse una sesión nueva para otra captura"
                ),
            )
        attachments = _events_of_type(
            events, "presenter.portal_session.synthetic_attachment_recorded"
        )
        if not attachments:
            raise PresenterConflict(
                "presenter.receipt_without_attachments",
                "No puede capturarse justificante sin adjuntos registrados",
            )
        attachment_items, actual_manifest_sha = _attachment_manifest(attachments)
        if actual_manifest_sha != expected_manifest_sha:
            raise PresenterConflict(
                "presenter.receipt_attachment_manifest_invalid",
                "El manifiesto no coincide con los adjuntos de la sesión",
            )

        registered_coordinates: set[tuple[str, str]] = set()

        def tracked_cleanup(bucket: str, key: str) -> None:
            clean_bucket = str(bucket or "").strip()
            clean_key = str(key or "").strip()
            if not clean_bucket or not clean_key:
                raise PresenterServiceError(
                    "presenter.storage_contract_invalid",
                    "Custodia documental no verificable",
                    status_code=502,
                )
            register_rollback_cleanup(clean_bucket, clean_key)
            registered_coordinates.add((clean_bucket, clean_key))

        coordinates = storage_writer(upload, tracked_cleanup)
        if not isinstance(coordinates, tuple) or len(coordinates) != 2:
            raise PresenterServiceError(
                "presenter.storage_contract_invalid",
                "Custodia documental no verificable",
                status_code=502,
            )
        storage_bucket = str(coordinates[0] or "").strip()
        storage_key = str(coordinates[1] or "").strip()
        if (
            not storage_bucket
            or not storage_key
            or (storage_bucket, storage_key) not in registered_coordinates
        ):
            if storage_bucket and storage_key:
                register_rollback_cleanup(storage_bucket, storage_key)
            raise PresenterServiceError(
                "presenter.storage_cleanup_not_pre_registered",
                "Custodia documental sin cleanup previo",
                status_code=502,
            )
        row = self.repository.insert_external_document_version(
            conn,
            case_id=exact_case_id,
            created_by_operator_id=actor.operator_id,
            upload=upload,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
            supersedes_document_version_id=None,
        )
        document = _document_from_row(row)
        if (
            document.state is not PresenterDocumentState.REVIEW
            or document.scan_status != "pending"
            or document.source_kind != "external_revision"
            or document.purpose != "submission_receipt"
            or document.sha256 != upload.sha256
        ):
            raise PresenterServiceError(
                "presenter.receipt_capture_state_invalid",
                "El justificante no quedó pendiente de revisión",
                status_code=500,
            )
        captured = {
            "portal_session_contract_version": (
                RTM_PRESENTER_PORTAL_SESSION_VERSION
            ),
            "portal_session_id": exact_session_id,
            "receipt_capture_contract_version": (
                RTM_PRESENTER_RECEIPT_CAPTURE_VERSION
            ),
            "receipt_capture_id": receipt_capture_id,
            "case_id": exact_case_id,
            "operator_id": actor.operator_id,
            "operator_session_id": actor.operator_session_id,
            "destination_profile_id": destination["destination_profile_id"],
            "destination_profile_version": destination["profile_version"],
            "destination_profile_sha256": destination["profile_sha256"],
            "portal_origin": exact_origin,
            "capture_source": source,
            "state": "receipt_pending",
            "captured_document_version_id": document.document_version_id,
            "captured_logical_document_id": document.logical_document_id,
            "captured_document_version": document.version_number,
            "captured_document_sha256": document.sha256,
            "captured_document_state": document.state.value,
            "captured_document_scan_status": document.scan_status,
            "attachment_manifest_sha256": actual_manifest_sha,
            "attachment_intent_ids": [
                item["attachment_intent_id"] for item in attachment_items
            ],
            "receipt_bytes_captured": True,
            "capture_requires_explicit_human_action": True,
            "native_download_observed": False,
            "download_is_submission": False,
            "authoritative_submission": False,
            "receipt_verified": False,
            "sent_at": None,
            "followup_activation_ready": False,
            "followups_created": False,
            "legal_deadline_calculated": False,
            "case_status_changed": False,
            "local_file_created": False,
            "storage_references_exposed": False,
            "captured_at": _stamp(current),
            "request_sha256": request_sha256,
            "synthetic_only": True,
            "legal_submission_executed": False,
        }
        self.repository.append_audit(
            conn,
            event_type="presenter.portal_session.receipt_captured",
            reason_code="explicit_receipt_capture_pending_not_submission",
            actor=actor,
            case_id=exact_case_id,
            payload=captured,
        )
        return captured

    def verify_receipt_and_enable_tracking(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        portal_session_id: str,
        receipt_document_version_id: str,
        expected_receipt_sha256: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_receipt_verification(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_session_id = _uuid(
            portal_session_id, "Sesión de sede no encontrada"
        )
        exact_receipt_id = _uuid(
            receipt_document_version_id, "Justificante no encontrado"
        )
        command_key = _idempotency_key(idempotency_key)
        exact_receipt_sha = _sha256(
            expected_receipt_sha256,
            "presenter.receipt_hash_invalid",
            "La huella del justificante no es válida",
        )
        self.repository.lock_portal_session(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        events = self._events(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        opened = self._opened(events)
        if opened.get("operator_id") == actor.operator_id:
            raise PresenterForbidden(
                "El presentador no puede verificar su propio justificante"
            )
        current = self._now()
        request_sha256 = canonical_sha256(
            {
                "portal_session_id": exact_session_id,
                "receipt_document_version_id": exact_receipt_id,
                "expected_receipt_sha256": exact_receipt_sha,
                "operator_id": actor.operator_id,
                "idempotency_key": command_key,
            }
        )
        prior_receipts = _events_of_type(
            events, "presenter.portal_session.receipt_verified"
        )
        if prior_receipts:
            receipt = prior_receipts[0]
            if receipt.get("request_sha256") != request_sha256:
                raise PresenterConflict(
                    "presenter.receipt_already_verified",
                    "La sesión ya tiene otro justificante verificado",
                )
            return receipt
        attachments = _events_of_type(
            events, "presenter.portal_session.synthetic_attachment_recorded"
        )
        if not attachments:
            raise PresenterConflict(
                "presenter.receipt_without_attachments",
                "No se puede cerrar una sesión sin adjuntos registrados",
            )
        destination = _json_object(opened.get("destination"))
        profile, projection = self._load_profile(
            conn,
            destination_profile_id=str(destination.get("destination_profile_id")),
            portal_origin=str(destination.get("portal_origin")),
        )
        if (
            projection.get("profile_version") != destination.get("profile_version")
            or projection.get("profile_sha256") != destination.get("profile_sha256")
        ):
            raise PresenterConflict(
                "presenter.destination_profile_changed",
                "El perfil de sede cambió antes de verificar el justificante",
            )
        fields, requirements = _requirements_fields(profile.get("requirements"))
        counts: dict[str, int] = {}
        for attachment in attachments:
            code = str(attachment.get("field_code") or "")
            if code not in fields:
                raise PresenterConflict(
                    "presenter.receipt_attachment_manifest_invalid",
                    "El justificante referencia un campo que ya no es verificable",
                )
            counts[code] = counts.get(code, 0) + 1
        mode = str(opened.get("representation_mode") or "")
        missing = sorted(
            code
            for code, contract in fields.items()
            if (
                bool(contract.get("required", False))
                or mode
                in {
                    str(value).strip().lower()
                    for value in contract.get("required_for_modes", ())
                }
            )
            and counts.get(code, 0) == 0
        )
        if missing:
            raise PresenterConflict(
                "presenter.receipt_required_fields_missing",
                "El justificante no concilia todos los campos requeridos",
            )
        attachment_items, attachment_manifest_sha256 = _attachment_manifest(
            attachments
        )
        del requirements
        row = self.repository.load_document_version(
            conn,
            case_id=exact_case_id,
            document_version_id=exact_receipt_id,
            for_update=True,
        )
        document = _document_from_row(row) if row else None
        if (
            document is None
            or document.state is not PresenterDocumentState.ACTIVE
            or document.purpose != "submission_receipt"
            or document.source_kind != "receipt"
            or document.sha256 != exact_receipt_sha
        ):
            raise PresenterConflict(
                "presenter.receipt_document_invalid",
                "El justificante activo no coincide con la evidencia declarada",
            )
        metadata = _json_object(row.get("metadata")) if row else {}
        receipt_metadata = _json_object(metadata.get(_RECEIPT_METADATA_KEY))
        if set(receipt_metadata) != _RECEIPT_MATERIAL_KEYS | {"material_sha256"}:
            raise PresenterConflict(
                "presenter.receipt_verification_failed",
                "El justificante no contiene evidencia cerrada de registro y fecha",
            )
        registration = _clean_reference(
            receipt_metadata.get("registration_number"), "Número de registro"
        )
        verification = _clean_reference(
            receipt_metadata.get("verification_reference"),
            "Referencia de verificación",
        )
        submitted = _aware(receipt_metadata.get("submitted_at"), "submitted_at")
        receipt_capture_id = _uuid(
            receipt_metadata.get("receipt_capture_id"),
            "Captura de justificante no encontrada",
        )
        captured_document_version_id = _uuid(
            receipt_metadata.get("captured_document_version_id"),
            "Documento capturado no encontrado",
        )
        captured_document_sha256 = _sha256(
            receipt_metadata.get("captured_document_sha256"),
            "presenter.receipt_capture_hash_invalid",
            "La huella de la captura no es válida",
        )
        if (
            exact_receipt_id != captured_document_version_id
            or exact_receipt_sha != captured_document_sha256
        ):
            raise PresenterConflict(
                "presenter.receipt_capture_bytes_mismatch",
                (
                    "La verificación exige exactamente la misma versión "
                    "y los mismos bytes capturados"
                ),
            )
        capture = next(
            (
                item
                for item in _events_of_type(
                    events, "presenter.portal_session.receipt_captured"
                )
                if item.get("receipt_capture_id") == receipt_capture_id
            ),
            None,
        )
        if (
            not capture
            or capture.get("state") != "receipt_pending"
            or capture.get("captured_document_version_id")
            != captured_document_version_id
            or capture.get("captured_document_sha256")
            != captured_document_sha256
            or capture.get("attachment_manifest_sha256")
            != attachment_manifest_sha256
            or capture.get("destination_profile_id")
            != destination.get("destination_profile_id")
            or capture.get("portal_origin") != destination.get("portal_origin")
        ):
            raise PresenterConflict(
                "presenter.receipt_capture_binding_invalid",
                "El justificante verificado no enlaza una captura pendiente exacta",
            )
        authority_hash_algorithm = str(
            receipt_metadata.get("authority_hash_algorithm") or ""
        ).strip().lower()
        authority_hash_scope = str(
            receipt_metadata.get("authority_hash_scope") or ""
        ).strip().lower()
        authority_hash_value = _sha256(
            receipt_metadata.get("authority_hash_value"),
            "presenter.receipt_authority_hash_invalid",
            "La huella oficial declarada por la sede no es válida",
        )
        if (
            authority_hash_algorithm != "sha-256"
            or authority_hash_scope != "attachment_manifest"
            or authority_hash_value != attachment_manifest_sha256
        ):
            raise PresenterConflict(
                "presenter.receipt_authority_hash_mismatch",
                "La huella oficial no acredita el manifiesto exacto de adjuntos",
            )
        opened_at = _aware(opened.get("opened_at"), "opened_at")
        if submitted < opened_at or submitted > current + _CLOCK_SKEW:
            raise PresenterConflict(
                "presenter.receipt_submission_time_invalid",
                "La fecha/hora inmutable del justificante queda fuera de la sesión",
            )
        if any(
            _aware(item.get("attached_at"), "attached_at") > submitted + _CLOCK_SKEW
            for item in attachments
        ):
            raise PresenterConflict(
                "presenter.receipt_attachment_time_invalid",
                "El justificante es anterior a un adjunto de la sesión",
            )
        material = {
            "format": _RECEIPT_FORMAT,
            "case_id": exact_case_id,
            "portal_session_id": exact_session_id,
            "destination_profile_id": destination["destination_profile_id"],
            "portal_origin": destination["portal_origin"],
            "registration_number": registration,
            "submitted_at": _stamp(submitted),
            "verification_reference": verification,
            "receipt_document_version_id": exact_receipt_id,
            "receipt_sha256": exact_receipt_sha,
            "receipt_capture_id": receipt_capture_id,
            "captured_document_version_id": captured_document_version_id,
            "captured_document_sha256": captured_document_sha256,
            "attachment_manifest_sha256": attachment_manifest_sha256,
            "authority_hash_algorithm": authority_hash_algorithm,
            "authority_hash_scope": authority_hash_scope,
            "authority_hash_value": authority_hash_value,
            "synthetic_only": True,
            "legal_submission_executed": False,
        }
        expected_material_sha = canonical_sha256(material)
        if (
            {
                key: receipt_metadata.get(key) for key in _RECEIPT_MATERIAL_KEYS
            }
            != material
            or receipt_metadata.get("material_sha256") != expected_material_sha
        ):
            raise PresenterConflict(
                "presenter.receipt_verification_failed",
                "El justificante no supera la verificación de registro, fecha y huellas",
            )

        deadline_event_payload = {
            "event_contract_version": RTM_PRESENTER_PORTAL_SESSION_VERSION,
            "source_event_type": RTM_PRESENTER_DEADLINE_SOURCE_EVENT,
            "case_id": exact_case_id,
            "portal_session_id": exact_session_id,
            "destination_profile_id": destination["destination_profile_id"],
            "destination_profile_version": destination["profile_version"],
            "destination_profile_sha256": destination["profile_sha256"],
            "portal_origin": destination["portal_origin"],
            "registration_number": registration,
            "verification_reference": verification,
            "receipt_document_version_id": exact_receipt_id,
            "receipt_evidence_id": exact_receipt_id,
            "receipt_sha256": exact_receipt_sha,
            "receipt_capture_id": receipt_capture_id,
            "captured_document_version_id": captured_document_version_id,
            "captured_document_sha256": captured_document_sha256,
            "receipt_material_sha256": expected_material_sha,
            "attachment_manifest_sha256": attachment_manifest_sha256,
            "authority_hash_algorithm": authority_hash_algorithm,
            "authority_hash_scope": authority_hash_scope,
            "authority_hash_value": authority_hash_value,
            "attachment_intent_ids": [
                item["attachment_intent_id"] for item in attachment_items
            ],
            "submitted_at": _stamp(submitted),
            "sent_at": _stamp(submitted),
            "deadline_anchor_at": _stamp(submitted),
            "deadline_kind": "operational_followup",
            "followup_activation_ready": True,
            "followups_created": False,
            "legal_due_at": None,
            "legal_deadline_calculated": False,
            "validated_profile_rule_required_for_legal_deadline": True,
            "calendar_validation_required_for_legal_deadline": True,
            "synthetic_only": True,
            "legal_submission_executed": False,
            "case_status_changed": False,
        }
        emitted = self.repository.emit_deadline_tracking_event(
            conn,
            case_id=exact_case_id,
            portal_session_id=exact_session_id,
            payload=deadline_event_payload,
        )
        if emitted is not True:
            raise PresenterConflict(
                "presenter.deadline_event_conflict",
                "El evento de seguimiento no pudo quedar enlazado al justificante",
            )
        verified = {
            **deadline_event_payload,
            "portal_session_contract_version": (
                RTM_PRESENTER_PORTAL_SESSION_VERSION
            ),
            "state": "receipt_verified_synthetic",
            "receipt_verified": True,
            "verified_at": _stamp(current),
            "verified_by_operator_id": actor.operator_id,
            "deadline_tracking": {
                "status": "followup_activation_ready",
                "source_event_type": RTM_PRESENTER_DEADLINE_SOURCE_EVENT,
                "deadline_kind": "operational_followup",
                "followup_activation_ready": True,
                "followups_created": False,
                "anchor_at": _stamp(submitted),
                "legal_due_at": None,
                "legal_deadline_calculated": False,
                "validated_legal_rule_required": True,
            },
            "attached_document_count": len(attachments),
            "operator_download_available": False,
            "local_files_created": False,
            "archive_created": False,
            "external_effects_executed": False,
            "human_final_submit_was_required": True,
            "request_sha256": request_sha256,
        }
        self.repository.append_audit(
            conn,
            event_type="presenter.portal_session.receipt_verified",
            reason_code="verified_receipt_anchors_operational_followup",
            actor=actor,
            case_id=exact_case_id,
            payload=verified,
        )
        return verified

    def status(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        portal_session_id: str,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_delivery_prepare(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        exact_case_id = _uuid(case_id, "Expediente no encontrado")
        exact_session_id = _uuid(
            portal_session_id, "Sesión de sede no encontrada"
        )
        events = self._events(
            conn, case_id=exact_case_id, portal_session_id=exact_session_id
        )
        opened = self._opened(events)
        self._assert_actor_binding(opened, actor)
        receipts = _events_of_type(
            events, "presenter.portal_session.receipt_verified"
        )
        intents = _events_of_type(
            events, "presenter.portal_session.attachment_intent_prepared"
        )
        attachments = _events_of_type(
            events, "presenter.portal_session.synthetic_attachment_recorded"
        )
        captures = _events_of_type(
            events, "presenter.portal_session.receipt_captured"
        )
        receipt = receipts[0] if receipts else None
        latest_capture = captures[-1] if captures else None
        return {
            **opened,
            "state": (
                "receipt_verified_synthetic"
                if receipt
                else "receipt_pending"
                if latest_capture
                else "open"
            ),
            "attachment_intents": intents,
            "attachments": attachments,
            "receipt_captures": captures,
            "pending_receipt": latest_capture if not receipt else None,
            "receipt": receipt,
            "sent_at": receipt.get("sent_at") if receipt else None,
            "receipt_verified": bool(receipt),
            "deadline_tracking": (
                receipt["deadline_tracking"]
                if receipt
                else opened["deadline_tracking"]
            ),
        }


__all__ = [
    "RTM_PRESENTER_DEADLINE_SOURCE_EVENT",
    "RTM_PRESENTER_PORTAL_SESSION_VERSION",
    "RTM_PRESENTER_RECEIPT_CAPTURE_VERSION",
    "PresenterPortalSessionService",
]
