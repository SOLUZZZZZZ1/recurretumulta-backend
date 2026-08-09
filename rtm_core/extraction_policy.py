"""Política conservadora para elegir una lectura profunda de Reanalysis.

No resuelve la familia jurídica. Únicamente decide si existe evidencia factual
suficiente para ejecutar un extractor profundo de Velocidad o Semáforo. Cualquier
formulario manuscrito, texto ambiguo, conflicto o señal meramente impresa pasa
por la ruta documental genérica y la revisión humana posterior.
"""

from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Optional


EXTRACTION_POLICY_VERSION = "rtm_extraction_route_policy_v1_0"

ExtractionRoute = Literal["velocidad", "semaforo", "traffic_generic"]

_FACT_TEXT_KEYS = (
    "hecho_imputado",
    "hecho_denunciado_literal",
    "hecho_denunciado_resumido",
    "hecho_para_recurso",
    "hecho_validado",
    "conducta_imputada",
    "descripcion_infraccion",
    "descripcion_hecho",
    "observacion_agente_validada",
)

_LEGACY_AUTHORITY_KEYS = (
    "familia_resuelta",
    "tipo_infraccion",
    "familia",
    "family",
    "specialist_dispatch",
    "classifier_result",
    "classification",
)

_SPEED_PATTERNS = (
    r"\b(?:exceso|exces)\s+de\s+(?:la\s+)?velocidad\b",
    r"\b(?:superar|rebasar|sobrepasar)\b.{0,70}\b(?:velocidad|maxima|limit)\w*\b",
    r"\b(?:velocidad|velocitat)\s+(?:captada|detectada|registrada|medida)\b.{0,45}\b\d{2,3}\s*km\s*/?\s*h\b",
    r"\b(?:circulaba|circulando|circular|conducia|conduciendo)\b.{0,55}\b\d{2,3}\s*km\s*/?\s*h\b.{0,90}\b(?:limit|maxim|permitid|autoritzad)\w*\b",
    r"\b\d{2,3}\s*km\s*/?\s*h\b.{0,90}\b(?:limit|maxim|permitid|autoritzad)\w*\b.{0,45}\b\d{2,3}\s*km\s*/?\s*h\b",
)

_SEMAPHORE_PATTERNS = (
    r"\b(?:no\s+respetar|no\s+respectar|rebasar|franquear|sobrepasar)\b.{0,85}\b(?:semafor|semaforo|luz|llum)\w*\b.{0,55}\b(?:roja|rojo|vermell|vermella)\b",
    r"\b(?:semafor|semaforo)\w*\b.{0,60}\b(?:fase|luz|llum)\b.{0,25}\b(?:roja|rojo|vermell|vermella)\b",
    r"\b(?:luz|llum)\s+(?:roja|vermella)\b.{0,75}\b(?:semafor|semaforo|detencion|detencio)\w*\b",
)

_OTHER_SPECIFIC_PATTERNS = (
    r"\b(?:conduccion|conduccio|conducir|conduir)\b.{0,35}\btemerari\w*\b",
    r"\b(?:telefono|telefon)\b.{0,25}\b(?:movil|mobil)\b",
    r"\b(?:cinturon|cinturo)\b",
    r"\b(?:conduccion|conduccio)\b.{0,35}\bnegligent\w*\b",
)

_LOW_QUALITY_KEY_TOKENS = (
    "handwritten",
    "manuscript",
    "manuscrito",
    "manuscrita",
    "legibility",
    "readability",
    "low_quality",
    "low_legibility",
    "document_quality",
    "requires_human_review",
)

_LOW_QUALITY_VALUE_TOKENS = (
    "low",
    "poor",
    "baja",
    "bajo",
    "ilegible",
    "unreadable",
    "handwritten",
    "manuscript",
    "manuscrito",
    "manuscrita",
)


@dataclass(frozen=True)
class ExtractionRouteDecision:
    route: ExtractionRoute
    version: str = EXTRACTION_POLICY_VERSION
    evidence: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    low_legibility_or_handwritten: bool = False
    ignored_legacy_values: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_LAST_DECISION: ContextVar[Optional[ExtractionRouteDecision]] = ContextVar(
    "rtm_last_extraction_route_decision",
    default=None,
)


