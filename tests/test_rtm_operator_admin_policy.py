from __future__ import annotations

import unittest
from dataclasses import dataclass

from rtm_core.operator_admin_policy import (
    OperatorAdminRoutesDisabled,
    OperatorAdminRuntimeMisconfigured,
    load_operator_admin_runtime_config,
    session_has_supervisor_permission,
)


def _base_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY": "H" * 64,
        "RTM_TRUST_PROXY_HEADERS": "1",
        "RTM_OPERATOR_ACCESS_RETENTION_DAYS": "180",
        "RTM_ENABLE_OPERATOR_ADMIN_V1": "0",
    }


@dataclass(frozen=True)
class _Session:
    permissions: tuple[str, ...]


class OperatorAdminPolicyTest(unittest.TestCase):
    def test_admin_is_disabled_by_default(self):
        config = load_operator_admin_runtime_config(
            _base_env(),
            require_enabled=False,
        )
        self.assertFalse(config.enabled)
        self.assertFalse(config.available)

    def test_require_enabled_fails_closed(self):
        with self.assertRaises(OperatorAdminRoutesDisabled):
            load_operator_admin_runtime_config(_base_env())

    def test_enabled_staging_requires_active_auth(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "1"
        config = load_operator_admin_runtime_config(env)
        self.assertTrue(config.available)

        env["RTM_ENABLE_OPERATOR_AUTH_V1"] = "0"
        with self.assertRaises(OperatorAdminRuntimeMisconfigured):
            load_operator_admin_runtime_config(env)

    def test_admin_refuses_outside_staging(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "1"
        env["RTM_ENV"] = "production"
        with self.assertRaises(OperatorAdminRuntimeMisconfigured):
            load_operator_admin_runtime_config(env)

    def test_invalid_flag_fails_closed(self):
        env = _base_env()
        env["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "perhaps"
        with self.assertRaises(OperatorAdminRuntimeMisconfigured):
            load_operator_admin_runtime_config(
                env,
                require_enabled=False,
            )

    def test_supervisor_requires_explicit_permission(self):
        self.assertTrue(
            session_has_supervisor_permission(
                _Session(("ops.view", "ops.supervise"))
            )
        )
        self.assertFalse(
            session_has_supervisor_permission(
                _Session(("ops.view",))
            )
        )


if __name__ == "__main__":
    unittest.main()
