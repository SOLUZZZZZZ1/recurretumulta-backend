from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from ai.text_loader import (
    MAX_TEXT_LOADER_BYTES,
    TextLoaderSecurityError,
    _download_bytes,
    load_text_from_b2,
)
from b2_storage import B2ObjectTooLargeError
from rtm_core.parser_isolation import ParserIsolationTimeout


CASE_ID = "11111111-2222-4333-8444-555555555555"
KEY = f"cases/{CASE_ID}/original/document.pdf"


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class AiTextLoaderSecurityTests(unittest.TestCase):
    def test_download_uses_only_bounded_primitive(self) -> None:
        with (
            patch("b2_storage.get_b2_bucket", return_value="private"),
            patch("b2_storage.download_bytes_limited", return_value=b"safe") as bounded,
            patch("b2_storage.download_bytes") as unbounded,
        ):
            self.assertEqual(_download_bytes("private", KEY), b"safe")
        bounded.assert_called_once_with(
            "private",
            KEY,
            max_bytes=MAX_TEXT_LOADER_BYTES,
            case_id="11111111-2222-4333-8444-555555555555",
        )
        unbounded.assert_not_called()

    def test_oversized_object_fails_closed_without_leaking_detail(self) -> None:
        with (
            patch("b2_storage.get_b2_bucket", return_value="private"),
            patch(
                "b2_storage.download_bytes_limited",
                side_effect=B2ObjectTooLargeError("secret://oversized-canary"),
            ),
        ):
            with self.assertRaises(TextLoaderSecurityError) as raised:
                load_text_from_b2("private", KEY, "application/pdf")
        self.assertNotIn("canary", str(raised.exception))

    def test_bucket_and_key_must_stay_in_private_case_namespace(self) -> None:
        with (
            patch("b2_storage.get_b2_bucket", return_value="private"),
            patch("b2_storage.download_bytes_limited") as bounded,
        ):
            with self.assertRaises(TextLoaderSecurityError):
                _download_bytes("other", KEY)
            with self.assertRaises(TextLoaderSecurityError):
                _download_bytes("private", "public/../../secret")
        bounded.assert_not_called()

    def test_parser_timeout_does_not_fall_back_to_stale_database_text(self) -> None:
        with (
            patch("ai.text_loader._download_bytes", return_value=_pdf_bytes()),
            patch(
                "rtm_core.upload_security.validate_document_bytes",
                side_effect=ParserIsolationTimeout("worker internals"),
            ),
            patch(
                "ai.text_loader._load_latest_extraction_text",
                return_value="stale " * 100,
            ) as fallback,
        ):
            with self.assertRaises(TextLoaderSecurityError):
                load_text_from_b2("private", KEY, "application/pdf")
        fallback.assert_not_called()

    def test_valid_but_textless_pdf_may_use_bounded_existing_extraction(self) -> None:
        existing = "texto verificado " * 30
        with (
            patch("ai.text_loader._download_bytes", return_value=_pdf_bytes()),
            patch(
                "ai.text_loader._load_latest_extraction_text",
                return_value=existing,
            ),
        ):
            result = load_text_from_b2("private", KEY, "application/pdf")
        self.assertEqual(result, existing)


if __name__ == "__main__":
    unittest.main()
