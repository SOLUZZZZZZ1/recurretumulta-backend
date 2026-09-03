from __future__ import annotations

import unittest

from rtm_core.operator_lifecycle_policy import (
    OperatorLifecycleRoutesDisabled,
    OperatorLifecycleRuntimeMisconfigured,
    load_operator_lifecycle_runtime_config,
)


def _base_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY": "H" * 64,
        "RTM_TRUST_PROXY_HEADERS": "1",
        "RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
        "RTM_OPERATOR_ACCESS_RETENTION_DAYS": "180",
        "RTM_ENABLE_OPERATOR_ADMIN_V1": "1",
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1": "0",
    }


class OperatorLifecyclePolicyTest(unittest.TestCase):
    def test_lifecycle_is_disabled_by_default(self):
        config = load_operator_lifecycle_runtime_config(
            _base_env(),
            require_enabled=False,
        )
        self.assertFalse(config.enabled)
        self.assertFalse(config.available)

    def test_require_enabled_fails_closed(self):
        with self.assertRaises(OperatorLifecycleRoutesDisabled):
            load_operator_lifecycle_runtime_config(_base_env())

    def test_enabled_staging_is_available(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_LIFECYCLE_V1"] = "1"
        config = load_operator_lifecycle_runtime_config(env)
        self.assertTrue(config.available)

    def test_enabled_lifecycle_requires_admin(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_LIFECYCLE_V1"] = "1"
        env["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "0"
        with self.assertRaises(OperatorLifecycleRuntimeMisconfigured):
            load_operator_lifecycle_runtime_config(env)

    def test_lifecycle_refuses_outside_staging(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_LIFECYCLE_V1"] = "1"
        env["RTM_ENV"] = "production"
        with self.assertRaises(OperatorLifecycleRuntimeMisconfigured):
            load_operator_lifecycle_runtime_config(env)

    def test_invalid_flag_fails_closed(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_LIFECYCLE_V1"] = "perhaps"
        with self.assertRaises(OperatorLifecycleRuntimeMisconfigured):
            load_operator_lifecycle_runtime_config(
                env,
                require_enabled=False,
            )


if __name__ == "__main__":
    unittest.main()
