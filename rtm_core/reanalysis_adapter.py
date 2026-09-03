"""Puente conservador entre Reanalysis y ``ValidatedFacts``.

El adaptador no extrae, no clasifica y no decide estrategia. Consume la última
extracción de Reanalysis y su evento de trazabilidad, descarta campos legacy de
familia/estrategia y crea un borrador de hechos con procedencia, confianza,
conflictos y campos no resueltos.

Una lectura candidata sin evidencia suficiente nunca se convierte en un hecho
validado: queda con ``value=None`` y revisión humana.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from rtm_core.contracts import (
    FactStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)


REANALYSIS_ADAPTER_VERSION = "rtm_reanalysis_to_validated_facts_v1_0"
_SUPPORTED_EXTRACTOR_PREFIX = "traffic_fine_reanalysis_"

# Solo estos campos documentales pueden cruzar el puente. Se excluyen
# deliberadamente OCR crudo, clasificación, familia, estrategia y borradores.
_FIELD_ALIASES: dict[str, str] = {
    "organismo": "organismo",
    "organo": "organismo",
    "emisor": "organismo",
    "administracion_emisora": "organismo",
    "expediente_ref": "expediente_ref",
    "numero_expediente": "expediente_ref",
    "expediente": "expediente_ref",
    "matricula": "matricula",
    "plate": "matricula",
    "vehicle_make_model": "vehiculo_marca_modelo",
    "vehiculo_marca_modelo": "vehiculo_marca_modelo",
    "fecha_infraccion": "fecha_infraccion",
    "hora_infraccion": "hora_infraccion",
    "lugar_infraccion": "lugar_infraccion",
    "poblacion": "poblacion",
    "hecho_denunciado_literal": "hecho_denunciado_literal",
    "hecho_denunciado_resumido": "hecho_denunciado_literal",
    "hecho_imputado": "hecho_denunciado_literal",
    "hecho_validado": "hecho_denunciado_literal",
    "conducta_imputada": "hecho_denunciado_literal",
    "descripcion_infraccion": "hecho_denunciado_literal",
    "sancion_importe_eur": "sancion_importe_eur",
    "sancion_ordinaria_eur": "sancion_importe_eur",
    "importe": "sancion_importe_eur",
    "importe_reducido_eur": "importe_reducido_eur",
    "importe_a_pagar_eur": "importe_a_pagar_eur",
    "puntos_detraccion": "puntos_detraccion",
    "puntos": "puntos_detraccion",
    "velocidad_medida_kmh": "velocidad_medida_kmh",
    "velocidad_captada_kmh": "velocidad_medida_kmh",
    "velocidad_detectada_kmh": "velocidad_medida_kmh",
    "velocidad_registrada_kmh": "velocidad_medida_kmh",
    "velocidad_limite_kmh": "velocidad_limite_kmh",
    "limite_velocidad_kmh": "velocidad_limite_kmh",
    "velocidad_maxima_kmh": "velocidad_limite_kmh",
    "radar_modelo_hint": "radar_modelo_hint",
    "radar_modelo": "radar_modelo_hint",
    "radar_antena": "radar_antena",
    "antenna": "radar_antena",
    "capture_method": "metodo_captura",
    "metodo_captura": "metodo_captura",
    "capture_automatic": "captura_automatica",
    "captura_automatica": "captura_automatica",
    "vehicle_photo_present": "fotografia_vehiculo_presente",
    "certificate_reproduction_present": "certificado_metrologico_reproducido",
    "document_title": "titulo_documento",
    "document_type": "tipo_documento",
    "tipo_documento": "tipo_documento",
    "documento_tipo": "tipo_documento",
    "procedural_stage_hint": "fase_procedimental",
    "fase_procedimental": "fase_procedimental",
    "tramite_detectado": "fase_procedimental",
    "administrative_finality_stated": "firmeza_administrativa_indicada",
    "payment_term_days": "plazo_pago_dias",
    "fecha_documento": "fecha_documento",
    "fecha_emision": "fecha_documento",
    "initiation_document_date": "fecha_documento_incoacion",
    "driver_data_date": "fecha_datos_conductor",
    "verification_date": "fecha_verificacion_metrologica",
    "fecha_limite": "fecha_limite",
    "fecha_vencimiento": "fecha_limite",
    "deadline_main": "fecha_limite",
    "plazo_fin": "fecha_limite",
    "fecha_limite_pago": "fecha_limite_pago",
    "norma": "norma_hint",
    "norma_hint": "norma_hint",
    "article": "articulo_infringido_num",
    "articulo": "articulo_infringido_num",
    "articulo_infringido_num": "articulo_infringido_num",
    "apartado_infringido_num": "apartado_infringido_num",
    "semaforo_fase": "semaforo_fase",
    "fase_semaforo": "semaforo_fase",
    "estado_semaforo": "semaforo_fase",
    "document_subject_name": "document_subject_name",
    "document_subject_id": "document_subject_id",
}

_NESTED_FIELD_MAP: dict[str, dict[str, str]] = {
    "normative_reference": {
        "norm": "norma_hint",
        "article": "articulo_infringido_num",
    },
    "document_subject": {
        "full_name": "document_subject_name",
        "id_number": "document_subject_id",
    },
}

_IGNORED_EXACT_KEYS = {
    "tipo_infraccion",
    "familia_resuelta",
    "familia",
    "family",
    "specialist_dispatch",
    "classifier_result",
    "classification",
    "ready_for_generate",
    "requires_operator_review",
    "operator_review_reasons",
    "modelo_defensa",
    "recommended_action",
    "strategy",
    "draft",
    "asunto",
    "cuerpo",
}
_IGNORED_KEY_TOKENS = {
    "raw_text",
    "vision_raw",
    "ocr",
    "prompt",
    "classifier",
    "classification",
    "scoring",
    "draft",
    "strategy",
    "template",
}

_SOURCE_SPECS = (
    (
        "handwritten_precision",
        "handwritten_precision_values",
        "handwritten_precision_confidence",
        "handwritten_precision_evidence",
        "handwritten_precision_version",
    ),
    (
        "semaforo_precision",
        "semaforo_precision_values",
        "semaforo_precision_confidence",
        "semaforo_precision_evidence",
        "semaforo_precision_version",
    ),
    (
        "traffic_generic_facts",
        "traffic_generic_facts",
        "traffic_generic_facts_confidence",
        "traffic_generic_facts_evidence",
        "traffic_generic_facts_version",
    ),
    (
        "semaforo_secondary_facts",
        "semaforo_secondary_facts",
        "semaforo_secondary_facts_confidence",
        "semaforo_secondary_facts_evidence",
        "semaforo_secondary_facts_version",
    ),
    (
        "velocity_secondary_facts",
        "velocity_secondary_facts",
        "velocity_secondary_facts_confidence",
        "velocity_secondary_facts_evidence",
        "velocity_secondary_facts_version",
    ),
    (
        "critical_zoom",
        "critical_fields_zoomed",
        "critical_fields_zoomed_confidence",
        "critical_fields_zoomed_evidence",
        "critical_fields_zoom_version",
    ),
    (
        "critical_vision",
        "critical_fields_vision",
        "critical_fields_vision_confidence",
        "critical_fields_vision_evidence",
        "critical_fields_vision_version",
    ),
    (
        "deterministic",
        "critical_fields_detected",
        "critical_fields_detected_confidence",
        "critical_fields_detected_evidence",
        "critical_fields_detected_version",
    ),
)

_SOURCE_PRIORITY = {
    "handwritten_precision": 90,
    "semaforo_precision": 85,
    "critical_zoom": 80,
    "critical_vision": 75,
    "deterministic": 70,
    "traffic_generic_facts": 60,
    "semaforo_secondary_facts": 55,
    "velocity_secondary_facts": 55,
    "reanalysis_core": 10,
}

# La confianza y la evidencia incluidas en estas salidas proceden del propio
# modelo. Son útiles para ordenar una revisión, pero nunca constituyen una
# validación documental. Solo el parser determinista puede producir un hecho
# validado automáticamente (si además conserva evidencia y supera su umbral).
_VERIFIABLE_DETERMINISTIC_METHODS = frozenset({"deterministic"})
_MODEL_DERIVED_METHODS = frozenset(
    {spec[0] for spec in _SOURCE_SPECS}
    - _VERIFIABLE_DETERMINISTIC_METHODS
    | {"reanalysis_core"}
)


@dataclass(frozen=True)
class _Candidate:
    key: str
    value: Any
    confidence: float
    evidence: Optional[str]
    method: str
    version: Optional[str]
    source_key: str
    priority: int


class ReanalysisAdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_version: str = REANALYSIS_ADAPTER_VERSION
    facts: ValidatedFacts
    accepted_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicted_fields: list[str] = Field(default_factory=list)
    ignored_fields: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalise_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _value_marker(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)
    if isinstance(value, str):
        return _normalise_text(value)
    return repr(value)


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {}, ())


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, number))


def _evidence(value: Any) -> Optional[str]:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    return text_value[:280] if text_value else None


def _safe_key(raw_key: Any) -> Optional[str]:
    key = str(raw_key or "").strip().lower()
    if not key or key in _IGNORED_EXACT_KEYS:
        return None
    if any(token in key for token in _IGNORED_KEY_TOKENS):
        return None
    return _FIELD_ALIASES.get(key)


def _nested_lookup(mapping: Mapping[str, Any], raw_key: str, subkey: Optional[str]) -> Any:
    value = mapping.get(raw_key)
    if subkey and isinstance(value, dict):
        return value.get(subkey)
    if value not in (None, "", {}, []):
        return value
    return None


def _flatten_values(values: Mapping[str, Any]) -> Iterable[tuple[str, Any, str, Optional[str]]]:
    for raw_key, value in values.items():
        key_text = str(raw_key or "").strip().lower()
        nested = _NESTED_FIELD_MAP.get(key_text)
        if nested and isinstance(value, dict):
            for subkey, canonical in nested.items():
                nested_value = value.get(subkey)
                if _nonempty(nested_value):
                    yield canonical, nested_value, key_text, subkey
            continue

        canonical = _safe_key(key_text)
        if canonical and _nonempty(value):
            yield canonical, value, key_text, None


def _source_candidates(
    payload: Mapping[str, Any],
    *,
    method: str,
    values_key: str,
    confidence_key: str,
    evidence_key: str,
    version_key: str,
) -> list[_Candidate]:
    values = _mapping(payload.get(values_key))
    if not values:
        return []
    confidences = _mapping(payload.get(confidence_key))
    evidences = _mapping(payload.get(evidence_key))
    version_value = payload.get(version_key)
    version = str(version_value).strip() if version_value not in (None, "") else None
    candidates: list[_Candidate] = []

    for canonical, value, raw_key, subkey in _flatten_values(values):
        conf_value = _nested_lookup(confidences, raw_key, subkey)
        if conf_value is None:
            conf_value = confidences.get(canonical)
        evidence_value = _nested_lookup(evidences, raw_key, subkey)
        if evidence_value is None:
            evidence_value = evidences.get(canonical)
        candidates.append(
            _Candidate(
                key=canonical,
                value=value,
                confidence=_confidence(conf_value),
                evidence=_evidence(evidence_value),
                method=method,
                version=version,
                source_key=raw_key,
                priority=_SOURCE_PRIORITY.get(method, 0),
            )
        )
    return candidates


def _document_sources(wrapper: Mapping[str, Any]) -> tuple[list[str], dict[str, Optional[int]]]:
    ids: list[str] = []
    page_by_document: dict[str, Optional[int]] = {}

    extracted = _mapping(wrapper.get("extracted"))
    storage = _mapping(wrapper.get("storage"))
    for collection in (
        extracted.get("source_document_ids"),
        storage.get("source_document_ids"),
    ):
        if isinstance(collection, list):
            for value in collection:
                document_id = str(value or "").strip()
                if document_id and document_id not in ids:
                    ids.append(document_id)

    pages = wrapper.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            document_id = str(page.get("document_id") or "").strip()
            if not document_id:
                continue
            if document_id not in ids:
                ids.append(document_id)
            raw_index = page.get("page_index")
            page_index: Optional[int] = None
            try:
                parsed = int(raw_index)
                page_index = max(0, parsed - 1) if parsed > 0 else max(0, parsed)
            except Exception:
                pass
            page_by_document[document_id] = page_index

    return ids, page_by_document


def _quality_is_low(event_payload: Mapping[str, Any]) -> bool:
    quality = _mapping(event_payload.get("handwritten_precision_quality"))
    if not quality:
        return False
    for key, value in quality.items():
        key_low = _normalise_text(key)
        value_low = _normalise_text(value)
        if key_low in {"requires_human_review", "human_review", "low_legibility"} and bool(value):
            return True
        if any(token in value_low for token in ("low", "poor", "baja", "ilegible", "unreadable")):
            return True
        if key_low in {"legibility", "legibility_score", "readability", "readability_score"}:
            try:
                if float(value) < 0.85:
                    return True
            except Exception:
                pass
    return False


def _field_names(values: Any) -> set[str]:
    fields: set[str] = set()
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                raw = value.get("field") or value.get("key") or value.get("code")
            else:
                raw = value
            canonical = _safe_key(raw)
            if canonical:
                fields.add(canonical)
    return fields


def _explicit_conflicts(event_payload: Mapping[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    values = event_payload.get("critical_conflicts_resolved")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        canonical = _safe_key(item.get("field") or item.get("key"))
        if not canonical:
            continue
        current = item.get("current_value") or item.get("previous")
        alternative = item.get("vision_value") or item.get("precision_value") or item.get("candidate")
        description = (
            f"Lecturas distintas para {canonical}: "
            f"{str(current)[:100]!r} frente a {str(alternative)[:100]!r}."
        )
        result.setdefault(canonical, []).append(description)
    return result


def _threshold(candidate: _Candidate) -> float:
    if candidate.method == "handwritten_precision":
        return 0.98
    if "precision" in candidate.method:
        return 0.93
    if candidate.method in {"critical_zoom", "critical_vision", "deterministic"}:
        return 0.92
    return 0.90


def _method_root(method: str) -> str:
    return str(method or "").strip().lower().split(":", 1)[0]


def reanalysis_method_is_model_derived(method: str) -> bool:
    """Indica si una procedencia de Reanalysis depende de una salida de modelo."""

    return _method_root(method) in _MODEL_DERIVED_METHODS


def _source_references(
    document_ids: list[str],
    page_by_document: Mapping[str, Optional[int]],
    candidate: _Candidate,
) -> list[SourceReference]:
    if candidate.method in _VERIFIABLE_DETERMINISTIC_METHODS:
        source_type = "deterministic_document"
    elif reanalysis_method_is_model_derived(candidate.method):
        source_type = "model_document_observation"
    else:
        source_type = "document" if len(document_ids) == 1 else "document_group"
    method = candidate.method
    if candidate.version:
        method = f"{method}:{candidate.version}"
    return [
        SourceReference(
            document_id=document_id,
            page_index=page_by_document.get(document_id),
            source_type=source_type,
            extraction_method=method,
            evidence=candidate.evidence,
            confidence=candidate.confidence,
        )
        for document_id in document_ids
    ]


def _candidate_note(candidate: _Candidate) -> str:
    value = str(candidate.value)
    if len(value) > 220:
        value = value[:217] + "..."
    return (
        f"Lectura candidata no consolidada ({candidate.method}, "
        f"confianza {candidate.confidence:.2f}): {value}"
    )


def assert_reanalysis_draft_is_safe(facts: ValidatedFacts) -> None:
    """Defensa en profundidad: un hecho de modelo no puede salir VALIDATED."""

    for fact_key, fact in facts.facts.items():
        if fact.status is not FactStatus.VALIDATED:
            continue
        if any(
            source.source_type == "model_document_observation"
            or reanalysis_method_is_model_derived(source.extraction_method)
            for source in fact.sources
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "El borrador de Reanalysis intentó validar una salida de modelo "
                    f"sin revisión documental: {fact_key}"
                ),
            )


def build_validated_facts_from_reanalysis(
    *,
    case_id: str,
    wrapper: Mapping[str, Any],
    event_payload: Optional[Mapping[str, Any]] = None,
) -> ReanalysisAdapterResult:
    """Transforma una salida de Reanalysis en un borrador de hechos.

    No persiste nada. Los hechos dudosos o sin evidencia quedan no resueltos.
    """

    event = dict(event_payload or {})
    core = _mapping(wrapper.get("extracted"))
    if not core:
        raise HTTPException(status_code=422, detail="La extracción de Reanalysis está vacía")

    extractor_version = str(core.get("extractor_version") or event.get("extractor_version") or "").strip()
    if not extractor_version.startswith(_SUPPORTED_EXTRACTOR_PREFIX):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "La extracción no procede del Reanalysis de multas validado",
                "extractor_version": extractor_version or None,
            },
        )

    document_ids, page_by_document = _document_sources(wrapper)
    warnings: list[str] = []
    if not document_ids:
        warnings.append("La extracción no conserva identificadores de documentos de origen")

    candidates: dict[str, list[_Candidate]] = {}
    ignored_fields: set[str] = set()

    for method, values_key, confidence_key, evidence_key, version_key in _SOURCE_SPECS:
        for payload in (event, core):
            for candidate in _source_candidates(
                payload,
                method=method,
                values_key=values_key,
                confidence_key=confidence_key,
                evidence_key=evidence_key,
                version_key=version_key,
            ):
                candidates.setdefault(candidate.key, []).append(candidate)

    # El top-level de Reanalysis solo sirve como candidato de revisión. Nunca
    # obtiene confianza por sí mismo ni puede transportar la familia legacy.
    for raw_key, value in core.items():
        canonical = _safe_key(raw_key)
        if canonical and _nonempty(value):
            candidates.setdefault(canonical, []).append(
                _Candidate(
                    key=canonical,
                    value=value,
                    confidence=0.0,
                    evidence=None,
                    method="reanalysis_core",
                    version=extractor_version,
                    source_key=str(raw_key),
                    priority=_SOURCE_PRIORITY["reanalysis_core"],
                )
            )
        elif str(raw_key).lower() in _IGNORED_EXACT_KEYS or any(
            token in str(raw_key).lower() for token in _IGNORED_KEY_TOKENS
        ):
            ignored_fields.add(str(raw_key))

    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Reanalysis no contiene campos documentales seguros para revisión",
        )

    unresolved_declared = _field_names(event.get("unresolved_critical_fields"))
    unresolved_declared.update(_field_names(event.get("missing_required_fields")))
    conflicts_declared = _explicit_conflicts(event)
    low_handwriting_quality = _quality_is_low(event)

    fact_models: dict[str, ValidatedFact] = {}
    accepted: list[str] = []
    unresolved: list[str] = []
    conflicted: list[str] = []
    global_conflicts: list[str] = []

    for key in sorted(candidates):
        field_candidates = candidates[key]
        # Elimina duplicados exactos conservando la mejor procedencia.
        best_by_value: dict[str, _Candidate] = {}
        for candidate in field_candidates:
            marker = _value_marker(candidate.value)
            current = best_by_value.get(marker)
            rank = (candidate.confidence, candidate.priority, bool(candidate.evidence))
            if current is None or rank > (
                current.confidence,
                current.priority,
                bool(current.evidence),
            ):
                best_by_value[marker] = candidate

        distinct = list(best_by_value.values())
        distinct.sort(
            key=lambda item: (item.confidence, item.priority, bool(item.evidence)),
            reverse=True,
        )
        chosen = distinct[0]

        conflict_descriptions = list(conflicts_declared.get(key, []))
        credible_values = [item for item in distinct if item.confidence >= 0.75]
        if len(credible_values) > 1:
            markers = {_value_marker(item.value) for item in credible_values}
            if len(markers) > 1:
                rendered = ", ".join(str(item.value)[:90] for item in credible_values[:3])
                conflict_descriptions.append(
                    f"Reanalysis conserva lecturas incompatibles para {key}: {rendered}."
                )

        if conflict_descriptions:
            fact_models[key] = ValidatedFact(
                value=chosen.value,
                status=FactStatus.CONFLICTED,
                confidence=chosen.confidence,
                sources=(
                    _source_references(document_ids, page_by_document, chosen)
                    if document_ids
                    else []
                ),
                conflicts=conflict_descriptions,
                notes=[_candidate_note(chosen)],
            )
            conflicted.append(key)
            global_conflicts.extend(conflict_descriptions)
            continue

        reason: Optional[str] = None
        if key in unresolved_declared:
            reason = "Reanalysis declaró el campo como ausente o no resuelto"
        elif not document_ids:
            reason = "No hay documentos de procedencia enlazados"
        elif chosen.method == "handwritten_precision" and low_handwriting_quality:
            reason = "La calidad manuscrita exige revisión humana"
        elif reanalysis_method_is_model_derived(chosen.method):
            reason = "La lectura derivada de modelo exige revisión documental del operador"
        elif chosen.confidence < _threshold(chosen):
            reason = "La confianza no alcanza el umbral de validación"
        elif not chosen.evidence:
            reason = "No existe fragmento o evidencia visual conservada"

        if reason:
            fact_models[key] = ValidatedFact(
                value=None,
                status=FactStatus.UNRESOLVED,
                confidence=chosen.confidence,
                sources=(
                    _source_references(document_ids, page_by_document, chosen)
                    if document_ids
                    else []
                ),
                notes=[reason, _candidate_note(chosen)],
            )
            unresolved.append(key)
            continue

        fact_models[key] = ValidatedFact(
            value=chosen.value,
            status=FactStatus.VALIDATED,
            confidence=chosen.confidence,
            sources=_source_references(document_ids, page_by_document, chosen),
            notes=[
                f"Promovido por {REANALYSIS_ADAPTER_VERSION}; la congelación corresponde a OPS."
            ],
        )
        accepted.append(key)

    snapshot = ValidatedFacts(
        case_id=case_id,
        service="traffic",
        extractor_version=f"{extractor_version}+{REANALYSIS_ADAPTER_VERSION}",
        facts=fact_models,
        unresolved=sorted(set(unresolved)),
        conflicts=sorted(set(global_conflicts)),
        source_document_ids=document_ids,
        frozen=False,
    )
    assert_reanalysis_draft_is_safe(snapshot)
    return ReanalysisAdapterResult(
        facts=snapshot,
        accepted_fields=sorted(accepted),
        unresolved_fields=sorted(unresolved),
        conflicted_fields=sorted(conflicted),
        ignored_fields=sorted(ignored_fields),
        source_document_ids=document_ids,
        warnings=warnings,
    )


def load_latest_reanalysis_snapshot(
    conn,
    case_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Carga extracción y evento correspondientes sin ejecutar Reanalysis."""

    row = conn.execute(
        text(
            """
            SELECT extracted_json, model, created_at
            FROM extractions
            WHERE case_id=:case_id
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No existe extracción para el expediente")

    wrapper = _mapping(row[0])
    core = _mapping(wrapper.get("extracted"))
    extractor_version = str(core.get("extractor_version") or "").strip()
    if not extractor_version.startswith(_SUPPORTED_EXTRACTOR_PREFIX):
        raise HTTPException(
            status_code=409,
            detail="La última extracción no corresponde al Reanalysis validado",
        )
    try:
        reanalysis_run_id = str(
            uuid.UUID(str(wrapper.get("reanalysis_run_id") or ""))
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="La última extracción no conserva una finalización verificable",
        ) from exc

    event_row = conn.execute(
        text(
            """
            SELECT payload, created_at
            FROM events
            WHERE case_id=:case_id
              AND type='case_reanalysis_completed'
              AND payload->>'reanalysis_run_id'=:reanalysis_run_id
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id, "reanalysis_run_id": reanalysis_run_id},
    ).fetchone()
    if not event_row:
        raise HTTPException(
            status_code=409,
            detail="La última extracción no conserva una finalización verificable",
        )
    event = _mapping(event_row[0])
    try:
        event_run_id = str(
            uuid.UUID(str(event.get("reanalysis_run_id") or ""))
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="La última extracción no conserva una finalización verificable",
        ) from exc
    event_version = str(event.get("extractor_version") or "").strip()
    if event_version != extractor_version or event_run_id != reanalysis_run_id:
        # Nunca se mezcla una extracción con el evento de otra ejecución.
        raise HTTPException(
            status_code=409,
            detail="La última extracción no conserva una finalización verificable",
        )

    document_ids, _ = _document_sources(wrapper)
    if document_ids:
        rows = conn.execute(
            text("SELECT CAST(id AS TEXT) FROM documents WHERE case_id=:case_id"),
            {"case_id": case_id},
        ).fetchall()
        available = {str(item[0]) for item in rows}
        foreign = sorted(set(document_ids) - available)
        if foreign:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "La extracción enlaza documentos ajenos o inexistentes",
                    "document_ids": foreign,
                },
            )

    return wrapper, event
