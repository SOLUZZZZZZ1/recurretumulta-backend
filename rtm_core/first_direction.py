"""Proyección común del primer rumbo de un expediente RTM.

No es una segunda autoridad. Cuando existe Previa Jurídica, la proyecta. Cuando
el especialista todavía no está disponible, muestra una orientación operativa
derivada del servicio, la familia y los hechos validados, pero mantiene Generate
bloqueado y exige revisión OPS.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from rtm_core.domain_catalog import (
    DOMAIN_CATALOG_VERSION,
    family_profile,
    service_profile,
)
from rtm_core.service_catalog import canonical_department


FIRST_DIRECTION_VERSION = "rtm_first_direction_projection_v1_0"


class FirstDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority: Literal["rtm_first_direction_projection"] = (
        "rtm_first_direction_projection"
    )
    version: str = FIRST_DIRECTION_VERSION
    catalog_version: str = DOMAIN_CATALOG_VERSION
    case_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    service_label: str = Field(min_length=1)
    family_status: str = "pending"
    family: Optional[str] = None
    family_label: Optional[str] = None
    specialist: Optional[str] = None
    specialist_available: bool = False
    source: Literal["core_projection", "legal_preview"] = "core_projection"
    maturity: Literal[
        "intake_pending",
        "facts_pending",
        "family_pending",
        "orientation_only",
        "legal_preview_draft",
        "legal_preview_review",
        "legal_preview_approved",
        "legal_preview_frozen",
    ]
    authoritative: bool = False
    generation_allowed: bool = False
    what_we_found: list[str] = Field(default_factory=list)
    how_we_understand_case: str = Field(min_length=1)
    primary_direction: str = Field(min_length=1)
    alternatives: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    deadlines: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    requested_outcomes: list[str] = Field(default_factory=list)
    next_action: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _clean(value: Any, *, limit: int = 240) -> str:
    text_value = " ".join(str(value or "").replace("\r", " ").split())
    if len(text_value) > limit:
        return text_value[: limit - 3] + "..."
    return text_value


def _label(key: str) -> str:
    labels = {
        "organismo": "Organismo",
        "expediente_ref": "Expediente",
        "hecho_denunciado_literal": "Hecho",
        "descripcion_hecho": "Hecho",
        "sancion_importe_eur": "Importe",
        "importe_reclamado_eur": "Importe reclamado",
        "importe_deuda_eur": "Importe de la deuda",
        "puntos_detraccion": "Puntos",
        "fecha_documento": "Fecha del documento",
        "fecha_notificacion": "Fecha de notificación",
        "fecha_limite": "Fecha límite",
        "fecha_vencimiento": "Vencimiento",
        "fase_procedimental": "Fase",
        "tipo_documento": "Documento",
        "proveedor": "Proveedor",
        "acreedor": "Acreedor",
        "deudor": "Deudor",
        "numero_reserva": "Reserva",
        "numero_vuelo": "Vuelo",
    }
    return labels.get(key, key.replace("_", " ").strip().capitalize())


def _facts_contract(record: Any) -> Mapping[str, Any]:
    contract = _value(record, "facts", {})
    return contract if isinstance(contract, Mapping) else {}


def _facts_map(record: Any) -> Mapping[str, Any]:
    contract = _facts_contract(record)
    values = contract.get("facts", {})
    return values if isinstance(values, Mapping) else {}


def _fact_status(fact: Any) -> str:
    return str(_enum_value(_value(fact, "status", "")) or "")


def _validated_findings(record: Any, *, limit: int = 14) -> list[str]:
    findings: list[str] = []
    for key, fact in _facts_map(record).items():
        if _fact_status(fact) != "validated":
            continue
        value = _value(fact, "value")
        if value in (None, "", [], {}):
            continue
        rendered = _clean(value)
        if not rendered:
            continue
        findings.append(f"{_label(str(key))}: {rendered}")
        if len(findings) >= limit:
            break
    return findings


def _unresolved_items(record: Any) -> list[str]:
    result: list[str] = []
    contract = _facts_contract(record)
    declared = contract.get("unresolved", [])
    if isinstance(declared, list):
        result.extend(f"Hecho pendiente: {_label(str(item))}" for item in declared)
    for key, fact in _facts_map(record).items():
        status = _fact_status(fact)
        if status == "unresolved":
            item = f"Hecho pendiente: {_label(str(key))}"
            if item not in result:
                result.append(item)
        elif status == "conflicted":
            conflicts = _value(fact, "conflicts", [])
            if isinstance(conflicts, list) and conflicts:
                result.extend(_clean(value) for value in conflicts if _clean(value))
            else:
                result.append(f"Lecturas en conflicto: {_label(str(key))}")
    return list(dict.fromkeys(result))


def _fact_conflicts(record: Any) -> list[str]:
    contract = _facts_contract(record)
    values = contract.get("conflicts", [])
    if not isinstance(values, list):
        return []
    return [_clean(item) for item in values if _clean(item)]


def _fact_deadlines(record: Any) -> list[dict[str, Any]]:
    deadlines: list[dict[str, Any]] = []
    for key, fact in _facts_map(record).items():
        key_low = str(key).lower()
        if not any(
            token in key_low
            for token in ("fecha_limite", "fecha_vencimiento", "deadline", "plazo_fin")
        ):
            continue
        if _fact_status(fact) != "validated":
            continue
        value = _value(fact, "value")
        if value in (None, "", [], {}):
            continue
        deadlines.append(
            {
                "label": _label(str(key)),
                "value": _clean(value),
                "status": "confirmed_from_validated_fact",
                "source_fact_key": str(key),
            }
        )
    return deadlines


def _resolution(record: Any) -> Mapping[str, Any]:
    value = _value(record, "resolution", {})
    return value if isinstance(value, Mapping) else {}


def _preview_contract(record: Any) -> Mapping[str, Any]:
    value = _value(record, "preview", {})
    return value if isinstance(value, Mapping) else {}


def _readiness_missing(readiness: Mapping[str, Any]) -> list[str]:
    values = readiness.get("blocking_issues", [])
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            message = _clean(item.get("message"))
        else:
            message = _clean(item)
        if message:
            result.append(message)
    return result


def _preview_deadlines(preview: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    values = preview.get("deadlines", [])
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "label": item.get("label"),
                "value": item.get("due_at"),
                "status": item.get("calculation_status"),
                "source_fact_keys": item.get("source_fact_keys", []),
                "notes": item.get("notes", []),
            }
        )
    return result


def _preview_missing(preview: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    values = preview.get("missing_items", [])
    if not isinstance(values, list):
        return result
    for item in values:
        if isinstance(item, Mapping):
            description = _clean(item.get("description"))
            severity = _clean(item.get("severity"), limit=40)
            if description:
                result.append(
                    f"{description} ({severity})" if severity else description
                )
        elif _clean(item):
            result.append(_clean(item))
    return result


def _primary_action_label(next_step: Mapping[str, Any]) -> str:
    actions = next_step.get("actions", [])
    primary = str(next_step.get("primary_action") or "")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, Mapping) and str(action.get("code") or "") == primary:
                label = _clean(action.get("label"))
                if label:
                    return label
    return _clean(primary.replace("_", " ")) or "Revisión OPS del expediente"


def build_first_direction(
    *,
    case_id: str,
    case_payload: Mapping[str, Any],
    readiness: Mapping[str, Any],
    latest_facts: Any,
    latest_family: Any,
    latest_preview: Any,
    next_step: Mapping[str, Any],
    registered_specialists: tuple[str, ...] = (),
) -> FirstDirection:
    department = canonical_department(
        case_payload.get("department"),
        case_payload.get("case_type"),
        case_payload.get("category"),
    )
    service = service_profile(
        department,
        case_payload.get("case_type"),
        case_payload.get("category"),
    )
    resolution = _resolution(latest_family)
    family_status = str(_enum_value(resolution.get("status")) or "pending")
    family = str(resolution.get("family") or "").strip() or None
    specialist = str(resolution.get("specialist") or "").strip() or None
    profile = family_profile(department, family)
    specialist_available = bool(
        specialist and specialist in set(registered_specialists)
    )
    family_label = profile.label if profile else None

    findings = _validated_findings(latest_facts)
    missing = _readiness_missing(readiness)
    missing.extend(_unresolved_items(latest_facts))
    missing.extend(_fact_conflicts(latest_facts))
    deadlines = _fact_deadlines(latest_facts)
    risks: list[str] = []
    warnings: list[str] = []

    preview = _preview_contract(latest_preview)
    preview_status = str(
        _enum_value(_value(latest_preview, "status", preview.get("status", ""))) or ""
    )
    if preview and preview_status != "invalidated":
        status_to_maturity = {
            "draft": "legal_preview_draft",
            "ops_review": "legal_preview_review",
            "approved": "legal_preview_approved",
            "frozen": "legal_preview_frozen",
            "changes_required": "legal_preview_draft",
        }
        maturity = status_to_maturity.get(preview_status, "legal_preview_draft")
        preview_findings = preview.get("validated_facts_summary", [])
        if isinstance(preview_findings, list) and preview_findings:
            findings = [_clean(item) for item in preview_findings if _clean(item)]
        primary_direction = _clean(preview.get("primary_strategy")) or service.initial_direction
        alternatives = [
            _clean(item)
            for item in preview.get("secondary_strategies", [])
            if _clean(item)
        ] if isinstance(preview.get("secondary_strategies"), list) else []
        missing.extend(_preview_missing(preview))
        deadlines = _preview_deadlines(preview) or deadlines
        risks.extend(
            _clean(item)
            for item in preview.get("risks", [])
            if _clean(item)
        ) if isinstance(preview.get("risks"), list) else None
        requested = [
            _clean(item)
            for item in preview.get("requested_outcomes", [])
            if _clean(item)
        ] if isinstance(preview.get("requested_outcomes"), list) else []
        authoritative = preview_status in {"approved", "frozen"}
        blocking_missing = any("(blocking)" in item for item in missing)
        generation_allowed = (
            preview_status == "frozen"
            and specialist_available
            and not blocking_missing
        )
        if not generation_allowed:
            warnings.append(
                "La proyección no habilita Generate hasta existir una Previa Jurídica congelada y un especialista disponible."
            )
        return FirstDirection(
            case_id=case_id,
            service=department,
            service_label=service.label,
            family_status=family_status,
            family=family,
            family_label=family_label,
            specialist=specialist,
            specialist_available=specialist_available,
            source="legal_preview",
            maturity=maturity,  # type: ignore[arg-type]
            authoritative=authoritative,
            generation_allowed=generation_allowed,
            what_we_found=findings,
            how_we_understand_case=(
                _clean(preview.get("problem_summary"))
                or (
                    f"{service.label}: {family_label}."
                    if family_label
                    else f"{service.label}: familia pendiente de confirmación."
                )
            ),
            primary_direction=primary_direction,
            alternatives=alternatives,
            missing_items=list(dict.fromkeys(item for item in missing if item)),
            deadlines=deadlines,
            risks=list(dict.fromkeys(item for item in risks if item)),
            requested_outcomes=requested,
            next_action=_primary_action_label(next_step),
            warnings=warnings,
        )

    if not latest_facts:
        maturity = (
            "intake_pending"
            if not bool(readiness.get("ready"))
            else "facts_pending"
        )
    elif family_status not in {"resolved"}:
        maturity = "family_pending"
    else:
        maturity = "orientation_only"

    if family_status == "resolved" and profile:
        understanding = f"{service.label}: {profile.label}. {profile.focus}"
        primary_direction = f"{service.initial_direction} Foco inicial: {profile.focus}"
    elif family_status == "conflicted":
        understanding = (
            f"{service.label}: existen varias familias compatibles con los hechos validados."
        )
        primary_direction = (
            "Resolver el conflicto de familia y comprobar si el expediente contiene "
            "varios asuntos antes de encargar una estrategia jurídica."
        )
        risks.append("Una familia elegida prematuramente podría orientar el caso por una vía incorrecta.")
    else:
        understanding = (
            f"{service.label}: el satélite está identificado, pero la familia concreta todavía no está resuelta."
        )
        primary_direction = service.initial_direction

    alternatives = list(service.alternatives)
    if family_status != "resolved":
        missing.append("Confirmar la familia desde hechos documentales validados.")
    if specialist and not specialist_available:
        missing.append(
            f"El especialista '{specialist}' todavía no dispone de adaptador LegalPreview."
        )
        risks.append(
            "La orientación es preliminar: OPS debe revisarla y Generate permanece bloqueado."
        )
    elif family_status == "resolved" and not specialist:
        missing.append("Asignar el especialista correspondiente a la familia resuelta.")
    if not findings:
        warnings.append(
            "Todavía no hay hechos validados suficientes para resumir documentalmente el asunto."
        )
    warnings.append(
        "El primer rumbo es una proyección operativa; solo una Previa Jurídica aprobada y congelada puede alimentar Generate."
    )

    return FirstDirection(
        case_id=case_id,
        service=department,
        service_label=service.label,
        family_status=family_status,
        family=family,
        family_label=family_label,
        specialist=specialist,
        specialist_available=specialist_available,
        source="core_projection",
        maturity=maturity,  # type: ignore[arg-type]
        authoritative=False,
        generation_allowed=False,
        what_we_found=findings,
        how_we_understand_case=understanding,
        primary_direction=primary_direction,
        alternatives=alternatives,
        missing_items=list(dict.fromkeys(item for item in missing if item)),
        deadlines=deadlines,
        risks=list(dict.fromkeys(item for item in risks if item)),
        requested_outcomes=[],
        next_action=_primary_action_label(next_step),
        warnings=list(dict.fromkeys(warnings)),
    )
