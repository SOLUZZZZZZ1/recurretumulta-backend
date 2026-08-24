import unittest

from rtm_connect.assisted_schema import (
    ASSISTED_TASK_STATUSES,
    CONNECT_C7_REQUIRED_COLUMNS,
    CONNECT_C7_REQUIRED_CONSTRAINTS,
    CONNECT_C7_REQUIRED_INDEXES,
    CONNECT_C7_REQUIRED_TRIGGERS,
    RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION,
    connect_c7_assisted_ddl,
)


class ConnectC7AssistedSchemaContractTest(unittest.TestCase):
    def test_version_and_own_tables_are_frozen(self):
        self.assertEqual(
            RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION,
            "rtm_connect_c7_assisted_schema_v1_0",
        )
        self.assertEqual(
            set(CONNECT_C7_REQUIRED_COLUMNS),
            {"rtm_connect_assisted_tasks", "rtm_connect_assisted_events"},
        )

    def test_schema_is_additive_and_has_no_destructive_statement(self):
        ddl = connect_c7_assisted_ddl()
        self.assertTrue(ddl)
        source = "\n".join(statement.lower() for _, statement in ddl)
        for forbidden in (
            "drop table", "drop column", "truncate ", "delete from",
            "alter column", "rename to",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("create table if not exists rtm_connect_assisted_tasks", source)
        self.assertIn("create table if not exists rtm_connect_assisted_events", source)

    def test_normal_and_unknown_paths_are_database_guarded(self):
        source = "\n".join(statement for _, statement in connect_c7_assisted_ddl())
        for status in ASSISTED_TASK_STATUSES:
            self.assertIn(f"'{status}'", source)
        for transition in (
            "OLD.status='in_progress'",
            "'awaiting_receipt','outcome_unknown'",
            "OLD.status='outcome_unknown'",
            "NEW.status='reconciling'",
            "OLD.status='reconciling'",
            "'manual_review', 'permanent_failed'",
            "OLD.status='verified' AND NEW.status='completed'",
        ):
            self.assertIn(transition, source)

    def test_identity_package_and_attestations_are_frozen(self):
        source = "\n".join(statement for _, statement in connect_c7_assisted_ddl())
        for required in (
            "authorization_id IS DISTINCT FROM OLD.authorization_id",
            "package_manifest IS DISTINCT FROM OLD.package_manifest",
            "assisted task assignment is frozen",
            "assisted review attestation is write-once",
            "assisted release is write-once",
            "assisted receipt evidence is write-once",
            "assisted verified evidence is write-once",
        ):
            self.assertIn(required, source)

    def test_r4_temporal_and_evidence_associations_are_write_once(self):
        source = "\n".join(
            statement for _, statement in connect_c7_assisted_ddl()
        )
        for required in (
            "NEW.metadata IS DISTINCT FROM OLD.metadata",
            "OLD.started_at IS NOT NULL",
            "NEW.started_at IS DISTINCT FROM OLD.started_at",
            "assisted execution start is write-once",
            "NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at",
            "NEW.ready_at IS DISTINCT FROM OLD.ready_at",
            "OLD.unknown_at IS NOT NULL",
            "NEW.unknown_at IS DISTINCT FROM OLD.unknown_at",
            "assisted unknown outcome is write-once",
            "NEW.receipt_submitted_at",
            "IS DISTINCT FROM OLD.receipt_submitted_at",
            "NEW.external_reference",
            "IS DISTINCT FROM OLD.external_reference",
            "NEW.verified_at IS DISTINCT FROM OLD.verified_at",
            "NEW.verified_by_operator_id",
            "IS DISTINCT FROM OLD.verified_by_operator_id",
        ):
            self.assertIn(required, source)

    def test_separation_and_append_only_are_enforced(self):
        source = "\n".join(statement for _, statement in connect_c7_assisted_ddl())
        self.assertIn("release_operator_id <> assignee_operator_id", source)
        self.assertIn("verified_by_operator_id <> assignee_operator_id", source)
        self.assertIn("verified_by_operator_id <> release_operator_id", source)
        self.assertIn("BEFORE UPDATE OR DELETE ON rtm_connect_assisted_events", source)
        self.assertEqual(len(CONNECT_C7_REQUIRED_TRIGGERS), 5)
        self.assertGreaterEqual(len(CONNECT_C7_REQUIRED_CONSTRAINTS), 20)
        self.assertEqual(len(CONNECT_C7_REQUIRED_INDEXES), 8)

    def test_task_and_event_scope_are_database_guarded(self):
        source = "\n".join(
            statement for _, statement in connect_c7_assisted_ddl()
        )
        for required in (
            "trg_rtm_connect_assisted_task_scope_guard",
            "BEFORE INSERT OR UPDATE ON rtm_connect_assisted_tasks",
            "attempt_action_id IS DISTINCT FROM NEW.action_id",
            "attempt_connector_id IS DISTINCT FROM NEW.connector_id",
            "authorization_action_id IS DISTINCT FROM NEW.action_id",
            "persisted_authorization_version",
            "IS DISTINCT FROM NEW.authorization_version",
            "assisted task differs from kernel scope",
            "trg_rtm_connect_assisted_event_scope_guard",
            "BEFORE INSERT ON rtm_connect_assisted_events",
            "NEW.action_id IS DISTINCT FROM parent_action_id",
            "NEW.attempt_id IS DISTINCT FROM parent_attempt_id",
            "assisted event differs from parent scope",
        ):
            self.assertIn(required, source)

    def test_required_columns_cover_authority_and_exact_evidence_ids(self):
        columns = CONNECT_C7_REQUIRED_COLUMNS["rtm_connect_assisted_tasks"]
        for name in (
            "authorization_id", "authorization_version", "package_sha256",
            "review_attestation_sha256", "release_attestation_sha256",
            "receipt_evidence_id", "verified_evidence_id",
            "release_operator_id", "verified_by_operator_id",
        ):
            self.assertIn(name, columns)


if __name__ == "__main__":
    unittest.main()
