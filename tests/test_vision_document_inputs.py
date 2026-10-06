from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import analyze
import openai_vision
import reanalysis
from rtm_core.ai_security import model_call_budget
from rtm_core.staging_rehearsal import fixture


class VisionDocumentInputTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}))
        self.enterContext(patch.object(openai_vision, "require_capability"))
        self.sent = []

    def provider(self, _url, **kwargs):
        payload = kwargs["json"]
        self.sent.append(payload)
        self.assertIs(payload["store"], False)
        self.assertNotIn("tools", payload)
        self.assertFalse(kwargs["allow_redirects"])
        parts = [part for item in payload["input"] for part in item["content"]]
        for part in parts:
            if part["type"] == "input_image":
                self.assertTrue(part["image_url"].startswith("data:image/"),
                                "PDF cannot be sent as input_image")
            if part["type"] == "input_file":
                self.assertEqual(set(part), {"type", "filename", "file_data"})
                self.assertEqual(part["filename"], "document.pdf")
                self.assertTrue(part["file_data"].startswith("data:application/pdf;base64,"))
        output = {"observaciones": "Ensayo", "vision_raw_text": "Peticion de identificacion del conductor"}
        return SimpleNamespace(ok=True, json=lambda: {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]
        })

    def test_actual_rehearsal_pdf_crosses_parser_and_vision_boundary(self):
        content = fixture("radar")[1]
        with patch.object(analyze, "extract_from_text", return_value={}), \
                patch.object(openai_vision.requests, "post", side_effect=self.provider):
            wrapper, _ = reanalysis._analyze_page_candidate(content, "rehearsal.pdf", "application/pdf")
        self.assertEqual(wrapper["evidence_status"], "candidate_only")
        parts = self.sent[0]["input"][-1]["content"]
        attachment = next(part for part in parts if part["type"] == "input_file")
        self.assertEqual(base64.b64decode(attachment["file_data"].split(",", 1)[1]), content)
        self.assertFalse(any(part["type"] == "input_image" for part in parts))

    def test_focused_pdf_uses_file_input_without_image_crop(self):
        content = fixture("radar")[1]
        with patch.object(openai_vision.requests, "post", side_effect=self.provider), \
                patch.object(openai_vision, "run_image_parser_isolated") as crop, model_call_budget(1):
            openai_vision.extract_fet_denunciat_focus(content, "application/pdf", "private-name.pdf")
        crop.assert_not_called()
        self.assertNotIn("private-name", json.dumps(self.sent))
        self.assertEqual(self.sent[0]["input"][-1]["content"][-1]["type"], "input_file")

    def test_existing_image_input_stays_an_image(self):
        with patch.object(openai_vision.requests, "post", side_effect=self.provider), model_call_budget(1):
            openai_vision.extract_from_image_bytes(b"synthetic-image", "image/png")
        attachment = self.sent[0]["input"][-1]["content"][-1]
        self.assertEqual(attachment["type"], "input_image")
        self.assertEqual(base64.b64decode(attachment["image_url"].split(",", 1)[1]), b"synthetic-image")


    def test_provider_error_preserves_status_without_response_body(self):
        response = SimpleNamespace(ok=False, status_code=400, text="private provider response")
        with patch.object(openai_vision.requests, "post", return_value=response), model_call_budget(1):
            with self.assertRaises(openai_vision.OCRProviderHTTPError) as raised:
                openai_vision.extract_from_image_bytes(fixture("radar")[1], "application/pdf")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("private", str(raised.exception))

    def test_failure_diagnostic_omits_exception_text_and_response(self):
        exc = openai_vision.OCRProviderHTTPError(400)
        exc.args = ("private data / api key / original document",)
        exc.response = {"secret": "private provider response"}
        with self.assertLogs("reanalysis", level="ERROR") as captured:
            reanalysis._log_reanalysis_failure(exc, "synthetic-run", "page_extraction")
        data = json.loads(captured.records[0].getMessage())
        self.assertEqual(data, {
            "event": "reanalysis_internal_failure", "reanalysis_run_id": "synthetic-run",
            "stage": "page_extraction", "exception_type": "OCRProviderHTTPError",
            "provider_status": 400})
        self.assertNotIn("private", captured.output[0])


if __name__ == "__main__":
    unittest.main()
