"""Normalización documental común para satélites RTM no tráfico.

Consume observaciones estructuradas con documento, página, evidencia y confianza
y devuelve un borrador ``ValidatedFacts``. No clasifica familias, no decide
estrategia, no redacta y nunca congela hechos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rtm_core.contracts import (
    FactStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.document_fact_catalog import (
    DOCUMENT_FACT_CATALOG_VERSION,
    FactFieldSpec,
    canonical_document_service,
    field_spec,
    minimum_fact_keys,
)
from rtm_core.service_catalog import normalize_code


DOCUMENT_NORMALIZATION_VERSION = "rtm_document_normalization_v1_0"
DOCUMENT_EXTRACTION_PACKET_VERSION = "rtm_document_extraction_packet_v1_0"

SourceType = Literal[
    "document_text",
    "document_vision",
    "deterministic_document",
    "operator_document_review",
    "ocr",
    "client_statement",
]

_BANNED_TOKENS = {
    "raw", "ocr", "prompt", "family", "familia", "classifier",
    "classification", "scoring", "strategy", "estrategia", "draft",
    "borrador", "legal_argument", "recommended_action", "ready_for_generate",
}
_BANNED_METHOD_TOKENS = {
    "family", "familia", "classifier", "classification", "scoring",
    "strategy", "draft", "generate",
}
_CRITICAL_QUALITY = {
    "unreadable", "ilegible", "low_legibility", "baja_legibilidad",
    "handwritten", "manuscrito", "manuscrita", "truncated", "cortado",
    "ocr_only",
    "untrusted_instruction_pattern_detected",
    "unanchored_document_evidence",
    "visual_evidence_requires_operator",
    "model_evidence_requires_operator",
}
_AUTOMATIC_SOURCES = {
    "document_text", "document_vision", "deterministic_document",
    "operator_document_review",
}
_SOURCE_PRIORITY = {
    "operator_document_review": 100,
    "document_vision": 80,
    "deterministic_document": 75,
    "document_text": 70,
    "ocr": 30,
    "client_statement": 10,
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DocumentObservation(_StrictModel):
    field: str = Field(min_length=1)
    value: Any = None
    document_id: str = Field(min_length=1)
    page_index: Optional[int] = Field(default=None, ge=0)
    evidence: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    extraction_method: str = Field(min_length=1)
    source_type: SourceType = "document_vision"
    notes: list[str] = Field(default_factory=list)


class DocumentExtractionPacket(_StrictModel):
    authority: Literal["rtm_document_extraction_packet"] = (
        "rtm_document_extraction_packet"
    )
    version: str = DOCUMENT_EXTRACTION_PACKET_VERSION
    case_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    source_document_ids: list[str] = Field(min_length=1)
    observations: list[DocumentObservation] = Field(default_factory=list)
    declared_unresolved: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_packet(self) -> "DocumentExtractionPacket":
        canonical_document_service(self.service)
        ids = [str(item).strip() for item in self.source_document_ids]
        if any(not item for item in ids):
            raise ValueError("source_document_ids contiene valores vacíos")
        if len(ids) != len(set(ids)):
            raise ValueError("source_document_ids contiene duplicados")
        foreign = sorted({
            item.document_id
            for item in self.observations
            if item.document_id not in set(ids)
        })
        if foreign:
            raise ValueError(
                "Observaciones vinculadas a documentos no declarados: "
                + ", ".join(foreign)
            )
        return self


class RejectedObservation(_StrictModel):
    field: str
    document_id: str
    reason: str


class DocumentNormalizationResult(_StrictModel):
    authority: Literal["rtm_document_normalization"] = (
        "rtm_document_normalization"
    )
    version: str = DOCUMENT_NORMALIZATION_VERSION
    catalog_version: str = DOCUMENT_FACT_CATALOG_VERSION
    facts: ValidatedFacts
    accepted_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicted_fields: list[str] = Field(default_factory=list)
    rejected_observations: list[RejectedObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _Candidate:
    spec: FactFieldSpec
    value: Any
    observation: DocumentObservation
    eligible: bool
    reasons: tuple[str, ...]


def _fold(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", raw.lower().replace("\r", " ").replace("\n", " ")).strip()


def _clean(value: Any, max_length: int) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip(" \t\r\n:;")
    if not text or len(text) > max_length:
        return None
    return text


def _identifier(value: Any, max_length: int) -> Optional[str]:
    text = _clean(value, max_length)
    if text is None or not re.search(r"[A-Za-z0-9]", text):
        return None
    return text


def _money(value: Any, allow_negative: bool) -> Optional[int | float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        raw = str(value)
    else:
        raw = str(value).upper().replace("EUR", "").replace("€", "")
    raw = re.sub(r"[^0-9,.\-+]", "", raw)
    if not raw or raw in {"-", "+", ".", ","}:
        return None
    sign = ""
    if raw[:1] in {"+", "-"}:
        sign, raw = raw[0], raw[1:]
    if "-" in raw or "+" in raw:
        return None
    if "," in raw and "." in raw:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif "," in raw or "." in raw:
        separator = "," if "," in raw else "."
        parts = raw.split(separator)
        if len(parts) > 2:
            if len(parts[-1]) in {1, 2}:
                raw = "".join(parts[:-1]) + "." + parts[-1]
            else:
                raw = "".join(parts)
        else:
            left, right = parts
            raw = left + "." + right if len(right) in {1, 2} else left + right
    try:
        amount = Decimal(sign + raw)
    except InvalidOperation:
        return None
    if amount < 0 and not allow_negative:
        return None
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def _date(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    patterns = (
        (r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", (1, 2, 3)),
        (r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", (3, 2, 1)),
        (r"^(\d{4})(\d{2})(\d{2})$", (1, 2, 3)),
    )
    for pattern, order in patterns:
        match = re.match(pattern, raw)
        if not match:
            continue
        year, month, day = (int(match.group(index)) for index in order)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _time(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0).strftime("%H:%M")
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0).strftime("%H:%M")
    match = re.fullmatch(
        r"\s*(\d{1,2})[:h.](\d{2})(?::\d{2})?\s*(?:h|hrs?)?\s*",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _integer(value: Any, allow_negative: bool) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, float) and value.is_integer():
        result = int(value)
    else:
        match = re.fullmatch(
            r"\s*([+-]?\d+)\s*(?:d[ií]as?|pasajeros?|personas?)?\s*",
            str(value),
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        result = int(match.group(1))
    return result if allow_negative or result >= 0 else None


def _boolean(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {"si", "true", "1", "consta", "pagada", "pagado"}:
        return True
    if folded in {"no", "false", "0", "no consta", "no pagada", "no pagado"}:
        return False
    return None


def _normalise(spec: FactFieldSpec, value: Any) -> Any:
    if spec.value_type == "text":
        return _clean(value, spec.max_length)
    if spec.value_type == "identifier":
        return _identifier(value, spec.max_length)
    if spec.value_type in {"money", "number"}:
        return _money(value, spec.allow_negative)
    if spec.value_type == "date":
        return _date(value)
    if spec.value_type == "time":
        return _time(value)
    if spec.value_type == "integer":
        return _integer(value, spec.allow_negative)
    if spec.value_type == "boolean":
        return _boolean(value)
    return None


def _marker(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )


def _forbidden(value: str, tokens: set[str]) -> bool:
    folded = normalize_code(value)
    return any(token in folded for token in tokens)


def _source(item: _Candidate) -> SourceReference:
    observation = item.observation
    evidence = re.sub(r"\s+", " ", str(observation.evidence or "")).strip()
    if len(evidence) > 500:
        evidence = evidence[:497] + "..."
    return SourceReference(
        document_id=observation.document_id,
        page_index=observation.page_index,
        source_type=observation.source_type,
        extraction_method=observation.extraction_method,
        evidence=evidence or None,
        confidence=observation.confidence,
    )


def _sources(items: list[_Candidate]) -> list[SourceReference]:
    result: list[SourceReference] = []
    seen: set[tuple[Any, ...]] = set()
    for item in sorted(
        items,
        key=lambda candidate: (
            candidate.observation.confidence,
            _SOURCE_PRIORITY.get(candidate.observation.source_type, 0),
        ),
        reverse=True,
    ):
        source = _source(item)
        key = (
            source.document_id, source.page_index, source.source_type,
            source.extraction_method, source.evidence, source.confidence,
        )
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result


def _note(item: _Candidate) -> str:
    value = str(item.observation.value)
    if len(value) > 180:
        value = value[:177] + "..."
    reason = ", ".join(item.reasons) if item.reasons else "eligible"
    return (
        f"Candidato {item.observation.source_type} "
        f"({item.observation.confidence:.2f}; {reason}): {value}"
    )


def normalize_document_packet(
    packet: DocumentExtractionPacket,
) -> DocumentNormalizationResult:
    service = canonical_document_service(packet.service)
    rejected: list[RejectedObservation] = []
    declared_unresolved: set[str] = set()
    for raw_key in packet.declared_unresolved:
        spec = field_spec(service, raw_key)
        if spec is None:
            rejected.append(
                RejectedObservation(
                    field=str(raw_key),
                    document_id="",
                    reason="declared_unresolved_field_not_registered",
                )
            )
        else:
            declared_unresolved.add(spec.key)

    quality = {
        normalize_code(flag) for flag in packet.quality_flags if normalize_code(flag)
    }
    critical_quality = bool(quality & _CRITICAL_QUALITY)
    candidates: dict[str, list[_Candidate]] = {}

    for observation in packet.observations:
        if _forbidden(observation.field, _BANNED_TOKENS):
            rejected.append(
                RejectedObservation(
                    field=observation.field,
                    document_id=observation.document_id,
                    reason="field_is_not_a_document_fact",
                )
            )
            continue
        spec = field_spec(service, observation.field)
        if spec is None:
            rejected.append(
                RejectedObservation(
                    field=observation.field,
                    document_id=observation.document_id,
                    reason="field_not_registered_for_service",
                )
            )
            continue

        value = _normalise(spec, observation.value)
        reasons: list[str] = []
        if value is None:
            reasons.append("value_not_safely_normalized")
        if not (observation.evidence or "").strip():
            reasons.append("document_evidence_missing")
        if observation.confidence < spec.min_confidence:
            reasons.append(
                f"confidence_below_threshold:{observation.confidence:.2f}"
                f"<{spec.min_confidence:.2f}"
            )
        if observation.source_type not in _AUTOMATIC_SOURCES:
            reasons.append(f"source_requires_operator:{observation.source_type}")
        if _forbidden(observation.extraction_method, _BANNED_METHOD_TOKENS):
            reasons.append("non_documentary_extraction_method")
        if spec.key in declared_unresolved:
            reasons.append("extractor_declared_unresolved")
        if critical_quality and observation.source_type != "operator_document_review":
            reasons.append("document_quality_requires_operator")
        if (
            spec.require_operator_if_handwritten
            and quality & {"handwritten", "manuscrito", "manuscrita"}
            and observation.source_type != "operator_document_review"
        ):
            reasons.append("handwritten_critical_field_requires_operator")

        candidates.setdefault(spec.key, []).append(
            _Candidate(
                spec=spec,
                value=value,
                observation=observation,
                eligible=not reasons,
                reasons=tuple(reasons),
            )
        )

    facts: dict[str, ValidatedFact] = {}
    accepted: list[str] = []
    unresolved: list[str] = []
    conflicted: list[str] = []
    global_conflicts: list[str] = []
    document_order = {
        document_id: index
        for index, document_id in enumerate(packet.source_document_ids)
    }

    for key in sorted(candidates):
        values = candidates[key]
        spec = values[0].spec
        deduplicated: dict[tuple[str, str, int | None, str], _Candidate] = {}
        for item in values:
            marker = (
                _marker(item.value), item.observation.document_id,
                item.observation.page_index, item.observation.extraction_method,
            )
            current = deduplicated.get(marker)
            rank = (
                item.observation.confidence,
                _SOURCE_PRIORITY.get(item.observation.source_type, 0),
            )
            if current is None or rank > (
                current.observation.confidence,
                _SOURCE_PRIORITY.get(current.observation.source_type, 0),
            ):
                deduplicated[marker] = item
        values = list(deduplicated.values())
        eligible = [item for item in values if item.eligible]

        if spec.merge_mode == "set":
            best_by_value: dict[str, _Candidate] = {}
            for item in eligible:
                marker = _marker(item.value)
                current = best_by_value.get(marker)
                rank = (
                    item.observation.confidence,
                    _SOURCE_PRIORITY.get(item.observation.source_type, 0),
                )
                if current is None or rank > (
                    current.observation.confidence,
                    _SOURCE_PRIORITY.get(current.observation.source_type, 0),
                ):
                    best_by_value[marker] = item
            selected = sorted(
                best_by_value.values(),
                key=lambda item: (
                    document_order.get(item.observation.document_id, 10**9),
                    item.observation.page_index
                    if item.observation.page_index is not None else 10**9,
                    -item.observation.confidence,
                    _marker(item.value),
                ),
            )
            if selected:
                output_values = [item.value for item in selected]
                selected_markers = {_marker(item.value) for item in selected}
                facts[key] = ValidatedFact(
                    value=output_values[0] if len(output_values) == 1 else output_values,
                    status=FactStatus.VALIDATED,
                    confidence=min(item.observation.confidence for item in selected),
                    sources=_sources([
                        item for item in eligible
                        if _marker(item.value) in selected_markers
                    ]),
                    notes=[
                        f"Campo repetible normalizado por "
                        f"{DOCUMENT_NORMALIZATION_VERSION}; "
                        "la congelación corresponde exclusivamente a OPS."
                    ],
                )
                accepted.append(key)
                continue

        credible = [
            item for item in values
            if item.value is not None
            and item.observation.confidence >= 0.75
            and bool((item.observation.evidence or "").strip())
        ]
        if spec.merge_mode == "single" and len({_marker(item.value) for item in credible}) > 1:
            rendered = ", ".join(
                repr(item.value)
                for item in sorted(
                    credible,
                    key=lambda candidate: candidate.observation.confidence,
                    reverse=True,
                )[:4]
            )
            conflict = f"Lecturas documentales incompatibles para {key}: {rendered}."
            facts[key] = ValidatedFact(
                value=None,
                status=FactStatus.CONFLICTED,
                confidence=max(item.observation.confidence for item in credible),
                sources=_sources(credible),
                conflicts=[conflict],
                notes=[_note(item) for item in credible[:6]],
            )
            conflicted.append(key)
            global_conflicts.append(conflict)
            continue

        eligible_markers = {_marker(item.value) for item in eligible}
        if len(eligible_markers) == 1 and eligible:
            chosen = max(
                eligible,
                key=lambda item: (
                    item.observation.confidence,
                    _SOURCE_PRIORITY.get(item.observation.source_type, 0),
                ),
            )
            same_value = [
                item for item in eligible if _marker(item.value) == _marker(chosen.value)
            ]
            facts[key] = ValidatedFact(
                value=chosen.value,
                status=FactStatus.VALIDATED,
                confidence=max(item.observation.confidence for item in same_value),
                sources=_sources(same_value),
                notes=[
                    f"Normalizado por {DOCUMENT_NORMALIZATION_VERSION}; "
                    "la congelación corresponde exclusivamente a OPS."
                ],
            )
            accepted.append(key)
            continue

        notes = [_note(item) for item in values[:8]]
        if key in declared_unresolved:
            notes.insert(0, "El extractor declaró expresamente el campo no resuelto.")
        facts[key] = ValidatedFact(
            value=None,
            status=FactStatus.UNRESOLVED,
            confidence=max(
                (item.observation.confidence for item in values), default=0.0
            ),
            sources=_sources(values),
            notes=notes or ["No existe una lectura documental consolidable."],
        )
        unresolved.append(key)

    for key in minimum_fact_keys(service):
        if key not in facts:
            facts[key] = ValidatedFact(
                value=None,
                status=FactStatus.UNRESOLVED,
                confidence=0.0,
                sources=[],
                notes=[
                    "Campo mínimo para el primer rumbo no extraído de los documentos."
                ],
            )
            unresolved.append(key)

    warnings: list[str] = []
    if critical_quality:
        warnings.append(
            "La calidad documental exige revisión de operador antes de validar."
        )
    if rejected:
        warnings.append(
            "Hay observaciones excluidas por no pertenecer al catálogo documental."
        )
    if not accepted:
        warnings.append("Ningún campo alcanzó el umbral de validación automática.")

    snapshot = ValidatedFacts(
        case_id=packet.case_id,
        service=service,
        extractor_version=(
            f"{packet.extractor_version}+{DOCUMENT_NORMALIZATION_VERSION}"
        ),
        facts=facts,
        unresolved=sorted(set(unresolved)),
        conflicts=sorted(set(global_conflicts)),
        source_document_ids=list(packet.source_document_ids),
        frozen=False,
    )
    return DocumentNormalizationResult(
        facts=snapshot,
        accepted_fields=sorted(set(accepted)),
        unresolved_fields=sorted(set(unresolved)),
        conflicted_fields=sorted(set(conflicted)),
        rejected_observations=rejected,
        warnings=warnings,
    )


def validate_packet_documents(
    *,
    packet: DocumentExtractionPacket,
    available_document_ids: set[str],
) -> None:
    foreign = sorted(
        set(packet.source_document_ids) - set(available_document_ids)
    )
    if foreign:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El paquete enlaza documentos ajenos o inexistentes",
                "document_ids": foreign,
            },
        )
