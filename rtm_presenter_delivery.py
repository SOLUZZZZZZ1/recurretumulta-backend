"""Preparación persistente y sin efectos externos de entregas Presenter.

Este módulo no envía correos ni pulsa controles de una sede. Convierte un
paquete ya congelado en una orden de trabajo idempotente, conserva el orden
exacto de sus elementos y registra en el ledger inmutable qué se pretende
presentar. Los efectos externos siguen sujetos a canales posteriores con
evidencia propia.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from rtm_presenter_contracts import (
    RTM_PRESENTER_MAX_FILE_BYTES,
    canonical_sha256,
    normalize_origin,
    safe_filename,
)
from rtm_presenter_policy import (
    PresenterActorContext,
    PresenterRuntimeConfiguration,
    authorize_delivery_prepare,
    require_presenter_runtime,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterForbidden,
    PresenterNotFound,
    PresenterSchemaNotReady,
    PresenterServiceError,
)


RTM_PRESENTER_DELIVERY_VERSION = "rtm_presenter_delivery_v1_3"
RTM_PRESENTER_SIGNATURE_QUEUE_VERSION = "rtm_presenter_signature_queue_v1_0"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_SYNTHETIC_EMAIL_DOMAINS = frozenset({"example.com", "example.net", "example.org"})
_DELIVERY_NAMESPACE = uuid.UUID("bd82c37f-6c59-4e13-9ba0-96b40f5ed35d")
RTM_CORRESPONDENCE_SENDER = "info@recurretumulta.eu"
_CORRESPONDENCE_CONFIRMATIONS = (
    "destination_reviewed",
    "interested_confirmed",
    "representation_confirmed",
    "text_confirmed",
    "attachments_confirmed",
    "data_minimization_confirmed",
)
_PORTAL_PREPARATION_CONFIRMATIONS = (
    "destination_reviewed",
    "interested_confirmed",
    "representation_confirmed",
    "text_confirmed",
    "attachments_confirmed",
)


def _synthetic_email_allowed(recipient: str) -> bool:
    domain = recipient.rpartition("@")[2]
    return domain in _SYNTHETIC_EMAIL_DOMAINS or domain.endswith(".example")


class PresenterDeliveryChannel(str, Enum):
    PORTAL = "portal"
    EMAIL = "email"


class PresenterDeliveryState(str, Enum):
    PREPARED = "prepared"
    AWAITING_SIGNATURE = "awaiting_signature"
    IN_PROGRESS = "in_progress"
    AWAITING_RECEIPT = "awaiting_receipt"
    COMPLETED = "completed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FAILED_BEFORE_EXTERNAL_EFFECT = "failed_before_external_effect"
    CANCELLED = "cancelled"


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
                "presenter.delivery_package_invalid",
                f"{name} no es verificable",
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresenterConflict(
            "presenter.delivery_package_invalid", f"{name} exige zona horaria"
        )
    return parsed.astimezone(timezone.utc)


def _email_destination(requirements: Mapping[str, Any]) -> dict[str, Any]:
    delivery = _json_object(requirements.get("delivery"))
    email = _json_object(delivery.get("email"))
    recipient = str(email.get("recipient") or "").strip().lower()
    template_code = str(email.get("template_code") or "").strip().lower()
    template_version = email.get("template_version")
    legal_entity_name = " ".join(
        str(email.get("legal_entity_name") or "").split()
    )
    entity_role = str(email.get("entity_role") or "").strip().lower()
    channel_status = str(email.get("channel_status") or "").strip().lower()
    official_source_label = " ".join(
        str(email.get("official_source_label") or "").split()
    )
    official_source_url = str(email.get("official_source_url") or "").strip()
    recommended_evidence_channel = str(
        email.get("recommended_evidence_channel") or ""
    ).strip().lower()
    sensitive_attachment_policy = str(
        email.get("sensitive_attachment_policy") or ""
    ).strip().lower()
    source = urlsplit(official_source_url)
    if (
        email.get("verified") is not True
        or not _EMAIL_RE.fullmatch(recipient)
        or not _synthetic_email_allowed(recipient)
        or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,95}", template_code)
        or isinstance(template_version, bool)
        or not isinstance(template_version, int)
        or template_version < 1
        or not 2 <= len(legal_entity_name) <= 200
        or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,127}", entity_role)
        or channel_status != "accepted"
        or not 2 <= len(official_source_label) <= 160
        or source.scheme != "https"
        or not source.netloc
        or source.username is not None
        or source.password is not None
        or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{1,127}", recommended_evidence_channel
        )
        or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{1,127}", sensitive_attachment_policy
        )
    ):
        raise PresenterConflict(
            "presenter.delivery_email_destination_unverified",
            "El destino de correo no tiene una configuración verificada",
        )
    return {
        "kind": "verified_email",
        "recipient": recipient,
        "verified": True,
        "template_code": template_code,
        "template_version": template_version,
        "legal_entity_name": legal_entity_name,
        "entity_role": entity_role,
        "channel_status": channel_status,
        "official_source_label": official_source_label,
        "official_source_url": official_source_url,
        "recommended_evidence_channel": recommended_evidence_channel,
        "sensitive_attachment_policy": sensitive_attachment_policy,
    }


def _operator_email_destination(
    recipient_email: str | None,
    *,
    recipient_confirmed: bool,
) -> dict[str, Any] | None:
    """Normaliza un destinatario escrito por el operador sin aprobarlo.

    Presenter sigue siendo exclusivamente sintético en este corte. Una
    dirección manual nunca se convierte en destino verificado y, además, solo
    puede usar dominios reservados para pruebas. El comando se puede preparar
    y auditar, pero un canal de salida posterior deberá exigir verificación
    independiente antes de enviar.
    """

    recipient = str(recipient_email or "").strip().lower()
    if not recipient:
        return None
    if (
        not _EMAIL_RE.fullmatch(recipient)
        or not _synthetic_email_allowed(recipient)
    ):
        raise PresenterConflict(
            "presenter.delivery_manual_email_not_synthetic",
            "El correo manual de staging debe usar un dominio sintético reservado",
        )
    if recipient_confirmed is not True:
        raise PresenterConflict(
            "presenter.delivery_manual_email_confirmation_required",
            "Confirma la dirección manual antes de preparar el correo",
        )
    return {
        "kind": "operator_entered_email_pending_verification",
        "recipient": recipient,
        "verified": False,
    }


def _correspondence_draft(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Valida el texto exacto y las confirmaciones que quedarán auditadas."""

    if not isinstance(value, Mapping):
        raise PresenterConflict(
            "presenter.correspondence_draft_required",
            "RTM Correspondencia exige asunto, texto y confirmaciones",
        )
    if set(value) != {"subject", "body", "confirmations"}:
        raise PresenterConflict(
            "presenter.correspondence_draft_invalid",
            "El borrador contiene campos fuera del contrato",
        )
    subject = " ".join(str(value.get("subject") or "").split())
    body = str(value.get("body") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if (
        not 1 <= len(subject) <= 240
        or "\n" in subject
        or any(ord(character) < 32 for character in subject)
        or not 1 <= len(body) <= 12000
        or any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in body
        )
    ):
        raise PresenterConflict(
            "presenter.correspondence_text_invalid",
            "El asunto o el texto del correo no es válido",
        )
    raw_confirmations = value.get("confirmations")
    if (
        not isinstance(raw_confirmations, Mapping)
        or set(raw_confirmations) != set(_CORRESPONDENCE_CONFIRMATIONS)
        or any(raw_confirmations.get(name) is not True for name in _CORRESPONDENCE_CONFIRMATIONS)
    ):
        raise PresenterConflict(
            "presenter.correspondence_confirmation_required",
            "Revisa destinatario, interesado, representación, texto y adjuntos",
        )
    return {
        "subject": subject,
        "body": body,
        "confirmations": {
            name: True for name in _CORRESPONDENCE_CONFIRMATIONS
        },
    }


