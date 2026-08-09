import os
from pathlib import Path
import unittest

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from rtm_core.legacy_guard_router import router as guard_router


class LegacyRouteGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.guard_source = Path("rtm_core/legacy_guard_router.py").read_text(
            encoding="utf-8"
        )

    def test_guard_is_mounted_before_legacy_routers(self):
        guard = self.app_source.index("app.include_router(rtm_core_legacy_guard_router)")
        generate = self.app_source.index("app.include_router(generate_router)")
        operator = self.app_source.index("app.include_router(ops_operator_router)")
        ops = self.app_source.index("app.include_router(ops_router)")
        self.assertLess(guard, generate)
        self.assertLess(guard, operator)
        self.assertLess(guard, ops)

    def test_debug_and_force_override_routers_are_not_mounted(self):
        self.assertNotIn("debug_generate_preview_router", self.app_source)
        self.assertNotIn("debug_test_classifier_router", self.app_source)
        self.assertNotIn("ops_override_router", self.app_source)

    def test_critical_legacy_routes_are_explicitly_blocked(self):
        for route in (
            '"/generate/dgt"',
            '"/ops/cases/{case_id}/reanalyze"',
            '"/ops/cases/{case_id}/save-ai-overrides"',
            '"/ops/cases/{case_id}/approve"',
            '"/ops/cases/{case_id}/override-family"',
            '"/ops/cases/{case_id}/override-family-and-regenerate"',
            '"/ops/cases/{case_id}/rewrite-hecho-and-regenerate"',
            '"/ops/cases/{case_id}/submit"',
            '"/ops/cases/{case_id}/mark-submitted"',
            '"/ops/cases/{case_id}/force-ready-to-submit"',
            '"/ops/cases/{case_id}/lab-force-authorize"',
            '"/ops/cases/{case_id}/lab-force-paid"',
            '"/ops/cases/{case_id}/force-generate"',
        ):
            self.assertIn(route, self.guard_source)

    def test_guard_wins_route_resolution(self):
        app = FastAPI()
        app.include_router(guard_router)

        legacy = APIRouter()

        @legacy.post("/generate/dgt")
        def legacy_generate():
            return {"unsafe": True}

        @legacy.post("/ops/cases/{case_id}/approve")
        def legacy_approve(case_id: str):
            return {"unsafe": True, "case_id": case_id}

        app.include_router(legacy)
        client = TestClient(app)

        response = client.post("/generate/dgt", json={"case_id": "case-1"})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.json()["detail"]["code"],
            "RTM_LEGACY_ROUTE_DISABLED",
        )

        previous = os.environ.get("OPERATOR_TOKEN")
        os.environ["OPERATOR_TOKEN"] = "operator-test-token"
        try:
            unauthorized = client.post("/ops/cases/case-1/approve", json={})
            self.assertEqual(unauthorized.status_code, 401)

            blocked = client.post(
                "/ops/cases/case-1/approve",
                json={},
                headers={"X-Operator-Token": "operator-test-token"},
            )
            self.assertEqual(blocked.status_code, 410)
            self.assertEqual(
                blocked.json()["detail"]["code"],
                "RTM_LEGACY_ROUTE_DISABLED",
            )
        finally:
            if previous is None:
                os.environ.pop("OPERATOR_TOKEN", None)
            else:
                os.environ["OPERATOR_TOKEN"] = previous


if __name__ == "__main__":
    unittest.main()
