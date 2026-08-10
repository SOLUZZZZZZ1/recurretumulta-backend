"""Especialista RTM para la familia ``debt.factura_impagada``.

Construye una Previa Jurídica conservadora para una factura vencida e impagada.
No transforma automáticamente la familia en un procedimiento monitorio, no
calcula intereses ni prescripción sin hechos suficientes y no aplica la Ley
3/2004 cuando no consta que la operación esté dentro de su ámbito.
"""

from __future__ import annotations

from typing import Any, Optional

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import (
    Deadline,
    LegalPreview,
    MissingItem,
    MissingItemSeverity,
    PreviewStatus,
)
from rtm_core.cross_service_specialist_support import (
    CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION,
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


DEBT_UNPAID_INVOICE_SPECIALIST_VERSION = (
    "rtm_debt_unpaid_invoice_specialist_v1_0"
)

_CIVIL_BASIS = [
    "Código Civil, artículos 1091, 1100, 1101 y 1108.",
]
_MONITORIO_BASIS = [
    "Ley 1/2000, de Enjuiciamiento Civil, artículos 812 a 818.",
]
_COMMERCIAL_LATE_PAYMENT_BASIS = [
    (
        "Ley 3/2004, de 29 de diciembre, artículos 3 a 8, solo si la "
        "operación está incluida en su ámbito de aplicación."
    ),
]
_LIMITATION_BASIS = [
    (
        "Código Civil, artículo 1964.2, sin perjuicio del régimen especial "
        "que resulte aplicable a la relación concreta."
    ),
]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _amount(
    record: ValidatedFactsRecord,
) -> tuple[Any, Optional[str]]:
    return validated_value(
        record,
        "saldo_pendiente_eur",
        "importe_deuda_eur",
        "importe_reclamado_eur",
    )


def _required_missing(
    record: ValidatedFactsRecord,
) -> list[MissingItem]:
    groups = (
        (
            "invoice_fact_missing",
            "Falta validar el hecho concreto que origina la factura impagada.",
            ("descripcion_hecho",),
        ),
        (
            "invoice_number_missing",
            "Falta validar el número o referencia de la factura.",
            ("factura_numero",),
        ),
        (
            "invoice_amount_missing",
            "Falta validar el saldo exacto pendiente de pago.",
            (
                "saldo_pendiente_eur",
                "importe_deuda_eur",
                "importe_reclamado_eur",
            ),
        ),
        (
            "invoice_due_date_missing",
            "Falta validar la fecha de vencimiento o exigibilidad.",
            ("fecha_vencimiento",),
        ),
        (
            "invoice_creditor_missing",
            "Falta validar la identidad del acreedor documental.",
            ("acreedor", "emisor_documento"),
        ),
        (
            "invoice_debtor_missing",
            "Falta validar la identidad del deudor documental.",
            ("deudor", "destinatario_documento"),
        ),
    )
    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    contract, _ = validated_value(record, "contrato_ref")
    concept, _ = validated_value(record, "concepto_deuda")
    if not _present(contract) and not _present(concept):
        result.append(
            missing_item(
                "invoice_contract_or_concept_review",
                (
                    "Debe identificarse el contrato, pedido, encargo o concepto "
                    "preciso del que nace la factura."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    result.append(
        missing_item(
            "invoice_performance_evidence_review",
            (
                "OPS debe comprobar contrato, pedido, albarán, parte de trabajo, "
                "aceptación o cualquier prueba de entrega o prestación."
            ),
            MissingItemSeverity.HUMAN_REVIEW,
        )
    )

    prior_date, _ = validated_value(record, "requerimiento_previo_fecha")
    prior_channel, _ = validated_value(record, "requerimiento_previo_medio")
    if not _present(prior_date) or not _present(prior_channel):
        result.append(
            missing_item(
                "invoice_prior_demand_review",
                (
                    "Debe comprobarse si hubo requerimiento previo, su fecha, "
                    "contenido, recepción y canal fehaciente."
                ),
                MissingItemSeverity.RECOMMENDED,
            )
        )

    result.append(
        missing_item(
            "invoice_commercial_scope_review",
            (
                "Antes de aplicar intereses o costes de la Ley 3/2004 debe "
                "confirmarse que la operación es entre empresas o entre empresa "
                "y Administración y que no interviene un consumidor."
            ),
            MissingItemSeverity.HUMAN_REVIEW,
        )
    )

    paid, _ = validated_value(record, "deuda_pagada")
    if paid is True:
        result.append(
            missing_item(
                "invoice_paid_conflict",
                (
                    "Consta la deuda como pagada; debe aclararse el saldo y la "
                    "procedencia de cualquier reclamación antes de continuar."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    disputed, _ = validated_value(record, "deuda_discutida")
    if disputed is True:
        result.append(
            missing_item(
                "invoice_disputed_review",
                (
                    "La deuda consta discutida; deben incorporarse la objeción "
                    "del deudor y la respuesta documental del acreedor."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return result


def _deadlines(
    record: ValidatedFactsRecord,
) -> list[Deadline]:
    due, due_key = validated_value(record, "fecha_vencimiento")
    deadlines: list[Deadline] = []
    if _present(due) and due_key:
        from datetime import datetime, time, timezone

        try:
            parsed = datetime.fromisoformat(str(due))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = None
        if parsed is not None:
            deadlines.append(
                Deadline(
                    label="Fecha de vencimiento de la obligación",
                    due_at=parsed,
                    calculation_status="confirmed",
                    source_fact_keys=[due_key],
                    notes=[
                        "Es la fecha documental de vencimiento, no un plazo procesal."
                    ],
                )
            )
        else:
            deadlines.append(
                Deadline(
                    label="Fecha de vencimiento de la obligación",
                    due_at=None,
                    calculation_status="unresolved",
                    source_fact_keys=[due_key],
                    notes=[
                        "El valor existe, pero no se ha convertido con seguridad a fecha."
                    ],
                )
            )

    deadlines.append(
        Deadline(
            label="Prescripción de la acción de reclamación",
            due_at=None,
            calculation_status="unresolved",
            notes=[
                (
                    "Debe fijarse la naturaleza jurídica de la relación, el día "
                    "inicial del cómputo y las posibles interrupciones antes de "
                    "calcular una fecha."
                ),
                *_LIMITATION_BASIS,
            ],
        )
    )
    return deadlines


def build_debt_unpaid_invoice_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="debt",
        family="factura_impagada",
        specialist="debt.unpaid_invoice",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    invoice, invoice_key = validated_value(facts_record, "factura_numero")
    amount, amount_key = _amount(facts_record)
    due, due_key = validated_value(facts_record, "fecha_vencimiento")
    invoice_date, invoice_date_key = validated_value(
        facts_record,
        "fecha_factura",
        "fecha_documento",
    )
    creditor, creditor_key = validated_value(
        facts_record,
        "acreedor",
        "emisor_documento",
    )
    debtor, debtor_key = validated_value(
        facts_record,
        "deudor",
        "destinatario_documento",
    )
    concept, concept_key = validated_value(facts_record, "concepto_deuda")
    contract, contract_key = validated_value(facts_record, "contrato_ref")
    prior_date, prior_date_key = validated_value(
        facts_record,
        "requerimiento_previo_fecha",
    )
    prior_channel, prior_channel_key = validated_value(
        facts_record,
        "requerimiento_previo_medio",
    )
    documented_response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
        "respuesta_proveedor",
    )

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("acreedor", "Acreedor", ""),
            ("deudor", "Deudor", ""),
            ("factura_numero", "Factura", ""),
            ("fecha_factura", "Fecha de factura", ""),
            ("fecha_vencimiento", "Vencimiento", ""),
            ("importe_deuda_eur", "Importe de deuda", " €"),
            ("saldo_pendiente_eur", "Saldo pendiente", " €"),
            ("concepto_deuda", "Concepto", ""),
            ("contrato_ref", "Contrato o referencia", ""),
            ("requerimiento_previo_fecha", "Requerimiento previo", ""),
            ("requerimiento_previo_medio", "Canal del requerimiento", ""),
            ("deuda_discutida", "Deuda discutida", ""),
            ("deuda_pagada", "Deuda pagada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {fact}.")
        if fact_key:
            summary_keys.insert(0, fact_key)

    argument_keys = family_evidence_keys(family_record)
    arguments = []

    contract_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            invoice_key,
            creditor_key,
            debtor_key,
            concept_key,
            contract_key,
            invoice_date_key,
        ),
    )
    if contract_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="invoice_contractual_source_and_performance",
                title="Origen contractual y acreditación de la prestación",
                body=(
                    "La reclamación debe identificar la relación jurídica de la "
                    "que nace la factura y enlazarla con la prestación realmente "
                    "ejecutada. La factura es un documento relevante, pero debe "
                    "contrastarse con contrato, pedido, encargo, albarán, parte de "
                    "trabajo, aceptación o comunicaciones que permitan acreditar "
                    "qué se entregó o prestó, por quién y frente a quién."
                ),
                source_fact_keys=contract_sources,
                priority="primary",
                legal_basis=list(_CIVIL_BASIS),
            )
        )

    debt_sources = validated_source_keys(
        facts_record,
        (fact_key, invoice_key, amount_key, due_key, invoice_date_key),
    )
    if debt_sources:
        amount_text = f"{amount} €" if _present(amount) else "pendiente de validar"
        due_text = str(due) if _present(due) else "pendiente de validar"
        arguments.append(
            legal_argument(
                facts_record,
                code="invoice_liquid_due_and_outstanding_balance",
                title="Cuantía, vencimiento y saldo pendiente",
                body=(
                    "Antes de requerir el pago debe fijarse una cuantía única, "
                    "desglosada y comprobable, evitando acumular conceptos no "
                    "documentados. El expediente refleja un saldo de "
                    f"{amount_text} y un vencimiento {due_text}; OPS debe comprobar "
                    "pagos parciales, abonos, compensaciones, devoluciones y la "
                    "correspondencia entre el total reclamado y la factura."
                ),
                source_fact_keys=debt_sources,
                priority="primary",
                legal_basis=list(_CIVIL_BASIS),
            )
        )

    default_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            amount_key,
            due_key,
            prior_date_key,
            prior_channel_key,
            response_key,
        ),
    )
    if default_sources:
        prior_text = (
            f"Consta un requerimiento de fecha {prior_date} por {prior_channel}."
            if _present(prior_date) and _present(prior_channel)
            else (
                "No consta todavía un requerimiento previo completo y fehaciente "
                "en los hechos validados."
            )
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="invoice_default_interest_and_collection_costs",
                title="Mora, intereses y costes de cobro",
                body=(
                    "La mora y sus efectos deben fundarse en el contrato, el "
                    "vencimiento, la exigibilidad y, cuando corresponda, el "
                    "requerimiento judicial o extrajudicial. No se incorporará un "
                    "tipo de interés, una indemnización o una cantidad fija sin "
                    "comprobar previamente su base jurídica. "
                    f"{prior_text} La Ley 3/2004 solo podrá aplicarse si OPS confirma "
                    "que la operación está dentro de su ámbito y que no interviene "
                    "un consumidor."
                ),
                source_fact_keys=default_sources,
                priority="secondary",
                legal_basis=[
                    *_CIVIL_BASIS,
                    *_COMMERCIAL_LATE_PAYMENT_BASIS,
                ],
            )
        )

    procedural_sources = validated_source_keys(
        facts_record,
        (fact_key, invoice_key, amount_key, due_key, contract_key),
    )
    if procedural_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="invoice_extrajudicial_then_procedural_route",
                title="Requerimiento documental y eventual vía judicial",
                body=(
                    "La actuación inicial de esta familia es un requerimiento "
                    "documentado de pago, con principal, origen, vencimiento y "
                    "documentos de respaldo. Una petición inicial de monitorio "
                    "solo debe valorarse después, cuando la deuda pueda sostenerse "
                    "como dineraria, líquida, determinada, vencida, exigible y "
                    "documentalmente acreditada. Este especialista no convierte "
                    "automáticamente una factura impagada en un escrito judicial."
                ),
                source_fact_keys=procedural_sources,
                priority="secondary",
                legal_basis=list(_MONITORIO_BASIS),
            )
        )

    if not arguments:
        # La familia resuelta debe conservar al menos una señal factual; este
        # bloqueo evita crear una previa vacía si la persistencia fuese incoherente.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa.",
        )

    source_keys = validated_source_keys(
        facts_record,
        [
            *argument_keys,
            *summary_keys,
            *(key for argument in arguments for key in argument.source_fact_keys),
        ],
    )

    missing = dedupe_missing(
        [
            *_required_missing(facts_record),
            *_review_missing(facts_record),
            *fact_review_items(facts_record, prefix="invoice"),
        ]
    )

    destination = (
        str(debtor).strip()
        if _present(debtor)
        else "DEUDOR PENDIENTE DE VALIDAR"
    )
    subject_parts = ["REQUERIMIENTO DE PAGO"]
    if _present(invoice):
        subject_parts.append(f"factura {invoice}")
    if _present(amount):
        subject_parts.append(f"saldo {amount} €")
    subject = " — ".join(subject_parts)

    risks = [
        (
            "Una factura aislada puede ser insuficiente si no se acredita la "
            "relación contractual y la prestación."
        ),
        (
            "No se ha calculado prescripción, interés ni coste de cobro: requieren "
            "calificación jurídica y revisión OPS."
        ),
        (
            "La eventual vía monitoria pertenece a una decisión posterior y no "
            "queda habilitada por esta Previa."
        ),
    ]
    if documented_response not in (None, "", [], {}):
        risks.append(
            "Existe respuesta documentada del deudor o contraparte y debe valorarse íntegramente."
        )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="factura_impagada",
        specialist="debt.unpaid_invoice",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se documenta una factura pendiente: {fact}."
            if _present(fact)
            else "La factura impagada requiere completar sus hechos documentales."
        ),
        client_goal=(
            "Obtener el pago del principal realmente debido y documentado, dejando "
            "constancia fehaciente del requerimiento y preservando la vía posterior "
            "que corresponda."
        ),
        primary_strategy=(
            "Cerrar primero la prueba del contrato o encargo, la prestación, la "
            "factura, el vencimiento y el saldo; después emitir un requerimiento "
            "preciso que no incluya intereses o costes sin base confirmada."
        ),
        secondary_strategies=[
            "Negociar un reconocimiento de deuda o calendario de pago documentado.",
            (
                "Valorar procedimiento monitorio únicamente cuando se cumplan y "
                "acrediten sus requisitos legales."
            ),
            (
                "Si la deuda está discutida, separar los conceptos controvertidos "
                "y revisar la prueba de cumplimiento."
            ),
        ],
        requested_outcomes=[
            "Pago del principal pendiente que resulte documentalmente acreditado.",
            (
                "Intereses de mora que correspondan una vez validado el régimen "
                "contractual o legal aplicable."
            ),
            (
                "Si se confirma el ámbito de la Ley 3/2004, cantidad fija y costes "
                "de cobro acreditados en los términos legalmente aplicables."
            ),
            "Reserva de la vía judicial procedente en caso de impago.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=risks,
        destination=destination,
        document_type="REQUERIMIENTO EXTRAJUDICIAL DE PAGO",
        subject=subject,
        legal_arguments=arguments,
        additional_requests=[
            "Contrato, presupuesto, pedido, encargo o condiciones aceptadas.",
            "Albarán, parte de trabajo, entrega, aceptación o prueba de prestación.",
            "Factura completa y prueba de su remisión o recepción.",
            "Extracto de pagos, abonos, devoluciones y saldo actualizado.",
            "Comunicaciones y requerimientos previos con prueba de recepción.",
        ],
        created_by_component=(
            f"debt.unpaid_invoice:{DEBT_UNPAID_INVOICE_SPECIALIST_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
