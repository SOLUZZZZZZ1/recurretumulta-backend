from __future__ import annotations

import ast
import unittest
from pathlib import Path

from rtm_connect.state_machine import (
    ActionStatus,
    automatic_retry_allowed,
    next_states,
)


ROOT = Path(__file__).resolve().parents[1]
WEBHOOKS = ROOT / "rtm_connect" / "webhooks.py"
RECONCILIATION = ROOT / "rtm_connect" / "reconciliation.py"
REPOSITORY = ROOT / "rtm_connect" / "repository.py"
KERNEL = ROOT / "rtm_connect" / "kernel.py"
CONNECTOR = ROOT / "rtm_connect" / "connectors" / "synthetic_webhook.py"


class ConnectC4WorkflowContractTest(unittest.TestCase):
    def test_unknown_has_no_blind_retry_transition(self):
        self.assertEqual(
            set(next_states(ActionStatus.UNKNOWN)),
            {ActionStatus.RECONCILING, ActionStatus.MANUAL_REVIEW},
        )
        self.assertFalse(automatic_retry_allowed(ActionStatus.UNKNOWN))
        self.assertTrue(
            automatic_retry_allowed(ActionStatus.RETRYABLE_FAILED)
        )

    def test_webhook_deduplication_binds_ingress_and_source_event(self):
        source = WEBHOOKS.read_text(encoding="utf-8")
        self.assertIn('"ingress_connector_id"', source)
        self.assertIn('"source_event_id"', source)
        self.assertIn("WebhookReplayConflict", source)
        self.assertIn("replay_count=replay_count+1", source)

    def test_match_requires_all_origin_identity_fields(self):
        source = WEBHOOKS.read_text(encoding="utf-8")
        for exact_field in (
            "claimed_action_id",
            "claimed_attempt_id",
            "origin_connector_code",
            "origin_connector_version",
            "request_sha256",
            "external_reference",
        ):
            self.assertIn(exact_field, source)
        for exact_guard in (
            "attempt_not_unknown",
            "action_not_unknown",
            "reconciliation_not_required",
            "origin_connector_not_reconcilable",
            "origin_connector_not_active",
            "origin_connector_not_staging",
            "origin_connector_not_synthetic",
            "origin_connector_has_credentials",
            "ingress_is_origin_connector",
        ):
            self.assertIn(exact_guard, source)

    def test_verification_rebuilds_the_persisted_delivery(self):
        source = WEBHOOKS.read_text(encoding="utf-8")
        section = source[
            source.index("def verify_webhook("):
            source.index("def match_webhook(")
        ]
        self.assertIn("SyntheticWebhookDelivery(", section)
        self.assertIn("verify_delivery(reconstructed)", section)
        self.assertIn("verification_method", section)

    def test_reconciliation_never_queues_or_starts_an_attempt(self):
        source = RECONCILIATION.read_text(encoding="utf-8")
        self.assertNotIn("queue_action", source)
        self.assertNotIn("start_attempt", source)
        self.assertIn("begin_reconciliation", source)
        self.assertIn("record_reconciliation_outcome", source)

    def test_started_reconciliation_is_not_returned_as_final_replay(self):
        source = RECONCILIATION.read_text(encoding="utf-8")
        self.assertIn("class ReconciliationInProgress", source)
        self.assertIn(
            'str(existing["status"]) == "started"',
            source,
        )
        self.assertIn("raise ReconciliationInProgress", source)
        self.assertIn("resolution: str | None", source)
        self.assertIn('row["resolution"] is not None', source)

    def test_kernel_exposes_reconciliation_outcome_seam(self):
        repository_tree = ast.parse(
            REPOSITORY.read_text(encoding="utf-8")
        )
        definition = next(
            node
            for node in repository_tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "record_reconciliation_outcome"
        )
        parameters = {
            argument.arg
            for argument in (
                definition.args.args + definition.args.kwonlyargs
            )
        }
        for required in (
            "action_id",
            "attempt_id",
            "target_status",
            "evidence_id",
        ):
            self.assertIn(required, parameters)
        kernel = KERNEL.read_text(encoding="utf-8")
        self.assertIn("record_reconciliation_outcome", kernel)
        self.assertIn('"record_reconciliation_outcome"', kernel)

    def test_unknown_remains_reconciliation_required(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn(
            "target_status is ActionStatus.UNKNOWN",
            source,
        )
        self.assertIn(
            "target_status is ActionStatus.RETRYABLE_FAILED",
            source,
        )
        self.assertIn("ActionStatus.RECONCILING.value", source)
        self.assertIn("attempt_status", source)
        self.assertIn("supports_reconciliation", source)

    def test_confirmed_uses_exact_e4_evidence(self):
        workflow = RECONCILIATION.read_text(encoding="utf-8")
        repository = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn(
            "EvidenceLevel.E4_RECEIPT_VERIFIED",
            workflow,
        )
        self.assertIn("evidence_id=evidence_id", workflow)
        self.assertIn("def _load_evidence(", repository)
        self.assertIn("id=CAST(:evidence_id AS UUID)", repository)
        self.assertIn(
            "confirmed exige la evidencia exacta de reconciliación",
            repository,
        )

    def test_ingress_is_registered_as_webhook_not_origin_api(self):
        source = WEBHOOKS.read_text(encoding="utf-8")
        self.assertIn("mode=ConnectorMode.WEBHOOK", source)
        self.assertIn('"synthetic_only": True', source)
        self.assertIn('"network_used": False', source)

    def test_c4_runtime_has_no_routes_or_network_clients(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (WEBHOOKS, RECONCILIATION, CONNECTOR)
        )
        for forbidden in (
            "APIRouter",
            "requests.",
            "httpx.",
            "urllib.request",
            "socket.",
            "boto3",
            "stripe",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
