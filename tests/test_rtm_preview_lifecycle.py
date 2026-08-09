from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from rtm_core.contracts import LegalPreview, PreviewStatus
from rtm_core.preview_repository import preview_digest, validated_preview_copy
from rtm_core.security import normalized_actor


NOW = datetime.now(timezone.utc)


def _draft() -> LegalPreview:
    return LegalPreview(
        case_id="case-1",
        service="traffic",
        family="temeraria",
        specialist="traffic.temeraria",
        facts_version="facts-v1",
        family_resolution_version="family-v1",
        status=PreviewStatus.DRAFT,
        validated_facts_summary=["Hecho acreditado"],
        primary_strategy="Insuficiencia probatoria específica",
        requested_outcomes=["Archivo"],
        created_by_component="traffic.temeraria",
    )


class LegalPreviewLifecycleTest(unittest.TestCase):
    def test_digest_is_deterministic(self):
        first = _draft()
        second = LegalPreview.model_validate(first.model_dump(mode="python"))
        self.assertEqual(preview_digest(first), preview_digest(second))

    def test_state_change_changes_digest(self):
        draft = _draft()
        review = validated_preview_copy(draft, status=PreviewStatus.OPS_REVIEW)
        self.assertNotEqual(preview_digest(draft), preview_digest(review))

    def test_approved_requires_operator_identity(self):
        with self.assertRaises(ValidationError):
            validated_preview_copy(_draft(), status=PreviewStatus.APPROVED)

    def test_frozen_preview_keeps_approval_and_freeze(self):
        approved = validated_preview_copy(
            _draft(),
            status=PreviewStatus.APPROVED,
            approved_by="ops:ramon",
            approved_at=NOW,
        )
        frozen = validated_preview_copy(
            approved,
            status=PreviewStatus.FROZEN,
            frozen_at=NOW,
        )
        self.assertEqual(frozen.status, PreviewStatus.FROZEN)
        self.assertEqual(frozen.approved_by, "ops:ramon")

    def test_actor_is_sanitized(self):
        self.assertEqual(normalized_actor(" Ramón / OPS "), "Ram_n_OPS")
        self.assertEqual(normalized_actor(""), "ops:operator")


if __name__ == "__main__":
    unittest.main()
