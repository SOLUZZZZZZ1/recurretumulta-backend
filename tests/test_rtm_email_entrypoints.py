from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import cases
import contact_backend_fastapi
import partner


class EmailEntryPointsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "RTM_ENV": "production",
            "RTM_ENABLE_OUTBOUND_EMAIL": "1",
            "CONTACT_TO": "info@recurretumulta.eu",
        }

    def test_contact_uses_central_transport_and_reply_to(self):
        payload = contact_backend_fastapi.ContactRequest(
            tipo_consulta="Expediente",
            nombre="Persona sintética",
            email="synthetic@example.com",
            mensaje="Consulta completamente sintética.",
        )
        with patch.dict(os.environ, self.environment, clear=False):
            with patch(
                "contact_backend_fastapi.send_email", return_value=True
            ) as send:
                result = contact_backend_fastapi.send_contact_email(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(send.call_args.kwargs["to_email"], "info@recurretumulta.eu")
        self.assertEqual(send.call_args.kwargs["reply_to"], "synthetic@example.com")

    def test_contact_reports_missing_transport_without_leaking_secrets(self):
        payload = contact_backend_fastapi.ContactRequest(
            tipo_consulta="Expediente",
            nombre="Persona sintética",
            email="synthetic@example.com",
            mensaje="Consulta completamente sintética.",
        )
        with patch.dict(os.environ, self.environment, clear=False):
            with patch("contact_backend_fastapi.send_email", return_value=False):
                with self.assertRaises(HTTPException) as context:
                    contact_backend_fastapi.send_contact_email(payload)

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(
            context.exception.detail,
            "Falta configuración SMTP en el servidor.",
        )

    def test_partner_signup_uses_info_mailbox_and_central_transport(self):
        payload = partner.PartnerSignupRequest(
            empresa="Empresa sintética",
            contacto="Persona sintética",
            email="synthetic@example.com",
        )
        with patch.dict(os.environ, self.environment, clear=False):
            with patch("partner.send_email", return_value=True) as send:
                result = partner.partner_signup(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(send.call_args.kwargs["to_email"], "info@recurretumulta.eu")
        self.assertEqual(send.call_args.kwargs["reply_to"], "synthetic@example.com")

    def test_case_notification_uses_central_transport(self):
        with patch("cases.send_email", return_value=True) as send:
            cases._send_email(
                "synthetic@example.com",
                "Aviso sintético",
                "Contenido sintético.",
            )

        send.assert_called_once_with(
            to_email="synthetic@example.com",
            subject="Aviso sintético",
            body="Contenido sintético.",
        )


if __name__ == "__main__":
    unittest.main()
