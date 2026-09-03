import unittest

from rtm_core.readiness import evaluate_review_readiness


BASE_DATA = {
    "full_name": "Ramón Ejemplo",
    "dni_nie": "12345678Z",
    "domicilio_notif": "Calle Mayor 1, Manresa",
    "email": "ramon@example.com",
    "telefono": "600000000",
    "customer_comment": "He recibido una notificación y quiero que se revise.",
}


class ReviewReadinessTest(unittest.TestCase):
    def test_complete_rtm_web_case_is_ready(self):
        result = evaluate_review_readiness(
            case_id="case-1",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=[
                "identity_front",
                "identity_back",
                "authorization_signed",
                "original",
            ],
            department="traffic",
            case_type="fine",
            source_module="rtm_web",
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.blocking_issues, [])
        self.assertEqual(result.quote.amount_cents, 1000)

    def test_admin_quote_is_authoritative(self):
        result = evaluate_review_readiness(
            case_id="case-2",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=[
                "identity_front",
                "identity_back",
                "authorization_signed",
                "original",
            ],
            department="administration",
            case_type="aeat",
            source_module="rtm_web",
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.quote.billing_code, "ADMIN_REVIEW")
        self.assertEqual(result.quote.amount_cents, 2500)

    def test_authorized_flag_without_signed_document_is_blocked(self):
        result = evaluate_review_readiness(
            case_id="case-3",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=["identity_front", "identity_back", "original"],
            department="debt",
            case_type="asnef_equifax",
            source_module="rtm_web",
        )
        self.assertFalse(result.ready)
        codes = {issue.code for issue in result.blocking_issues}
        self.assertIn("authorization_signed", codes)

    def test_unreviewed_signature_candidate_cannot_enable_checkout(self):
        result = evaluate_review_readiness(
            case_id="case-candidate",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=[
                "identity_front",
                "identity_back",
                "authorization_signed_candidate",
                "original",
            ],
            department="traffic",
            case_type="fine",
            source_module="rtm_web",
        )
        self.assertFalse(result.ready)
        self.assertIn(
            "authorization_signed",
            {issue.code for issue in result.blocking_issues},
        )

    def test_missing_main_document_is_blocked(self):
        result = evaluate_review_readiness(
            case_id="case-4",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=["identity_front", "identity_back", "authorization_signed"],
            department="claims",
            case_type="airline",
            source_module="rtm_web",
        )
        self.assertFalse(result.ready)
        self.assertIn("main_document", {issue.code for issue in result.blocking_issues})

    def test_core_intake_requires_identity_both_sides(self):
        result = evaluate_review_readiness(
            case_id="case-5",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=["authorization_signed", "original"],
            department="claims",
            case_type="consumer",
            source_module="rtm_web",
        )
        self.assertFalse(result.ready)
        codes = {issue.code for issue in result.blocking_issues}
        self.assertIn("identity_front", codes)
        self.assertIn("identity_back", codes)

    def test_legacy_identity_gap_warns_without_blocking(self):
        result = evaluate_review_readiness(
            case_id="case-6",
            interested_data=BASE_DATA,
            authorized=True,
            document_kinds=["authorization_signed", "original"],
            department="traffic",
            case_type="fine",
            source_module="",
        )
        self.assertTrue(result.ready)
        warning_codes = {issue.code for issue in result.warnings}
        self.assertIn("identity_front", warning_codes)
        self.assertIn("identity_back", warning_codes)

    def test_missing_explanation_blocks_new_core_intake(self):
        data = dict(BASE_DATA)
        data.pop("customer_comment")
        result = evaluate_review_readiness(
            case_id="case-7",
            interested_data=data,
            authorized=True,
            document_kinds=[
                "identity_front",
                "identity_back",
                "authorization_signed",
                "original",
            ],
            department="traffic",
            case_type="fine",
            source_module="rtm_web",
        )
        self.assertFalse(result.ready)
        self.assertIn("customer_comment", {issue.code for issue in result.blocking_issues})


if __name__ == "__main__":
    unittest.main()
