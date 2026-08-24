from __future__ import annotations

import unittest

from rtm_connect.production_schema import (
    CONNECT_C8_REQUIRED_COLUMNS,
    CONNECT_C8_REQUIRED_CONSTRAINTS,
    CONNECT_C8_REQUIRED_INDEXES,
    CONNECT_C8_REQUIRED_TRIGGERS,
    DISPATCH_OUTBOX_STATUSES,
    PRODUCTION_RELEASE_STATUSES,
    RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION,
    RTM_CONNECT_C8_SCHEMA_VERSION,
    connect_c8_production_ddl,
)


def _rendered_ddl() -> str:
    return "\n".join(statement for _, statement in connect_c8_production_ddl())


class ConnectC8ProductionSchemaContractTest(unittest.TestCase):
    def test_version_statuses_and_four_tables_are_frozen(self):
        self.assertEqual(
            RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION,
            "rtm_connect_c8_production_schema_v1_0",
        )
        self.assertEqual(
            RTM_CONNECT_C8_SCHEMA_VERSION,
            RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION,
        )
        self.assertEqual(
            PRODUCTION_RELEASE_STATUSES,
            (
                "proposed", "security_approved", "operations_approved",
                "ready", "simulated_active", "halted", "rejected",
                "expired",
            ),
        )
        self.assertEqual(
            DISPATCH_OUTBOX_STATUSES,
            (
                "prepared", "claimed", "dry_run_confirmed", "unknown",
                "manual_review", "cancelled",
            ),
        )
        self.assertEqual(
            set(CONNECT_C8_REQUIRED_COLUMNS),
            {
                "rtm_connect_production_releases",
                "rtm_connect_production_release_events",
                "rtm_connect_dispatch_outbox",
                "rtm_connect_dispatch_events",
            },
        )

    def test_ddl_is_idempotent_additive_unseeded_and_non_destructive(self):
        ddl = connect_c8_production_ddl()
        self.assertTrue(ddl)
        self.assertEqual(ddl, connect_c8_production_ddl())
        self.assertEqual(len({name for name, _ in ddl}), len(ddl))
        source = _rendered_ddl().lower()
        self.assertEqual(source.count("create table if not exists"), 4)
        for table in CONNECT_C8_REQUIRED_COLUMNS:
            self.assertIn(
                f"create table if not exists public.{table}", source
            )
        for forbidden in (
            "drop table", "drop column", "truncate table", "delete from",
            "alter table", "rename to", "insert into",
        ):
            self.assertNotIn(forbidden, source)

    def test_every_foreign_key_is_restrict(self):
        source = _rendered_ddl().upper()
        self.assertGreater(source.count("REFERENCES "), 0)
        self.assertEqual(
            source.count("REFERENCES "),
            source.count("ON DELETE RESTRICT"),
        )
        self.assertNotIn("ON DELETE CASCADE", source)
        self.assertNotIn("ON DELETE SET NULL", source)

    def test_release_is_permanently_inert_and_tightly_limited(self):
        source = _rendered_ddl()
        for required in (
            "simulation_only BOOLEAN NOT NULL DEFAULT TRUE",
            "external_effects_allowed BOOLEAN NOT NULL DEFAULT FALSE",
            "live_activation_allowed BOOLEAN NOT NULL DEFAULT FALSE",
            "human_activation_required BOOLEAN NOT NULL DEFAULT TRUE",
            "provider_pack_present BOOLEAN NOT NULL DEFAULT FALSE",
            "CHECK (simulation_only = TRUE)",
            "CHECK (external_effects_allowed = FALSE)",
            "CHECK (live_activation_allowed = FALSE)",
            "CHECK (human_activation_required = TRUE)",
            "CHECK (provider_pack_present = FALSE)",
            "canary_percent > 0 AND canary_percent <= 5",
            "CHECK (max_concurrency = 1)",
            "CHECK (daily_action_limit = 1)",
        ):
            self.assertIn(required, source)

    def test_release_binds_commits_hashes_and_separated_approvals(self):
        columns = CONNECT_C8_REQUIRED_COLUMNS[
            "rtm_connect_production_releases"
        ]
        for name in (
            "source_commit_sha", "manifest_sha256", "policy_sha256",
            "schema_sha256", "build_artifact_sha256",
            "release_binding_sha256", "requested_by_operator_id",
            "security_approved_by_operator_id", "security_approval_sha256",
            "operations_approved_by_operator_id",
            "operations_approval_sha256",
        ):
            self.assertIn(name, columns)
        source = _rendered_ddl()
        self.assertIn(
            "CHECK (source_commit_sha ~ '^[0-9a-f]{40}$')",
            source,
        )
        self.assertIn(
            "release_code = 'rtmc8-release-' ||\n"
            "                        SUBSTRING(release_binding_sha256 FROM 1 FOR 24)",
            source,
        )
        for required in (
            "security_approved_by_operator_id\n                            <> requested_by_operator_id",
            "operations_approved_by_operator_id\n                            <> requested_by_operator_id",
            "security_approved_by_operator_id\n                            <> operations_approved_by_operator_id",
            "production security approval is write-once",
            "production operations approval is write-once",
            "security identity may only be set by security approval",
            "operations identity may only be set by operations approval",
            "security_approved_at >= requested_at",
            "security_approval_sha256 IS NOT NULL",
            "operations_approved_at\n                                    >= security_approved_at",
            "operations_approval_sha256 IS NOT NULL",
            "ready_at >= operations_approved_at",
            "simulated_active_at >= ready_at",
            ")) IS TRUE",
        ):
            self.assertIn(required, source)

    def test_release_metadata_is_null_safe_and_ttl_binds_validity(self):
        source = _rendered_ddl()
        for required in (
            "ck_rtm_connect_production_release_metadata CHECK (\n"
            "                    (jsonb_typeof(metadata) = 'object'",
            ") IS TRUE\n                )",
            "(valid_until - requested_at) <=\n"
            "                        CAST(metadata->'candidate'->>\n"
            "                            'admission_ttl_seconds' AS INTEGER)\n"
            "                            * INTERVAL '1 second'",
        ):
            self.assertIn(required, source)

    def test_release_state_machine_and_emergency_halt_are_guarded(self):
        source = _rendered_ddl()
        for status in PRODUCTION_RELEASE_STATUSES:
            self.assertIn(f"'{status}'", source)
        for required in (
            "production release must start proposed at version 1",
            "guard_now := clock_timestamp()",
            "NEW.requested_at > guard_now",
            "NEW.valid_until <= guard_now",
            "production release validity must include the current database time",
            "production release transition timestamps cannot be in the future",
            "NEW.version <> OLD.version + 1",
            "OLD.status = 'proposed'",
            "NEW.status IN (\n                            'security_approved', 'rejected', 'expired'",
            "OLD.status = 'security_approved'",
            "OLD.status = 'operations_approved'",
            "OLD.status = 'ready'",
            "OLD.status = 'simulated_active'",
            "NEW.status = 'halted'",
            "OLD.status NOT IN ('halted', 'rejected', 'expired')",
            "(status = 'halted') = emergency_halt",
            "production emergency halt is terminal",
        ):
            self.assertIn(required, source)

    def test_outbox_scope_hashes_and_stable_identities_are_exact(self):
        columns = CONNECT_C8_REQUIRED_COLUMNS["rtm_connect_dispatch_outbox"]
        for name in (
            "action_id", "authorization_id", "authorization_version",
            "release_id", "business_command_id", "production_effect_key",
            "payload_sha256", "request_sha256", "release_manifest_sha256",
            "release_binding_sha256",
        ):
            self.assertIn(name, columns)
        source = _rendered_ddl()
        for required in (
            "authorization_action_id IS DISTINCT FROM NEW.action_id",
            "persisted_authorization_version\n                        IS DISTINCT FROM NEW.authorization_version",
            "authorized_payload_sha256\n                        IS DISTINCT FROM NEW.payload_sha256",
            "parent_manifest_sha256\n                        IS DISTINCT FROM NEW.release_manifest_sha256",
            "parent_binding_sha256\n                        IS DISTINCT FROM NEW.release_binding_sha256",
            "parent_release_status <> 'simulated_active'",
            "uq_rtm_connect_dispatch_business_command",
            "uq_rtm_connect_dispatch_production_effect",
            "action_risk_class IS DISTINCT FROM\n                        'R4_critical_regulated'",
            "authorization_evidence_level IS DISTINCT FROM\n                        'E4_receipt_verified'",
            "authorization_modes IS DISTINCT FROM\n                        jsonb_build_array('assisted')",
            "action_contract_version IS DISTINCT FROM\n"
            "                        'rtm_connect_contract_v1_0'",
            "action_status IS DISTINCT FROM 'authorized'",
            "authorization_authorized_at < action_requested_at",
            "authorization_authorized_at > guard_now",
            "authorization_expires_at\n"
            "                        > parent_valid_until",
            "authorization_legal_effect IS DISTINCT FROM FALSE",
            "authorization_revoked_at IS NOT NULL",
            "authorization_expires_at <= guard_now",
            "parent_valid_until <= guard_now",
            "'rtmc8:command:' ||",
            "'rtmc8:dry-run:' ||",
            "NEW.metadata->>'dispatch_binding_sha256' IS NULL",
            "NEW.metadata->>'production_effect_sha256' IS NULL",
        ):
            self.assertIn(required, source)

    def test_outbox_scope_guard_has_valid_active_authority_predicate(self):
        source = _rendered_ddl()
        self.assertIn(
            "(TG_OP = 'INSERT' OR NEW.status = 'claimed') AND (\n"
            "                            action_status IS DISTINCT FROM 'authorized'\n"
            "                            OR authorization_revoked_at IS NOT NULL",
            source,
        )
        self.assertNotIn(
            ") AND (\n"
            "                            OR authorization_revoked_at",
            source,
        )
        for required in (
            "'contract_version'\n                        IS DISTINCT FROM\n"
            "                            'rtm.connect.c8.simulated_outbox.v1'",
            "'simulation_only'\n                        IS DISTINCT FROM 'true'::jsonb",
            "'external_effects_allowed'\n                        IS DISTINCT FROM 'false'::jsonb",
            "'network_call_performed'\n                        IS DISTINCT FROM 'false'::jsonb",
            "'secret_resolution_performed'\n                        IS DISTINCT FROM 'false'::jsonb",
            "'blind_retry_allowed'\n                        IS DISTINCT FROM 'false'::jsonb",
            "NEW.metadata->'expected_admission_payload'\n                        IS DISTINCT FROM action_payload",
            "FROM public.rtm_connect_actions\n"
            "                WHERE id = NEW.action_id\n"
            "                FOR SHARE",
            "FROM public.rtm_connect_authorizations\n"
            "                WHERE id = NEW.authorization_id\n"
            "                FOR SHARE",
            "WHERE id = NEW.release_id\n                FOR UPDATE",
            "guard_now := clock_timestamp()",
            "dispatch frozen release quota exhausted",
            "dispatch frozen concurrency exhausted",
            "dispatch claim exceeds frozen lease bounds",
            "NEW.claim_expires_at <= guard_now",
            "claim_expires_at > guard_now",
            "OLD.claim_expires_at <= guard_now",
            "max_simulated_actions_total",
            "max_payload_bytes",
            "parent_valid_until IS NULL",
            "parent_requested_at > guard_now",
            "parent_simulated_active_at > guard_now",
            "NEW.created_at > guard_now",
            "NEW.updated_at > guard_now",
            "NEW.dry_run_confirmed_at > guard_now",
            "octet_length(regexp_replace(\n"
            "                        CAST(action_payload AS TEXT)",
        ):
            self.assertIn(required, source)

    def test_outbox_cannot_contact_network_provider_or_external_world(self):
        source = _rendered_ddl()
        for required in (
            "dry_run_only BOOLEAN NOT NULL DEFAULT TRUE",
            "network_allowed BOOLEAN NOT NULL DEFAULT FALSE",
            "provider_contacted BOOLEAN NOT NULL DEFAULT FALSE",
            "CHECK (dry_run_only = TRUE)",
            "CHECK (network_allowed = FALSE)",
            "CHECK (provider_contacted = FALSE)",
            "dispatch identity, hashes, release, and inert flags are frozen",
        ):
            self.assertIn(required, source)

    def test_claim_is_fenced_once_and_unknown_is_never_reclaimed(self):
        source = _rendered_ddl()
        for required in (
            "claim_token UUID",
            "claim_fence BIGINT NOT NULL DEFAULT 0",
            "claim_fence > 0",
            "uq_rtm_connect_dispatch_claim_token",
            "NEW.claim_fence <> OLD.claim_fence + 1",
            "dispatch claim identity and fence are write-once",
            "OLD.status = 'unknown'",
            "NEW.status IN ('prepared', 'claimed')",
            "UNKNOWN dispatch outcome must never be retried or reclaimed",
            "WHEN OLD.status = 'unknown'\n                        AND NEW.status = 'manual_review'",
        ):
            self.assertIn(required, source)

    def test_claimed_cancellation_retains_claim_and_prepared_review_is_blocked(self):
        source = _rendered_ddl()
        self.assertIn(
            "status IN (\n"
            "                            'claimed', 'dry_run_confirmed',\n"
            "                            'unknown', 'manual_review', 'cancelled'",
            source,
        )
        self.assertIn(
            "OLD.status = 'prepared'\n"
            "                        AND NEW.status IN (\n"
            "                            'claimed', 'cancelled'",
            source,
        )
        self.assertNotIn(
            "OLD.status = 'prepared'\n"
            "                        AND NEW.status IN (\n"
            "                            'claimed', 'manual_review', 'cancelled'",
            source,
        )

    def test_events_are_sequential_scope_guarded_and_append_only(self):
        source = _rendered_ddl()
        for required in (
            "COALESCE(MAX(sequence_number), 0) + 1",
            "FOR UPDATE",
            "NEW.sequence_number IS DISTINCT FROM expected_sequence",
            "NEW.sequence_number IS DISTINCT FROM parent_version",
            "NEW.from_status IS DISTINCT FROM previous_status",
            "NEW.created_at < parent_requested_at",
            "NEW.created_at < parent_created_at",
            "NEW.created_at < previous_created_at",
            "production release event differs from parent scope or sequence",
            "dispatch event differs from parent scope or sequence",
            "NEW.payload->>'approval_id' IS NULL",
            "NEW.payload->>'approval_sha256' IS NULL",
            "NEW.payload->>'human_gate_sha256' IS NULL",
            "NEW.payload->>'dispatch_binding_sha256' IS NULL",
            "NEW.payload->>'claim_token_sha256' IS NULL",
            "NEW.event_type = 'release_proposed'",
            "NEW.event_type = 'security_approval_recorded'",
            "NEW.event_type = 'operations_approval_recorded'",
            "NEW.event_type = 'simulation_activation_recorded'",
            "NEW.event_type = 'dispatch_dry_run_prepared'",
            "NEW.event_type = 'dispatch_dry_run_claimed'",
            "NEW.event_type = 'dispatch_dry_run_confirmed'",
            "NEW.event_type = 'dispatch_simulation_unknown'",
            "NEW.event_type =\n                                'dispatch_manual_review_recorded'",
            "parent_claim_expires_at - parent_claimed_at",
            "BEFORE UPDATE OR DELETE ON\n"
            "                        public.rtm_connect_production_release_events",
            "BEFORE UPDATE OR DELETE ON\n"
            "                        public.rtm_connect_dispatch_events",
        ):
            self.assertIn(required, source)

    def test_rows_are_frozen_and_all_four_tables_block_deletion(self):
        source = _rendered_ddl()
        for required in (
            "IF NEW.id IS DISTINCT FROM OLD.id",
            "production release binding and inert limits are frozen",
            "dispatch identity, hashes, release, and inert flags are frozen",
            "BEFORE DELETE ON public.rtm_connect_production_releases",
            "BEFORE DELETE ON public.rtm_connect_dispatch_outbox",
            "BEFORE TRUNCATE ON",
            "FOR EACH STATEMENT EXECUTE FUNCTION",
            "rtm_connect_c8_delete_guard()",
            "rtm_connect_c8_append_only_guard()",
        ):
            self.assertIn(required, source)
        self.assertEqual(source.count("BEFORE TRUNCATE ON"), 4)

    def test_functions_resist_search_path_and_temp_relation_hijacking(self):
        source = _rendered_ddl()
        self.assertEqual(
            source.count(
                "SET search_path = pg_catalog, public, pg_temp;"
            ),
            9,
        )
        for relation in (
            "rtm_connect_actions",
            "rtm_connect_authorizations",
            "rtm_connect_production_releases",
            "rtm_connect_production_release_events",
            "rtm_connect_dispatch_outbox",
            "rtm_connect_dispatch_events",
        ):
            self.assertNotIn(f"FROM {relation}", source)
            self.assertIn(f"public.{relation}", source)

    def test_one_release_has_one_semantic_dispatch_identity(self):
        source = _rendered_ddl()
        self.assertIn("uq_rtm_connect_dispatch_release_once", source)
        self.assertIn(
            "ON public.rtm_connect_dispatch_outbox(release_id)", source
        )

    def test_exported_catalog_matches_rendered_schema(self):
        source = _rendered_ddl()
        self.assertGreaterEqual(len(CONNECT_C8_REQUIRED_CONSTRAINTS), 40)
        self.assertGreaterEqual(len(CONNECT_C8_REQUIRED_INDEXES), 12)
        self.assertGreaterEqual(len(CONNECT_C8_REQUIRED_TRIGGERS), 10)
        for name in (
            *CONNECT_C8_REQUIRED_CONSTRAINTS,
            *CONNECT_C8_REQUIRED_INDEXES,
            *CONNECT_C8_REQUIRED_TRIGGERS,
        ):
            self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()
