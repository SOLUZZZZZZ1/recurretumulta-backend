from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RtmPrejudicialAuthorizationIntakeContractTest(unittest.TestCase):
    def test_intake_records_request_as_optional_preference_not_consent(self):
        source = (ROOT / "cases.py").read_text(encoding="utf-8")

        self.assertIn(
            "prejudicial_counsel_requested: bool = Form(False)", source
        )
        self.assertIn(
            '"prejudicial_counsel_requested": bool(', source
        )
        self.assertIn("solicitud informativa, no consentimiento", source)
        self.assertNotIn("if not prejudicial_counsel_requested", source)

    def test_document_two_has_its_own_presenter_purpose(self):
        service = (ROOT / "rtm_presenter_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"prejudicial_authorization"', service)


if __name__ == "__main__":
    unittest.main()
