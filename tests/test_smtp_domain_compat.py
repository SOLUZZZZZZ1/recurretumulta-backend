"""Existing senders and links exercised without SMTP, Stripe or database access."""
import os
import ssl
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import billing_dynamic
import cases
import contact_backend_fastapi as contact
import email_utils
import partner
from smtp_transport import smtp_connection, smtp_password


class SmtpDomainCompatibilityTest(unittest.TestCase):
    def environment(self, port=465):
        return {
            'SMTP_HOST': 'smtp.example.invalid', 'SMTP_PORT': str(port),
            'SMTP_USER': 'sender@example.invalid', 'SMTP_FROM': 'sender@example.invalid',
            'SMTP_PASSWORD': 'synthetic-password', 'SMTP_USE_TLS': 'true',
            'FRONTEND_URL': 'https://www.resuelvetusmovidas.es', 'RTM_ENV': 'production',
        }

    def invoke(self, name, port):
        if name == 'utility':
            self.assertTrue(email_utils.send_email(to_email='recipient@example.invalid', subject='Test', body='Synthetic body'))
        elif name == 'cases':
            cases._send_email('recipient@example.invalid', 'Test', 'Synthetic body')
        elif name == 'contact':
            with patch.multiple(contact, SMTP_HOST='smtp.example.invalid', SMTP_PORT=port,
                                SMTP_USER='sender@example.invalid', SMTP_PASSWORD='synthetic-password',
                                SMTP_FROM='sender@example.invalid', CONTACT_TO='recipient@example.invalid', SMTP_USE_TLS=True):
                result = contact.send_contact_email(contact.ContactRequest(
                    tipo_consulta='Consulta sintética', nombre='Prueba', email='reply@example.com',
                    mensaje='Mensaje sintético para comprobar el transporte.'))
                self.assertTrue(result['ok'])
        else:
            result = partner.partner_signup(partner.PartnerSignupRequest(
                empresa='Empresa ficticia', contacto='Prueba', email='reply@example.com'))
            self.assertTrue(result['ok'])

    def test_all_four_senders_use_verified_implicit_tls_on_465_and_starttls_on_587(self):
        for port in (465, 587):
            for caller in ('utility', 'cases', 'contact', 'partner'):
                with self.subTest(port=port, caller=caller), patch.dict(os.environ, self.environment(port), clear=True), \
                        patch('smtp_transport.smtplib.SMTP_SSL') as implicit, patch('smtp_transport.smtplib.SMTP') as explicit:
                    server = MagicMock()
                    chosen, unused = (implicit, explicit) if port == 465 else (explicit, implicit)
                    chosen.return_value.__enter__.return_value = server
                    order = []
                    server.starttls.side_effect = lambda **kwargs: order.append('tls')
                    server.login.side_effect = lambda *args: order.append('login')
                    server.send_message.side_effect = lambda *args: order.append('send')
                    self.invoke(caller, port)
                    unused.assert_not_called()
                    self.assertEqual(chosen.call_args.args, ('smtp.example.invalid', port))
                    self.assertGreater(chosen.call_args.kwargs['timeout'], 0)
                    if port == 465:
                        context = chosen.call_args.kwargs['context']
                        server.starttls.assert_not_called()
                        self.assertEqual(order, ['login', 'send'])
                    else:
                        context = server.starttls.call_args.kwargs['context']
                        self.assertEqual(order, ['tls', 'login', 'send'])
                    self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                    server.login.assert_called_once_with('sender@example.invalid', 'synthetic-password')
                    server.send_message.assert_called_once()
                    message = server.send_message.call_args.args[0]
                    self.assertEqual(message['From'], 'sender@example.invalid')
                    self.assertEqual(message['To'], 'soporte@recurretumulta.eu' if caller == 'partner' else 'recipient@example.invalid')

    def test_starttls_failure_never_authenticates_or_sends_for_any_existing_sender(self):
        for caller in ('utility', 'cases', 'contact', 'partner'):
            with self.subTest(caller=caller), patch.dict(os.environ, self.environment(587), clear=True), \
                    patch('smtp_transport.smtplib.SMTP') as explicit, patch('smtp_transport.smtplib.SMTP_SSL') as implicit:
                server = explicit.return_value.__enter__.return_value
                server.starttls.side_effect = ssl.SSLError('synthetic-tls-failure')
                if caller == 'cases':
                    self.invoke(caller, 587)
                else:
                    with self.assertRaises((ssl.SSLError, HTTPException)):
                        self.invoke(caller, 587)
                server.login.assert_not_called()
                server.send_message.assert_not_called()
                implicit.assert_not_called()

    def test_implicit_tls_certificate_failure_does_not_fallback(self):
        with patch('smtp_transport.smtplib.SMTP_SSL', side_effect=ssl.SSLCertVerificationError('synthetic')), \
                patch('smtp_transport.smtplib.SMTP') as explicit:
            with self.assertRaises(ssl.SSLCertVerificationError):
                with smtp_connection('smtp.example.invalid', 465):
                    self.fail('Context entered after failed certificate verification')
            explicit.assert_not_called()

    def test_465_remains_encrypted_when_starttls_flag_is_false(self):
        with patch('smtp_transport.smtplib.SMTP_SSL') as implicit, patch('smtp_transport.smtplib.SMTP') as explicit:
            with smtp_connection('smtp.example.invalid', 465, starttls=False):
                pass
            implicit.assert_called_once()
            explicit.assert_not_called()
            implicit.return_value.__enter__.return_value.starttls.assert_not_called()

    def test_standard_credential_wins_and_legacy_alias_is_only_a_fallback(self):
        with patch.dict(os.environ, {'SMTP_PASSWORD': 'new-synthetic', 'SMTP_PASS': 'old-synthetic'}, clear=True):
            self.assertEqual(smtp_password(), 'new-synthetic')
        with patch.dict(os.environ, {'SMTP_PASS': 'old-synthetic'}, clear=True):
            self.assertEqual(smtp_password(), 'old-synthetic')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(smtp_password(), '')

    def test_provider_error_is_not_returned_to_contact_or_partner_client(self):
        for caller in ('contact', 'partner'):
            with self.subTest(caller=caller), patch.dict(os.environ, self.environment(), clear=True), \
                    patch('smtp_transport.smtplib.SMTP_SSL', side_effect=RuntimeError('private-provider-error')):
                with self.assertRaises(HTTPException) as caught:
                    self.invoke(caller, 465)
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn('private-provider-error', str(caught.exception.detail))

    def test_existing_case_and_checkout_links_follow_both_canonical_domains(self):
        for origin in ('https://www.recurretumulta.eu', 'https://www.resuelvetusmovidas.es'):
            with self.subTest(origin=origin), patch.dict(os.environ, {'FRONTEND_URL': origin, 'RTM_ENV': 'production'}, clear=True):
                self.assertEqual(cases._case_link('synthetic-case'), origin + '/#/resumen?case=synthetic-case')
                self.assertEqual(billing_dynamic._frontend_base(), origin)

    def test_untrusted_or_ambiguous_origin_does_not_generate_a_link(self):
        for config in ({'FRONTEND_URL': 'https://evil.example'},
                       {'FRONTEND_URL': 'https://www.resuelvetusmovidas.es', 'FRONTEND_BASE_URL': 'https://www.recurretumulta.eu'}):
            with self.subTest(config=config), patch.dict(os.environ, config, clear=True):
                with self.assertRaises(RuntimeError):
                    cases._case_link('synthetic-case')
                with self.assertRaises(RuntimeError):
                    billing_dynamic._frontend_base()


if __name__ == '__main__':
    unittest.main()
