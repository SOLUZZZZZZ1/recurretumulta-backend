from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rtm_connect" / "supervisor_policy.py"


@dataclass(frozen=True)
class _Session:
    permissions: tuple[str, ...]


class _ScalarResult:
    def __init__(self, value: str):
        self.value = value

    def scalar_one(self) -> str:
        return self.value


class _DatabaseConnection:
    def __init__(self, database_name: str):
        self.database_name = database_name

    def exec_driver_sql(self, statement: str) -> _ScalarResult:
        if statement != "SELECT current_database()":
            raise AssertionError(statement)
        return _ScalarResult(self.database_name)


def _load_policy_module():
    dependency_name = "rtm_core.operator_auth_request"
    previous_dependency = sys.modules.get(dependency_name)
    dependency = types.ModuleType(dependency_name)

    class OperatorAuthRuntimeConfig:
        pass

    class OperatorAuthRuntimeMisconfigured(RuntimeError):
        pass

    def load_operator_auth_runtime_config(source, *, require_enabled=False):
        del require_enabled
        raw = str(source.get("RTM_ENABLE_OPERATOR_AUTH_V1") or "")
        enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
        return types.SimpleNamespace(enabled=enabled, available=enabled)

    dependency.OperatorAuthRuntimeConfig = OperatorAuthRuntimeConfig
    dependency.OperatorAuthRuntimeMisconfigured = (
        OperatorAuthRuntimeMisconfigured
    )
    dependency.load_operator_auth_runtime_config = (
        load_operator_auth_runtime_config
    )
    sys.modules[dependency_name] = dependency

    module_name = "_rtm_connect_c5_supervisor_policy_under_test"
    spec = importlib.util.spec_from_file_location(module_name, POLICY)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar supervisor_policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous_dependency is None:
            sys.modules.pop(dependency_name, None)
        else:
            sys.modules[dependency_name] = previous_dependency
    return module


def _safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_INSTANCE_ID": "rtm-staging",
        "RTM_DATA_NAMESPACE": "rtm-staging-c5",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_staging"
        ),
        "FRONTEND_URL": "https://staging.recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://staging.recurretumulta.eu",
        "OPERATOR_TOKEN": "op_" + ("x" * 48),
        "RTM_PUBLIC_CASE_ACCESS_SECRET": "case_" + ("c" * 48),
        "RTM_AUTHORITY_SIGNING_SECRET": "authority_" + ("a" * 48),
        "RTM_EXPECTED_BRANCH": "rtm-core-consolidation-2026-08-08",
        "RENDER_GIT_BRANCH": "rtm-core-consolidation-2026-08-08",
        "RENDER_SERVICE_NAME": "rtm-staging-backend",
        "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
        "RTM_ENABLE_CONNECT_SUPERVISOR_V1": "1",
    }


