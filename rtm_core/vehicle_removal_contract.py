"""Contrato puro y autoritativo para cotización y checkout de retirada."""

from __future__ import annotations

import hashlib
from typing import Any


VEHICLE_REMOVAL_AMOUNT_CENTS = 3_900
VEHICLE_REMOVAL_CURRENCY = "EUR"
VEHICLE_REMOVAL_SERVICE_CODE = "vehicle_removal"
VEHICLE_REMOVAL_PRODUCT_CODE = "ELIMINAR_COCHE"
VEHICLE_REMOVAL_QUOTE_VERSION = "rtm_vehicle_removal_quote_v1"
VEHICLE_REMOVAL_CHECKOUT_CONTRACT = "rtm_vehicle_removal_v3"
VEHICLE_REMOVAL_REQUEST_CONTRACT = "rtm_vehicle_removal_request_v3"
VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION = "rtm-core-vehicle-removal-v3"
VEHICLE_REMOVAL_PREPARATION_CONSENT_TEXT = (
    "Solicito expresamente a RTM que prepare la gestión administrativa de baja o "
    "retirada de este vehículo para el expediente indicado. La solicitud seguirá "
    "sujeta a revisión humana y no ejecuta por sí sola la baja, retirada ni "
    "transmisión del vehículo."
)
VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256 = (
    "b8c54b902450421ba7b4754e50f79ffc6bb83aaf77de480989fe350adfaf621d"
)
# Alias de transporte conservados temporalmente porque el cliente v3 ya usa
# estos nombres. No representan mandato, firma ni autorización legal genérica.
VEHICLE_REMOVAL_AUTHORIZATION_VERSION = VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION
VEHICLE_REMOVAL_AUTHORIZATION_TEXT = VEHICLE_REMOVAL_PREPARATION_CONSENT_TEXT
VEHICLE_REMOVAL_AUTHORIZATION_SHA256 = VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256
VEHICLE_REMOVAL_METADATA_KEYS = frozenset(
    {
        "amount_cents",
        "case_id",
        "checkout_contract",
        "currency",
        "product_code",
        "quote_version",
        "service_code",
    }
)
VEHICLE_REMOVAL_INTENT_KEYS = frozenset(
    {
        "amount_total",
        "checkout_contract",
        "currency",
        "product_code",
        "quote_version",
        "service_code",
        "session_id",
    }
)


def _authorization_digest() -> str:
    return hashlib.sha256(
        VEHICLE_REMOVAL_PREPARATION_CONSENT_TEXT.encode("utf-8")
    ).hexdigest()


if _authorization_digest() != VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256:
    raise RuntimeError("Contrato canónico de consentimiento de vehículo inconsistente")


def vehicle_removal_preparation_consent_is_exact(
    version: Any,
    digest: Any,
) -> bool:
    return (
        str(version or "") == VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION
        and str(digest or "") == VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256
    )


def vehicle_removal_authorization_is_exact(version: Any, digest: Any) -> bool:
    """Alias wire-v3; valida consentimiento limitado, no representación."""

    return vehicle_removal_preparation_consent_is_exact(version, digest)


def build_vehicle_removal_preparation_consent() -> dict[str, Any]:
    return {
        "accepted": True,
        "version": VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION,
        "sha256": VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256,
        "human_review_required": True,
        "legal_representation": False,
    }


def build_vehicle_removal_stripe_metadata(case_id: str) -> dict[str, str]:
    metadata = {
        "amount_cents": str(VEHICLE_REMOVAL_AMOUNT_CENTS),
        "case_id": str(case_id),
        "checkout_contract": VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
        "currency": VEHICLE_REMOVAL_CURRENCY,
        "product_code": VEHICLE_REMOVAL_PRODUCT_CODE,
        "quote_version": VEHICLE_REMOVAL_QUOTE_VERSION,
        "service_code": VEHICLE_REMOVAL_SERVICE_CODE,
    }
    if set(metadata) != VEHICLE_REMOVAL_METADATA_KEYS:
        raise RuntimeError("Contrato interno de metadata no válido")
    return metadata


def is_exact_vehicle_removal_stripe_metadata(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    case_id = value.get("case_id")
    return bool(case_id) and value == build_vehicle_removal_stripe_metadata(
        str(case_id)
    )


def build_vehicle_removal_quote(case_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "case_id": str(case_id),
        "service_code": VEHICLE_REMOVAL_SERVICE_CODE,
        "amount_cents": VEHICLE_REMOVAL_AMOUNT_CENTS,
        "currency": VEHICLE_REMOVAL_CURRENCY,
        "quote_version": VEHICLE_REMOVAL_QUOTE_VERSION,
        "authorization_version": VEHICLE_REMOVAL_AUTHORIZATION_VERSION,
        "authorization_text": VEHICLE_REMOVAL_AUTHORIZATION_TEXT,
        "authorization_sha256": VEHICLE_REMOVAL_AUTHORIZATION_SHA256,
    }
