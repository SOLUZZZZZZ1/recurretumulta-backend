from __future__ import annotations

import ast
import unittest
from collections import Counter, defaultdict
from pathlib import Path

import app as backend_app


class RouteUniquenessSecurityTest(unittest.TestCase):
    def test_only_explicit_fail_closed_guards_may_shadow_a_route(self):
        registrations = defaultdict(list)
        for route in backend_app.app.routes:
            for method in getattr(route, "methods", set()) or set():
                if method in {"HEAD", "OPTIONS"}:
                    continue
                registrations[(method, getattr(route, "path", ""))].append(route)

        for key, routes in registrations.items():
            if len(routes) == 1:
                continue
            with self.subTest(route=key):
                self.assertEqual(len(routes), 2)
                first = routes[0]
                self.assertEqual(
                    getattr(first.endpoint, "__module__", ""),
                    "rtm_core.legacy_guard_router",
                )
                self.assertTrue(getattr(first, "name", "").startswith("block_"))

    def test_partner_case_has_one_registered_router(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertNotIn("partner_cases_router", source)
        self.assertEqual(source.count("app.include_router(partner_router)"), 1)

    def test_no_source_file_declares_duplicate_partner_case_handler(self):
        declarations = []
        for path in Path(".").glob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except SyntaxError:
                # Hay artefactos legacy no importados; este contrato solo
                # inventaría rutas si el módulo pudiera cargarse.
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not decorator.args:
                        continue
                    function = decorator.func
                    if (
                        isinstance(function, ast.Attribute)
                        and function.attr == "post"
                        and isinstance(decorator.args[0], ast.Constant)
                        and decorator.args[0].value == "/cases"
                    ):
                        declarations.append((str(path), node.name))
        counts = Counter(declarations)
        self.assertEqual(sum(counts.values()), 1, declarations)


if __name__ == "__main__":
    unittest.main()
