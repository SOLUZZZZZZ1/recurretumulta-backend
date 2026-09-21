"""Informational public prices projected from the existing review catalog.

This endpoint has no case or checkout authority. Billing continues resolving
the payable quote from the persisted case and its existing readiness checks.
"""

from fastapi import APIRouter, Response

from rtm_core import service_catalog


router = APIRouter(prefix="/public", tags=["public-pricing"])
PUBLIC_REVIEW_PRICES_VERSION = "rtm_public_review_prices_v1"
_REVIEW_SERVICES = (
    ("traffic", "fine"),
    ("debt", "asnef_equifax"),
    ("administration", "general_administration"),
    ("claims", "consumer"),
)


@router.get("/review-prices")
def public_review_prices(response: Response):
    prices = []
    for service, case_type in _REVIEW_SERVICES:
        department, validated_type = service_catalog.validate_public_intake_classification(
            service, case_type
        )
        quote = service_catalog.resolve_review_quote(department, validated_type)
        prices.append({
            "service": service,
            "amount_cents": quote.amount_cents,
            "currency": quote.currency,
        })
    fine_appeal = service_catalog.resolve_traffic_fine_appeal_offer()
    response.headers["Cache-Control"] = "no-store"
    return {
        "version": PUBLIC_REVIEW_PRICES_VERSION,
        "catalog_version": service_catalog.SERVICE_CATALOG_VERSION,
        "prices": prices,
        "final_services": [{
            "service": fine_appeal.service,
            "amount_cents": fine_appeal.amount_cents,
            "currency": fine_appeal.currency,
            "review_credit_applies": fine_appeal.review_credit_applies,
        }],
    }
