"""Registro de especialistas que transforman autoridad en Previa Jurídica.

Los adaptadores reciben hechos congelados y familia bloqueada. No leen OCR
crudo, no consumen clasificaciones legacy y no pueden modificar hechos ni
familia. Cada especialista devuelve un ``LegalPreview`` estructurado.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Optional

from fastapi import HTTPException

from ai.infractions.temeraria import build_temeraria_strong_template
from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
from rtm_core.contracts import (
    Deadline,
    DocumentUse,
    LegalArgument,
    LegalPreview,
    MissingItem,
    MissingItemSeverity,
    PreviewStatus,
)


SPECIALIST_REGISTRY_VERSION = "rtm_specialist_registry_v1_1"

SpecialistBuilder = Callable[
    [ValidatedFactsRecord, FamilyResolutionRecord],
    LegalPreview,
]

_COMMON_SPECIALIST_FACT_KEYS = {
    "expediente_ref",
    "numero_expediente",
    "expediente",
    "organismo",
    "organo",
    "emisor",
    "administracion_emisora",
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_imputado",
    "hecho_validado",
    "conducta_imputada",
    "descripcion_infraccion",
    "descripcion_hecho",
    "literal_denuncia",
    "observacion_agente_validada",
    "sancion_importe_eur",
    "importe",
    "puntos_detraccion",
    "puntos",
    "fecha_infraccion",
    "lugar_infraccion",
    "matricula",
    "fase_procedimental",
    "tipo_documento",
    "documento_tipo",
    "acto_notificado",
    "tramite_detectado",
    "fecha_limite",
    "fecha_vencimiento",
    "deadline_main",
    "plazo_fin",
    "articulo_infringido_num",
    "apartado_infringido_num",
    "norma_hint",
    "preceptos_detectados",
}

_UNSAFE_KEY_TOKENS = {
    "raw",
    "ocr",
    "vision",
    "formulario",
    "template",
    "prompt",
    "classifier",
    "classification",
    "scoring",
    "draft",
    "recommended",
    "strategy",
    "modelo_defensa",
}
_UNSAFE_EXACT_KEYS = {
    "tipo_infraccion",
    "familia_resuelta",
    "specialist_dispatch",
    "classifier_result",
    "classification",
    "resultado_estrategico",
    "recommended_action",
    "modelo_defensa",
}

_FACT_LABELS = {
    "expediente_ref": "Número de expediente",
    "numero_expediente": "Número de expediente",
    "organismo": "Organismo",
    "organo": "Órgano",
    "hecho_denunciado_literal": "Hecho denunciado",
    "hecho_denunciado_resumido": "Hecho denunciado",
    "hecho_imputado": "Hecho imputado",
    "sancion_importe_eur": "Importe de la sanción",
    "importe": "Importe",
    "puntos_detraccion": "Puntos",
    "puntos": "Puntos",
    "fecha_infraccion": "Fecha de la infracción",
    "lugar_infraccion": "Lugar de la infracción",
    "matricula": "Matrícula",
    "fase_procedimental": "Fase procedimental",
    "tipo_documento": "Tipo de documento",
    "fecha_limite": "Fecha límite",
    "fecha_vencimiento": "Fecha de vencimiento",
    "deadline_main": "Fecha de vencimiento",
}

_SUMMARY_KEYS = [
    "expediente_ref",
    "numero_expediente",
    "organismo",
    "organo",
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_imputado",
    "sancion_importe_eur",
    "puntos_detraccion",
    "fecha_infraccion",
    "lugar_infraccion",
    "matricula",
    "fase_procedimental",
    "tipo_documento",
]
_PHASE_KEYS = (
    "fase_procedimental",
    "tipo_documento",
    "documento_tipo",
    "acto_notificado",
    "tramite_detectado",
)
_DEADLINE_KEYS = (
    "fecha_limite",
    "fecha_vencimiento",
    "deadline_main",
    "plazo_fin",
)
_ORGANISM_KEYS = ("organismo", "organo", "emisor", "administracion_emisora")
_CASE_REF_KEYS = ("expediente_ref", "numero_expediente", "expediente")
_HECHO_KEYS = (
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_imputado",
)


def _norm(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_")
    return normalized or "item"


def _safe_specialist_key(key: str) -> bool:
    normalized = str(key or "").lower().strip()
    if not normalized or normalized in _UNSAFE_EXACT_KEYS:
        return False
    if any(token in normalized for token in _UNSAFE_KEY_TOKENS):
        return False
    if normalized in _COMMON_SPECIALIST_FACT_KEYS:
        return True
    return normalized.endswith(("_literal", "_resumido", "_imputado", "_validado"))


def _primitive(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, (str, int, float, bool))]
    return None


def _fact_value(
    facts_record: ValidatedFactsRecord,
    *keys: str,
) -> tuple[Any, Optional[str]]:
    for key in keys:
        fact = facts_record.facts.facts.get(key)
        if (
            fact
            and fact.status.value == "validated"
            and fact.value not in (None, "", [], {})
        ):
            return fact.value, key
    return None, None


def _sanitized_core(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> dict[str, Any]:
    core: dict[str, Any] = {}
    for key, fact in facts_record.facts.facts.items():
        if fact.status.value != "validated" or not _safe_specialist_key(key):
            continue
        value = _primitive(fact.value)
        if value not in (None, "", [], {}):
            core[key] = value

    # Estos dos campos no proceden del extractor: se añaden únicamente desde la
    # resolución de familia ya bloqueada para orientar al especialista correcto.
    core["familia_resuelta"] = family_record.resolution.family
    core["tipo_infraccion"] = family_record.resolution.family
    return core


def _evidence_keys(family_record: FamilyResolutionRecord) -> list[str]:
    keys: list[str] = []
    for evidence in family_record.resolution.evidence:
        for key in evidence.source_fact_keys:
            if key not in keys:
                keys.append(key)
    return keys


def _summary(facts_record: ValidatedFactsRecord) -> tuple[list[str], list[str]]:
    summaries: list[str] = []
    used_keys: list[str] = []
    for key in _SUMMARY_KEYS:
        fact = facts_record.facts.facts.get(key)
        if not fact or fact.status.value != "validated":
            continue
        value = _primitive(fact.value)
        if value in (None, "", [], {}):
            continue
        display = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
        label = _FACT_LABELS.get(key, key.replace("_", " ").capitalize())
        sentence = f"{label}: {display}."
        if sentence not in summaries:
            summaries.append(sentence)
            used_keys.append(key)
    return summaries, used_keys


def _document_uses(facts_record: ValidatedFactsRecord) -> list[DocumentUse]:
    pages_by_doc: dict[str, set[int]] = {
        str(document_id): set()
        for document_id in facts_record.facts.source_document_ids
    }
    for fact in facts_record.facts.facts.values():
        for source in fact.sources:
            pages_by_doc.setdefault(source.document_id, set())
            if source.page_index is not None:
                pages_by_doc[source.document_id].add(source.page_index)
    return [
        DocumentUse(
            document_id=document_id,
            label=f"Documento de origen {document_id}",
            status="validated",
            pages_used=sorted(pages),
        )
        for document_id, pages in sorted(pages_by_doc.items())
    ]


def _critical_missing_items(
    facts_record: ValidatedFactsRecord,
) -> list[MissingItem]:
    groups = [
        ("organismo", _ORGANISM_KEYS),
        ("expediente", _CASE_REF_KEYS),
        ("hecho_imputado", _HECHO_KEYS),
    ]
    items: list[MissingItem] = []
    for code, keys in groups:
        value, _ = _fact_value(facts_record, *keys)
        if value in (None, "", [], {}):
            items.append(
                MissingItem(
                    code=code,
                    description=f"Falta validar {code.replace('_', ' ')}.",
                    severity=MissingItemSeverity.BLOCKING,
                )
            )

    existing = {item.code for item in items}
    for value in facts_record.facts.unresolved:
        code = f"unresolved_{_slug(str(value))}"[:120]
        if code not in existing:
            items.append(
                MissingItem(
                    code=code,
                    description=f"Dato no resuelto: {value}",
                    severity=MissingItemSeverity.HUMAN_REVIEW,
                )
            )
            existing.add(code)
    for value in facts_record.facts.conflicts:
        code = f"conflict_{_slug(str(value))}"[:120]
        if code not in existing:
            items.append(
                MissingItem(
                    code=code,
                    description=f"Conflicto documental: {value}",
                    severity=MissingItemSeverity.BLOCKING,
                )
            )
            existing.add(code)
    return items


def _phase_document_type(
    facts_record: ValidatedFactsRecord,
) -> tuple[str, list[MissingItem]]:
    values = []
    for key in _PHASE_KEYS:
        value, _ = _fact_value(facts_record, key)
        if value not in (None, "", [], {}):
            values.append(_norm(value))
    blob = " ".join(values)

    if any(
        token in blob
        for token in (
            "requerimiento de pago",
            "providencia de apremio",
            "via ejecutiva",
            "sancion firme",
            "resolucion firme",
        )
    ):
        return (
            "ACTUACIÓN PENDIENTE DE REVISIÓN PROCESAL",
            [
                MissingItem(
                    code="procedural_phase_incompatible_with_allegations",
                    description=(
                        "El documento parece corresponder a pago, ejecutiva o firmeza; "
                        "no puede generarse automáticamente un escrito de alegaciones."
                    ),
                    severity=MissingItemSeverity.BLOCKING,
                )
            ],
        )

    if any(
        token in blob
        for token in (
            "denuncia",
            "iniciacion",
            "incoacion",
            "propuesta de sancion",
            "tramite de audiencia",
            "alegaciones",
        )
    ):
        return "ESCRITO DE ALEGACIONES", []

    if any(token in blob for token in ("resolucion sancionadora", "resolucion definitiva")):
        return (
            "RECURSO ADMINISTRATIVO PENDIENTE DE DETERMINAR",
            [
                MissingItem(
                    code="appeal_type_unresolved",
                    description=(
                        "Consta una resolución sancionadora, pero debe validarse el "
                        "recurso procedente y su plazo antes de generar."
                    ),
                    severity=MissingItemSeverity.BLOCKING,
                )
            ],
        )

    return (
        "ACTUACIÓN PENDIENTE DE DETERMINAR",
        [
            MissingItem(
                code="procedural_phase_unresolved",
                description="No consta validada la fase procesal ni el tipo de escrito procedente.",
                severity=MissingItemSeverity.BLOCKING,
            )
        ],
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("/", "-"), raw.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
    if match:
        day, month, year = (int(piece) for piece in match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)
    return None


def _deadline(
    facts_record: ValidatedFactsRecord,
) -> tuple[list[Deadline], list[MissingItem]]:
    for key in _DEADLINE_KEYS:
        value, found_key = _fact_value(facts_record, key)
        parsed = _parse_datetime(value)
        if parsed and found_key:
            return (
                [
                    Deadline(
                        label="Plazo para presentar el escrito",
                        due_at=parsed,
                        calculation_status="confirmed",
                        source_fact_keys=[found_key],
                    )
                ],
                [],
            )
    return (
        [
            Deadline(
                label="Plazo para presentar el escrito",
                due_at=None,
                calculation_status="unresolved",
                notes=["OPS debe validar el vencimiento antes del freeze."],
            )
        ],
        [
            MissingItem(
                code="deadline_unresolved",
                description="No consta una fecha de vencimiento validada.",
                severity=MissingItemSeverity.BLOCKING,
            )
        ],
    )


def _parse_allegations(
    body: str,
    source_fact_keys: list[str],
) -> list[LegalArgument]:
    normalized = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(
        r"ALEGACIÓN\s+[^—\n]+\s+—\s+([^\n]+)\n\n([\s\S]*?)"
        r"(?=\nALEGACIÓN\s+[^—\n]+\s+—|\nFUNDAMENTOS DE DERECHO|\nS\s+U\s+P\s+L\s+I\s+C\s+A)",
        flags=re.IGNORECASE,
    )
    basis = [
        "Artículos 24 y 25 de la Constitución Española.",
        "Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común.",
        "Principios de presunción de inocencia, tipicidad y prueba suficiente.",
    ]
    arguments: list[LegalArgument] = []
    for index, match in enumerate(pattern.finditer(normalized), start=1):
        title = re.sub(r"\s+", " ", match.group(1)).strip(" .")
        argument_body = re.sub(r"\n{3,}", "\n\n", match.group(2)).strip()
        if not title or not argument_body:
            continue
        title_norm = _norm(title)
        priority = (
            "primary"
            if index == 1
            else "subsidiary"
            if any(
                token in title_norm
                for token in ("subsidiaria", "recalificacion", "proporcionalidad")
            )
            else "secondary"
        )
        arguments.append(
            LegalArgument(
                code=f"temeraria_{index}_{_slug(title)}"[:120],
                title=title,
                body=argument_body,
                priority=priority,
                source_fact_keys=source_fact_keys,
                legal_basis=basis,
            )
        )
    if not arguments:
        raise HTTPException(
            status_code=500,
            detail="El especialista Temeraria no devolvió alegaciones estructurables.",
        )
    return arguments


def build_temeraria_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    resolution = family_record.resolution
    if resolution.family != "temeraria" or resolution.specialist != "traffic.temeraria":
        raise HTTPException(status_code=409, detail="La familia no corresponde a Temeraria")

    evidence_keys = _evidence_keys(family_record)
    if not evidence_keys:
        raise HTTPException(status_code=409, detail="La familia no conserva hechos de evidencia")

    template = build_temeraria_strong_template(
        _sanitized_core(facts_record, family_record)
    )
    arguments = _parse_allegations(template.get("cuerpo") or "", evidence_keys)
    summary, summary_keys = _summary(facts_record)
    source_keys = list(dict.fromkeys([*evidence_keys, *summary_keys]))

    organismo, _ = _fact_value(facts_record, *_ORGANISM_KEYS)
    expediente, _ = _fact_value(facts_record, *_CASE_REF_KEYS)
    hecho, _ = _fact_value(facts_record, *_HECHO_KEYS)
    document_type, phase_missing = _phase_document_type(facts_record)
    deadlines, deadline_missing = _deadline(facts_record)

    missing = _critical_missing_items(facts_record)
    existing = {item.code for item in missing}
    for item in [*phase_missing, *deadline_missing]:
        if item.code not in existing:
            missing.append(item)
            existing.add(item.code)

    destination = (
        str(organismo).strip()
        if organismo not in (None, "", [], {})
        else "ÓRGANO SANCIONADOR PENDIENTE DE VALIDAR"
    )
    subject = (
        f"{document_type} — expediente {expediente}"
        if expediente not in (None, "", [], {})
        else f"{document_type} — expediente pendiente de validar"
    )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="temeraria",
        specialist="traffic.temeraria",
        facts_version=facts_record.facts.version,
        family_resolution_version=resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se atribuye al interesado la conducta siguiente: {hecho}."
            if hecho not in (None, "", [], {})
            else "La conducta imputada está pendiente de validación documental."
        ),
        client_goal=(
            "Obtener el archivo del expediente o, subsidiariamente, una "
            "recalificación conforme a la prueba acreditada."
        ),
        primary_strategy=(
            "Exigir concreción reforzada de la maniobra y prueba objetiva del "
            "riesgo real, concreto e individualizado que justificaría la "
            "calificación de conducción temeraria."
        ),
        secondary_strategies=[
            "Diferenciar una eventual infracción formal de una verdadera conducción temeraria.",
            "Solicitar subsidiariamente recalificación y sanción mínima sin reconocer los hechos.",
        ],
        requested_outcomes=[
            "Que se acuerde el archivo del expediente por insuficiencia probatoria o falta de motivación individualizada.",
            "Subsidiariamente, que se recalifiquen los hechos según la prueba realmente acreditada.",
            "Subsidiariamente, que se aplique la sanción mínima legalmente procedente.",
        ],
        documents_used=_document_uses(facts_record),
        missing_items=missing,
        deadlines=deadlines,
        risks=[
            "La imputación exige verificar la descripción exacta de la maniobra y del riesgo concreto.",
            "La fase procesal y el plazo deben estar validados antes del freeze.",
        ],
        destination=destination,
        document_type=document_type,
        subject=subject,
        legal_arguments=arguments,
        additional_requests=[
            "Que se facilite el expediente íntegro y toda la prueba objetiva disponible para contradicción efectiva.",
            "Que se tengan por reservados los recursos y acciones que correspondan.",
        ],
        created_by_component="traffic.temeraria",
    )


_REGISTRY: dict[str, SpecialistBuilder] = {
    "traffic.temeraria": build_temeraria_preview,
}


def registered_specialists() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_legal_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    specialist = str(family_record.resolution.specialist or "").strip()
    builder = _REGISTRY.get(specialist)
    if not builder:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El especialista resuelto todavía no dispone de adaptador LegalPreview.",
                "specialist": specialist or None,
                "registered_specialists": list(registered_specialists()),
                "requires_operator_review": True,
            },
        )
    return builder(facts_record, family_record)
