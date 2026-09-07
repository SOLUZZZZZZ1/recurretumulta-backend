from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import cases
from rtm_core.trusted_origins import trusted_frontend_origin


STAGING_BRANCH_HOST = (
    "recurretumulta-frontendweb3-8-26-git-r-cbbb3a-soluzzzs-projects.vercel.app"
)
STAGING_BRANCH_ORIGIN = f"https://{STAGING_BRANCH_HOST}"


class CaseNotificationLinkSecurityTest(unittest.TestCase):
    def test_email_link_exposes_route_to_host_but_keeps_bearer_in_fragment(self):
        case_id = "11111111-1111-1111-1111-111111111111"
        token = f"v2.1735689600.{'b' * 32}.{'a' * 64}"
        for environment, origin in (
            ("production", "https://www.recurretumulta.eu"),
            ("staging", STAGING_BRANCH_ORIGIN),
        ):
            with (
                self.subTest(origin=origin),
                patch.dict(
                    os.environ,
                    {"RTM_ENV": environment, "FRONTEND_URL": f"{origin}/"},
                    clear=True,
                ),
                patch.object(cases, "issue_case_access_token", return_value=token),
            ):
                link = cases._case_link(case_id)

                self.assertEqual(
                    link,
                    f"{origin}/resumen?case={case_id}#access_token={token}",
                )
                request_target, fragment = link.split("#", 1)
                self.assertNotIn("/#/", link)
                self.assertNotIn(token, request_target)
                self.assertEqual(fragment, f"access_token={token}")

    def test_trusted_hosts_preserve_https_origin_canonicalization(self):
        for host in (
            "recurretumulta.eu",
            "www.recurretumulta.eu",
            "recurretumulta.vercel.app",
            "staging.recurretumulta.eu",
            STAGING_BRANCH_HOST,
        ):
            with self.subTest(host=host), patch.dict(
                os.environ,
                {"RTM_ENV": "staging", "FRONTEND_URL": f"https://{host.upper()}:443/"},
                clear=True,
            ):
                self.assertEqual(trusted_frontend_origin(), f"https://{host}")

    def test_staging_hosts_cannot_receive_production_links(self):
        for origin in ("https://staging.recurretumulta.eu", STAGING_BRANCH_ORIGIN):
            with self.subTest(origin=origin), patch.dict(
                os.environ,
                {"RTM_ENV": "production", "FRONTEND_URL": origin},
                clear=True,
            ):
                with self.assertRaises(RuntimeError):
                    trusted_frontend_origin()

    def test_staging_branch_origin_rejects_lookalikes_and_non_origin_urls(self):
        for origin in (
            STAGING_BRANCH_ORIGIN.replace("cbbb3a", "cbbb3b"),
            STAGING_BRANCH_ORIGIN.replace("soluzzzs-projects", "other-projects"),
            f"{STAGING_BRANCH_ORIGIN}.attacker.example",
            "https://*.vercel.app",
            f"http://{STAGING_BRANCH_HOST}",
            f"https://user:password@{STAGING_BRANCH_HOST}",
            f"{STAGING_BRANCH_ORIGIN}:444",
            f"{STAGING_BRANCH_ORIGIN}/path",
            f"{STAGING_BRANCH_ORIGIN}?redirect=attacker",
            f"{STAGING_BRANCH_ORIGIN}#fragment",
        ):
            with self.subTest(origin=origin), patch.dict(
                os.environ,
                {"RTM_ENV": "staging", "FRONTEND_URL": origin},
                clear=True,
            ):
                with self.assertRaises(RuntimeError):
                    trusted_frontend_origin()

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
