import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rtm_core import public_pricing_router as pricing
from rtm_core import service_catalog


class PublicPricingTest(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(pricing.router)
        self.client = self.enterContext(TestClient(self.app))

    def test_anonymous_exact_projection_without_database_payment_or_cookie(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("database.get_engine", side_effect=AssertionError("No database")) as database, \
             patch("stripe.checkout.Session.create", side_effect=AssertionError("No checkout")) as checkout:
            response = self.client.get("/public/review-prices")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)
        payload = response.json()
        self.assertEqual(set(payload), {"version", "catalog_version", "prices", "final_services"})
        self.assertEqual(payload["version"], "rtm_public_review_prices_v1")
        self.assertEqual(payload["catalog_version"], service_catalog.SERVICE_CATALOG_VERSION)
        pairs = (
            ("traffic", "fine"), ("debt", "asnef_equifax"),
            ("administration", "general_administration"), ("claims", "consumer"),
        )
        self.assertEqual(len(payload["prices"]), len(pairs))
        for price, (service, case_type) in zip(payload["prices"], pairs):
            quote = service_catalog.resolve_review_quote(service, case_type)
            self.assertEqual(price, {
                "service": service, "amount_cents": quote.amount_cents, "currency": quote.currency,
            })
            self.assertIs(type(price["amount_cents"]), int)
            self.assertEqual(price["currency"], "EUR")
        offer = service_catalog.resolve_traffic_fine_appeal_offer()
        self.assertEqual(payload["final_services"], [{
            "service": "traffic_fine_appeal", "amount_cents": offer.amount_cents,
            "currency": "EUR", "review_credit_applies": True,
        }])
        database.assert_not_called()
        checkout.assert_not_called()

    def test_prices_follow_resolver_changes_without_exposing_internal_quote_fields(self):
        resolver = service_catalog.resolve_review_quote
        changed_offer = service_catalog.resolve_traffic_fine_appeal_offer().model_copy(
            update={"amount_cents": 4321}
        )

        def changed_quote(department, case_type):
            return resolver(department, case_type).model_copy(update={"amount_cents": 1234})

        with patch.object(service_catalog, "resolve_review_quote", side_effect=changed_quote) as resolve, \
             patch.object(service_catalog, "resolve_traffic_fine_appeal_offer", return_value=changed_offer) as final_resolve, \
             patch.object(service_catalog, "validate_public_intake_classification", wraps=service_catalog.validate_public_intake_classification) as validate:
            payload = self.client.get("/public/review-prices").json()
        self.assertEqual(resolve.call_count, 4)
        self.assertEqual(validate.call_count, 4)
        final_resolve.assert_called_once_with()
        self.assertEqual(payload["final_services"], [{
            "service": "traffic_fine_appeal", "amount_cents": 4321,
            "currency": "EUR", "review_credit_applies": True,
        }])
        for price in payload["prices"]:
            self.assertEqual(price["amount_cents"], 1234)
            self.assertEqual(set(price), {"service", "amount_cents", "currency"})
        for internal in ("stripe_price_env", "billing_code", "case_id", "token"):
            self.assertNotIn(internal, str(payload))

    def test_read_only_and_cannot_choose_price_using_query_parameters(self):
        baseline = self.client.get("/public/review-prices").json()
        chosen = self.client.get("/public/review-prices?amount_cents=1&service=other&case_type=aeat")
        self.assertEqual(chosen.json(), baseline)
        self.assertEqual(self.client.post("/public/review-prices", json={"amount_cents": 1}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