class ConnectC5SupervisorPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = _load_policy_module()

    def test_feature_is_disabled_by_default_and_fails_closed(self):
        env = _safe_env()
        env.pop("RTM_ENABLE_CONNECT_SUPERVISOR_V1")
        config = self.policy.load_connect_supervisor_runtime_config(
            env,
            require_enabled=False,
        )
        self.assertFalse(config.enabled)
        self.assertFalse(config.available)
        with self.assertRaises(self.policy.ConnectSupervisorRoutesDisabled):
            self.policy.load_connect_supervisor_runtime_config(env)

    def test_safe_staging_configuration_is_available(self):
        config = self.policy.load_connect_supervisor_runtime_config(
            _safe_env()
        )
        self.assertTrue(config.enabled)
        self.assertTrue(config.available)
        self.assertEqual(config.environment, "staging")
        self.assertEqual(config.side_effect_policy, "isolated")

    def test_unsafe_environment_dimensions_fail_closed(self):
        cases = (
            ("RTM_ENV", "production"),
            ("RTM_ENVIRONMENT_CONFIRMATION", "RTM_PRODUCTION_LIVE"),
            ("RTM_INSTANCE_ID", "rtm-production-staging"),
            ("RTM_DATA_NAMESPACE", "rtm-production"),
            (
                "DATABASE_URL",
                "postgresql+psycopg://rtm:password@db.internal/rtm_production",
            ),
            ("ALLOWED_ORIGINS", "*"),
            ("RTM_DOCUMENT_INPUT_POLICY", "customer_documents"),
            ("RTM_EXPECTED_BRANCH", "main"),
            ("RTM_SIDE_EFFECT_POLICY", "enabled"),
            ("RTM_ALLOW_REAL_CUSTOMER_DATA", "1"),
            ("RTM_ENABLE_B2", "1"),
            ("RTM_ENABLE_DOCUMENT_PROVIDER", "1"),
            ("RTM_ENABLE_EXTERNAL_SUBMISSION", "1"),
            ("RTM_ENABLE_OUTBOUND_EMAIL", "1"),
            ("RTM_ENABLE_STRIPE", "1"),
            ("RTM_ENABLE_FINAL_PAYMENTS", "1"),
            ("RTM_ENABLE_OPERATOR_AUTH_V1", "0"),
        )
        for name, value in cases:
            with self.subTest(name=name):
                env = _safe_env()
                env[name] = value
                with self.assertRaises(
                    self.policy.ConnectSupervisorRuntimeMisconfigured
                ):
                    self.policy.load_connect_supervisor_runtime_config(env)

    def test_missing_safety_flags_are_not_treated_as_disabled(self):
        for name in (
            "RTM_ALLOW_REAL_CUSTOMER_DATA",
            "RTM_PUBLIC_CASE_ACCESS_SECRET",
            "RTM_AUTHORITY_SIGNING_SECRET",
            "RTM_ENABLE_EXTERNAL_SUBMISSION",
            "RTM_ENABLE_OUTBOUND_EMAIL",
            "RTM_ENABLE_STRIPE",
            "RTM_ENABLE_FINAL_PAYMENTS",
        ):
            with self.subTest(name=name):
                env = _safe_env()
                env.pop(name)
                with self.assertRaises(
                    self.policy.ConnectSupervisorRuntimeMisconfigured
                ):
                    self.policy.load_connect_supervisor_runtime_config(env)

    def test_invalid_boolean_flag_fails_closed(self):
        env = _safe_env()
        env["RTM_ENABLE_CONNECT_SUPERVISOR_V1"] = "perhaps"
        with self.assertRaises(
            self.policy.ConnectSupervisorRuntimeMisconfigured
        ):
            self.policy.load_connect_supervisor_runtime_config(env)

    def test_supervisor_permission_is_explicit(self):
        self.assertEqual(
            self.policy.CONNECT_SUPERVISOR_PERMISSION,
            "ops.supervise",
        )
        self.assertTrue(
            self.policy.session_has_connect_supervisor_permission(
                _Session(("ops.view", "ops.supervise"))
            )
        )
        self.assertFalse(
            self.policy.session_has_connect_supervisor_permission(
                _Session(("ops.view",))
            )
        )

    def test_connected_database_must_equal_declared_staging_database(self):
        actual = self.policy.assert_connect_supervisor_database_identity(
            _DatabaseConnection("rtm_staging"),
            expected_database_name="rtm_staging",
        )
        self.assertEqual(actual, "rtm_staging")
        for connected, expected in (
            ("rtm_production", "rtm_staging"),
            ("rtm_staging", "rtm_other_staging"),
        ):
            with self.subTest(connected=connected, expected=expected):
                with self.assertRaises(
                    self.policy.ConnectSupervisorRuntimeMisconfigured
                ):
                    self.policy.assert_connect_supervisor_database_identity(
                        _DatabaseConnection(connected),
                        expected_database_name=expected,
                    )


if __name__ == "__main__":
    unittest.main()
