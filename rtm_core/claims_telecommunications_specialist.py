"""Especialista RTM para ``claims.telecommunications``.

Construye una Previa Jurídica conservadora para controversias de telefonía,
internet y otros servicios de comunicaciones electrónicas. No presume la
identidad del operador, no calcula plazos por calendario, no convierte una
reclamación sectorial en una acción de daños y no da por acreditada una baja,
portabilidad, contratación o factura sin hechos documentales validados.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

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


CLAIMS_TELECOMMUNICATIONS_SPECIALIST_VERSION = (
    "rtm_claims_telecommunications_specialist_v1_0"
)

_SECTOR_BASIS = [
    (
        "Ley 11/2022, de 28 de junio, General de Telecomunicaciones, "
        "artículos 64 a 71 y 78, según la materia y el tipo de usuario."
    ),
    (
        "Real Decreto 899/2009, de 22 de mayo, sobre derechos de los "
        "usuarios de comunicaciones electrónicas, en lo que continúe vigente "
        "y resulte aplicable."
    ),
    (
        "Orden ITC/1030/2007, de 12 de abril, sobre reclamaciones de "
        "usuarios finales y atención al cliente por los operadores."
    ),
]
_CONSUMER_BASIS = [
    (
        "Real Decreto Legislativo 1/2007, de 16 de noviembre, texto "
        "refundido de la Ley General para la Defensa de los Consumidores y "
        "Usuarios, cuando el reclamante tenga esa condición."
    ),
]

RouteState = Literal["operator", "office_review", "operator_period_review"]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _display(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return str(value)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw = str(value or "").strip()
    if not raw:
        return None

    for candidate in (raw, raw.replace("/", "-"), raw.replace(".", "-")):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass

    for separator in ("/", "-", "."):
        parts = raw.split(separator)
        if len(parts) != 3:
            continue
        try:
            day, month, year = (int(part) for part in parts)
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "producto_servicio",
        "respuesta_proveedor",
        "respuesta_documentada",
        "solucion_solicitada",
        "periodo_facturado",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    provider_response, _ = validated_value(
        record,
        "respuesta_proveedor",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(record, "fecha_respuesta")

    if not _present(prior_claim):
        return "operator"
    if _present(provider_response) or _present(response_date):
        return "office_review"
    return "operator_period_review"


def _required_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        (
            "telecom_fact_missing",
            "Falta validar el incumplimiento o incidencia de telecomunicaciones.",
            ("descripcion_hecho",),
        ),
        (
            "telecom_operator_missing",
            "Falta validar el operador que presta y factura el servicio.",
            ("proveedor", "emisor_documento"),
        ),
        (
            "telecom_service_missing",
            "Falta validar el servicio o paquete afectado.",
            ("producto_servicio",),
        ),
        (
            "telecom_service_reference_missing",
            "Falta validar el contrato, número de cliente o referencia del servicio.",
            ("contrato_ref", "referencia_servicio"),
        ),
        (
            "telecom_requested_solution_missing",
            "Falta validar qué solución solicita el usuario.",
            ("solucion_solicitada",),
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
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    text = _all_text(record)

    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    claim_channel, _ = validated_value(record, "canal_reclamacion")
    provider_response, _ = validated_value(
        record,
        "respuesta_proveedor",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(record, "fecha_respuesta")
    claim_reference, _ = validated_value(
        record,
        "expediente_ref",
        "referencia_documento",
    )

    if route == "operator":
        result.append(
            missing_item(
                "telecom_prior_operator_claim_required",
                (
                    "La reclamación debe presentarse primero al operador y conservar "
                    "fecha, contenido, canal y número de referencia antes de acudir "
                    "a la vía administrativa sectorial."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "operator_period_review":
        result.append(
            missing_item(
                "telecom_operator_response_period_review",
                (
                    "Consta reclamación previa sin respuesta validada. Debe "
                    "comprobarse su recepción y la finalización del plazo de un mes "
                    "antes de acudir a la Oficina."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "telecom_office_eligibility_review",
                (
                    "Antes de acudir a la Oficina debe comprobarse que el reclamante "
                    "es persona física —incluido autónomo— o microempresa y que la "
                    "controversia pertenece a los derechos sectoriales admitidos."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if _present(prior_claim) and not _present(claim_channel):
        result.append(
            missing_item(
                "telecom_claim_channel_missing",
                (
                    "Falta validar el canal por el que se presentó la reclamación "
                    "previa al operador."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(claim_reference):
        result.append(
            missing_item(
                "telecom_claim_reference_missing",
                (
                    "Debe obtenerse el número de referencia o justificante que "
                    "acredite la reclamación previa y su contenido."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(provider_response) and not _present(prior_claim):
        result.append(
            missing_item(
                "telecom_response_without_prior_claim_date",
                (
                    "Consta una respuesta del operador, pero no la fecha validada "
                    "de la reclamación que la originó."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if _present(response_date) and not _present(provider_response):
        result.append(
            missing_item(
                "telecom_response_content_missing",
                (
                    "Consta una fecha de respuesta sin su contenido completo; debe "
                    "incorporarse antes de fijar la pretensión administrativa."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    result.extend(
        [
            missing_item(
                "telecom_contract_and_offer_review",
                (
                    "OPS debe contrastar contrato, resumen contractual, oferta, "
                    "tarifa, permanencia, equipos asociados y modificaciones "
                    "comunicadas por el operador."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "telecom_operator_identity_review",
                (
                    "Debe distinguirse el operador que presta el servicio y emite "
                    "la factura de distribuidores, instaladores, comercios o "
                    "plataformas intermediarias."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    invoice, _ = validated_value(record, "factura_numero")
    billed_period, _ = validated_value(record, "periodo_facturado")
    claimed_amount, _ = validated_value(record, "importe_reclamado_eur")
    if (
        _present(invoice)
        or _present(billed_period)
        or _present(claimed_amount)
        or any(token in text for token in ("factura", "cobro", "cargo", "cuota"))
    ):
        result.append(
            missing_item(
                "telecom_billing_breakdown_review",
                (
                    "Debe comprobarse cada factura, periodo, tarifa, impuesto, "
                    "servicio adicional, pago y abono, sin aceptar automáticamente "
                    "el total reclamado."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        if _present(claimed_amount) and not (
            _present(invoice) or _present(billed_period)
        ):
            result.append(
                missing_item(
                    "telecom_claimed_amount_support_missing",
                    (
                        "La cuantía reclamada carece de factura o periodo "
                        "facturado validado que permita reconstruirla."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    cancellation_requested, _ = validated_value(
        record,
        "baja_solicitada_fecha",
    )
    cancellation_effective, _ = validated_value(
        record,
        "fecha_baja_efectiva",
    )
    if "baja" in text or _present(cancellation_requested):
        if not _present(cancellation_requested):
            result.append(
                missing_item(
                    "telecom_cancellation_request_date_missing",
                    (
                        "Debe validarse la fecha, canal, contenido y referencia de "
                        "la solicitud de baja."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if not _present(cancellation_effective):
            result.append(
                missing_item(
                    "telecom_effective_cancellation_review",
                    (
                        "Debe comprobarse la fecha en que la baja surtió efecto y "
                        "si existieron cargos posteriores no imputables al usuario."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        requested_date = _parse_date(cancellation_requested)
        effective_date = _parse_date(cancellation_effective)
        if (
            requested_date is not None
            and effective_date is not None
            and effective_date < requested_date
        ):
            result.append(
                missing_item(
                    "telecom_cancellation_date_conflict",
                    (
                        "La baja efectiva aparece anterior a su solicitud; debe "
                        "revisarse la lectura documental."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    if any(token in text for token in ("portabilidad", "portar", "cambio de operador")):
        result.append(
            missing_item(
                "telecom_portability_evidence_review",
                (
                    "Deben incorporarse solicitud de portabilidad, operador de "
                    "origen y destino, número afectado, fechas y comunicaciones de "
                    "rechazo, demora o cancelación."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if any(
        token in text
        for token in (
            "averia",
            "interrupcion",
            "sin servicio",
            "velocidad",
            "cobertura",
        )
    ):
        result.append(
            missing_item(
                "telecom_quality_and_interruption_review",
                (
                    "Debe concretarse inicio, fin, alcance y prueba de la avería, "
                    "interrupción o discrepancia de calidad, junto con el compromiso "
                    "contractual y cualquier medición válida."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if any(
        token in text
        for token in (
            "alta no solicitada",
            "contratacion no solicitada",
            "contrato no solicitado",
            "servicio no solicitado",
        )
    ):
        result.append(
            missing_item(
                "telecom_unauthorised_contract_proof_review",
                (
                    "La contratación no solicitada debe contrastarse con la prueba "
                    "de consentimiento que corresponde aportar al operador y con "
                    "los cargos o servicios efectivamente activados."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if any(
        token in text
        for token in (
            "llamada comercial",
            "llamadas comerciales",
            "llamada no deseada",
            "proteccion de datos",
            "datos personales",
            "spam",
        )
    ):
        result.append(
            missing_item(
                "telecom_privacy_authority_review",
                (
                    "La protección de datos y las llamadas comerciales no deseadas "
                    "pueden quedar fuera del procedimiento sectorial ante la Oficina; "
                    "debe revisarse la competencia de la AEPD u otra vía."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if any(
        token in text
        for token in (
            "danos y perjuicios",
            "lucro cesante",
            "dano moral",
            "clausula abusiva",
        )
    ):
        result.append(
            missing_item(
                "telecom_non_sectoral_remedy_review",
                (
                    "Los daños y perjuicios y el control de cláusulas abusivas no "
                    "deben incluirse como si fueran una restitución sectorial "
                    "automática; debe determinarse la vía civil o de consumo."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    return result


def _deadlines(
    record: ValidatedFactsRecord,
    route: RouteState,
) -> list[Deadline]:
    explicit, explicit_key = validated_value(record, "fecha_limite")
    if _present(explicit) and explicit_key:
        parsed = _parse_date(explicit)
        if parsed is not None:
            return [
                Deadline(
                    label="Fecha límite documental indicada",
                    due_at=datetime(
                        parsed.year,
                        parsed.month,
                        parsed.day,
                        tzinfo=timezone.utc,
                    ),
                    calculation_status="confirmed",
                    source_fact_keys=[explicit_key],
                    notes=[
                        (
                            "Es una fecha transcrita del documento; OPS debe "
                            "confirmar qué actuación concreta vence en ella."
                        )
                    ],
                )
            ]

    prior_claim, prior_key = validated_value(
        record,
        "reclamacion_previa_fecha",
    )
    response_date, response_key = validated_value(record, "fecha_respuesta")
    deadlines: list[Deadline] = []

    if not _present(prior_claim):
        deadlines.append(
            Deadline(
                label="Reclamación previa al operador",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[],
                notes=[
                    (
                        "La Orden ITC/1030/2007 prevé, con carácter general, la "
                        "reclamación al operador dentro del mes desde que se conoce "
                        "el hecho; debe fijarse el dies a quo documental."
                    )
                ],
            )
        )
        return deadlines

    deadlines.append(
        Deadline(
            label="Plazo de respuesta del operador",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[prior_key] if prior_key else [],
            notes=[
                (
                    "El operador dispone de un mes desde la recepción de la "
                    "reclamación. RTM no suma automáticamente meses ni presume la "
                    "fecha de recepción."
                )
            ],
        )
    )

    office_sources = [key for key in (response_key, prior_key) if key]
    office_note = (
        "El plazo general para acudir a la Oficina es de tres meses desde la "
        "respuesta del operador o desde que termina su plazo de un mes para "
        "responder. Debe fijarse el punto inicial exacto."
    )
    if route == "office_review" and _present(response_date):
        office_note += f" Fecha de respuesta validada: {response_date}."
    deadlines.append(
        Deadline(
            label="Reclamación ante la Oficina de Atención al Usuario",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=list(dict.fromkeys(office_sources)),
            notes=[office_note],
        )
    )
    return deadlines


def build_claims_telecommunications_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="telecomunicaciones",
        specialist="claims.telecommunications",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    provider, provider_key = validated_value(
        facts_record,
        "proveedor",
        "emisor_documento",
    )
    service, service_key = validated_value(
        facts_record,
        "producto_servicio",
    )
    contract, contract_key = validated_value(
        facts_record,
        "contrato_ref",
    )
    service_ref, service_ref_key = validated_value(
        facts_record,
        "referencia_servicio",
    )
    invoice, invoice_key = validated_value(
        facts_record,
        "factura_numero",
    )
    billed_period, billed_period_key = validated_value(
        facts_record,
        "periodo_facturado",
    )
    claimed_amount, claimed_amount_key = validated_value(
        facts_record,
        "importe_reclamado_eur",
    )
    paid_amount, paid_amount_key = validated_value(
        facts_record,
        "importe_pagado_eur",
    )
    contract_date, contract_date_key = validated_value(
        facts_record,
        "fecha_contrato",
    )
    cancellation_requested, cancellation_requested_key = validated_value(
        facts_record,
        "baja_solicitada_fecha",
    )
    cancellation_effective, cancellation_effective_key = validated_value(
        facts_record,
        "fecha_baja_efectiva",
    )
    prior_claim, prior_claim_key = validated_value(
        facts_record,
        "reclamacion_previa_fecha",
    )
    claim_channel, claim_channel_key = validated_value(
        facts_record,
        "canal_reclamacion",
    )
    response, response_key = validated_value(
        facts_record,
        "respuesta_proveedor",
        "respuesta_documentada",
    )
    response_date, response_date_key = validated_value(
        facts_record,
        "fecha_respuesta",
    )
    requested_solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )

    route = _route_state(facts_record)

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("proveedor", "Operador o proveedor", ""),
            ("producto_servicio", "Servicio", ""),
            ("contrato_ref", "Contrato", ""),
            ("referencia_servicio", "Referencia de cliente o línea", ""),
            ("fecha_contrato", "Fecha del contrato", ""),
            ("factura_numero", "Factura", ""),
            ("periodo_facturado", "Periodo facturado", ""),
            ("importe_reclamado_eur", "Importe reclamado", " €"),
            ("importe_pagado_eur", "Importe pagado", " €"),
            ("baja_solicitada_fecha", "Solicitud de baja", ""),
            ("fecha_baja_efectiva", "Baja efectiva", ""),
            ("reclamacion_previa_fecha", "Reclamación previa", ""),
            ("canal_reclamacion", "Canal de reclamación", ""),
            ("fecha_respuesta", "Respuesta del operador", ""),
            ("solucion_solicitada", "Solución solicitada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {_display(fact)}.")
        if fact_key:
            summary_keys.insert(0, fact_key)

    arguments = []

    contract_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            provider_key,
            service_key,
            contract_key,
            service_ref_key,
            contract_date_key,
        ),
    )
    if contract_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_operator_contract_and_service",
                title="Operador, contrato y servicio afectados",
                body=(
                    "La reclamación debe identificar al operador responsable, el "
                    "servicio o paquete, la referencia contractual y las condiciones "
                    "ofrecidas. No debe atribuirse al operador una oferta, "
                    "permanencia, equipo o modificación que no conste en hechos "
                    "documentales validados."
                ),
                source_fact_keys=contract_sources,
                priority="primary",
                legal_basis=[*_SECTOR_BASIS, *_CONSUMER_BASIS],
            )
        )

    billing_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            provider_key,
            invoice_key,
            billed_period_key,
            claimed_amount_key,
            paid_amount_key,
            service_key,
        ),
    )
    if billing_sources:
        amount_text = (
            f"{claimed_amount} €"
            if _present(claimed_amount)
            else "sin cuantía total validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_billing_accuracy_and_breakdown",
                title="Facturación, conceptos y cuantía discutida",
                body=(
                    "La factura debe ser clara, detallada y permitir comprobar los "
                    "servicios, periodos y cargos aplicados. La cuantía discutida "
                    f"figura como {amount_text}. RTM no acepta ni recalcula el total "
                    "sin contrastar contrato, tarifa, consumo, abonos y pagos."
                ),
                source_fact_keys=billing_sources,
                priority="primary",
                legal_basis=[*_SECTOR_BASIS, *_CONSUMER_BASIS],
            )
        )

    cancellation_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            provider_key,
            service_key,
            cancellation_requested_key,
            cancellation_effective_key,
            billed_period_key,
            claimed_amount_key,
        ),
    )
    if cancellation_sources and (
        _present(cancellation_requested)
        or "baja" in _all_text(facts_record)
    ):
        requested_text = (
            str(cancellation_requested)
            if _present(cancellation_requested)
            else "pendiente de validar"
        )
        effective_text = (
            str(cancellation_effective)
            if _present(cancellation_effective)
            else "pendiente de validar"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_cancellation_and_post_termination_charges",
                title="Baja del servicio y cargos posteriores",
                body=(
                    "La voluntad de baja debe conservar fecha, canal, contenido y "
                    "referencia. La solicitud aparece en "
                    f"{requested_text} y la baja efectiva en {effective_text}. "
                    "Debe verificarse el plazo de dos días hábiles y excluir cargos "
                    "posteriores no imputables al usuario, sin calcular días "
                    "hábiles automáticamente."
                ),
                source_fact_keys=cancellation_sources,
                priority="primary",
                legal_basis=[*_SECTOR_BASIS, *_CONSUMER_BASIS],
            )
        )

    performance_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            provider_key,
            service_key,
            contract_key,
            service_ref_key,
            response_key,
        ),
    )
    if performance_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_contractual_performance_and_sector_rights",
                title="Cumplimiento de la oferta y derechos sectoriales",
                body=(
                    "El operador debe cumplir las condiciones contractuales y los "
                    "derechos específicos aplicables a facturación, calidad, "
                    "interrupciones, baja, cambio de operador o contratación. OPS "
                    "debe seleccionar únicamente el incumplimiento respaldado por "
                    "el expediente, sin mezclarlo con materias ajenas al "
                    "procedimiento sectorial."
                ),
                source_fact_keys=performance_sources,
                priority="primary",
                legal_basis=[*_SECTOR_BASIS, *_CONSUMER_BASIS],
            )
        )

    claim_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            prior_claim_key,
            claim_channel_key,
            response_key,
            response_date_key,
            provider_key,
            solution_key,
        ),
    )
    if claim_sources:
        response_text = (
            _display(response)
            if _present(response)
            else "sin respuesta validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_prior_claim_and_operator_response",
                title="Reclamación previa y respuesta del operador",
                body=(
                    "La vía administrativa exige acreditar primero una reclamación "
                    "al operador. Deben conservarse su recepción, número de "
                    "referencia, contenido y respuesta. En este expediente la "
                    f"respuesta figura como: {response_text}."
                ),
                source_fact_keys=claim_sources,
                priority="primary",
                legal_basis=list(_SECTOR_BASIS),
            )
        )

    route_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            prior_claim_key,
            response_key,
            response_date_key,
            solution_key,
            provider_key,
        ),
    )
    if route_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="telecom_sector_route_scope_and_exclusions",
                title="Vía sectorial, competencia y límites de la petición",
                body=(
                    "La Oficina puede resolver controversias sobre derechos "
                    "específicos de telecomunicaciones de personas físicas, "
                    "autónomos y microempresas, después de la reclamación previa. "
                    "No deben introducirse como si fueran restituciones sectoriales "
                    "automáticas los daños y perjuicios, las cláusulas abusivas o "
                    "las materias de protección de datos."
                ),
                source_fact_keys=route_sources,
                priority="secondary",
                legal_basis=list(_SECTOR_BASIS),
            )
        )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa.",
        )

    source_keys = validated_source_keys(
        facts_record,
        [
            *family_evidence_keys(family_record),
            *summary_keys,
            *(key for argument in arguments for key in argument.source_fact_keys),
        ],
    )

    missing = dedupe_missing(
        [
            *_required_missing(facts_record),
            *_review_missing(facts_record, route),
            *fact_review_items(facts_record, prefix="telecommunications"),
        ]
    )

    if route == "office_review":
        destination = "OFICINA DE ATENCIÓN AL USUARIO DE TELECOMUNICACIONES"
        document_type = (
            "RECLAMACIÓN ADMINISTRATIVA EN MATERIA DE TELECOMUNICACIONES "
            "— COMPETENCIA Y PLAZO PENDIENTES DE VALIDAR"
        )
    else:
        destination = (
            str(provider).strip()
            if _present(provider)
            else "OPERADOR DE TELECOMUNICACIONES PENDIENTE DE VALIDAR"
        )
        document_type = (
            "RECLAMACIÓN PREVIA AL OPERADOR DE TELECOMUNICACIONES"
            if route == "operator"
            else "REITERACIÓN AL OPERADOR Y RESERVA DE RECLAMACIÓN ADMINISTRATIVA"
        )

    subject_parts = ["RECLAMACIÓN DE TELECOMUNICACIONES"]
    if _present(service_ref):
        subject_parts.append(f"referencia {service_ref}")
    elif _present(contract):
        subject_parts.append(f"contrato {contract}")
    if _present(invoice):
        subject_parts.append(f"factura {invoice}")
    if _present(claimed_amount):
        subject_parts.append(f"importe {claimed_amount} €")

    risks = [
        (
            "La Oficina sectorial exige reclamación previa al operador y solo "
            "admite determinadas personas y materias."
        ),
        (
            "El plazo para reclamar al operador, el mes de respuesta y los tres "
            "meses para acudir a la Oficina no se han calculado automáticamente."
        ),
        (
            "Daños y perjuicios, cláusulas abusivas y protección de datos pueden "
            "exigir una vía distinta a la sectorial de telecomunicaciones."
        ),
        (
            "No se ha dado por válida ninguna factura, penalización, permanencia, "
            "baja, portabilidad o contratación sin soporte documental."
        ),
    ]
    if _present(response):
        risks.append(
            "La respuesta del operador debe valorarse íntegramente y no solo por su resultado."
        )

    primary_strategy = (
        "Reconstruir operador, contrato, servicio, facturas y comunicaciones; "
        "separar el incumplimiento sectorial de otras pretensiones; presentar o "
        "acreditar la reclamación previa; y escalar a la Oficina únicamente "
        "cuando competencia, legitimación y plazo estén validados."
    )
    if _present(requested_solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(requested_solution)}."
        )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="telecomunicaciones",
        specialist="claims.telecommunications",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una controversia de telecomunicaciones: {_display(fact)}."
            if _present(fact)
            else "Se ha documentado una posible controversia de telecomunicaciones."
        ),
        client_goal=(
            "Corregir el servicio o la relación contractual, anular cargos "
            "indebidos, recuperar importes y obtener una respuesta motivada."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            (
                "Solicitar al operador contrato, grabación o prueba de "
                "contratación, detalle de facturación y trazabilidad de gestiones."
            ),
            (
                "Valorar Junta Arbitral de Consumo cuando sea compatible con la "
                "materia y la estrategia elegida."
            ),
            (
                "Separar protección de datos, daños y cláusulas abusivas para "
                "dirigirlas a la autoridad o jurisdicción competente."
            ),
        ],
        requested_outcomes=[
            "Rectificación o anulación de facturas y cargos no justificados.",
            "Devolución de importes indebidamente cobrados y aplicación de abonos.",
            (
                "Baja, restablecimiento, portabilidad o corrección del servicio "
                "cuando la documentación lo justifique."
            ),
            (
                "Entrega del contrato, grabaciones, referencias de gestión, "
                "facturación detallada y respuesta motivada."
            ),
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, route),
        risks=list(dict.fromkeys(risks)),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Contrato, resumen contractual, oferta y condiciones vigentes.",
            "Facturas completas, periodos, consumos, pagos, abonos y penalizaciones.",
            "Número de cliente, línea o referencia del servicio afectado.",
            "Justificante de la baja, portabilidad, avería o gestión controvertida.",
            "Reclamación previa al operador con fecha, contenido, canal y referencia.",
            "Respuesta completa del operador y justificante de su recepción.",
            "Grabación o soporte que acredite la contratación cuando sea discutida.",
            "Documentación que identifique al operador efectivo frente a intermediarios.",
        ],
        created_by_component=(
            "claims.telecommunications:"
            f"{CLAIMS_TELECOMMUNICATIONS_SPECIALIST_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
