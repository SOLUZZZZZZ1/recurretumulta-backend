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
    deployment_runtime_signals,
    runtime_requires_environment_preflight,
)


BRANCH = "rtm-core-consolidation-2026-08-08"
SECRET_A = "A7mQ2vN9kR4xT8pL3sW6cD1hJ5uZ0bY"
SECRET_B = "F9rK3xV7nM2qP8dT4zH6wC1jL5sG0aU"
SECRET_C = "Z6pD1yW8kQ4mR9vB2tN7cH5xJ3fL0sE"


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
        # Dominio reservado exclusivamente para validar el contrato; el host
        # real se configura en el entorno del despliegue, no en el repositorio.
        "RTM_ALLOWED_HOSTS": "backend-staging.invalid",
        "OPERATOR_TOKEN": "op_" + SECRET_A,
        "RTM_PUBLIC_CASE_ACCESS_SECRET": "case_" + SECRET_B,
        "RTM_AUTHORITY_SIGNING_SECRET": "authority_" + SECRET_C,
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
        "RTM_ALLOWED_HOSTS": "backend-production.invalid",
        "OPERATOR_TOKEN": "op_" + SECRET_A,
        "RTM_PUBLIC_CASE_ACCESS_SECRET": "case_" + SECRET_B,
        "RTM_AUTHORITY_SIGNING_SECRET": "authority_" + SECRET_C,
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
            "rtm_environment_contract_v1_2",
        )
        report = build_environment_preflight(_base_staging())
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertEqual(report.environment, "staging")
        self.assertFalse(any(report.capabilities.values()))
        self.assertFalse(report.blockers)

    def test_deployed_or_ambiguous_runtime_never_degrades_to_local(self):
        self.assertFalse(runtime_requires_environment_preflight({}))
        self.assertFalse(
            runtime_requires_environment_preflight(
                {"RTM_ENV": "development", "DATABASE_URL": "postgresql://local/rtm"}
            )
        )
        scenarios = (
            {"RTM_ENV": "stagin"},
            {"DATABASE_URL": "postgresql://deployed/rtm"},
            {"RTM_ENABLE_STRIPE": "1"},
            {"RTM_ALLOWED_HOSTS": "api.example.invalid"},
            {"FRONTEND_BASE_URL": "https://legacy.example"},
            {"OPENAI_BASE_URL": "https://model-gateway.example/v1"},
            {"RTM_ENV": "development", "RENDER_SERVICE_ID": "srv-123"},
        )
        for environment in scenarios:
            with self.subTest(environment=environment):
                self.assertTrue(runtime_requires_environment_preflight(environment))

        signals = deployment_runtime_signals(
            {"DATABASE_URL": "secret-value", "RENDER_FUTURE_MARKER": "private"}
        )
        self.assertEqual(signals, ("DATABASE_URL", "RENDER_FUTURE_MARKER"))
        self.assertNotIn("secret-value", signals)
        self.assertNotIn("private", signals)

    def test_deployed_runtime_rejects_legacy_and_provider_base_url_overrides(self):
        scenarios = (
            (
                "FRONTEND_BASE_URL",
                "https://attacker.example",
                "frontend_base_url_forbidden",
            ),
            (
                "OPENAI_BASE_URL",
                "https://api.openai.com/v1",
                "openai_base_url_forbidden",
            ),
        )
        for name, value, blocker in scenarios:
            with self.subTest(variable=name):
                environment = _base_staging()
                environment[name] = value
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn(blocker, report.blockers)

    def test_deployed_runtime_rejects_trivial_security_secrets(self):
        scenarios = (
            ("OPERATOR_TOKEN", "operator_token_ready"),
            ("RTM_PUBLIC_CASE_ACCESS_SECRET", "public_case_access_secret_ready"),
            ("RTM_AUTHORITY_SIGNING_SECRET", "authority_signing_secret_ready"),
        )
        for name, blocker in scenarios:
            with self.subTest(variable=name):
                environment = _base_staging()
                environment[name] = "x" * 64
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn(blocker, report.blockers)

    def test_minimal_production_profile_is_separate(self):
        report = build_environment_preflight(_base_production())
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertEqual(report.environment, "production")

    def test_case_access_and_authority_secrets_are_mandatory_and_independent(self):
        for name, blocker in (
            ("RTM_PUBLIC_CASE_ACCESS_SECRET", "public_case_access_secret_ready"),
            ("RTM_AUTHORITY_SIGNING_SECRET", "authority_signing_secret_ready"),
        ):
            with self.subTest(variable=name):
                environment = _base_staging()
                environment.pop(name)
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn(blocker, report.blockers)

        environment = _base_staging()
        environment["RTM_AUTHORITY_SIGNING_SECRET"] = environment[
            "RTM_PUBLIC_CASE_ACCESS_SECRET"
        ]
        report = build_environment_preflight(environment)
        self.assertFalse(report.safe)
        self.assertIn("authority_secrets_independent", report.blockers)

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

    def test_matching_hostile_frontend_and_cors_are_still_rejected(self):
        environment = _base_production()
        environment["FRONTEND_URL"] = "https://attacker.example"
        environment["ALLOWED_ORIGINS"] = "https://attacker.example"
        report = build_environment_preflight(environment)
        self.assertFalse(report.safe)
        self.assertIn("frontend_host_trusted", report.blockers)
        self.assertIn("cors_hosts_trusted", report.blockers)

    def test_deployed_allowed_hosts_are_mandatory_and_exact(self):
        for environment_factory in (_base_staging, _base_production):
            with self.subTest(profile=environment_factory.__name__, value="missing"):
                environment = environment_factory()
                environment.pop("RTM_ALLOWED_HOSTS")
                report = build_environment_preflight(environment)
                self.assertFalse(report.safe)
                self.assertIn("allowed_hosts_present", report.blockers)

            for value in (
                "*",
                "*.example.invalid",
                "https://api.example.invalid",
                "api.example.invalid:443",
                "api.example.invalid/path",
            ):
                with self.subTest(
                    profile=environment_factory.__name__,
                    value=value,
                ):
                    environment = environment_factory()
                    environment["RTM_ALLOWED_HOSTS"] = value
                    report = build_environment_preflight(environment)
                    self.assertFalse(report.safe)
                    self.assertIn("allowed_hosts_exact", report.blockers)

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
                "B2_APPLICATION_KEY": SECRET_A,
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

        environment["B2_BUCKET"] = "rtm-staging-documents"
        environment["B2_ENDPOINT"] = "https://attacker.example"
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn("b2_endpoint_official", blocked.blockers)

    def test_proxy_headers_require_restricted_peer_cidrs(self):
        environment = _base_staging()
        environment["RTM_TRUST_PROXY_HEADERS"] = "1"
        for value in ("", "not-a-cidr", "0.0.0.0/0", "::/0"):
            with self.subTest(value=value):
                environment["RTM_TRUSTED_PROXY_CIDRS"] = value
                blocked = build_environment_preflight(environment)
                self.assertFalse(blocked.safe)
                self.assertIn("trusted_proxy_cidrs_restricted", blocked.blockers)

        environment["RTM_TRUSTED_PROXY_CIDRS"] = "10.0.0.0/8,2001:db8::/48"
        ready = build_environment_preflight(environment)
        self.assertTrue(ready.safe, ready.model_dump(mode="json"))

    def test_staging_stripe_accepts_only_test_mode_and_never_real_payments(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_STRIPE": "1",
                "STRIPE_SECRET_KEY": "sk_test_" + SECRET_A,
                "STRIPE_WEBHOOK_SECRET": "whsec_" + SECRET_B,
                "STRIPE_PRICE_ID_REVIEW_BASIC": "price_test_basic",
                "STRIPE_PRICE_ID_ADMIN": "price_test_admin",
                "RTM_STRIPE_MODE": "test",
                "RTM_ALLOW_REAL_PAYMENTS": "0",
            }
        )
        report = build_environment_preflight(environment)
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertTrue(report.capabilities["stripe"])

        environment["STRIPE_SECRET_KEY"] = "sk_live_" + SECRET_C
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn("staging_stripe_test_mode", blocked.blockers)

        environment["STRIPE_SECRET_KEY"] = "sk_test_" + SECRET_A
        environment["RTM_ALLOW_REAL_PAYMENTS"] = "1"
        blocked = build_environment_preflight(environment)
        self.assertIn("staging_real_payments_disabled", blocked.blockers)

    def test_final_payments_require_the_runtime_vehicle_removal_price(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_STRIPE": "1",
                "RTM_ENABLE_FINAL_PAYMENTS": "1",
                "STRIPE_SECRET_KEY": "sk_test_" + SECRET_A,
                "STRIPE_WEBHOOK_SECRET": "whsec_" + SECRET_B,
                "STRIPE_PRICE_ID_REVIEW_BASIC": "price_test_basic",
                "STRIPE_PRICE_ID_ADMIN": "price_test_admin",
                "RTM_STRIPE_MODE": "test",
                "RTM_ALLOW_REAL_PAYMENTS": "0",
            }
        )

        # Los aliases legacy no satisfacen el contrato del router montado.
        environment["STRIPE_PRICE_ID_DGT"] = "price_legacy_dgt"
        environment["STRIPE_PRICE_ID_VEHICLE"] = "price_legacy_vehicle"
        blocked = build_environment_preflight(environment)
        self.assertFalse(blocked.safe)
        self.assertIn(
            "stripe_price_id_eliminar_coche_present",
            blocked.blockers,
        )

        environment["STRIPE_PRICE_ID_ELIMINAR_COCHE"] = "not-a-price"
        malformed = build_environment_preflight(environment)
        self.assertFalse(malformed.safe)
        self.assertIn(
            "stripe_price_id_eliminar_coche_format",
            malformed.blockers,
        )

        environment["STRIPE_PRICE_ID_ELIMINAR_COCHE"] = "price_vehicle_removal"
        ready = build_environment_preflight(environment)
        self.assertTrue(ready.safe, ready.model_dump(mode="json"))
        self.assertTrue(ready.capabilities["final_payments"])

    def test_document_provider_in_staging_is_synthetic_only(self):
        environment = _base_staging()
        environment.update(
            {
                "RTM_ENABLE_DOCUMENT_PROVIDER": "1",
                "OPENAI_API_KEY": "sk-proj-" + SECRET_A,
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

    def test_production_smtp_requires_the_approved_tls_profile(self):
        environment = _base_production()
        environment.update(
            {
                "RTM_ENABLE_OUTBOUND_EMAIL": "1",
                "RTM_ALLOW_REAL_NOTIFICATIONS": "1",
                "SMTP_HOST": "authsmtp.securemail.pro",
                "SMTP_PORT": "465",
                "SMTP_SECURITY": "ssl",
                "SMTP_USER": "info@recurretumulta.eu",
                "SMTP_FROM": "RecurreTuMulta <info@recurretumulta.eu>",
                "SMTP_PASSWORD": SECRET_A,
            }
        )
        report = build_environment_preflight(environment)
        self.assertTrue(report.safe, report.model_dump(mode="json"))
        self.assertNotIn(
            environment["SMTP_PASSWORD"],
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False),
        )

        scenarios = (
            ("SMTP_HOST", "smtp.attacker.example", "smtp_host_allowed"),
            ("SMTP_PORT", "587", "smtp_transport_secure"),
            ("SMTP_SECURITY", "plain", "smtp_transport_secure"),
            ("SMTP_USER", "not-a-mailbox", "smtp_user_valid"),
            ("SMTP_USER", "info@example.com injected", "smtp_user_valid"),
            ("SMTP_FROM", "bad-from", "smtp_from_valid"),
            ("SMTP_PASSWORD", "p" * 40, "smtp_password_ready"),
        )
        for name, value, blocker in scenarios:
            with self.subTest(variable=name):
                candidate = dict(environment)
                candidate[name] = value
                blocked = build_environment_preflight(candidate)
                self.assertFalse(blocked.safe)
                self.assertIn(blocker, blocked.blockers)

    def test_report_and_exception_never_expose_secret_values(self):
        environment = _base_staging()
        secret_values = {
            "OPERATOR_TOKEN": "operator-super-private-" + SECRET_A,
            "RTM_PUBLIC_CASE_ACCESS_SECRET": "public-super-private-" + SECRET_B,
            "RTM_AUTHORITY_SIGNING_SECRET": "authority-super-private-" + SECRET_C,
            "STRIPE_SECRET_KEY": "sk_test_" + SECRET_A,
            "STRIPE_WEBHOOK_SECRET": "whsec_" + SECRET_B,
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
