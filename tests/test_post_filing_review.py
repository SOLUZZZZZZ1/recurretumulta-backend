from types import SimpleNamespace
from unittest.mock import patch
import unittest
import uuid
from fastapi import HTTPException, Request
from pydantic import ValidationError
from rtm_core.post_filing_review import CalendarReview, DeadlineReviewBody


def body(**changes):
    return {"expected_source_sha256": "a" * 64, "expected_review_id": None,
            "rule_checked": False, "calendar": None, "procedural_status": "pending",
            "notes": "Falta contrastar el calendario del expediente ficticio.", "attested": True, **changes}


class DeadlineReviewTests(unittest.TestCase):
    def test_review_requires_personal_attestation_and_rejects_client_due_or_actor(self):
        for changes in ({"attested": False}, {"attested": "true"}, {"legal_due_on": "2027-09-17"},
                        {"actor": "operator:forged"}, {"notes": ""}, {"procedural_status": "approved"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                DeadlineReviewBody.model_validate(body(**changes))
        self.assertIsNone(DeadlineReviewBody.model_validate(body()).calendar)

    def test_calendar_requires_source_scope_valid_dates_and_bounded_unique_holidays(self):
        values = dict(from_date="2027-09-01", to_date="2027-09-30", holidays=["2027-09-17"],
                      source="Calendario ficticio para prueba", territory="Municipio ficticio")
        self.assertEqual(CalendarReview(**values).holidays, ["2027-09-17"])
        for changes in ({"from_date": "2027-02-30"}, {"to_date": "2027-08-30"},
                        {"holidays": ["2027-10-01"]}, {"holidays": ["2027-09-17"] * 2},
                        {"source": ""}, {"territory": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                CalendarReview(**{**values, **changes})

    def test_route_rejects_legacy_operator_and_missing_permission_before_database_access(self):
        import ops_operator_router as router
        request = Request({"type": "http"})
        for individual, role, permissions in [(False, "rtm.supervisor", ("ops.supervise",)),
                (True, "rtm.operator", ("ops.supervise",)), (True, "rtm.supervisor", ())]:
            scope = SimpleNamespace(individual_session=individual, role_code=role, permissions=permissions)
            with patch.object(router, "require_operator_token"), patch.object(router, "load_ops_case_scope", return_value=scope), \
                 patch.object(router, "get_engine") as engine, self.assertRaises(HTTPException) as denied:
                router.review_case_deadlines(str(uuid.uuid4()), DeadlineReviewBody(**body()), request, "test")
            self.assertEqual(denied.exception.status_code, 403)
            engine.assert_not_called()

    def test_route_requires_trusted_identity_matching_scope(self):
        import ops_operator_router as router
        request = Request({"type": "http"})
        operator_id = str(uuid.uuid4())
        scope = SimpleNamespace(individual_session=True, role_code="rtm.supervisor",
                                permissions=("ops.supervise",), operator_id=operator_id)
        for context in (None, SimpleNamespace(operator_id=operator_id, session_id=str(uuid.uuid4()), actor="operator:forged"),
                SimpleNamespace(operator_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), actor="operator:" + operator_id)):
            request.state.rtm_operator_context = context
            with patch.object(router, "require_operator_token"), patch.object(router, "load_ops_case_scope", return_value=scope), \
                 patch.object(router, "get_engine") as engine, self.assertRaises(HTTPException) as denied:
                router.review_case_deadlines(str(uuid.uuid4()), DeadlineReviewBody(**body()), request, "test")
            self.assertEqual(denied.exception.status_code, 403)
            engine.assert_not_called()

if __name__ == "__main__": unittest.main()
