from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

import authorization_pdf
from authorization_pdf import generate_authorization_pdf


class AuthorizationPdfSecurityTest(unittest.TestCase):
    def test_user_markup_cannot_load_external_or_local_resources(self):
        malicious = '<img src="file:///etc/passwd"/><link href="https://example.invalid/x"/>'
        payload = {
            "case_id": malicious,
            "organismo": malicious,
            "expediente_ref": malicious,
            "full_name": malicious,
            "dni_nie": malicious,
            "domicilio_notif": malicious,
            "email": "safe@example.com",
            "telefono": malicious,
            "ip": malicious,
            "version": malicious,
            "authorized_at": "2026-09-03T12:00:00+00:00",
        }
        with patch(
            "reportlab.platypus.paraparser.ImageReader",
            side_effect=AssertionError("ReportLab intentó cargar un recurso no confiable"),
        ):
            rendered = generate_authorization_pdf(payload)
        self.assertTrue(rendered.startswith(b"%PDF-"))
        self.assertGreater(len(rendered), 500)

    def test_authorization_pdf_never_loads_or_embeds_a_raster_signature(self):
        source = inspect.getsource(authorization_pdf)
        self.assertNotIn("templates/firma.png", source)
        self.assertNotIn("SIGNATURE_PATH", source)
        self.assertNotIn("_find_signature_path", source)
        self.assertNotIn("Image(", source)


if __name__ == "__main__":
    unittest.main()
