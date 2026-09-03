from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import b2_storage
import billing
import email_utils
import rtm_core.document_extraction_router as document_extraction_router
from rtm_core.ai_security import consume_model_call_budget
from rtm_core.document_extraction_router import (
    RunDocumentExtractionBody,
    run_document_extraction,
)
from rtm_core.runtime_capabilities import CapabilityDisabledError
from submitter_dgt import DGTSubmitter
from submitters.registro import RegistroSubmitter


class ExternalCapabilityGuardsTest(unittest.TestCase):
    def test_b2_guard_runs_before_credentials_and_client_creation(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_B2": "0",
            "B2_ENDPOINT": "https://storage.example.test",
            "B2_KEY_ID": "synthetic-placeholder-id",
            "B2_APPLICATION_KEY": "synthetic-placeholder-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            with patch("b2_storage.boto3.client") as client:
                with self.assertRaises(CapabilityDisabledError):
                    b2_storage.get_s3_client()
        client.assert_not_called()

    def test_email_guard_runs_before_smtp_connection(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_OUTBOUND_EMAIL": "0",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_USER": "synthetic-placeholder-user",
            "SMTP_PASSWORD": "synthetic-placeholder-password",
        }
        with patch.dict(os.environ, environment, clear=False):
            with patch("email_utils.smtplib.SMTP") as smtp:
                with self.assertRaises(CapabilityDisabledError):
                    email_utils.send_email(
                        to_email="nobody@example.com",
                        subject="No debe salir",
                        body="Mensaje sintético.",
                    )
        smtp.assert_not_called()

    def test_dgt_guard_runs_before_signature_or_network(self):
        submitter = DGTSubmitter()
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
        }
        with patch.dict(os.environ, environment, clear=False):
            with patch.object(submitter, "sign_xml") as signer:
                with self.assertRaises(CapabilityDisabledError):
                    submitter.submit("synthetic-case", b"%PDF synthetic")
        signer.assert_not_called()

    def test_registry_guard_runs_before_provider_url_or_network(self):
        submitter = RegistroSubmitter()
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
            "REG_PROVIDER_URL": "https://registry.example.test",
            "REG_PROVIDER_TOKEN": "synthetic-placeholder-token",
        }
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(CapabilityDisabledError):
                submitter.submit(
                    case_id="synthetic-case",
                    pdf_bytes=b"%PDF synthetic",
                )

    def test_checkout_guard_runs_before_database_and_stripe(self):
        request = billing.CheckoutRequest(
            case_id="synthetic-case",
            email="synthetic@example.com",
            payment_stage="review",
        )
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_STRIPE": "0",
            "STRIPE_SECRET_KEY": "synthetic-placeholder-stripe-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            with patch.object(
                billing,
                "require_case_access_token",
                return_value="synthetic-case",
            ):
                with patch("billing.get_engine") as get_engine:
                    with patch("billing.stripe.checkout.Session.create") as create_session:
                        with self.assertRaises(HTTPException) as context:
                            billing.create_checkout(request)
        self.assertEqual(context.exception.status_code, 503)
        get_engine.assert_not_called()
        create_session.assert_not_called()

    def test_legacy_final_checkout_is_retired_before_database_or_stripe(self):
        request = billing.CheckoutRequest(
            case_id="synthetic-case",
            product="vehicle",
            email="synthetic@example.com",
            payment_stage="final",
        )
        with (
            patch.object(
                billing,
                "require_case_access_token",
                return_value="synthetic-case",
            ),
            patch.object(billing, "require_http_capability") as capability,
            patch("billing.get_engine") as get_engine,
            patch("billing.stripe.checkout.Session.create") as create_session,
            self.assertRaises(HTTPException) as context,
        ):
            billing.create_checkout(request)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("presupuesto aprobado", str(context.exception.detail))
        self.assertEqual(
            [call.args[0] for call in capability.call_args_list],
            ["stripe", "final_payments"],
        )
        get_engine.assert_not_called()
        create_session.assert_not_called()

    def test_document_provider_guard_runs_before_database_or_b2(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
            "OPENAI_API_KEY": "synthetic-placeholder-provider-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            with patch(
                "rtm_core.document_extraction_router._operator",
                return_value="ops:test",
            ):
                with patch(
                    "rtm_core.document_extraction_router.get_engine"
                ) as get_engine:
                    with patch(
                        "rtm_core.document_extraction_router.extract_service_documents"
                    ) as extract:
                        with self.assertRaises(HTTPException) as context:
                            run_document_extraction(
                                "synthetic-case",
                                RunDocumentExtractionBody(document_ids=[]),
                                object(),
                                x_operator_token="synthetic-token",
                                x_operator_actor="ops:test",
                            )
        self.assertEqual(context.exception.status_code, 503)
        get_engine.assert_not_called()
        extract.assert_not_called()

    def test_document_extraction_requires_versioned_service_authority(self):
        engine = MagicMock()

        with (
            patch.object(document_extraction_router, "_operator", return_value="ops:test"),
            patch.object(document_extraction_router, "require_http_capability"),
            patch.object(document_extraction_router, "get_engine", return_value=engine),
            patch.object(
                document_extraction_router,
                "prepare_document_extraction",
                return_value=("debt", [object()]),
            ),
            patch.object(
                document_extraction_router,
                "extract_service_documents",
            ) as extract,
            patch.object(document_extraction_router, "normalize_document_packet") as normalize,
            patch.object(document_extraction_router, "persist_document_extraction") as persist,
        ):
            with self.assertRaises(HTTPException) as context:
                run_document_extraction(
                    "synthetic-case",
                    RunDocumentExtractionBody(document_ids=[]),
                    object(),
                    x_operator_token="synthetic-token",
                    x_operator_actor="ops:test",
                )

        self.assertEqual(context.exception.status_code, 503)
        self.assertIn("autorización firmada", str(context.exception.detail).lower())
        engine.begin.assert_not_called()
        normalize.assert_not_called()
        persist.assert_not_called()
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
