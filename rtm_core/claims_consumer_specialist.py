"""Especialista RTM para reclamaciones residuales de consumo general.

Construye una Previa Jurídica trazable desde hechos congelados. No usa OCR
crudo, no inventa una relación de consumo, no absorbe sectores especializados y
no convierte automáticamente un defecto, una cláusula o un retraso en derecho a
una indemnización concreta.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from types import UnionType
from typing import Any, Optional, Union, get_args, get_origin

from fastapi import HTTPException

from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
from rtm_core.claims_consumer_regime import (
    CLAIMS_CONSUMER_REGIME_VERSION,
    ClaimsConsumerRegimeDecision,
    resolve_claims_consumer_regime,
)
from rtm_core.contracts import (
    LEGAL_PREVIEW_VERSION,
    LegalPreview,
    MissingItemSeverity,
    PreviewStatus,
)
from rtm_core.cross_service_specialist_support import (
    dedupe_missing,
    document_uses,
    ensure_specialist_authority,
    fact_review_items,
    family_evidence_keys,
    legal_argument,
    missing_item,
    summary_rows,
    validated_source_keys,
    validated_value,
)


CLAIMS_CONSUMER_SPECIALIST_VERSION = "rtm_claims_consumer_specialist_v1_0"


def _value(record: ValidatedFactsRecord, *keys: str) -> tuple[Any, Optional[str]]:
    return validated_value(record, *keys)


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value if item not in (None, ""))
    return str(value or "").strip()


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = _text(value).lower()
    if normalized in {"sí", "si", "true", "1", "consta", "acreditado"}:
        return True
    if normalized in {"no", "false", "0", "no consta", "no acreditado"}:
        return False
    return None


def _money(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = _text(value).replace("€", "").replace("EUR", "").replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _first_key(*keys: Optional[str]) -> Optional[str]:
    return next((key for key in keys if key), None)


def _draft_status() -> Any:
    for name in ("DRAFT", "draft", "IN_REVIEW", "in_review"):
        if hasattr(PreviewStatus, name):
            return getattr(PreviewStatus, name)
    try:
        return list(PreviewStatus)[0]
    except Exception:
        return "draft"


def _annotation_default(annotation: Any, field_name: str) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        if type(None) in args:
            return None
        if args:
            return _annotation_default(args[0], field_name)
    if origin in (list, set, tuple):
        return [] if origin is list else (() if origin is tuple else set())
    if origin is dict:
        return {}
    if annotation is str:
        return "rtm"
    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is datetime:
        return datetime.now(timezone.utc)
    if annotation is date:
        return date.today()
    try:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return list(annotation)[0]
    except Exception:
        pass
    if field_name.endswith("_at"):
        return datetime.now(timezone.utc)
    if field_name.endswith("_keys") or field_name.endswith("_items"):
        return []
    return "rtm"


def _adapt_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        non_null = [item for item in args if item is not type(None)]
        if value is None:
            return None
        return _adapt_value(non_null[0], value) if non_null else value
    if origin is list:
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return [value]
    if origin is tuple:
        if isinstance(value, tuple):
            return value
        if isinstance(value, (list, set)):
            return tuple(value)
        return (value,)
    if annotation is str and isinstance(value, (list, tuple, set)):
        return "\n".join(str(item) for item in value)
    try:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            if isinstance(value, annotation):
                return value
            for member in annotation:
                if str(member.value) == str(value):
                    return member
            return list(annotation)[0]
    except Exception:
        pass
    return value


def _build_preview_model(
    *,
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
    destination: str,
    document_type: str,
    subject: str,
    factual_summary: list[str],
    legal_arguments: list[Any],
    requested_actions: list[str],
    alternatives: list[str],
    missing_items: list[Any],
    source_fact_keys: list[str],
) -> LegalPreview:
    """Adapta el payload al contrato autoritativo sin depender de campos legacy."""

    created = datetime.now(timezone.utc)
    preferred: dict[str, Any] = {
        "version": LEGAL_PREVIEW_VERSION,
        "case_id": facts_record.case_id,
        "service": "claims",
        "family": "consumo",
        "specialist": "claims.consumer",
        "facts_version": facts_record.facts.version,
        "validated_facts_id": facts_record.id,
        "family_resolution_version": family_record.resolution.version,
        "family_resolution_id": family_record.id,
        "status": _draft_status(),
        "destination": destination,
        "document_type": document_type,
        "subject": subject,
        "factual_summary": factual_summary,
        "summary": factual_summary,
        "legal_arguments": legal_arguments,
        "requested_action": requested_actions,
        "requested_actions": requested_actions,
        "alternatives": alternatives,
        "deadlines": [],
        "missing_items": missing_items,
        "documents_used": document_uses(facts_record),
        "document_uses": document_uses(facts_record),
        "source_fact_keys": source_fact_keys,
        "created_by_component": (
            f"{CLAIMS_CONSUMER_SPECIALIST_VERSION};"
            f"{CLAIMS_CONSUMER_REGIME_VERSION}"
        ),
        "created_at": created,
        "updated_at": created,
        "ops_approved": False,
        "approved": False,
        "frozen": False,
        "locked": False,
    }

    payload: dict[str, Any] = {}
    for name, field in LegalPreview.model_fields.items():
        if name in preferred:
            payload[name] = _adapt_value(field.annotation, preferred[name])
        elif field.is_required():
            payload[name] = _annotation_default(field.annotation, name)

    try:
        return LegalPreview.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No pudo construirse la Previa Jurídica de consumo.",
                "error": str(exc),
            },
        ) from exc


def build_claims_consumer_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="consumo",
        specialist="claims.consumer",
    )

    issue, issue_key = _value(
        facts_record,
        "descripcion_hecho",
        "incidencia_consumo_tipo",
    )
    incident, incident_key = _value(facts_record, "incidencia_consumo_tipo")
    consumer, consumer_key = _value(facts_record, "consumidor_es_consumidor")
    consumer_country, consumer_country_key = _value(
        facts_record, "pais_consumidor_general"
    )
    trader, trader_key = _value(
        facts_record,
        "empresario_consumo",
        "proveedor",
        "emisor_documento",
    )
    trader_country, trader_country_key = _value(
        facts_record, "pais_empresario_consumo"
    )
    supplier_is_trader, supplier_is_trader_key = _value(
        facts_record, "empresario_consumo_es_empresario"
    )
    establishment, establishment_key = _value(
        facts_record, "establecimiento_consumo"
    )
    contract_ref, contract_ref_key = _value(
        facts_record, "contrato_consumo_ref", "contrato_ref"
    )
    invoice_ref, invoice_ref_key = _value(
        facts_record, "factura_consumo_ref", "factura_numero"
    )
    contract_date, contract_date_key = _value(
        facts_record, "fecha_contrato_consumo"
    )
    purchase_date, purchase_date_key = _value(
        facts_record, "fecha_compra_consumo", "fecha_documento"
    )
    delivery_date, delivery_date_key = _value(
        facts_record, "fecha_entrega_consumo"
    )
    incident_date, incident_date_key = _value(
        facts_record, "fecha_incidencia_consumo", "fecha_incidencia"
    )
    complaint_date, complaint_date_key = _value(
        facts_record,
        "fecha_reclamacion_previa_consumo",
        "reclamacion_previa_fecha",
    )
    channel, channel_key = _value(
        facts_record, "modalidad_contratacion_consumo"
    )
    online, online_key = _value(facts_record, "compra_online_consumo")
    object_type, object_type_key = _value(facts_record, "objeto_consumo_tipo")
    product, product_key = _value(facts_record, "producto_consumo_descripcion")
    service, service_key = _value(facts_record, "servicio_consumo_descripcion")
    terms, terms_key = _value(facts_record, "condiciones_contrato_consumo")
    advertising, advertising_key = _value(
        facts_record, "publicidad_oferta_consumo"
    )
    total_price, total_price_key = _value(
        facts_record, "precio_total_consumo_eur"
    )
    paid, paid_key = _value(
        facts_record,
        "importe_pagado_consumo_eur",
        "importe_pagado_eur",
    )
    claimed, claimed_key = _value(
        facts_record,
        "importe_reclamado_consumo_eur",
        "importe_reclamado_eur",
    )
    advertised_price, advertised_price_key = _value(
        facts_record, "precio_anunciado_consumo_eur"
    )
    charged_price, charged_price_key = _value(
        facts_record, "precio_cobrado_consumo_eur"
    )
    surcharge, surcharge_key = _value(
        facts_record, "cargo_adicional_no_informado_consumo_eur"
    )
    nonconformity, nonconformity_key = _value(
        facts_record, "falta_conformidad_consumo"
    )
    nonconformity_description, nonconformity_description_key = _value(
        facts_record, "falta_conformidad_descripcion_consumo"
    )
    manifestation_date, manifestation_date_key = _value(
        facts_record, "fecha_manifestacion_falta_conformidad_consumo"
    )
    service_not_performed, service_not_performed_key = _value(
        facts_record, "servicio_no_prestado_consumo"
    )
    service_incomplete, service_incomplete_key = _value(
        facts_record, "servicio_incompleto_consumo"
    )
    service_defective, service_defective_key = _value(
        facts_record, "servicio_defectuoso_consumo"
    )
    breach_description, breach_description_key = _value(
        facts_record, "incumplimiento_consumo_descripcion"
    )
    disputed_term, disputed_term_key = _value(
        facts_record, "clausula_discutida_consumo"
    )
    commercial_guarantee, commercial_guarantee_key = _value(
        facts_record, "garantia_comercial_consumo"
    )
    repair, repair_key = _value(facts_record, "reparacion_solicitada_consumo")
    replacement, replacement_key = _value(
        facts_record, "sustitucion_solicitada_consumo"
    )
    price_reduction, price_reduction_key = _value(
        facts_record, "reduccion_precio_solicitada_consumo"
    )
    termination, termination_key = _value(
        facts_record, "resolucion_contrato_solicitada_consumo"
    )
    refund_requested, refund_requested_key = _value(
        facts_record, "reembolso_solicitado_consumo_eur"
    )
    refund_received, refund_received_key = _value(
        facts_record, "reembolso_recibido_consumo_eur"
    )
    recovered_elsewhere, recovered_elsewhere_key = _value(
        facts_record, "importe_recuperado_tercero_consumo_eur"
    )
    complaint_ref, complaint_ref_key = _value(
        facts_record, "referencia_reclamacion_consumo"
    )
    complaint_channel, complaint_channel_key = _value(
        facts_record, "canal_reclamacion_consumo", "canal_reclamacion"
    )
    trader_response, trader_response_key = _value(
        facts_record,
        "respuesta_empresario_consumo",
        "respuesta_documentada",
    )
    requested_solution, requested_solution_key = _value(
        facts_record,
        "solucion_solicitada_consumo",
        "solucion_solicitada",
    )
    unsafe_product, unsafe_product_key = _value(
        facts_record, "producto_inseguro_consumo"
    )
    regulated_hint, regulated_hint_key = _value(
        facts_record, "servicio_regulado_indicio"
    )

    regime = resolve_claims_consumer_regime(
        contract_date=contract_date,
        purchase_date=purchase_date,
        delivery_date=delivery_date,
        incident_date=incident_date,
        complaint_date=complaint_date,
        trader_country=trader_country,
        consumer_country=consumer_country,
        customer_is_consumer=consumer,
        supplier_is_trader=supplier_is_trader,
        contract_channel=channel,
        online_purchase=online,
        object_type=object_type,
        product_description=product,
        service_description=service,
        incident_type=incident,
        issue_text=(issue, nonconformity_description, breach_description),
        regulated_service_hint=regulated_hint,
        nonconformity=nonconformity,
        nonconformity_description=nonconformity_description,
        service_not_performed=service_not_performed,
        service_incomplete=service_incomplete,
        service_defective=service_defective,
        advertised_price=advertised_price,
        charged_price=charged_price,
        undisclosed_surcharge=surcharge,
        disputed_term=disputed_term,
        commercial_guarantee=commercial_guarantee,
        refund_requested=refund_requested,
        termination_requested=termination,
        unsafe_product=unsafe_product,
    )

    missing = fact_review_items(facts_record, prefix="consumer")
    if not issue_key:
        missing.append(
            missing_item(
                "consumer_issue_missing",
                "Falta una descripción documental concreta de la incidencia de consumo.",
            )
        )
    if not trader_key and not establishment_key:
        missing.append(
            missing_item(
                "consumer_trader_identity_missing",
                "Debe identificarse el empresario o establecimiento destinatario.",
            )
        )
    if not _first_key(contract_date_key, purchase_date_key):
        missing.append(
            missing_item(
                "consumer_contract_date_missing",
                "Falta la fecha documental de compra o contratación.",
            )
        )
    if consumer_key is None:
        missing.append(
            missing_item(
                "consumer_status_review",
                "Debe confirmarse que el cliente actuó fuera de una actividad empresarial o profesional.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif _bool(consumer) is not True:
        missing.append(
            missing_item(
                "consumer_status_not_confirmed",
                "La condición de consumidor no está confirmada para esta contratación.",
            )
        )
    if supplier_is_trader_key is None or _bool(supplier_is_trader) is not True:
        missing.append(
            missing_item(
                "consumer_trader_status_missing",
                "Debe acreditarse que la contraparte actuó como empresario o profesional.",
            )
        )
    if object_type_key is None and product_key is None and service_key is None:
        missing.append(
            missing_item(
                "consumer_object_missing",
                "Debe identificarse el bien o servicio contratado.",
            )
        )
    if requested_solution_key is None and not any(
        _bool(value) is True for value in (repair, replacement, price_reduction, termination)
    ) and _money(refund_requested) is None:
        missing.append(
            missing_item(
                "consumer_requested_solution_missing",
                "Debe concretarse la solución solicitada al empresario.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if complaint_date_key is None and complaint_ref_key is None:
        missing.append(
            missing_item(
                "consumer_prior_claim_required",
                "Conviene presentar o documentar la reclamación previa al empresario antes del escalado de consumo.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    paid_amount = _money(paid)
    total_amount = _money(total_price)
    claimed_amount = _money(claimed)
    refund_amount = _money(refund_requested)
    refunded_amount = _money(refund_received) or 0.0
    recovered_amount = _money(recovered_elsewhere) or 0.0
    surcharge_amount = _money(surcharge) or 0.0

    if paid_amount is not None and total_amount is not None:
        permitted = total_amount + max(0.0, surcharge_amount)
        if paid_amount > permitted + 0.01:
            missing.append(
                missing_item(
                    "consumer_payment_exceeds_documented_price",
                    "El importe pagado supera el precio y cargos documentados; deben conciliarse factura, recibo y conceptos.",
                )
            )
    if refund_amount is not None and paid_amount is not None and refund_amount > paid_amount + 0.01:
        missing.append(
            missing_item(
                "consumer_refund_exceeds_payment",
                "El reembolso solicitado supera el importe pagado documentado.",
            )
        )
    if claimed_amount is not None and paid_amount is not None and claimed_amount > paid_amount + 0.01:
        missing.append(
            missing_item(
                "consumer_claim_amount_requires_damage_breakdown",
                "La cuantía reclamada supera el precio pagado y exige separar devolución, gastos y daños acreditados.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if recovered_amount > 0 or refunded_amount > 0:
        missing.append(
            missing_item(
                "consumer_recovery_coordination_review",
                "Deben descontarse y coordinarse las cantidades ya recuperadas para evitar doble recuperación.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.status != "current":
        missing.append(
            missing_item(
                "consumer_regime_review",
                regime.blocking_reason or "El régimen jurídico requiere revisión OPS.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.incident_type in {"non_conformity", "guarantee"}:
        if nonconformity_description_key is None and commercial_guarantee_key is None:
            missing.append(
                missing_item(
                    "consumer_nonconformity_description_missing",
                    "Debe describirse documentalmente la falta de conformidad o la garantía invocada.",
                )
            )
        if manifestation_date_key is None:
            missing.append(
                missing_item(
                    "consumer_nonconformity_date_review",
                    "Debe documentarse cuándo se manifestó el defecto para revisar garantías y plazos.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    if regime.incident_type == "price_or_advertising" and not any(
        key for key in (advertising_key, advertised_price_key, charged_price_key, surcharge_key)
    ):
        missing.append(
            missing_item(
                "consumer_price_offer_evidence_missing",
                "Falta la oferta, publicidad, etiqueta o factura que permita comparar el precio comunicado y cobrado.",
            )
        )
    if regime.incident_type == "unfair_term" and disputed_term_key is None:
        missing.append(
            missing_item(
                "consumer_disputed_term_missing",
                "Debe aportarse el texto completo de la cláusula discutida y el contrato aplicable.",
            )
        )
    if regime.incident_type in {
        "service_not_performed",
        "service_incomplete_or_defective",
    } and breach_description_key is None and service_key is None:
        missing.append(
            missing_item(
                "consumer_service_breach_missing",
                "Debe concretarse el servicio pactado y el incumplimiento atribuido.",
            )
        )

    basis = list(regime.legal_basis) if regime.status == "current" else []
    base_keys = validated_source_keys(
        facts_record,
        [
            issue_key,
            incident_key,
            *family_evidence_keys(family_record),
            trader_key,
            establishment_key,
            product_key,
            service_key,
        ],
    )
    if not base_keys:
        raise HTTPException(
            status_code=409,
            detail="La previa de consumo no conserva hechos validados de origen.",
        )

    arguments = []
    arguments.append(
        legal_argument(
            facts_record,
            code="consumer_relationship_and_scope",
            title="Relación de consumo y ámbito residual",
            body=(
                "La reclamación debe partir de la identidad de las partes, la fecha, "
                "el bien o servicio y el canal de contratación. El encuadre general "
                "solo es admisible mientras no aparezca una materia sectorial que "
                "corresponda a otro especialista."
            ),
            source_fact_keys=[
                issue_key,
                consumer_key,
                consumer_country_key,
                trader_key,
                trader_country_key,
                supplier_is_trader_key,
                object_type_key,
                product_key,
                service_key,
            ],
            priority="primary",
            legal_basis=basis[:2],
        )
    )

    contract_sources = [
        contract_ref_key,
        invoice_ref_key,
        contract_date_key,
        purchase_date_key,
        terms_key,
        advertising_key,
        total_price_key,
        paid_key,
        issue_key,
    ]
    arguments.append(
        legal_argument(
            facts_record,
            code="consumer_contract_and_offer",
            title="Contrato, oferta, precio e información",
            body=(
                "Deben compararse contrato, presupuesto, publicidad, etiqueta, "
                "ticket o factura para determinar qué se ofreció, qué se cobró y "
                "qué condiciones quedaron incorporadas, sin reconstruir extremos "
                "que no estén documentados."
            ),
            source_fact_keys=contract_sources,
            priority="primary",
            legal_basis=basis[:3],
        )
    )

    incident_sources = [
        issue_key,
        incident_key,
        nonconformity_key,
        nonconformity_description_key,
        manifestation_date_key,
        service_not_performed_key,
        service_incomplete_key,
        service_defective_key,
        breach_description_key,
        advertising_key,
        advertised_price_key,
        charged_price_key,
        surcharge_key,
        disputed_term_key,
        commercial_guarantee_key,
    ]
    incident_body = {
        "non_conformity": (
            "La falta de conformidad debe vincularse al bien, la entrega, la fecha "
            "de manifestación y las medidas ya intentadas. La existencia de una "
            "avería no permite elegir automáticamente entre reparación, sustitución, "
            "reducción del precio o resolución."
        ),
        "guarantee": (
            "La garantía legal y cualquier garantía comercial deben separarse. "
            "Debe comprobarse su contenido, duración, garante, exclusiones y relación "
            "con la falta de conformidad alegada."
        ),
        "service_not_performed": (
            "Debe acreditarse el servicio contratado, su precio y la ausencia de "
            "prestación, junto con las comunicaciones y cualquier devolución parcial."
        ),
        "service_incomplete_or_defective": (
            "La calidad o integridad del servicio debe compararse con el alcance "
            "pactado y la ejecución acreditada, sin presumir negligencia, causalidad "
            "o daño por la sola disconformidad del cliente."
        ),
        "price_or_advertising": (
            "La reclamación debe confrontar la información y precio anunciados con "
            "el importe efectivamente cobrado y los cargos informados antes de contratar."
        ),
        "unfair_term": (
            "La cláusula discutida debe analizarse íntegramente y en su contexto. "
            "Su denominación o severidad económica no bastan por sí solas para "
            "declararla abusiva."
        ),
        "refund_or_cancellation": (
            "La cancelación o reembolso exige revisar quién resolvió el contrato, "
            "la causa, las condiciones aplicables, la parte ejecutada y las cantidades "
            "ya devueltas."
        ),
    }.get(
        regime.incident_type,
        "Debe precisarse el incumplimiento de consumo y contrastarlo con el contrato, la ejecución y la respuesta empresarial.",
    )
    arguments.append(
        legal_argument(
            facts_record,
            code=f"consumer_incident_{regime.incident_type}",
            title="Incidencia y cumplimiento",
            body=incident_body,
            source_fact_keys=incident_sources,
            priority="primary",
            legal_basis=basis,
        )
    )

    remedy_sources = [
        requested_solution_key,
        repair_key,
        replacement_key,
        price_reduction_key,
        termination_key,
        refund_requested_key,
        refund_received_key,
        recovered_elsewhere_key,
        claimed_key,
        paid_key,
        issue_key,
    ]
    arguments.append(
        legal_argument(
            facts_record,
            code="consumer_remedies_and_amounts",
            title="Solución solicitada y coordinación de cantidades",
            body=(
                "La petición debe separar cumplimiento, reparación, sustitución, "
                "reducción del precio, resolución, reembolso, gastos y daños. Las "
                "cantidades ya abonadas o recuperadas deben descontarse para impedir "
                "una doble recuperación."
            ),
            source_fact_keys=remedy_sources,
            priority="secondary",
            legal_basis=basis,
        )
    )

    if complaint_date_key or complaint_ref_key or trader_response_key:
        arguments.append(
            legal_argument(
                facts_record,
                code="consumer_prior_complaint_and_adr",
                title="Reclamación previa y eventual escalado de consumo",
                body=(
                    "La reclamación previa, su referencia, canal y respuesta deben "
                    "conservarse. Solo después cabe valorar hoja de reclamaciones, "
                    "arbitraje o una entidad de resolución alternativa competente y "
                    "admisible, sin prometer su aceptación."
                ),
                source_fact_keys=[
                    complaint_date_key,
                    complaint_ref_key,
                    complaint_channel_key,
                    trader_response_key,
                    issue_key,
                ],
                priority="secondary",
                legal_basis=basis,
            )
        )

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("empresario_consumo", "Empresario", ""),
            ("establecimiento_consumo", "Establecimiento", ""),
            ("contrato_consumo_ref", "Contrato", ""),
            ("factura_consumo_ref", "Factura o ticket", ""),
            ("fecha_compra_consumo", "Fecha de compra", ""),
            ("objeto_consumo_tipo", "Objeto", ""),
            ("producto_consumo_descripcion", "Producto", ""),
            ("servicio_consumo_descripcion", "Servicio", ""),
            ("precio_total_consumo_eur", "Precio total", " euros"),
            ("importe_pagado_consumo_eur", "Importe pagado", " euros"),
            ("incidencia_consumo_tipo", "Incidencia", ""),
            ("fecha_reclamacion_previa_consumo", "Reclamación previa", ""),
            ("referencia_reclamacion_consumo", "Referencia", ""),
        ),
    )
    if issue_key and _text(issue):
        summary.insert(0, f"Hecho principal: {_text(issue)}")
        summary_keys.insert(0, issue_key)

    destination = _text(trader) or _text(establishment) or "Empresario pendiente de identificar"
    reference = _text(contract_ref) or _text(invoice_ref)
    object_label = _text(product) or _text(service) or "relación de consumo"
    subject = f"Reclamación de consumo por {object_label}"
    if reference:
        subject = f"{subject} — referencia {reference}"

    requested_actions = []
    if _text(requested_solution):
        requested_actions.append(_text(requested_solution))
    if _bool(repair) is True:
        requested_actions.append("Reparación del bien sin coste cuando proceda.")
    if _bool(replacement) is True:
        requested_actions.append("Sustitución del bien cuando proceda.")
    if _bool(price_reduction) is True:
        requested_actions.append("Reducción proporcionada del precio cuando proceda.")
    if _bool(termination) is True:
        requested_actions.append("Resolución del contrato y liquidación de cantidades cuando proceda.")
    if refund_amount is not None:
        requested_actions.append(f"Reembolso solicitado: {refund_amount:.2f} euros.")
    if not requested_actions:
        requested_actions.append(
            "Que el empresario responda motivadamente y ofrezca la medida correctora que corresponda tras revisar los hechos y documentos."
        )

    alternatives = [
        "Completar contrato, ticket, factura, publicidad, fotografías, informes y comunicaciones que falten.",
        "Valorar hoja de reclamaciones, arbitraje de consumo o entidad ADR solo tras comprobar competencia y admisibilidad.",
        "Reasignar el expediente inmediatamente si aparece una materia sectorial especializada.",
    ]

    source_keys = validated_source_keys(
        facts_record,
        [
            *summary_keys,
            *family_evidence_keys(family_record),
            *[key for argument in arguments for key in argument.source_fact_keys],
            regulated_hint_key,
            online_key,
            unsafe_product_key,
            delivery_date_key,
            incident_date_key,
            complaint_channel_key,
        ],
    )

    return _build_preview_model(
        facts_record=facts_record,
        family_record=family_record,
        destination=destination,
        document_type="RECLAMACIÓN PREVIA DE CONSUMO AL EMPRESARIO",
        subject=subject,
        factual_summary=summary,
        legal_arguments=arguments,
        requested_actions=requested_actions,
        alternatives=alternatives,
        missing_items=dedupe_missing(missing),
        source_fact_keys=source_keys,
    )
