from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ai.expediente_engine as expediente_engine
import openai_text


OFFICIAL_BASE_URL = "https://api.openai.com/v1"


class OpenAIEndpointSecurityTest(unittest.TestCase):
    def tearDown(self) -> None:
        openai_text._client.cache_clear()

    def test_text_client_does_not_inherit_openai_base_url(self):
        client = MagicMock(name="openai_client")
        openai_text._client.cache_clear()
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "unit-test-api-key",
                "OPENAI_BASE_URL": "https://attacker.example/v1",
            },
            clear=False,
        ), patch.object(openai_text, "OpenAI", return_value=client) as factory:
            self.assertIs(openai_text._client(), client)

        self.assertEqual(factory.call_args.kwargs["base_url"], OFFICIAL_BASE_URL)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    def test_legacy_expediente_client_does_not_inherit_openai_base_url(self):
        client = MagicMock(name="openai_client")
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        )
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "unit-test-api-key",
                "OPENAI_BASE_URL": "https://attacker.example/v1",
            },
            clear=False,
        ), patch.object(
            expediente_engine,
            "OpenAI",
            return_value=client,
        ) as factory, patch.object(
            expediente_engine,
            "require_capability",
        ), patch.object(
            expediente_engine,
            "require_model_call_budget",
        ), patch.object(
            expediente_engine,
            "consume_model_call_budget",
        ):
            self.assertEqual(
                expediente_engine._llm_json("system", {"content": "evidence"}),
                {"ok": True},
            )

        self.assertEqual(factory.call_args.kwargs["base_url"], OFFICIAL_BASE_URL)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