def _fold(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _factual_texts(core: Mapping[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key in _FACT_TEXT_KEYS:
        value = core.get(key)
        if isinstance(value, str) and value.strip():
            result.append((key, _fold(value)))
    return result


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _quality_signal(key: str, value: Any) -> bool:
    key_folded = _fold(key).replace(" ", "_")
    if not any(token in key_folded for token in _LOW_QUALITY_KEY_TOKENS):
        return False

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if any(token in key_folded for token in ("legibility", "readability", "quality")):
            return float(value) < 0.85
        return False
    if isinstance(value, Mapping):
        return any(_quality_signal(str(subkey), subvalue) for subkey, subvalue in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(_quality_signal(key, item) for item in value)

    folded = _fold(value)
    return any(token in folded for token in _LOW_QUALITY_VALUE_TOKENS)


def _is_low_legibility_or_handwritten(core: Mapping[str, Any]) -> bool:
    for key, value in core.items():
        if _quality_signal(str(key), value):
            return True

    reasons = core.get("operator_review_reasons")
    if isinstance(reasons, (list, tuple, set)):
        blob = " ".join(_fold(item) for item in reasons)
        if any(token in blob for token in _LOW_QUALITY_VALUE_TOKENS):
            return True
    return False


def _legacy_values(core: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in _LEGACY_AUTHORITY_KEYS:
        value = core.get(key)
        if value not in (None, "", [], {}):
            values.append(f"{key}={str(value)[:80]}")
    return tuple(values)


def _structured_red_phase(core: Mapping[str, Any], factual_blob: str) -> bool:
    value = core.get("semaforo_fase")
    if value in (None, "", [], {}):
        value = core.get("fase_semaforo")
    phase = _fold(value)
    return (
        phase in {"roja", "rojo", "red", "vermell", "vermella"}
        and bool(re.search(r"\b(?:semafor|semaforo)\w*\b", factual_blob))
    )


def _structured_speed_pair_is_corrobated(core: Mapping[str, Any], factual_blob: str) -> bool:
    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")
    try:
        measured_number = int(float(measured))
        limit_number = int(float(limit))
    except Exception:
        return False

    if not (20 <= measured_number <= 300 and 10 <= limit_number <= 200):
        return False
    if measured_number <= limit_number:
        return False

    measured_pattern = rf"\b{measured_number}\s*km\s*/?\s*h\b"
    limit_pattern = rf"\b{limit_number}\s*km\s*/?\s*h\b"
    has_both_numbers = bool(
        re.search(measured_pattern, factual_blob)
        and re.search(limit_pattern, factual_blob)
    )
    has_explicit_context = _matches(_SPEED_PATTERNS, factual_blob)
    return has_both_numbers and has_explicit_context


def decide_extraction_route(
    core: Mapping[str, Any],
    text_blob: str = "",
) -> ExtractionRouteDecision:
    """Decide solo la ruta de extracción profunda.

    ``text_blob`` se acepta por compatibilidad con Reanalysis, pero nunca se usa
    como prueba: puede contener etiquetas fijas del formulario, OCR crudo o
    casillas impresas como ``km/h``.
    """

    core_map = dict(core or {})
    factual_items = _factual_texts(core_map)
    factual_blob = " ".join(text for _, text in factual_items)
    evidence_keys = tuple(key for key, _ in factual_items)
    ignored_legacy = _legacy_values(core_map)
    low_quality = _is_low_legibility_or_handwritten(core_map)

    speed_signal = _matches(_SPEED_PATTERNS, factual_blob) or _structured_speed_pair_is_corrobated(
        core_map,
        factual_blob,
    )
    semaphore_signal = _matches(_SEMAPHORE_PATTERNS, factual_blob) or _structured_red_phase(
        core_map,
        factual_blob,
    )
    other_specific_signal = _matches(_OTHER_SPECIFIC_PATTERNS, factual_blob)

    reasons: list[str] = ["raw_document_text_ignored"]
    conflicts: list[str] = []

    if ignored_legacy:
        reasons.append("legacy_family_values_ignored")
    if low_quality:
        reasons.append("handwritten_or_low_legibility_requires_generic_first")
        decision = ExtractionRouteDecision(
            route="traffic_generic",
            evidence=evidence_keys,
            reasons=tuple(reasons),
            low_legibility_or_handwritten=True,
            ignored_legacy_values=ignored_legacy,
        )
        _LAST_DECISION.set(decision)
        return decision

    signals = [
        name
        for name, active in (
            ("velocidad", speed_signal),
            ("semaforo", semaphore_signal),
            ("otra_familia_especifica", other_specific_signal),
        )
        if active
    ]
    if len(signals) > 1:
        conflicts.append("multiple_specific_factual_signals:" + ",".join(signals))
        reasons.append("conflict_requires_generic_first")
        decision = ExtractionRouteDecision(
            route="traffic_generic",
            evidence=evidence_keys,
            reasons=tuple(reasons),
            conflicts=tuple(conflicts),
            ignored_legacy_values=ignored_legacy,
        )
        _LAST_DECISION.set(decision)
        return decision

    if speed_signal:
        reasons.append("explicit_speed_fact")
        decision = ExtractionRouteDecision(
            route="velocidad",
            evidence=evidence_keys,
            reasons=tuple(reasons),
            ignored_legacy_values=ignored_legacy,
        )
        _LAST_DECISION.set(decision)
        return decision

    if semaphore_signal:
        reasons.append("explicit_red_light_fact")
        decision = ExtractionRouteDecision(
            route="semaforo",
            evidence=evidence_keys,
            reasons=tuple(reasons),
            ignored_legacy_values=ignored_legacy,
        )
        _LAST_DECISION.set(decision)
        return decision

    if other_specific_signal:
        reasons.append("non_deep_specific_fact_deferred_to_family_core")
    else:
        reasons.append("no_explicit_deep_extractor_fact")

    decision = ExtractionRouteDecision(
        route="traffic_generic",
        evidence=evidence_keys,
        reasons=tuple(reasons),
        ignored_legacy_values=ignored_legacy,
    )
    _LAST_DECISION.set(decision)
    return decision


def select_deep_extraction_route(core: Mapping[str, Any], text_blob: str = "") -> str:
    """Firma compatible con el selector legacy de Reanalysis."""

    return decide_extraction_route(core, text_blob).route


def last_extraction_route_decision() -> Optional[ExtractionRouteDecision]:
    return _LAST_DECISION.get()
