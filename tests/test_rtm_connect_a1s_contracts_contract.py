from __future__ import annotations

import dataclasses
import unittest
from uuid import UUID

from rtm_connect.human_filing_contracts import (
    ArtifactKind,
    HUMAN_FILING_CONTRACT_VERSION,
    HUMAN_FILING_MARKER,
    RTM_CONNECT_A1S_CONTRACTS_VERSION,
    HumanFilingCaseBinding,
    HumanFilingContractError,
    HumanFilingTaskStatus,
    HumanFilingTransitionError,
    RepresentationKind,
    canonical_sha256,
    validate_human_filing_transition,
)


class ConnectA1SContractsContractTest(unittest.TestCase):
    def test_versions_and_marker_are_frozen(self):
        self.assertEqual(
            RTM_CONNECT_A1S_CONTRACTS_VERSION,
            "rtm_connect_a1s_human_filing_contracts_v1_0",
        )
        self.assertEqual(
            HUMAN_FILING_CONTRACT_VERSION,
            "rtm.connect.a1s.human_filing.v1",
        )
        self.assertEqual(HUMAN_FILING_MARKER, "RTM_A1S_SYNTHETIC_ONLY")

    def test_status_machine_has_unknown_reconciliation_without_retry(self):
        values = {item.value for item in HumanFilingTaskStatus}
        self.assertIn("outcome_unknown", values)
        self.assertIn("reconciling", values)
        self.assertEqual(
            validate_human_filing_transition("outcome_unknown", "reconciling"),
            HumanFilingTaskStatus.RECONCILING,
        )
        for forbidden in ("released", "in_progress", "awaiting_receipt"):
            with self.assertRaises(HumanFilingTransitionError):
                validate_human_filing_transition("outcome_unknown", forbidden)

    def test_artifact_and_representation_allowlists_are_synthetic(self):
        artifacts = {item.value for item in ArtifactKind}
        self.assertIn("filing_package", artifacts)
        self.assertIn("synthetic_submission_report", artifacts)
        self.assertIn("synthetic_receipt", artifacts)
        self.assertNotIn("provider_receipt", artifacts)
        self.assertTrue(
            all(item.value.startswith("synthetic_") for item in RepresentationKind)
        )

    def test_canonical_sha256_is_order_independent(self):
        left = canonical_sha256({"b": [2, 1], "a": True})
        right = canonical_sha256({"a": True, "b": [2, 1]})
        self.assertEqual(left, right)
        self.assertRegex(left, r"^[0-9a-f]{64}$")

    def test_case_binding_is_frozen_tenant_scoped_and_synthetic(self):
        tenant_id = str(UUID(int=1))
        binding = HumanFilingCaseBinding(
            binding_id=str(UUID(int=2)),
            tenant_id=tenant_id,
            case_id=str(UUID(int=3)),
            binding_code="rtm-a1s-binding-" + "a" * 24,
            case_snapshot_sha256="b" * 64,
            bound_by_operator_id=str(UUID(int=4)),
            bound_at="2026-08-25T08:00:00Z",
        )
        self.assertEqual(binding.tenant_id, tenant_id)
        self.assertTrue(binding.synthetic_only)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            binding.tenant_id = str(UUID(int=5))  # type: ignore[misc]

    def test_case_binding_rejects_non_synthetic_or_bad_hash(self):
        values = dict(
            binding_id=str(UUID(int=2)),
            tenant_id=str(UUID(int=1)),
            case_id=str(UUID(int=3)),
            binding_code="rtm-a1s-binding-" + "a" * 24,
            case_snapshot_sha256="b" * 64,
            bound_by_operator_id=str(UUID(int=4)),
            bound_at="2026-08-25T08:00:00Z",
        )
        with self.assertRaises(HumanFilingContractError):
            HumanFilingCaseBinding(**values, synthetic_only=False)
        values["case_snapshot_sha256"] = "not-a-hash"
        with self.assertRaises(HumanFilingContractError):
            HumanFilingCaseBinding(**values)


if __name__ == "__main__":
    unittest.main()
