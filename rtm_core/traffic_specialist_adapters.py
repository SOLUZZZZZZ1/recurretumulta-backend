"""Adaptadores LegalPreview para los especialistas congelados de Tráfico.

Este módulo envuelve, sin modificar, ``velocity_legal_v1_2`` y
``semaforo_legal_v1_0``. Recibe únicamente hechos congelados y una familia
bloqueada; no lee OCR crudo, no reclasifica y no completa datos ausentes.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Optional

from fastapi import HTTPException

from ai.infractions.semaforo import (
    SEMAFORO_LEGAL_INTELLIGENCE_VERSION,
    build_semaforo_intelligence_template,
    build_semaforo_legal_intelligence,
)
from ai.infractions.velocidad import (
    VELOCITY_LEGAL_INTELLIGENCE_VERSION,
    build_velocity_legal_intelligence,
)
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


TRAFFIC_SPECIALIST_ADAPTERS_VERSION = "rtm_traffic_specialist_adapters_v1_0"

_BASE_LEGAL_BASIS = [
    "Artículos 24 y 25 de la Constitución Española.",
    "Ley 39/2015, de 1 de octubre, del Procedimiento Administrativo Común.",
    "Principios de presunción de inocencia, tipicidad, motivación y prueba suficiente.",
]

_ORGANISM_KEYS = ("organismo", "organo", "emisor", "administracion_emisora")
_CASE_REF_KEYS = ("expediente_ref", "numero_expediente", "expediente")
_FACT_KEYS = (
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_imputado",
    "hecho_validado",
    "conducta_imputada",
)
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

_VELOCITY_CORE_MAP = {
    "organismo": "organismo",
    "expediente_ref": "expediente_ref",
    "matricula": "matricula",
    "fecha_infraccion": "fecha_infraccion",
    "lugar_infraccion": "lugar_infraccion",
    "velocidad_medida_kmh": "velocidad_medida_kmh",
    "velocidad_limite_kmh": "velocidad_limite_kmh",
    "sancion_importe_eur": "sancion_importe_eur",
    "puntos_detraccion": "puntos_detraccion",
    "radar_modelo_hint": "radar_modelo_hint",
    "radar_antena": "radar_antena",
    "vehiculo_marca_modelo": "vehicle_make_model",
    "radar_modalidad_instalacion": "radar_installation_mode",
}

_SEMAPHORE_CORE_MAP = {
    "organismo": "organismo",
    "expediente_ref": "expediente_ref",
    "matricula": "matricula",
    "fecha_infraccion": "fecha_infraccion",
    "hora_infraccion": "hora_infraccion",
    "lugar_infraccion": "lugar_infraccion",
    "sancion_importe_eur": "sancion_importe_eur",
    "puntos_detraccion": "puntos_detraccion",
    "semaforo_fase": "semaforo_fase",
}

_SUMMARY_LABELS = {
    "organismo": "Organismo",
    "expediente_ref": "Número de expediente",
    "hecho_denunciado_literal": "Hecho denunciado",
    "matricula": "Matrícula",
    "fecha_infraccion": "Fecha del hecho",
    "hora_infraccion": "Hora del hecho",
    "lugar_infraccion": "Lugar",
    "velocidad_medida_kmh": "Velocidad consignada",
    "velocidad_limite_kmh": "Límite consignado",
    "radar_modelo_hint": "Modelo de cinemómetro",
    "radar_antena": "Identificador de antena",
    "sancion_importe_eur": "Sanción ordinaria",
    "importe_reducido_eur": "Importe reducido",
    "puntos_detraccion": "Puntos",
    "metodo_captura": "Método de captación",
    "captura_automatica": "Captación automática",
    "fotografia_vehiculo_presente": "Fotografía del vehículo en la copia",
    "norma_hint": "Norma transcrita",
    "articulo_infringido_num": "Artículo transcrito",
    "fase_procedimental": "Fase procedimental",
    "tipo_documento": "Tipo de documento",
}


def _norm(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_") or "item"


def _primitive(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, (str, int, float, bool))]
    return None


def _fact(
    record: ValidatedFactsRecord,
    *keys: str,
):
    for key in keys:
        fact = record.facts.facts.get(key)
        if fact and fact.status.value == "validated" and fact.value not in (None, "", [], {}):
            return fact, key
    return None, None


def _value(record: ValidatedFactsRecord, *keys: str) -> tuple[Any, Optional[str]]:
    fact, key = _fact(record, *keys)
    return (fact.value, key) if fact else (None, None)


def _source_meta(record: ValidatedFactsRecord, key: str) -> tuple[float, Optional[str]]:
    fact = record.facts.facts.get(key)
    if not fact:
        return 0.0, None
    evidence = next(
        (source.evidence for source in fact.sources if source.evidence),
        None,
    )
    return float(fact.confidence or 0), evidence


def _validated_core(
    facts_record: ValidatedFactsRecord,
    mapping: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    core: dict[str, Any] = {}
    used: list[str] = []
    for fact_key, target_key in mapping.items():
        value, found_key = _value(facts_record, fact_key)
        if value in (None, "", [], {}) or not found_key:
            continue
        primitive = _primitive(value)
        if primitive in (None, "", [], {}):
            continue
        core[target_key] = primitive
        used.append(found_key)

    hecho, hecho_key = _value(facts_record, *_FACT_KEYS)
    if hecho not in (None, "", [], {}) and hecho_key:
        core["hecho_imputado"] = _primitive(hecho)
        core["hecho_denunciado_literal"] = _primitive(hecho)
        used.append(hecho_key)
    return core, list(dict.fromkeys(used))


def _velocity_secondary(
    facts_record: ValidatedFactsRecord,
) -> tuple[dict[str, Any], dict[str, float], dict[str, str], list[str]]:
    facts: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    evidence: dict[str, str] = {}
    used: list[str] = []

    scalar_map = {
        "fecha_verificacion_metrologica": "verification_date",
        "fecha_datos_conductor": "driver_data_date",
        "fecha_documento_incoacion": "initiation_document_date",
        "captura_automatica": "capture_automatic",
        "fotografia_vehiculo_presente": "vehicle_photo_present",
        "certificado_metrologico_reproducido": "certificate_reproduction_present",
    }
    for source_key, target_key in scalar_map.items():
        value, found = _value(facts_record, source_key)
        if value in (None, "", [], {}) or not found:
            continue
        facts[target_key] = _primitive(value)
        conf, ev = _source_meta(facts_record, found)
        confidence[target_key] = conf
        if ev:
            evidence[target_key] = ev
        used.append(found)

    norm, norm_key = _value(facts_record, "norma_hint")
    article, article_key = _value(
        facts_record,
        "articulo_infringido_num",
        "apartado_infringido_num",
    )
    if norm not in (None, "", [], {}) or article not in (None, "", [], {}):
        facts["normative_reference"] = {
            "norm": _primitive(norm),
            "article": _primitive(article),
        }
        meta_keys = [key for key in (norm_key, article_key) if key]
        confidence["normative_reference"] = max(
            (_source_meta(facts_record, key)[0] for key in meta_keys),
            default=0.0,
        )
        ev = next(
            (_source_meta(facts_record, key)[1] for key in meta_keys if _source_meta(facts_record, key)[1]),
            None,
        )
        if ev:
            evidence["normative_reference"] = ev
        used.extend(meta_keys)

    name, name_key = _value(facts_record, "document_subject_name")
    id_number, id_key = _value(facts_record, "document_subject_id")
    if name not in (None, "", [], {}) or id_number not in (None, "", [], {}):
        facts["document_subject"] = {
            "full_name": _primitive(name),
            "id_number": _primitive(id_number),
        }
        meta_keys = [key for key in (name_key, id_key) if key]
        confidence["document_subject"] = max(
            (_source_meta(facts_record, key)[0] for key in meta_keys),
            default=0.0,
        )
        ev = next(
            (_source_meta(facts_record, key)[1] for key in meta_keys if _source_meta(facts_record, key)[1]),
            None,
        )
        if ev:
            evidence["document_subject"] = ev
        used.extend(meta_keys)

    return facts, confidence, evidence, list(dict.fromkeys(used))


def _semaphore_secondary(
    facts_record: ValidatedFactsRecord,
) -> tuple[dict[str, Any], dict[str, float], dict[str, str], list[str]]:
    facts: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    evidence: dict[str, str] = {}
    used: list[str] = []

    scalar_map = {
        "metodo_captura": "capture_method",
        "captura_automatica": "capture_automatic",
        "fotografia_vehiculo_presente": "vehicle_photo_present",
        "fecha_documento": "fecha_emision",
        "fecha_limite_pago": "fecha_limite_pago",
        "sancion_importe_eur": "sancion_ordinaria_eur",
        "importe_reducido_eur": "importe_reducido_eur",
    }
    for source_key, target_key in scalar_map.items():
        value, found = _value(facts_record, source_key)
        if value in (None, "", [], {}) or not found:
            continue
        facts[target_key] = _primitive(value)
        conf, ev = _source_meta(facts_record, found)
        confidence[target_key] = conf
        if ev:
            evidence[target_key] = ev
        used.append(found)

    norm, norm_key = _value(facts_record, "norma_hint")
    article, article_key = _value(
        facts_record,
        "articulo_infringido_num",
        "apartado_infringido_num",
    )
    if norm not in (None, "", [], {}) or article not in (None, "", [], {}):
        facts["normative_reference"] = {
            "norm": _primitive(norm),
            "article": _primitive(article),
        }
        for key in (norm_key, article_key):
            if key:
                used.append(key)
                conf, ev = _source_meta(facts_record, key)
                confidence[key] = conf
                if ev:
                    evidence[key] = ev

    name, name_key = _value(facts_record, "document_subject_name")
    id_number, id_key = _value(facts_record, "document_subject_id")
    if name not in (None, "", [], {}) or id_number not in (None, "", [], {}):
        facts["document_subject"] = {
            "full_name": _primitive(name),
            "id_number": _primitive(id_number),
        }
        for key, target in ((name_key, "document_subject_name"), (id_key, "document_subject_id")):
            if key:
                used.append(key)
                conf, ev = _source_meta(facts_record, key)
                confidence[target] = conf
                if ev:
                    evidence[target] = ev

    return facts, confidence, evidence, list(dict.fromkeys(used))


def _evidence_keys(record: FamilyResolutionRecord) -> list[str]:
    keys: list[str] = []
    for item in record.resolution.evidence:
        for key in item.source_fact_keys:
            if key not in keys:
                keys.append(key)
    return keys


def _documents(record: ValidatedFactsRecord) -> list[DocumentUse]:
    pages: dict[str, set[int]] = {
        str(document_id): set()
        for document_id in record.facts.source_document_ids
    }
    for fact in record.facts.facts.values():
        for source in fact.sources:
            pages.setdefault(source.document_id, set())
            if source.page_index is not None:
                pages[source.document_id].add(source.page_index)
    return [
        DocumentUse(
            document_id=document_id,
            label=f"Documento de origen {document_id}",
            status="validated",
            pages_used=sorted(page_indexes),
        )
        for document_id, page_indexes in sorted(pages.items())
    ]


def _summary(
    record: ValidatedFactsRecord,
    keys: Iterable[str],
) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    used: list[str] = []
    for key in keys:
        value, found = _value(record, key)
        if value in (None, "", [], {}) or not found:
            continue
        label = _SUMMARY_LABELS.get(key, key.replace("_", " ").capitalize())
        display = str(value)
        if key in {"velocidad_medida_kmh", "velocidad_limite_kmh"}:
            display = f"{value} km/h"
        elif key in {"sancion_importe_eur", "importe_reducido_eur"}:
            display = f"{value} €"
        rows.append(f"{label}: {display}.")
        used.append(found)
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def _missing(
    code: str,
    description: str,
    severity: MissingItemSeverity = MissingItemSeverity.BLOCKING,
) -> MissingItem:
    return MissingItem(code=code, description=description, severity=severity)


def _base_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        ("organismo", _ORGANISM_KEYS),
        ("expediente", _CASE_REF_KEYS),
        ("hecho_imputado", _FACT_KEYS),
    )
    result: list[MissingItem] = []
    for code, keys in groups:
        value, _ = _value(record, *keys)
        if value in (None, "", [], {}):
            result.append(_missing(code, f"Falta validar {code.replace('_', ' ')}."))

    for conflict in record.facts.conflicts:
        result.append(
            _missing(
                f"conflict_{_slug(conflict)}"[:120],
                f"Conflicto documental: {conflict}",
            )
        )
    for unresolved in record.facts.unresolved:
        result.append(
            _missing(
                f"unresolved_{_slug(unresolved)}"[:120],
                f"Dato pendiente de revisión: {unresolved}",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    return _dedupe_missing(result)


def _phase(record: ValidatedFactsRecord) -> tuple[str, list[MissingItem]]:
    values = []
    for key in _PHASE_KEYS:
        value, _ = _value(record, key)
        if value not in (None, "", [], {}):
            values.append(_norm(value))
    blob = " ".join(values)

    if any(token in blob for token in ("requerimiento de pago", "providencia de apremio", "via ejecutiva", "firme")):
        return (
            "ACTUACIÓN PENDIENTE DE REVISIÓN PROCESAL",
            [
                _missing(
                    "procedural_phase_incompatible_with_allegations",
                    "La fase parece corresponder a pago, ejecutiva o firmeza; debe determinarse el escrito procedente.",
                )
            ],
        )
    if any(token in blob for token in ("denuncia", "initial_notice", "iniciacion", "incoacion", "audiencia", "alegaciones")):
        return "ESCRITO DE ALEGACIONES", []
    if "resolucion" in blob:
        return (
            "RECURSO ADMINISTRATIVO PENDIENTE DE DETERMINAR",
            [
                _missing(
                    "appeal_type_unresolved",
                    "Consta una resolución, pero falta validar el recurso procedente.",
                )
            ],
        )
    return (
        "ACTUACIÓN PENDIENTE DE DETERMINAR",
        [_missing("procedural_phase_unresolved", "Falta validar la fase procesal y el tipo de escrito.")],
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
        day, month, year = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _deadlines(record: ValidatedFactsRecord) -> tuple[list[Deadline], list[MissingItem]]:
    for key in _DEADLINE_KEYS:
        value, found = _value(record, key)
        parsed = _parse_datetime(value)
        if parsed and found:
            return [
                Deadline(
                    label="Plazo para presentar el escrito",
                    due_at=parsed,
                    calculation_status="confirmed",
                    source_fact_keys=[found],
                )
            ], []
    return [
        Deadline(
            label="Plazo para presentar el escrito",
            due_at=None,
            calculation_status="unresolved",
            notes=["OPS debe validar el vencimiento antes del freeze."],
        )
    ], [_missing("deadline_unresolved", "Falta validar el vencimiento del escrito.")]


def _dedupe_missing(items: Iterable[MissingItem]) -> list[MissingItem]:
    result: list[MissingItem] = []
    seen: set[str] = set()
    for item in items:
        if item.code in seen:
            continue
        seen.add(item.code)
        result.append(item)
    return result


def _issue_items(intelligence: dict[str, Any], prefix: str) -> tuple[list[MissingItem], list[str]]:
    items: list[MissingItem] = []
    risks: list[str] = []
    for issue in intelligence.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "review").strip()
        message = str(issue.get("message") or code).strip()
        severity = str(issue.get("severity") or "info").lower()
        risks.append(f"{code}: {message}")
        if severity in {"high", "medium"}:
            items.append(
                _missing(
                    f"{prefix}_{_slug(code)}"[:120],
                    message,
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    return items, risks


def _argument(
    code: str,
    title: str,
    body: str,
    source_keys: Iterable[str],
    *,
    priority: str = "secondary",
    legal_basis: Optional[list[str]] = None,
) -> LegalArgument:
    keys = list(dict.fromkeys(key for key in source_keys if key))
    if not keys:
        raise HTTPException(status_code=409, detail=f"El argumento {code} no conserva hechos de origen")
    return LegalArgument(
        code=code,
        title=title,
        body=body.strip(),
        priority=priority,
        source_fact_keys=keys,
        legal_basis=legal_basis or list(_BASE_LEGAL_BASIS),
    )


def _destination_subject(
    record: ValidatedFactsRecord,
    document_type: str,
) -> tuple[str, str]:
    organismo, _ = _value(record, *_ORGANISM_KEYS)
    expediente, _ = _value(record, *_CASE_REF_KEYS)
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
    return destination, subject


def _ensure_authority(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
    family: str,
    specialist: str,
) -> None:
    if not facts_record.frozen or facts_record.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="Los hechos deben estar activos y congelados")
    if not family_record.locked or family_record.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="La familia debe estar activa y bloqueada")
    resolution = family_record.resolution
    if resolution.family != family or resolution.specialist != specialist:
        raise HTTPException(status_code=409, detail="La autoridad no corresponde al especialista solicitado")
    if family_record.validated_facts_id != facts_record.id:
        raise HTTPException(status_code=409, detail="La familia no procede de esta versión de hechos")


def build_velocity_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    _ensure_authority(facts_record, family_record, "velocidad", "traffic.velocidad")
    evidence_keys = _evidence_keys(family_record)
    core, core_keys = _validated_core(facts_record, _VELOCITY_CORE_MAP)
    secondary, confidence, evidence, secondary_keys = _velocity_secondary(facts_record)
    if secondary:
        core["velocity_secondary_facts"] = secondary
        core["velocity_secondary_facts_version"] = "validated_facts_projection_v1"
        core["velocity_secondary_facts_confidence"] = confidence
        core["velocity_secondary_facts_evidence"] = evidence

    intelligence = build_velocity_legal_intelligence(core)
    if intelligence.get("version") != VELOCITY_LEGAL_INTELLIGENCE_VERSION:
        raise HTTPException(status_code=500, detail="Versión inesperada del especialista Velocidad")

    source_keys = list(dict.fromkeys([*evidence_keys, *core_keys, *secondary_keys]))
    measured, measured_key = _value(facts_record, "velocidad_medida_kmh")
    limit, limit_key = _value(facts_record, "velocidad_limite_kmh")
    fact, fact_key = _value(facts_record, *_FACT_KEYS)
    radar_model, radar_model_key = _value(facts_record, "radar_modelo_hint")
    radar_antenna, radar_antenna_key = _value(facts_record, "radar_antena")
    verification, verification_key = _value(facts_record, "fecha_verificacion_metrologica")
    fine, fine_key = _value(facts_record, "sancion_importe_eur")
    points, points_key = _value(facts_record, "puntos_detraccion")

    arguments = [
        _argument(
            "velocity_measurement_traceability",
            "Acreditación de la medición, identificación del cinemómetro y trazabilidad",
            (
                "La Administración debe aportar el registro original de la medición y permitir comprobar su "
                "vinculación inequívoca con el vehículo, la fecha, el lugar y el expediente. Deben constar la "
                "identificación del cinemómetro, sus elementos relevantes y la trazabilidad íntegra de la captura, "
                "sin que una mera reproducción parcial o una referencia no verificable baste para sostener la sanción. "
                f"En los hechos validados constan una velocidad de {measured} km/h y un límite de {limit} km/h"
                + (f", con modelo {radar_model}" if radar_model not in (None, "", [], {}) else "")
                + (f" e identificador de antena {radar_antenna}" if radar_antenna not in (None, "", [], {}) else "")
                + "."
            ),
            [fact_key, measured_key, limit_key, radar_model_key, radar_antenna_key],
            priority="primary",
            legal_basis=[
                *_BASE_LEGAL_BASIS,
                "Normativa metrológica aplicable a los cinemómetros y su control en servicio.",
            ],
        ),
        _argument(
            "velocity_margin_semantics",
            "Margen de error y naturaleza de la velocidad consignada",
            (
                "La documentación debe aclarar si la cifra utilizada corresponde a la lectura obtenida por el aparato "
                "o a la velocidad jurídicamente considerada después de aplicar la corrección procedente. No se aplica "
                "automáticamente un margen concreto: se solicita que la Administración identifique la modalidad real "
                "de funcionamiento del equipo, el margen utilizado, la operación practicada y la velocidad final sobre "
                "la que se determinó el tramo sancionador."
            ),
            [measured_key, limit_key, fact_key],
            priority="primary",
        ),
        _argument(
            "velocity_metrological_verification",
            "Verificación metrológica vigente en la fecha del hecho",
            (
                "Debe incorporarse el certificado y la documentación de control metrológico que acrediten que el "
                "cinemómetro concreto estaba autorizado y verificado en la fecha de la medición, con correspondencia "
                "entre el equipo, sus identificadores y el registro aportado. "
                + (
                    f"Consta como hecho validado una fecha de verificación {verification}, cuya relación temporal con el hecho debe revisarse."
                    if verification not in (None, "", [], {})
                    else "No consta como hecho validado una fecha inequívoca de verificación, por lo que debe aportarse la acreditación vigente."
                )
            ),
            [fact_key, measured_key, radar_model_key, radar_antenna_key, verification_key],
            priority="secondary",
        ),
        _argument(
            "velocity_sanction_band",
            "Correcta determinación del tramo, importe y detracción de puntos",
            (
                "Una vez fijada la velocidad jurídicamente utilizable, la Administración debe motivar su encaje exacto "
                "en el tramo sancionador aplicado. Debe comprobarse especialmente cualquier cifra situada en un umbral "
                "entre tramos y la correspondencia entre velocidad, importe y puntos. "
                f"Los hechos validados consignan {fine if fine is not None else 'un importe pendiente'} € y "
                f"{points if points is not None else 'puntos pendientes de validar'}."
            ),
            [measured_key, limit_key, fine_key, points_key],
            priority="secondary",
        ),
    ]

    summary, summary_keys = _summary(
        facts_record,
        (
            "organismo",
            "expediente_ref",
            "hecho_denunciado_literal",
            "matricula",
            "fecha_infraccion",
            "lugar_infraccion",
            "velocidad_medida_kmh",
            "velocidad_limite_kmh",
            "radar_modelo_hint",
            "radar_antena",
            "sancion_importe_eur",
            "puntos_detraccion",
            "fase_procedimental",
        ),
    )
    source_keys = list(dict.fromkeys([*source_keys, *summary_keys]))
    document_type, phase_missing = _phase(facts_record)
    deadlines, deadline_missing = _deadlines(facts_record)
    issue_missing, issue_risks = _issue_items(intelligence, "velocity")
    missing = _base_missing(facts_record)
    if measured in (None, "", [], {}):
        missing.append(_missing("velocity_measured_missing", "Falta validar la velocidad consignada."))
    if limit in (None, "", [], {}):
        missing.append(_missing("velocity_limit_missing", "Falta validar el límite aplicable."))
    missing = _dedupe_missing([*missing, *phase_missing, *deadline_missing, *issue_missing])
    destination, subject = _destination_subject(facts_record, document_type)

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="velocidad",
        specialist="traffic.velocidad",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se atribuye una infracción de velocidad basada en la conducta validada: {fact}."
            if fact not in (None, "", [], {})
            else "El hecho de velocidad está pendiente de validación documental."
        ),
        client_goal=(
            "Obtener el archivo si la medición, el equipo, el margen, la trazabilidad o el tramo no quedan acreditados; "
            "subsidiariamente, corregir la calificación, el importe o los puntos."
        ),
        primary_strategy=(
            "Exigir la acreditación técnica completa de la medición y separar estrictamente la lectura del aparato, "
            "la corrección aplicada y la velocidad utilizada para determinar la sanción."
        ),
        secondary_strategies=[
            "Comprobar la vigencia y correspondencia de la verificación metrológica.",
            "Revisar umbrales, tramo sancionador, importe y detracción de puntos.",
        ],
        requested_outcomes=[
            "Que se acuerde el archivo por falta de prueba técnica suficiente o de trazabilidad de la medición.",
            "Subsidiariamente, que se rectifique la velocidad jurídicamente utilizable y el tramo sancionador.",
            "Subsidiariamente, que se corrijan el importe y la detracción de puntos conforme a los hechos acreditados.",
        ],
        documents_used=_documents(facts_record),
        missing_items=missing,
        deadlines=deadlines,
        risks=[
            f"Especialista congelado utilizado: {VELOCITY_LEGAL_INTELLIGENCE_VERSION}.",
            *issue_risks,
            "OPS debe comprobar que el escrito no afirme un margen o una modalidad del radar que no consten acreditados.",
        ],
        destination=destination,
        document_type=document_type,
        subject=subject,
        legal_arguments=arguments,
        additional_requests=[
            "Que se aporte la captura original con sus metadatos y trazabilidad.",
            "Que se aporte la documentación metrológica del cinemómetro y sus identificadores.",
            "Que se detalle el margen aplicado, la velocidad resultante y el tramo sancionador utilizado.",
        ],
        created_by_component=f"traffic.velocidad:{VELOCITY_LEGAL_INTELLIGENCE_VERSION}",
    )


def _parse_semaphore_arguments(body: str, source_keys: list[str]) -> list[LegalArgument]:
    normalized = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(
        r"ALEGACIÓN\s+([^—\n]+)\s+—\s+([^\n]+)\n\n([\s\S]*?)"
        r"(?=\nALEGACIÓN\s+[^—\n]+\s+—|\nIII\.|\Z)",
        flags=re.IGNORECASE,
    )
    arguments: list[LegalArgument] = []
    for index, match in enumerate(pattern.finditer(normalized), start=1):
        ordinal = _slug(match.group(1))
        title = re.sub(r"\s+", " ", match.group(2)).strip(" .")
        argument_body = re.sub(r"\n{3,}", "\n\n", match.group(3)).strip()
        if not title or not argument_body:
            continue
        arguments.append(
            _argument(
                f"semaforo_{index}_{ordinal}_{_slug(title)}"[:120],
                title,
                argument_body,
                source_keys,
                priority="primary" if index == 1 else "secondary",
            )
        )
    if not arguments:
        raise HTTPException(status_code=500, detail="Semáforo no devolvió alegaciones estructurables")
    return arguments


def build_semaforo_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    _ensure_authority(facts_record, family_record, "semaforo", "traffic.semaforo")
    evidence_keys = _evidence_keys(family_record)
    core, core_keys = _validated_core(facts_record, _SEMAPHORE_CORE_MAP)
    secondary, confidence, evidence, secondary_keys = _semaphore_secondary(facts_record)
    if secondary:
        core["semaforo_secondary_facts"] = secondary
        core["semaforo_secondary_facts_version"] = "validated_facts_projection_v1"
        core["semaforo_secondary_facts_confidence"] = confidence
        core["semaforo_secondary_facts_evidence"] = evidence

    intelligence = build_semaforo_legal_intelligence(core)
    if intelligence.get("version") != SEMAFORO_LEGAL_INTELLIGENCE_VERSION:
        raise HTTPException(status_code=500, detail="Versión inesperada del especialista Semáforo")
    template_core = dict(core)
    template_core["_semaforo_legal_intelligence"] = intelligence

    source_keys = list(dict.fromkeys([*evidence_keys, *core_keys, *secondary_keys]))
    arguments = _parse_semaphore_arguments(
        build_semaforo_intelligence_template(template_core).get("cuerpo") or "",
        source_keys,
    )
    fact, _ = _value(facts_record, *_FACT_KEYS)
    summary, summary_keys = _summary(
        facts_record,
        (
            "organismo",
            "expediente_ref",
            "hecho_denunciado_literal",
            "matricula",
            "fecha_infraccion",
            "hora_infraccion",
            "lugar_infraccion",
            "metodo_captura",
            "captura_automatica",
            "fotografia_vehiculo_presente",
            "sancion_importe_eur",
            "importe_reducido_eur",
            "puntos_detraccion",
            "norma_hint",
            "articulo_infringido_num",
            "fase_procedimental",
        ),
    )
    source_keys = list(dict.fromkeys([*source_keys, *summary_keys]))
    document_type, phase_missing = _phase(facts_record)
    deadlines, deadline_missing = _deadlines(facts_record)
    issue_missing, issue_risks = _issue_items(intelligence, "semaforo")
    missing = _base_missing(facts_record)

    required = (
        ("semaforo_matricula_missing", "Falta validar la matrícula.", ("matricula",)),
        ("semaforo_date_missing", "Falta validar la fecha del hecho.", ("fecha_infraccion",)),
        ("semaforo_location_missing", "Falta validar el lugar del hecho.", ("lugar_infraccion",)),
        ("semaforo_fine_missing", "Falta validar la sanción ordinaria.", ("sancion_importe_eur",)),
        ("semaforo_points_missing", "Falta validar la detracción de puntos.", ("puntos_detraccion",)),
    )
    for code, description, keys in required:
        value, _ = _value(facts_record, *keys)
        if value in (None, "", [], {}):
            missing.append(_missing(code, description))
    missing = _dedupe_missing([*missing, *phase_missing, *deadline_missing, *issue_missing])
    destination, subject = _destination_subject(facts_record, document_type)

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="semaforo",
        specialist="traffic.semaforo",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se atribuye al interesado la conducta siguiente: {fact}."
            if fact not in (None, "", [], {})
            else "El hecho relativo al semáforo está pendiente de validación documental."
        ),
        client_goal=(
            "Obtener el archivo si no se acredita la fase roja activa, el rebase efectivo, la identificación del vehículo "
            "y la trazabilidad temporal; subsidiariamente, corregir la subsunción o la sanción."
        ),
        primary_strategy=(
            "Exigir la evidencia original que permita reconstruir la fase roja, la posición respecto de la línea de detención "
            "y la correspondencia temporal inequívoca con el vehículo denunciado."
        ),
        secondary_strategies=[
            "Revisar el precepto transcrito y su correcta subsunción en la fecha del hecho.",
            "Comprobar la consistencia entre sanción ordinaria, importe reducido y puntos.",
        ],
        requested_outcomes=[
            "Que se acuerde el archivo por insuficiencia de prueba original o falta de trazabilidad del hecho.",
            "Subsidiariamente, que se aclare y corrija la subsunción normativa y la motivación del expediente.",
            "Subsidiariamente, que se rectifiquen importe y puntos conforme a los hechos acreditados.",
        ],
        documents_used=_documents(facts_record),
        missing_items=missing,
        deadlines=deadlines,
        risks=[
            f"Especialista congelado utilizado: {SEMAFORO_LEGAL_INTELLIGENCE_VERSION}.",
            *issue_risks,
            "La presencia de una fotografía impresa no demuestra por sí sola la secuencia original ni la cronometría.",
        ],
        destination=destination,
        document_type=document_type,
        subject=subject,
        legal_arguments=arguments,
        additional_requests=[
            "Que se aporte la secuencia original de imágenes o vídeo y sus metadatos.",
            "Que se acredite la fase roja activa y el instante de rebase de la línea de detención.",
            "Que se incorpore la documentación técnica y de sincronización del sistema de captación.",
        ],
        created_by_component=f"traffic.semaforo:{SEMAFORO_LEGAL_INTELLIGENCE_VERSION}",
    )
