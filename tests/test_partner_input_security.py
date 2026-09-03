from __future__ import annotations

import ast
import inspect
import json
import textwrap
import unittest
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI, HTTPException, Response
from pydantic import ValidationError

import partner


class PartnerInputSecurityTest(unittest.TestCase):
    def test_json_inputs_forbid_extras_trim_strings_and_bound_email(self):
        fixtures = (
            (
                partner.PartnerCreateIn,
                {
                    "name": "  Asesoría sintética  ",
                    "email": "  partner@example.com  ",
                    "password": "  temporary-password  ",
                },
                ("name", "Asesoría sintética"),
            ),
            (
                partner.PartnerLoginIn,
                {
                    "email": "  partner@example.com  ",
                    "password": "  current-password  ",
                },
                ("password", "current-password"),
            ),
            (
                partner.PartnerChangePasswordIn,
                {
                    "email": "  partner@example.com  ",
                    "old_password": "  temporary-password  ",
                    "new_password": "  replacement-password  ",
                },
                ("new_password", "replacement-password"),
            ),
            (
                partner.PartnerSignupRequest,
                {
                    "empresa": "  Empresa sintética  ",
                    "contacto": "  Persona sintética  ",
                    "email": "  partner@example.com  ",
                },
                ("empresa", "Empresa sintética"),
            ),
        )

        for model, payload, trimmed in fixtures:
            with self.subTest(model=model.__name__):
                instance = model.model_validate(payload)
                self.assertEqual(getattr(instance, trimmed[0]), trimmed[1])
                self.assertEqual(str(instance.email), "partner@example.com")
                schema = model.model_json_schema()
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["email"]["maxLength"], 254)
                with self.assertRaises(ValidationError):
                    model.model_validate({**payload, "unexpected": "ignored-before"})

    def test_email_limit_is_enforced_by_every_partner_json_model(self):
        oversized_email = f"{'a' * 245}@example.com"
        fixtures = (
            (
                partner.PartnerCreateIn,
                {"name": "Asesoría", "password": "temporary-password"},
            ),
            (partner.PartnerLoginIn, {"password": "current-password"}),
            (
                partner.PartnerChangePasswordIn,
                {
                    "old_password": "temporary-password",
                    "new_password": "replacement-password",
                },
            ),
            (
                partner.PartnerSignupRequest,
                {"empresa": "Empresa", "contacto": "Persona"},
            ),
        )

        for model, payload in fixtures:
            with self.subTest(model=model.__name__), self.assertRaises(
                ValidationError
            ):
                model.model_validate({**payload, "email": oversized_email})

    def test_disabled_case_intake_declares_no_body_contract(self):
        parameters = inspect.signature(partner.create_partner_case).parameters
        self.assertEqual(
            set(parameters),
            {
                "response",
                "authorization",
                "rtm_partner_session",
                "x_csrf_token",
                "rtm_partner_csrf",
            },
        )
        app = FastAPI()
        app.include_router(partner.router)
        route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/partner/cases"
        )
        self.assertIsNone(route.body_field)

    def test_case_intake_fails_closed_before_storage_or_persistence(self):
        engine = MagicMock()
        connection = Mock()
        engine.begin.return_value.__enter__.return_value = connection
        credential = partner.PartnerCredential(token="ps1.1.synthetic", via_cookie=True)
        with (
            patch.object(partner, "_partner_credential", return_value=credential),
            patch.object(partner, "_require_partner_csrf") as csrf,
            patch.object(partner, "get_engine", return_value=engine),
            patch.object(partner, "_get_partner_by_token") as authenticate,
            patch.object(partner, "upload_bytes") as upload,
            self.assertRaises(HTTPException) as raised,
        ):
            partner.create_partner_case(
                Response(),
                authorization=None,
                rtm_partner_session="opaque-session",
                x_csrf_token="csrf",
                rtm_partner_csrf="csrf",
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail["code"],
            "partner_authorization_flow_unavailable",
        )
        self.assertEqual(
            raised.exception.headers["Cache-Control"],
            "no-store, max-age=0",
        )
        self.assertEqual(raised.exception.headers["Vary"], "Authorization, Cookie")
        csrf.assert_called_once()
        authenticate.assert_called_once_with(connection, credential.token)
        upload.assert_not_called()

    def test_signup_transport_failures_share_one_opaque_response(self):
        payload = partner.PartnerSignupRequest(
            empresa="Empresa sintética",
            contacto="Persona sintética",
            email="partner@example.com",
        )
        failures = (False, RuntimeError("smtp://user:secret@mail.internal"))
        observed = []

        for failure in failures:
            kwargs = (
                {"side_effect": failure}
                if isinstance(failure, Exception)
                else {"return_value": failure}
            )
            with self.subTest(failure=type(failure).__name__), patch.object(
                partner, "require_http_capability"
            ), patch.object(partner, "send_email", **kwargs), self.assertRaises(
                HTTPException
            ) as raised:
                partner.partner_signup(payload)
            observed.append((raised.exception.status_code, raised.exception.detail))

        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[0][0], 500)
        detail = str(observed[0][1]).casefold()
        for fragment in ("smtp", "config", "secret", "mail.internal"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, detail)

    def test_partner_events_reject_secret_and_storage_fields_recursively(self):
        unsafe_payloads = (
            {"api_token": "secret"},
            {"nested": {"Authorization": "Bearer secret"}},
            {"items": [{"b2-key": "internal-object-key"}]},
            {"csrfToken": "secret"},
        )

        for payload in unsafe_payloads:
            connection = Mock()
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                partner._event(connection, "case-id", "event-type", payload)
            connection.execute.assert_not_called()

        connection = Mock()
        safe_payload = {
            "partner_id": "partner-id",
            "filename": "document.pdf",
            "count": 1,
        }
        partner._event(connection, "case-id", "event-type", safe_payload)
        serialized = connection.execute.call_args.args[1]["payload"]
        self.assertEqual(json.loads(serialized), safe_payload)

    def test_case_events_and_success_response_have_no_secret_coordinates(self):
        source = "\n\n".join(
            textwrap.dedent(inspect.getsource(function))
            for function in (
                partner.create_partner_case,
                partner._persist_partner_case,
            )
        )
        functions = ast.parse(source).body
        forbidden = {
            "access_token",
            "api_token",
            "authorization",
            "b2_bucket",
            "b2_key",
            "bucket",
            "cookie",
            "csrf_token",
            "key",
            "password",
            "secret",
            "token",
        }

        exposed_keys = set()
        for function in functions:
            for node in ast.walk(function):
                is_event = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_event"
                )
                is_uploaded_item = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "uploaded"
                    and node.func.attr == "append"
                )
                is_response = isinstance(node, ast.Return)
                if not (is_event or is_uploaded_item or is_response):
                    continue
                for nested in ast.walk(node):
                    if isinstance(nested, ast.Dict):
                        exposed_keys.update(
                            key.value
                            for key in nested.keys
                            if isinstance(key, ast.Constant)
                            and isinstance(key.value, str)
                        )

        self.assertTrue(
            {"case_id", "uploaded", "authorization_evidence", "candidate_document"}
            <= exposed_keys
        )
        self.assertNotIn("authorization_signed", exposed_keys)
        self.assertFalse(forbidden & exposed_keys)


if __name__ == "__main__":
    unittest.main()
