from __future__ import annotations

import ast
import hashlib
import inspect
import unittest

import billing
import cases
import vehicle_removal_router as vehicle
from rtm_core import vehicle_removal_contract as contract


CASE_ID = "123e4567-e89b-12d3-a456-426614174000"


class VehicleRemovalAuthoritativeContractTest(unittest.TestCase):
    def test_intake_does_not_treat_legacy_checkbox_as_legal_authority(self):
        source = inspect.getsource(cases.create_rtm_intake_draft)
        self.assertIn("del representation_confirmed", source)
        self.assertNotIn("not representation_confirmed or", source)
        self.assertIn("if not privacy_accepted", source)

    def test_checkout_and_webhook_import_one_authoritative_contract(self):
        self.assertEqual(
            vehicle._safe_stripe_metadata(CASE_ID),
            contract.build_vehicle_removal_stripe_metadata(CASE_ID),
        )
        self.assertEqual(
            billing._VEHICLE_REMOVAL_AMOUNT_CENTS,
            contract.VEHICLE_REMOVAL_AMOUNT_CENTS,
        )
        self.assertEqual(
            billing._VEHICLE_REMOVAL_CURRENCY,
            contract.VEHICLE_REMOVAL_CURRENCY,
        )
        self.assertEqual(
            billing._VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
            contract.VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
        )
        self.assertIs(
            billing._VEHICLE_REMOVAL_METADATA_KEYS,
            contract.VEHICLE_REMOVAL_METADATA_KEYS,
        )
        self.assertIs(
            billing._VEHICLE_REMOVAL_INTENT_KEYS,
            contract.VEHICLE_REMOVAL_INTENT_KEYS,
        )

    def test_preparation_consent_is_immutable_and_not_legal_authority(self):
        digest = hashlib.sha256(
            contract.VEHICLE_REMOVAL_PREPARATION_CONSENT_TEXT.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            digest,
            contract.VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256,
        )
        self.assertTrue(
            contract.vehicle_removal_preparation_consent_is_exact(
                contract.VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION,
                digest,
            )
        )
        self.assertFalse(
            contract.vehicle_removal_preparation_consent_is_exact(
                contract.VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION + "-ai-edited",
                digest,
            )
        )
        self.assertFalse(
            contract.vehicle_removal_preparation_consent_is_exact(
                contract.VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION,
                "0" * 64,
            )
        )
        marker = contract.build_vehicle_removal_preparation_consent()
        self.assertTrue(marker["accepted"])
        self.assertTrue(marker["human_review_required"])
        self.assertFalse(marker["legal_representation"])
        # Los alias se conservan solo para el payload wire-v3 ya publicado.
        self.assertTrue(
            contract.vehicle_removal_authorization_is_exact(
                contract.VEHICLE_REMOVAL_AUTHORIZATION_VERSION,
                contract.VEHICLE_REMOVAL_AUTHORIZATION_SHA256,
            )
        )

    def test_contract_module_remains_pure_and_quote_has_exact_public_allowlist(self):
        source = inspect.getsource(contract)
        tree = ast.parse(source)
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertFalse(
            imported_roots
            & {"fastapi", "sqlalchemy", "stripe", "database", "openai"}
        )

        quote = contract.build_vehicle_removal_quote(CASE_ID)
        self.assertEqual(
            set(quote),
            {
                "ok",
                "case_id",
                "service_code",
                "amount_cents",
                "currency",
                "quote_version",
                "authorization_version",
                "authorization_text",
                "authorization_sha256",
            },
        )
        self.assertEqual(quote["amount_cents"], 3900)
        self.assertEqual(quote["currency"], "EUR")
        rendered = repr(quote).casefold()
        for forbidden in ("price_id", "email", "dni", "phone", "plate"):
            self.assertNotIn(forbidden, rendered)

    def test_payment_return_urls_use_server_visible_paths_without_tokens(self):
        for module in (billing, vehicle):
            source = inspect.getsource(module)
            self.assertNotIn("/#/", source)
            self.assertNotIn("token=", source.casefold())

        billing_source = inspect.getsource(billing)
        vehicle_source = inspect.getsource(vehicle)
        self.assertIn("/pago-ok?case=", billing_source)
        self.assertIn("/resumen?case=", billing_source)
        self.assertIn("/eliminar-coche?case=", vehicle_source)


if __name__ == "__main__":
    unittest.main()
