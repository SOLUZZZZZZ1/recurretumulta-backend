from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c4_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c4_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c4_smoke.py"


class ConnectC4ScriptsContractTest(unittest.TestCase):
    def test_schema_requires_exact_confirmation_and_migration(self):
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("STAGING_CONNECT_C4_SCHEMA_ONLY", source)
        self.assertIn("invalid_apply_confirmation", source)
        self.assertIn("--apply", source)
        self.assertIn("connect_c4_webhook_ddl", source)
        self.assertIn("RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION", source)
        self.assertIn("rtm_management_schema_migrations", source)

    def test_schema_is_additive_non_destructive_and_unseeded(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for declaration in (
            '"destructive": False',
            '"routes_published": False',
            '"connectors_seeded": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        for forbidden in (
            "DROP TABLE",
            "DROP COLUMN",
            "TRUNCATE",
            "DELETE FROM",
            "INSERT INTO RTM_CONNECT_CONNECTORS",
        ):
            self.assertNotIn(forbidden, source.upper())

    def test_all_scripts_reuse_staging_and_effect_barriers(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            source = path.read_text(encoding="utf-8")
            for required in (
                "RTM_ENV_must_be_staging",
                "RTM_DATA_NAMESPACE",
                "RTM_SIDE_EFFECT_POLICY",
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
                "RTM_ENABLE_OUTBOUND_EMAIL",
                "RTM_ENABLE_STRIPE",
                "RTM_ENABLE_FINAL_PAYMENTS",
            ):
                self.assertIn(required, source)

    def test_scripts_refuse_production_before_database(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            process = subprocess.run(
                [sys.executable, str(path), "--compact"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(process.returncode, 2, path.name)
            payload = json.loads(process.stdout)
            self.assertIn(
                "RTM_ENV_must_be_staging",
                payload["blockers"],
                path.name,
            )

    def test_preflight_is_read_only_and_checks_all_schemas(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"schema_changes_required": False', source)
        self.assertNotIn("--apply", source)
        for required in (
            "c1_schema_ready",
            "c3_schema_ready",
            "c4_schema_ready",
            "c1_migration_registered",
            "c3_migration_registered",
            "c4_migration_registered",
        ):
            self.assertIn(required, source)

    def test_preflight_requires_zero_residue_and_no_runtime(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            "no_real_connectors",
            "no_persistent_connectors",
            "webhook_connector_not_persistently_seeded",
            "no_persistent_c4_residue",
            "webhook_inbox_total",
            "webhook_events_total",
            "reconciliations_total",
            "reconciliation_events_total",
            "runtime_not_wired",
            "rtm_connect_runtime_unexpectedly_wired",
        ):
            self.assertIn(required, source)

    def test_smoke_is_transactional_synthetic_and_effect_free(self):
        source = SMOKE.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"transactional": True',
            '"network_used": False',
            '"routes_published": False',
            '"schema_changes_applied": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        self.assertIn("transaction.rollback()", source)

    def test_smoke_covers_unknown_replay_conflict_dlq_and_ledgers(self):
        source = SMOKE.read_text(encoding="utf-8")
        for required in (
            "c2_action_persisted_unknown",
            "unknown_blind_retry_blocked",
            "verified_webhook_exactly_correlated",
            "unknown_reconciled_confirmed_after_e4",
            "exact_replay_reused_without_duplicates",
            "changed_replay_conflict_blocked",
            "delivery_tampering_blocked",
            "indeterminate_reconciliation_stays_unknown",
            "unmatched_webhook_dead_lettered",
            "non_synthetic_origin_blocked",
            "reconciliation_transition_ledgers_complete",
            "webhook_identity_frozen",
            "webhook_events_append_only",
            "reconciliation_identity_frozen",
            "reconciliation_events_append_only",
            "webhook_event_cross_scope_blocked",
            "reconciliation_event_cross_scope_blocked",
            "single_attempt_per_unknown_action",
            "rollback_removed_synthetic_records",
        ):
            self.assertIn(required, source)

    def test_runtime_remains_unwired_if_full_repository_is_present(self):
        app = ROOT / "app.py"
        if not app.exists():
            return
        source = app.read_text(encoding="utf-8")
        for forbidden in (
            "rtm_connect.webhooks",
            "rtm_connect.reconciliation",
            "synthetic_webhook",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
