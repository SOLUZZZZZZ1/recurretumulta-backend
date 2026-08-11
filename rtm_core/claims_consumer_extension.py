"""Registro modular de hechos y capacidad para ``claims.consumer``.

La familia de consumo general es deliberadamente residual. Añade hechos
 documentales tipados para compras presenciales y servicios ordinarios, pero no
absorbe telecomunicaciones, energía, banca, seguros, comercio electrónico,
viajes ni servicios profesionales. La instalación no decide el fondo jurídico:
solo amplía catálogos, capacidad, registro y observabilidad.
"""

from __future__ import annotations

from dataclasses import is_dataclass, replace as dataclass_replace
from typing import Any

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


CLAIMS_CONSUMER_EXTENSION_VERSION = "rtm_claims_consumer_extension_v1_0"
CLAIMS_CONSUMER_REGIME_VERSION = "rtm_claims_consumer_regime_v1_0"
CLAIMS_CONSUMER_SPECIALIST_VERSION = "rtm_claims_consumer_specialist_v1_0"

_INSTALLED = False


def _spec(
    key: str,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
    allow_negative: bool = False,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("claims",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_CONSUMER_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "incidencia_consumo_tipo",
        aliases=("general_consumer_incident_type", "consumer_claim_type"),
        max_length=300,
    ),
    _spec(
        "consumidor_es_consumidor",
        "boolean",
        ("general_customer_is_consumer", "consumer_status_documented"),
        0.98,
    ),
    _spec(
        "pais_consumidor_general",
        aliases=("general_consumer_country",),
        max_length=160,
    ),
    _spec(
        "empresario_consumo",
        aliases=("general_consumer_trader", "general_seller_or_provider"),
        confidence=0.97,
        max_length=280,
    ),
    _spec(
        "pais_empresario_consumo",
        aliases=("general_consumer_trader_country",),
        max_length=160,
    ),
    _spec(
        "empresario_consumo_es_empresario",
        "boolean",
        ("general_supplier_is_trader",),
        0.98,
    ),
    _spec(
        "establecimiento_consumo",
        aliases=("general_retail_establishment", "physical_store_name"),
        confidence=0.97,
        max_length=280,
    ),
    _spec(
        "direccion_establecimiento_consumo",
        aliases=("general_retail_establishment_address",),
        max_length=500,
    ),
    _spec(
        "contrato_consumo_ref",
        "identifier",
        ("general_consumer_contract_reference",),
        0.97,
        max_length=180,
    ),
    _spec(
        "factura_consumo_ref",
        "identifier",
        ("general_consumer_invoice_reference", "general_consumer_receipt_reference"),
        0.98,
        max_length=180,
    ),
    _spec(
        "fecha_contrato_consumo",
        "date",
        ("general_consumer_contract_date",),
        0.98,
    ),
    _spec(
        "fecha_compra_consumo",
        "date",
        ("general_consumer_purchase_date",),
        0.98,
    ),
    _spec(
        "fecha_entrega_consumo",
        "date",
        ("general_consumer_delivery_date",),
        0.97,
    ),
    _spec(
        "fecha_incidencia_consumo",
        "date",
        ("general_consumer_issue_date",),
        0.97,
    ),
    _spec(
        "modalidad_contratacion_consumo",
        aliases=("general_consumer_contract_channel", "purchase_channel"),
        confidence=0.97,
        max_length=220,
    ),
    _spec(
        "compra_online_consumo",
        "boolean",
        ("general_purchase_was_online", "consumer_online_purchase"),
        0.98,
    ),
    _spec(
        "objeto_consumo_tipo",
        aliases=("general_consumer_object_type", "goods_or_service_type"),
        confidence=0.97,
        max_length=220,
    ),
    _spec(
        "producto_consumo_descripcion",
        aliases=("general_consumer_product_description",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "servicio_consumo_descripcion",
        aliases=("general_consumer_service_description",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "condiciones_contrato_consumo",
        aliases=("general_consumer_contract_terms",),
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "publicidad_oferta_consumo",
        aliases=("general_consumer_advertising_or_offer",),
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "precio_total_consumo_eur",
        "money",
        ("general_consumer_total_price_eur",),
        0.98,
    ),
    _spec(
        "importe_pagado_consumo_eur",
        "money",
        ("general_consumer_amount_paid_eur",),
        0.98,
    ),
    _spec(
        "importe_reclamado_consumo_eur",
        "money",
        ("general_consumer_amount_claimed_eur",),
        0.98,
    ),
    _spec(
        "precio_anunciado_consumo_eur",
        "money",
        ("general_consumer_advertised_price_eur",),
        0.98,
    ),
    _spec(
        "precio_cobrado_consumo_eur",
        "money",
        ("general_consumer_charged_price_eur",),
        0.98,
    ),
    _spec(
        "cargo_adicional_no_informado_consumo_eur",
        "money",
        ("general_consumer_undisclosed_surcharge_eur",),
        0.98,
    ),
    _spec(
        "producto_entregado_consumo",
        "boolean",
        ("general_consumer_product_delivered",),
        0.98,
    ),
    _spec(
        "falta_conformidad_consumo",
        "boolean",
        ("general_consumer_nonconformity_present",),
        0.98,
    ),
    _spec(
        "falta_conformidad_descripcion_consumo",
        aliases=("general_consumer_nonconformity_description",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "fecha_manifestacion_falta_conformidad_consumo",
        "date",
        ("general_consumer_nonconformity_manifestation_date",),
        0.98,
    ),
    _spec(
        "servicio_no_prestado_consumo",
        "boolean",
        ("general_consumer_service_not_performed",),
        0.98,
    ),
    _spec(
        "servicio_incompleto_consumo",
        "boolean",
        ("general_consumer_service_incomplete",),
        0.98,
    ),
    _spec(
        "servicio_defectuoso_consumo",
        "boolean",
        ("general_consumer_service_defective",),
        0.98,
    ),
    _spec(
        "incumplimiento_consumo_descripcion",
        aliases=("general_consumer_breach_description",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "clausula_discutida_consumo",
        aliases=("general_consumer_disputed_term",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "garantia_comercial_consumo",
        aliases=("general_consumer_commercial_guarantee",),
        merge_mode="set",
        max_length=1400,
    ),
    _spec(
        "reparacion_solicitada_consumo",
        "boolean",
        ("general_consumer_repair_requested",),
        0.98,
    ),
    _spec(
        "sustitucion_solicitada_consumo",
        "boolean",
        ("general_consumer_replacement_requested",),
        0.98,
    ),
    _spec(
        "reduccion_precio_solicitada_consumo",
        "boolean",
        ("general_consumer_price_reduction_requested",),
        0.98,
    ),
    _spec(
        "resolucion_contrato_solicitada_consumo",
        "boolean",
        ("general_consumer_contract_termination_requested",),
        0.98,
    ),
    _spec(
        "reembolso_solicitado_consumo_eur",
        "money",
        ("general_consumer_refund_requested_eur",),
        0.98,
    ),
    _spec(
        "reembolso_recibido_consumo_eur",
        "money",
        ("general_consumer_refund_received_eur",),
        0.98,
    ),
    _spec(
        "fecha_reembolso_consumo",
        "date",
        ("general_consumer_refund_date",),
        0.97,
    ),
    _spec(
        "fecha_reclamacion_previa_consumo",
        "date",
        ("general_consumer_prior_complaint_date",),
        0.98,
    ),
    _spec(
        "referencia_reclamacion_consumo",
        "identifier",
        ("general_consumer_complaint_reference",),
        0.98,
        max_length=180,
    ),
    _spec(
        "canal_reclamacion_consumo",
        aliases=("general_consumer_complaint_channel",),
        max_length=220,
    ),
    _spec(
        "respuesta_empresario_consumo",
        aliases=("general_consumer_trader_response",),
        confidence=0.97,
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "fecha_respuesta_empresario_consumo",
        "date",
        ("general_consumer_trader_response_date",),
        0.97,
    ),
    _spec(
        "hoja_reclamaciones_solicitada",
        "boolean",
        ("general_consumer_complaint_form_requested",),
        0.97,
    ),
    _spec(
        "hoja_reclamaciones_entregada",
        "boolean",
        ("general_consumer_complaint_form_provided",),
        0.97,
    ),
    _spec(
        "adhesion_arbitraje_consumo",
        "boolean",
        ("general_consumer_arbitration_adherence",),
        0.97,
    ),
    _spec(
        "adr_consumo_solicitada",
        "boolean",
        ("general_consumer_adr_requested",),
        0.97,
    ),
    _spec(
        "producto_inseguro_consumo",
        "boolean",
        ("general_consumer_unsafe_product",),
        0.98,
    ),
    _spec(
        "servicio_regulado_indicio",
        aliases=("general_consumer_regulated_service_hint",),
        merge_mode="set",
        max_length=1000,
    ),
    _spec(
        "importe_recuperado_tercero_consumo_eur",
        "money",
        ("general_consumer_amount_recovered_elsewhere_eur",),
        0.98,
    ),
    _spec(
        "solucion_solicitada_consumo",
        aliases=("general_consumer_requested_solution",),
        confidence=0.97,
        merge_mode="set",
        max_length=1200,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    index = catalog._BY_SERVICE["claims"]
    known_keys = {spec.key for spec in index.values()}
    new_specs = tuple(spec for spec in _CONSUMER_FACTS if spec.key not in known_keys)
    if not new_specs:
        return

    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en claims: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec
    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("claims", "consumo")
    current = catalog._FAMILY_PROFILES.get(key)
    if current is None:
        raise RuntimeError("La familia claims.consumo no está registrada")
    if current.capability == "specialist_ready":
        return

    ready = current.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_specialist() -> None:
    import rtm_core.claims_specialist_registry as registry
    from rtm_core.claims_consumer_specialist import build_claims_consumer_preview

    specialist = "claims.consumer"
    installed = False

    for name in (
        "register_claims_specialist",
        "register_specialist",
        "register",
    ):
        register = getattr(registry, name, None)
        if not callable(register):
            continue
        attempts = (
            lambda: register(specialist, build_claims_consumer_preview),
            lambda: register(
                specialist=specialist,
                builder=build_claims_consumer_preview,
            ),
            lambda: register(
                name=specialist,
                builder=build_claims_consumer_preview,
            ),
        )
        for attempt in attempts:
            try:
                attempt()
                installed = True
                break
            except TypeError:
                continue
        if installed:
            break

    for value in vars(registry).values():
        if not isinstance(value, dict):
            continue
        if "claims.professional_services" in value or specialist in value:
            value[specialist] = build_claims_consumer_preview
            installed = True

    if not installed:
        raise RuntimeError("No se pudo registrar claims.consumer")


def _rewrite_version_value(value: Any, donor: str, target: str, version: str) -> Any:
    if isinstance(value, str):
        rewritten = value.replace(donor, target)
        if rewritten.startswith("rtm_claims_professional_services_"):
            suffix = rewritten.removeprefix("rtm_claims_professional_services_")
            rewritten = f"rtm_claims_consumer_{suffix}"
        if value.startswith("rtm_claims_professional_services_"):
            rewritten = version
        return rewritten
    if callable(value):
        return lambda _version=version: _version
    if isinstance(value, tuple):
        return tuple(
            _rewrite_version_value(item, donor, target, version) for item in value
        )
    if isinstance(value, list):
        return [
            _rewrite_version_value(item, donor, target, version) for item in value
        ]
    if isinstance(value, dict):
        return {
            _rewrite_version_value(key, donor, target, version):
            _rewrite_version_value(item, donor, target, version)
            for key, item in value.items()
        }
    if hasattr(value, "model_copy") and hasattr(value, "model_dump"):
        payload = _rewrite_version_value(
            value.model_dump(mode="python"), donor, target, version
        )
        try:
            return value.__class__.model_validate(payload)
        except Exception:
            return value.model_copy(update=payload)
    if is_dataclass(value):
        updates = {
            field: _rewrite_version_value(
                getattr(value, field), donor, target, version
            )
            for field in getattr(value, "__dataclass_fields__", {})
        }
        try:
            return dataclass_replace(value, **updates)
        except Exception:
            return value
    return value


def _install_versions() -> None:
    import rtm_core.versioning as versioning

    components = {
        "claims_consumer_extension": CLAIMS_CONSUMER_EXTENSION_VERSION,
        "claims_consumer_regime": CLAIMS_CONSUMER_REGIME_VERSION,
        "claims_consumer_specialist": CLAIMS_CONSUMER_SPECIALIST_VERSION,
    }
    donor_prefix = "claims_professional_services"
    target_prefix = "claims_consumer"

    for attribute, container in list(vars(versioning).items()):
        if isinstance(container, dict):
            for target, version in components.items():
                if target in container:
                    continue
                donor = target.replace(target_prefix, donor_prefix)
                if donor not in container:
                    continue
                container[target] = _rewrite_version_value(
                    container[donor], donor_prefix, target_prefix, version
                )
        elif isinstance(container, list):
            additions: list[Any] = []
            rendered = repr(container)
            if donor_prefix not in rendered:
                continue
            for target, version in components.items():
                donor = target.replace(target_prefix, donor_prefix)
                for item in container:
                    if donor not in repr(item):
                        continue
                    candidate = _rewrite_version_value(
                        item, donor_prefix, target_prefix, version
                    )
                    if candidate not in container and candidate not in additions:
                        additions.append(candidate)
            container.extend(additions)
        elif isinstance(container, tuple) and donor_prefix in repr(container):
            additions = []
            for target, version in components.items():
                donor = target.replace(target_prefix, donor_prefix)
                for item in container:
                    if donor not in repr(item):
                        continue
                    candidate = _rewrite_version_value(
                        item, donor_prefix, target_prefix, version
                    )
                    if candidate not in container and candidate not in additions:
                        additions.append(candidate)
            if additions:
                setattr(versioning, attribute, (*container, *additions))


def install_claims_consumer_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_specialist()
    _install_versions()
    _INSTALLED = True
