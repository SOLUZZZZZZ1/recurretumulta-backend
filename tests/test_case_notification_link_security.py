from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import cases
from rtm_core.trusted_origins import trusted_frontend_origin


class CaseNotificationLinkSecurityTest(unittest.TestCase):
    def test_email_link_exposes_route_to_host_but_keeps_bearer_in_fragment(self):
        case_id = "11111111-1111-1111-1111-111111111111"
        token = f"v2.1735689600.{'b' * 32}.{'a' * 64}"
        with (
            patch.dict(
                os.environ,
                {"FRONTEND_URL": "https://www.recurretumulta.eu/"},
                clear=True,
            ),
            patch.object(cases, "issue_case_access_token", return_value=token),
        ):
            link = cases._case_link(case_id)

        self.assertEqual(
            link,
            f"https://www.recurretumulta.eu/resumen?case={case_id}#access_token={token}",
        )
        request_target, fragment = link.split("#", 1)
        self.assertNotIn("/#/", link)
        self.assertNotIn(token, request_target)
        self.assertEqual(fragment, f"access_token={token}")

    def test_frontend_origin_rejects_hostile_and_legacy_values(self):
        for environment in (
            {"FRONTEND_URL": "https://attacker.example"},
            {
                "FRONTEND_URL": "https://www.recurretumulta.eu",
                "FRONTEND_BASE_URL": "https://attacker.example",
            },
            {"FRONTEND_URL": "https://www.recurretumulta.eu/path"},
            {"FRONTEND_URL": "http://www.recurretumulta.eu"},
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaises(RuntimeError):
                    trusted_frontend_origin()


if __name__ == "__main__":
    unittest.main()
