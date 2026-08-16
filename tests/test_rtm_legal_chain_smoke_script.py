from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_legal_chain_smoke.py"


class LegalChainSmokeScriptTest(unittest.TestCase):
    def _module(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_legal_chain_smoke as smoke
        finally:
            sys.path.pop(0)
        return smoke

    def test_contract_is_synthetic_transactional_and_complete(self):
        smoke = self._module()
        self.assertEqual(
            smoke.SMOKE_VERSION,
            "rtm_legal_chain_smoke_v1_0",
        )
        fixture = smoke._fixture_bytes()
        self.assertIn(
            smoke.SYNTHETIC_MARKER.encode("utf-8"),
            fixture,
        )
        for event_type in (
            "rtm_document_extraction_completed",
            "rtm_validated_facts_frozen",
            "rtm_family_resolution_locked",
            "rtm_legal_preview_frozen",
            "rtm_resource_generated_from_frozen_preview",
        ):
            self.assertIn(event_type, smoke._EXPECTED_EVENT_TYPES)

    def test_refuses_to_run_outside_staging_before_database_or_b2(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
                "RTM_ENABLE_B2": "0",
                "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
                "RTM_DOCUMENT_INPUT_POLICY": "customer_documents",
                "RTM_STAGING_CONFIRM": "",
                "RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION": "0",
                "RTM_ENABLE_OUTBOUND_EMAIL": "1",
                "RTM_ENABLE_EXTERNAL_SUBMISSION": "1",
                "RTM_ENABLE_FINAL_PAYMENTS": "1",
                "OPENAI_API_KEY": "",
                "OPENAI_DOCUMENT_MODEL": "",
            }
        )
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--compact"],
            cwd=REPOSITORY_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["safe"])
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertNotIn("cleanup", payload)

    def test_exact_staging_profile_passes_static_guards(self):
        smoke = self._module()
        env = {
            "RTM_ENV": "staging",
            "RTM_DATA_NAMESPACE": "rtm_staging",
            "RTM_SIDE_EFFECT_POLICY": "isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
            "RTM_ENABLE_B2": "true",
            "RTM_ENABLE_DOCUMENT_PROVIDER": "true",
            "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
            "RTM_STAGING_CONFIRM": "SYNTHETIC_ONLY",
            "RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION": "1",
            "RTM_ENABLE_OUTBOUND_EMAIL": "false",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "false",
            "RTM_ENABLE_FINAL_PAYMENTS": "false",
            "OPENAI_API_KEY": "sk-synthetic-test-only",
            "OPENAI_DOCUMENT_MODEL": "gpt-4o",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(smoke._safety_blockers(), [])

    def test_generate_guard_requires_frozen_preview(self):
        smoke = self._module()
        error = HTTPException(
            status_code=409,
            detail="La Previa Jurídica no está congelada",
        )
        with patch(
            "rtm_core.generation_gateway.generate_from_frozen_preview",
            side_effect=error,
        ):
            self.assertTrue(
                smoke._generate_is_blocked(
                    object(),
                    case_id="synthetic-case",
                    preview_id="synthetic-preview",
                )
            )


if __name__ == "__main__":
    unittest.main()
