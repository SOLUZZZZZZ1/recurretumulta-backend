from __future__ import annotations

from datetime import datetime, timezone
import unittest

from rtm_core.workspace_router import WORKSPACE_VERSION, determine_workspace_stage


NOW = datetime.now(timezone.utc)
CASE_ID = "case-workspace-1"
BASE = f"/ops/core/cases/{CASE_ID}"


def _facts(*, frozen: bool = True):
    return {
        "id": "facts-1",
        "frozen": frozen,
        "invalidated_at": None,
    }


def _family(*, status: str = "resolved", locked: bool = True):
    return {
        "id": "family-1",
        "locked": locked,
        "invalidated_at": None,
        "resolution": {"status": status},
    }


def _preview(status: str):
    return {"id": "preview-1", "status": status}


def _resource(*, approved: bool):
    return {
        "id": "resource-1",
        "status": "final_ready",
        "approved_at": NOW if approved else None,
    }


def _stage(**updates):
    payload = {
        "case_id": CASE_ID,
        "case_status": "core_review_pending",
        "payment_status": "paid",
        "authorized": True,
        "readiness_ready": True,
        "reanalysis_available": True,
        "latest_facts": None,
        "latest_family": None,
        "latest_preview": None,
        "latest_resource": None,
        "service": "traffic",
        "specialist_available": True,
    }
    payload.update(updates)
    return determine_workspace_stage(**payload)


class WorkspaceProgressionTest(unittest.TestCase):
    def test_version_and_route_are_explicit(self):
        self.assertEqual(WORKSPACE_VERSION, "rtm_ops_workspace_v1_2")
        import app

        paths = {getattr(route, "path", "") for route in app.app.routes}
        self.assertIn(f"{BASE}/workspace".replace(CASE_ID, "{case_id}"), paths)

    def test_intake_and_payment_stages(self):
        result = _stage(
            payment_status="",
            authorized=False,
            readiness_ready=False,
            reanalysis_available=False,
        )
        self.assertEqual(result["stage"], "intake_incomplete")
        self.assertEqual(result["primary_action"], "complete_intake")

        result = _stage(
            payment_status="",
            authorized=True,
            readiness_ready=True,
        )
        self.assertEqual(result["stage"], "study_payment_pending")
        self.assertEqual(result["primary_action"], "collect_study_payment")

    def test_authorization_has_priority_after_payment(self):
        result = _stage(authorized=False)
        self.assertEqual(result["stage"], "authorization_required")

    def test_reanalysis_and_facts_progression(self):
        result = _stage(reanalysis_available=False)
        self.assertEqual(result["stage"], "reanalysis_required")
        self.assertEqual(result["primary_action"], "run_safe_reanalysis")
        self.assertEqual(
            result["actions"][0]["endpoint"],
            f"{BASE}/reanalysis/run",
        )

        result = _stage()
        self.assertEqual(result["stage"], "validated_facts_pending")
        endpoints = {action["endpoint"] for action in result["actions"]}
        self.assertIn(f"{BASE}/reanalysis/facts-preview", endpoints)
        self.assertIn(f"{BASE}/reanalysis/facts-draft", endpoints)

        result = _stage(latest_facts=_facts(frozen=False))
        self.assertEqual(result["stage"], "validated_facts_review")
        self.assertEqual(result["primary_action"], "review_validated_facts")
        freeze = next(action for action in result["actions"] if action["code"] == "freeze_validated_facts")
        self.assertEqual(freeze["endpoint"], f"{BASE}/validated-facts/facts-1/freeze")

    def test_family_progression(self):
        result = _stage(latest_facts=_facts())
        self.assertEqual(result["stage"], "family_resolution_pending")
        self.assertEqual(result["actions"][0]["endpoint"], f"{BASE}/resolve-family")

        result = _stage(
            latest_facts=_facts(),
            latest_family=_family(status="conflicted", locked=False),
        )
        self.assertEqual(result["stage"], "family_operator_review")
        self.assertEqual(result["primary_action"], "review_family_conflict")

        result = _stage(
            latest_facts=_facts(),
            latest_family=_family(locked=False),
        )
        self.assertEqual(result["stage"], "family_lock_pending")
        self.assertEqual(
            result["actions"][0]["endpoint"],
            f"{BASE}/family-resolutions/family-1/lock",
        )

    def test_preview_progression(self):
        authority = {"latest_facts": _facts(), "latest_family": _family()}

        result = _stage(**authority)
        self.assertEqual(result["stage"], "legal_preview_pending")
        self.assertEqual(result["actions"][0]["endpoint"], f"{BASE}/build-legal-preview")

        result = _stage(
            **authority,
            specialist_available=False,
            service="debt",
        )
        self.assertEqual(result["stage"], "initial_direction_review")
        self.assertEqual(result["primary_action"], "review_first_direction")

        result = _stage(**authority, latest_preview=_preview("draft"))
        self.assertEqual(result["stage"], "legal_preview_draft")
        self.assertEqual(
            result["primary_action"],
            "submit_preview_review",
        )

        result = _stage(**authority, latest_preview=_preview("ops_review"))
        self.assertEqual(result["stage"], "legal_preview_ops_review")
        action_codes = {action["code"] for action in result["actions"]}
        self.assertEqual(action_codes, {"approve_preview", "request_preview_changes"})

        result = _stage(**authority, latest_preview=_preview("approved"))
        self.assertEqual(result["stage"], "legal_preview_freeze_pending")
        self.assertEqual(
            result["actions"][0]["endpoint"],
            f"{BASE}/legal-previews/preview-1/freeze",
        )

    def test_generate_and_submission_progression(self):
        authority = {
            "latest_facts": _facts(),
            "latest_family": _family(),
            "latest_preview": _preview("frozen"),
        }

        result = _stage(**authority)
        self.assertEqual(result["stage"], "generate_pending")
        self.assertEqual(
            result["actions"][0]["endpoint"],
            f"{BASE}/legal-previews/preview-1/generate",
        )

        result = _stage(**authority, latest_resource=_resource(approved=False))
        self.assertEqual(result["stage"], "resource_approval_pending")
        self.assertEqual(
            result["actions"][0]["endpoint"],
            f"{BASE}/generated-resources/resource-1/approve-submission",
        )

        result = _stage(
            **authority,
            latest_resource=_resource(approved=True),
            case_status="ready_to_submit",
        )
        self.assertEqual(result["stage"], "presentation_ready")

    def test_submitted_case_never_returns_to_presentation_ready(self):
        result = _stage(
            latest_facts=_facts(),
            latest_family=_family(),
            latest_preview=_preview("frozen"),
            latest_resource=_resource(approved=True),
            case_status="submitted",
        )
        self.assertEqual(result["stage"], "submitted_followup")
        self.assertEqual(result["primary_action"], "monitor_followup")


if __name__ == "__main__":
    unittest.main()
