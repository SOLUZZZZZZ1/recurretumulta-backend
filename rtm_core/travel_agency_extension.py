"""Registro modular de hechos y capacidad para ``travel.agency``.

La extensión se instala una sola vez al cargar ``rtm_core``. Añade campos
documentales tipados de agencia/plataforma, declara la capacidad profunda de la
familia y registra sus versiones en la observabilidad sin permitir que estos
campos decidan por sí solos estrategia o redacción.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


TRAVEL_AGENCY_EXTENSION_VERSION = "rtm_travel_agency_extension_v1_0"

_INSTALLED = False


def _spec(
    key: str,
    *,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("travel",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=min_confidence,
        merge_mode=merge_mode,
        max_length=max_length,
    )


_AGENCY_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "rol_agencia_plataforma",
        aliases=(
            "platform_role",
            "agency_role",
            "intermediary_role",
            "marketplace_role",
        ),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "pais_agencia_plataforma",
        aliases=(
            "platform_country",
            "agency_country",
            "intermediary_country",
            "marketplace_country",
        ),
        min_confidence=0.96,
        max_length=160,
    ),
    _spec(
        "parte_contratante_reserva",
        aliases=(
            "booking_contracting_party",
            "contracting_party",
            "seller_of_record",
        ),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "proveedor_subyacente",
        aliases=(
            "underlying_supplier",
            "travel_service_provider",
            "service_supplier",
        ),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "cobrador_reserva",
        aliases=(
            "payment_collector",
            "charged_by",
            "merchant_of_record",
        ),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "emisor_factura_reserva",
        aliases=(
            "booking_invoice_issuer",
            "invoice_issuer",
            "receipt_issuer",
        ),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "reserva_transmitida_proveedor",
        value_type="boolean",
        aliases=(
            "booking_forwarded_to_supplier",
            "booking_transmitted_to_provider",
        ),
        min_confidence=0.97,
    ),
    _spec(
        "reserva_confirmada_proveedor",
        value_type="boolean",
        aliases=(
            "supplier_booking_confirmed",
            "provider_confirmation_received",
        ),
        min_confidence=0.97,
    ),
    _spec(
        "identidad_proveedor_informada",
        value_type="boolean",
        aliases=(
            "supplier_identity_disclosed",
            "seller_identity_disclosed",
        ),
        min_confidence=0.97,
    ),
    _spec(
        "reparto_responsabilidad_informado",
        value_type="boolean",
        aliases=(
            "responsibility_allocation_disclosed",
            "obligations_split_disclosed",
        ),
        min_confidence=0.97,
    ),
    _spec(
        "condiciones_intermediacion",
        aliases=(
            "intermediary_terms",
            "platform_terms",
            "marketplace_terms",
        ),
        merge_mode="set",
        max_length=1400,
    ),
    _spec(
        "precio_mostrado_eur",
        value_type="money",
        aliases=("displayed_price", "advertised_total_price"),
        min_confidence=0.97,
    ),
    _spec(
        "cargo_total_reserva_eur",
        value_type="money",
        aliases=("charged_total", "booking_charge_total"),
        min_confidence=0.97,
    ),
    _spec(
        "comision_servicio_eur",
        value_type="money",
        aliases=("service_fee", "platform_fee", "agency_fee"),
        min_confidence=0.97,
    ),
    _spec(
        "cargo_duplicado_eur",
        value_type="money",
        aliases=("duplicate_charge", "duplicated_charge"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_solicitud_reembolso",
        value_type="date",
        aliases=("refund_request_date", "refund_requested_on"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_reembolso_prometido",
        value_type="date",
        aliases=("promised_refund_date", "refund_due_date"),
        min_confidence=0.97,
    ),
    _spec(
        "estado_pago_proveedor",
        aliases=("supplier_payment_status", "provider_paid_status"),
        min_confidence=0.96,
        max_length=300,
    ),
    _spec(
        "mercado_en_linea",
        value_type="boolean",
        aliases=("online_marketplace", "is_online_marketplace"),
        min_confidence=0.97,
    ),
    _spec(
        "vendedor_es_empresario",
        value_type="boolean",
        aliases=("seller_is_trader", "third_party_is_trader"),
        min_confidence=0.97,
    ),
    _spec(
        "cancelacion_por_plataforma",
        value_type="boolean",
        aliases=("platform_cancelled_booking", "agency_cancelled_booking"),
        min_confidence=0.97,
    ),
    _spec(
        "modificacion_por_plataforma",
        aliases=("platform_booking_change", "agency_booking_change"),
        min_confidence=0.96,
        merge_mode="set",
        max_length=1000,
    ),
    _spec(
        "error_reserva_plataforma",
        aliases=("booking_error", "platform_booking_error"),
        min_confidence=0.96,
        merge_mode="set",
        max_length=1200,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["travel"].values()}
    new_specs = tuple(spec for spec in _AGENCY_FACTS if spec.key not in registered)
    if not new_specs:
        return

    index = catalog._BY_SERVICE["travel"]
    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en travel: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec

    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("travel", "agencia_plataforma")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia travel.agencia_plataforma")

    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    declared = {
        "travel_agency_extension": TRAVEL_AGENCY_EXTENSION_VERSION,
        "travel_agency_regime": "rtm_travel_agency_regime_v1_0",
        "travel_agency_specialist": "rtm_travel_agency_specialist_v1_0",
        "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
    }
    lookups = {
        "travel_agency_extension": (
            "rtm_core.travel_agency_extension",
            "TRAVEL_AGENCY_EXTENSION_VERSION",
        ),
        "travel_agency_regime": (
            "rtm_core.travel_agency_regime",
            "TRAVEL_AGENCY_REGIME_VERSION",
        ),
        "travel_agency_specialist": (
            "rtm_core.travel_agency_specialist",
            "TRAVEL_AGENCY_SPECIALIST_VERSION",
        ),
        "travel_specialist_registry": (
            "rtm_core.travel_specialist_registry",
            "TRAVEL_SPECIALIST_REGISTRY_VERSION",
        ),
    }
    versioning.DECLARED_COMPONENT_VERSIONS.update(declared)
    versioning._RUNTIME_LOOKUPS.update(lookups)


def install_travel_agency_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
