from __future__ import annotations

import os
import unittest
from unittest.mock import ANY, patch

import email_utils


class NominaliaEmailTransportTest(unittest.TestCase):
    def _environment(self, **overrides: str) -> dict[str, str]:
        environment = {
            "RTM_ENV": "production",
            "RTM_ENABLE_OUTBOUND_EMAIL": "1",
            "SMTP_HOST": "authsmtp.securemail.pro",
            "SMTP_PORT": "465",
            "SMTP_SECURITY": "ssl",
            "SMTP_USER": "info@recurretumulta.eu",
            "SMTP_PASSWORD": "synthetic-placeholder-password",
            "SMTP_FROM": "RecurreTuMulta <info@recurretumulta.eu>",
        }
        environment.update(overrides)
        return environment

    def test_nominalia_port_465_uses_implicit_ssl(self):
        with patch.dict(os.environ, self._environment(), clear=True):
            with patch("email_utils.smtplib.SMTP_SSL") as smtp_ssl:
                with patch("email_utils.smtplib.SMTP") as smtp:
                    connection = smtp_ssl.return_value.__enter__.return_value
                    sent = email_utils.send_email(
                        to_email="recipient@example.com",
                        subject="Prueba RTM",
                        body="Mensaje sintético.",
                        reply_to="info@recurretumulta.eu",
                    )

        self.assertTrue(sent)
        smtp_ssl.assert_called_once_with(
            "authsmtp.securemail.pro", 465, timeout=20, context=ANY
        )
        smtp.assert_not_called()
        connection.starttls.assert_not_called()
        connection.login.assert_called_once_with(
            "info@recurretumulta.eu", "synthetic-placeholder-password"
        )
        message = connection.send_message.call_args.args[0]
        self.assertEqual(
            message["From"], "RecurreTuMulta <info@recurretumulta.eu>"
        )
        self.assertEqual(message["Reply-To"], "info@recurretumulta.eu")

    def test_port_587_keeps_starttls_compatibility(self):
        environment = self._environment(
            SMTP_PORT="587",
            SMTP_SECURITY="starttls",
        )
        with patch.dict(os.environ, environment, clear=True):
            with patch("email_utils.smtplib.SMTP_SSL") as smtp_ssl:
                with patch("email_utils.smtplib.SMTP") as smtp:
                    connection = smtp.return_value.__enter__.return_value
                    sent = email_utils.send_email(
                        to_email="recipient@example.com",
                        subject="Prueba RTM",
                        body="Mensaje sintético.",
                    )

        self.assertTrue(sent)
        smtp.assert_called_once_with(
            "authsmtp.securemail.pro", 587, timeout=20
        )
        smtp_ssl.assert_not_called()
        connection.starttls.assert_called_once_with(context=ANY)
        self.assertEqual(connection.ehlo.call_count, 2)

    def test_canonical_password_is_required_and_legacy_pass_is_ignored(self):
        environment = self._environment()
        environment.pop("SMTP_PASSWORD")
        environment["SMTP_PASS"] = "legacy-placeholder-password"

        with patch.dict(os.environ, environment, clear=True):
            with patch("email_utils.smtplib.SMTP_SSL") as smtp_ssl:
                with self.assertRaisesRegex(RuntimeError, "incompleta"):
                    email_utils.send_email(
                        to_email="recipient@example.com",
                        subject="Prueba RTM",
                        body="Mensaje sintético.",
                    )

        smtp_ssl.assert_not_called()

    def test_plain_smtp_and_non_allowlisted_hosts_are_rejected(self):
        for overrides in (
            {"SMTP_SECURITY": "plain", "SMTP_PORT": "587"},
            {"SMTP_HOST": "smtp.attacker.example"},
        ):
            with self.subTest(overrides=overrides):
                with patch.dict(
                    os.environ,
                    self._environment(**overrides),
                    clear=True,
                ):
                    with patch("email_utils.smtplib.SMTP") as smtp:
                        with self.assertRaises(RuntimeError):
                            email_utils.send_email(
                                to_email="recipient@example.com",
                                subject="Prueba RTM",
                                body="Mensaje sintético.",
                            )
                    smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