def _portal_preparation(
    requirements: Mapping[str, Any], value: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Valida la hoja exacta que el operador deja para el puesto de firma.

    Los campos permitidos nacen exclusivamente del perfil de destino verificado.
    El operador no puede inventar selectores, URL, acciones de navegador ni datos
    de certificado. Esta instantánea solo prepara; no abre una sesión externa.
    """

    contract = requirements.get("portal_preparation")
    if not isinstance(contract, Mapping) or contract.get("enabled") is not True:
        raise PresenterConflict(
            "presenter.portal_preparation_profile_required",
            "El destino todavía no admite preparación para la cola de firma",
        )
    if not isinstance(value, Mapping) or set(value) != {
        "form_code",
        "values",
        "confirmations",
    }:
        raise PresenterConflict(
            "presenter.portal_preparation_required",
            "Completa y revisa la hoja del trámite antes de dejarla para firma",
        )

    form_code = str(contract.get("form_code") or "").strip().lower()
    if (
        not re.fullmatch(r"[a-z][a-z0-9_.-]{1,127}", form_code)
        or str(value.get("form_code") or "").strip().lower() != form_code
    ):
        raise PresenterConflict(
            "presenter.portal_preparation_form_mismatch",
            "La hoja preparada no corresponde al perfil de destino",
        )

    raw_fields = contract.get("fields")
    if (
        not isinstance(raw_fields, Sequence)
        or isinstance(raw_fields, (str, bytes))
        or not 1 <= len(raw_fields) <= 32
        or any(not isinstance(item, Mapping) for item in raw_fields)
    ):
        raise PresenterConflict(
            "presenter.portal_preparation_profile_invalid",
            "El perfil no define una hoja de trámite verificable",
        )

    fields: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for order, raw_field in enumerate(raw_fields, start=1):
        field = dict(raw_field)
        code = str(field.get("field_code") or "").strip().lower()
        label = " ".join(str(field.get("label") or "").split())
        required = field.get("required")
        multiline = field.get("multiline")
        max_length = field.get("max_length")
        if (
            not re.fullmatch(r"[a-z][a-z0-9_.-]{1,127}", code)
            or code in seen_codes
            or not 2 <= len(label) <= 120
            or type(required) is not bool
            or type(multiline) is not bool
            or type(max_length) is not int
            or not 1 <= max_length <= 12000
        ):
            raise PresenterConflict(
                "presenter.portal_preparation_profile_invalid",
                "El perfil contiene campos de trámite no válidos",
            )
        fields.append(
            {
                "field_code": code,
                "label": label,
                "required": required,
                "multiline": multiline,
                "max_length": max_length,
                "step_order": order,
            }
        )
        seen_codes.add(code)

    raw_values = value.get("values")
    if not isinstance(raw_values, Mapping) or set(raw_values) != seen_codes:
        raise PresenterConflict(
            "presenter.portal_preparation_values_invalid",
            "La hoja no contiene exactamente los campos exigidos por el destino",
        )
    exact_values: dict[str, str] = {}
    for field in fields:
        code = field["field_code"]
        raw_text = raw_values.get(code)
        if not isinstance(raw_text, str):
            raise PresenterConflict(
                "presenter.portal_preparation_values_invalid",
                "La hoja contiene un valor no textual",
            )
        text_value = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not field["multiline"]:
            text_value = " ".join(text_value.split())
        if (
            (field["required"] and not text_value)
            or len(text_value) > field["max_length"]
            or any(
                ord(character) < 32 and character not in {"\n", "\t"}
                for character in text_value
            )
            or (not field["multiline"] and "\n" in text_value)
        ):
            raise PresenterConflict(
                "presenter.portal_preparation_values_invalid",
                f"El campo {field['label']} no es válido",
            )
        exact_values[code] = text_value

    confirmations = value.get("confirmations")
    if (
        not isinstance(confirmations, Mapping)
        or set(confirmations) != set(_PORTAL_PREPARATION_CONFIRMATIONS)
        or any(
            confirmations.get(name) is not True
            for name in _PORTAL_PREPARATION_CONFIRMATIONS
        )
    ):
        raise PresenterConflict(
            "presenter.portal_preparation_confirmation_required",
            "Revisa destino, interesado, representación, texto y adjuntos",
        )

    return {
        "form_code": form_code,
        "fields": fields,
        "values": exact_values,
        "confirmations": {
            name: True for name in _PORTAL_PREPARATION_CONFIRMATIONS
        },
    }


class PresenterDeliveryService:
    """Deriva órdenes de entrega desde paquetes congelados y auditados."""

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
                "presenter.delivery_clock_invalid",
                "Reloj de entrega sin zona",
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

    @staticmethod
    def _validate_package(
        package: Mapping[str, Any], *, case_id: str, package_id: str, now: datetime
    ) -> tuple[dict[str, Any], ...]:
        if (
            str(package.get("id")) != package_id
            or str(package.get("case_id")) != case_id
            or str(package.get("status")) != "frozen"
            or str(package.get("profile_status")) != "active"
            or _aware(package.get("expires_at"), "expires_at") <= now
        ):
            raise PresenterConflict(
                "presenter.delivery_package_unavailable",
                "El paquete ya no está disponible para presentación",
            )
        manifest_sha256 = str(package.get("manifest_sha256") or "").lower()
        profile_sha256 = str(package.get("profile_sha256") or "").lower()
        if not _SHA256_RE.fullmatch(manifest_sha256) or not _SHA256_RE.fullmatch(
            profile_sha256
        ):
            raise PresenterConflict(
                "presenter.delivery_package_invalid",
                "Las huellas del paquete no son verificables",
            )
        raw_items = package.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(
            raw_items, (str, bytes)
        ) or not raw_items:
            raise PresenterConflict(
                "presenter.delivery_package_invalid",
                "El paquete no contiene documentos verificables",
            )
        items: list[dict[str, Any]] = []
        expected_order = 1
        seen_item_ids: set[str] = set()
        seen_document_version_ids: set[str] = set()
        for raw in raw_items:
            item = _json_object(raw)
            document_sha256 = str(item.get("document_sha256") or "").lower()
            current_sha256 = str(item.get("current_document_sha256") or "").lower()
            try:
                package_item_id = str(uuid.UUID(str(item.get("id") or "")))
                document_version_id = str(
                    uuid.UUID(str(item.get("document_version_id") or ""))
                )
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterConflict(
                    "presenter.delivery_package_invalid",
                    "El paquete contiene identificadores no verificables",
                ) from exc
            field_code = str(item.get("field_code") or "").strip().lower()
            portal_filename = str(item.get("portal_filename") or "").strip()
            media_type = str(item.get("detected_mime") or "").strip().lower()
            size_bytes = item.get("size_bytes")
            try:
                filename_is_safe = safe_filename(portal_filename) == portal_filename
            except ValueError:
                filename_is_safe = False
            if (
                item.get("item_order") != expected_order
                or str(item.get("state")) != "active"
                or str(item.get("scan_status")) != "clean"
                or not _SHA256_RE.fullmatch(document_sha256)
                or current_sha256 != document_sha256
                or package_item_id in seen_item_ids
                or document_version_id in seen_document_version_ids
                or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,127}", field_code)
                or not filename_is_safe
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}",
                    media_type,
                )
                or isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or not 1 <= size_bytes <= RTM_PRESENTER_MAX_FILE_BYTES
            ):
                raise PresenterConflict(
                    "presenter.delivery_package_stale",
                    "El paquete contiene una versión no vigente o no verificada",
                )
            items.append(
                {
                    "package_item_id": package_item_id,
                    "item_order": expected_order,
                    "field_code": field_code,
                    "portal_filename": portal_filename,
                    "document_version_id": document_version_id,
                    "document_sha256": document_sha256,
                    "media_type": media_type,
                    "size_bytes": size_bytes,
                    "state": "pending",
                }
            )
            seen_item_ids.add(package_item_id)
            seen_document_version_ids.add(document_version_id)
            expected_order += 1
        return tuple(items)

    @staticmethod
    def _snapshot_from_event(event: Mapping[str, Any]) -> dict[str, Any]:
        payload = _json_object(event.get("payload"))
        if payload.get("delivery_contract_version") != RTM_PRESENTER_DELIVERY_VERSION:
            raise PresenterConflict(
                "presenter.delivery_history_invalid",
                "El historial de la entrega no es verificable",
            )
        # El repositorio añade estos marcadores al ledger, pero no pertenecen al
        # contrato público de la entrega. Así una repetición idempotente devuelve
        # exactamente la misma forma que la primera preparación.
        payload.pop("service_version", None)
        payload.pop("synthetic_marker", None)
        payload.pop("synthetic_only", None)
        return payload

    def prepare(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        package_id: str,
        channel: str,
        idempotency_key: str | None,
        recipient_email: str | None = None,
        recipient_confirmed: bool = False,
        correspondence: Mapping[str, Any] | None = None,
        portal_preparation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_delivery_prepare(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        command_key = str(idempotency_key or "").strip()
        if not _IDEMPOTENCY_KEY_RE.fullmatch(command_key):
            raise PresenterConflict(
                "presenter.delivery_idempotency_key_required",
                "La preparación exige una clave idempotente válida",
            )
        try:
            exact_channel = PresenterDeliveryChannel(str(channel)).value
        except ValueError as exc:
            raise PresenterConflict(
                "presenter.delivery_channel_invalid",
                "Canal de presentación no admitido",
            ) from exc
        manual_email: dict[str, Any] | None = None
        correspondence_draft: dict[str, Any] | None = None
        portal_preparation_snapshot: dict[str, Any] | None = None
        if exact_channel == PresenterDeliveryChannel.PORTAL.value:
            if recipient_email or recipient_confirmed or correspondence is not None:
                raise PresenterConflict(
                    "presenter.delivery_email_not_allowed_for_portal",
                    "Una presentación en sede no admite datos de correspondencia",
                )
        else:
            if portal_preparation is not None:
                raise PresenterConflict(
                    "presenter.portal_preparation_not_allowed_for_email",
                    "RTM Correspondencia no admite una hoja de sede",
                )
            manual_email = _operator_email_destination(
                recipient_email,
                recipient_confirmed=recipient_confirmed,
            )
            correspondence_draft = _correspondence_draft(correspondence)
        current = self._now()
        package = self.repository.load_frozen_package(
            conn, case_id=case_id, package_id=package_id, for_update=True
        )
        if not package:
            raise PresenterNotFound("Paquete Presenter no encontrado")
        representation_mode = str(
            package.get("representation_mode") or ""
        ).strip().lower()
        if representation_mode not in {"self", "representative"}:
            raise PresenterConflict(
                "presenter.delivery_package_invalid",
                "La representación del paquete no es verificable",
            )
        items = self._validate_package(
            package, case_id=case_id, package_id=package_id, now=current
        )
        requirements = _json_object(package.get("destination_requirements"))
        if exact_channel == PresenterDeliveryChannel.PORTAL.value:
            portal_preparation_snapshot = _portal_preparation(
                requirements, portal_preparation
            )
        delivery_id = str(
            uuid.uuid5(
                _DELIVERY_NAMESPACE,
                f"{actor.operator_id}:{package_id}:{command_key}",
            )
        )
        self.repository.lock_delivery_command(
            conn, package_id=package_id, delivery_id=delivery_id
        )
        request_sha256 = canonical_sha256(
            {
                "case_id": case_id,
                "package_id": package_id,
                "package_manifest_sha256": str(package["manifest_sha256"]),
                "channel": exact_channel,
                "recipient_email": (
                    str(manual_email["recipient"]) if manual_email else None
                ),
                "correspondence": correspondence_draft,
                "portal_preparation": portal_preparation_snapshot,
                "operator_id": actor.operator_id,
            }
        )
        previous = self.repository.list_delivery_events(
            conn,
            case_id=case_id,
            package_id=package_id,
            delivery_id=delivery_id,
        )
        if previous:
            snapshot = self._snapshot_from_event(previous[0])
            if snapshot.get("request_sha256") != request_sha256:
                raise PresenterConflict(
                    "presenter.delivery_idempotency_key_reused",
                    "La clave idempotente pertenece a otra preparación",
                )
            return snapshot

        if exact_channel == PresenterDeliveryChannel.PORTAL.value:
            destination = {
                "kind": "verified_portal_origin",
                "portal_origin": normalize_origin(package.get("portal_origin")),
            }
            mode = "operator_prepared_signer_local_bridge"
            next_action = (
                "signer_local_activation_ready"
                if self.runtime.managed_extension_attestation_enabled
                else "managed_signing_bridge_activation_required"
            )
        else:
            verified_email = _email_destination(requirements)
            destination = (
                {
                    **manual_email,
                    "official_profile_recipient": verified_email["recipient"],
                    **{
                        key: verified_email[key]
                        for key in (
                            "legal_entity_name",
                            "entity_role",
                            "channel_status",
                            "official_source_label",
                            "official_source_url",
                            "recommended_evidence_channel",
                            "sensitive_attachment_policy",
                        )
                    },
                }
                if manual_email
                else verified_email
            )
            mode = "server_side_email_from_custody"
            next_action = (
                "recipient_verification_required"
                if manual_email
                else "step_up_and_send_blocked_in_staging"
            )

        correspondence_snapshot = None
        if exact_channel == PresenterDeliveryChannel.EMAIL.value:
            assert correspondence_draft is not None
            correspondence_snapshot = {
                "sender": RTM_CORRESPONDENCE_SENDER,
                "recipient": str(destination["recipient"]),
                "subject": correspondence_draft["subject"],
                "body": correspondence_draft["body"],
                "template_code": verified_email["template_code"],
                "template_version": verified_email["template_version"],
                "confirmations": correspondence_draft["confirmations"],
                "attachments": [
                    {
                        "package_item_id": item["package_item_id"],
                        "document_version_id": item["document_version_id"],
                        "document_sha256": item["document_sha256"],
                        "filename": item["portal_filename"],
                    }
                    for item in items
                ],
                "transport_evidence": {
                    "message_id": None,
                    "smtp_response": None,
                    "server_accepted": False,
                    "delivery_receipt_proven": False,
                    "bounce_status": None,
                    "reply_recorded": False,
                    "claim_reference": None,
                },
            }

        snapshot = {
            "delivery_contract_version": RTM_PRESENTER_DELIVERY_VERSION,
            "delivery_id": delivery_id,
            "case_id": case_id,
            "package_id": package_id,
            "package_manifest_sha256": str(package["manifest_sha256"]),
            "destination_profile_id": str(package["destination_profile_id"]),
            "destination_profile_code": str(package.get("profile_code") or ""),
            "destination_profile_version": package.get("profile_version"),
            "destination_profile_sha256": str(package["profile_sha256"]),
            "destination_display_name": str(
                package.get("destination_display_name") or package.get("profile_code")
            ),
            "representation_mode": representation_mode,
            "channel": exact_channel,
            "mode": mode,
            "state": (
                PresenterDeliveryState.AWAITING_SIGNATURE.value
                if exact_channel == PresenterDeliveryChannel.PORTAL.value
                else PresenterDeliveryState.PREPARED.value
            ),
            "destination": destination,
            **(
                {"portal_preparation": portal_preparation_snapshot}
                if portal_preparation_snapshot is not None
                else {}
            ),
            **(
                {"correspondence": correspondence_snapshot}
                if correspondence_snapshot is not None
                else {}
            ),
            "items": list(items),
            "prepared_at": current.isoformat(),
            "prepared_by_operator_id": actor.operator_id,
            "request_sha256": request_sha256,
            "external_effects_allowed": False,
            "authoritative_submission": False,
            "local_files_created": False,
            "operator_download_available": False,
            "automatic_retry_allowed": False,
            "human_final_submit_required": True,
            "receipt_required": True,
            **(
                {
                    "signature_queue_ready": True,
                    "signing_controls": {
                        "certificate_stored_by_rtm": False,
                        "certificate_secret_allowed": False,
                        "browser_session_shared_with_operator": False,
                        "remote_desktop_required": False,
                        "local_signer_activation_required": True,
                        "final_review_required": True,
                        "signature_automated": False,
                        "final_submit_automated": False,
                    },
                }
                if exact_channel == PresenterDeliveryChannel.PORTAL.value
                else {}
            ),
            "next_action": next_action,
        }
        self.repository.append_audit(
            conn,
            event_type="presenter.delivery.prepared",
            reason_code="external_effects_not_started",
            actor=actor,
            case_id=case_id,
            package_id=package_id,
            payload=snapshot,
        )
        return snapshot

    def signature_queue(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Devuelve la cola del actor sin concederle sesión ni firma remota."""

        self._open(conn)
        authorize_delivery_prepare(actor)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise PresenterConflict(
                "presenter.signature_queue_limit_invalid",
                "El límite de la cola de firma no es válido",
            )
        try:
            events = self.repository.list_signature_queue_events(
                conn,
                operator_id=actor.operator_id,
                limit=limit,
            )
        except Exception as exc:
            raise PresenterServiceError(
                "presenter.signature_queue_unavailable",
                "No se pudo consultar la cola de firma",
                status_code=503,
            ) from exc

        entries: list[dict[str, Any]] = []
        seen_delivery_ids: set[str] = set()
        for event in events:
            snapshot = self._snapshot_from_event(event)
            try:
                delivery_id = str(uuid.UUID(str(snapshot.get("delivery_id") or "")))
                case_id = str(uuid.UUID(str(snapshot.get("case_id") or "")))
                package_id = str(uuid.UUID(str(snapshot.get("package_id") or "")))
                prepared_by_operator_id = str(
                    uuid.UUID(str(snapshot.get("prepared_by_operator_id") or ""))
                )
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterConflict(
                    "presenter.signature_queue_history_invalid",
                    "La cola contiene una tarea no verificable",
                ) from exc
            items = snapshot.get("items")
            controls = snapshot.get("signing_controls")
            destination = " ".join(
                str(snapshot.get("destination_display_name") or "").split()
            )
            prepared_at = _aware(snapshot.get("prepared_at"), "prepared_at")
            if (
                delivery_id in seen_delivery_ids
                or snapshot.get("channel") != PresenterDeliveryChannel.PORTAL.value
                or snapshot.get("state")
                != PresenterDeliveryState.AWAITING_SIGNATURE.value
                or snapshot.get("signature_queue_ready") is not True
                or not isinstance(items, list)
                or not items
                or not destination
                or not isinstance(controls, Mapping)
                or controls.get("certificate_stored_by_rtm") is not False
                or controls.get("certificate_secret_allowed") is not False
                or controls.get("browser_session_shared_with_operator") is not False
                or controls.get("local_signer_activation_required") is not True
                or controls.get("signature_automated") is not False
                or controls.get("final_submit_automated") is not False
            ):
                raise PresenterConflict(
                    "presenter.signature_queue_history_invalid",
                    "La cola contiene una tarea no verificable",
                )
            entries.append(
                {
                    "delivery_id": delivery_id,
                    "case_id": case_id,
                    "package_id": package_id,
                    "destination_display_name": destination,
                    "prepared_at": prepared_at.isoformat(),
                    "prepared_by_operator_id": prepared_by_operator_id,
                    "document_count": len(items),
                    "state": PresenterDeliveryState.AWAITING_SIGNATURE.value,
                    "authoritative_submission": False,
                    "local_signer_activation_required": True,
                    "local_activation_available": (
                        self.runtime.managed_extension_attestation_enabled is True
                    ),
                    "certificate_stored_by_rtm": False,
                    "browser_session_shared": False,
                }
            )
            seen_delivery_ids.add(delivery_id)
        return {
            "queue_contract_version": RTM_PRESENTER_SIGNATURE_QUEUE_VERSION,
            "state": PresenterDeliveryState.AWAITING_SIGNATURE.value,
            "items": entries,
            "item_count": len(entries),
            "certificate_stored_by_rtm": False,
            "browser_session_shared": False,
            "local_activation_available": (
                self.runtime.managed_extension_attestation_enabled is True
            ),
        }

    def status(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        package_id: str,
        delivery_id: str,
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_delivery_prepare(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        try:
            exact_delivery_id = str(uuid.UUID(str(delivery_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise PresenterNotFound("Entrega Presenter no encontrada") from exc
        events = self.repository.list_delivery_events(
            conn,
            case_id=case_id,
            package_id=package_id,
            delivery_id=exact_delivery_id,
        )
        if not events:
            raise PresenterNotFound("Entrega Presenter no encontrada")
        snapshot = self._snapshot_from_event(events[0])
        if snapshot.get("prepared_by_operator_id") != actor.operator_id:
            raise PresenterForbidden()
        return snapshot


__all__ = [
    "RTM_CORRESPONDENCE_SENDER",
    "RTM_PRESENTER_DELIVERY_VERSION",
    "RTM_PRESENTER_SIGNATURE_QUEUE_VERSION",
    "PresenterDeliveryChannel",
    "PresenterDeliveryService",
    "PresenterDeliveryState",
]
