from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from rtm_core.environment_contract import (
    ENVIRONMENT_CONTRACT_VERSION,
    assert_environment_ready,
    build_environment_preflight,
)


BRANCH = "rtm-core-consolidation-2026-08-08"


def _base_staging() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_INSTANCE_ID": "rtm-staging",
        "RTM_DATA_NAMESPACE": "rtm-staging",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_staging"
        ),
        "FRONTEND_URL": "https://staging.recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://staging.recurretumulta.eu",
        "OPERATOR_TOKEN": "op_" + ("x" * 48),
        "RTM_EXPECTED_BRANCH": BRANCH,
        "RENDER_GIT_BRANCH": BRANCH,
        "RENDER_SERVICE_NAME": "rtm-staging-backend",
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
    }


def _base_production() -> dict[str, str]:
    return {
        "RTM_ENV": "production",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_PRODUCTION_LIVE",
        "RTM_INSTANCE_ID": "rtm-production",
        "RTM_DATA_NAMESPACE": "rtm-production",
        "RTM_SIDE_EFFECT_POLICY": "live",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_production"
        ),
        "FRONTEND_URL": "https://recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://recurretumulta.eu",
        "OPERATOR_TOKEN": "op_" + ("p" * 48),
        "RTM_EXPECTED_BRANCH": "main",
        "RENDER_GIT_BRANCH": "main",
        "RENDER_SERVICE_NAME": "rtm-production-backend",
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
    }


class EnvironmentContractTest(unittest.TestCase):
    def test_version_and_minimal_staging_profile_are_explicit(self):
        self.assertEqual(
            ENVIRONMENT_CONTRACT_VERSION,
            "rtm_environment_contract_v1_0",
        )
        report = build_environment_preflight(_base_staging())
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertEqual(report.environment, "staging")
        self.assertFalse(any(report.capabilities.values()))
        self.assertFalse(report.blockers)

    def test_minimal_production_profile_is_separate(self):
        report = build_environment_preflight(_base_production())
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertEqual(report.environment, "production")

    def test_staging_rejects_wildcard_production_frontend_and_unmarked_database(self):
        scenarios = [
            ("ALLOWED_ORIGINS", "*", "cors_no_wildcard"),
            (
                "FRONTEND_URL",
                "https://recurretumulta.eu",
                "staging_frontend_isolated",
            ),
            (
                "DATABASE_URL",
                "postgresql+psycopg://rtm:password@db.internal/rtm",
                "staging_database_isolated",
            ),
        ]
        for name, value, expected_blocker in scenarios:
            with self.subTest(variable=name):
                environment = _base_staging()
                environment[name] = value
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn(expected_blocker, report.blockers)

    def test_runtime_branch_must_equal_the_declared_branch(self):
        environment = _base_staging()
        environment["RENDER_GIT_BRANCH"] = "main"
        report = build_environment_preflight(environment)
        self.assertFalse(report.safe)
        self.assertIn("runtime_branch_matches", report.blockers)

    def test_invalid_feature_flag_fails_closed(self):
        environment = _base_staging()
        environment["RTM_ENABLE_B2"] = "perhaps"
        report = build_environment_preflight(environment)
        self.assertFalse(report.safe)
        self.assertIn("b2_flag_valid", report.blockers)

    def test_staging_b2_requires_https_and_a_dedicated_marked_bucket(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_B2": "1",
                "B2_ENDPOINT": "https://s3.us-west-000.backblazeb2.com",
                "B2_BUCKET": "rtm-staging-documents",
                "B2_KEY_ID": "0030123456789abc",
                "B2_APPLICATION_KEY": "K" * 40,
                "RTM_B2_ISOLATION_MODE": "dedicated_bucket",
            }
        )
        report = build_environment_preflight(environment)
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertTrue(report.capabilities["b2"])

        environment["B2_BUCKET"] = "rtm-production-documents"
        environment["RTM_PRODUCTION_B2_BUCKET"] = "rtm-production-documents"
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn("staging_b2_bucket_isolated", blocked.blockers)

    def test_staging_stripe_accepts_only_test_mode_and_never_real_payments(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_STRIPE": "1",
                "STRIPE_SECRET_KEY": "sk_test_" + ("a" * 32),
                "STRIPE_WEBHOOK_SECRET": "whsec_" + ("b" * 32),
                "STRIPE_PRICE_ID_REVIEW_BASIC": "price_test_basic",
                "STRIPE_PRICE_ID_ADMIN": "price_test_admin",
                "RTM_STRIPE_MODE": "test",
                "RTM_ALLOW_REAL_PAYMENTS": "0",
            }
        )
        report = build_environment_preflight(environment)
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertTrue(report.capabilities["stripe"])

        environment["STRIPE_SECRET_KEY"] = "sk_live_" + ("z" * 32)
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn("staging_stripe_test_mode", blocked.blockers)

        environment["STRIPE_SECRET_KEY"] = "sk_test_" + ("a" * 32)
        environment["RTM_ALLOW_REAL_PAYMENTS"] = "1"
        blocked = build_environment_preflight(environment)
        self.assertIn("staging_real_payments_disabled", blocked.blockers)

    def test_document_provider_in_staging_is_synthetic_only(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_DOCUMENT_PROVIDER": "1",
                "OPENAI_API_KEY": "sk-proj-" + ("q" * 40),
                "OPENAI_DOCUMENT_MODEL": "gpt-4o",
                "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
            }
        )
        report = build_environment_preflight(environment)
        self.assertTrue(report.safe, report.model_dump(mode="json"))

        environment["RTM_DOCUMENT_INPUT_POLICY"] = "customer_documents"
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn("staging_document_input_policy", blocked.blockers)

    def test_staging_blocks_email_and_external_submissions(self):
        for flag, blocker in (
            ("RTM_ENABLE_OUTBOUND_EMAIL", "staging_outbound_email_disabled"),
            (
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
                "staging_external_submission_disabled",
            ),
        ):
            with self.subTest(flag=flag):
                environment = _base_staging()
                environment[flag] = "1"
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn(blocker, report.blockers)

    def test_report_and_exception_never_expose_secret_values(self):
        environment = _base_staging()
        secret_values = {
            "OPERATOR_TOKEN": "operator-super-private-" + ("x" * 32),
            "STRIPE_SECRET_KEY": "sk_test_" + ("s" * 32),
            "STRIPE_WEBHOOK_SECRET": "whsec_" + ("w" * 32),
        }
        environment.update(secret_values)
        environment.update(
            {
                "RTM_ENABLE_STRIPE": "1",
                "STRIPE_PRICE_ID_REVIEW_BASIC": "price_test_basic",
                "STRIPE_PRICE_ID_ADMIN": "price_test_admin",
                "RTM_STRIPE_MODE": "test",
                "RTM_ALLOW_REAL_PAYMENTS": "0",
            }
        )
        report = build_environment_preflight(environment)
        rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
        for value in secret_values.values():
            self.assertNotIn(value, rendered)

        environment["RENDER_GIT_BRANCH"] = "main"
        with self.assertRaises(RuntimeError) as context:
            assert_environment_ready(environment)
        for value in secret_values.values():
            self.assertNotIn(value, str(context.exception))

    def test_preflight_runner_operates_from_repository_root(self):
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment.update(_base_staging())
        process = subprocess.run(
            [
                sys.executable,
                "scripts/rtm_environment_preflight.py",
                "--compact",
            ],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["safe"])
        self.assertEqual(payload["environment"], "staging")
        self.assertNotIn(environment["OPERATOR_TOKEN"], process.stdout)


if __name__ == "__main__":
    unittest.main()
