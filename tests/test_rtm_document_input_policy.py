from __future__ import annotations

import unittest

from rtm_core.document_input_policy import (
    DOCUMENT_INPUT_POLICY_VERSION,
    document_input_policy_block,
)


RUN_PATH = "/ops/core/cases/case-123/document-extractions/run"


class DocumentInputPolicyTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(
            DOCUMENT_INPUT_POLICY_VERSION,
            "rtm_document_input_policy_v1_2",
        )

    def test_irrelevant_route_is_not_blocked(self):
        block = document_input_policy_block(
            method="GET",
            path=RUN_PATH,
            environ={"RTM_ENV": "staging"},
        )
        self.assertIsNone(block)

    def test_staging_without_synthetic_policy_fails_closed(self):
        block = document_input_policy_block(
            method="POST",
            path=RUN_PATH,
            environ={
                "RTM_ENV": "staging",
                "RTM_DOCUMENT_INPUT_POLICY": "",
            },
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.status_code, 503)
        self.assertEqual(block.detail["required_policy"], "synthetic_only")

    def test_staging_synthetic_only_blocks_persisted_case_documents(self):
        block = document_input_policy_block(
            method="POST",
            path=RUN_PATH,
            environ={
                "RTM_ENV": "staging",
                "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
            },
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.status_code, 409)
        self.assertEqual(
            block.detail["allowed_entrypoint"],
            "scripts/rtm_staging_smoke.py",
        )

    def test_staging_blocks_every_public_or_ops_document_entrypoint(self):
        paths = (
            RUN_PATH,
            "/ops/core/cases/case-123/reanalysis/run",
            "/analyze",
            "/analyze/expediente",
            "/vehicle-removal/verify-registration",
            "/cases/intake-draft",
            "/cases/case-123/append-documents",
            "/cases/case-123/upload-authorization-signed",
            "/cases/case-123/authorization-signed",
            "/cases/case-123/upload-receipt",
            "/partner/cases",
            "/ops/cases/case-123/upload-justificante",
            "/ops/cases/case-123/register-manual-submission",
        )
        for path in paths:
            with self.subTest(path=path):
                block = document_input_policy_block(
                    method="POST",
                    path=path,
                    environ={
                        "RTM_ENV": "staging",
                        "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
                    },
                )
                self.assertIsNotNone(block)
                self.assertEqual(block.status_code, 409)

    def test_production_requires_customer_documents_policy(self):
        block = document_input_policy_block(
            method="POST",
            path=RUN_PATH,
            environ={
                "RTM_ENV": "production",
                "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
            },
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.status_code, 503)
        self.assertEqual(block.detail["required_policy"], "customer_documents")

    def test_production_customer_documents_can_continue(self):
        block = document_input_policy_block(
            method="POST",
            path=RUN_PATH,
            environ={
                "RTM_ENV": "production",
                "RTM_DOCUMENT_INPUT_POLICY": "customer_documents",
            },
        )
        self.assertIsNone(block)

    def test_legacy_environment_keeps_existing_compatibility(self):
        block = document_input_policy_block(
            method="POST",
            path=RUN_PATH,
            environ={},
        )
        self.assertIsNone(block)

    def test_ambiguous_deployment_blocks_document_inputs(self):
        for environment in (
            {"RENDER_SERVICE_ID": "srv-rtm"},
            {"RTM_ENV": "stagin"},
            {"RTM_ENABLE_DOCUMENT_PROVIDER": "1"},
        ):
            with self.subTest(environment=environment):
                block = document_input_policy_block(
                    method="POST",
                    path=RUN_PATH,
                    environ=environment,
                )
                self.assertIsNotNone(block)
                self.assertEqual(block.status_code, 503)
                self.assertNotIn("srv-rtm", str(block.detail))


if __name__ == "__main__":
    unittest.main()
