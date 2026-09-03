from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminPartnerHttpSurfaceContractTest(unittest.TestCase):
    def test_database_migration_routers_are_not_imported_or_mounted(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        ast.parse(source)
        for router_name in (
            "admin_migrate_router",
            "admin_payments_router",
            "rtm_core_migration_router",
            "rtm_core_document_extraction_migration_router",
        ):
            with self.subTest(router=router_name):
                self.assertNotIn(router_name, source)
        self.assertNotIn("/admin/migrate", source)

    def test_schema_definitions_do_not_publish_dormant_http_routers(self):
        for relative in (
            "rtm_core/migration_router.py",
            "rtm_core/document_extraction_migration.py",
        ):
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                tree = ast.parse(source)
                self.assertNotIn("APIRouter", source)
                self.assertFalse(
                    [
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith("migrate_")
                    ]
                )

    def test_core_security_has_no_shared_admin_http_credential(self):
        source = (ROOT / "rtm_core/security.py").read_text(encoding="utf-8")
        self.assertNotIn("require_admin_token", source)
        self.assertNotIn('"ADMIN_TOKEN"', source)

    def test_partner_admin_has_no_shared_secret_contract(self):
        source = (ROOT / "partner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        admin = functions["admin_create_partner"]
        argument_names = {
            argument.arg for argument in (*admin.args.posonlyargs, *admin.args.args)
        }
        self.assertNotIn("x_admin_token", argument_names)
        self.assertNotIn("ADMIN_TOKEN", source)
        self.assertIn("require_partner_admin_supervisor", source)
        self.assertIn("require_recent_supervisor_context", source)

    def test_partner_session_has_expiry_revocation_cookie_and_csrf_contract(self):
        source = (ROOT / "partner.py").read_text(encoding="utf-8")
        for marker in (
            "RTM_PARTNER_SESSION_TTL_SECONDS",
            "_partner_token_expiration",
            "api_token=NULL",
            '@router.post("/logout")',
            "__Host-rtm_partner_session",
            "httponly=True",
            '"secure": True',
            '"samesite": "lax"',
            "_require_partner_csrf",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_sensitive_partner_routes_have_process_local_rate_limits(self):
        source = (ROOT / "rtm_core" / "http_security.py").read_text(
            encoding="utf-8"
        )
        for path in (
            "/partner/admin-create",
            "/partner/change-password",
            "/partner/login",
            "/partner/logout",
            "/partner/cases",
        ):
            with self.subTest(path=path):
                self.assertIn(f'("POST", "{path}")', source)


if __name__ == "__main__":
    unittest.main()
