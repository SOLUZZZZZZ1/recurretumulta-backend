from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from rtm_core import operator_auth_service as service
from rtm_core.operator_auth_request import (
    OperatorAuthRuntimeConfig,
    RequestFingerprint,
)


class OperatorLoginEnumerationSecurityTest(unittest.TestCase):
    def test_locked_and_unknown_accounts_share_the_public_failure_contract(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        config = OperatorAuthRuntimeConfig(
            environment="staging",
            enabled=True,
            trust_proxy_headers=False,
            hmac_key="H" * 64,
            evidence_retention_days=180,
        )
        context = RequestFingerprint(
            request_id="request-id",
            ip_address="203.0.113.10",
            ip_masked="203.0.113.xxx",
            ip_hash_sha256="a" * 64,
            ip_family=4,
            ip_source="direct",
            ip_trusted=True,
            raw_user_agent=None,
            user_agent_summary=None,
            device_type="unknown",
            os_family=None,
            os_version=None,
            browser_family=None,
            browser_version=None,
            country_code=None,
            region=None,
            city=None,
            timezone=None,
            location_source=None,
            trusted_headers={},
            risk_flags=(),
        )
        locked_operator = {
            "id": "11111111-1111-4111-8111-111111111111",
            "email": "known@example.test",
            "display_name": "Known",
            "password_hash": "$argon2id$synthetic",
            "status": "active",
            "must_change_password": False,
            "mfa_required": False,
            "failed_login_count": 5,
            "locked_until": now + timedelta(minutes=10),
            "auth_epoch": 1,
            "role_code": "rtm.operator",
            "permissions": ["ops.view"],
        }

        outcomes = []
        for row in (None, locked_operator):
            with (
                patch.object(service, "find_operator_for_login", return_value=row),
                patch.object(service, "verify_operator_password") as verify,
                patch.object(service, "record_operator_access_event"),
            ):
                outcomes.append(
                    service.login_operator(
                        Mock(),
                        email="candidate@example.test",
                        password="wrong-password",
                        device_token=None,
                        context=context,
                        config=config,
                        now=now,
                    )
                )
                verify.assert_called_once()

        self.assertEqual(
            [(item.status_code, item.detail, item.retry_after) for item in outcomes],
            [
                (401, "Credenciales no válidas", None),
                (401, "Credenciales no válidas", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
