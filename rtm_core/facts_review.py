"""Corrección humana de borradores de multas con sustitución atómica de versión."""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from case_authority import verify_signed_case_authority
from rtm_core import authority_repository as repository
from rtm_core.contracts import SourceReference, ValidatedFact, ValidatedFacts, FactStatus

REVIEW_VERSION = "rtm_traffic_facts_review_v1_1"
TEXT_FIELDS = frozenset({
    "organismo", "expediente_ref", "matricula", "hecho_denunciado_literal",
    "lugar_infraccion", "hora_infraccion", "tipo_documento", "fase_procedimental",
    "norma_hint", "articulo_infringido_num", "apartado_infringido_num",
    "ordenanza_aplicable", "senalizacion_estacionamiento", "horario_estacionamiento",
    "autorizacion_estacionamiento", "tipo_denunciante", "prueba_estacionamiento",
    "contradiccion_estacionamiento",
})
DATE_FIELDS = frozenset({"fecha_notificacion", "fecha_documento", "fecha_infraccion", "fecha_limite"})
NUMBER_FIELDS = frozenset({"sancion_importe_eur", "importe_reducido_eur", "velocidad_medida_kmh", "velocidad_limite_kmh"})
INTEGER_FIELDS = frozenset({"puntos_detraccion", "plazo_pago_dias"})
BOOLEAN_FIELDS = frozenset({"pago_multa_reducido", "fotografia_vehiculo_presente"})
REVIEW_FIELDS = TEXT_FIELDS | DATE_FIELDS | NUMBER_FIELDS | INTEGER_FIELDS | BOOLEAN_FIELDS


class FactCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    field: str
    operation: Literal["correct", "add"] = "correct"
    value: Any
    document_id: str
    page_index: int = Field(ge=0, le=9999)
    evidence: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_correction(self):
        if self.field not in REVIEW_FIELDS:
            raise ValueError("Este campo todavía no admite corrección desde el formulario")
        if str(UUID(self.document_id)) != self.document_id:
            raise ValueError("Identificador documental no canónico")
        if self.field in TEXT_FIELDS:
            if not isinstance(self.value, str) or not 1 <= len(self.value.strip()) <= 4000:
                raise ValueError("El valor debe ser texto no vacío, de hasta 4000 caracteres")
            self.value = self.value.strip()
        elif self.field in DATE_FIELDS:
            if not isinstance(self.value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
                raise ValueError("La fecha debe tener formato AAAA-MM-DD")
            date.fromisoformat(self.value)
        elif self.field in INTEGER_FIELDS:
            if type(self.value) is not int or not 0 <= self.value <= 100000:
                raise ValueError("El valor debe ser un entero no negativo")
        elif self.field in BOOLEAN_FIELDS:
            if type(self.value) is not bool:
                raise ValueError("Selecciona explícitamente Sí o No para el dato documentado")
        elif type(self.value) not in (int, float) or not math.isfinite(self.value) or not 0 <= self.value <= 100000000:
            raise ValueError("El valor debe ser un número finito no negativo")
        return self


class ReviewFactsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    expected_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=3, max_length=2000)
    changes: list[FactCorrection] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({item.field for item in self.changes}) != len(self.changes):
            raise ValueError("No puede corregirse el mismo campo dos veces en una petición")
        return self


def corrected_snapshot(previous, body: ReviewFactsBody) -> ValidatedFacts:
    payload = previous.facts.model_dump(mode="python")
    resolved = {item.field for item in body.changes}
    old_conflicts = set()
    for item in body.changes:
        existing = previous.facts.facts.get(item.field)
        if item.operation == "correct" and existing is None:
            raise HTTPException(409, "El campo no pertenece a esta versión de hechos")
        if item.operation == "add" and existing is not None:
            raise HTTPException(409, "El dato ya existe. Recarga los hechos y utiliza Revisar")
        if item.document_id not in previous.facts.source_document_ids:
            raise HTTPException(409, "El documento no pertenece a la procedencia de esta versión")
        if existing is not None:
            old_conflicts.update(existing.conflicts)
        # La lectura de IA se conserva en la versión anterior. La nueva fuente
        # recoge la ubicación contrastada por el operador, no una página inferida.
        payload["facts"][item.field] = ValidatedFact(
            value=item.value, status=FactStatus.VALIDATED, confidence=1.0,
            sources=[SourceReference(
                document_id=item.document_id, page_index=item.page_index,
                source_type="operator_document_review", extraction_method="ops_document_review_v1",
                evidence=item.evidence, confidence=1.0,
            )],
            notes=[f"{'Incorporación' if item.operation == 'add' else 'Revisión'} documental de la versión {previous.id}.", body.reason],
        ).model_dump(mode="python")
    payload["unresolved"] = [key for key in previous.facts.unresolved if key not in resolved]
    remaining_conflicts = [conflict for key, fact in previous.facts.facts.items()
                           if key not in resolved for conflict in fact.conflicts]
    payload["conflicts"] = list(dict.fromkeys(
        [item for item in previous.facts.conflicts if item not in old_conflicts] + remaining_conflicts
    ))
    payload.update(frozen=False, created_at=repository.utcnow(), supersedes_version=previous.facts.version)
    return ValidatedFacts.model_validate(payload)


def review_facts(conn, *, case_id: str, facts_id: str, body: ReviewFactsBody, actor: str):
    """El llamador debe usar una única transacción, incluido cualquier rollback."""
    meta = repository._case_authority_meta(conn, case_id, for_update=True)
    repository._require_authority_work_allowed(meta)
    if meta["department"] != "traffic" or meta["case_type"] != "fine":
        raise HTTPException(409, "Este formulario corresponde a recursos de multa")
    verify_signed_case_authority(conn, case_id)
    previous = repository.latest_validated_facts(conn, case_id, active_only=True, for_update=True)
    if not previous or previous.id != facts_id or previous.payload_sha256 != body.expected_payload_sha256:
        raise HTTPException(409, "La versión ha cambiado. Recarga los hechos antes de guardar")
    if previous.frozen:
        raise HTTPException(409, "Esta versión está cerrada; no se puede corregir como borrador")
    updated = corrected_snapshot(previous, body)
    allowed = {str(row[0]) for row in conn.execute(text(
        "SELECT id FROM documents WHERE case_id=:id AND kind='original'"
    ), {"id": case_id}).fetchall()}
    if any(item.document_id not in allowed for item in body.changes):
        raise HTTPException(409, "La corrección requiere un documento original de este expediente")
    repository.invalidate_validated_facts(conn, case_id, previous.id, actor, body.reason)
    record = repository.create_validated_facts(
        conn, case_id=case_id, facts=updated, created_by=actor, supersedes_id=previous.id,
    )
    repository._append_event(conn, case_id, "rtm_validated_facts_reviewed", {
        "review_version": REVIEW_VERSION, "actor": actor,
        "previous_facts_id": previous.id, "facts_id": record.id,
        "previous_payload_sha256": previous.payload_sha256,
        "payload_sha256": record.payload_sha256,
        "reviewed_fields": sorted(item.field for item in body.changes),
        "added_fields": sorted(item.field for item in body.changes if item.operation == "add"),
        "corrected_fields": sorted(item.field for item in body.changes if item.operation == "correct"),
    })
    return record
