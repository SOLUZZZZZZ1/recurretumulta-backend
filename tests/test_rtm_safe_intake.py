from pathlib import Path
import unittest

from fastapi import APIRouter, FastAPI, HTTPException

from rtm_core.intake_router import MAX_FILE_BYTES, _validate_upload, router as intake_router


class SafeIntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = Path("app.py").read_text(encoding="utf-8")
        cls.source = Path("rtm_core/intake_router.py").read_text(encoding="utf-8")

    def test_safe_intake_is_mounted_before_legacy_cases(self):
        safe = self.app_source.index("app.include_router(rtm_core_intake_router)")
        legacy = self.app_source.index("app.include_router(cases_router)")
        self.assertLess(safe, legacy)

    def test_safe_intake_does_not_import_legacy_intelligence(self):
        self.assertNotIn("run_expediente_ai", self.source)
        self.assertNotIn("analyze_existing_case_document", self.source)
        self.assertNotIn("from scoring import", self.source)
        self.assertNotIn("ai.infractions.dispatch", self.source)
        self.assertIn("analysis_deferred", self.source)
        self.assertIn("classification_deferred", self.source)

    def test_public_projection_does_not_return_pii_or_extraction(self):
        self.assertIn('"privacy_projection": "rtm_public_status_v1_0"', self.source)
        self.assertNotIn('"contact_name":', self.source)
        self.assertNotIn('"contact_email":', self.source)
        self.assertNotIn('"interested_data":', self.source)
        self.assertNotIn('"extracted":', self.source)

    def test_upload_limit_and_extension_are_enforced(self):
        with self.assertRaises(HTTPException) as too_large:
            _validate_upload(
                "documento.pdf",
                "application/pdf",
                b"x" * (MAX_FILE_BYTES + 1),
            )
        self.assertEqual(too_large.exception.status_code, 413)

        with self.assertRaises(HTTPException) as bad_type:
            _validate_upload("programa.exe", "application/octet-stream", b"x")
        self.assertEqual(bad_type.exception.status_code, 415)

        extension = _validate_upload(
            "documento.tiff",
            "image/tiff",
            b"contenido",
        )
        self.assertEqual(extension, ".tiff")

    def test_safe_route_precedes_dummy_legacy_route(self):
        app = FastAPI()
        app.include_router(intake_router)
        legacy = APIRouter(prefix="/cases")

        @legacy.get("/{case_id}/public-status")
        def legacy_public_status(case_id: str):
            return {"unsafe": True, "case_id": case_id}

        app.include_router(legacy)
        matches = [
            route
            for route in app.routes
            if getattr(route, "path", None) == "/cases/{case_id}/public-status"
        ]
        self.assertGreaterEqual(len(matches), 2)
        self.assertEqual(matches[0].endpoint.__name__, "public_status_core")


if __name__ == "__main__":
    unittest.main()
