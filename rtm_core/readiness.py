"""Evaluación común del expediente mínimo antes del pago de estudio RTM."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rtm_core.service_catalog import ReviewQuote, resolve_review_quote


REVIEW_READINESS_VERSION = "rtm_review_readiness_v1_0"


class ReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    area: Literal["data", "identity", "authorization", "documents", "service"]
    blocking: bool


class ReviewReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: Literal["rtm_review_readiness"] = "rtm_review_readiness"
    version: str = REVIEW_READINESS_VERSION
    case_id: str = Field(min_length=1)
    ready: bool
    quote: ReviewQuote
    blocking_issues: list[ReadinessIssue] = Field(default_factory=list)
    warnings: list[ReadinessIssue] = Field(default_factory=list)
    received_document_kinds: list[str] = Field(default_factory=list)


_DOCUMENT_ALIASES = {
    "identity_front": {"identity_front", "dni_front", "id_front"},
    "identity_back": {"identity_back", "dni_back", "id_back"},
    "authorization_signed": {
        "authorization_signed",
        "signed_authorization",
        "autorizacion_firmada",
    },
    "main_document": {
        "original",
        "main_document",
        "primary_document",
        "document_original",
    },
}


def _has_value(mapping: dict[str, Any], *keys: str) -> bool:
    return any(mapping.get(key) not in (None, "", [], {}) for key in keys)


def _has_document(document_kinds: set[str], requirement: str) -> bool:
    return bool(document_kinds.intersection(_DOCUMENT_ALIASES[requirement]))


def _issue(
    code: str,
    message: str,
    area: Literal["data", "identity", "authorization", "documents", "service"],
    *,
    blocking: bool,
) -> ReadinessIssue:
    return ReadinessIssue(code=code, message=message, area=area, blocking=blocking)


def evaluate_review_readiness(
    *,
    case_id: str,
    interested_data: dict[str, Any] | None,
    authorized: bool,
    document_kinds: list[str] | set[str] | tuple[str, ...] | None,
    department: str | None,
    case_type: str | None = None,
    category: str | None = None,
    source_module: str | None = None,
    contact_email: str | None = None,
    customer_comment: str | None = None,
) -> ReviewReadiness:
    """Devuelve una sola decisión de preparación y la tarifa autoritativa.

    Los expedientes creados por el nuevo flujo RTM (`source_module=rtm_web`)
    deben contener datos, DNI por ambas caras, autorización firmada y al menos
    un documento principal antes de pagar. Los expedientes legacy conservan
    compatibilidad: la falta de DNI subido se advierte a OPS, pero no bloquea
    por sí sola el checkout; autorización firmada y documento principal sí son
    siempre obligatorios.
    """

    interested = interested_data if isinstance(interested_data, dict) else {}
    kinds = {
        str(kind or "").strip().lower()
        for kind in (document_kinds or [])
        if str(kind or "").strip()
    }
    source = str(source_module or interested.get("source_module") or "").strip().lower()
    is_core_intake = source == "rtm_web"
    quote = resolve_review_quote(department, case_type, category)

    blocking: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []

    required_data = (
        ("full_name", "Falta el nombre y apellidos", ("full_name", "name")),
        ("dni_nie", "Falta el DNI/NIE", ("dni_nie", "dni", "identity_number")),
        (
            "domicilio_notif",
            "Falta el domicilio de notificaciones",
            ("domicilio_notif", "domicilio", "address"),
        ),
    )
    for code, message, keys in required_data:
        if not _has_value(interested, *keys):
            blocking.append(_issue(code, message, "data", blocking=True))

    persisted_email = str(contact_email or "").strip()
    if not _has_value(interested, "email") and not persisted_email:
        blocking.append(_issue("email", "Falta el correo electrónico", "data", blocking=True))

    explanation = str(
        customer_comment
        or interested.get("customer_comment")
        or interested.get("explanation")
        or ""
    ).strip()
    if is_core_intake and not explanation:
        blocking.append(
            _issue(
                "customer_comment",
                "Falta la explicación inicial del asunto",
                "data",
                blocking=True,
            )
        )

    for requirement, message in (
        ("identity_front", "Falta el anverso del documento de identidad"),
        ("identity_back", "Falta el reverso del documento de identidad"),
    ):
        if not _has_document(kinds, requirement):
            target = blocking if is_core_intake else warnings
            target.append(
                _issue(
                    requirement,
                    message,
                    "identity",
                    blocking=is_core_intake,
                )
            )

    if not authorized:
        blocking.append(
            _issue(
                "authorization_state",
                "El expediente no consta autorizado",
                "authorization",
                blocking=True,
            )
        )

    if not _has_document(kinds, "authorization_signed"):
        blocking.append(
            _issue(
                "authorization_signed",
                "Falta la autorización firmada",
                "authorization",
                blocking=True,
            )
        )

    if not _has_document(kinds, "main_document"):
        blocking.append(
            _issue(
                "main_document",
                "Falta el documento principal del asunto",
                "documents",
                blocking=True,
            )
        )

    return ReviewReadiness(
        case_id=case_id,
        ready=not blocking,
        quote=quote,
        blocking_issues=blocking,
        warnings=warnings,
        received_document_kinds=sorted(kinds),
    )
