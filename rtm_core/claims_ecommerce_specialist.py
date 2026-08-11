"""Especialista RTM para comercio electrónico y mercados en línea.

Consume hechos congelados y una familia bloqueada. Distingue vendedor,
marketplace y transportista; no convierte a la plataforma en vendedora por
defecto, no presupone la conformidad o el desistimiento y no duplica importes
recuperados por el medio de pago.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
from rtm_core.claims_ecommerce_regime import (
    CLAIMS_ECOMMERCE_REGIME_VERSION,
    ClaimsEcommerceRegimeDecision,
    resolve_claims_ecommerce_regime,
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


CLAIMS_ECOMMERCE_SPECIALIST_VERSION = "rtm_claims_ecommerce_specialist_v1_0"

RouteState = Literal[
    "seller",
    "seller_period_review",
    "marketplace",
    "authority_review",
]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"[^a-z0-9%/.,:+@€-]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
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


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_ecommerce_tipo",
        "producto_servicio",
        "pedido_tipo_contrato",
        "pedido_producto_descripcion",
        "pedido_servicio_descripcion",
        "publicidad_oferta_descripcion",
        "falta_conformidad_descripcion",
        "excepcion_desistimiento_invocada",
        "respuesta_proveedor",
        "respuesta_documentada",
        "solucion_solicitada",
        "retencion_reembolso_motivo",
        "aviso_seguridad_producto",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsEcommerceRegimeDecision:
    purchase_date, _ = validated_value(record, "fecha_compra", "fecha_pedido")
    delivery_date, _ = validated_value(
        record,
        "fecha_entrega_efectiva",
        "fecha_entrega",
    )
    incident_date, _ = validated_value(
        record,
        "fecha_incidencia",
        "fecha_manifestacion_falta_conformidad",
    )
    withdrawal_date, _ = validated_value(
        record,
        "fecha_comunicacion_desistimiento",
    )
    complaint_date, _ = validated_value(record, "reclamacion_previa_fecha")
    seller_country, _ = validated_value(record, "pais_vendedor")
    consumer_country, _ = validated_value(record, "pais_consumidor")
    buyer_consumer, _ = validated_value(record, "comprador_es_consumidor")
    seller_trader, _ = validated_value(record, "vendedor_es_empresario")
    distance_contract, _ = validated_value(record, "contrato_a_distancia")
    contract_type, _ = validated_value(record, "pedido_tipo_contrato")
    product_description, _ = validated_value(
        record,
        "pedido_producto_descripcion",
        "producto_servicio",
    )
    service_description, _ = validated_value(record, "pedido_servicio_descripcion")
    goods_digital, _ = validated_value(record, "producto_bien_digital")
    digital, _ = validated_value(record, "contenido_servicio_digital")
    incident_type, _ = validated_value(record, "incidencia_ecommerce_tipo")
    marketplace, _ = validated_value(record, "marketplace")
    platform_party, _ = validated_value(record, "marketplace_es_parte_contractual")
    delivered, _ = validated_value(record, "pedido_entregado")
    agreed_delivery, _ = validated_value(record, "fecha_entrega_pactada")
    nonconformity, _ = validated_value(record, "falta_conformidad_descripcion")
    withdrawal, _ = validated_value(record, "desistimiento_comunicado")
    refund_amount, _ = validated_value(
        record,
        "importe_reembolso_pedido_eur",
        "importe_reclamado_eur",
    )
    refund_date, _ = validated_value(record, "fecha_reembolso_pedido")
    subscription, _ = validated_value(record, "suscripcion_online")
    renewal, _ = validated_value(record, "renovacion_automatica")
    seller_identified, _ = validated_value(record, "marketplace_vendedor_identificado")
    trader_disclosed, _ = validated_value(
        record,
        "marketplace_informa_condicion_empresario",
    )
    unsafe, _ = validated_value(record, "producto_inseguro")
    post_guarantee, _ = validated_value(
        record,
        "reparacion_fuera_garantia_solicitada",
    )
    return resolve_claims_ecommerce_regime(
        purchase_date=purchase_date,
        delivery_date=delivery_date,
        incident_date=incident_date,
        withdrawal_date=withdrawal_date,
        complaint_date=complaint_date,
        seller_country=seller_country,
        consumer_country=consumer_country,
        buyer_is_consumer=buyer_consumer,
        seller_is_trader=seller_trader,
        distance_contract=distance_contract,
        contract_type=contract_type,
        product_description=product_description,
        service_description=service_description,
        goods_with_digital_elements=goods_digital,
        digital_content_or_service=digital,
        incident_type=incident_type,
        issue_text=_all_text(record),
        marketplace_present=marketplace,
        platform_is_contracting_party=platform_party,
        order_delivered=delivered,
        agreed_delivery_date=agreed_delivery,
        nonconformity_description=nonconformity,
        withdrawal_communicated=withdrawal,
        refund_amount=refund_amount,
        refund_date=refund_date,
        subscription=subscription,
        automatic_renewal=renewal,
        seller_identified=seller_identified,
        trader_status_disclosed=trader_disclosed,
        unsafe_product=unsafe,
        post_guarantee_repair_requested=post_guarantee,
    )


def _route_state(
    record: ValidatedFactsRecord,
    regime: ClaimsEcommerceRegimeDecision,
) -> RouteState:
    seller, _ = validated_value(record, "vendedor_online", "proveedor")
    marketplace, _ = validated_value(record, "marketplace")
    seller_identified, _ = validated_value(record, "marketplace_vendedor_identificado")
    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    response, _ = validated_value(
        record,
        "respuesta_proveedor",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(
        record,
        "fecha_respuesta_vendedor",
        "fecha_respuesta",
    )

    if _present(prior_claim) and (_present(response) or _present(response_date)):
        return "authority_review"
    if regime.incident_type in {
        "marketplace_disclosure",
        "seller_identity_or_illicit_goods",
    } and _present(marketplace):
        return "marketplace"
    if not _present(seller) and _present(marketplace):
        return "marketplace"
    if seller_identified is False and _present(marketplace):
        return "marketplace"
    if _present(prior_claim):
        return "seller_period_review"
    return "seller"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsEcommerceRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "ecommerce_fact_missing",
            "Falta validar la incidencia concreta de comercio electrónico.",
            ("descripcion_hecho", "incidencia_ecommerce_tipo"),
        ),
        (
            "ecommerce_purchase_date_missing",
            "Falta la fecha de compra o celebración del contrato.",
            ("fecha_compra", "fecha_pedido"),
        ),
        (
            "ecommerce_order_reference_missing",
            "Falta el número o referencia del pedido.",
            ("numero_pedido", "referencia_documento", "contrato_ref"),
        ),
        (
            "ecommerce_seller_country_missing",
            "Falta el país del vendedor.",
            ("pais_vendedor",),
        ),
        (
            "ecommerce_trader_status_missing",
            "Falta validar si el vendedor actúa como empresario.",
            ("vendedor_es_empresario",),
        ),
        (
            "ecommerce_distance_contract_missing",
            "Falta validar que el contrato se celebró a distancia.",
            ("contrato_a_distancia",),
        ),
        (
            "ecommerce_subject_missing",
            "Falta identificar el bien, contenido digital o servicio contratado.",
            (
                "pedido_tipo_contrato",
                "pedido_producto_descripcion",
                "pedido_servicio_descripcion",
                "producto_servicio",
            ),
        ),
        (
            "ecommerce_requested_solution_missing",
            "Falta validar la solución solicitada.",
            ("solucion_solicitada",),
        ),
    ]
    if route == "marketplace":
        groups.append(
            (
                "ecommerce_marketplace_missing",
                "Falta identificar el marketplace o plataforma.",
                ("marketplace",),
            )
        )
    else:
        groups.append(
            (
                "ecommerce_seller_missing",
                "Falta identificar al vendedor reclamado.",
                ("vendedor_online", "proveedor", "emisor_documento"),
            )
        )

    if regime.incident_type in {"non_delivery", "late_delivery"}:
        groups.extend(
            [
                (
                    "ecommerce_delivery_status_missing",
                    "Falta el estado documental de entrega.",
                    ("pedido_entregado", "fecha_entrega_efectiva", "fecha_entrega"),
                ),
                (
                    "ecommerce_delivery_term_missing",
                    "Falta la fecha pactada o documentación que permita aplicar el plazo supletorio.",
                    ("fecha_entrega_pactada", "fecha_compra", "fecha_pedido"),
                ),
            ]
        )
    elif regime.incident_type == "partial_or_wrong_delivery":
        groups.append(
            (
                "ecommerce_order_content_evidence_missing",
                "Falta comparar lo pedido, lo anunciado y lo recibido.",
                (
                    "pedido_producto_descripcion",
                    "publicidad_oferta_descripcion",
                    "producto_servicio",
                ),
            )
        )
    elif regime.incident_type == "non_conformity":
        groups.extend(
            [
                (
                    "ecommerce_nonconformity_missing",
                    "Falta describir la falta de conformidad.",
                    ("falta_conformidad_descripcion",),
                ),
                (
                    "ecommerce_delivery_date_for_conformity_missing",
                    "Falta la fecha de entrega o suministro.",
                    ("fecha_entrega_efectiva", "fecha_entrega"),
                ),
                (
                    "ecommerce_remedy_missing",
                    "Falta concretar la medida correctora solicitada.",
                    (
                        "reparacion_solicitada",
                        "sustitucion_solicitada",
                        "reduccion_precio_solicitada",
                        "resolucion_contrato_solicitada",
                        "solucion_solicitada",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "withdrawal":
        groups.append(
            (
                "ecommerce_withdrawal_notice_missing",
                "Falta la comunicación y fecha del desistimiento.",
                ("desistimiento_comunicado", "fecha_comunicacion_desistimiento"),
            )
        )
    elif regime.incident_type == "refund_delay":
        groups.extend(
            [
                (
                    "ecommerce_refund_amount_missing",
                    "Falta el importe reclamado o reembolso discutido.",
                    ("importe_reembolso_pedido_eur", "importe_reclamado_eur"),
                ),
                (
                    "ecommerce_refund_trigger_missing",
                    "Falta la fecha o hecho que origina el reembolso.",
                    (
                        "fecha_comunicacion_desistimiento",
                        "reclamacion_previa_fecha",
                        "contrato_resuelto_por_no_entrega",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "subscription":
        groups.append(
            (
                "ecommerce_subscription_evidence_missing",
                "Faltan las condiciones y fechas de la suscripción o renovación.",
                (
                    "suscripcion_online",
                    "renovacion_automatica",
                    "fecha_renovacion_suscripcion",
                    "baja_suscripcion_solicitada_fecha",
                ),
            )
        )
    elif regime.incident_type == "digital_content":
        groups.append(
            (
                "ecommerce_digital_supply_missing",
                "Falta identificar el contenido o servicio digital y su incumplimiento.",
                (
                    "contenido_servicio_digital",
                    "pedido_tipo_contrato",
                    "pedido_servicio_descripcion",
                    "falta_conformidad_descripcion",
                ),
            )
        )
    elif regime.incident_type == "marketplace_disclosure":
        groups.append(
            (
                "ecommerce_marketplace_role_missing",
                "Falta la información del marketplace sobre vendedor y reparto de obligaciones.",
                (
                    "marketplace_vendedor_identificado",
                    "marketplace_informa_condicion_empresario",
                    "marketplace_reparte_obligaciones",
                ),
            )
        )
    elif regime.incident_type == "unsafe_product":
        groups.append(
            (
                "ecommerce_safety_evidence_missing",
                "Falta la alerta, retirada o documentación del riesgo del producto.",
                (
                    "producto_inseguro",
                    "retirada_producto_anunciada",
                    "aviso_seguridad_producto",
                ),
            )
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsEcommerceRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    if regime.status != "current":
        result.append(
            missing_item(
                "ecommerce_regime_review",
                regime.blocking_reason or "Debe determinarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.consumer_status_review:
        result.append(
            missing_item(
                "ecommerce_consumer_status_review",
                "Debe confirmarse que el comprador actuó como consumidor y fuera de una actividad empresarial o profesional.",
                MissingItemSeverity.BLOCKING,
            )
        )

    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    channel, _ = validated_value(record, "canal_reclamacion")
    claim_ref, _ = validated_value(
        record,
        "reclamacion_ecommerce_ref",
        "referencia_documento",
        "expediente_ref",
    )
    if route == "seller":
        result.append(
            missing_item(
                "ecommerce_prior_seller_claim_required",
                "Debe presentarse primero una reclamación trazable al vendedor y conservar fecha, canal y referencia.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "seller_period_review":
        result.append(
            missing_item(
                "ecommerce_seller_response_period_review",
                "Consta reclamación previa sin respuesta. Debe comprobarse el plazo aplicable antes de escalar, sin convertir automáticamente días hábiles en naturales.",
                MissingItemSeverity.BLOCKING,
            )
        )
    elif route == "authority_review":
        result.append(
            missing_item(
                "ecommerce_authority_or_adr_competence_review",
                "Debe verificarse la competencia territorial y material del organismo de consumo o entidad RAL antes de dirigir el escrito.",
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "ecommerce_marketplace_own_duties_review",
                "La reclamación al marketplace debe limitarse a su información, trazabilidad, interfaz, pago, logística o actos propios, sin tratarlo automáticamente como vendedor.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if _present(prior_claim) and not _present(channel):
        result.append(
            missing_item(
                "ecommerce_claim_channel_missing",
                "Falta el canal de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(claim_ref):
        result.append(
            missing_item(
                "ecommerce_claim_reference_missing",
                "Falta el justificante o número de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    marketplace, _ = validated_value(record, "marketplace")
    platform_party, _ = validated_value(record, "marketplace_es_parte_contractual")
    seller_identified, _ = validated_value(record, "marketplace_vendedor_identificado")
    trader_disclosed, _ = validated_value(
        record,
        "marketplace_informa_condicion_empresario",
    )
    seller_address, _ = validated_value(record, "vendedor_domicilio")
    seller_tax, _ = validated_value(record, "vendedor_identificador_fiscal")
    if _present(marketplace):
        if seller_identified is False:
            result.append(
                missing_item(
                    "ecommerce_marketplace_seller_identity_missing",
                    "El marketplace no identifica al vendedor tercero; debe requerirse su trazabilidad.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if trader_disclosed is False:
            result.append(
                missing_item(
                    "ecommerce_marketplace_trader_status_missing",
                    "El marketplace no informó si el oferente actuaba como empresario.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if platform_party is not True:
            result.append(
                missing_item(
                    "ecommerce_marketplace_not_automatic_seller",
                    "No consta que la plataforma sea parte contractual; la pretensión principal debe dirigirse al vendedor salvo obligación propia de la plataforma.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    if not _present(seller_address) and not _present(seller_tax):
        result.append(
            missing_item(
                "ecommerce_seller_traceability_review",
                "Conviene completar domicilio o identificador del vendedor para una reclamación ejecutable.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    purchase = _parse_date(validated_value(record, "fecha_compra", "fecha_pedido")[0])
    agreed = _parse_date(validated_value(record, "fecha_entrega_pactada")[0])
    delivered_on = _parse_date(
        validated_value(record, "fecha_entrega_efectiva", "fecha_entrega")[0]
    )
    shipped = _parse_date(validated_value(record, "fecha_envio")[0])
    delivered, _ = validated_value(record, "pedido_entregado")
    proof, _ = validated_value(record, "prueba_entrega_aportada")
    authorized_third, _ = validated_value(record, "entrega_a_tercero_autorizado")
    consumer_carrier, _ = validated_value(
        record,
        "transportista_elegido_por_consumidor",
    )
    additional_request = _parse_date(
        validated_value(record, "fecha_requerimiento_entrega_adicional")[0]
    )
    terminated_non_delivery, _ = validated_value(
        record,
        "contrato_resuelto_por_no_entrega",
    )
    if purchase and agreed and agreed < purchase:
        result.append(
            missing_item(
                "ecommerce_agreed_delivery_before_purchase",
                "La fecha de entrega pactada aparece anterior a la compra.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if purchase and shipped and shipped < purchase:
        result.append(
            missing_item(
                "ecommerce_shipping_before_purchase",
                "La fecha de envío aparece anterior a la compra.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if purchase and delivered_on and delivered_on < purchase:
        result.append(
            missing_item(
                "ecommerce_delivery_before_purchase",
                "La entrega aparece anterior a la compra.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if delivered is False and proof is True:
        result.append(
            missing_item(
                "ecommerce_delivery_proof_conflict",
                "Constan simultáneamente no entrega y prueba de entrega; debe revisarse destinatario, lugar y autenticidad.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if proof is True and authorized_third is not True and delivered is not True:
        result.append(
            missing_item(
                "ecommerce_delivery_recipient_review",
                "La prueba de entrega no acredita todavía recepción por el consumidor o tercero autorizado.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if consumer_carrier is True:
        result.append(
            missing_item(
                "ecommerce_consumer_selected_carrier_risk_review",
                "El consumidor pudo elegir un transportista no propuesto por el vendedor; debe revisarse el momento de transmisión del riesgo.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if terminated_non_delivery is True and additional_request is None:
        result.append(
            missing_item(
                "ecommerce_additional_delivery_period_review",
                "Debe comprobarse si era exigible conceder un plazo adicional antes de resolver por falta de entrega o si concurría una excepción.",
                MissingItemSeverity.BLOCKING,
            )
        )

    manifestation = _parse_date(
        validated_value(record, "fecha_manifestacion_falta_conformidad")[0]
    )
    notice = _parse_date(
        validated_value(record, "fecha_comunicacion_falta_conformidad")[0]
    )
    misuse, _ = validated_value(record, "uso_instalacion_incorrectos_consumidor")
    second_hand, _ = validated_value(record, "producto_segunda_mano")
    second_hand_months = _number(
        validated_value(record, "plazo_garantia_segunda_mano_meses")[0]
    )
    repair_requested, _ = validated_value(record, "reparacion_solicitada")
    replacement_requested, _ = validated_value(record, "sustitucion_solicitada")
    reduction_requested, _ = validated_value(record, "reduccion_precio_solicitada")
    termination_requested, _ = validated_value(record, "resolucion_contrato_solicitada")
    remedy, _ = validated_value(record, "remedio_ofrecido_vendedor")
    repair_handover = _parse_date(validated_value(record, "fecha_entrega_reparacion")[0])
    repair_return = _parse_date(validated_value(record, "fecha_devolucion_reparacion")[0])
    repair_free, _ = validated_value(record, "reparacion_sin_coste")
    repair_reasonable, _ = validated_value(record, "reparacion_plazo_razonable")
    inconvenience, _ = validated_value(record, "inconvenientes_significativos")
    if delivered_on and manifestation and manifestation < delivered_on:
        result.append(
            missing_item(
                "ecommerce_nonconformity_before_delivery",
                "La falta de conformidad aparece manifestada antes de la entrega.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if manifestation and notice and notice < manifestation:
        result.append(
            missing_item(
                "ecommerce_nonconformity_notice_chronology",
                "La comunicación al vendedor aparece anterior a la manifestación del defecto.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if misuse is True:
        result.append(
            missing_item(
                "ecommerce_consumer_misuse_causation_review",
                "Consta uso o instalación incorrecta del consumidor; debe verificarse su relación causal con el defecto.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if second_hand is True and second_hand_months is not None:
        if second_hand_months < 12 or second_hand_months > 36:
            result.append(
                missing_item(
                    "ecommerce_second_hand_guarantee_period_review",
                    "El plazo pactado para el bien de segunda mano queda fuera del rango operativo de uno a tres años y requiere revisión.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if repair_handover and repair_return and repair_return < repair_handover:
        result.append(
            missing_item(
                "ecommerce_repair_chronology_conflict",
                "La devolución de la reparación aparece anterior a su entrega al vendedor.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if repair_requested is True and repair_free is False:
        result.append(
            missing_item(
                "ecommerce_repair_cost_review",
                "La reparación de conformidad consta como no gratuita.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if repair_requested is True and repair_reasonable is False:
        result.append(
            missing_item(
                "ecommerce_repair_delay_review",
                "La reparación consta fuera de un plazo razonable.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if inconvenience is True:
        result.append(
            missing_item(
                "ecommerce_significant_inconvenience_review",
                "Constan inconvenientes significativos en la puesta en conformidad.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if (reduction_requested is True or termination_requested is True) and not _present(remedy):
        result.append(
            missing_item(
                "ecommerce_remedy_hierarchy_review",
                "Debe comprobarse por qué procede reducción del precio o resolución frente a reparación o sustitución.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if repair_requested is True and replacement_requested is True:
        result.append(
            missing_item(
                "ecommerce_multiple_primary_remedies_review",
                "Constan simultáneamente reparación y sustitución como petición principal; debe fijarse orden o subsidiariedad.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    withdrawal, _ = validated_value(record, "desistimiento_comunicado")
    withdrawal_info, _ = validated_value(record, "informacion_desistimiento_entregada")
    exception, _ = validated_value(record, "excepcion_desistimiento_invocada")
    personalized, _ = validated_value(record, "producto_personalizado")
    hygiene, _ = validated_value(record, "producto_precintado_higiene")
    seal_open, _ = validated_value(record, "precinto_abierto_consumidor")
    digital_started, _ = validated_value(record, "contenido_digital_ejecucion_iniciada")
    digital_consent, _ = validated_value(record, "consentimiento_inicio_digital")
    digital_ack, _ = validated_value(record, "conocimiento_perdida_desistimiento")
    durable_confirmation, _ = validated_value(
        record,
        "confirmacion_contrato_soporte_duradero",
    )
    return_date = _parse_date(validated_value(record, "fecha_devolucion_producto")[0])
    return_received, _ = validated_value(record, "devolucion_recibida_vendedor")
    return_proof, _ = validated_value(record, "prueba_devolucion_aportada")
    return_cost_disclosed, _ = validated_value(
        record,
        "vendedor_informo_coste_devolucion",
    )
    if _present(exception):
        exception_text = _fold(exception)
        if "personaliz" in exception_text and personalized is not True:
            result.append(
                missing_item(
                    "ecommerce_personalized_goods_exception_review",
                    "La excepción por producto personalizado no está respaldada por un hecho validado.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if any(marker in exception_text for marker in ("higiene", "precint")) and not (
            hygiene is True and seal_open is True
        ):
            result.append(
                missing_item(
                    "ecommerce_hygiene_exception_review",
                    "La excepción de higiene exige producto precintado y apertura del precinto por el consumidor.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if digital_started is True and withdrawal is True:
        if not (
            digital_consent is True
            and digital_ack is True
            and durable_confirmation is True
        ):
            result.append(
                missing_item(
                    "ecommerce_digital_withdrawal_loss_requirements_missing",
                    "La pérdida del desistimiento digital exige consentimiento expreso, conocimiento y confirmación en soporte duradero.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if return_date and return_received is not True and return_proof is not True:
        result.append(
            missing_item(
                "ecommerce_return_traceability_review",
                "Consta devolución enviada, pero no recepción ni prueba suficiente de entrega al vendedor.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if withdrawal is True and return_cost_disclosed is False:
        result.append(
            missing_item(
                "ecommerce_return_cost_information_review",
                "El vendedor no informó del coste de devolución antes del contrato.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if withdrawal_info is False:
        result.append(
            missing_item(
                "ecommerce_extended_withdrawal_period_review",
                "No consta información sobre desistimiento; debe revisarse la posible ampliación del plazo sin calcularla automáticamente.",
                MissingItemSeverity.BLOCKING,
            )
        )

    subscription, _ = validated_value(record, "suscripcion_online")
    renewal, _ = validated_value(record, "renovacion_automatica")
    renewal_notice, _ = validated_value(record, "aviso_renovacion_suscripcion")
    cancellation_date = _parse_date(
        validated_value(record, "baja_suscripcion_solicitada_fecha")[0]
    )
    renewal_date = _parse_date(validated_value(record, "fecha_renovacion_suscripcion")[0])
    if subscription is True and renewal is True and renewal_notice is False:
        result.append(
            missing_item(
                "ecommerce_subscription_renewal_notice_review",
                "La renovación automática consta sin aviso previo documentado.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if cancellation_date and renewal_date and renewal_date > cancellation_date:
        result.append(
            missing_item(
                "ecommerce_subscription_charge_after_cancellation_review",
                "La renovación aparece posterior a la solicitud de baja.",
                MissingItemSeverity.BLOCKING,
            )
        )

    paid = _number(validated_value(record, "importe_pagado_eur", "precio_total_pedido_eur")[0])
    order_total = _number(validated_value(record, "precio_total_pedido_eur")[0])
    refund = _number(validated_value(record, "importe_reembolso_pedido_eur")[0])
    recovered = _number(validated_value(record, "importe_recuperado_medio_pago_eur")[0])
    return_cost = _number(validated_value(record, "coste_devolucion_eur")[0])
    unit_price = _number(validated_value(record, "precio_unidad_eur")[0])
    shipping = _number(validated_value(record, "gastos_envio_eur")[0])
    extras = _number(validated_value(record, "gastos_adicionales_pedido_eur")[0])
    for code, label, amount in (
        ("ecommerce_negative_order_total", "precio total", order_total),
        ("ecommerce_negative_refund", "reembolso", refund),
        ("ecommerce_negative_return_cost", "coste de devolución", return_cost),
    ):
        if amount is not None and amount < 0:
            result.append(
                missing_item(
                    code,
                    f"El {label} aparece negativo y debe verificarse como abono o error.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    reference_total = paid if paid is not None else order_total
    if refund is not None and reference_total is not None and refund > reference_total + 0.01:
        result.append(
            missing_item(
                "ecommerce_refund_exceeds_payment",
                "El reembolso solicitado o registrado supera el importe pagado por el pedido.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if recovered is not None and recovered > 0:
        result.append(
            missing_item(
                "ecommerce_payment_recovery_coordination_review",
                "Consta una recuperación por el medio de pago; debe descontarse de cualquier reembolso contractual para evitar duplicidad.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        order_total is not None
        and unit_price is not None
        and shipping is not None
        and extras is not None
        and order_total + 0.01 < unit_price + shipping + extras
    ):
        result.append(
            missing_item(
                "ecommerce_price_components_review",
                "El total es inferior a la suma simple de precio, envío y cargos; deben revisarse descuentos, unidades y abonos.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    unsafe, _ = validated_value(record, "producto_inseguro")
    recall, _ = validated_value(record, "retirada_producto_anunciada")
    safety_notice, _ = validated_value(record, "aviso_seguridad_producto")
    marketplace_knows, _ = validated_value(record, "marketplace_conoce_producto_ilegal")
    marketplace_notice_date, _ = validated_value(
        record,
        "marketplace_aviso_producto_ilegal_fecha",
    )
    if unsafe is True:
        result.append(
            missing_item(
                "ecommerce_product_safety_route_review",
                "Debe coordinarse la reclamación contractual con retirada, alerta y autoridad de vigilancia del mercado competente.",
                MissingItemSeverity.BLOCKING,
            )
        )
        if recall is not True and not _present(safety_notice):
            result.append(
                missing_item(
                    "ecommerce_product_recall_evidence_missing",
                    "No consta retirada ni aviso de seguridad documentado.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    if marketplace_knows is True and not _present(marketplace_notice_date):
        result.append(
            missing_item(
                "ecommerce_marketplace_illicit_product_notice_date_missing",
                "Consta conocimiento de producto ilícito sin fecha documental del aviso o conocimiento.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return dedupe_missing(result)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsEcommerceRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []
    purchase_value, purchase_key = validated_value(record, "fecha_compra", "fecha_pedido")
    purchase = _parse_date(purchase_value)
    agreed_value, agreed_key = validated_value(record, "fecha_entrega_pactada")
    agreed = _parse_date(agreed_value)
    delivery_value, delivery_key = validated_value(
        record,
        "fecha_entrega_efectiva",
        "fecha_entrega",
    )
    delivery = _parse_date(delivery_value)
    withdrawal_value, withdrawal_key = validated_value(
        record,
        "fecha_comunicacion_desistimiento",
    )
    withdrawal = _parse_date(withdrawal_value)
    complaint_value, complaint_key = validated_value(record, "reclamacion_previa_fecha")

    if agreed and agreed_key:
        result.append(
            Deadline(
                label="Entrega pactada del pedido",
                due_at=_utc(agreed),
                calculation_status="confirmed",
                source_fact_keys=[agreed_key],
                notes=["Fecha contractual validada; no determina por sí sola la consecuencia jurídica del retraso."],
            )
        )
    elif purchase and purchase_key and regime.delivery_default_days:
        result.append(
            Deadline(
                label="Entrega supletoria del pedido",
                due_at=_utc(purchase + timedelta(days=regime.delivery_default_days)),
                calculation_status="estimated",
                source_fact_keys=[purchase_key],
                notes=["Referencia de treinta días salvo pacto distinto o naturaleza incompatible."],
            )
        )

    withdrawal_start = delivery if regime.product_type == "goods" else purchase
    withdrawal_source = delivery_key if regime.product_type == "goods" else purchase_key
    if withdrawal_start and withdrawal_source and regime.withdrawal_days:
        result.append(
            Deadline(
                label="Periodo ordinario de desistimiento",
                due_at=_utc(withdrawal_start + timedelta(days=regime.withdrawal_days)),
                calculation_status="estimated",
                source_fact_keys=[withdrawal_source],
                notes=["Referencia ordinaria de catorce días naturales; deben revisarse información y excepciones."],
            )
        )
    if withdrawal and withdrawal_key and regime.withdrawal_refund_days:
        result.append(
            Deadline(
                label="Reembolso tras el desistimiento",
                due_at=_utc(withdrawal + timedelta(days=regime.withdrawal_refund_days)),
                calculation_status="estimated",
                source_fact_keys=[withdrawal_key],
                notes=["La retención puede depender de devolución o prueba de envío del bien."],
            )
        )

    if delivery and delivery_key and regime.goods_conformity_years:
        result.append(
            Deadline(
                label="Periodo legal de responsabilidad por falta de conformidad del bien",
                due_at=_utc(_add_years(delivery, regime.goods_conformity_years)),
                calculation_status="estimated",
                source_fact_keys=[delivery_key],
                notes=["Debe revisarse suspensión por reparación, segunda mano y fecha exacta de puesta a disposición."],
            )
        )
    if purchase and purchase_key and regime.digital_conformity_years:
        result.append(
            Deadline(
                label="Periodo de responsabilidad por contenido o servicio digital",
                due_at=_utc(_add_years(purchase, regime.digital_conformity_years)),
                calculation_status="estimated",
                source_fact_keys=[purchase_key],
                notes=["La duración puede depender de suministro único o continuo."],
            )
        )
    if _present(complaint_value) and complaint_key:
        result.append(
            Deadline(
                label="Respuesta a la reclamación previa de comercio electrónico",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[complaint_key],
                notes=["Debe verificarse el plazo aplicable y el calendario competente antes de escalar."],
            )
        )
    result.append(
        Deadline(
            label="Prescripción de acciones contractuales o de consumo",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[purchase_key] if purchase_key else [],
            notes=["Debe determinarse acción, dies a quo, interrupciones y normativa territorial antes de calcular."],
        )
    )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsEcommerceRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("vendedor_online", "Vendedor", ""),
            ("marketplace", "Marketplace", ""),
            ("numero_pedido", "Pedido", ""),
            ("fecha_compra", "Fecha de compra", ""),
            ("pedido_tipo_contrato", "Tipo de contrato", ""),
            ("pedido_producto_descripcion", "Producto", ""),
            ("pedido_servicio_descripcion", "Servicio", ""),
            ("precio_total_pedido_eur", "Total del pedido", " EUR"),
            ("fecha_entrega_pactada", "Entrega pactada", ""),
            ("fecha_entrega_efectiva", "Entrega efectiva", ""),
            ("falta_conformidad_descripcion", "Falta de conformidad", ""),
            ("fecha_comunicacion_desistimiento", "Desistimiento", ""),
            ("importe_reembolso_pedido_eur", "Reembolso", " EUR"),
            ("reclamacion_previa_fecha", "Reclamación previa", ""),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre de comercio electrónico: {regime.product_type}; "
            f"incidencia {regime.incident_type}; régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_ecommerce_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="comercio_electronico",
        specialist="claims.ecommerce",
    )
    regime = _regime(facts_record)
    route = _route_state(facts_record, regime)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    seller, seller_key = validated_value(
        facts_record,
        "vendedor_online",
        "proveedor",
        "emisor_documento",
    )
    marketplace, marketplace_key = validated_value(facts_record, "marketplace")
    order, order_key = validated_value(
        facts_record,
        "numero_pedido",
        "referencia_documento",
        "contrato_ref",
    )
    solution, solution_key = validated_value(facts_record, "solucion_solicitada")
    _, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_ecommerce_tipo",
    )
    summary, summary_keys = _summary(facts_record, regime)
    arguments = []

    def add(
        code: str,
        title: str,
        body: str,
        keys: tuple[Optional[str], ...],
        priority: str = "secondary",
    ) -> None:
        sources = validated_source_keys(facts_record, keys)
        if not sources:
            return
        arguments.append(
            legal_argument(
                facts_record,
                code=code,
                title=title,
                body=body,
                source_fact_keys=sources,
                priority=priority,
                legal_basis=basis,
            )
        )

    add(
        "ecommerce_contract_information_price_and_confirmation",
        "Contrato electrónico, información, precio y confirmación",
        (
            "Debe reconstruirse la oferta aceptada, identidad del empresario, "
            "objeto, precio total, gastos, obligación de pago y confirmación en "
            "soporte duradero. La previa no presume válidas condiciones no aportadas."
        ),
        (
            fact_key,
            order_key,
            "fecha_compra",
            "contrato_a_distancia",
            "vendedor_es_empresario",
            "pedido_tipo_contrato",
            "pedido_producto_descripcion",
            "pedido_servicio_descripcion",
            "publicidad_oferta_descripcion",
            "precio_unidad_eur",
            "gastos_envio_eur",
            "gastos_adicionales_pedido_eur",
            "precio_total_pedido_eur",
            "boton_pedido_indica_obligacion_pago",
            "confirmacion_contrato_soporte_duradero",
            "condiciones_generales_pedido",
        ),
        "primary",
    )
    add(
        "ecommerce_seller_marketplace_and_trader_traceability",
        "Vendedor, marketplace y trazabilidad del comerciante",
        (
            "La responsabilidad contractual debe dirigirse al vendedor salvo que "
            "la plataforma sea parte o haya asumido obligaciones propias. El "
            "marketplace debe conservar la trazabilidad e información exigible sin "
            "ser tratado automáticamente como vendedor."
        ),
        (
            fact_key,
            seller_key,
            marketplace_key,
            "vendedor_domicilio",
            "vendedor_identificador_fiscal",
            "marketplace_es_parte_contractual",
            "marketplace_vendedor_identificado",
            "marketplace_informa_condicion_empresario",
            "marketplace_reparte_obligaciones",
            "marketplace_ofrece_pago",
            "marketplace_ofrece_logistica",
        ),
        "primary",
    )
    add(
        "ecommerce_delivery_risk_carrier_and_proof",
        "Entrega, riesgo, transportista y prueba",
        (
            "Deben compararse plazo pactado, envío, seguimiento, recepción y "
            "destinatario. La entrega al transportista no equivale siempre a entrega "
            "al consumidor y la elección independiente del porte puede alterar el riesgo."
        ),
        (
            fact_key,
            "fecha_entrega_pactada",
            "fecha_envio",
            "transportista_pedido",
            "transportista_elegido_por_consumidor",
            "seguimiento_envio_ref",
            "fecha_entrega_efectiva",
            "fecha_entrega",
            "pedido_entregado",
            "prueba_entrega_aportada",
            "entrega_a_tercero_autorizado",
            "fecha_requerimiento_entrega_adicional",
            "plazo_adicional_entrega_dias",
            "contrato_resuelto_por_no_entrega",
        ),
        "primary",
    )
    add(
        "ecommerce_conformity_and_remedy_hierarchy",
        "Conformidad y jerarquía de medidas correctoras",
        (
            "La falta de conformidad debe vincularse a la oferta y fecha de entrega. "
            "Reparación, sustitución, reducción y resolución no son intercambiables "
            "sin revisar proporcionalidad, gratuidad, plazo e inconvenientes."
        ),
        (
            fact_key,
            "falta_conformidad_descripcion",
            "fecha_manifestacion_falta_conformidad",
            "fecha_comunicacion_falta_conformidad",
            "falta_conformidad_existia_entrega",
            "uso_instalacion_incorrectos_consumidor",
            "reparacion_solicitada",
            "sustitucion_solicitada",
            "reduccion_precio_solicitada",
            "resolucion_contrato_solicitada",
            "remedio_ofrecido_vendedor",
            "fecha_entrega_reparacion",
            "fecha_devolucion_reparacion",
            "reparacion_sin_coste",
            "reparacion_plazo_razonable",
            "inconvenientes_significativos",
            solution_key,
        ),
        "primary",
    )
    add(
        "ecommerce_withdrawal_return_and_refund",
        "Desistimiento, devolución y reembolso",
        (
            "Debe verificarse información previa, fecha de comunicación, excepción "
            "invocada, devolución, costes y reembolso. Una devolución por defecto "
            "no debe confundirse con desistimiento sin causa."
        ),
        (
            fact_key,
            "desistimiento_comunicado",
            "fecha_comunicacion_desistimiento",
            "informacion_desistimiento_entregada",
            "excepcion_desistimiento_invocada",
            "producto_personalizado",
            "producto_precintado_higiene",
            "precinto_abierto_consumidor",
            "fecha_devolucion_producto",
            "devolucion_recibida_vendedor",
            "prueba_devolucion_aportada",
            "coste_devolucion_eur",
            "vendedor_informo_coste_devolucion",
            "importe_reembolso_pedido_eur",
            "fecha_reembolso_pedido",
            "reembolso_mismo_medio_pago",
            "retencion_reembolso_motivo",
            solution_key,
        ),
        "primary",
    )
    add(
        "ecommerce_digital_content_and_subscription",
        "Contenidos digitales y suscripciones",
        (
            "El suministro digital exige separar acceso, conformidad y posible "
            "pérdida del desistimiento. Las suscripciones requieren condiciones, "
            "renovación, aviso y baja documentados."
        ),
        (
            fact_key,
            "contenido_servicio_digital",
            "contenido_digital_ejecucion_iniciada",
            "consentimiento_inicio_digital",
            "conocimiento_perdida_desistimiento",
            "confirmacion_contrato_soporte_duradero",
            "suscripcion_online",
            "periodicidad_suscripcion",
            "renovacion_automatica",
            "fecha_renovacion_suscripcion",
            "aviso_renovacion_suscripcion",
            "baja_suscripcion_solicitada_fecha",
            solution_key,
        ),
    )
    add(
        "ecommerce_product_safety_and_illicit_offer",
        "Seguridad de producto y oferta ilícita",
        (
            "La seguridad, retirada o posible ilicitud debe coordinarse con la "
            "reclamación contractual y la vigilancia del mercado. No se presume "
            "falsificación ni conocimiento de la plataforma sin prueba."
        ),
        (
            fact_key,
            "producto_inseguro",
            "retirada_producto_anunciada",
            "aviso_seguridad_producto",
            "marketplace_conoce_producto_ilegal",
            "marketplace_aviso_producto_ilegal_fecha",
            solution_key,
        ),
    )
    add(
        "ecommerce_prior_claim_and_competent_route",
        "Reclamación previa y vía competente",
        (
            "La reclamación debe conservar destinatario, contenido, fecha, canal y "
            "respuesta. El escalado posterior exige verificar competencia, adhesión "
            "a RAL y disponibilidad real del canal utilizado."
        ),
        (
            fact_key,
            "reclamacion_previa_fecha",
            "canal_reclamacion",
            "reclamacion_ecommerce_ref",
            "respuesta_proveedor",
            "respuesta_documentada",
            "fecha_respuesta_vendedor",
            "fecha_respuesta",
            solution_key,
        ),
    )
    add(
        "ecommerce_quantification_and_no_double_recovery",
        "Cuantificación y ausencia de doble recuperación",
        (
            "La petición debe separar precio, envío, cargos, devolución, reembolso "
            "y cantidades ya recuperadas. No cabe sumar un chargeback íntegro y un "
            "reembolso contractual por el mismo perjuicio, pues supondría una doble "
            "recuperación."
        ),
        (
            fact_key,
            "precio_unidad_eur",
            "gastos_envio_eur",
            "gastos_adicionales_pedido_eur",
            "precio_total_pedido_eur",
            "importe_pagado_eur",
            "importe_reclamado_eur",
            "coste_devolucion_eur",
            "importe_reembolso_pedido_eur",
            "importe_recuperado_medio_pago_eur",
            "disputa_medio_pago_abierta",
            solution_key,
        ),
        "primary",
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa de comercio electrónico.",
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
            *_required_missing(facts_record, regime, route),
            *_review_missing(facts_record, regime, route),
            *fact_review_items(facts_record, prefix="ecommerce"),
        ]
    )

    if route == "marketplace":
        destination = (
            str(marketplace).strip()
            if _present(marketplace)
            else "MARKETPLACE PENDIENTE DE VALIDAR"
        )
        document_type = "RECLAMACIÓN AL MARKETPLACE POR SUS OBLIGACIONES PROPIAS"
    elif route == "authority_review":
        destination = "ORGANISMO DE CONSUMO O ENTIDAD RAL PENDIENTE DE COMPETENCIA"
        document_type = "RECLAMACIÓN DE CONSUMO — COMPETENCIA PENDIENTE DE VALIDAR"
    elif route == "seller_period_review":
        destination = (
            str(seller).strip()
            if _present(seller)
            else "VENDEDOR PENDIENTE DE VALIDAR"
        )
        document_type = "REITERACIÓN AL VENDEDOR Y RESERVA DE ESCALADO"
    else:
        destination = (
            str(seller).strip()
            if _present(seller)
            else "VENDEDOR PENDIENTE DE VALIDAR"
        )
        document_type = "RECLAMACIÓN PREVIA AL VENDEDOR DE COMERCIO ELECTRÓNICO"

    subject_parts = ["RECLAMACIÓN COMERCIO ELECTRÓNICO", regime.incident_type.upper()]
    if _present(order):
        subject_parts.append(f"pedido {_display(order)}")

    strategy = (
        "Fijar vendedor, plataforma y transportista; reconstruir oferta, pedido, "
        "entrega y reclamaciones; aplicar únicamente el remedio correspondiente; "
        "cuantificar importes documentados y coordinar cualquier recuperación bancaria."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="comercio_electronico",
        specialist="claims.ecommerce",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Incidencia de comercio electrónico ({regime.incident_type})"
            + (f" en el pedido {_display(order)}." if _present(order) else ".")
        ),
        client_goal=(
            "Obtener la entrega, puesta en conformidad, desistimiento, resolución "
            "o reembolso que corresponda, sin dirigir la pretensión al sujeto equivocado."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Requerir al marketplace la identidad del vendedor y sus obligaciones propias.",
            "Coordinar la reclamación contractual con el medio de pago sin doble recuperación.",
            "Valorar organismo de consumo, entidad RAL, vigilancia del mercado o vía judicial según materia y competencia.",
        ],
        requested_outcomes=[
            "Identificación completa del vendedor y del rol de la plataforma.",
            "Entrega o explicación trazable de la incidencia logística.",
            "Reparación, sustitución, reducción del precio o resolución cuando proceda.",
            "Aceptación del desistimiento y devolución conforme a derecho cuando proceda.",
            "Reembolso de cantidades documentadas, descontando importes ya recuperados.",
            "Respuesta motivada con contrato, prueba de entrega, condiciones y cálculo económico.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "La plataforma puede no ser el vendedor ni asumir el incumplimiento contractual del tercero.",
                    "La falta de entrega, la no conformidad y el desistimiento tienen presupuestos y remedios distintos.",
                    "Las excepciones de desistimiento requieren hechos concretos y no una etiqueta genérica.",
                    "Los importes recuperados por tarjeta o proveedor de pago deben descontarse.",
                    "La seguridad o ilicitud del producto puede abrir una vía pública adicional a la contractual.",
                    "La antigua plataforma europea ODR ya no debe ofrecerse como vía disponible para asuntos posteriores a su supresión.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Confirmación del pedido y condiciones aplicables.",
            "Identidad, domicilio y condición empresarial del vendedor.",
            "Publicidad, ficha del producto y precio mostrado.",
            "Factura o justificante de pago.",
            "Seguimiento y prueba íntegra de entrega.",
            "Historial de reclamaciones con vendedor, marketplace y transportista.",
            "Prueba de devolución y recepción por el vendedor.",
            "Cálculo del reembolso, retenciones y cantidades ya recuperadas.",
            "Documentación de reparación, sustitución o medida correctora ofrecida.",
            "Aviso de seguridad o retirada cuando exista riesgo del producto.",
        ],
        created_by_component=(
            "claims.ecommerce:"
            f"{CLAIMS_ECOMMERCE_SPECIALIST_VERSION}+"
            f"{CLAIMS_ECOMMERCE_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
