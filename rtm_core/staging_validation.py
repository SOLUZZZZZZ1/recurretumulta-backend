"""Validación sintética y aislada del flujo documental de satélites RTM.

Este módulo prepara pruebas de staging con documentos ficticios incluidos en el
repositorio. No lee la base de datos, no accede a B2, no usa expedientes reales,
no persiste hechos y no habilita Generate. Su única finalidad es comprobar, con
un proveedor documental real o controlado, la cadena:

    documento sintético -> extracción -> normalización -> familia -> primer rumbo

Los informes nunca incluyen el contenido del documento ni fragmentos de
evidencia; conservan únicamente huellas, versiones, campos y resultados.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rtm_core.ai_security import model_call_budget
from rtm_core.contracts import FactStatus, ResolutionStatus
from rtm_core.document_extraction import (
    DocumentProvider,
    OpenAIResponsesDocumentProvider,
    SourceDocument,
    extract_service_documents,
)
from rtm_core.document_normalization import normalize_document_packet
from rtm_core.document_provider_retry import MAX_DOCUMENT_PROVIDER_ATTEMPTS
from rtm_core.family_dispatch import resolve_family
from rtm_core.first_direction import build_first_direction
from rtm_core.specialist_dispatch import registered_specialists


STAGING_VALIDATION_VERSION = "rtm_synthetic_staging_validation_v1_0"
STAGING_FIXTURE_SET_VERSION = "rtm_synthetic_fixture_set_v1_0"
SYNTHETIC_MARKER = "DOCUMENTO SINTÉTICO RTM — SOLO PRUEBAS DE STAGING"
LIVE_CONFIRMATION = "SYNTHETIC_ONLY"

_FORBIDDEN_FACT_KEY_TOKENS = (
    "raw",
    "ocr",
    "prompt",
    "family",
    "familia",
    "classifier",
    "classification",
    "scoring",
    "strategy",
    "estrategia",
    "draft",
    "borrador",
    "legal_argument",
    "recommended_action",
    "ready_for_generate",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyntheticStagingScenario(_StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    service: str = Field(min_length=1)
    fixture_filename: str = Field(min_length=1)
    expected_family: str = Field(min_length=1)
    expected_specialist: str = Field(min_length=1)
    required_fields: tuple[str, ...] = ()
    required_any_groups: tuple[tuple[str, ...], ...] = ()
    minimum_resolution_confidence: float = Field(default=0.90, ge=0.0, le=1.0)


class SyntheticScenarioResult(_StrictModel):
    scenario: str
    service: str
    fixture_sha256: str
    provider_version: str
    model: str
    extractor_version: str
    accepted_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicted_fields: list[str] = Field(default_factory=list)
    family_status: str
    family: Optional[str] = None
    specialist: Optional[str] = None
    family_confidence: float = 0.0
    direction_source: str
    direction_maturity: str
    generation_allowed: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    passed: bool = False


class SyntheticStagingReport(_StrictModel):
    authority: str = "rtm_synthetic_staging_validation"
    version: str = STAGING_VALIDATION_VERSION
    fixture_set_version: str = STAGING_FIXTURE_SET_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    live_provider: bool = False
    provider_version: str
    model: str
    scenarios: list[SyntheticScenarioResult] = Field(default_factory=list)
    passed: bool = False


_SCENARIOS: tuple[SyntheticStagingScenario, ...] = (
    SyntheticStagingScenario(
        code="debt_unpaid_invoice",
        service="debt",
        fixture_filename="debt_invoice.txt",
        expected_family="factura_impagada",
        expected_specialist="debt.unpaid_invoice",
        required_fields=("descripcion_hecho", "factura_numero"),
        required_any_groups=(
            ("importe_deuda_eur", "saldo_pendiente_eur"),
            ("fecha_vencimiento",),
        ),
    ),
    SyntheticStagingScenario(
        code="administration_enforcement",
        service="administration",
        fixture_filename="administration_enforcement.txt",
        expected_family="apremio_recaudacion",
        expected_specialist="administration.enforcement",
        required_fields=("descripcion_hecho", "expediente_ref"),
        required_any_groups=(
            ("acto_administrativo", "tipo_documento"),
            ("importe_exigido_eur", "principal_eur"),
        ),
    ),
    SyntheticStagingScenario(
        code="travel_flight_cancelled",
        service="travel",
        fixture_filename="travel_flight_cancelled.txt",
        expected_family="vuelo_cancelado",
        expected_specialist="travel.flight_cancelled",
        required_fields=("descripcion_hecho", "numero_vuelo", "numero_reserva"),
        required_any_groups=(("fecha_vuelo", "fecha_incidencia"),),
    ),
    SyntheticStagingScenario(
        code="claims_telecommunications",
        service="claims",
        fixture_filename="claims_telecommunications.txt",
        expected_family="telecomunicaciones",
        expected_specialist="claims.telecommunications",
        required_fields=("descripcion_hecho", "proveedor"),
        required_any_groups=(
            ("baja_solicitada_fecha", "fecha_baja_efectiva"),
            ("importe_reclamado_eur", "importe_pagado_eur", "factura_numero"),
        ),
    ),
)


def fixture_root() -> Path:
    return Path(__file__).resolve().parents[1] / "staging" / "fixtures"


def staging_scenarios(
    selected_services: Optional[Iterable[str]] = None,
) -> tuple[SyntheticStagingScenario, ...]:
    if selected_services is None:
        return _SCENARIOS
    wanted = {str(value).strip().lower() for value in selected_services if str(value).strip()}
    return tuple(item for item in _SCENARIOS if item.service in wanted)


def _fixture_bytes(
    scenario: SyntheticStagingScenario,
    *,
    root: Optional[Path] = None,
) -> bytes:
    base = (root or fixture_root()).resolve()
    path = (base / scenario.fixture_filename).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="La ruta del documento sintético sale del directorio permitido.",
        ) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No existe el documento sintético {scenario.fixture_filename}.",
        )
    content = path.read_bytes()
    if SYNTHETIC_MARKER.encode("utf-8") not in content:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El documento {scenario.fixture_filename} no conserva la marca "
                "obligatoria de contenido sintético."
            ),
        )
    return content


def assert_live_synthetic_guard() -> None:
    """Impide una llamada live accidental fuera de un entorno de staging explícito."""

    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    confirmation = (os.getenv("RTM_STAGING_CONFIRM") or "").strip()
    allowed = (os.getenv("RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION") or "").strip()

    errors: list[str] = []
    if environment not in {"staging", "test"}:
        errors.append("RTM_ENV debe ser staging o test")
    if confirmation != LIVE_CONFIRMATION:
        errors.append(f"RTM_STAGING_CONFIRM debe ser {LIVE_CONFIRMATION}")
    if allowed != "1":
        errors.append("RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION debe ser 1")
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        errors.append("OPENAI_API_KEY no está configurado")

    if errors:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "La prueba live sintética de staging está bloqueada.",
                "requirements": errors,
            },
        )


def _source_document(
    scenario: SyntheticStagingScenario,
    *,
    case_id: str,
    content: bytes,
) -> SourceDocument:
    return SourceDocument(
        id=f"synthetic-document-{scenario.code}",
        case_id=case_id,
        kind="synthetic_staging_original",
        mime="text/plain",
        b2_bucket="synthetic-staging",
        b2_key=f"staging/fixtures/{scenario.fixture_filename}",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _direction_for(
    *,
    case_id: str,
    service: str,
    facts: Any,
    resolution: Any,
) -> Any:
    facts_record = {
        "id": f"synthetic-facts-{case_id}",
        "frozen": False,
        "invalidated_at": None,
        "facts": facts.model_dump(mode="json"),
    }
    family_record = {
        "id": f"synthetic-family-{case_id}",
        "locked": False,
        "invalidated_at": None,
        "resolution": resolution.model_dump(mode="json"),
    }
    next_step = {
        "stage": "initial_direction_review",
        "primary_action": "review_first_direction",
        "actions": [
            {
                "code": "review_first_direction",
                "label": "Revisar el primer rumbo sintético en staging",
            }
        ],
    }
    return build_first_direction(
        case_id=case_id,
        case_payload={
            "department": service,
            "case_type": "synthetic_staging",
            "category": service,
            "status": "family_resolved",
            "payment_status": "paid",
            "authorized": True,
        },
        readiness={"ready": True, "blocking_issues": []},
        latest_facts=facts_record,
        latest_family=family_record,
        latest_preview=None,
        next_step=next_step,
        registered_specialists=registered_specialists(),
    )


def _forbidden_fact_keys(keys: Iterable[str]) -> list[str]:
    result: list[str] = []
    for key in keys:
        folded = str(key).strip().lower()
        if any(token in folded for token in _FORBIDDEN_FACT_KEY_TOKENS):
            result.append(str(key))
    return sorted(set(result))


def run_synthetic_scenario(
    scenario: SyntheticStagingScenario,
    *,
    provider: DocumentProvider,
    root: Optional[Path] = None,
) -> SyntheticScenarioResult:
    content = _fixture_bytes(scenario, root=root)
    digest = hashlib.sha256(content).hexdigest()
    case_id = f"synthetic-case-{scenario.code}"
    document = _source_document(
        scenario,
        case_id=case_id,
        content=content,
    )

    errors: list[str] = []
    warnings: list[str] = []
    accepted: list[str] = []
    unresolved: list[str] = []
    conflicted: list[str] = []
    family_status = "unresolved"
    family: Optional[str] = None
    specialist: Optional[str] = None
    family_confidence = 0.0
    direction_source = "core_projection"
    direction_maturity = "facts_pending"
    generation_allowed = False
    extractor_version = ""

    try:
        # Cada escenario usa un único documento. El presupuesto cubre el
        # intento inicial y, como máximo, los reintentos 429 permitidos.
        with model_call_budget(MAX_DOCUMENT_PROVIDER_ATTEMPTS):
            extraction = extract_service_documents(
                case_id=case_id,
                service=scenario.service,
                documents=[document],
                provider=provider,
                byte_loader=lambda _bucket, _key: content,
            )
        extractor_version = extraction.packet.extractor_version
        warnings.extend(extraction.warnings)
        for diagnostic in extraction.diagnostics:
            warnings.extend(diagnostic.notes)
            if diagnostic.error:
                errors.append(
                    f"document_extraction_error:{diagnostic.document_id}:{diagnostic.error}"
                )

        normalization = normalize_document_packet(extraction.packet)
        accepted = list(normalization.accepted_fields)
        unresolved = list(normalization.unresolved_fields)
        conflicted = list(normalization.conflicted_fields)
        warnings.extend(normalization.warnings)

        facts = normalization.facts
        if facts.frozen:
            errors.append("La normalización sintética no puede congelar hechos.")
        forbidden = _forbidden_fact_keys(facts.facts)
        if forbidden:
            errors.append(
                "La normalización dejó entrar campos no documentales: "
                + ", ".join(forbidden)
            )

        for key, fact in facts.facts.items():
            if fact.status is not FactStatus.VALIDATED:
                continue
            if not fact.sources:
                errors.append(f"El hecho validado {key} no conserva fuentes.")
                continue
            for source in fact.sources:
                if source.document_id != document.id:
                    errors.append(
                        f"El hecho {key} apunta a un documento no sintético."
                    )
                if not (source.evidence or "").strip():
                    errors.append(
                        f"El hecho {key} no conserva fragmento de evidencia."
                    )

        missing_required = sorted(set(scenario.required_fields) - set(accepted))
        if missing_required:
            errors.append(
                "Faltan campos obligatorios del escenario: "
                + ", ".join(missing_required)
            )
        for group in scenario.required_any_groups:
            if not set(group) & set(accepted):
                errors.append(
                    "No se obtuvo ningún campo del grupo requerido: "
                    + " | ".join(group)
                )

        resolution = resolve_family(facts)
        family_status = str(getattr(resolution.status, "value", resolution.status))
        family = resolution.family
        specialist = resolution.specialist
        family_confidence = float(resolution.confidence or 0.0)

        if resolution.status is not ResolutionStatus.RESOLVED:
            errors.append(
                f"La familia no quedó resuelta: {family_status}."
            )
        if family != scenario.expected_family:
            errors.append(
                f"Familia esperada {scenario.expected_family}; obtenida {family}."
            )
        if specialist != scenario.expected_specialist:
            errors.append(
                "Especialista esperado "
                f"{scenario.expected_specialist}; obtenido {specialist}."
            )
        if family_confidence < scenario.minimum_resolution_confidence:
            errors.append(
                "Confianza de familia inferior al mínimo del escenario: "
                f"{family_confidence:.2f} < "
                f"{scenario.minimum_resolution_confidence:.2f}."
            )
        if resolution.locked:
            errors.append("La prueba sintética no puede bloquear la familia.")

        direction = _direction_for(
            case_id=case_id,
            service=scenario.service,
            facts=facts,
            resolution=resolution,
        )
        direction_source = direction.source
        direction_maturity = direction.maturity
        generation_allowed = bool(direction.generation_allowed)
        warnings.extend(direction.warnings)

        if direction.family != scenario.expected_family:
            errors.append("El primer rumbo contradice la familia resuelta.")
        if direction.source != "core_projection":
            errors.append("Sin Previa Jurídica, el primer rumbo debe ser una proyección CORE.")
        if direction.maturity != "orientation_only":
            errors.append(
                "El primer rumbo sintético debe quedar en orientation_only."
            )
        if direction.generation_allowed:
            errors.append("Generate no puede quedar habilitado en una prueba sintética.")
    except HTTPException as exc:
        errors.append(f"HTTP {exc.status_code}: {exc.detail}")
    except Exception as exc:  # pragma: no cover - defensa de informe de staging
        errors.append(f"{type(exc).__name__}: {exc}")

    return SyntheticScenarioResult(
        scenario=scenario.code,
        service=scenario.service,
        fixture_sha256=digest,
        provider_version=str(provider.version),
        model=str(provider.model),
        extractor_version=extractor_version,
        accepted_fields=sorted(set(accepted)),
        unresolved_fields=sorted(set(unresolved)),
        conflicted_fields=sorted(set(conflicted)),
        family_status=family_status,
        family=family,
        specialist=specialist,
        family_confidence=family_confidence,
        direction_source=direction_source,
        direction_maturity=direction_maturity,
        generation_allowed=generation_allowed,
        warnings=list(dict.fromkeys(item for item in warnings if item)),
        errors=list(dict.fromkeys(item for item in errors if item)),
        passed=not errors,
    )


def run_synthetic_staging_suite(
    *,
    provider: Optional[DocumentProvider] = None,
    selected_services: Optional[Iterable[str]] = None,
    require_live_guard: bool = False,
    root: Optional[Path] = None,
) -> SyntheticStagingReport:
    selected_provider = provider or OpenAIResponsesDocumentProvider()
    live_provider = isinstance(selected_provider, OpenAIResponsesDocumentProvider)
    if require_live_guard or (provider is None and live_provider):
        assert_live_synthetic_guard()

    scenarios = staging_scenarios(selected_services)
    if not scenarios:
        raise HTTPException(
            status_code=409,
            detail="La selección no contiene escenarios sintéticos registrados.",
        )

    results = [
        run_synthetic_scenario(
            scenario,
            provider=selected_provider,
            root=root,
        )
        for scenario in scenarios
    ]
    return SyntheticStagingReport(
        live_provider=live_provider,
        provider_version=str(selected_provider.version),
        model=str(selected_provider.model),
        scenarios=results,
        passed=all(item.passed for item in results),
    )
