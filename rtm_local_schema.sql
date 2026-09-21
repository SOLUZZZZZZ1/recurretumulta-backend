-- RTM: schema-only setup for a fresh local PostgreSQL 17 database.
-- Source: rtm_staging / public, exported read-only on 2026-09-18.
-- Source SHA-256: 581520e4cdd942aa934a76ef1202c79fb9f417848efcb01d51b52d6ada54c528
-- Local changes: target/empty-schema guard; IF NOT EXISTS for public;
-- final object-count verification. No application rows or credentials copied.
-- Run with psql -X -h 127.0.0.1 -p 5432 -U rtm_local_app -d rtm_local
--     -W --set=ON_ERROR_STOP=1 --single-transaction --file=rtm_local_schema.sql
-- This is schema installation only. Application seed/configuration is separate.

--
-- PostgreSQL database dump
--

\restrict fX7xfHjEAbR6by6vrG3trQD4m0r8zf1ouZhUj4aUAt8nIcHwz5BJgwE6k5nO4mh

-- Dumped from database version 17.10 (Debian 17.10-1.pgdg12+1)
-- Dumped by pg_dump version 18.4 (Debian 18.4-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- Local-only preflight: require the fresh database created for this setup.
DO $rtm_local_guard$
BEGIN
    IF current_database() <> 'rtm_local'
       OR current_user <> 'rtm_local_app'
       OR session_user <> 'rtm_local_app'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet
       OR inet_client_addr() IS DISTINCT FROM '127.0.0.1'::inet
       OR inet_server_port() IS DISTINCT FROM 5432 THEN
        RAISE EXCEPTION 'Use only rtm_local / rtm_local_app at 127.0.0.1:5432';
    END IF;
    IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
        RAISE EXCEPTION 'This local setup requires PostgreSQL 17';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_database
        WHERE datname = current_database()
          AND pg_catalog.pg_get_userbyid(datdba) = current_user
    ) THEN
        RAISE EXCEPTION 'The local database must belong to rtm_local_app';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_namespace
        WHERE nspname = 'public'
          AND pg_catalog.has_schema_privilege(current_user, oid, 'CREATE')
    ) THEN
        RAISE EXCEPTION 'The public schema must exist and allow creation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_type t
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'The public schema is not empty; no changes applied';
    END IF;
END
$rtm_local_guard$;

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: rtm_connect_a1s_approval_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_approval_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                task_row rtm_connect_a1s_human_tasks%ROWTYPE;
                actor_role TEXT;
                expected_kind TEXT;
            BEGIN
                SELECT * INTO task_row
                FROM rtm_connect_a1s_human_tasks
                WHERE id = NEW.task_id AND tenant_id = NEW.tenant_id
                FOR UPDATE;
                IF NOT FOUND OR task_row.status <> 'ready_for_release' THEN
                    RAISE EXCEPTION 'A1-S approvals require ready_for_release';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_authorizations frozen_authorization
                    WHERE frozen_authorization.id = task_row.authorization_id
                      AND frozen_authorization.action_id = task_row.action_id
                      AND frozen_authorization.authorization_version =
                          task_row.authorization_version
                      AND frozen_authorization.decision = 'approved_frozen'
                      AND frozen_authorization.frozen = TRUE
                      AND frozen_authorization.revoked_at IS NULL
                      AND (frozen_authorization.expires_at IS NULL OR
                           frozen_authorization.expires_at > NOW())
                      AND jsonb_typeof(
                          frozen_authorization.approved_by_operator_ids
                      ) = 'array'
                      AND frozen_authorization.approved_by_operator_ids
                          ? NEW.operator_id::text
                ) THEN
                    RAISE EXCEPTION
                        'A1-S approval actor is outside frozen CORE authority';
                END IF;
                SELECT role INTO actor_role
                FROM rtm_connect_a1s_memberships
                WHERE id = NEW.membership_id AND tenant_id = NEW.tenant_id
                  AND principal_id = NEW.principal_id
                  AND operator_id = NEW.operator_id
                  AND status = 'active' AND synthetic_only = TRUE;
                IF actor_role IS NULL THEN
                    RAISE EXCEPTION 'Invalid A1-S approval membership';
                END IF;
                IF NEW.approval_type = 'release' THEN
                    expected_kind := 'release_attestation';
                    IF actor_role NOT IN ('releaser', 'supervisor') THEN
                        RAISE EXCEPTION 'A1-S release role required';
                    END IF;
                ELSE
                    expected_kind := 'verification_preapproval_attestation';
                    IF actor_role NOT IN ('verifier', 'supervisor') THEN
                        RAISE EXCEPTION 'A1-S verifier role required';
                    END IF;
                END IF;
                IF NEW.principal_id IN (
                    task_row.requester_principal_id,
                    task_row.assignee_principal_id
                ) THEN
                    RAISE EXCEPTION 'A1-S approval principal is not separated';
                END IF;
                IF NEW.approved_at < task_row.ready_at
                   OR NEW.approved_at > NOW() THEN
                    RAISE EXCEPTION 'A1-S approval timestamp is invalid';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_artifacts f
                    WHERE f.id = NEW.artifact_id
                      AND f.tenant_id = NEW.tenant_id
                      AND f.task_id = NEW.task_id
                      AND f.kind = expected_kind
                      AND f.sha256 = NEW.attestation_sha256
                      AND f.submitted_by_membership_id = NEW.membership_id
                      AND f.submitted_by_principal_id = NEW.principal_id
                      AND f.submitted_by_operator_id = NEW.operator_id
                      AND f.synthetic_only = TRUE
                ) THEN
                    RAISE EXCEPTION 'A1-S approval artifact does not match actor';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_artifact_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_artifact_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                task_status TEXT;
            BEGIN
                SELECT h.status INTO task_status
                FROM rtm_connect_a1s_human_tasks h
                JOIN rtm_connect_a1s_memberships m
                  ON m.id = NEW.submitted_by_membership_id
                 AND m.tenant_id = NEW.tenant_id
                 AND m.principal_id = NEW.submitted_by_principal_id
                 AND m.operator_id = NEW.submitted_by_operator_id
                WHERE h.id = NEW.task_id AND h.tenant_id = NEW.tenant_id
                  AND m.status = 'active' AND m.synthetic_only = TRUE;
                IF task_status IS NULL THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact tenant or submitter';
                END IF;
                IF NEW.supersedes_artifact_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_artifacts prior
                    WHERE prior.id = NEW.supersedes_artifact_id
                      AND prior.task_id = NEW.task_id
                      AND prior.tenant_id = NEW.tenant_id
                      AND prior.kind = NEW.kind
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact supersession';
                END IF;
                IF NEW.kind IN (
                    'release_attestation',
                    'verification_preapproval_attestation'
                ) AND task_status <> 'ready_for_release' THEN
                    RAISE EXCEPTION 'A1-S approval artifacts require ready task';
                END IF;
                IF NEW.kind = 'synthetic_receipt' AND task_status NOT IN (
                    'awaiting_receipt', 'outcome_unknown', 'reconciling'
                ) THEN
                    RAISE EXCEPTION 'A1-S receipt is not currently expected';
                END IF;
                IF NEW.kind = 'synthetic_receipt' AND NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_a1s_human_tasks receipt_task
                    JOIN rtm_connect_a1s_case_bindings receipt_binding
                      ON receipt_binding.id = receipt_task.case_binding_id
                     AND receipt_binding.tenant_id = receipt_task.tenant_id
                    JOIN rtm_connect_actions receipt_action
                      ON receipt_action.id = receipt_task.action_id
                     AND receipt_action.case_id = receipt_binding.case_id
                    JOIN rtm_connect_attempts receipt_attempt
                      ON receipt_attempt.id = receipt_task.attempt_id
                     AND receipt_attempt.action_id = receipt_action.id
                    JOIN rtm_connect_authorizations receipt_authorization
                      ON receipt_authorization.id =
                          receipt_task.authorization_id
                     AND receipt_authorization.action_id = receipt_action.id
                     AND receipt_authorization.authorization_version =
                          receipt_task.authorization_version
                    JOIN documents receipt_document
                      ON receipt_document.case_id = receipt_binding.case_id
                    WHERE receipt_task.id = NEW.task_id
                      AND receipt_task.tenant_id = NEW.tenant_id
                      AND NEW.canonical_payload->>'format' =
                          'rtm.a1s.synthetic_receipt.v1'
                      AND NEW.canonical_payload->>'tenant_id' =
                          receipt_task.tenant_id::text
                      AND NEW.canonical_payload->>'task_id' =
                          receipt_task.id::text
                      AND NEW.canonical_payload->>'case_binding_id' =
                          receipt_binding.id::text
                      AND NEW.canonical_payload->>'case_id' =
                          receipt_binding.case_id::text
                      AND NEW.canonical_payload->>'action_id' =
                          receipt_action.id::text
                      AND NEW.canonical_payload->>'attempt_id' =
                          receipt_attempt.id::text
                      AND NEW.canonical_payload->>'authorization_id' =
                          receipt_authorization.id::text
                      AND NEW.canonical_payload->>'authorization_version' =
                          receipt_authorization.authorization_version::text
                      AND NEW.canonical_payload->>'request_sha256' =
                          receipt_action.payload_sha256
                      AND receipt_attempt.request_sha256 =
                          receipt_action.payload_sha256
                      AND receipt_authorization.payload_sha256 =
                          receipt_action.payload_sha256
                      AND NEW.canonical_payload->>'package_sha256' =
                          receipt_task.package_sha256
                      AND NEW.canonical_payload->>'external_reference' =
                          receipt_task.external_reference
                      AND NEW.canonical_payload->>'storage_backend' =
                          'database_manifest_only'
                      AND NEW.canonical_payload->>'b2_used' = 'false'
                      AND NEW.canonical_payload->>'network_used' = 'false'
                      AND NEW.canonical_payload->>
                          'legal_submission_executed' = 'false'
                      AND receipt_document.id::text =
                          NEW.canonical_payload->>'document_id'
                      AND receipt_document.sha256 =
                          NEW.canonical_payload->>'document_sha256'
                      AND receipt_document.kind =
                          'rtm_connect_a1s_synthetic_receipt_fixture'
                      AND receipt_document.mime = 'application/json'
                      AND receipt_document.size_bytes BETWEEN 1 AND 65536
                      AND receipt_document.b2_bucket IS NULL
                      AND receipt_document.b2_key IS NULL
                      AND jsonb_typeof(
                          receipt_task.package_manifest->'document_hashes'
                      ) = 'array'
                      AND NOT (
                          receipt_task.package_manifest->'document_hashes'
                              ? receipt_document.sha256
                      )
                      AND NOT (
                          receipt_task.package_manifest->'document_hashes'
                              ? NEW.sha256
                      )
                      AND NOT (
                          receipt_action.document_hashes
                              ? receipt_document.sha256
                      )
                ) THEN
                    RAISE EXCEPTION
                        'A1-S receipt artifact is not an inline case fixture';
                END IF;
                IF NEW.kind = 'verification_attestation'
                   AND task_status <> 'receipt_submitted' THEN
                    RAISE EXCEPTION 'A1-S E4 requires submitted receipt';
                END IF;
                IF NEW.verified_by_membership_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_memberships verifier
                    WHERE verifier.id = NEW.verified_by_membership_id
                      AND verifier.tenant_id = NEW.tenant_id
                      AND verifier.principal_id = NEW.verified_by_principal_id
                      AND verifier.operator_id = NEW.verified_by_operator_id
                      AND verifier.status = 'active'
                      AND verifier.synthetic_only = TRUE
                      AND verifier.role IN ('verifier', 'supervisor')
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact verifier';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_binding_frozen_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_binding_frozen_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.metadata->>'test_mode' <> 'true'
                       OR NOT EXISTS (
                            SELECT 1 FROM rtm_connect_a1s_tenants t
                            JOIN cases c ON c.id = NEW.case_id
                              AND COALESCE(c.test_mode, FALSE) = TRUE
                            JOIN rtm_operators o
                              ON o.id = NEW.bound_by_operator_id
                            WHERE t.id = NEW.tenant_id
                              AND t.status = 'active'
                              AND t.synthetic_only = TRUE
                              AND o.status = 'active'
                       ) THEN
                        RAISE EXCEPTION 'A1-S binding requires active synthetic test_mode';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S case bindings cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.case_id IS DISTINCT FROM OLD.case_id
                   OR NEW.binding_code IS DISTINCT FROM OLD.binding_code
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.case_snapshot_sha256 IS DISTINCT FROM
                        OLD.case_snapshot_sha256
                   OR NEW.bound_by_operator_id IS DISTINCT FROM
                        OLD.bound_by_operator_id
                   OR NEW.bound_at IS DISTINCT FROM OLD.bound_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                    RAISE EXCEPTION 'A1-S case binding is frozen';
                END IF;
                IF OLD.status <> 'active' OR NEW.status <> 'revoked'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoked_by_operator_id IS NULL
                   OR NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Only one-way A1-S binding revocation is allowed';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_human_tasks h
                    WHERE h.id = NEW.task_id AND h.tenant_id = NEW.tenant_id
                      AND h.action_id = NEW.action_id
                      AND h.attempt_id = NEW.attempt_id
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S event scope';
                END IF;
                IF NEW.actor_type = 'operator' AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_memberships m
                    WHERE m.id = NEW.membership_id
                      AND m.tenant_id = NEW.tenant_id
                      AND m.principal_id = NEW.principal_id
                      AND m.operator_id = NEW.operator_id
                      AND m.status = 'active' AND m.synthetic_only = TRUE
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S event principal';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_idempotency_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_idempotency_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S idempotency claims cannot be deleted';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.expires_at > NEW.created_at + INTERVAL '24 hours'
                       OR NOT EXISTS (
                            SELECT 1 FROM rtm_connect_a1s_memberships m
                            JOIN rtm_connect_a1s_tenants t
                              ON t.id = NEW.tenant_id
                            WHERE m.id = NEW.claimed_by_membership_id
                              AND m.tenant_id = NEW.tenant_id
                              AND m.principal_id = NEW.claimed_by_principal_id
                              AND m.operator_id = NEW.claimed_by_operator_id
                              AND m.status = 'active'
                              AND m.synthetic_only = TRUE
                              AND t.status = 'active'
                              AND t.synthetic_only = TRUE
                       ) THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency claim';
                    END IF;
                    IF NEW.task_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM rtm_connect_a1s_human_tasks h
                        WHERE h.id = NEW.task_id
                          AND h.tenant_id = NEW.tenant_id
                          AND (NEW.action_id IS NULL
                               OR h.action_id = NEW.action_id)
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency task scope';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.scope IS DISTINCT FROM OLD.scope
                   OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                   OR NEW.claimed_by_membership_id IS DISTINCT FROM
                        OLD.claimed_by_membership_id
                   OR NEW.claimed_by_principal_id IS DISTINCT FROM
                        OLD.claimed_by_principal_id
                   OR NEW.claimed_by_operator_id IS DISTINCT FROM
                        OLD.claimed_by_operator_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata
                   OR NEW.replay_count < OLD.replay_count THEN
                    RAISE EXCEPTION 'A1-S idempotency identity is frozen';
                END IF;
                IF OLD.status = 'claimed' THEN
                    IF NEW.status NOT IN ('completed', 'conflict') THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency completion';
                    END IF;
                    IF OLD.task_id IS NOT NULL OR OLD.action_id IS NOT NULL
                       OR NEW.task_id IS NULL OR NEW.action_id IS NULL
                       OR NOT EXISTS (
                           SELECT 1 FROM rtm_connect_a1s_human_tasks h
                           WHERE h.id = NEW.task_id
                             AND h.tenant_id = NEW.tenant_id
                             AND h.action_id = NEW.action_id
                       ) THEN
                        RAISE EXCEPTION 'Invalid A1-S one-shot claim binding';
                    END IF;
                ELSIF NEW.status <> OLD.status
                      OR NEW.task_id IS DISTINCT FROM OLD.task_id
                      OR NEW.action_id IS DISTINCT FROM OLD.action_id
                      OR NEW.response_sha256 IS DISTINCT FROM
                           OLD.response_sha256
                      OR NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                    RAISE EXCEPTION 'Completed A1-S idempotency is frozen';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_membership_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_membership_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM rtm_connect_a1s_tenants t
                        JOIN rtm_operators o ON o.id = NEW.operator_id
                        JOIN rtm_operators g ON g.id = NEW.granted_by_operator_id
                        WHERE t.id = NEW.tenant_id AND t.status = 'active'
                          AND t.synthetic_only = TRUE
                          AND o.status = 'active' AND g.status = 'active'
                    ) THEN
                        RAISE EXCEPTION 'Inactive A1-S tenant or operator';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S memberships cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.principal_id IS DISTINCT FROM OLD.principal_id
                   OR NEW.operator_id IS DISTINCT FROM OLD.operator_id
                   OR NEW.role IS DISTINCT FROM OLD.role
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.granted_by_operator_id IS DISTINCT FROM
                        OLD.granted_by_operator_id
                   OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                    RAISE EXCEPTION 'A1-S membership identity is frozen';
                END IF;
                IF OLD.status <> 'active' OR NEW.status <> 'revoked'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoked_by_operator_id IS NULL
                   OR NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Only one-way A1-S membership revocation is allowed';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_reject_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only in A1-S', TG_TABLE_NAME;
            END;
            $$;


--
-- Name: rtm_connect_a1s_representation_frozen_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_representation_frozen_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM rtm_connect_a1s_case_bindings b
                        JOIN rtm_connect_a1s_memberships m
                          ON m.id = NEW.recorded_by_membership_id
                         AND m.tenant_id = NEW.tenant_id
                         AND m.principal_id = NEW.recorded_by_principal_id
                         AND m.operator_id = NEW.recorded_by_operator_id
                        WHERE b.id = NEW.case_binding_id
                          AND b.tenant_id = NEW.tenant_id
                          AND b.status = 'active' AND b.synthetic_only = TRUE
                          AND b.metadata->>'test_mode' = 'true'
                          AND m.status = 'active' AND m.synthetic_only = TRUE
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S representation scope';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S representation cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.case_binding_id IS DISTINCT FROM OLD.case_binding_id
                   OR NEW.representation_code IS DISTINCT FROM
                        OLD.representation_code
                   OR NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.subject_ref_sha256 IS DISTINCT FROM
                        OLD.subject_ref_sha256
                   OR NEW.evidence_sha256 IS DISTINCT FROM OLD.evidence_sha256
                   OR NEW.canonical_evidence IS DISTINCT FROM
                        OLD.canonical_evidence
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.recorded_by_membership_id IS DISTINCT FROM
                        OLD.recorded_by_membership_id
                   OR NEW.recorded_by_principal_id IS DISTINCT FROM
                        OLD.recorded_by_principal_id
                   OR NEW.recorded_by_operator_id IS DISTINCT FROM
                        OLD.recorded_by_operator_id
                   OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'A1-S representation evidence is frozen';
                END IF;
                IF OLD.status <> 'active'
                   OR NEW.status NOT IN ('revoked', 'expired')
                   OR NEW.version <> OLD.version + 1
                   OR (NEW.status = 'revoked' AND (
                        NEW.revoked_at IS NULL OR
                        NEW.revoked_by_operator_id IS NULL
                   )) THEN
                    RAISE EXCEPTION 'Invalid A1-S representation closure';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_a1s_task_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_task_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
            DECLARE
                scope_ok BOOLEAN;
                release_membership UUID;
                release_principal UUID;
                release_operator UUID;
                release_hash TEXT;
                release_time TIMESTAMPTZ;
                verify_membership UUID;
                verify_principal UUID;
                verify_operator UUID;
                verify_time TIMESTAMPTZ;
                approvals_count INTEGER;
                manual_review_closure BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S human tasks cannot be deleted';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    manual_review_closure :=
                        NEW.status = 'manual_review'
                        AND OLD.status IS DISTINCT FROM NEW.status;
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_a1s_tenants t
                    JOIN rtm_connect_a1s_case_bindings b
                      ON b.id = NEW.case_binding_id
                     AND b.tenant_id = NEW.tenant_id
                    JOIN cases actual_case
                      ON actual_case.id = b.case_id
                     AND COALESCE(actual_case.test_mode, FALSE) = TRUE
                    JOIN rtm_connect_a1s_representation_evidence r
                      ON r.id = NEW.representation_evidence_id
                     AND r.tenant_id = NEW.tenant_id
                     AND r.case_binding_id = b.id
                    JOIN rtm_connect_actions a
                      ON a.id = NEW.action_id AND a.case_id = b.case_id
                    JOIN rtm_connect_attempts x
                      ON x.id = NEW.attempt_id AND x.action_id = a.id
                     AND x.connector_id = NEW.connector_id
                    JOIN rtm_connect_connectors c
                      ON c.id = NEW.connector_id
                    JOIN rtm_connect_authorizations z
                      ON z.id = NEW.authorization_id
                     AND z.action_id = a.id
                     AND z.authorization_version = NEW.authorization_version
                    JOIN rtm_connect_a1s_memberships requester
                      ON requester.id = NEW.requester_membership_id
                     AND requester.tenant_id = NEW.tenant_id
                     AND requester.principal_id = NEW.requester_principal_id
                     AND requester.operator_id = NEW.requester_operator_id
                    LEFT JOIN rtm_connect_a1s_memberships executor
                      ON executor.id = NEW.assignee_membership_id
                     AND executor.tenant_id = NEW.tenant_id
                     AND executor.principal_id = NEW.assignee_principal_id
                     AND executor.operator_id = NEW.assignee_operator_id
                    WHERE t.id = NEW.tenant_id AND t.status = 'active'
                      AND t.synthetic_only = TRUE
                      AND b.synthetic_only = TRUE
                      AND b.metadata->>'test_mode' = 'true'
                      AND r.synthetic_only = TRUE
                      AND requester.synthetic_only = TRUE
                      AND requester.role IN (
                          'requester', 'executor', 'supervisor'
                      )
                      AND (NEW.assignee_membership_id IS NULL OR (
                          executor.synthetic_only = TRUE
                          AND executor.role IN ('executor', 'supervisor')
                      ))
                      AND (
                          manual_review_closure OR (
                              b.status = 'active'
                              AND r.status = 'active'
                              AND r.valid_from <= NOW()
                              AND r.expires_at > NOW()
                              AND requester.status = 'active'
                              AND (
                                  NEW.assignee_membership_id IS NULL
                                  OR executor.status = 'active'
                              )
                          )
                      )
                      AND a.requested_by_operator_id = NEW.requester_operator_id
                      AND a.capability =
                          'administration.submit.human.synthetic'
                      AND a.satellite = 'rtm.human.filing.synthetic'
                      AND a.target_type = 'administration.synthetic.filing'
                      AND a.target_ref = 'synthetic-a1s-administration'
                      AND a.risk_class = 'R4_critical_regulated'
                      AND a.requires_dual_control = TRUE
                      AND a.payload @> '{"contract_version":                          "rtm.connect.a1s.human_filing.v1",                          "synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",                          "synthetic_only": true,"network_used": false,                          "b2_used": false,"provider_contacted": false,                          "external_effects_allowed": false}'::jsonb
                      AND a.payload->>'case_binding_id' = b.id::text
                      AND a.payload->>'representation_evidence_id' = r.id::text
                      AND a.payload->>'case_snapshot_sha256' =
                          b.case_snapshot_sha256
                      AND jsonb_typeof(a.document_hashes) = 'array'
                      AND jsonb_array_length(a.document_hashes) BETWEEN 1 AND 8
                      AND NEW.package_manifest->'document_hashes' =
                          a.document_hashes
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements_text(a.document_hashes)
                              AS requested_document(document_sha256)
                          WHERE requested_document.document_sha256 !~
                                  '^[0-9a-f]{64}$'
                             OR (
                                NOT manual_review_closure
                                AND NOT EXISTS (
                                  SELECT 1 FROM documents source_document
                                  WHERE source_document.case_id = b.case_id
                                    AND source_document.sha256 =
                                        requested_document.document_sha256
                                )
                             )
                      )
                      AND c.code = 'human.filing.a1s'
                      AND c.version = 'v1.0' AND c.mode = 'assisted'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE AND c.credential_ref IS NULL
                      AND c.capabilities @>
                          '["administration.submit.human.synthetic"]'::jsonb
                      AND c.configuration @> '{"synthetic_marker":                          "RTM_A1S_SYNTHETIC_ONLY",                          "synthetic_only": true,"network_used": false,                          "b2_used": false,"provider_contacted": false,                          "external_effects": false}'::jsonb
                      AND z.authority_code = 'rtm.core.authorization'
                      AND z.authority_version = 'rtm_core_authority_v1'
                      AND z.decision = 'approved_frozen' AND z.frozen = TRUE
                      AND z.required_evidence_level = 'E4_receipt_verified'
                      AND z.authorized_connector_modes = '["assisted"]'::jsonb
                      AND z.legal_effect_authorized = TRUE
                      AND (
                          manual_review_closure OR NOT EXISTS (
                              SELECT 1
                              FROM rtm_connect_authorizations newer_authority
                              WHERE newer_authority.action_id = z.action_id
                                AND newer_authority.authorization_version >
                                    z.authorization_version
                          )
                      )
                      AND (
                          manual_review_closure OR (
                              c.status = 'active'
                              AND z.revoked_at IS NULL
                              AND (
                                  z.expires_at IS NULL
                                  OR z.expires_at > NOW()
                              )
                          )
                      )
                      AND z.payload_sha256 = a.payload_sha256
                      AND x.request_sha256 = a.payload_sha256
                      AND NEW.package_manifest->>'request_sha256' =
                          a.payload_sha256
                      AND NEW.package_manifest->>'tenant_id' = t.id::text
                      AND NEW.package_manifest->>'case_binding_id' = b.id::text
                      AND NEW.package_manifest->>
                          'representation_evidence_id' = r.id::text
                      AND NEW.package_manifest->>'action_id' = a.id::text
                      AND NEW.package_manifest->>'attempt_id' = x.id::text
                      AND NEW.package_manifest->>'authorization_id' = z.id::text
                      AND NEW.package_manifest->'checklist' = '[
                          "confirm_synthetic_case_binding",
                          "confirm_frozen_core_authority",
                          "confirm_synthetic_representation",
                          "confirm_exact_package_hash",
                          "simulate_human_filing_without_external_contact",
                          "capture_synthetic_receipt",
                          "verify_receipt_with_independent_principal"
                      ]'::jsonb
                      AND CAST(NEW.package_manifest->>'due_at' AS TIMESTAMPTZ)
                          = NEW.due_at
                ) INTO scope_ok;
                IF NOT scope_ok THEN
                    RAISE EXCEPTION 'A1-S task scope or authority is invalid';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.version <> 1
                       OR NEW.status <> 'prepared'
                       OR NEW.assignee_membership_id IS NOT NULL
                       OR NEW.assignee_principal_id IS NOT NULL
                       OR NEW.assignee_operator_id IS NOT NULL
                       OR NEW.assigned_by_operator_id IS NOT NULL
                       OR NEW.assigned_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'A1-S task must start prepared v1 and unassigned';
                    END IF;
                ELSE
                    IF NEW.id IS DISTINCT FROM OLD.id
                       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                       OR NEW.case_binding_id IS DISTINCT FROM OLD.case_binding_id
                       OR NEW.representation_evidence_id IS DISTINCT FROM
                            OLD.representation_evidence_id
                       OR NEW.action_id IS DISTINCT FROM OLD.action_id
                       OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                       OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                       OR NEW.authorization_id IS DISTINCT FROM OLD.authorization_id
                       OR NEW.authorization_version IS DISTINCT FROM
                            OLD.authorization_version
                       OR NEW.task_code IS DISTINCT FROM OLD.task_code
                       OR NEW.requester_membership_id IS DISTINCT FROM
                            OLD.requester_membership_id
                       OR NEW.requester_principal_id IS DISTINCT FROM
                            OLD.requester_principal_id
                       OR NEW.requester_operator_id IS DISTINCT FROM
                            OLD.requester_operator_id
                       OR NEW.package_manifest IS DISTINCT FROM OLD.package_manifest
                       OR NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256
                       OR NEW.due_at IS DISTINCT FROM OLD.due_at
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'A1-S task identity and package are frozen';
                    END IF;
                    IF NEW.version <> OLD.version + 1 THEN
                        RAISE EXCEPTION 'A1-S task version must increment once';
                    END IF;
                    IF OLD.external_reference IS NOT NULL
                       AND NEW.external_reference IS DISTINCT FROM
                            OLD.external_reference THEN
                        RAISE EXCEPTION 'A1-S external reference is write-once';
                    END IF;
                    IF OLD.status = 'prepared' AND NEW.status = 'assigned' THEN
                        IF OLD.assignee_membership_id IS NOT NULL
                           OR OLD.assignee_principal_id IS NOT NULL
                           OR OLD.assignee_operator_id IS NOT NULL
                           OR OLD.assigned_by_operator_id IS NOT NULL
                           OR OLD.assigned_at IS NOT NULL
                           OR NEW.assignee_membership_id IS NULL
                           OR NEW.assignee_principal_id IS NULL
                           OR NEW.assignee_operator_id IS NULL
                           OR NEW.assigned_by_operator_id IS NULL
                           OR NEW.assigned_at IS NULL THEN
                            RAISE EXCEPTION
                                'A1-S assignment must be one atomic null-to-value change';
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1
                            FROM rtm_connect_a1s_memberships assigner
                            WHERE assigner.tenant_id = NEW.tenant_id
                              AND assigner.operator_id =
                                  NEW.assigned_by_operator_id
                              AND assigner.status = 'active'
                              AND assigner.synthetic_only = TRUE
                              AND assigner.role = 'supervisor'
                        ) THEN
                            RAISE EXCEPTION
                                'A1-S assignment requires active tenant supervisor';
                        END IF;
                    ELSIF NEW.assignee_membership_id IS DISTINCT FROM
                              OLD.assignee_membership_id
                       OR NEW.assignee_principal_id IS DISTINCT FROM
                              OLD.assignee_principal_id
                       OR NEW.assignee_operator_id IS DISTINCT FROM
                              OLD.assignee_operator_id
                       OR NEW.assigned_by_operator_id IS DISTINCT FROM
                              OLD.assigned_by_operator_id
                       OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at THEN
                        RAISE EXCEPTION 'A1-S assignment is write-once';
                    END IF;
                    IF NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                       AND NOT (
                           OLD.reviewed_at IS NULL
                           AND NEW.reviewed_at IS NOT NULL
                           AND (
                               (OLD.status = 'assigned'
                                    AND NEW.status = 'reviewing')
                               OR (OLD.status = 'reviewing'
                                    AND NEW.status = 'ready_for_release')
                           )
                       ) THEN
                        RAISE EXCEPTION 'A1-S reviewed_at is write-once';
                    END IF;
                    IF (
                        NEW.ready_at IS DISTINCT FROM OLD.ready_at
                        OR NEW.review_attestation_sha256 IS DISTINCT FROM
                            OLD.review_attestation_sha256
                    ) AND NOT (
                        OLD.status = 'reviewing'
                        AND NEW.status = 'ready_for_release'
                        AND OLD.ready_at IS NULL
                        AND NEW.ready_at IS NOT NULL
                        AND OLD.review_attestation_sha256 IS NULL
                        AND NEW.review_attestation_sha256 IS NOT NULL
                    ) THEN
                        RAISE EXCEPTION
                            'A1-S review readiness is write-once';
                    END IF;
                    IF OLD.status = 'reviewing'
                       AND NEW.status = 'ready_for_release'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM rtm_connect_a1s_artifacts review_artifact
                           WHERE review_artifact.tenant_id = NEW.tenant_id
                             AND review_artifact.task_id = NEW.id
                             AND review_artifact.kind =
                                 'human_review_attestation'
                             AND review_artifact.sha256 =
                                 NEW.review_attestation_sha256
                             AND review_artifact.submitted_by_membership_id =
                                 NEW.assignee_membership_id
                             AND review_artifact.submitted_by_principal_id =
                                 NEW.assignee_principal_id
                             AND review_artifact.submitted_by_operator_id =
                                 NEW.assignee_operator_id
                             AND review_artifact.synthetic_only = TRUE
                       ) THEN
                        RAISE EXCEPTION
                            'A1-S review attestation artifact is missing';
                    END IF;
                    IF NEW.status = OLD.status THEN
                        IF ROW(
                            NEW.assignee_membership_id,
                            NEW.assignee_principal_id,
                            NEW.assignee_operator_id,
                            NEW.assigned_by_operator_id, NEW.assigned_at,
                            NEW.release_membership_id, NEW.release_principal_id,
                            NEW.release_operator_id,
                            NEW.verified_by_membership_id,
                            NEW.verified_by_principal_id,
                            NEW.verified_by_operator_id, NEW.due_at,
                            NEW.reviewed_at, NEW.ready_at, NEW.released_at,
                            NEW.started_at, NEW.awaiting_receipt_at,
                            NEW.unknown_at, NEW.reconciling_at,
                            NEW.receipt_submitted_at, NEW.verified_at,
                            NEW.completed_at, NEW.review_attestation_sha256,
                            NEW.release_attestation_sha256,
                            NEW.verification_attestation_sha256,
                            NEW.external_reference, NEW.metadata
                        ) IS DISTINCT FROM ROW(
                            OLD.assignee_membership_id,
                            OLD.assignee_principal_id,
                            OLD.assignee_operator_id,
                            OLD.assigned_by_operator_id, OLD.assigned_at,
                            OLD.release_membership_id, OLD.release_principal_id,
                            OLD.release_operator_id,
                            OLD.verified_by_membership_id,
                            OLD.verified_by_principal_id,
                            OLD.verified_by_operator_id, OLD.due_at,
                            OLD.reviewed_at, OLD.ready_at, OLD.released_at,
                            OLD.started_at, OLD.awaiting_receipt_at,
                            OLD.unknown_at, OLD.reconciling_at,
                            OLD.receipt_submitted_at, OLD.verified_at,
                            OLD.completed_at, OLD.review_attestation_sha256,
                            OLD.release_attestation_sha256,
                            OLD.verification_attestation_sha256,
                            OLD.external_reference, OLD.metadata
                        ) THEN
                            RAISE EXCEPTION
                                'A1-S checkpoint cannot mutate workflow fields';
                        END IF;
                    ELSIF NOT (
                        (OLD.status = 'prepared' AND NEW.status = 'assigned') OR
                        (OLD.status = 'assigned' AND NEW.status = 'reviewing') OR
                        (OLD.status = 'reviewing' AND NEW.status IN (
                            'ready_for_release', 'manual_review'
                        )) OR
                        (OLD.status = 'ready_for_release' AND NEW.status IN (
                            'released', 'manual_review'
                        )) OR
                        (OLD.status = 'released' AND NEW.status = 'in_progress') OR
                        (OLD.status = 'in_progress' AND NEW.status IN (
                            'awaiting_receipt', 'outcome_unknown', 'manual_review'
                        )) OR
                        (OLD.status = 'awaiting_receipt' AND NEW.status IN (
                            'receipt_submitted', 'outcome_unknown', 'manual_review'
                        )) OR
                        (OLD.status = 'outcome_unknown' AND NEW.status IN (
                            'reconciling', 'manual_review'
                        )) OR
                        (OLD.status = 'reconciling' AND NEW.status IN (
                            'outcome_unknown', 'receipt_submitted',
                            'manual_review', 'permanent_failed'
                        )) OR
                        (OLD.status = 'receipt_submitted' AND NEW.status IN (
                            'verified', 'manual_review'
                        )) OR
                        (OLD.status = 'verified' AND NEW.status = 'completed')
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S task transition % -> %',
                            OLD.status, NEW.status;
                    END IF;
                END IF;

                IF NEW.status IN (
                    'released', 'in_progress', 'awaiting_receipt',
                    'outcome_unknown', 'reconciling', 'receipt_submitted',
                    'verified', 'completed', 'permanent_failed'
                ) THEN
                    SELECT COUNT(*) INTO approvals_count
                    FROM rtm_connect_a1s_approvals p
                    WHERE p.task_id = NEW.id AND p.tenant_id = NEW.tenant_id
                      AND p.decision = 'approved_frozen'
                      AND p.approval_type IN (
                          'release', 'verification_preapproval'
                      );
                    IF approvals_count <> 2 THEN
                        RAISE EXCEPTION 'A1-S requires two frozen pre-approvals';
                    END IF;
                    SELECT membership_id, principal_id, operator_id,
                           attestation_sha256, approved_at
                    INTO release_membership, release_principal,
                         release_operator, release_hash, release_time
                    FROM rtm_connect_a1s_approvals
                    WHERE task_id = NEW.id AND approval_type = 'release';
                    SELECT membership_id, principal_id, operator_id, approved_at
                    INTO verify_membership, verify_principal,
                         verify_operator, verify_time
                    FROM rtm_connect_a1s_approvals
                    WHERE task_id = NEW.id
                      AND approval_type = 'verification_preapproval';
                    IF release_principal = verify_principal
                       OR release_principal IN (
                           NEW.requester_principal_id,
                           NEW.assignee_principal_id
                       ) OR verify_principal IN (
                           NEW.requester_principal_id,
                           NEW.assignee_principal_id
                       ) OR NEW.release_membership_id <> release_membership
                       OR NEW.release_principal_id <> release_principal
                       OR NEW.release_operator_id <> release_operator
                       OR NEW.release_attestation_sha256 <> release_hash
                       OR release_time > NEW.released_at
                       OR verify_time > NEW.released_at THEN
                        RAISE EXCEPTION 'A1-S pre-operation separation failed';
                    END IF;
                END IF;

                IF NEW.status IN ('receipt_submitted', 'verified', 'completed')
                   AND NOT EXISTS (
                       SELECT 1 FROM rtm_connect_a1s_artifacts f
                       WHERE f.task_id = NEW.id AND f.tenant_id = NEW.tenant_id
                         AND f.kind = 'synthetic_receipt'
                         AND f.synthetic_only = TRUE
                   ) THEN
                    RAISE EXCEPTION 'A1-S synthetic receipt artifact is missing';
                END IF;

                IF NEW.status IN ('verified', 'completed') THEN
                    IF NEW.verified_by_membership_id <> verify_membership
                       OR NEW.verified_by_principal_id <> verify_principal
                       OR NEW.verified_by_operator_id <> verify_operator
                       OR NOT EXISTS (
                           SELECT 1
                           FROM rtm_connect_a1s_artifacts f
                           JOIN rtm_connect_a1s_artifacts receipt_artifact
                             ON receipt_artifact.id::text =
                                f.canonical_payload->>'receipt_artifact_id'
                            AND receipt_artifact.task_id = NEW.id
                            AND receipt_artifact.tenant_id = NEW.tenant_id
                            AND receipt_artifact.kind = 'synthetic_receipt'
                            AND receipt_artifact.synthetic_only = TRUE
                           JOIN rtm_connect_a1s_case_bindings receipt_binding
                             ON receipt_binding.id = NEW.case_binding_id
                            AND receipt_binding.tenant_id = NEW.tenant_id
                           JOIN rtm_connect_actions receipt_action
                             ON receipt_action.id = NEW.action_id
                            AND receipt_action.case_id = receipt_binding.case_id
                           JOIN rtm_connect_attempts receipt_attempt
                             ON receipt_attempt.id = NEW.attempt_id
                            AND receipt_attempt.action_id = receipt_action.id
                           JOIN rtm_connect_authorizations
                                receipt_authorization
                             ON receipt_authorization.id = NEW.authorization_id
                            AND receipt_authorization.action_id =
                                receipt_action.id
                            AND receipt_authorization.authorization_version =
                                NEW.authorization_version
                           JOIN documents receipt_document
                             ON receipt_document.case_id = receipt_binding.case_id
                            AND receipt_document.id::text =
                                receipt_artifact.canonical_payload->>'document_id'
                           WHERE f.task_id = NEW.id
                             AND f.tenant_id = NEW.tenant_id
                             AND f.kind = 'verification_attestation'
                             AND f.sha256 =
                                 NEW.verification_attestation_sha256
                             AND f.submitted_by_membership_id = verify_membership
                             AND f.submitted_by_principal_id = verify_principal
                             AND f.submitted_by_operator_id = verify_operator
                             AND f.synthetic_only = TRUE
                             AND f.canonical_payload->>'format' =
                                 'rtm.a1s.synthetic_receipt_verification.v1'
                             AND f.canonical_payload->>'task_id' = NEW.id::text
                             AND f.canonical_payload->>'action_id' =
                                 NEW.action_id::text
                             AND f.canonical_payload->>'authorization_id' =
                                 NEW.authorization_id::text
                             AND f.canonical_payload->>'receipt_sha256' =
                                 receipt_document.sha256
                             AND f.canonical_payload->>'external_reference' =
                                 NEW.external_reference
                             AND f.canonical_payload->>'package_sha256' =
                                 NEW.package_sha256
                             AND receipt_artifact.canonical_payload->>'format' =
                                 'rtm.a1s.synthetic_receipt.v1'
                             AND receipt_artifact.canonical_payload->>'tenant_id' =
                                 NEW.tenant_id::text
                             AND receipt_artifact.canonical_payload->>'task_id' =
                                 NEW.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'case_binding_id' = NEW.case_binding_id::text
                             AND receipt_artifact.canonical_payload->>'case_id' =
                                 receipt_binding.case_id::text
                             AND receipt_artifact.canonical_payload->>'action_id' =
                                 receipt_action.id::text
                             AND receipt_artifact.canonical_payload->>'attempt_id' =
                                 receipt_attempt.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'authorization_id' =
                                 receipt_authorization.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'authorization_version' =
                                 receipt_authorization.authorization_version::text
                             AND receipt_artifact.canonical_payload->>
                                 'request_sha256' = receipt_action.payload_sha256
                             AND receipt_attempt.request_sha256 =
                                 receipt_action.payload_sha256
                             AND receipt_authorization.payload_sha256 =
                                 receipt_action.payload_sha256
                             AND receipt_artifact.canonical_payload->>
                                 'package_sha256' = NEW.package_sha256
                             AND receipt_artifact.canonical_payload->>
                                 'external_reference' = NEW.external_reference
                             AND receipt_artifact.canonical_payload->>
                                 'document_sha256' = receipt_document.sha256
                             AND receipt_document.kind =
                                 'rtm_connect_a1s_synthetic_receipt_fixture'
                             AND receipt_document.mime = 'application/json'
                             AND receipt_document.size_bytes BETWEEN 1 AND 65536
                             AND receipt_document.b2_bucket IS NULL
                             AND receipt_document.b2_key IS NULL
                             AND NOT (
                                 NEW.package_manifest->'document_hashes'
                                     ? receipt_document.sha256
                             )
                             AND NOT (
                                 NEW.package_manifest->'document_hashes'
                                     ? receipt_artifact.sha256
                             )
                             AND NOT (
                                 receipt_action.document_hashes
                                     ? receipt_document.sha256
                             )
                       ) THEN
                        RAISE EXCEPTION 'A1-S E4 verifier must match pre-approval';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_connect_a1s_tenant_frozen_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_a1s_tenant_frozen_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S tenants cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_code IS DISTINCT FROM OLD.tenant_code
                   OR NEW.display_name IS DISTINCT FROM OLD.display_name
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'A1-S tenant identity is frozen';
                END IF;
                IF NOT (
                    (OLD.status = 'active' AND NEW.status IN (
                        'active', 'suspended', 'disabled'
                    )) OR
                    (OLD.status = 'suspended' AND NEW.status IN (
                        'active', 'suspended', 'disabled'
                    )) OR
                    (OLD.status = 'disabled' AND NEW.status = 'disabled')
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S tenant state change';
                END IF;
                IF NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION 'A1-S tenant time cannot move backwards';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_assisted_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_assisted_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                parent_action_id UUID;
                parent_attempt_id UUID;
            BEGIN
                SELECT action_id, attempt_id
                  INTO parent_action_id, parent_attempt_id
                FROM rtm_connect_assisted_tasks
                WHERE id=NEW.task_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted event parent task does not exist';
                END IF;
                IF NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.attempt_id IS DISTINCT FROM parent_attempt_id
                THEN
                    RAISE EXCEPTION
                        'assisted event differs from parent scope';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_assisted_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_assisted_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'rtm_connect_assisted_events is append-only';
            END;
            $$;


--
-- Name: rtm_connect_assisted_task_frozen(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_assisted_task_frozen() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                    OR NEW.authorization_id IS DISTINCT FROM OLD.authorization_id
                    OR NEW.authorization_version
                        IS DISTINCT FROM OLD.authorization_version
                    OR NEW.task_code IS DISTINCT FROM OLD.task_code
                    OR NEW.due_at IS DISTINCT FROM OLD.due_at
                    OR NEW.package_manifest IS DISTINCT FROM OLD.package_manifest
                    OR NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                THEN
                    RAISE EXCEPTION 'assisted legal package is frozen';
                END IF;
                IF OLD.status <> 'prepared' AND (
                    NEW.assignee_operator_id
                        IS DISTINCT FROM OLD.assignee_operator_id
                    OR NEW.assigned_by_operator_id
                        IS DISTINCT FROM OLD.assigned_by_operator_id
                    OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
                ) THEN
                    RAISE EXCEPTION 'assisted task assignment is frozen';
                END IF;
                IF OLD.started_at IS NOT NULL AND
                    NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                    RAISE EXCEPTION 'assisted execution start is write-once';
                END IF;
                IF OLD.review_attestation_sha256 IS NOT NULL AND (
                    NEW.review_attestation_sha256
                        IS DISTINCT FROM OLD.review_attestation_sha256
                    OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                    OR NEW.ready_at IS DISTINCT FROM OLD.ready_at
                ) THEN
                    RAISE EXCEPTION 'assisted review attestation is write-once';
                END IF;
                IF OLD.release_attestation_sha256 IS NOT NULL AND (
                    NEW.release_attestation_sha256
                        IS DISTINCT FROM OLD.release_attestation_sha256
                    OR NEW.release_operator_id
                        IS DISTINCT FROM OLD.release_operator_id
                    OR NEW.released_at IS DISTINCT FROM OLD.released_at
                ) THEN
                    RAISE EXCEPTION 'assisted release is write-once';
                END IF;
                IF OLD.unknown_at IS NOT NULL AND (
                    NEW.unknown_at IS DISTINCT FROM OLD.unknown_at
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                ) THEN
                    RAISE EXCEPTION 'assisted unknown outcome is write-once';
                END IF;
                IF OLD.receipt_evidence_id IS NOT NULL AND (
                    NEW.receipt_evidence_id
                        IS DISTINCT FROM OLD.receipt_evidence_id
                    OR NEW.receipt_submitted_at
                        IS DISTINCT FROM OLD.receipt_submitted_at
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                ) THEN
                    RAISE EXCEPTION 'assisted receipt evidence is write-once';
                END IF;
                IF OLD.verified_evidence_id IS NOT NULL AND (
                    NEW.verified_evidence_id
                        IS DISTINCT FROM OLD.verified_evidence_id
                    OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
                    OR NEW.verified_by_operator_id
                        IS DISTINCT FROM OLD.verified_by_operator_id
                ) THEN
                    RAISE EXCEPTION 'assisted verified evidence is write-once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_assisted_task_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_assisted_task_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                attempt_action_id UUID;
                attempt_connector_id UUID;
                authorization_action_id UUID;
                persisted_authorization_version INTEGER;
            BEGIN
                SELECT action_id, connector_id
                  INTO attempt_action_id, attempt_connector_id
                FROM rtm_connect_attempts
                WHERE id=NEW.attempt_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted task attempt does not exist';
                END IF;
                SELECT action_id, authorization_version
                  INTO authorization_action_id,
                       persisted_authorization_version
                FROM rtm_connect_authorizations
                WHERE id=NEW.authorization_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted task authorization does not exist';
                END IF;
                IF attempt_action_id IS DISTINCT FROM NEW.action_id
                    OR attempt_connector_id IS DISTINCT FROM NEW.connector_id
                    OR authorization_action_id IS DISTINCT FROM NEW.action_id
                    OR persisted_authorization_version
                        IS DISTINCT FROM NEW.authorization_version
                THEN
                    RAISE EXCEPTION
                        'assisted task differs from kernel scope';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_assisted_task_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_assisted_task_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'assisted task must start prepared at version 1';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'assisted task version must increment exactly once';
                END IF;
                transition_ok := CASE
                    WHEN OLD.status='prepared' AND NEW.status='assigned' THEN TRUE
                    WHEN OLD.status='assigned' AND NEW.status='reviewing' THEN TRUE
                    WHEN OLD.status='reviewing'
                        AND NEW.status='ready_for_release' THEN TRUE
                    WHEN OLD.status='ready_for_release'
                        AND NEW.status='released' THEN TRUE
                    WHEN OLD.status='released' AND NEW.status='in_progress' THEN TRUE
                    WHEN OLD.status='in_progress'
                        AND NEW.status IN ('awaiting_receipt','outcome_unknown')
                        THEN TRUE
                    WHEN OLD.status='outcome_unknown'
                        AND NEW.status='reconciling' THEN TRUE
                    WHEN OLD.status='reconciling'
                        AND NEW.status IN (
                            'receipt_submitted', 'outcome_unknown',
                            'manual_review', 'permanent_failed'
                        ) THEN TRUE
                    WHEN OLD.status='awaiting_receipt'
                        AND NEW.status='receipt_submitted' THEN TRUE
                    WHEN OLD.status='receipt_submitted'
                        AND NEW.status='verified' THEN TRUE
                    WHEN OLD.status='verified' AND NEW.status='completed' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION 'invalid assisted task transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_c8_append_only_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_c8_append_only_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$;


--
-- Name: rtm_connect_c8_delete_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_c8_delete_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                RAISE EXCEPTION '% cannot be deleted', TG_TABLE_NAME;
            END;
            $$;


--
-- Name: rtm_connect_dispatch_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_dispatch_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
            DECLARE
                parent_action_id UUID;
                parent_authorization_id UUID;
                parent_release_id UUID;
                parent_binding_sha256 TEXT;
                parent_status TEXT;
                parent_created_at TIMESTAMPTZ;
                parent_metadata JSONB;
                parent_claim_owner TEXT;
                parent_claim_fence BIGINT;
                parent_claimed_at TIMESTAMPTZ;
                parent_claim_expires_at TIMESTAMPTZ;
                parent_version INTEGER;
                expected_sequence INTEGER;
                previous_status TEXT;
                previous_created_at TIMESTAMPTZ;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT action_id, authorization_id, release_id,
                       release_binding_sha256, status, created_at, metadata,
                       claim_owner, claim_fence, claimed_at, claim_expires_at,
                       version
                  INTO parent_action_id, parent_authorization_id,
                       parent_release_id, parent_binding_sha256, parent_status,
                       parent_created_at, parent_metadata,
                       parent_claim_owner, parent_claim_fence,
                       parent_claimed_at, parent_claim_expires_at,
                       parent_version
                FROM public.rtm_connect_dispatch_outbox
                WHERE id = NEW.outbox_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch event parent outbox missing';
                END IF;
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                  INTO expected_sequence
                FROM public.rtm_connect_dispatch_events
                WHERE outbox_id = NEW.outbox_id;
                guard_now := clock_timestamp();
                SELECT to_status, created_at
                  INTO previous_status, previous_created_at
                FROM public.rtm_connect_dispatch_events
                WHERE outbox_id = NEW.outbox_id
                ORDER BY sequence_number DESC
                LIMIT 1;
                IF NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.authorization_id
                        IS DISTINCT FROM parent_authorization_id
                    OR NEW.release_id IS DISTINCT FROM parent_release_id
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM parent_binding_sha256
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.sequence_number IS DISTINCT FROM expected_sequence
                    OR NEW.sequence_number IS DISTINCT FROM parent_version
                    OR (expected_sequence = 1 AND NEW.from_status IS NOT NULL)
                    OR (expected_sequence > 1 AND
                        NEW.from_status IS DISTINCT FROM previous_status)
                    OR NEW.created_at > guard_now
                    OR NEW.created_at < parent_created_at
                    OR (
                        previous_created_at IS NOT NULL
                        AND NEW.created_at < previous_created_at
                    )
                    OR NOT (
                        (
                            NEW.event_type = 'dispatch_dry_run_prepared'
                            AND NEW.from_status IS NULL
                            AND NEW.to_status = 'prepared'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code = 'simulation_only_recorded'
                        ) OR (
                            NEW.event_type = 'dispatch_dry_run_claimed'
                            AND NEW.from_status = 'prepared'
                            AND NEW.to_status = 'claimed'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code = 'simulation_claim_fenced'
                        ) OR (
                            NEW.event_type = 'dispatch_dry_run_confirmed'
                            AND NEW.from_status = 'claimed'
                            AND NEW.to_status = 'dry_run_confirmed'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code =
                                'simulation_completed_without_effect'
                        ) OR (
                            NEW.event_type = 'dispatch_simulation_unknown'
                            AND NEW.from_status = 'claimed'
                            AND NEW.to_status = 'unknown'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code =
                                'manual_reconciliation_required'
                        ) OR (
                            NEW.event_type =
                                'dispatch_manual_review_recorded'
                            AND NEW.from_status = 'unknown'
                            AND NEW.to_status = 'manual_review'
                            AND NEW.actor_type = 'operator'
                            AND NEW.operator_id IS NOT NULL
                        )
                    )
                    OR (
                        NEW.event_type = 'dispatch_dry_run_prepared' AND (
                            NOT NEW.payload ?& ARRAY[
                                'dispatch_binding_sha256',
                                'production_effect_sha256', 'dry_run_only',
                                'network_allowed', 'provider_contacted',
                                'external_effects_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'dispatch_binding_sha256',
                                'production_effect_sha256', 'dry_run_only',
                                'network_allowed', 'provider_contacted',
                                'external_effects_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->>'dispatch_binding_sha256'
                                IS DISTINCT FROM
                                    parent_metadata->>
                                        'dispatch_binding_sha256'
                            OR NEW.payload->>'production_effect_sha256'
                                IS DISTINCT FROM
                                    parent_metadata->>
                                        'production_effect_sha256'
                        )
                    )
                    OR (
                        NEW.event_type = 'dispatch_dry_run_claimed' AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_owner', 'claim_token_sha256',
                                'claim_fence', 'claim_ttl_seconds',
                                'claim_expires_at'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_owner', 'claim_token_sha256',
                                'claim_fence', 'claim_ttl_seconds',
                                'claim_expires_at'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->>'claim_owner'
                                IS DISTINCT FROM parent_claim_owner
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR NEW.payload->>'claim_ttl_seconds' IS NULL
                            OR CAST(NEW.payload->>'claim_ttl_seconds' AS INTEGER)
                                NOT BETWEEN 1 AND 300
                            OR (parent_claim_expires_at - parent_claimed_at)
                                IS DISTINCT FROM
                                    CAST(NEW.payload->>'claim_ttl_seconds'
                                        AS INTEGER) * INTERVAL '1 second'
                            OR CAST(NEW.payload->>'claim_expires_at'
                                AS TIMESTAMPTZ) IS DISTINCT FROM
                                    parent_claim_expires_at
                            OR parent_claimed_at IS NULL
                            OR parent_claim_expires_at <= parent_claimed_at
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'dispatch_dry_run_confirmed',
                            'dispatch_simulation_unknown'
                        ) AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'network_call_performed',
                                'external_effects_allowed',
                                'reconciliation_required'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'network_call_performed',
                                'external_effects_allowed',
                                'reconciliation_required'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR (
                                NEW.event_type =
                                    'dispatch_dry_run_confirmed'
                                AND NEW.payload->'reconciliation_required'
                                    IS DISTINCT FROM 'false'::jsonb
                            )
                            OR (
                                NEW.event_type =
                                    'dispatch_simulation_unknown'
                                AND NEW.payload->'reconciliation_required'
                                    IS DISTINCT FROM 'true'::jsonb
                            )
                        )
                    )
                    OR (
                        NEW.event_type =
                            'dispatch_manual_review_recorded' AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'reconciliation_required',
                                'blind_retry_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'reconciliation_required',
                                'blind_retry_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR NEW.payload->'reconciliation_required'
                                IS DISTINCT FROM 'true'::jsonb
                            OR NEW.payload->'blind_retry_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                        )
                    )
                    OR NEW.payload - ARRAY[
                        'dispatch_binding_sha256',
                        'production_effect_sha256', 'dry_run_only',
                        'network_allowed', 'provider_contacted',
                        'external_effects_allowed', 'claim_owner',
                        'claim_token_sha256', 'claim_fence',
                        'claim_ttl_seconds', 'claim_expires_at',
                        'network_call_performed',
                        'reconciliation_required', 'blind_retry_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR (
                        NEW.payload ? 'dispatch_binding_sha256'
                        AND (
                            NEW.payload->>'dispatch_binding_sha256' IS NULL
                            OR NEW.payload->>'dispatch_binding_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'production_effect_sha256'
                        AND (
                            NEW.payload->>'production_effect_sha256' IS NULL
                            OR NEW.payload->>'production_effect_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'claim_token_sha256'
                        AND (
                            NEW.payload->>'claim_token_sha256' IS NULL
                            OR NEW.payload->>'claim_token_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'claim_owner'
                        AND (
                            NEW.payload->>'claim_owner' IS NULL
                            OR NEW.payload->>'claim_owner'
                                !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'dry_run_only'
                        AND NEW.payload->'dry_run_only'
                            IS DISTINCT FROM 'true'::jsonb
                    )
                    OR (
                        NEW.payload ? 'network_allowed'
                        AND NEW.payload->'network_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'provider_contacted'
                        AND NEW.payload->'provider_contacted'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'external_effects_allowed'
                        AND NEW.payload->'external_effects_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'network_call_performed'
                        AND NEW.payload->'network_call_performed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'blind_retry_allowed'
                        AND NEW.payload->'blind_retry_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                THEN
                    RAISE EXCEPTION
                        'dispatch event differs from parent scope or sequence';
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_connect_dispatch_outbox_frozen_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_dispatch_outbox_frozen_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.authorization_id
                        IS DISTINCT FROM OLD.authorization_id
                    OR NEW.authorization_version
                        IS DISTINCT FROM OLD.authorization_version
                    OR NEW.release_id IS DISTINCT FROM OLD.release_id
                    OR NEW.business_command_id
                        IS DISTINCT FROM OLD.business_command_id
                    OR NEW.production_effect_key
                        IS DISTINCT FROM OLD.production_effect_key
                    OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
                    OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                    OR NEW.release_manifest_sha256
                        IS DISTINCT FROM OLD.release_manifest_sha256
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM OLD.release_binding_sha256
                    OR NEW.dry_run_only IS DISTINCT FROM OLD.dry_run_only
                    OR NEW.network_allowed IS DISTINCT FROM OLD.network_allowed
                    OR NEW.provider_contacted
                        IS DISTINCT FROM OLD.provider_contacted
                    OR NEW.external_effects_allowed
                        IS DISTINCT FROM OLD.external_effects_allowed
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION
                        'dispatch identity, hashes, release, and inert flags are frozen';
                END IF;
                IF OLD.claim_token IS NOT NULL AND (
                    NEW.claim_owner IS DISTINCT FROM OLD.claim_owner
                    OR NEW.claim_token IS DISTINCT FROM OLD.claim_token
                    OR NEW.claim_fence IS DISTINCT FROM OLD.claim_fence
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.claim_expires_at IS DISTINCT FROM OLD.claim_expires_at
                ) THEN
                    RAISE EXCEPTION
                        'dispatch claim identity and fence are write-once';
                END IF;
                IF OLD.dry_run_confirmed_at IS NOT NULL
                    AND NEW.dry_run_confirmed_at
                        IS DISTINCT FROM OLD.dry_run_confirmed_at THEN
                    RAISE EXCEPTION 'dry-run confirmation is write-once';
                END IF;
                IF OLD.unknown_at IS NOT NULL
                    AND NEW.unknown_at IS DISTINCT FROM OLD.unknown_at THEN
                    RAISE EXCEPTION 'dispatch UNKNOWN timestamp is write-once';
                END IF;
                IF OLD.manual_review_at IS NOT NULL
                    AND NEW.manual_review_at
                        IS DISTINCT FROM OLD.manual_review_at THEN
                    RAISE EXCEPTION 'manual review timestamp is write-once';
                END IF;
                IF OLD.cancelled_at IS NOT NULL
                    AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at THEN
                    RAISE EXCEPTION 'dispatch cancellation is write-once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_dispatch_outbox_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_dispatch_outbox_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
            DECLARE
                action_capability TEXT;
                action_satellite TEXT;
                action_target_type TEXT;
                action_target_ref TEXT;
                action_payload JSONB;
                action_payload_sha256 TEXT;
                action_document_hashes JSONB;
                action_risk_class TEXT;
                action_requires_dual_control BOOLEAN;
                action_requester_id UUID;
                action_case_id UUID;
                action_correlation_id TEXT;
                action_idempotency_key TEXT;
                action_requested_at TIMESTAMPTZ;
                action_contract_version TEXT;
                action_status TEXT;
                authorization_action_id UUID;
                persisted_authorization_version INTEGER;
                authorized_payload_sha256 TEXT;
                authorization_idempotency_key TEXT;
                authorization_authority_code TEXT;
                authorization_authority_version TEXT;
                authorization_decision TEXT;
                authorization_frozen BOOLEAN;
                authorization_authorized_at TIMESTAMPTZ;
                authorization_revoked_at TIMESTAMPTZ;
                authorization_expires_at TIMESTAMPTZ;
                authorization_evidence_level TEXT;
                authorization_modes JSONB;
                authorization_approvers JSONB;
                authorization_legal_effect BOOLEAN;
                parent_manifest_sha256 TEXT;
                parent_binding_sha256 TEXT;
                parent_release_status TEXT;
                parent_emergency_halt BOOLEAN;
                parent_requester_id UUID;
                parent_security_id UUID;
                parent_operations_id UUID;
                parent_simulation_only BOOLEAN;
                parent_external_effects BOOLEAN;
                parent_live_activation BOOLEAN;
                parent_human_activation BOOLEAN;
                parent_provider_pack BOOLEAN;
                parent_requested_at TIMESTAMPTZ;
                parent_valid_until TIMESTAMPTZ;
                parent_simulated_active_at TIMESTAMPTZ;
                parent_daily_action_limit INTEGER;
                parent_max_concurrency INTEGER;
                parent_metadata JSONB;
                candidate_total_limit INTEGER;
                candidate_payload_limit INTEGER;
                existing_total_count BIGINT;
                existing_daily_count BIGINT;
                existing_active_claims BIGINT;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT capability, satellite, target_type, target_ref,
                       payload, payload_sha256, document_hashes, risk_class,
                       requires_dual_control, requested_by_operator_id,
                       case_id, correlation_id, idempotency_key,
                       requested_at, contract_version, status
                  INTO action_capability, action_satellite,
                       action_target_type, action_target_ref, action_payload,
                       action_payload_sha256, action_document_hashes,
                       action_risk_class, action_requires_dual_control,
                       action_requester_id, action_case_id,
                       action_correlation_id, action_idempotency_key,
                       action_requested_at, action_contract_version,
                       action_status
                FROM public.rtm_connect_actions
                WHERE id = NEW.action_id
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch action does not exist';
                END IF;
                SELECT action_id, authorization_version, payload_sha256,
                       idempotency_key, authority_code, authority_version,
                       decision, frozen, authorized_at, revoked_at, expires_at,
                       required_evidence_level, authorized_connector_modes,
                       approved_by_operator_ids, legal_effect_authorized
                  INTO authorization_action_id,
                       persisted_authorization_version,
                       authorized_payload_sha256,
                       authorization_idempotency_key,
                       authorization_authority_code,
                       authorization_authority_version,
                       authorization_decision,
                       authorization_frozen, authorization_authorized_at,
                       authorization_revoked_at,
                       authorization_expires_at,
                       authorization_evidence_level, authorization_modes,
                       authorization_approvers,
                       authorization_legal_effect
                FROM public.rtm_connect_authorizations
                WHERE id = NEW.authorization_id
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch authorization does not exist';
                END IF;
                SELECT manifest_sha256, release_binding_sha256, status,
                       emergency_halt, requested_by_operator_id,
                       security_approved_by_operator_id,
                       operations_approved_by_operator_id,
                       simulation_only, external_effects_allowed,
                       live_activation_allowed, human_activation_required,
                       provider_pack_present, requested_at, valid_until,
                       simulated_active_at,
                       daily_action_limit, max_concurrency, metadata
                  INTO parent_manifest_sha256, parent_binding_sha256,
                       parent_release_status, parent_emergency_halt,
                       parent_requester_id, parent_security_id,
                       parent_operations_id, parent_simulation_only,
                       parent_external_effects, parent_live_activation,
                       parent_human_activation, parent_provider_pack,
                       parent_requested_at, parent_valid_until,
                       parent_simulated_active_at,
                       parent_daily_action_limit, parent_max_concurrency,
                       parent_metadata
                FROM public.rtm_connect_production_releases
                WHERE id = NEW.release_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch release does not exist';
                END IF;
                guard_now := clock_timestamp();
                candidate_total_limit := NULLIF(
                    parent_metadata->'candidate'->>
                        'max_simulated_actions_total',
                    ''
                )::INTEGER;
                candidate_payload_limit := NULLIF(
                    parent_metadata->'candidate'->>'max_payload_bytes',
                    ''
                )::INTEGER;
                IF candidate_total_limit IS NULL
                    OR candidate_total_limit <> 1
                    OR candidate_payload_limit IS NULL
                    OR candidate_payload_limit < 1
                    OR octet_length(regexp_replace(
                        CAST(action_payload AS TEXT),
                        '[[:space:]]+', '', 'g'
                    ))
                        > candidate_payload_limit
                THEN
                    RAISE EXCEPTION
                        'dispatch exceeds frozen candidate limits';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF (NEW.created_at AT TIME ZONE 'UTC')::DATE
                        IS DISTINCT FROM
                        (guard_now AT TIME ZONE 'UTC')::DATE
                    THEN
                        RAISE EXCEPTION
                            'dispatch creation day must be current UTC day';
                    END IF;
                    SELECT COUNT(*), COUNT(*) FILTER (
                        WHERE (created_at AT TIME ZONE 'UTC')::DATE =
                            (guard_now AT TIME ZONE 'UTC')::DATE
                    )
                      INTO existing_total_count, existing_daily_count
                    FROM public.rtm_connect_dispatch_outbox
                    WHERE release_id = NEW.release_id;
                    IF existing_total_count >= candidate_total_limit
                        OR existing_daily_count
                            >= parent_daily_action_limit
                    THEN
                        RAISE EXCEPTION
                            'dispatch frozen release quota exhausted';
                    END IF;
                END IF;
                IF NEW.status = 'claimed' THEN
                    IF NEW.claimed_at IS NULL
                        OR NEW.claim_expires_at IS NULL
                        OR NEW.claimed_at <
                            guard_now - INTERVAL '5 minutes'
                        OR NEW.claimed_at >
                            guard_now + INTERVAL '1 minute'
                        OR NEW.claim_expires_at <= guard_now
                        OR NEW.claim_expires_at >
                            NEW.claimed_at + INTERVAL '300 seconds'
                        OR NEW.claim_expires_at > authorization_expires_at
                        OR NEW.claim_expires_at > parent_valid_until
                    THEN
                        RAISE EXCEPTION
                            'dispatch claim exceeds frozen lease bounds';
                    END IF;
                    SELECT COUNT(*)
                      INTO existing_active_claims
                    FROM public.rtm_connect_dispatch_outbox
                    WHERE release_id = NEW.release_id
                      AND id <> NEW.id
                      AND status = 'claimed'
                      AND claim_expires_at > guard_now;
                    IF existing_active_claims >= parent_max_concurrency THEN
                        RAISE EXCEPTION
                            'dispatch frozen concurrency exhausted';
                    END IF;
                END IF;
                IF action_capability IS DISTINCT FROM
                        'connect.production.admission.simulate'
                    OR action_satellite IS DISTINCT FROM
                        'rtm.connect.production.admission'
                    OR action_target_type IS DISTINCT FROM
                        'production.admission.candidate'
                    OR action_target_ref IS DISTINCT FROM
                        'synthetic-c8-admission'
                    OR action_risk_class IS DISTINCT FROM
                        'R4_critical_regulated'
                    OR action_requires_dual_control IS DISTINCT FROM TRUE
                    OR action_contract_version IS DISTINCT FROM
                        'rtm_connect_contract_v1_0'
                    OR action_requested_at IS NULL
                    OR action_requested_at > guard_now
                    OR NEW.created_at > guard_now
                    OR NEW.updated_at > guard_now
                    OR NEW.updated_at < NEW.created_at
                    OR (
                        NEW.dry_run_confirmed_at IS NOT NULL
                        AND NEW.dry_run_confirmed_at > guard_now
                    )
                    OR (NEW.unknown_at IS NOT NULL AND NEW.unknown_at > guard_now)
                    OR (
                        NEW.manual_review_at IS NOT NULL
                        AND NEW.manual_review_at > guard_now
                    )
                    OR (
                        NEW.cancelled_at IS NOT NULL
                        AND NEW.cancelled_at > guard_now
                    )
                    OR action_requester_id IS DISTINCT FROM parent_requester_id
                    OR action_case_id IS NOT NULL
                    OR action_correlation_id IS NOT NULL
                    OR jsonb_typeof(action_document_hashes)
                        IS DISTINCT FROM 'array'
                    OR jsonb_array_length(action_document_hashes) <> 0
                    OR action_payload IS DISTINCT FROM jsonb_build_object(
                        'contract_version', 'rtm.connect.c8.admission.v1',
                        'candidate_sha256', parent_binding_sha256,
                        'synthetic_marker', 'RTM_C8_SYNTHETIC_ONLY',
                        'simulation_only', TRUE,
                        'external_effects_allowed', FALSE,
                        'live_activation_allowed', FALSE,
                        'human_activation_required', TRUE
                    )
                    OR action_payload_sha256
                        IS DISTINCT FROM NEW.payload_sha256
                    OR action_payload_sha256
                        IS DISTINCT FROM NEW.request_sha256
                    OR authorization_action_id IS DISTINCT FROM NEW.action_id
                    OR persisted_authorization_version
                        IS DISTINCT FROM NEW.authorization_version
                    OR authorized_payload_sha256
                        IS DISTINCT FROM NEW.payload_sha256
                    OR authorization_idempotency_key
                        IS DISTINCT FROM action_idempotency_key
                    OR authorization_authority_code IS DISTINCT FROM
                        'rtm.core.authorization'
                    OR authorization_authority_version IS DISTINCT FROM
                        'rtm_core_authority_v1'
                    OR authorization_decision IS DISTINCT FROM
                        'approved_frozen'
                    OR authorization_frozen IS DISTINCT FROM TRUE
                    OR authorization_authorized_at IS NULL
                    OR authorization_authorized_at < action_requested_at
                    OR authorization_authorized_at > guard_now
                    OR authorization_expires_at
                        > parent_valid_until
                    OR authorization_evidence_level IS DISTINCT FROM
                        'E4_receipt_verified'
                    OR authorization_modes IS DISTINCT FROM
                        jsonb_build_array('assisted')
                    OR jsonb_array_length(authorization_approvers) <> 2
                    OR parent_security_id IS NULL
                    OR parent_operations_id IS NULL
                    OR NOT authorization_approvers @> jsonb_build_array(
                        CAST(parent_security_id AS TEXT),
                        CAST(parent_operations_id AS TEXT)
                    )
                    OR authorization_legal_effect IS DISTINCT FROM FALSE
                    OR parent_manifest_sha256
                        IS DISTINCT FROM NEW.release_manifest_sha256
                    OR parent_binding_sha256
                        IS DISTINCT FROM NEW.release_binding_sha256
                    OR parent_simulation_only IS DISTINCT FROM TRUE
                    OR parent_external_effects IS DISTINCT FROM FALSE
                    OR parent_live_activation IS DISTINCT FROM FALSE
                    OR parent_human_activation IS DISTINCT FROM TRUE
                    OR parent_provider_pack IS DISTINCT FROM FALSE
                    OR NOT NEW.metadata ?& ARRAY[
                        'intent', 'dispatch_binding_sha256',
                        'production_effect_sha256',
                        'expected_admission_payload',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed'
                    ]
                    OR NEW.metadata - ARRAY[
                        'intent', 'dispatch_binding_sha256',
                        'production_effect_sha256',
                        'expected_admission_payload',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR jsonb_typeof(NEW.metadata->'intent')
                        IS DISTINCT FROM 'object'
                    OR NOT NEW.metadata->'intent' ?& ARRAY[
                        'intent_id', 'candidate_id', 'action_id',
                        'authorization_id', 'candidate_sha256',
                        'request_sha256', 'idempotency_key', 'status',
                        'created_at', 'reconciliation_required',
                        'simulation_only', 'external_effects_allowed',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed', 'contract_version'
                    ]
                    OR (NEW.metadata->'intent') - ARRAY[
                        'intent_id', 'candidate_id', 'action_id',
                        'authorization_id', 'candidate_sha256',
                        'request_sha256', 'idempotency_key', 'status',
                        'created_at', 'reconciliation_required',
                        'simulation_only', 'external_effects_allowed',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed', 'contract_version'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR NEW.metadata->'intent'->>'intent_id'
                        IS DISTINCT FROM CAST(NEW.id AS TEXT)
                    OR NEW.metadata->'intent'->>'candidate_id'
                        IS DISTINCT FROM CAST(NEW.release_id AS TEXT)
                    OR NEW.metadata->'intent'->>'action_id'
                        IS DISTINCT FROM CAST(NEW.action_id AS TEXT)
                    OR NEW.metadata->'intent'->>'authorization_id'
                        IS DISTINCT FROM CAST(NEW.authorization_id AS TEXT)
                    OR NEW.metadata->'intent'->>'candidate_sha256'
                        IS DISTINCT FROM NEW.release_binding_sha256
                    OR NEW.metadata->'intent'->>'request_sha256'
                        IS DISTINCT FROM NEW.request_sha256
                    OR NEW.metadata->'intent'->>'idempotency_key'
                        IS DISTINCT FROM action_idempotency_key
                    OR NEW.metadata->'intent'->>'status'
                        IS DISTINCT FROM 'prepared'
                    OR CAST(NEW.metadata->'intent'->>'created_at'
                        AS TIMESTAMPTZ) IS DISTINCT FROM NEW.created_at
                    OR NEW.metadata->'intent'->>'contract_version'
                        IS DISTINCT FROM
                            'rtm.connect.c8.simulated_outbox.v1'
                    OR NEW.metadata->'intent'->'reconciliation_required'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'simulation_only'
                        IS DISTINCT FROM 'true'::jsonb
                    OR NEW.metadata->'intent'->'external_effects_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'network_call_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'secret_resolution_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'blind_retry_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'expected_admission_payload'
                        IS DISTINCT FROM action_payload
                    OR NEW.metadata->'network_call_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'secret_resolution_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'blind_retry_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->>'dispatch_binding_sha256' IS NULL
                    OR NEW.metadata->>'dispatch_binding_sha256'
                        !~ '^[0-9a-f]{64}$'
                    OR NEW.metadata->>'production_effect_sha256' IS NULL
                    OR NEW.metadata->>'production_effect_sha256'
                        !~ '^[0-9a-f]{64}$'
                    OR NEW.business_command_id IS DISTINCT FROM
                        'rtmc8:command:' ||
                        (NEW.metadata->>'production_effect_sha256')
                    OR NEW.production_effect_key IS DISTINCT FROM
                        'rtmc8:dry-run:' ||
                        (NEW.metadata->>'production_effect_sha256')
                    OR (
                        (TG_OP = 'INSERT' OR NEW.status = 'claimed') AND (
                            action_status IS DISTINCT FROM 'authorized'
                            OR authorization_revoked_at IS NOT NULL
                            OR authorization_expires_at IS NULL
                            OR authorization_expires_at <= guard_now
                            OR parent_release_status <> 'simulated_active'
                            OR parent_emergency_halt IS DISTINCT FROM FALSE
                            OR parent_requested_at IS NULL
                            OR parent_requested_at > guard_now
                            OR parent_valid_until IS NULL
                            OR parent_valid_until <= guard_now
                            OR parent_simulated_active_at IS NULL
                            OR parent_simulated_active_at > guard_now
                        )
                    )
                    OR (
                        TG_OP = 'UPDATE'
                        AND NEW.status = 'dry_run_confirmed'
                        AND (
                            OLD.status <> 'claimed'
                            OR OLD.claim_expires_at IS NULL
                            OR OLD.claim_expires_at <= guard_now
                        )
                    )
                THEN
                    RAISE EXCEPTION
                        'dispatch outbox differs from exact action, authorization, or release scope';
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_connect_dispatch_outbox_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_dispatch_outbox_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            DECLARE transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared'
                        OR NEW.version <> 1
                        OR NEW.claim_fence <> 0
                    THEN
                        RAISE EXCEPTION
                            'dispatch outbox must start prepared, unfenced, at version 1';
                    END IF;
                    IF NEW.dry_run_confirmed_at IS NOT NULL
                        OR NEW.unknown_at IS NOT NULL
                        OR NEW.manual_review_at IS NOT NULL
                        OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION
                            'prepared dispatch outbox must have no outcome';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.status = OLD.status THEN
                    RAISE EXCEPTION
                        'dispatch outbox update requires a status transition';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'dispatch outbox version must increment exactly once';
                END IF;
                IF (
                    NEW.claim_owner IS DISTINCT FROM OLD.claim_owner
                    OR NEW.claim_token IS DISTINCT FROM OLD.claim_token
                    OR NEW.claim_fence IS DISTINCT FROM OLD.claim_fence
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.claim_expires_at IS DISTINCT FROM
                        OLD.claim_expires_at
                ) AND NEW.status <> 'claimed' THEN
                    RAISE EXCEPTION
                        'dispatch claim identity may only be set on claim';
                END IF;
                IF NEW.dry_run_confirmed_at
                        IS DISTINCT FROM OLD.dry_run_confirmed_at
                    AND NEW.status <> 'dry_run_confirmed' THEN
                    RAISE EXCEPTION
                        'dry-run timestamp has wrong transition';
                END IF;
                IF NEW.unknown_at IS DISTINCT FROM OLD.unknown_at
                    AND NEW.status <> 'unknown' THEN
                    RAISE EXCEPTION 'UNKNOWN timestamp has wrong transition';
                END IF;
                IF NEW.manual_review_at IS DISTINCT FROM OLD.manual_review_at
                    AND NEW.status <> 'manual_review' THEN
                    RAISE EXCEPTION
                        'manual review timestamp has wrong transition';
                END IF;
                IF NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                    AND NEW.status <> 'cancelled' THEN
                    RAISE EXCEPTION
                        'cancellation timestamp has wrong transition';
                END IF;
                IF OLD.status = 'unknown'
                    AND NEW.status IN ('prepared', 'claimed') THEN
                    RAISE EXCEPTION
                        'UNKNOWN dispatch outcome must never be retried or reclaimed';
                END IF;
                transition_ok := CASE
                    WHEN OLD.status = 'prepared'
                        AND NEW.status IN (
                            'claimed', 'cancelled'
                        ) THEN TRUE
                    WHEN OLD.status = 'claimed'
                        AND NEW.status IN (
                            'dry_run_confirmed', 'unknown',
                            'manual_review', 'cancelled'
                        ) THEN TRUE
                    WHEN OLD.status = 'unknown'
                        AND NEW.status = 'manual_review' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid dispatch outbox transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF OLD.status = 'prepared' AND NEW.status = 'claimed'
                    AND NEW.claim_fence <> OLD.claim_fence + 1 THEN
                    RAISE EXCEPTION
                        'dispatch claim fence must increment exactly once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_manual_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_manual_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_manual_events is append-only';
            END;
            $$;


--
-- Name: rtm_connect_manual_task_package_frozen(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_manual_task_package_frozen() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF (
                    NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                    OR NEW.task_code IS DISTINCT FROM OLD.task_code
                    OR NEW.due_at IS DISTINCT FROM OLD.due_at
                    OR NEW.package_manifest
                        IS DISTINCT FROM OLD.package_manifest
                    OR NEW.package_sha256
                        IS DISTINCT FROM OLD.package_sha256
                    OR NEW.instructions IS DISTINCT FROM OLD.instructions
                ) THEN
                    RAISE EXCEPTION 'manual handoff package is frozen';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_manual_task_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_manual_task_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'manual task must start prepared at version 1';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'manual task version must increment exactly once';
                END IF;

                transition_ok := CASE
                    WHEN OLD.status = 'prepared'
                        AND NEW.status = 'assigned' THEN TRUE
                    WHEN OLD.status = 'assigned'
                        AND NEW.status = 'in_progress' THEN TRUE
                    WHEN OLD.status = 'in_progress'
                        AND NEW.status = 'awaiting_receipt' THEN TRUE
                    WHEN OLD.status = 'awaiting_receipt'
                        AND NEW.status = 'receipt_submitted' THEN TRUE
                    WHEN OLD.status = 'receipt_submitted'
                        AND NEW.status = 'verified' THEN TRUE
                    WHEN OLD.status = 'verified'
                        AND NEW.status = 'completed' THEN TRUE
                    ELSE FALSE
                END;

                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid manual task transition: % -> %',
                        OLD.status, NEW.status;
                END IF;

                IF OLD.status <> 'prepared' AND (
                    NEW.assignee_operator_id
                        IS DISTINCT FROM OLD.assignee_operator_id
                    OR NEW.assigned_by_operator_id
                        IS DISTINCT FROM OLD.assigned_by_operator_id
                    OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
                ) THEN
                    RAISE EXCEPTION
                        'manual task assignment is frozen after assignment';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_production_release_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_production_release_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
            DECLARE
                parent_binding TEXT;
                parent_status TEXT;
                parent_metadata JSONB;
                parent_requested_at TIMESTAMPTZ;
                parent_requester_id UUID;
                parent_security_id UUID;
                parent_operations_id UUID;
                parent_halted_by_id UUID;
                parent_halt_reason TEXT;
                parent_version INTEGER;
                expected_sequence INTEGER;
                previous_status TEXT;
                previous_created_at TIMESTAMPTZ;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT release_binding_sha256, status, metadata, requested_at,
                       requested_by_operator_id,
                       security_approved_by_operator_id,
                       operations_approved_by_operator_id,
                       halted_by_operator_id, halt_reason_code, version
                  INTO parent_binding, parent_status, parent_metadata,
                       parent_requested_at, parent_requester_id,
                       parent_security_id, parent_operations_id,
                       parent_halted_by_id, parent_halt_reason, parent_version
                FROM public.rtm_connect_production_releases
                WHERE id = NEW.release_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'production release event parent missing';
                END IF;
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                  INTO expected_sequence
                FROM public.rtm_connect_production_release_events
                WHERE release_id = NEW.release_id;
                guard_now := clock_timestamp();
                SELECT to_status, created_at
                  INTO previous_status, previous_created_at
                FROM public.rtm_connect_production_release_events
                WHERE release_id = NEW.release_id
                ORDER BY sequence_number DESC
                LIMIT 1;
                IF NEW.release_binding_sha256 IS DISTINCT FROM parent_binding
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.sequence_number IS DISTINCT FROM expected_sequence
                    OR NEW.sequence_number IS DISTINCT FROM parent_version
                    OR (expected_sequence = 1 AND NEW.from_status IS NOT NULL)
                    OR (expected_sequence > 1 AND
                        NEW.from_status IS DISTINCT FROM previous_status)
                    OR NEW.created_at > guard_now
                    OR NEW.created_at < parent_requested_at
                    OR (
                        previous_created_at IS NOT NULL
                        AND NEW.created_at < previous_created_at
                    )
                    OR NOT (
                        (
                            NEW.event_type = 'release_proposed'
                            AND NEW.from_status IS NULL
                            AND NEW.to_status = 'proposed'
                            AND NEW.actor_type = 'requester'
                            AND NEW.operator_id = parent_requester_id
                            AND NEW.reason_code =
                                'simulation_candidate_recorded'
                        ) OR (
                            NEW.event_type = 'security_approval_recorded'
                            AND NEW.from_status = 'proposed'
                            AND NEW.to_status = 'security_approved'
                            AND NEW.actor_type = 'security'
                            AND NEW.operator_id = parent_security_id
                            AND NEW.reason_code =
                                'simulation_admission_approved'
                        ) OR (
                            NEW.event_type = 'operations_approval_recorded'
                            AND NEW.from_status = 'security_approved'
                            AND NEW.to_status = 'operations_approved'
                            AND NEW.actor_type = 'operations'
                            AND NEW.operator_id = parent_operations_id
                            AND NEW.reason_code =
                                'simulation_admission_approved'
                        ) OR (
                            NEW.event_type = 'simulation_release_ready'
                            AND NEW.from_status = 'operations_approved'
                            AND NEW.to_status = 'ready'
                            AND NEW.actor_type = 'operations'
                            AND NEW.operator_id = parent_operations_id
                            AND NEW.reason_code = 'simulation_release_ready'
                        ) OR (
                            NEW.event_type = 'simulation_activation_recorded'
                            AND NEW.from_status = 'ready'
                            AND NEW.to_status = 'simulated_active'
                            AND NEW.actor_type = 'system'
                            AND NEW.operator_id IS NOT NULL
                            AND NEW.operator_id <> parent_requester_id
                            AND NEW.operator_id <> parent_security_id
                            AND NEW.operator_id <> parent_operations_id
                            AND NEW.reason_code =
                                'simulation_activation_recorded'
                        ) OR (
                            NEW.event_type = 'emergency_halt_recorded'
                            AND NEW.from_status IN (
                                'proposed', 'security_approved',
                                'operations_approved', 'ready',
                                'simulated_active'
                            )
                            AND NEW.to_status = 'halted'
                            AND NEW.actor_type = 'system'
                            AND NEW.operator_id = parent_halted_by_id
                            AND NEW.reason_code = parent_halt_reason
                        )
                    )
                    OR (
                        NEW.event_type = 'release_proposed' AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'assessment'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'assessment'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'security_approval_recorded',
                            'operations_approval_recorded'
                        ) AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'approval_id',
                                'approval_sha256', 'approval'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'approval_id',
                                'approval_sha256', 'approval'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'simulation_release_ready',
                            'emergency_halt_recorded'
                        ) AND (
                            NOT NEW.payload ? 'candidate_sha256'
                            OR NEW.payload - 'candidate_sha256'
                                IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type = 'simulation_activation_recorded'
                        AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'human_gate_sha256',
                                'live_activation_allowed',
                                'external_effects_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'human_gate_sha256',
                                'live_activation_allowed',
                                'external_effects_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR NEW.payload->>'candidate_sha256'
                        IS DISTINCT FROM parent_binding
                    OR NEW.payload - ARRAY[
                        'candidate_sha256', 'assessment', 'approval_id',
                        'approval_sha256', 'approval',
                        'human_gate_sha256', 'live_activation_allowed',
                        'external_effects_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR (
                        NEW.payload ? 'assessment'
                        AND NEW.payload->'assessment' IS DISTINCT FROM
                            parent_metadata->'assessment'
                    )
                    OR (
                        NEW.payload ? 'approval' AND (
                            jsonb_typeof(NEW.payload->'approval')
                                IS DISTINCT FROM 'object'
                            OR NOT NEW.payload->'approval' ?& ARRAY[
                                'approval_id', 'candidate_id',
                                'candidate_sha256',
                                'requested_by_operator_id',
                                'approver_operator_id', 'approval_role',
                                'approved_at', 'expires_at', 'decision',
                                'simulation_only',
                                'external_effects_allowed',
                                'live_activation_allowed',
                                'human_activation_required'
                            ]
                            OR (NEW.payload->'approval') - ARRAY[
                                'approval_id', 'candidate_id',
                                'candidate_sha256',
                                'requested_by_operator_id',
                                'approver_operator_id', 'approval_role',
                                'approved_at', 'expires_at', 'decision',
                                'simulation_only',
                                'external_effects_allowed',
                                'live_activation_allowed',
                                'human_activation_required'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->'approval'->>'candidate_id'
                                IS DISTINCT FROM CAST(NEW.release_id AS TEXT)
                            OR NEW.payload->'approval'->>'candidate_sha256'
                                IS DISTINCT FROM parent_binding
                            OR NEW.payload->'approval'->>'approval_id'
                                IS DISTINCT FROM
                                    NEW.payload->>'approval_id'
                            OR NEW.payload->'approval'->
                                'requested_by_operator_id' IS DISTINCT FROM
                                    parent_metadata->'candidate'->
                                        'requested_by_operator_id'
                            OR NEW.payload->'approval'->>'approver_operator_id'
                                IS DISTINCT FROM CAST(NEW.operator_id AS TEXT)
                            OR NEW.payload->'approval'->>'approval_role' IS NULL
                            OR NEW.payload->'approval'->>'approval_role'
                                NOT IN ('security', 'operations')
                            OR NEW.payload->'approval'->>'decision'
                                IS DISTINCT FROM
                                    'simulation_admission_approved'
                            OR NEW.payload->'approval'->'simulation_only'
                                IS DISTINCT FROM 'true'::jsonb
                            OR NEW.payload->'approval'->
                                'external_effects_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                            OR NEW.payload->'approval'->
                                'live_activation_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                            OR NEW.payload->'approval'->
                                'human_activation_required'
                                IS DISTINCT FROM 'true'::jsonb
                        )
                    )
                    OR (
                        NEW.payload ? 'approval_id' AND (
                            NEW.payload->>'approval_id' IS NULL
                            OR NEW.payload->>'approval_id'
                                !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                            OR NEW.payload->>'approval_sha256' IS NULL
                            OR NEW.payload->>'approval_sha256'
                                !~ '^[0-9a-f]{64}$'
                            OR NOT NEW.payload ? 'approval'
                        )
                    )
                    OR (
                        NEW.payload ? 'live_activation_allowed'
                        AND NEW.payload->'live_activation_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'external_effects_allowed'
                        AND NEW.payload->'external_effects_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'human_gate_sha256'
                        AND (
                            NEW.payload->>'human_gate_sha256' IS NULL
                            OR NEW.payload->>'human_gate_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                THEN
                    RAISE EXCEPTION
                        'production release event differs from parent scope or sequence';
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_connect_production_release_frozen_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_production_release_frozen_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.release_code IS DISTINCT FROM OLD.release_code
                    OR NEW.connector_code IS DISTINCT FROM OLD.connector_code
                    OR NEW.connector_version
                        IS DISTINCT FROM OLD.connector_version
                    OR NEW.source_commit_sha
                        IS DISTINCT FROM OLD.source_commit_sha
                    OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
                    OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
                    OR NEW.schema_sha256 IS DISTINCT FROM OLD.schema_sha256
                    OR NEW.build_artifact_sha256
                        IS DISTINCT FROM OLD.build_artifact_sha256
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM OLD.release_binding_sha256
                    OR NEW.requested_by_operator_id
                        IS DISTINCT FROM OLD.requested_by_operator_id
                    OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                    OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
                    OR NEW.simulation_only IS DISTINCT FROM OLD.simulation_only
                    OR NEW.external_effects_allowed
                        IS DISTINCT FROM OLD.external_effects_allowed
                    OR NEW.live_activation_allowed
                        IS DISTINCT FROM OLD.live_activation_allowed
                    OR NEW.human_activation_required
                        IS DISTINCT FROM OLD.human_activation_required
                    OR NEW.provider_pack_present
                        IS DISTINCT FROM OLD.provider_pack_present
                    OR NEW.canary_percent IS DISTINCT FROM OLD.canary_percent
                    OR NEW.max_concurrency IS DISTINCT FROM OLD.max_concurrency
                    OR NEW.daily_action_limit
                        IS DISTINCT FROM OLD.daily_action_limit
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION
                        'production release binding and inert limits are frozen';
                END IF;
                IF OLD.security_approved_by_operator_id IS NOT NULL AND (
                    NEW.security_approved_by_operator_id IS DISTINCT FROM
                        OLD.security_approved_by_operator_id
                    OR NEW.security_approval_sha256 IS DISTINCT FROM
                        OLD.security_approval_sha256
                    OR NEW.security_approved_at IS DISTINCT FROM
                        OLD.security_approved_at
                ) THEN
                    RAISE EXCEPTION
                        'production security approval is write-once';
                END IF;
                IF OLD.operations_approved_by_operator_id IS NOT NULL AND (
                    NEW.operations_approved_by_operator_id IS DISTINCT FROM
                        OLD.operations_approved_by_operator_id
                    OR NEW.operations_approval_sha256 IS DISTINCT FROM
                        OLD.operations_approval_sha256
                    OR NEW.operations_approved_at IS DISTINCT FROM
                        OLD.operations_approved_at
                ) THEN
                    RAISE EXCEPTION
                        'production operations approval is write-once';
                END IF;
                IF OLD.ready_at IS NOT NULL
                    AND NEW.ready_at IS DISTINCT FROM OLD.ready_at THEN
                    RAISE EXCEPTION 'production ready timestamp is write-once';
                END IF;
                IF OLD.simulated_active_at IS NOT NULL
                    AND NEW.simulated_active_at
                        IS DISTINCT FROM OLD.simulated_active_at THEN
                    RAISE EXCEPTION
                        'production simulated activation is write-once';
                END IF;
                IF OLD.emergency_halt = TRUE AND (
                    NEW.emergency_halt IS DISTINCT FROM OLD.emergency_halt
                    OR NEW.halted_at IS DISTINCT FROM OLD.halted_at
                    OR NEW.halted_by_operator_id
                        IS DISTINCT FROM OLD.halted_by_operator_id
                    OR NEW.halt_reason_code IS DISTINCT FROM OLD.halt_reason_code
                ) THEN
                    RAISE EXCEPTION 'production emergency halt is terminal';
                END IF;
                IF OLD.rejected_at IS NOT NULL AND (
                    NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
                    OR NEW.rejected_by_operator_id
                        IS DISTINCT FROM OLD.rejected_by_operator_id
                    OR NEW.rejection_reason_code
                        IS DISTINCT FROM OLD.rejection_reason_code
                ) THEN
                    RAISE EXCEPTION 'production rejection is write-once';
                END IF;
                IF OLD.expired_at IS NOT NULL
                    AND NEW.expired_at IS DISTINCT FROM OLD.expired_at THEN
                    RAISE EXCEPTION 'production expiry is write-once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_production_release_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_production_release_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
                guard_now TIMESTAMPTZ;
            BEGIN
                guard_now := clock_timestamp();
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'proposed' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'production release must start proposed at version 1';
                    END IF;
                    IF NEW.requested_at > guard_now
                        OR NEW.created_at > guard_now
                        OR NEW.updated_at > guard_now
                        OR NEW.valid_until <= guard_now
                    THEN
                        RAISE EXCEPTION
                            'production release validity must include the current database time';
                    END IF;
                    IF NEW.security_approved_by_operator_id IS NOT NULL
                        OR NEW.security_approval_sha256 IS NOT NULL
                        OR NEW.security_approved_at IS NOT NULL
                        OR NEW.operations_approved_by_operator_id IS NOT NULL
                        OR NEW.operations_approval_sha256 IS NOT NULL
                        OR NEW.operations_approved_at IS NOT NULL
                        OR NEW.ready_at IS NOT NULL
                        OR NEW.simulated_active_at IS NOT NULL
                        OR NEW.emergency_halt = TRUE
                        OR NEW.halted_at IS NOT NULL
                        OR NEW.halted_by_operator_id IS NOT NULL
                        OR NEW.halt_reason_code IS NOT NULL
                        OR NEW.rejected_at IS NOT NULL
                        OR NEW.rejected_by_operator_id IS NOT NULL
                        OR NEW.rejection_reason_code IS NOT NULL
                        OR NEW.expired_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION
                            'proposed production release must have no decisions';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.status = OLD.status THEN
                    RAISE EXCEPTION
                        'production release update requires a status transition';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'production release version must increment exactly once';
                END IF;
                IF NEW.updated_at < OLD.updated_at
                    OR NEW.updated_at > guard_now
                    OR (
                        NEW.security_approved_at IS NOT NULL
                        AND NEW.security_approved_at > guard_now
                    )
                    OR (
                        NEW.operations_approved_at IS NOT NULL
                        AND NEW.operations_approved_at > guard_now
                    )
                    OR (NEW.ready_at IS NOT NULL AND NEW.ready_at > guard_now)
                    OR (
                        NEW.simulated_active_at IS NOT NULL
                        AND NEW.simulated_active_at > guard_now
                    )
                    OR (NEW.halted_at IS NOT NULL AND NEW.halted_at > guard_now)
                    OR (
                        NEW.rejected_at IS NOT NULL
                        AND NEW.rejected_at > guard_now
                    )
                    OR (NEW.expired_at IS NOT NULL AND NEW.expired_at > guard_now)
                THEN
                    RAISE EXCEPTION
                        'production release transition timestamps cannot be in the future';
                END IF;
                IF (
                    NEW.security_approved_by_operator_id IS DISTINCT FROM
                        OLD.security_approved_by_operator_id
                    OR NEW.security_approval_sha256 IS DISTINCT FROM
                        OLD.security_approval_sha256
                    OR NEW.security_approved_at IS DISTINCT FROM
                        OLD.security_approved_at
                ) AND NEW.status <> 'security_approved' THEN
                    RAISE EXCEPTION
                        'security identity may only be set by security approval';
                END IF;
                IF (
                    NEW.operations_approved_by_operator_id IS DISTINCT FROM
                        OLD.operations_approved_by_operator_id
                    OR NEW.operations_approval_sha256 IS DISTINCT FROM
                        OLD.operations_approval_sha256
                    OR NEW.operations_approved_at IS DISTINCT FROM
                        OLD.operations_approved_at
                ) AND NEW.status <> 'operations_approved' THEN
                    RAISE EXCEPTION
                        'operations identity may only be set by operations approval';
                END IF;
                IF NEW.ready_at IS DISTINCT FROM OLD.ready_at
                    AND NEW.status <> 'ready' THEN
                    RAISE EXCEPTION
                        'ready timestamp may only be set on ready transition';
                END IF;
                IF NEW.simulated_active_at
                        IS DISTINCT FROM OLD.simulated_active_at
                    AND NEW.status <> 'simulated_active' THEN
                    RAISE EXCEPTION
                        'simulated activation timestamp has wrong transition';
                END IF;
                IF (
                    NEW.emergency_halt IS DISTINCT FROM OLD.emergency_halt
                    OR NEW.halted_at IS DISTINCT FROM OLD.halted_at
                    OR NEW.halted_by_operator_id
                        IS DISTINCT FROM OLD.halted_by_operator_id
                    OR NEW.halt_reason_code IS DISTINCT FROM OLD.halt_reason_code
                ) AND NEW.status <> 'halted' THEN
                    RAISE EXCEPTION
                        'emergency halt fields have wrong transition';
                END IF;
                IF (
                    NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
                    OR NEW.rejected_by_operator_id
                        IS DISTINCT FROM OLD.rejected_by_operator_id
                    OR NEW.rejection_reason_code
                        IS DISTINCT FROM OLD.rejection_reason_code
                ) AND NEW.status <> 'rejected' THEN
                    RAISE EXCEPTION 'rejection fields have wrong transition';
                END IF;
                IF NEW.expired_at IS DISTINCT FROM OLD.expired_at
                    AND NEW.status <> 'expired' THEN
                    RAISE EXCEPTION 'expiry field has wrong transition';
                END IF;
                transition_ok := CASE
                    WHEN NEW.status = 'halted'
                        AND OLD.status NOT IN ('halted', 'rejected', 'expired')
                        THEN TRUE
                    WHEN OLD.status = 'proposed'
                        AND NEW.status IN (
                            'security_approved', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'security_approved'
                        AND NEW.status IN (
                            'operations_approved', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'operations_approved'
                        AND NEW.status IN ('ready', 'rejected', 'expired')
                        THEN TRUE
                    WHEN OLD.status = 'ready'
                        AND NEW.status IN (
                            'simulated_active', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'simulated_active'
                        AND NEW.status = 'expired' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid production release transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_reconciliation_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_reconciliation_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                parent_status TEXT;
                parent_resolution TEXT;
                parent_action_id UUID;
                parent_attempt_id UUID;
                parent_webhook_id UUID;
                parent_evidence_id UUID;
                parent_version INTEGER;
            BEGIN
                SELECT status, resolution, action_id, attempt_id,
                       webhook_inbox_id, evidence_id, version
                INTO parent_status, parent_resolution, parent_action_id,
                     parent_attempt_id, parent_webhook_id,
                     parent_evidence_id, parent_version
                FROM rtm_connect_reconciliations
                WHERE id = NEW.reconciliation_id;
                IF NOT FOUND
                    OR NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.attempt_id IS DISTINCT FROM parent_attempt_id
                    OR NEW.webhook_inbox_id
                        IS DISTINCT FROM parent_webhook_id
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.resolution IS DISTINCT FROM parent_resolution
                    OR NEW.evidence_id
                        IS DISTINCT FROM parent_evidence_id
                    OR NEW.sequence_number <> parent_version THEN
                    RAISE EXCEPTION
                        'reconciliation event differs from parent scope';
                END IF;
                IF parent_status = 'started' AND (
                    NEW.from_status IS NOT NULL
                    OR NEW.event_type <> 'reconciliation.started'
                ) THEN
                    RAISE EXCEPTION
                        'invalid reconciliation started event';
                END IF;
                IF parent_status = 'resolved' AND (
                    NEW.from_status <> 'started'
                    OR NEW.event_type <> 'reconciliation.resolved'
                ) THEN
                    RAISE EXCEPTION
                        'invalid reconciliation resolved event';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_reconciliation_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_reconciliation_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_reconciliation_events is append-only';
            END;
            $$;


--
-- Name: rtm_connect_reconciliation_identity_frozen(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_reconciliation_identity_frozen() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF (
                    NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.webhook_inbox_id
                        IS DISTINCT FROM OLD.webhook_inbox_id
                    OR NEW.reconciliation_number
                        IS DISTINCT FROM OLD.reconciliation_number
                    OR NEW.request_sha256
                        IS DISTINCT FROM OLD.request_sha256
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                    OR NEW.started_at IS DISTINCT FROM OLD.started_at
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'reconciliation identity is frozen';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_reconciliation_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_reconciliation_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'started' OR NEW.version <> 1
                        OR NEW.resolution IS NOT NULL
                        OR NEW.resolved_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'reconciliation must start at version 1';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM rtm_connect_webhook_inbox w
                        JOIN rtm_connect_attempts x
                            ON x.id = NEW.attempt_id
                        JOIN rtm_connect_actions a
                            ON a.id = NEW.action_id
                        JOIN rtm_connect_connectors c
                            ON c.id = x.connector_id
                        WHERE w.id = NEW.webhook_inbox_id
                          AND w.status = 'matched'
                          AND w.matched_action_id = NEW.action_id
                          AND w.matched_attempt_id = NEW.attempt_id
                          AND x.action_id = NEW.action_id
                          AND x.status = 'unknown'
                          AND x.reconciliation_required = TRUE
                          AND c.status = 'active'
                          AND c.environment = 'staging'
                          AND c.synthetic_only = TRUE
                          AND c.credential_ref IS NULL
                          AND c.supports_reconciliation = TRUE
                          AND NEW.request_sha256 = x.request_sha256
                          AND NEW.external_reference = x.external_reference
                          AND a.status = 'unknown'
                    ) THEN
                        RAISE EXCEPTION
                            'reconciliation scope is not an exact match';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status <> 'started' OR NEW.status <> 'resolved' THEN
                    RAISE EXCEPTION
                        'invalid reconciliation transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF OLD.version <> 1 OR NEW.version <> 2 THEN
                    RAISE EXCEPTION
                        'reconciliation resolves exactly at version 2';
                END IF;
                IF NEW.resolution = 'confirmed' AND NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_evidence e
                    JOIN rtm_connect_webhook_inbox w
                        ON w.id = NEW.webhook_inbox_id
                    WHERE e.id = NEW.evidence_id
                      AND e.action_id = NEW.action_id
                      AND e.attempt_id = NEW.attempt_id
                      AND e.evidence_level = 'E4_receipt_verified'
                      AND e.request_sha256 = NEW.request_sha256
                      AND e.external_reference = NEW.external_reference
                      AND e.receipt_sha256 = w.receipt_sha256
                      AND e.receipt_storage_ref = w.receipt_storage_ref
                ) THEN
                    RAISE EXCEPTION
                        'confirmed reconciliation requires exact E4';
                END IF;
                IF NEW.resolution <> 'confirmed'
                    AND NEW.evidence_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'non-confirmed reconciliation cannot bind evidence';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_actions a
                    JOIN rtm_connect_attempts x
                        ON x.action_id = a.id
                    JOIN rtm_connect_connectors c
                        ON c.id = x.connector_id
                    JOIN rtm_connect_webhook_inbox w
                        ON w.id = NEW.webhook_inbox_id
                    WHERE a.id = NEW.action_id
                      AND x.id = NEW.attempt_id
                      AND w.status = 'matched'
                      AND w.matched_action_id = NEW.action_id
                      AND w.matched_attempt_id = NEW.attempt_id
                      AND NEW.resolution = w.reported_outcome
                      AND c.status = 'active'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE
                      AND c.credential_ref IS NULL
                      AND c.supports_reconciliation = TRUE
                      AND a.payload_sha256 = NEW.request_sha256
                      AND x.request_sha256 = NEW.request_sha256
                      AND a.external_reference = NEW.external_reference
                      AND x.external_reference = NEW.external_reference
                      AND a.status = NEW.resolution
                      AND x.status = CASE
                          WHEN NEW.resolution = 'confirmed'
                              THEN 'succeeded'
                          WHEN NEW.resolution = 'unknown'
                              THEN 'unknown'
                          ELSE 'failed'
                      END
                      AND x.retryable =
                          (NEW.resolution = 'retryable_failed')
                      AND x.reconciliation_required =
                          (NEW.resolution = 'unknown')
                ) THEN
                    RAISE EXCEPTION
                        'reconciliation resolution differs from CORE scope';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_webhook_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_webhook_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                inbox_status TEXT;
                inbox_action_id UUID;
                inbox_attempt_id UUID;
                expected_sequence INTEGER;
            BEGIN
                SELECT status, matched_action_id, matched_attempt_id,
                       version - replay_count
                INTO inbox_status, inbox_action_id, inbox_attempt_id,
                     expected_sequence
                FROM rtm_connect_webhook_inbox
                WHERE id = NEW.webhook_inbox_id;
                IF NOT FOUND
                    OR NEW.to_status IS DISTINCT FROM inbox_status
                    OR NEW.sequence_number <> expected_sequence
                    OR NEW.event_type <>
                        ('webhook.' || inbox_status) THEN
                    RAISE EXCEPTION
                        'webhook event does not match inbox state';
                END IF;
                IF (NEW.action_id IS NULL) <>
                        (NEW.attempt_id IS NULL) THEN
                    RAISE EXCEPTION
                        'webhook event scope must be complete or empty';
                END IF;
                IF NEW.action_id IS NULL OR NEW.attempt_id IS NULL THEN
                    IF inbox_action_id IS NOT NULL
                        OR inbox_attempt_id IS NOT NULL THEN
                        RAISE EXCEPTION
                            'webhook event omits resolved scope';
                    END IF;
                ELSIF NEW.action_id IS DISTINCT FROM inbox_action_id
                    OR NEW.attempt_id IS DISTINCT FROM inbox_attempt_id THEN
                    RAISE EXCEPTION
                        'webhook event scope differs from inbox';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_webhook_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_webhook_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_webhook_events is append-only';
            END;
            $$;


--
-- Name: rtm_connect_webhook_identity_frozen(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_webhook_identity_frozen() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF (
                    NEW.ingress_connector_id
                        IS DISTINCT FROM OLD.ingress_connector_id
                    OR NEW.source_event_id
                        IS DISTINCT FROM OLD.source_event_id
                    OR NEW.event_type IS DISTINCT FROM OLD.event_type
                    OR NEW.deduplication_key
                        IS DISTINCT FROM OLD.deduplication_key
                    OR NEW.origin_connector_code
                        IS DISTINCT FROM OLD.origin_connector_code
                    OR NEW.origin_connector_version
                        IS DISTINCT FROM OLD.origin_connector_version
                    OR NEW.reported_outcome
                        IS DISTINCT FROM OLD.reported_outcome
                    OR NEW.claimed_action_id
                        IS DISTINCT FROM OLD.claimed_action_id
                    OR NEW.claimed_attempt_id
                        IS DISTINCT FROM OLD.claimed_attempt_id
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                    OR NEW.request_sha256
                        IS DISTINCT FROM OLD.request_sha256
                    OR NEW.payload IS DISTINCT FROM OLD.payload
                    OR NEW.payload_sha256
                        IS DISTINCT FROM OLD.payload_sha256
                    OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
                    OR NEW.received_at IS DISTINCT FROM OLD.received_at
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'webhook identity and payload are frozen';
                END IF;
                IF (
                    (OLD.verification_method IS NOT NULL AND
                        NEW.verification_method
                            IS DISTINCT FROM OLD.verification_method)
                    OR (OLD.verification_sha256 IS NOT NULL AND
                        NEW.verification_sha256
                            IS DISTINCT FROM OLD.verification_sha256)
                    OR (OLD.receipt_sha256 IS NOT NULL AND
                        NEW.receipt_sha256
                            IS DISTINCT FROM OLD.receipt_sha256)
                    OR (OLD.receipt_storage_ref IS NOT NULL AND
                        NEW.receipt_storage_ref
                            IS DISTINCT FROM OLD.receipt_storage_ref)
                ) THEN
                    RAISE EXCEPTION
                        'webhook verification and receipt are write-once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_webhook_match_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_webhook_match_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                scope_ok BOOLEAN := FALSE;
            BEGIN
                IF NEW.status <> 'matched' OR OLD.status = 'matched' THEN
                    RETURN NEW;
                END IF;

                IF NEW.matched_action_id IS DISTINCT FROM
                        NEW.claimed_action_id
                    OR NEW.matched_attempt_id IS DISTINCT FROM
                        NEW.claimed_attempt_id THEN
                    RAISE EXCEPTION
                        'webhook matched scope differs from claimed scope';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_attempts x
                    JOIN rtm_connect_actions a
                        ON a.id = x.action_id
                    JOIN rtm_connect_connectors c
                        ON c.id = x.connector_id
                    WHERE x.id = NEW.matched_attempt_id
                      AND x.action_id = NEW.matched_action_id
                      AND x.status = 'unknown'
                      AND x.reconciliation_required = TRUE
                      AND x.request_sha256 = NEW.request_sha256
                      AND x.external_reference = NEW.external_reference
                      AND c.code = NEW.origin_connector_code
                      AND c.version = NEW.origin_connector_version
                      AND c.status = 'active'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE
                      AND c.credential_ref IS NULL
                      AND c.supports_reconciliation = TRUE
                      AND c.id IS DISTINCT FROM NEW.ingress_connector_id
                      AND a.status = 'unknown'
                ) INTO scope_ok;

                IF NOT scope_ok THEN
                    RAISE EXCEPTION
                        'webhook matched scope does not correlate exactly';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_connect_webhook_state_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_connect_webhook_state_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'received' OR NEW.version <> 1
                        OR NEW.replay_count <> 0 THEN
                        RAISE EXCEPTION
                            'webhook must start received at version 1';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'webhook version must increment exactly once';
                END IF;

                IF NEW.status = OLD.status THEN
                    IF NEW.replay_count <> OLD.replay_count + 1
                        OR NEW.last_seen_at < OLD.last_seen_at
                        OR NEW.matched_action_id
                            IS DISTINCT FROM OLD.matched_action_id
                        OR NEW.matched_attempt_id
                            IS DISTINCT FROM OLD.matched_attempt_id
                        OR NEW.matched_at IS DISTINCT FROM OLD.matched_at
                        OR NEW.processed_at IS DISTINCT FROM OLD.processed_at
                        OR NEW.dead_letter_reason_code
                            IS DISTINCT FROM OLD.dead_letter_reason_code
                        OR NEW.dead_letter_reason_detail
                            IS DISTINCT FROM OLD.dead_letter_reason_detail
                        OR NEW.verification_method
                            IS DISTINCT FROM OLD.verification_method
                        OR NEW.verification_sha256
                            IS DISTINCT FROM OLD.verification_sha256
                        OR NEW.receipt_sha256
                            IS DISTINCT FROM OLD.receipt_sha256
                        OR NEW.receipt_storage_ref
                            IS DISTINCT FROM OLD.receipt_storage_ref
                        OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                        RAISE EXCEPTION
                            'same-state webhook update must be exact replay';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.replay_count <> OLD.replay_count
                    OR NEW.last_seen_at IS DISTINCT FROM OLD.last_seen_at THEN
                    RAISE EXCEPTION
                        'webhook replay counters change only on replay';
                END IF;

                transition_ok := CASE
                    WHEN OLD.status = 'received'
                        AND NEW.status IN (
                            'verified', 'dead_lettered'
                        ) THEN TRUE
                    WHEN OLD.status = 'verified'
                        AND NEW.status IN (
                            'matched', 'dead_lettered'
                        ) THEN TRUE
                    WHEN OLD.status = 'matched'
                        AND NEW.status IN (
                            'processed', 'dead_lettered'
                        ) THEN TRUE
                    ELSE FALSE
                END;

                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid webhook transition: % -> %',
                        OLD.status, NEW.status;
                END IF;

                IF OLD.matched_action_id IS NOT NULL AND (
                    NEW.matched_action_id
                        IS DISTINCT FROM OLD.matched_action_id
                    OR NEW.matched_attempt_id
                        IS DISTINCT FROM OLD.matched_attempt_id
                    OR NEW.matched_at IS DISTINCT FROM OLD.matched_at
                ) THEN
                    RAISE EXCEPTION
                        'webhook resolved correlation is frozen after match';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_guard_attention_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_guard_attention_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_attention_events is append-only; % is not permitted',
                    TG_OP
                    USING ERRCODE = '55000';
            END;
            $$;


--
-- Name: rtm_guard_connect_action_transition(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_guard_connect_action_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                allowed BOOLEAN := FALSE;
            BEGIN
                IF NEW.status = OLD.status THEN
                    NEW.updated_at := NOW();
                    RETURN NEW;
                END IF;

                allowed := CASE OLD.status
                    WHEN 'draft' THEN NEW.status IN ('authorized', 'cancelled')
                    WHEN 'authorized' THEN NEW.status IN ('queued', 'cancelled')
                    WHEN 'queued' THEN NEW.status IN ('executing', 'cancelled')
                    WHEN 'executing' THEN NEW.status IN (
                        'external_accepted', 'confirmed', 'retryable_failed',
                        'unknown', 'manual_review', 'permanent_failed'
                    )
                    WHEN 'external_accepted' THEN NEW.status IN (
                        'evidence_pending', 'confirmed', 'unknown',
                        'reconciling', 'manual_review'
                    )
                    WHEN 'evidence_pending' THEN NEW.status IN (
                        'confirmed', 'unknown', 'reconciling', 'manual_review'
                    )
                    WHEN 'retryable_failed' THEN NEW.status IN (
                        'queued', 'reconciling', 'manual_review', 'cancelled'
                    )
                    WHEN 'unknown' THEN NEW.status IN (
                        'reconciling', 'manual_review'
                    )
                    WHEN 'reconciling' THEN NEW.status IN (
                        'confirmed', 'retryable_failed', 'unknown',
                        'manual_review', 'permanent_failed'
                    )
                    WHEN 'manual_review' THEN NEW.status IN (
                        'queued', 'reconciling', 'confirmed',
                        'permanent_failed', 'cancelled'
                    )
                    ELSE FALSE
                END;

                IF NOT allowed THEN
                    RAISE EXCEPTION
                        'Invalid RTM CONNECT transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                NEW.status_version := OLD.status_version + 1;
                NEW.updated_at := NOW();
                IF NEW.status = 'unknown' AND NEW.unknown_since IS NULL THEN
                    NEW.unknown_since := NOW();
                END IF;
                IF NEW.status = 'confirmed' AND NEW.confirmed_at IS NULL THEN
                    NEW.confirmed_at := NOW();
                END IF;
                IF NEW.status = 'cancelled' AND NEW.cancelled_at IS NULL THEN
                    NEW.cancelled_at := NOW();
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_guard_connect_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_guard_connect_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = '55000';
            END;
            $$;


--
-- Name: rtm_guard_operator_access_events_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_guard_operator_access_events_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_operator_access_events is append-only; % is not permitted',
                    TG_OP
                    USING ERRCODE = '55000';
            END;
            $$;


--
-- Name: rtm_guard_operator_access_evidence_retention(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_guard_operator_access_evidence_retention() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION
                        'rtm_operator_access_evidence is immutable; UPDATE is not permitted'
                        USING ERRCODE = '55000';
                END IF;

                IF TG_OP = 'DELETE' THEN
                    IF OLD.retention_until <= NOW()
                       AND current_setting(
                           'rtm.operator_access_evidence_purge',
                           TRUE
                       ) = 'enabled'
                    THEN
                        RETURN OLD;
                    END IF;

                    RAISE EXCEPTION
                        'rtm_operator_access_evidence is retention-protected'
                        USING ERRCODE = '55000';
                END IF;

                RETURN OLD;
            END;
            $$;


--
-- Name: rtm_presenter_admin_export_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_admin_export_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
            DECLARE
                package_case UUID;
                export_doc RECORD;
                admin_ok BOOLEAN := FALSE;
                reauthentication_ok BOOLEAN := FALSE;
                scope_session_id UUID;
                scope_event_id UUID;
                value TEXT;
            BEGIN
                IF NEW.package_id IS NOT NULL THEN
                    SELECT case_id INTO package_case
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.package_id AND status = 'frozen';
                    IF package_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter admin export package mismatch';
                    END IF;
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r
                      ON r.id = o.primary_role_id
                    WHERE o.id = NEW.admin_operator_id
                      AND o.status = 'active'
                      AND r.active = TRUE
                      AND r.code = 'rtm.admin'
                      AND r.permissions ? 'ops.documents.export_exceptional'
                ) INTO admin_ok;
                IF NOT admin_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export permission missing';
                END IF;

                IF NEW.reauthenticated_at > NEW.created_at
                   OR NEW.reauthenticated_at <
                        NEW.created_at - INTERVAL '5 minutes' THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication stale';
                END IF;

                IF COALESCE(
                        NEW.export_scope->>'operator_session_id', ''
                    ) !~ '^[0-9a-fA-F-]{36}$'
                   OR COALESCE(
                        NEW.export_scope->>'reauthentication_event_id', ''
                    ) !~ '^[0-9a-fA-F-]{36}$' THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication scope missing';
                END IF;
                scope_session_id := (
                    NEW.export_scope->>'operator_session_id'
                )::UUID;
                scope_event_id := (
                    NEW.export_scope->>'reauthentication_event_id'
                )::UUID;
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operator_sessions s
                    JOIN rtm_operator_access_events e
                      ON e.id = scope_event_id
                     AND e.session_id = s.id
                     AND e.operator_id = s.operator_id
                    WHERE s.id = scope_session_id
                      AND s.operator_id = NEW.admin_operator_id
                      AND s.status = 'active'
                      AND s.expires_at > NEW.created_at
                      AND (
                          s.absolute_expires_at IS NULL
                          OR s.absolute_expires_at > NEW.created_at
                      )
                      AND s.last_verified_at > s.login_at
                      AND s.last_verified_at = NEW.reauthenticated_at
                      AND e.event_type = 'auth.reauthenticated'
                      AND e.result = 'success'
                      AND e.reason_code = 'password_reverified'
                      AND e.occurred_at = s.last_verified_at
                ) INTO reauthentication_ok;
                IF NOT reauthentication_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication evidence invalid';
                END IF;

                FOR value IN
                    SELECT jsonb_array_elements_text(NEW.source_hashes)
                LOOP
                    IF value !~ '^[0-9a-f]{64}$' THEN
                        RAISE EXCEPTION
                            'RTM Presenter export source hash invalid';
                    END IF;
                END LOOP;

                IF NEW.export_document_id IS NOT NULL THEN
                    SELECT case_id, sha256 INTO export_doc
                    FROM documents
                    WHERE id = NEW.export_document_id;
                    IF export_doc IS NULL
                       OR export_doc.case_id IS DISTINCT FROM NEW.case_id
                       OR export_doc.sha256 IS DISTINCT FROM
                            NEW.export_sha256 THEN
                        RAISE EXCEPTION
                            'RTM Presenter export document/hash mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_presenter_audit_event_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_audit_event_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                related_case UUID;
            BEGIN
                IF NEW.package_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.package_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit package scope mismatch';
                    END IF;
                END IF;
                IF NEW.package_item_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_package_items
                    WHERE id = NEW.package_item_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit item scope mismatch';
                    END IF;
                END IF;
                IF NEW.handoff_ticket_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_handoff_tickets
                    WHERE id = NEW.handoff_ticket_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit ticket scope mismatch';
                    END IF;
                END IF;
                IF NEW.admin_export_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_admin_exports
                    WHERE id = NEW.admin_export_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit export scope mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_destination_profile_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_destination_profile_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                previous_version INTEGER;
            BEGIN
                IF NEW.version_number > 1 THEN
                    SELECT MAX(version_number) INTO previous_version
                    FROM rtm_presenter_destination_profiles
                    WHERE profile_code = NEW.profile_code;
                    IF previous_version IS DISTINCT FROM
                            NEW.version_number - 1 THEN
                        RAISE EXCEPTION
                            'Presenter destination profile version gap';
                    END IF;
                END IF;
                IF NEW.verified_at IS NOT NULL
                   AND NEW.verified_at > NEW.created_at THEN
                    RAISE EXCEPTION
                        'Presenter profile verification cannot be future';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_document_version_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_document_version_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
            DECLARE
                source_ok BOOLEAN := FALSE;
                predecessor RECORD;
            BEGIN
                -- La misma clave se toma antes de congelar desde el servicio.
                -- Al ser un xact lock, insertar y congelar una linea documental
                -- quedan ordenados hasta commit, incluso cuando aun no existe
                -- una fila nueva que se pueda bloquear con FOR UPDATE.
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        'rtm-presenter-document-lineage:'
                        || NEW.case_id::TEXT || ':'
                        || NEW.logical_document_id::TEXT,
                        0
                    )
                );

                SELECT EXISTS (
                    SELECT 1
                    FROM documents d
                    WHERE d.id = NEW.source_document_id
                      AND d.case_id = NEW.case_id
                      AND d.sha256 = NEW.sha256
                      AND d.sha256 ~ '^[0-9a-f]{64}$'
                      AND COALESCE(d.size_bytes, 0) = NEW.size_bytes
                ) INTO source_ok;
                IF NOT source_ok THEN
                    RAISE EXCEPTION
                        'Presenter document source/case/hash/size mismatch';
                END IF;

                IF NEW.version_number = 1 THEN
                    IF NEW.supersedes_version_id IS NOT NULL THEN
                        RAISE EXCEPTION
                            'Presenter document v1 cannot supersede another row';
                    END IF;
                ELSE
                    SELECT case_id, logical_document_id, version_number
                    INTO predecessor
                    FROM rtm_presenter_document_versions
                    WHERE id = NEW.supersedes_version_id
                    FOR UPDATE;
                    IF predecessor IS NULL
                       OR predecessor.case_id IS DISTINCT FROM NEW.case_id
                       OR predecessor.logical_document_id
                            IS DISTINCT FROM NEW.logical_document_id
                       OR predecessor.version_number
                            IS DISTINCT FROM NEW.version_number - 1 THEN
                        RAISE EXCEPTION
                            'Presenter document predecessor mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $_$;


--
-- Name: rtm_presenter_filing_package_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_filing_package_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                profile_status TEXT;
                authorization_ok BOOLEAN := FALSE;
                predecessor RECORD;
                actual_item_count INTEGER;
                invalid_items INTEGER;
                locked_document RECORD;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'RTM Presenter package cannot be deleted';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    IF OLD.status = 'frozen' THEN
                        RAISE EXCEPTION
                            'RTM Presenter frozen package is immutable';
                    END IF;
                    IF OLD.status <> 'draft' THEN
                        RAISE EXCEPTION
                            'RTM Presenter non-draft package is immutable';
                    END IF;
                    IF NEW.id IS DISTINCT FROM OLD.id
                       OR NEW.case_id IS DISTINCT FROM OLD.case_id
                       OR NEW.logical_package_id
                            IS DISTINCT FROM OLD.logical_package_id
                       OR NEW.package_version
                            IS DISTINCT FROM OLD.package_version
                       OR NEW.supersedes_package_id
                            IS DISTINCT FROM OLD.supersedes_package_id
                       OR NEW.destination_profile_id
                            IS DISTINCT FROM OLD.destination_profile_id
                       OR NEW.representation_mode
                            IS DISTINCT FROM OLD.representation_mode
                       OR NEW.authorization_document_version_id
                            IS DISTINCT FROM
                                OLD.authorization_document_version_id
                       OR NEW.created_by_operator_id
                            IS DISTINCT FROM OLD.created_by_operator_id
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION
                            'RTM Presenter package identity is write-once';
                    END IF;
                    IF NEW.status NOT IN ('draft', 'frozen', 'cancelled') THEN
                        RAISE EXCEPTION
                            'RTM Presenter package transition rejected';
                    END IF;
                END IF;

                IF NEW.status = 'frozen' THEN
                    -- Orden comun con el servicio para evitar ciclos entre
                    -- freezes que compartan mas de una linea documental.
                    FOR locked_document IN
                        SELECT DISTINCT v.case_id, v.logical_document_id
                        FROM rtm_presenter_package_items i
                        JOIN rtm_presenter_document_versions v
                          ON v.id = i.document_version_id
                        WHERE i.package_id = NEW.id
                        ORDER BY v.case_id, v.logical_document_id
                    LOOP
                        PERFORM pg_advisory_xact_lock(
                            hashtextextended(
                                'rtm-presenter-document-lineage:'
                                || locked_document.case_id::TEXT || ':'
                                || locked_document.logical_document_id::TEXT,
                                0
                            )
                        );
                    END LOOP;
                END IF;

                SELECT status INTO profile_status
                FROM rtm_presenter_destination_profiles
                WHERE id = NEW.destination_profile_id;
                IF profile_status IS DISTINCT FROM 'active' THEN
                    RAISE EXCEPTION
                        'RTM Presenter requires active destination profile';
                END IF;

                IF NEW.representation_mode = 'representative' THEN
                    SELECT EXISTS (
                        SELECT 1
                        FROM rtm_presenter_document_versions v
                        WHERE v.id = NEW.authorization_document_version_id
                          AND v.case_id = NEW.case_id
                          AND v.purpose IN (
                              'representation', 'signed_authorization',
                              'representation_authorization'
                          )
                          AND v.state = 'active'
                          AND v.scan_status = 'clean'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM rtm_presenter_document_versions newer
                              WHERE newer.case_id = v.case_id
                                AND newer.logical_document_id =
                                      v.logical_document_id
                                AND newer.version_number > v.version_number
                                AND newer.state = 'active'
                                AND newer.scan_status = 'clean'
                          )
                    ) INTO authorization_ok;
                    IF NOT authorization_ok THEN
                        RAISE EXCEPTION
                            'Presenter representation authorization invalid';
                    END IF;
                END IF;

                IF TG_OP = 'INSERT' AND NEW.package_version > 1 THEN
                    SELECT case_id, logical_package_id, package_version, status
                    INTO predecessor
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.supersedes_package_id;
                    IF predecessor IS NULL
                       OR predecessor.case_id IS DISTINCT FROM NEW.case_id
                       OR predecessor.logical_package_id
                            IS DISTINCT FROM NEW.logical_package_id
                       OR predecessor.package_version
                            IS DISTINCT FROM NEW.package_version - 1
                       OR predecessor.status IS DISTINCT FROM 'frozen' THEN
                        RAISE EXCEPTION
                            'RTM Presenter package predecessor mismatch';
                    END IF;
                END IF;

                IF NEW.status = 'frozen' THEN
                    SELECT COUNT(*), COUNT(*) FILTER (
                        WHERE v.case_id IS DISTINCT FROM NEW.case_id
                           OR v.sha256 IS DISTINCT FROM i.document_sha256
                           OR v.state IS DISTINCT FROM 'active'
                           OR v.scan_status IS DISTINCT FROM 'clean'
                           OR EXISTS (
                               SELECT 1
                               FROM rtm_presenter_document_versions newer
                               WHERE newer.case_id = v.case_id
                                 AND newer.logical_document_id =
                                       v.logical_document_id
                                 AND newer.version_number > v.version_number
                                 AND newer.state = 'active'
                                 AND newer.scan_status = 'clean'
                           )
                    )
                    INTO actual_item_count, invalid_items
                    FROM rtm_presenter_package_items i
                    JOIN rtm_presenter_document_versions v
                      ON v.id = i.document_version_id
                    WHERE i.package_id = NEW.id;
                    IF actual_item_count IS DISTINCT FROM
                            NEW.expected_item_count
                       OR actual_item_count < 1
                       OR invalid_items <> 0 THEN
                        RAISE EXCEPTION
                            'RTM Presenter package items are not freeze-ready';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_handoff_ticket_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_handoff_ticket_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                package RECORD;
                item RECORD;
                profile_origin TEXT;
                session_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket cannot be deleted';
                END IF;

                SELECT p.case_id, p.status, p.destination_profile_id
                INTO package
                FROM rtm_presenter_filing_packages p
                WHERE p.id = NEW.package_id;
                SELECT i.package_id, i.case_id, i.field_code
                INTO item
                FROM rtm_presenter_package_items i
                WHERE i.id = NEW.package_item_id;
                SELECT portal_origin INTO profile_origin
                FROM rtm_presenter_destination_profiles
                WHERE id = package.destination_profile_id;
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operator_sessions s
                    JOIN rtm_operators o ON o.id = s.operator_id
                    WHERE s.id = NEW.operator_session_id
                      AND s.operator_id = NEW.operator_id
                      AND s.status = 'active'
                      AND s.expires_at > NOW()
                      AND o.status = 'active'
                ) INTO session_ok;

                IF package IS NULL OR item IS NULL
                   OR package.status IS DISTINCT FROM 'frozen'
                   OR package.case_id IS DISTINCT FROM NEW.case_id
                   OR item.package_id IS DISTINCT FROM NEW.package_id
                   OR item.case_id IS DISTINCT FROM NEW.case_id
                   OR item.field_code IS DISTINCT FROM NEW.field_code
                   OR profile_origin IS DISTINCT FROM NEW.portal_origin
                   OR NOT session_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff scope/session mismatch';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.used_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'RTM Presenter ticket must be issued unused';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.used_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket is single-use';
                END IF;
                IF NEW.used_at IS NULL OR NEW.used_at > NEW.expires_at
                   OR NOW() > NEW.expires_at THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket expired or not consumed';
                END IF;
                IF (to_jsonb(NEW) - 'used_at') IS DISTINCT FROM
                        (to_jsonb(OLD) - 'used_at') THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket fields are immutable';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_idempotency_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_idempotency_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                package RECORD;
            BEGIN
                SELECT case_id, created_by_operator_id, status
                INTO package
                FROM rtm_presenter_filing_packages
                WHERE id = NEW.package_id;
                IF package IS NULL
                   OR package.case_id IS DISTINCT FROM NEW.case_id
                   OR package.created_by_operator_id
                        IS DISTINCT FROM NEW.operator_id
                   OR package.status IS DISTINCT FROM 'frozen' THEN
                    RAISE EXCEPTION
                        'RTM Presenter idempotency scope mismatch';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_package_item_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_package_item_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                parent RECORD;
                document RECORD;
                target_package_id UUID;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    target_package_id := OLD.package_id;
                ELSE
                    target_package_id := NEW.package_id;
                END IF;
                SELECT case_id, status INTO parent
                FROM rtm_presenter_filing_packages
                WHERE id = target_package_id
                FOR UPDATE;
                IF parent IS NULL THEN
                    RAISE EXCEPTION 'RTM Presenter package not found';
                END IF;
                IF parent.status <> 'draft' THEN
                    RAISE EXCEPTION
                        'RTM Presenter frozen package items are immutable';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                IF NEW.case_id IS DISTINCT FROM parent.case_id THEN
                    RAISE EXCEPTION 'Presenter package item case mismatch';
                END IF;
                SELECT case_id, sha256, purpose, state, scan_status
                INTO document
                FROM rtm_presenter_document_versions
                WHERE id = NEW.document_version_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM rtm_presenter_document_versions newer
                      WHERE newer.case_id =
                              rtm_presenter_document_versions.case_id
                        AND newer.logical_document_id =
                              rtm_presenter_document_versions.logical_document_id
                        AND newer.version_number >
                              rtm_presenter_document_versions.version_number
                        AND newer.state = 'active'
                        AND newer.scan_status = 'clean'
                  );
                IF document IS NULL
                   OR document.case_id IS DISTINCT FROM NEW.case_id
                   OR document.sha256 IS DISTINCT FROM NEW.document_sha256
                   OR document.purpose IS DISTINCT FROM NEW.purpose
                   OR document.state IS DISTINCT FROM 'active'
                   OR document.scan_status IS DISTINCT FROM 'clean' THEN
                    RAISE EXCEPTION
                        'Presenter package item document binding invalid';
                END IF;
                IF TG_OP = 'UPDATE' AND (
                    NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.package_id IS DISTINCT FROM OLD.package_id
                    OR NEW.case_id IS DISTINCT FROM OLD.case_id
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'RTM Presenter package item identity is write-once';
                END IF;
                RETURN NEW;
            END;
            $$;


--
-- Name: rtm_presenter_reject_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'RTM Presenter append-only row cannot mutate';
            END;
            $$;


--
-- Name: rtm_presenter_signer_installation_scope_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rtm_presenter_signer_installation_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            DECLARE
                signer_device_ok BOOLEAN := FALSE;
            BEGIN
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operator_devices d
                    JOIN rtm_operators o
                      ON o.id = d.operator_id
                    JOIN rtm_operator_roles r
                      ON r.id = o.primary_role_id
                    WHERE d.id = NEW.operator_device_id
                      AND d.operator_id = NEW.operator_id
                      AND d.status IN ('known', 'trusted')
                      AND o.status = 'active'
                      AND r.active = TRUE
                      AND r.code = 'rtm.signer'
                      AND jsonb_typeof(r.permissions) = 'array'
                      AND jsonb_array_length(r.permissions) = 3
                      AND r.permissions @> '[
                          "ops.view",
                          "presenter.signing.queue",
                          "presenter.signing.claim"
                      ]'::jsonb
                ) INTO signer_device_ok;
                IF NOT signer_device_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter signer installation device invalid';
                END IF;
                IF NEW.registered_at < NOW() - INTERVAL '5 minutes'
                   OR NEW.registered_at > NOW() + INTERVAL '1 minute' THEN
                    RAISE EXCEPTION
                        'RTM Presenter signer installation time invalid';
                END IF;
                RETURN NEW;
            END;
            $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    contact_email text,
    status text DEFAULT 'uploaded'::text NOT NULL,
    category text,
    organismo text,
    expediente_ref text,
    notified_at date,
    deadline_main date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    department text,
    case_type text,
    customer_comment text,
    source_module text,
    interested_data jsonb,
    authorized boolean DEFAULT false NOT NULL,
    authorized_at timestamp with time zone,
    authorization_version text,
    authorization_ip text,
    authorization_user_agent text,
    authorization_full_name text,
    authorization_dni_nie text,
    authorization_address text,
    authorization_email text,
    authorization_phone text,
    authorization_checks jsonb,
    authorization_snapshot jsonb,
    payment_status text,
    product_code text,
    stripe_session_id text,
    stripe_payment_intent text,
    paid_at timestamp with time zone,
    channel text DEFAULT 'direct'::text NOT NULL,
    partner_id uuid,
    partner_name text,
    contact_name text,
    test_mode boolean DEFAULT false NOT NULL,
    override_deadlines boolean DEFAULT false NOT NULL
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    kind text NOT NULL,
    b2_bucket text,
    b2_key text,
    sha256 text,
    mime text,
    size_bytes bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid,
    type text NOT NULL,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extractions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    extracted_json jsonb NOT NULL,
    confidence double precision,
    model text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ops_followups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ops_followups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    kind text DEFAULT 'seguimiento'::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    title text NOT NULL,
    description text,
    due_at timestamp with time zone,
    source_event_type text,
    created_by text DEFAULT 'ops'::text NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by text,
    resolution_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ops_followups_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'resolved'::text])))
);


--
-- Name: partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    password_salt text NOT NULL,
    password_hash text NOT NULL,
    api_token text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    billing_mode text DEFAULT 'monthly'::text NOT NULL,
    billing_status text DEFAULT 'current'::text NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL
);


--
-- Name: rtm_attention_engine_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_attention_engine_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_key text NOT NULL,
    engine_version text NOT NULL,
    environment text NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    triggered_by text DEFAULT 'system'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    scanned_count integer DEFAULT 0 NOT NULL,
    created_count integer DEFAULT 0 NOT NULL,
    updated_count integer DEFAULT 0 NOT NULL,
    resolved_count integer DEFAULT 0 NOT NULL,
    error_count integer DEFAULT 0 NOT NULL,
    error_summary text,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_engine_run_finished CHECK ((((status = 'running'::text) AND (finished_at IS NULL)) OR ((status <> 'running'::text) AND (finished_at IS NOT NULL)))),
    CONSTRAINT rtm_attention_engine_runs_created_count_check CHECK ((created_count >= 0)),
    CONSTRAINT rtm_attention_engine_runs_error_count_check CHECK ((error_count >= 0)),
    CONSTRAINT rtm_attention_engine_runs_metrics_check CHECK ((jsonb_typeof(metrics) = 'object'::text)),
    CONSTRAINT rtm_attention_engine_runs_resolved_count_check CHECK ((resolved_count >= 0)),
    CONSTRAINT rtm_attention_engine_runs_scanned_count_check CHECK ((scanned_count >= 0)),
    CONSTRAINT rtm_attention_engine_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text, 'partial'::text, 'skipped'::text]))),
    CONSTRAINT rtm_attention_engine_runs_updated_count_check CHECK ((updated_count >= 0))
);


--
-- Name: rtm_attention_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_attention_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    attention_item_id uuid,
    case_id uuid,
    operator_id uuid,
    session_id uuid,
    actor_type text NOT NULL,
    event_type text NOT NULL,
    result text DEFAULT 'success'::text NOT NULL,
    reason text,
    request_id text,
    previous_state jsonb DEFAULT '{}'::jsonb NOT NULL,
    new_state jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rtm_attention_events_actor_type_check CHECK ((actor_type = ANY (ARRAY['operator'::text, 'system'::text, 'integration'::text, 'migration'::text]))),
    CONSTRAINT rtm_attention_events_new_state_check CHECK ((jsonb_typeof(new_state) = 'object'::text)),
    CONSTRAINT rtm_attention_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT rtm_attention_events_previous_state_check CHECK ((jsonb_typeof(previous_state) = 'object'::text)),
    CONSTRAINT rtm_attention_events_result_check CHECK ((result = ANY (ARRAY['success'::text, 'failure'::text, 'denied'::text, 'noop'::text])))
);


--
-- Name: rtm_attention_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_attention_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid,
    satellite text DEFAULT 'other'::text NOT NULL,
    attention_class text NOT NULL,
    code text NOT NULL,
    dedupe_key text NOT NULL,
    title text NOT NULL,
    summary text,
    severity text DEFAULT 'attention'::text NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    source_event_id uuid,
    source_document_id uuid,
    source_entity_type text,
    source_entity_id uuid,
    due_at timestamp with time zone,
    assigned_operator_id uuid,
    assigned_at timestamp with time zone,
    seen_by uuid,
    seen_at timestamp with time zone,
    in_review_by uuid,
    in_review_at timestamp with time zone,
    resolved_by uuid,
    resolved_at timestamp with time zone,
    resolution_code text,
    resolution_note text,
    version integer DEFAULT 1 NOT NULL,
    first_detected_at timestamp with time zone DEFAULT now() NOT NULL,
    last_detected_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_attention_assignment_state CHECK (((assigned_operator_id IS NULL) OR (assigned_at IS NOT NULL))),
    CONSTRAINT ck_rtm_attention_resolution_state CHECK ((((status = 'resolved'::text) AND (resolved_at IS NOT NULL)) OR ((status <> 'resolved'::text) AND (resolved_at IS NULL)))),
    CONSTRAINT rtm_attention_items_attention_class_check CHECK ((attention_class = ANY (ARRAY['deadline'::text, 'document'::text, 'workflow'::text, 'data_quality'::text, 'assignment'::text, 'system_health'::text, 'security'::text]))),
    CONSTRAINT rtm_attention_items_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_attention_items_severity_check CHECK ((severity = ANY (ARRAY['informational'::text, 'attention'::text, 'upcoming'::text, 'urgent'::text, 'critical'::text]))),
    CONSTRAINT rtm_attention_items_status_check CHECK ((status = ANY (ARRAY['new'::text, 'seen'::text, 'assigned'::text, 'in_review'::text, 'resolved'::text]))),
    CONSTRAINT rtm_attention_items_version_check CHECK ((version > 0))
);


--
-- Name: rtm_connect_a1s_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    task_id uuid NOT NULL,
    approval_type text NOT NULL,
    decision text NOT NULL,
    membership_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    operator_id uuid NOT NULL,
    attestation_sha256 text NOT NULL,
    artifact_id uuid NOT NULL,
    approved_at timestamp with time zone NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_approval_decision CHECK (((decision = 'approved_frozen'::text) AND (synthetic_only = true) AND (attestation_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_a1s_approval_time CHECK ((approved_at <= created_at)),
    CONSTRAINT ck_rtm_connect_a1s_approval_type CHECK ((approval_type = ANY (ARRAY['release'::text, 'verification_preapproval'::text])))
);


--
-- Name: rtm_connect_a1s_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_artifacts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    task_id uuid NOT NULL,
    artifact_code text NOT NULL,
    kind text NOT NULL,
    media_type text DEFAULT 'application/json'::text NOT NULL,
    sha256 text NOT NULL,
    canonical_payload jsonb NOT NULL,
    submitted_by_membership_id uuid NOT NULL,
    submitted_by_principal_id uuid NOT NULL,
    submitted_by_operator_id uuid NOT NULL,
    verified_by_membership_id uuid,
    verified_by_principal_id uuid,
    verified_by_operator_id uuid,
    verified_at timestamp with time zone,
    synthetic_only boolean DEFAULT true NOT NULL,
    storage_backend text DEFAULT 'database_manifest_only'::text NOT NULL,
    supersedes_artifact_id uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_artifact_code CHECK ((artifact_code ~ '^rtm-a1s-artifact-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_artifact_hash CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_artifact_kind CHECK ((kind = ANY (ARRAY['authority_snapshot'::text, 'representation_evidence'::text, 'filing_package'::text, 'human_review_attestation'::text, 'release_attestation'::text, 'verification_preapproval_attestation'::text, 'synthetic_submission_report'::text, 'synthetic_receipt'::text, 'verification_attestation'::text, 'reconciliation_attestation'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_artifact_payload CHECK (((jsonb_typeof(canonical_payload) = 'object'::text) AND (canonical_payload @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_artifact_storage CHECK (((synthetic_only = true) AND (storage_backend = 'database_manifest_only'::text) AND (media_type = 'application/json'::text))),
    CONSTRAINT ck_rtm_connect_a1s_artifact_verification CHECK ((((verified_at IS NULL) AND (verified_by_membership_id IS NULL) AND (verified_by_principal_id IS NULL) AND (verified_by_operator_id IS NULL)) OR ((verified_at IS NOT NULL) AND (verified_by_membership_id IS NOT NULL) AND (verified_by_principal_id IS NOT NULL) AND (verified_by_operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_artifact_version CHECK ((version = 1))
);


--
-- Name: rtm_connect_a1s_case_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_case_bindings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    case_id uuid NOT NULL,
    binding_code text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    case_snapshot_sha256 text NOT NULL,
    bound_by_operator_id uuid NOT NULL,
    bound_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_by_operator_id uuid,
    revoked_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_binding_code CHECK ((binding_code ~ '^rtm-a1s-binding-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_binding_hash CHECK ((case_snapshot_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_binding_status CHECK (((status = ANY (ARRAY['active'::text, 'revoked'::text])) AND (((status = 'active'::text) AND (revoked_at IS NULL) AND (revoked_by_operator_id IS NULL)) OR ((status = 'revoked'::text) AND (revoked_at IS NOT NULL) AND (revoked_by_operator_id IS NOT NULL))))),
    CONSTRAINT ck_rtm_connect_a1s_binding_synthetic CHECK (((synthetic_only = true) AND (jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"test_mode": true, "synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_binding_version CHECK ((version > 0))
);


--
-- Name: rtm_connect_a1s_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    membership_id uuid,
    principal_id uuid,
    operator_id uuid,
    from_status text,
    to_status text,
    reason_code text NOT NULL,
    payload_sha256 text NOT NULL,
    payload jsonb NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_event_actor CHECK ((((actor_type = 'operator'::text) AND (membership_id IS NOT NULL) AND (principal_id IS NOT NULL) AND (operator_id IS NOT NULL)) OR ((actor_type = ANY (ARRAY['connect'::text, 'core'::text, 'system'::text])) AND (membership_id IS NULL) AND (principal_id IS NULL) AND (operator_id IS NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_event_hash CHECK (((payload_sha256 ~ '^[0-9a-f]{64}$'::text) AND (synthetic_only = true) AND (jsonb_typeof(payload) = 'object'::text) AND (payload @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_a1s_event_states CHECK ((((from_status IS NULL) OR (from_status = ANY (ARRAY['prepared'::text, 'assigned'::text, 'reviewing'::text, 'ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text]))) AND ((to_status IS NULL) OR (to_status = ANY (ARRAY['prepared'::text, 'assigned'::text, 'reviewing'::text, 'ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text]))))),
    CONSTRAINT ck_rtm_connect_a1s_event_type CHECK (((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text) AND (reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)))
);


--
-- Name: rtm_connect_a1s_human_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_human_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    case_binding_id uuid NOT NULL,
    representation_evidence_id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    connector_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    authorization_version integer NOT NULL,
    task_code text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    requester_membership_id uuid NOT NULL,
    requester_principal_id uuid NOT NULL,
    requester_operator_id uuid NOT NULL,
    assignee_membership_id uuid,
    assignee_principal_id uuid,
    assignee_operator_id uuid,
    assigned_by_operator_id uuid,
    release_membership_id uuid,
    release_principal_id uuid,
    release_operator_id uuid,
    verified_by_membership_id uuid,
    verified_by_principal_id uuid,
    verified_by_operator_id uuid,
    due_at timestamp with time zone NOT NULL,
    assigned_at timestamp with time zone,
    reviewed_at timestamp with time zone,
    ready_at timestamp with time zone,
    released_at timestamp with time zone,
    started_at timestamp with time zone,
    awaiting_receipt_at timestamp with time zone,
    unknown_at timestamp with time zone,
    reconciling_at timestamp with time zone,
    receipt_submitted_at timestamp with time zone,
    verified_at timestamp with time zone,
    completed_at timestamp with time zone,
    package_manifest jsonb NOT NULL,
    package_sha256 text NOT NULL,
    review_attestation_sha256 text,
    release_attestation_sha256 text,
    verification_attestation_sha256 text,
    external_reference text,
    version integer DEFAULT 1 NOT NULL,
    status_version integer GENERATED ALWAYS AS (version) STORED,
    metadata jsonb DEFAULT '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_task_assignment CHECK ((((status = 'prepared'::text) AND (assignee_membership_id IS NULL) AND (assignee_principal_id IS NULL) AND (assignee_operator_id IS NULL) AND (assigned_by_operator_id IS NULL) AND (assigned_at IS NULL)) OR ((status <> 'prepared'::text) AND ((assignee_membership_id IS NOT NULL) AND (assignee_principal_id IS NOT NULL) AND (assignee_operator_id IS NOT NULL) AND (assigned_by_operator_id IS NOT NULL) AND (assigned_at IS NOT NULL))))),
    CONSTRAINT ck_rtm_connect_a1s_task_awaiting CHECK (((status <> ALL (ARRAY['awaiting_receipt'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR (awaiting_receipt_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_a1s_task_code CHECK ((task_code ~ '^rtm-a1s-human-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_task_completed CHECK (((status <> 'completed'::text) OR (completed_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_a1s_task_due CHECK ((due_at > created_at)),
    CONSTRAINT ck_rtm_connect_a1s_task_external_reference CHECK (((external_reference IS NULL) OR (external_reference ~ '^a1s-synthetic-[0-9a-f]{24}$'::text))),
    CONSTRAINT ck_rtm_connect_a1s_task_package_hash CHECK (((package_sha256 ~ '^[0-9a-f]{64}$'::text) AND ((review_attestation_sha256 IS NULL) OR (review_attestation_sha256 ~ '^[0-9a-f]{64}$'::text)) AND ((release_attestation_sha256 IS NULL) OR (release_attestation_sha256 ~ '^[0-9a-f]{64}$'::text)) AND ((verification_attestation_sha256 IS NULL) OR (verification_attestation_sha256 ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT ck_rtm_connect_a1s_task_package_scope CHECK (((jsonb_typeof(package_manifest) = 'object'::text) AND (package_manifest @> '{"b2_used": false, "network_used": false, "synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY", "provider_contacted": false, "legal_submission_executed": false}'::jsonb) AND (jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_task_receipt CHECK (((status <> ALL (ARRAY['receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR ((receipt_submitted_at IS NOT NULL) AND (external_reference IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_task_reconciling CHECK (((status <> 'reconciling'::text) OR (reconciling_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_a1s_task_release CHECK (((status <> ALL (ARRAY['released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR ((release_membership_id IS NOT NULL) AND (release_principal_id IS NOT NULL) AND (release_operator_id IS NOT NULL) AND (released_at IS NOT NULL) AND (release_attestation_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_task_review CHECK (((status <> ALL (ARRAY['ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR ((reviewed_at IS NOT NULL) AND (ready_at IS NOT NULL) AND (review_attestation_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_task_separation CHECK ((((release_principal_id IS NULL) OR (release_principal_id <> requester_principal_id)) AND ((release_principal_id IS NULL) OR (assignee_principal_id IS NULL) OR (release_principal_id <> assignee_principal_id)) AND ((verified_by_principal_id IS NULL) OR (verified_by_principal_id <> requester_principal_id)) AND ((verified_by_principal_id IS NULL) OR (assignee_principal_id IS NULL) OR (verified_by_principal_id <> assignee_principal_id)) AND ((verified_by_principal_id IS NULL) OR (release_principal_id IS NULL) OR (verified_by_principal_id <> release_principal_id)))),
    CONSTRAINT ck_rtm_connect_a1s_task_started CHECK (((status <> ALL (ARRAY['in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR (started_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_a1s_task_status CHECK ((status = ANY (ARRAY['prepared'::text, 'assigned'::text, 'reviewing'::text, 'ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_task_unknown CHECK (((status <> ALL (ARRAY['outcome_unknown'::text, 'reconciling'::text, 'permanent_failed'::text])) OR (unknown_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_a1s_task_verified CHECK (((status <> ALL (ARRAY['verified'::text, 'completed'::text])) OR ((verified_by_membership_id IS NOT NULL) AND (verified_by_principal_id IS NOT NULL) AND (verified_by_operator_id IS NOT NULL) AND (verified_at IS NOT NULL) AND (verification_attestation_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_task_version CHECK (((version > 0) AND (authorization_version > 0)))
);


--
-- Name: rtm_connect_a1s_idempotency; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_idempotency (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    scope text NOT NULL,
    request_sha256 text NOT NULL,
    response_sha256 text,
    task_id uuid,
    action_id uuid,
    status text DEFAULT 'claimed'::text NOT NULL,
    claimed_by_membership_id uuid NOT NULL,
    claimed_by_principal_id uuid NOT NULL,
    claimed_by_operator_id uuid NOT NULL,
    replay_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    metadata jsonb DEFAULT '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_idempotency_completion CHECK ((((status = 'claimed'::text) AND (response_sha256 IS NULL) AND (completed_at IS NULL)) OR ((status = ANY (ARRAY['completed'::text, 'conflict'::text])) AND (response_sha256 IS NOT NULL) AND (completed_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_idempotency_expiry CHECK ((expires_at > created_at)),
    CONSTRAINT ck_rtm_connect_a1s_idempotency_hashes CHECK (((request_sha256 ~ '^[0-9a-f]{64}$'::text) AND ((response_sha256 IS NULL) OR (response_sha256 ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT ck_rtm_connect_a1s_idempotency_key CHECK (((idempotency_key ~ '^rtma1s:[0-9a-f]{64}$'::text) AND (scope ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))),
    CONSTRAINT ck_rtm_connect_a1s_idempotency_metadata CHECK (((jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_idempotency_status CHECK (((status = ANY (ARRAY['claimed'::text, 'completed'::text, 'conflict'::text])) AND (replay_count >= 0)))
);


--
-- Name: rtm_connect_a1s_memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_memberships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    operator_id uuid NOT NULL,
    role text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    granted_by_operator_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_by_operator_id uuid,
    revoked_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_membership_revocation CHECK ((((status = 'active'::text) AND (revoked_at IS NULL) AND (revoked_by_operator_id IS NULL)) OR ((status = 'revoked'::text) AND (revoked_at IS NOT NULL) AND (revoked_by_operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_a1s_membership_role CHECK ((role = ANY (ARRAY['requester'::text, 'executor'::text, 'releaser'::text, 'verifier'::text, 'supervisor'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_membership_status CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_membership_synthetic CHECK (((synthetic_only = true) AND (jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_membership_version CHECK ((version > 0))
);


--
-- Name: rtm_connect_a1s_representation_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_representation_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    case_binding_id uuid NOT NULL,
    representation_code text NOT NULL,
    kind text NOT NULL,
    subject_ref_sha256 text NOT NULL,
    evidence_sha256 text NOT NULL,
    canonical_evidence jsonb NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    recorded_by_membership_id uuid NOT NULL,
    recorded_by_principal_id uuid NOT NULL,
    recorded_by_operator_id uuid NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_by_operator_id uuid,
    revoked_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_representation_code CHECK ((representation_code ~ '^rtm-a1s-representation-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_representation_hashes CHECK (((subject_ref_sha256 ~ '^[0-9a-f]{64}$'::text) AND (evidence_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_a1s_representation_kind CHECK ((kind = ANY (ARRAY['synthetic_power_of_attorney'::text, 'synthetic_signed_authorization'::text, 'synthetic_legal_representative_attestation'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_representation_payload CHECK (((jsonb_typeof(canonical_evidence) = 'object'::text) AND (canonical_evidence @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb))),
    CONSTRAINT ck_rtm_connect_a1s_representation_status CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text, 'expired'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_representation_synthetic CHECK ((synthetic_only = true)),
    CONSTRAINT ck_rtm_connect_a1s_representation_version CHECK ((version > 0)),
    CONSTRAINT ck_rtm_connect_a1s_representation_vigency CHECK (((expires_at > valid_from) AND (((status = 'active'::text) AND (revoked_at IS NULL)) OR ((status = 'revoked'::text) AND (revoked_at IS NOT NULL)) OR (status = 'expired'::text))))
);


--
-- Name: rtm_connect_a1s_tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_a1s_tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_code text NOT NULL,
    display_name text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_a1s_tenant_code CHECK ((tenant_code ~ '^a1s-synthetic-[a-z0-9-]{3,48}$'::text)),
    CONSTRAINT ck_rtm_connect_a1s_tenant_display_name CHECK (((length(display_name) >= 3) AND (length(display_name) <= 96))),
    CONSTRAINT ck_rtm_connect_a1s_tenant_status CHECK ((status = ANY (ARRAY['active'::text, 'suspended'::text, 'disabled'::text]))),
    CONSTRAINT ck_rtm_connect_a1s_tenant_synthetic CHECK (((synthetic_only = true) AND (jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"synthetic_only": true, "synthetic_marker": "RTM_A1S_SYNTHETIC_ONLY"}'::jsonb)))
);


--
-- Name: rtm_connect_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_actions (
    id uuid NOT NULL,
    case_id uuid,
    capability text NOT NULL,
    satellite text NOT NULL,
    target_type text NOT NULL,
    target_ref text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    document_hashes jsonb DEFAULT '[]'::jsonb NOT NULL,
    risk_class text NOT NULL,
    requires_dual_control boolean DEFAULT false NOT NULL,
    requested_by_operator_id uuid NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    contract_version text NOT NULL,
    correlation_id text,
    status text DEFAULT 'draft'::text NOT NULL,
    status_version integer DEFAULT 1 NOT NULL,
    idempotency_key text NOT NULL,
    current_connector_id uuid,
    external_reference text,
    next_attempt_at timestamp with time zone,
    unknown_since timestamp with time zone,
    confirmed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_action_cancelled_at CHECK (((status <> 'cancelled'::text) OR (cancelled_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_action_capability CHECK ((capability ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_action_confirmed_at CHECK (((status <> 'confirmed'::text) OR (confirmed_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_action_document_hashes CHECK ((jsonb_typeof(document_hashes) = 'array'::text)),
    CONSTRAINT ck_rtm_connect_action_idempotency_key CHECK ((idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_action_payload_sha256 CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_action_r4_dual_control CHECK (((risk_class <> 'R4_critical_regulated'::text) OR (requires_dual_control = true))),
    CONSTRAINT ck_rtm_connect_action_risk CHECK ((risk_class = ANY (ARRAY['R0_observation'::text, 'R1_low_reversible'::text, 'R2_business_effect'::text, 'R3_legal_or_financial'::text, 'R4_critical_regulated'::text]))),
    CONSTRAINT ck_rtm_connect_action_satellite CHECK ((satellite ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_action_status CHECK ((status = ANY (ARRAY['draft'::text, 'authorized'::text, 'queued'::text, 'executing'::text, 'external_accepted'::text, 'evidence_pending'::text, 'confirmed'::text, 'retryable_failed'::text, 'unknown'::text, 'reconciling'::text, 'manual_review'::text, 'permanent_failed'::text, 'cancelled'::text]))),
    CONSTRAINT ck_rtm_connect_action_status_version CHECK ((status_version > 0)),
    CONSTRAINT ck_rtm_connect_action_target_type CHECK ((target_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_action_unknown_at CHECK (((status <> 'unknown'::text) OR (unknown_since IS NOT NULL))),
    CONSTRAINT rtm_connect_actions_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_connect_actions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: rtm_connect_assisted_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_assisted_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text,
    reason_code text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_assisted_event_actor CHECK ((actor_type = ANY (ARRAY['operator'::text, 'connect'::text, 'core'::text, 'reconciliation'::text, 'system'::text]))),
    CONSTRAINT ck_rtm_connect_assisted_event_payload CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_assisted_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_assisted_event_type CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))
);


--
-- Name: rtm_connect_assisted_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_assisted_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    connector_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    authorization_version integer NOT NULL,
    task_code text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    assignee_operator_id uuid,
    assigned_by_operator_id uuid,
    assigned_at timestamp with time zone,
    release_operator_id uuid,
    released_at timestamp with time zone,
    verified_by_operator_id uuid,
    due_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    reviewed_at timestamp with time zone,
    ready_at timestamp with time zone,
    unknown_at timestamp with time zone,
    reconciling_at timestamp with time zone,
    receipt_submitted_at timestamp with time zone,
    verified_at timestamp with time zone,
    completed_at timestamp with time zone,
    package_manifest jsonb NOT NULL,
    package_sha256 text NOT NULL,
    review_attestation_sha256 text,
    release_attestation_sha256 text,
    external_reference text,
    receipt_evidence_id uuid,
    verified_evidence_id uuid,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_assisted_task_assignment CHECK (((status = 'prepared'::text) OR ((assignee_operator_id IS NOT NULL) AND (assigned_by_operator_id IS NOT NULL) AND (assigned_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_attestations CHECK ((((review_attestation_sha256 IS NULL) OR (review_attestation_sha256 ~ '^[0-9a-f]{64}$'::text)) AND ((release_attestation_sha256 IS NULL) OR (release_attestation_sha256 ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT ck_rtm_connect_assisted_task_code CHECK ((task_code ~ '^rtm-assisted-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_assisted_task_completed CHECK (((status <> 'completed'::text) OR (completed_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_assisted_task_due CHECK ((due_at > created_at)),
    CONSTRAINT ck_rtm_connect_assisted_task_metadata CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_assisted_task_package CHECK ((jsonb_typeof(package_manifest) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_assisted_task_package_sha256 CHECK ((package_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_assisted_task_receipt CHECK (((status <> ALL (ARRAY['receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR ((receipt_submitted_at IS NOT NULL) AND (external_reference IS NOT NULL) AND (receipt_evidence_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_release CHECK (((status <> ALL (ARRAY['released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text])) OR ((release_operator_id IS NOT NULL) AND (released_at IS NOT NULL) AND (release_attestation_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_review CHECK (((status <> ALL (ARRAY['ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text])) OR ((reviewed_at IS NOT NULL) AND (ready_at IS NOT NULL) AND (review_attestation_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_separation CHECK ((((release_operator_id IS NULL) OR (assignee_operator_id IS NULL) OR (release_operator_id <> assignee_operator_id)) AND ((verified_by_operator_id IS NULL) OR (assignee_operator_id IS NULL) OR (verified_by_operator_id <> assignee_operator_id)) AND ((verified_by_operator_id IS NULL) OR (release_operator_id IS NULL) OR (verified_by_operator_id <> release_operator_id)))),
    CONSTRAINT ck_rtm_connect_assisted_task_started CHECK (((status <> ALL (ARRAY['in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text])) OR (started_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_assisted_task_status CHECK ((status = ANY (ARRAY['prepared'::text, 'assigned'::text, 'reviewing'::text, 'ready_for_release'::text, 'released'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'outcome_unknown'::text, 'reconciling'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text, 'manual_review'::text, 'permanent_failed'::text]))),
    CONSTRAINT ck_rtm_connect_assisted_task_unknown CHECK (((status <> ALL (ARRAY['outcome_unknown'::text, 'reconciling'::text, 'manual_review'::text, 'permanent_failed'::text])) OR ((unknown_at IS NOT NULL) AND (external_reference IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_verified CHECK (((status <> ALL (ARRAY['verified'::text, 'completed'::text])) OR ((verified_at IS NOT NULL) AND (verified_by_operator_id IS NOT NULL) AND (verified_evidence_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_assisted_task_version CHECK (((version > 0) AND (authorization_version > 0)))
);


--
-- Name: rtm_connect_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_attempts (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    connector_id uuid,
    attempt_number integer NOT NULL,
    status text DEFAULT 'started'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    request_sha256 text NOT NULL,
    external_reference text,
    failure_class text,
    error_code text,
    retryable boolean DEFAULT false NOT NULL,
    reconciliation_required boolean DEFAULT false NOT NULL,
    request_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    result_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_attempt_finished CHECK (((status = 'started'::text) OR (finished_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_attempt_number CHECK ((attempt_number > 0)),
    CONSTRAINT ck_rtm_connect_attempt_status CHECK ((status = ANY (ARRAY['started'::text, 'external_accepted'::text, 'succeeded'::text, 'failed'::text, 'unknown'::text, 'cancelled'::text]))),
    CONSTRAINT rtm_connect_attempts_request_metadata_check CHECK ((jsonb_typeof(request_metadata) = 'object'::text)),
    CONSTRAINT rtm_connect_attempts_request_sha256_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT rtm_connect_attempts_result_metadata_check CHECK ((jsonb_typeof(result_metadata) = 'object'::text))
);


--
-- Name: rtm_connect_authorizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_authorizations (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    authorization_version integer NOT NULL,
    supersedes_id uuid,
    authority_code text NOT NULL,
    authority_version text NOT NULL,
    decision text NOT NULL,
    payload_sha256 text NOT NULL,
    idempotency_key text NOT NULL,
    required_evidence_level text NOT NULL,
    authorized_connector_modes jsonb NOT NULL,
    approved_by_operator_ids jsonb NOT NULL,
    authorized_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    legal_effect_authorized boolean DEFAULT false NOT NULL,
    frozen boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_authorization_approvers CHECK (((jsonb_typeof(approved_by_operator_ids) = 'array'::text) AND (jsonb_array_length(approved_by_operator_ids) > 0))),
    CONSTRAINT ck_rtm_connect_authorization_authority CHECK ((authority_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_authorization_decision CHECK ((decision = 'approved_frozen'::text)),
    CONSTRAINT ck_rtm_connect_authorization_evidence CHECK ((required_evidence_level = ANY (ARRAY['E0_none'::text, 'E1_request_recorded'::text, 'E2_external_reference'::text, 'E3_receipt_captured'::text, 'E4_receipt_verified'::text]))),
    CONSTRAINT ck_rtm_connect_authorization_expiry CHECK (((expires_at IS NULL) OR (expires_at > authorized_at))),
    CONSTRAINT ck_rtm_connect_authorization_frozen CHECK ((frozen = true)),
    CONSTRAINT ck_rtm_connect_authorization_modes CHECK (((jsonb_typeof(authorized_connector_modes) = 'array'::text) AND (jsonb_array_length(authorized_connector_modes) > 0))),
    CONSTRAINT ck_rtm_connect_authorization_version CHECK ((authorization_version > 0)),
    CONSTRAINT rtm_connect_authorizations_idempotency_key_check CHECK ((idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'::text)),
    CONSTRAINT rtm_connect_authorizations_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_connect_authorizations_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: rtm_connect_connectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_connectors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    version text NOT NULL,
    mode text NOT NULL,
    status text DEFAULT 'inactive'::text NOT NULL,
    environment text DEFAULT 'staging'::text NOT NULL,
    synthetic_only boolean DEFAULT true NOT NULL,
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    risk_ceiling text DEFAULT 'R0_observation'::text NOT NULL,
    supports_idempotency boolean DEFAULT true NOT NULL,
    supports_reconciliation boolean DEFAULT false NOT NULL,
    credential_ref text,
    configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_connector_code CHECK ((code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_connector_mode CHECK ((mode = ANY (ARRAY['api'::text, 'webhook'::text, 'polling'::text, 'batch'::text, 'assisted'::text, 'manual'::text]))),
    CONSTRAINT ck_rtm_connect_connector_risk CHECK ((risk_ceiling = ANY (ARRAY['R0_observation'::text, 'R1_low_reversible'::text, 'R2_business_effect'::text, 'R3_legal_or_financial'::text, 'R4_critical_regulated'::text]))),
    CONSTRAINT ck_rtm_connect_connector_status CHECK ((status = ANY (ARRAY['inactive'::text, 'active'::text, 'paused'::text, 'disabled'::text]))),
    CONSTRAINT ck_rtm_connect_connector_version CHECK ((version ~ '^[a-z0-9][a-z0-9_.-]{1,63}$'::text)),
    CONSTRAINT rtm_connect_connectors_capabilities_check CHECK ((jsonb_typeof(capabilities) = 'array'::text)),
    CONSTRAINT rtm_connect_connectors_configuration_check CHECK ((jsonb_typeof(configuration) = 'object'::text)),
    CONSTRAINT rtm_connect_connectors_environment_check CHECK ((environment = ANY (ARRAY['staging'::text, 'production'::text])))
);


--
-- Name: rtm_connect_dispatch_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_dispatch_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    outbox_id uuid NOT NULL,
    action_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    release_id uuid NOT NULL,
    release_binding_sha256 text NOT NULL,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text NOT NULL,
    reason_code text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_dispatch_event_actor CHECK (((actor_type = ANY (ARRAY['connect'::text, 'operator'::text, 'system'::text])) AND ((actor_type <> 'operator'::text) OR (operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_dispatch_event_payload CHECK (((release_binding_sha256 ~ '^[0-9a-f]{64}$'::text) AND (jsonb_typeof(payload) = 'object'::text))),
    CONSTRAINT ck_rtm_connect_dispatch_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_dispatch_event_statuses CHECK ((((from_status IS NULL) OR (from_status = ANY (ARRAY['prepared'::text, 'claimed'::text, 'dry_run_confirmed'::text, 'unknown'::text, 'manual_review'::text, 'cancelled'::text]))) AND (to_status = ANY (ARRAY['prepared'::text, 'claimed'::text, 'dry_run_confirmed'::text, 'unknown'::text, 'manual_review'::text, 'cancelled'::text])) AND (((sequence_number = 1) AND (from_status IS NULL)) OR ((sequence_number > 1) AND (from_status IS NOT NULL))))),
    CONSTRAINT ck_rtm_connect_dispatch_event_type CHECK (((event_type = ANY (ARRAY['dispatch_dry_run_prepared'::text, 'dispatch_dry_run_claimed'::text, 'dispatch_dry_run_confirmed'::text, 'dispatch_simulation_unknown'::text, 'dispatch_manual_review_recorded'::text])) AND (reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)))
);


--
-- Name: rtm_connect_dispatch_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_dispatch_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    authorization_version integer NOT NULL,
    release_id uuid NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    business_command_id text NOT NULL,
    production_effect_key text NOT NULL,
    payload_sha256 text NOT NULL,
    request_sha256 text NOT NULL,
    release_manifest_sha256 text NOT NULL,
    release_binding_sha256 text NOT NULL,
    dry_run_only boolean DEFAULT true NOT NULL,
    network_allowed boolean DEFAULT false NOT NULL,
    provider_contacted boolean DEFAULT false NOT NULL,
    external_effects_allowed boolean DEFAULT false NOT NULL,
    claim_owner text,
    claim_token uuid,
    claim_fence bigint DEFAULT 0 NOT NULL,
    claimed_at timestamp with time zone,
    claim_expires_at timestamp with time zone,
    dry_run_confirmed_at timestamp with time zone,
    unknown_at timestamp with time zone,
    manual_review_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_dispatch_outbox_business_command CHECK ((business_command_id ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{7,255}$'::text)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_claim CHECK ((((status = ANY (ARRAY['prepared'::text, 'cancelled'::text])) AND (claim_owner IS NULL) AND (claim_token IS NULL) AND (claim_fence = 0) AND (claimed_at IS NULL) AND (claim_expires_at IS NULL)) OR ((status = ANY (ARRAY['claimed'::text, 'dry_run_confirmed'::text, 'unknown'::text, 'manual_review'::text, 'cancelled'::text])) AND (claim_owner IS NOT NULL) AND (claim_token IS NOT NULL) AND (claim_fence > 0) AND (claimed_at IS NOT NULL) AND (claim_expires_at > claimed_at)))),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_claim_fence CHECK ((claim_fence >= 0)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_dry_run CHECK ((dry_run_only = true)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_effect_key CHECK ((production_effect_key ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{7,255}$'::text)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_external_effects CHECK ((external_effects_allowed = false)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_hashes CHECK (((payload_sha256 ~ '^[0-9a-f]{64}$'::text) AND (request_sha256 ~ '^[0-9a-f]{64}$'::text) AND (release_manifest_sha256 ~ '^[0-9a-f]{64}$'::text) AND (release_binding_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_metadata CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_network CHECK ((network_allowed = false)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_outcomes CHECK ((((status <> 'dry_run_confirmed'::text) OR (dry_run_confirmed_at IS NOT NULL)) AND ((status <> 'unknown'::text) OR (unknown_at IS NOT NULL)) AND ((status <> 'manual_review'::text) OR (manual_review_at IS NOT NULL)) AND ((status <> 'cancelled'::text) OR (cancelled_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_provider CHECK ((provider_contacted = false)),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_status CHECK ((status = ANY (ARRAY['prepared'::text, 'claimed'::text, 'dry_run_confirmed'::text, 'unknown'::text, 'manual_review'::text, 'cancelled'::text]))),
    CONSTRAINT ck_rtm_connect_dispatch_outbox_version CHECK (((version > 0) AND (authorization_version > 0)))
);


--
-- Name: rtm_connect_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_evidence (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid,
    sequence_number integer NOT NULL,
    evidence_level text NOT NULL,
    request_sha256 text,
    external_reference text,
    receipt_sha256 text,
    receipt_storage_ref text,
    verified_at timestamp with time zone,
    verification_method text,
    verified_by_operator_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_evidence_level CHECK ((evidence_level = ANY (ARRAY['E0_none'::text, 'E1_request_recorded'::text, 'E2_external_reference'::text, 'E3_receipt_captured'::text, 'E4_receipt_verified'::text]))),
    CONSTRAINT ck_rtm_connect_evidence_receipt_hash CHECK (((receipt_sha256 IS NULL) OR (receipt_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_evidence_request_hash CHECK (((request_sha256 IS NULL) OR (request_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_evidence_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT rtm_connect_evidence_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text))
);


--
-- Name: rtm_connect_idempotency_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_idempotency_claims (
    idempotency_key text NOT NULL,
    action_id uuid NOT NULL,
    payload_sha256 text NOT NULL,
    authority_scope text NOT NULL,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    replay_count integer DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_connect_idempotency_key CHECK ((idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_idempotency_replay_count CHECK ((replay_count >= 0)),
    CONSTRAINT rtm_connect_idempotency_claims_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_connect_idempotency_claims_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: rtm_connect_manual_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_manual_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text,
    reason_code text NOT NULL,
    reason_detail text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_manual_event_actor CHECK ((actor_type = ANY (ARRAY['operator'::text, 'connect'::text, 'core'::text, 'system'::text]))),
    CONSTRAINT ck_rtm_connect_manual_event_payload CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_manual_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_manual_event_type CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))
);


--
-- Name: rtm_connect_manual_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_manual_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    connector_id uuid NOT NULL,
    task_code text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    assignee_operator_id uuid,
    assigned_by_operator_id uuid,
    assigned_at timestamp with time zone,
    due_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    receipt_submitted_at timestamp with time zone,
    verified_at timestamp with time zone,
    verified_by_operator_id uuid,
    completed_at timestamp with time zone,
    package_manifest jsonb NOT NULL,
    package_sha256 text NOT NULL,
    instructions text NOT NULL,
    external_reference text,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_manual_task_assignment CHECK (((status = 'prepared'::text) OR ((assignee_operator_id IS NOT NULL) AND (assigned_by_operator_id IS NOT NULL) AND (assigned_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_manual_task_code CHECK ((task_code ~ '^rtm-manual-[0-9a-f]{24}$'::text)),
    CONSTRAINT ck_rtm_connect_manual_task_completed CHECK (((status <> 'completed'::text) OR (completed_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_manual_task_due CHECK ((due_at > created_at)),
    CONSTRAINT ck_rtm_connect_manual_task_metadata CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_manual_task_package CHECK ((jsonb_typeof(package_manifest) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_manual_task_package_sha256 CHECK ((package_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_manual_task_receipt CHECK (((status <> ALL (ARRAY['receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR ((receipt_submitted_at IS NOT NULL) AND (external_reference IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_manual_task_started CHECK (((status <> ALL (ARRAY['in_progress'::text, 'awaiting_receipt'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text])) OR (started_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_manual_task_status CHECK ((status = ANY (ARRAY['prepared'::text, 'assigned'::text, 'in_progress'::text, 'awaiting_receipt'::text, 'receipt_submitted'::text, 'verified'::text, 'completed'::text]))),
    CONSTRAINT ck_rtm_connect_manual_task_verified CHECK (((status <> ALL (ARRAY['verified'::text, 'completed'::text])) OR ((verified_at IS NOT NULL) AND (verified_by_operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_manual_task_version CHECK ((version > 0))
);


--
-- Name: rtm_connect_production_release_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_production_release_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    release_id uuid NOT NULL,
    release_binding_sha256 text NOT NULL,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text NOT NULL,
    reason_code text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_production_release_event_actor CHECK (((actor_type = ANY (ARRAY['requester'::text, 'security'::text, 'operations'::text, 'system'::text])) AND ((actor_type = 'system'::text) OR (operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_production_release_event_payload CHECK (((release_binding_sha256 ~ '^[0-9a-f]{64}$'::text) AND (jsonb_typeof(payload) = 'object'::text))),
    CONSTRAINT ck_rtm_connect_production_release_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_production_release_event_statuses CHECK ((((from_status IS NULL) OR (from_status = ANY (ARRAY['proposed'::text, 'security_approved'::text, 'operations_approved'::text, 'ready'::text, 'simulated_active'::text, 'halted'::text, 'rejected'::text, 'expired'::text]))) AND (to_status = ANY (ARRAY['proposed'::text, 'security_approved'::text, 'operations_approved'::text, 'ready'::text, 'simulated_active'::text, 'halted'::text, 'rejected'::text, 'expired'::text])) AND (((sequence_number = 1) AND (from_status IS NULL)) OR ((sequence_number > 1) AND (from_status IS NOT NULL))))),
    CONSTRAINT ck_rtm_connect_production_release_event_type CHECK (((event_type = ANY (ARRAY['release_proposed'::text, 'security_approval_recorded'::text, 'operations_approval_recorded'::text, 'simulation_release_ready'::text, 'simulation_activation_recorded'::text, 'emergency_halt_recorded'::text])) AND (reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)))
);


--
-- Name: rtm_connect_production_releases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_production_releases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    release_code text NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    connector_code text NOT NULL,
    connector_version text NOT NULL,
    source_commit_sha text NOT NULL,
    manifest_sha256 text NOT NULL,
    policy_sha256 text NOT NULL,
    schema_sha256 text NOT NULL,
    build_artifact_sha256 text NOT NULL,
    release_binding_sha256 text NOT NULL,
    requested_by_operator_id uuid NOT NULL,
    security_approved_by_operator_id uuid,
    security_approval_sha256 text,
    operations_approved_by_operator_id uuid,
    operations_approval_sha256 text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    security_approved_at timestamp with time zone,
    operations_approved_at timestamp with time zone,
    ready_at timestamp with time zone,
    simulated_active_at timestamp with time zone,
    emergency_halt boolean DEFAULT false NOT NULL,
    halted_at timestamp with time zone,
    halted_by_operator_id uuid,
    halt_reason_code text,
    rejected_at timestamp with time zone,
    rejected_by_operator_id uuid,
    rejection_reason_code text,
    valid_until timestamp with time zone NOT NULL,
    expired_at timestamp with time zone,
    simulation_only boolean DEFAULT true NOT NULL,
    external_effects_allowed boolean DEFAULT false NOT NULL,
    live_activation_allowed boolean DEFAULT false NOT NULL,
    human_activation_required boolean DEFAULT true NOT NULL,
    provider_pack_present boolean DEFAULT false NOT NULL,
    canary_percent numeric(5,2) DEFAULT 1.00 NOT NULL,
    max_concurrency integer DEFAULT 1 NOT NULL,
    daily_action_limit integer DEFAULT 1 NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_production_release_approval_hashes CHECK ((((((security_approved_by_operator_id IS NULL) AND (security_approved_at IS NULL) AND (security_approval_sha256 IS NULL)) OR ((security_approved_by_operator_id IS NOT NULL) AND (security_approved_at IS NOT NULL) AND (security_approved_at >= requested_at) AND (security_approval_sha256 IS NOT NULL) AND (security_approval_sha256 ~ '^[0-9a-f]{64}$'::text))) AND (((operations_approved_by_operator_id IS NULL) AND (operations_approved_at IS NULL) AND (operations_approval_sha256 IS NULL)) OR ((operations_approved_by_operator_id IS NOT NULL) AND (operations_approved_at IS NOT NULL) AND (security_approved_at IS NOT NULL) AND (operations_approved_at >= security_approved_at) AND (operations_approval_sha256 IS NOT NULL) AND (operations_approval_sha256 ~ '^[0-9a-f]{64}$'::text)))) IS TRUE)),
    CONSTRAINT ck_rtm_connect_production_release_approvals CHECK ((((security_approved_by_operator_id IS NULL) OR (security_approved_by_operator_id <> requested_by_operator_id)) AND ((operations_approved_by_operator_id IS NULL) OR (operations_approved_by_operator_id <> requested_by_operator_id)) AND ((security_approved_by_operator_id IS NULL) OR (operations_approved_by_operator_id IS NULL) OR (security_approved_by_operator_id <> operations_approved_by_operator_id)) AND ((status <> ALL (ARRAY['security_approved'::text, 'operations_approved'::text, 'ready'::text, 'simulated_active'::text])) OR (security_approved_by_operator_id IS NOT NULL)) AND ((status <> ALL (ARRAY['operations_approved'::text, 'ready'::text, 'simulated_active'::text])) OR (operations_approved_by_operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_production_release_canary CHECK (((canary_percent > (0)::numeric) AND (canary_percent <= (5)::numeric))),
    CONSTRAINT ck_rtm_connect_production_release_code CHECK (((release_code ~ '^rtmc8-release-[0-9a-f]{24}$'::text) AND (release_code = ('rtmc8-release-'::text || SUBSTRING(release_binding_sha256 FROM 1 FOR 24))))),
    CONSTRAINT ck_rtm_connect_production_release_connector CHECK (((connector_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text) AND (connector_version ~ '^v[0-9]+\.[0-9]+$'::text))),
    CONSTRAINT ck_rtm_connect_production_release_daily_limit CHECK ((daily_action_limit = 1)),
    CONSTRAINT ck_rtm_connect_production_release_expiry CHECK (((status <> 'expired'::text) OR ((expired_at IS NOT NULL) AND (expired_at >= requested_at)))),
    CONSTRAINT ck_rtm_connect_production_release_external_effects CHECK ((external_effects_allowed = false)),
    CONSTRAINT ck_rtm_connect_production_release_halt CHECK ((((status = 'halted'::text) = emergency_halt) AND (((status <> 'halted'::text) AND (halted_at IS NULL) AND (halted_by_operator_id IS NULL) AND (halt_reason_code IS NULL)) OR ((status = 'halted'::text) AND (halted_at IS NOT NULL) AND (halted_at >= requested_at) AND (halted_by_operator_id IS NOT NULL) AND (halt_reason_code IS NOT NULL))))),
    CONSTRAINT ck_rtm_connect_production_release_hashes CHECK (((manifest_sha256 ~ '^[0-9a-f]{64}$'::text) AND (policy_sha256 ~ '^[0-9a-f]{64}$'::text) AND (schema_sha256 ~ '^[0-9a-f]{64}$'::text) AND (build_artifact_sha256 ~ '^[0-9a-f]{64}$'::text) AND (release_binding_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_production_release_human_activation CHECK ((human_activation_required = true)),
    CONSTRAINT ck_rtm_connect_production_release_live_activation CHECK ((live_activation_allowed = false)),
    CONSTRAINT ck_rtm_connect_production_release_max_concurrency CHECK ((max_concurrency = 1)),
    CONSTRAINT ck_rtm_connect_production_release_metadata CHECK ((((jsonb_typeof(metadata) = 'object'::text) AND (metadata ?& ARRAY['candidate'::text, 'assessment'::text, 'expected_admission_payload'::text, 'control_version'::text]) AND ((metadata - ARRAY['candidate'::text, 'assessment'::text, 'expected_admission_payload'::text, 'control_version'::text]) = '{}'::jsonb) AND ((metadata ->> 'control_version'::text) = 'rtm_connect_c8_production_control_v1_0'::text) AND (jsonb_typeof((metadata -> 'candidate'::text)) = 'object'::text) AND ((metadata -> 'candidate'::text) ?& ARRAY['candidate_id'::text, 'requested_by_operator_id'::text, 'source_commit_sha40'::text, 'build_artifact_sha256'::text, 'connector_manifest_sha256'::text, 'provider_contract_sha256'::text, 'egress_policy_sha256'::text, 'credential_reference_sha256'::text, 'schema_snapshot_sha256'::text, 'test_report_sha256'::text, 'created_at'::text, 'expires_at'::text, 'canary_percent'::text, 'concurrency'::text, 'max_simulated_actions_total'::text, 'max_simulated_actions_per_day'::text, 'max_payload_bytes'::text, 'admission_ttl_seconds'::text, 'simulation_only'::text, 'external_effects_allowed'::text, 'live_activation_allowed'::text, 'human_activation_required'::text, 'contract_version'::text]) AND (((metadata -> 'candidate'::text) - ARRAY['candidate_id'::text, 'requested_by_operator_id'::text, 'source_commit_sha40'::text, 'build_artifact_sha256'::text, 'connector_manifest_sha256'::text, 'provider_contract_sha256'::text, 'egress_policy_sha256'::text, 'credential_reference_sha256'::text, 'schema_snapshot_sha256'::text, 'test_report_sha256'::text, 'created_at'::text, 'expires_at'::text, 'canary_percent'::text, 'concurrency'::text, 'max_simulated_actions_total'::text, 'max_simulated_actions_per_day'::text, 'max_payload_bytes'::text, 'admission_ttl_seconds'::text, 'simulation_only'::text, 'external_effects_allowed'::text, 'live_activation_allowed'::text, 'human_activation_required'::text, 'contract_version'::text]) = '{}'::jsonb) AND (((metadata -> 'candidate'::text) ->> 'candidate_id'::text) = (id)::text) AND (((metadata -> 'candidate'::text) ->> 'requested_by_operator_id'::text) = (requested_by_operator_id)::text) AND (((metadata -> 'candidate'::text) ->> 'source_commit_sha40'::text) = source_commit_sha) AND (((metadata -> 'candidate'::text) ->> 'build_artifact_sha256'::text) = build_artifact_sha256) AND (((metadata -> 'candidate'::text) ->> 'connector_manifest_sha256'::text) = manifest_sha256) AND (((metadata -> 'candidate'::text) ->> 'egress_policy_sha256'::text) = policy_sha256) AND (((metadata -> 'candidate'::text) ->> 'schema_snapshot_sha256'::text) = schema_sha256) AND (((metadata -> 'candidate'::text) ->> 'provider_contract_sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND (((metadata -> 'candidate'::text) ->> 'credential_reference_sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND (((metadata -> 'candidate'::text) ->> 'test_report_sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND ((((metadata -> 'candidate'::text) ->> 'created_at'::text))::timestamp with time zone = requested_at) AND ((((metadata -> 'candidate'::text) ->> 'expires_at'::text))::timestamp with time zone = valid_until) AND ((((metadata -> 'candidate'::text) ->> 'canary_percent'::text))::numeric = canary_percent) AND ((((metadata -> 'candidate'::text) ->> 'concurrency'::text))::integer = max_concurrency) AND ((((metadata -> 'candidate'::text) ->> 'max_simulated_actions_total'::text))::integer = 1) AND ((((metadata -> 'candidate'::text) ->> 'max_simulated_actions_per_day'::text))::integer = daily_action_limit) AND (((((metadata -> 'candidate'::text) ->> 'max_payload_bytes'::text))::integer >= 1) AND ((((metadata -> 'candidate'::text) ->> 'max_payload_bytes'::text))::integer <= 1048576)) AND (((((metadata -> 'candidate'::text) ->> 'admission_ttl_seconds'::text))::integer >= 1) AND ((((metadata -> 'candidate'::text) ->> 'admission_ttl_seconds'::text))::integer <= 86400)) AND ((valid_until - requested_at) <= (((((metadata -> 'candidate'::text) ->> 'admission_ttl_seconds'::text))::integer)::double precision * '00:00:01'::interval)) AND (((metadata -> 'candidate'::text) -> 'simulation_only'::text) = 'true'::jsonb) AND (((metadata -> 'candidate'::text) -> 'external_effects_allowed'::text) = 'false'::jsonb) AND (((metadata -> 'candidate'::text) -> 'live_activation_allowed'::text) = 'false'::jsonb) AND (((metadata -> 'candidate'::text) -> 'human_activation_required'::text) = 'true'::jsonb) AND (((metadata -> 'candidate'::text) ->> 'contract_version'::text) = 'rtm.connect.c8.admission.v1'::text) AND (jsonb_typeof((metadata -> 'assessment'::text)) = 'object'::text) AND ((metadata -> 'assessment'::text) ?& ARRAY['candidate_sha256'::text, 'evaluated_at'::text, 'blocker_codes'::text, 'verdict'::text, 'simulation_admitted'::text, 'live_production_admitted'::text, 'production_effects_available'::text]) AND (((metadata -> 'assessment'::text) - ARRAY['candidate_sha256'::text, 'evaluated_at'::text, 'blocker_codes'::text, 'verdict'::text, 'simulation_admitted'::text, 'live_production_admitted'::text, 'production_effects_available'::text]) = '{}'::jsonb) AND (((metadata -> 'assessment'::text) ->> 'candidate_sha256'::text) = release_binding_sha256) AND ((((metadata -> 'assessment'::text) ->> 'evaluated_at'::text))::timestamp with time zone >= requested_at) AND ((((metadata -> 'assessment'::text) ->> 'evaluated_at'::text))::timestamp with time zone < valid_until) AND (((metadata -> 'assessment'::text) ->> 'verdict'::text) = 'no_go'::text) AND (((metadata -> 'assessment'::text) -> 'blocker_codes'::text) = jsonb_build_array('provider_specific_pack_missing', 'production_transport_absent', 'live_activation_unavailable', 'external_effects_forbidden')) AND (((metadata -> 'assessment'::text) -> 'simulation_admitted'::text) = 'true'::jsonb) AND (((metadata -> 'assessment'::text) -> 'live_production_admitted'::text) = 'false'::jsonb) AND (((metadata -> 'assessment'::text) -> 'production_effects_available'::text) = 'false'::jsonb) AND ((metadata -> 'expected_admission_payload'::text) = jsonb_build_object('contract_version', 'rtm.connect.c8.admission.v1', 'candidate_sha256', release_binding_sha256, 'synthetic_marker', 'RTM_C8_SYNTHETIC_ONLY', 'simulation_only', true, 'external_effects_allowed', false, 'live_activation_allowed', false, 'human_activation_required', true))) IS TRUE)),
    CONSTRAINT ck_rtm_connect_production_release_provider_pack CHECK ((provider_pack_present = false)),
    CONSTRAINT ck_rtm_connect_production_release_ready CHECK (((status <> ALL (ARRAY['ready'::text, 'simulated_active'::text])) OR ((security_approved_by_operator_id IS NOT NULL) AND (operations_approved_by_operator_id IS NOT NULL) AND (ready_at IS NOT NULL) AND (ready_at >= operations_approved_at)))),
    CONSTRAINT ck_rtm_connect_production_release_rejection CHECK (((status <> 'rejected'::text) OR ((rejected_at IS NOT NULL) AND (rejected_at >= requested_at) AND (rejected_by_operator_id IS NOT NULL) AND (rejection_reason_code IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_production_release_simulated_active CHECK (((status <> 'simulated_active'::text) OR ((simulated_active_at IS NOT NULL) AND (ready_at IS NOT NULL) AND (simulated_active_at >= ready_at)))),
    CONSTRAINT ck_rtm_connect_production_release_simulation_only CHECK ((simulation_only = true)),
    CONSTRAINT ck_rtm_connect_production_release_source_commit CHECK ((source_commit_sha ~ '^[0-9a-f]{40}$'::text)),
    CONSTRAINT ck_rtm_connect_production_release_status CHECK ((status = ANY (ARRAY['proposed'::text, 'security_approved'::text, 'operations_approved'::text, 'ready'::text, 'simulated_active'::text, 'halted'::text, 'rejected'::text, 'expired'::text]))),
    CONSTRAINT ck_rtm_connect_production_release_validity CHECK ((valid_until > requested_at)),
    CONSTRAINT ck_rtm_connect_production_release_version CHECK ((version > 0))
);


--
-- Name: rtm_connect_reconciliation_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_reconciliation_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reconciliation_id uuid NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    webhook_inbox_id uuid NOT NULL,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text,
    resolution text,
    reason_code text NOT NULL,
    reason_detail text,
    evidence_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_reconciliation_event_actor CHECK ((actor_type = ANY (ARRAY['webhook'::text, 'connect'::text, 'reconciliation'::text, 'operator'::text, 'system'::text]))),
    CONSTRAINT ck_rtm_connect_reconciliation_event_payload CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_reconciliation_event_resolution CHECK (((resolution IS NULL) OR (resolution = ANY (ARRAY['confirmed'::text, 'retryable_failed'::text, 'unknown'::text, 'manual_review'::text, 'permanent_failed'::text])))),
    CONSTRAINT ck_rtm_connect_reconciliation_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_reconciliation_event_status CHECK ((((from_status IS NULL) OR (from_status = ANY (ARRAY['started'::text, 'resolved'::text]))) AND ((to_status IS NULL) OR (to_status = ANY (ARRAY['started'::text, 'resolved'::text]))))),
    CONSTRAINT ck_rtm_connect_reconciliation_event_type CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))
);


--
-- Name: rtm_connect_reconciliations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_reconciliations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    webhook_inbox_id uuid NOT NULL,
    reconciliation_number integer NOT NULL,
    status text DEFAULT 'started'::text NOT NULL,
    resolution text,
    request_sha256 text NOT NULL,
    external_reference text NOT NULL,
    evidence_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_operator_id uuid,
    resolution_code text,
    resolution_detail text,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_reconciliation_confirmed_evidence CHECK (((resolution <> 'confirmed'::text) OR (evidence_id IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_reconciliation_external_reference CHECK (((length(btrim(external_reference)) >= 1) AND (length(btrim(external_reference)) <= 512))),
    CONSTRAINT ck_rtm_connect_reconciliation_metadata CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_reconciliation_number CHECK ((reconciliation_number > 0)),
    CONSTRAINT ck_rtm_connect_reconciliation_request_sha256 CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_reconciliation_resolution CHECK (((resolution IS NULL) OR (resolution = ANY (ARRAY['confirmed'::text, 'retryable_failed'::text, 'unknown'::text, 'manual_review'::text, 'permanent_failed'::text])))),
    CONSTRAINT ck_rtm_connect_reconciliation_resolved CHECK ((((status = 'started'::text) AND (resolution IS NULL) AND (resolved_at IS NULL)) OR ((status = 'resolved'::text) AND (resolution IS NOT NULL) AND (resolved_at IS NOT NULL) AND (resolution_code IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_reconciliation_started CHECK (((resolved_at IS NULL) OR (started_at <= resolved_at))),
    CONSTRAINT ck_rtm_connect_reconciliation_status CHECK ((status = ANY (ARRAY['started'::text, 'resolved'::text]))),
    CONSTRAINT ck_rtm_connect_reconciliation_version CHECK ((version > 0))
);


--
-- Name: rtm_connect_transitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_transitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_id uuid NOT NULL,
    attempt_id uuid,
    sequence_number integer NOT NULL,
    from_status text,
    to_status text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    reason_code text NOT NULL,
    reason_detail text,
    request_id text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_transition_actor CHECK ((actor_type = ANY (ARRAY['core'::text, 'connect'::text, 'operator'::text, 'system'::text, 'reconciliation'::text, 'migration'::text]))),
    CONSTRAINT ck_rtm_connect_transition_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_transition_to_status CHECK ((to_status = ANY (ARRAY['draft'::text, 'authorized'::text, 'queued'::text, 'executing'::text, 'external_accepted'::text, 'evidence_pending'::text, 'confirmed'::text, 'retryable_failed'::text, 'unknown'::text, 'reconciling'::text, 'manual_review'::text, 'permanent_failed'::text, 'cancelled'::text]))),
    CONSTRAINT rtm_connect_transitions_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text))
);


--
-- Name: rtm_connect_webhook_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_webhook_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    webhook_inbox_id uuid NOT NULL,
    action_id uuid,
    attempt_id uuid,
    sequence_number integer NOT NULL,
    event_type text NOT NULL,
    actor_type text NOT NULL,
    operator_id uuid,
    from_status text,
    to_status text,
    reason_code text NOT NULL,
    reason_detail text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_webhook_event_actor CHECK ((actor_type = ANY (ARRAY['webhook'::text, 'connect'::text, 'reconciliation'::text, 'operator'::text, 'system'::text]))),
    CONSTRAINT ck_rtm_connect_webhook_event_payload CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_webhook_event_sequence CHECK ((sequence_number > 0)),
    CONSTRAINT ck_rtm_connect_webhook_event_status CHECK ((((from_status IS NULL) OR (from_status = ANY (ARRAY['received'::text, 'verified'::text, 'matched'::text, 'processed'::text, 'dead_lettered'::text]))) AND ((to_status IS NULL) OR (to_status = ANY (ARRAY['received'::text, 'verified'::text, 'matched'::text, 'processed'::text, 'dead_lettered'::text]))))),
    CONSTRAINT ck_rtm_connect_webhook_event_type CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))
);


--
-- Name: rtm_connect_webhook_inbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_connect_webhook_inbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ingress_connector_id uuid NOT NULL,
    source_event_id text NOT NULL,
    event_type text NOT NULL,
    deduplication_key text NOT NULL,
    origin_connector_code text NOT NULL,
    origin_connector_version text NOT NULL,
    reported_outcome text NOT NULL,
    claimed_action_id uuid NOT NULL,
    claimed_attempt_id uuid NOT NULL,
    matched_action_id uuid,
    matched_attempt_id uuid,
    external_reference text NOT NULL,
    request_sha256 text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    verification_method text,
    verification_sha256 text,
    receipt_sha256 text,
    receipt_storage_ref text,
    status text DEFAULT 'received'::text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    matched_at timestamp with time zone,
    processed_at timestamp with time zone,
    dead_letter_reason_code text,
    dead_letter_reason_detail text,
    replay_count integer DEFAULT 0 NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_connect_webhook_confirmed_receipt CHECK (((status <> 'processed'::text) OR (reported_outcome <> 'confirmed'::text) OR ((receipt_sha256 IS NOT NULL) AND (receipt_storage_ref IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_webhook_dead_lettered CHECK (((status <> 'dead_lettered'::text) OR ((processed_at IS NOT NULL) AND (dead_letter_reason_code IS NOT NULL) AND (dead_letter_reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)))),
    CONSTRAINT ck_rtm_connect_webhook_deduplication CHECK ((deduplication_key ~ '^rtmwh1:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_event_type CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_external_reference CHECK (((length(btrim(external_reference)) >= 1) AND (length(btrim(external_reference)) <= 512))),
    CONSTRAINT ck_rtm_connect_webhook_matched CHECK (((status <> ALL (ARRAY['matched'::text, 'processed'::text])) OR ((matched_action_id IS NOT NULL) AND (matched_attempt_id IS NOT NULL) AND (matched_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_webhook_metadata CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_webhook_occurred CHECK (((occurred_at <= received_at) AND (last_seen_at >= received_at))),
    CONSTRAINT ck_rtm_connect_webhook_origin_code CHECK ((origin_connector_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_origin_version CHECK ((origin_connector_version ~ '^[a-z0-9][a-z0-9_.-]{1,63}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_payload CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_rtm_connect_webhook_payload_sha256 CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_processed CHECK (((status <> 'processed'::text) OR (processed_at IS NOT NULL))),
    CONSTRAINT ck_rtm_connect_webhook_receipt_sha256 CHECK (((receipt_sha256 IS NULL) OR (receipt_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_webhook_receipt_storage_ref CHECK (((receipt_storage_ref IS NULL) OR ((receipt_storage_ref ~ '^synthetic://webhook/'::text) AND (length(receipt_storage_ref) <= 1024)))),
    CONSTRAINT ck_rtm_connect_webhook_replay_count CHECK ((replay_count >= 0)),
    CONSTRAINT ck_rtm_connect_webhook_reported_outcome CHECK ((reported_outcome = ANY (ARRAY['confirmed'::text, 'retryable_failed'::text, 'unknown'::text, 'manual_review'::text, 'permanent_failed'::text]))),
    CONSTRAINT ck_rtm_connect_webhook_request_sha256 CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_source_event CHECK ((source_event_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{2,191}$'::text)),
    CONSTRAINT ck_rtm_connect_webhook_status CHECK ((status = ANY (ARRAY['received'::text, 'verified'::text, 'matched'::text, 'processed'::text, 'dead_lettered'::text]))),
    CONSTRAINT ck_rtm_connect_webhook_verification_method CHECK (((verification_method IS NULL) OR (verification_method ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))),
    CONSTRAINT ck_rtm_connect_webhook_verification_sha256 CHECK (((verification_sha256 IS NULL) OR (verification_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_connect_webhook_verified CHECK (((status <> ALL (ARRAY['verified'::text, 'matched'::text, 'processed'::text])) OR ((verification_method IS NOT NULL) AND (verification_sha256 IS NOT NULL)))),
    CONSTRAINT ck_rtm_connect_webhook_version CHECK ((version > 0))
);


--
-- Name: rtm_core_schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_core_schema_migrations (
    name text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rtm_deadlines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_deadlines (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid,
    attention_item_id uuid,
    deadline_class text NOT NULL,
    code text NOT NULL,
    title text NOT NULL,
    source_event_id uuid,
    source_document_id uuid,
    origin_at timestamp with time zone,
    origin_status text DEFAULT 'unverified'::text NOT NULL,
    origin_timezone text DEFAULT 'Europe/Madrid'::text NOT NULL,
    rule_code text,
    rule_version text,
    computation_basis text DEFAULT 'none'::text NOT NULL,
    calendar_code text,
    quantity integer,
    due_at timestamp with time zone,
    confidence double precision DEFAULT 0 NOT NULL,
    validation_status text DEFAULT 'pending'::text NOT NULL,
    validated_by uuid,
    validated_at timestamp with time zone,
    validation_note text,
    supersedes_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_deadline_due_has_authority CHECK (((due_at IS NULL) OR ((origin_at IS NOT NULL) AND (rule_code IS NOT NULL) AND (computation_basis <> 'none'::text)))),
    CONSTRAINT ck_rtm_deadline_missing_origin CHECK (((origin_status <> 'missing'::text) OR ((origin_at IS NULL) AND (due_at IS NULL)))),
    CONSTRAINT ck_rtm_deadline_validated_state CHECK (((validation_status <> 'validated'::text) OR ((validated_at IS NOT NULL) AND (due_at IS NOT NULL) AND (origin_status = 'verified'::text)))),
    CONSTRAINT rtm_deadlines_computation_basis_check CHECK ((computation_basis = ANY (ARRAY['none'::text, 'natural_days'::text, 'business_days'::text, 'calendar_date'::text, 'manual'::text]))),
    CONSTRAINT rtm_deadlines_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT rtm_deadlines_deadline_class_check CHECK ((deadline_class = ANY (ARRAY['operational'::text, 'legal_candidate'::text, 'legal_validated'::text, 'system'::text]))),
    CONSTRAINT rtm_deadlines_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_deadlines_origin_status_check CHECK ((origin_status = ANY (ARRAY['missing'::text, 'unverified'::text, 'verified'::text, 'conflicted'::text]))),
    CONSTRAINT rtm_deadlines_quantity_check CHECK (((quantity IS NULL) OR (quantity >= 0))),
    CONSTRAINT rtm_deadlines_validation_status_check CHECK ((validation_status = ANY (ARRAY['pending'::text, 'validated'::text, 'rejected'::text, 'superseded'::text])))
);


--
-- Name: rtm_document_extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_document_extractions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    sequence integer NOT NULL,
    service text NOT NULL,
    status text DEFAULT 'completed'::text NOT NULL,
    extractor_version text NOT NULL,
    provider_version text NOT NULL,
    model text NOT NULL,
    packet jsonb NOT NULL,
    packet_sha256 text NOT NULL,
    diagnostics jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    invalidated_by text,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    CONSTRAINT rtm_document_extractions_sequence_check CHECK ((sequence > 0)),
    CONSTRAINT rtm_document_extractions_status_check CHECK ((status = ANY (ARRAY['completed'::text, 'invalidated'::text])))
);


--
-- Name: rtm_family_resolutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_family_resolutions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    validated_facts_id uuid NOT NULL,
    sequence integer NOT NULL,
    version text NOT NULL,
    service text NOT NULL,
    status text NOT NULL,
    family text,
    specialist text,
    confidence double precision DEFAULT 0 NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    locked boolean DEFAULT false NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_by text,
    locked_at timestamp with time zone,
    invalidated_by text,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    supersedes_id uuid,
    CONSTRAINT rtm_family_resolutions_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT rtm_family_resolutions_sequence_check CHECK ((sequence > 0)),
    CONSTRAINT rtm_family_resolutions_status_check CHECK ((status = ANY (ARRAY['unresolved'::text, 'resolved'::text, 'conflicted'::text, 'operator_review'::text])))
);


--
-- Name: rtm_generated_resources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_generated_resources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    legal_preview_id uuid NOT NULL,
    sequence integer NOT NULL,
    status text DEFAULT 'generated'::text NOT NULL,
    family text NOT NULL,
    generator_version text NOT NULL,
    preview_payload_sha256 text NOT NULL,
    content_sha256 text NOT NULL,
    docx_document_id uuid,
    pdf_document_id uuid,
    generated_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_by text,
    approved_at timestamp with time zone,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    CONSTRAINT rtm_generated_resources_sequence_check CHECK ((sequence > 0)),
    CONSTRAINT rtm_generated_resources_status_check CHECK ((status = ANY (ARRAY['generated'::text, 'final_ready'::text, 'invalidated'::text])))
);


--
-- Name: rtm_legal_previews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_legal_previews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    validated_facts_id uuid NOT NULL,
    family_resolution_id uuid NOT NULL,
    sequence integer NOT NULL,
    status text NOT NULL,
    service text NOT NULL,
    family text NOT NULL,
    specialist text NOT NULL,
    facts_version text NOT NULL,
    family_resolution_version text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_by text,
    approved_at timestamp with time zone,
    frozen_by text,
    frozen_at timestamp with time zone,
    invalidated_by text,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    supersedes_id uuid,
    state_reason text,
    CONSTRAINT rtm_legal_previews_sequence_check CHECK ((sequence > 0)),
    CONSTRAINT rtm_legal_previews_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'ops_review'::text, 'changes_required'::text, 'approved'::text, 'frozen'::text, 'invalidated'::text])))
);


--
-- Name: rtm_management_schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_management_schema_migrations (
    name text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rtm_management_schema_migrations_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text))
);


--
-- Name: rtm_operator_access_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operator_access_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operator_id uuid,
    session_id uuid,
    device_id uuid,
    event_type text NOT NULL,
    result text DEFAULT 'success'::text NOT NULL,
    auth_method text,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    login_identifier_sha256 text,
    ip_masked text,
    ip_hash_sha256 text,
    ip_family smallint,
    ip_source text DEFAULT 'unknown'::text NOT NULL,
    ip_trusted boolean DEFAULT false NOT NULL,
    device_key_sha256 text,
    device_type text DEFAULT 'unknown'::text NOT NULL,
    os_family text,
    os_version text,
    browser_family text,
    browser_version text,
    country_code text,
    region text,
    city text,
    timezone text,
    location_source text,
    request_id text,
    reason_code text,
    reason_detail text,
    risk_flags jsonb DEFAULT '[]'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rtm_operator_access_events_country_code_check CHECK (((country_code IS NULL) OR (country_code ~ '^[A-Z]{2}$'::text))),
    CONSTRAINT rtm_operator_access_events_device_key_sha256_check CHECK (((device_key_sha256 IS NULL) OR (device_key_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT rtm_operator_access_events_device_type_check CHECK ((device_type = ANY (ARRAY['desktop'::text, 'mobile'::text, 'tablet'::text, 'bot'::text, 'other'::text, 'unknown'::text]))),
    CONSTRAINT rtm_operator_access_events_event_type_check CHECK ((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text)),
    CONSTRAINT rtm_operator_access_events_ip_family_check CHECK (((ip_family IS NULL) OR (ip_family = ANY (ARRAY[4, 6])))),
    CONSTRAINT rtm_operator_access_events_ip_hash_sha256_check CHECK (((ip_hash_sha256 IS NULL) OR (ip_hash_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT rtm_operator_access_events_ip_source_check CHECK ((ip_source = ANY (ARRAY['x_vercel_forwarded_for'::text, 'x_forwarded_for'::text, 'x_real_ip'::text, 'render_proxy'::text, 'direct'::text, 'unknown'::text]))),
    CONSTRAINT rtm_operator_access_events_login_identifier_sha256_check CHECK (((login_identifier_sha256 IS NULL) OR (login_identifier_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT rtm_operator_access_events_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_operator_access_events_result_check CHECK ((result = ANY (ARRAY['success'::text, 'failure'::text, 'denied'::text, 'noop'::text]))),
    CONSTRAINT rtm_operator_access_events_risk_flags_check CHECK ((jsonb_typeof(risk_flags) = 'array'::text))
);


--
-- Name: rtm_operator_access_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operator_access_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    access_event_id uuid NOT NULL,
    ip_address inet,
    raw_user_agent text,
    trusted_headers jsonb DEFAULT '{}'::jsonb NOT NULL,
    retention_until timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_operator_access_evidence_retention CHECK ((retention_until > created_at)),
    CONSTRAINT rtm_operator_access_evidence_trusted_headers_check CHECK ((jsonb_typeof(trusted_headers) = 'object'::text))
);


--
-- Name: rtm_operator_devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operator_devices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operator_id uuid NOT NULL,
    device_key_sha256 text NOT NULL,
    status text DEFAULT 'known'::text NOT NULL,
    display_name text,
    device_type text DEFAULT 'unknown'::text NOT NULL,
    os_family text,
    os_version text,
    browser_family text,
    browser_version text,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    first_ip_hash_sha256 text,
    last_ip_hash_sha256 text,
    trusted_at timestamp with time zone,
    trusted_by uuid,
    revoked_at timestamp with time zone,
    revoked_by uuid,
    revocation_reason text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_operator_device_seen_order CHECK ((last_seen_at >= first_seen_at)),
    CONSTRAINT ck_rtm_operator_device_status CHECK ((((status = 'trusted'::text) AND (trusted_at IS NOT NULL)) OR ((status = 'revoked'::text) AND (revoked_at IS NOT NULL)) OR (status = 'known'::text))),
    CONSTRAINT rtm_operator_devices_device_key_sha256_check CHECK ((device_key_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT rtm_operator_devices_device_type_check CHECK ((device_type = ANY (ARRAY['desktop'::text, 'mobile'::text, 'tablet'::text, 'bot'::text, 'other'::text, 'unknown'::text]))),
    CONSTRAINT rtm_operator_devices_first_ip_hash_sha256_check CHECK (((first_ip_hash_sha256 IS NULL) OR (first_ip_hash_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT rtm_operator_devices_last_ip_hash_sha256_check CHECK (((last_ip_hash_sha256 IS NULL) OR (last_ip_hash_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT rtm_operator_devices_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_operator_devices_status_check CHECK ((status = ANY (ARRAY['known'::text, 'trusted'::text, 'revoked'::text])))
);


--
-- Name: rtm_operator_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operator_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    description text,
    permissions jsonb DEFAULT '[]'::jsonb NOT NULL,
    system_role boolean DEFAULT false NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_operator_role_code CHECK ((code ~ '^[a-z][a-z0-9_.-]{2,63}$'::text)),
    CONSTRAINT rtm_operator_roles_permissions_check CHECK ((jsonb_typeof(permissions) = 'array'::text))
);


--
-- Name: rtm_operator_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operator_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operator_id uuid NOT NULL,
    token_sha256 text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    login_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    logout_at timestamp with time zone,
    revoked_at timestamp with time zone,
    revoked_by uuid,
    close_reason text,
    ip_address text,
    user_agent text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    device_id uuid,
    login_access_event_id uuid,
    ip_source text,
    ip_trusted boolean DEFAULT false NOT NULL,
    country_code text,
    region text,
    city text,
    timezone text,
    risk_flags jsonb DEFAULT '[]'::jsonb NOT NULL,
    auth_epoch integer DEFAULT 1 NOT NULL,
    last_verified_at timestamp with time zone,
    absolute_expires_at timestamp with time zone,
    CONSTRAINT ck_rtm_operator_session_absolute_expiry CHECK (((absolute_expires_at IS NULL) OR (absolute_expires_at >= expires_at))),
    CONSTRAINT ck_rtm_operator_session_auth_epoch CHECK ((auth_epoch > 0)),
    CONSTRAINT ck_rtm_operator_session_closed CHECK ((((status = 'active'::text) AND (logout_at IS NULL) AND (revoked_at IS NULL)) OR (status <> 'active'::text))),
    CONSTRAINT ck_rtm_operator_session_expiry CHECK ((expires_at > login_at)),
    CONSTRAINT ck_rtm_operator_session_risk_flags CHECK ((jsonb_typeof(risk_flags) = 'array'::text)),
    CONSTRAINT rtm_operator_sessions_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_operator_sessions_status_check CHECK ((status = ANY (ARRAY['active'::text, 'closed'::text, 'revoked'::text, 'expired'::text]))),
    CONSTRAINT rtm_operator_sessions_token_sha256_check CHECK ((token_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: rtm_operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_operators (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    display_name text NOT NULL,
    password_hash text,
    status text DEFAULT 'invited'::text NOT NULL,
    primary_role_id uuid,
    must_change_password boolean DEFAULT true NOT NULL,
    mfa_required boolean DEFAULT false NOT NULL,
    profile jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_login_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    disabled_by uuid,
    disabled_at timestamp with time zone,
    failed_login_count integer DEFAULT 0 NOT NULL,
    last_failed_login_at timestamp with time zone,
    locked_until timestamp with time zone,
    password_changed_at timestamp with time zone,
    password_algorithm text DEFAULT 'argon2id'::text NOT NULL,
    password_version integer DEFAULT 1 NOT NULL,
    auth_epoch integer DEFAULT 1 NOT NULL,
    CONSTRAINT ck_rtm_operator_auth_epoch CHECK ((auth_epoch > 0)),
    CONSTRAINT ck_rtm_operator_disabled_state CHECK ((((status = 'disabled'::text) AND (disabled_at IS NOT NULL)) OR (status <> 'disabled'::text))),
    CONSTRAINT ck_rtm_operator_failed_login_count CHECK ((failed_login_count >= 0)),
    CONSTRAINT ck_rtm_operator_password_algorithm CHECK ((password_algorithm = 'argon2id'::text)),
    CONSTRAINT ck_rtm_operator_password_version CHECK ((password_version > 0)),
    CONSTRAINT rtm_operators_profile_check CHECK ((jsonb_typeof(profile) = 'object'::text)),
    CONSTRAINT rtm_operators_status_check CHECK ((status = ANY (ARRAY['invited'::text, 'active'::text, 'suspended'::text, 'disabled'::text])))
);


--
-- Name: rtm_presenter_admin_exports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_admin_exports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    package_id uuid,
    admin_operator_id uuid NOT NULL,
    reason text NOT NULL,
    reauthenticated_at timestamp with time zone NOT NULL,
    reauthentication_evidence_sha256 text NOT NULL,
    export_scope jsonb NOT NULL,
    watermark text NOT NULL,
    watermark_sha256 text NOT NULL,
    source_hashes jsonb NOT NULL,
    manifest_sha256 text NOT NULL,
    export_sha256 text NOT NULL,
    export_document_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_rtm_presenter_export_expiry CHECK (((expires_at > created_at) AND (expires_at <= (created_at + '01:00:00'::interval)))),
    CONSTRAINT ck_rtm_presenter_export_hashes CHECK (((reauthentication_evidence_sha256 ~ '^[0-9a-f]{64}$'::text) AND (watermark_sha256 ~ '^[0-9a-f]{64}$'::text) AND (manifest_sha256 ~ '^[0-9a-f]{64}$'::text) AND (export_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_presenter_export_payloads CHECK (((jsonb_typeof(export_scope) = 'object'::text) AND (jsonb_typeof(source_hashes) = 'array'::text) AND ((jsonb_array_length(source_hashes) >= 1) AND (jsonb_array_length(source_hashes) <= 50)) AND ((length(watermark) >= 8) AND (length(watermark) <= 500)) AND (NOT (export_scope ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text])))),
    CONSTRAINT ck_rtm_presenter_export_reason CHECK (((length(reason) >= 8) AND (length(reason) <= 500))),
    CONSTRAINT ck_rtm_presenter_export_reauthentication CHECK (((reauthenticated_at <= created_at) AND (reauthenticated_at >= (created_at - '00:05:00'::interval))))
);


--
-- Name: rtm_presenter_audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_audit_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sequence_number bigint NOT NULL,
    case_id uuid NOT NULL,
    package_id uuid,
    package_item_id uuid,
    handoff_ticket_id uuid,
    admin_export_id uuid,
    actor_type text NOT NULL,
    actor_operator_id uuid,
    event_type text NOT NULL,
    reason_code text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_presenter_audit_actor CHECK ((((actor_type = 'system'::text) AND (actor_operator_id IS NULL)) OR ((actor_type = ANY (ARRAY['operator'::text, 'admin'::text])) AND (actor_operator_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_presenter_audit_event_type CHECK (((event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'::text) AND (reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))),
    CONSTRAINT ck_rtm_presenter_audit_payload CHECK (((payload_sha256 ~ '^[0-9a-f]{64}$'::text) AND (jsonb_typeof(payload) = 'object'::text) AND (NOT (payload ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text, 'raw_ticket'::text]))))
);


--
-- Name: rtm_presenter_audit_events_sequence_number_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rtm_presenter_audit_events ALTER COLUMN sequence_number ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rtm_presenter_audit_events_sequence_number_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: rtm_presenter_destination_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_destination_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_code text NOT NULL,
    version_number integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    authority_code text NOT NULL,
    display_name text NOT NULL,
    portal_origin text NOT NULL,
    requirements jsonb NOT NULL,
    profile_sha256 text NOT NULL,
    created_by_operator_id uuid NOT NULL,
    verified_by_operator_id uuid,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_presenter_profile_code CHECK (((profile_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text) AND (authority_code ~ '^[a-z][a-z0-9_.-]{2,95}$'::text))),
    CONSTRAINT ck_rtm_presenter_profile_hash CHECK ((profile_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_profile_origin CHECK (((portal_origin ~ '^https://[A-Za-z0-9.-]+(:[0-9]{2,5})?$'::text) AND (portal_origin !~ '[/?#]$'::text))),
    CONSTRAINT ck_rtm_presenter_profile_payload CHECK ((((length(display_name) >= 3) AND (length(display_name) <= 160)) AND (jsonb_typeof(requirements) = 'object'::text) AND (jsonb_typeof(metadata) = 'object'::text) AND (NOT (requirements ?| ARRAY['password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text, 'private_key'::text, 'credential_ref'::text, 'b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text])) AND (NOT (metadata ?| ARRAY['password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text, 'private_key'::text, 'credential_ref'::text, 'b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text])))),
    CONSTRAINT ck_rtm_presenter_profile_status CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT ck_rtm_presenter_profile_verification CHECK ((((status = 'draft'::text) AND (verified_by_operator_id IS NULL) AND (verified_at IS NULL)) OR ((status = ANY (ARRAY['active'::text, 'retired'::text])) AND (verified_by_operator_id IS NOT NULL) AND (verified_at IS NOT NULL)))),
    CONSTRAINT ck_rtm_presenter_profile_version CHECK ((version_number > 0))
);


--
-- Name: rtm_presenter_document_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_document_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    logical_document_id uuid NOT NULL,
    version_number integer NOT NULL,
    supersedes_version_id uuid,
    source_document_id uuid NOT NULL,
    sha256 text NOT NULL,
    purpose text NOT NULL,
    state text DEFAULT 'draft'::text NOT NULL,
    scan_status text NOT NULL,
    original_filename text NOT NULL,
    detected_mime text NOT NULL,
    size_bytes bigint NOT NULL,
    source_kind text NOT NULL,
    created_by_operator_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_presenter_document_filename CHECK ((((length(original_filename) >= 1) AND (length(original_filename) <= 255)) AND (original_filename !~ '[\/\x00-\x1f]'::text))),
    CONSTRAINT ck_rtm_presenter_document_hash CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_document_metadata CHECK (((jsonb_typeof(metadata) = 'object'::text) AND (NOT (metadata ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text])))),
    CONSTRAINT ck_rtm_presenter_document_mime CHECK ((detected_mime ~ '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$'::text)),
    CONSTRAINT ck_rtm_presenter_document_purpose CHECK ((purpose ~ '^[a-z][a-z0-9_.-]{2,63}$'::text)),
    CONSTRAINT ck_rtm_presenter_document_scan CHECK ((scan_status = ANY (ARRAY['pending'::text, 'clean'::text, 'blocked'::text, 'error'::text]))),
    CONSTRAINT ck_rtm_presenter_document_scan_state CHECK ((((state = 'active'::text) AND (scan_status = 'clean'::text)) OR ((state = 'quarantined'::text) AND (scan_status = ANY (ARRAY['blocked'::text, 'error'::text]))) OR (state = ANY (ARRAY['draft'::text, 'review'::text, 'superseded'::text, 'rejected'::text])))),
    CONSTRAINT ck_rtm_presenter_document_size CHECK (((size_bytes > 0) AND (size_bytes <= 52428800))),
    CONSTRAINT ck_rtm_presenter_document_source_kind CHECK ((source_kind = ANY (ARRAY['customer_upload'::text, 'operator_upload'::text, 'generated'::text, 'external_revision'::text, 'derived_for_portal'::text, 'authorization'::text, 'receipt'::text, 'legacy_backfill'::text]))),
    CONSTRAINT ck_rtm_presenter_document_state CHECK ((state = ANY (ARRAY['draft'::text, 'review'::text, 'active'::text, 'superseded'::text, 'rejected'::text, 'quarantined'::text]))),
    CONSTRAINT ck_rtm_presenter_document_version_number CHECK (((version_number > 0) AND (((version_number = 1) AND (supersedes_version_id IS NULL)) OR ((version_number > 1) AND (supersedes_version_id IS NOT NULL)))))
);


--
-- Name: rtm_presenter_filing_packages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_filing_packages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    logical_package_id uuid NOT NULL,
    package_version integer NOT NULL,
    supersedes_package_id uuid,
    destination_profile_id uuid NOT NULL,
    representation_mode text NOT NULL,
    authorization_document_version_id uuid,
    status text DEFAULT 'draft'::text NOT NULL,
    manifest jsonb NOT NULL,
    manifest_sha256 text NOT NULL,
    expected_item_count integer DEFAULT 0 NOT NULL,
    created_by_operator_id uuid NOT NULL,
    frozen_by_operator_id uuid,
    frozen_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_presenter_package_expiry CHECK (((expires_at IS NULL) OR (expires_at > created_at))),
    CONSTRAINT ck_rtm_presenter_package_freeze CHECK ((((expected_item_count >= 0) AND (expected_item_count <= 50)) AND (((status = 'frozen'::text) AND (expected_item_count > 0) AND (frozen_by_operator_id IS NOT NULL) AND (frozen_at IS NOT NULL)) OR ((status = ANY (ARRAY['draft'::text, 'cancelled'::text])) AND (frozen_by_operator_id IS NULL) AND (frozen_at IS NULL))))),
    CONSTRAINT ck_rtm_presenter_package_hash CHECK ((manifest_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_package_manifest CHECK (((jsonb_typeof(manifest) = 'object'::text) AND (jsonb_typeof(metadata) = 'object'::text) AND (NOT (manifest ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text, 'private_key'::text, 'credential_ref'::text])) AND (NOT (metadata ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text, 'private_key'::text, 'credential_ref'::text])))),
    CONSTRAINT ck_rtm_presenter_package_representation CHECK ((((representation_mode = 'self'::text) AND (authorization_document_version_id IS NULL)) OR ((representation_mode = 'representative'::text) AND (authorization_document_version_id IS NOT NULL)))),
    CONSTRAINT ck_rtm_presenter_package_status CHECK ((status = ANY (ARRAY['draft'::text, 'frozen'::text, 'cancelled'::text]))),
    CONSTRAINT ck_rtm_presenter_package_version CHECK (((package_version > 0) AND (((package_version = 1) AND (supersedes_package_id IS NULL)) OR ((package_version > 1) AND (supersedes_package_id IS NOT NULL)))))
);


--
-- Name: rtm_presenter_handoff_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_handoff_tickets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticket_hash text NOT NULL,
    operator_id uuid NOT NULL,
    operator_session_id uuid NOT NULL,
    extension_client_id text NOT NULL,
    case_id uuid NOT NULL,
    package_id uuid NOT NULL,
    package_item_id uuid NOT NULL,
    portal_origin text NOT NULL,
    field_code text NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_presenter_ticket_extension CHECK ((extension_client_id = 'rtm.presenter.browser_extension.v1'::text)),
    CONSTRAINT ck_rtm_presenter_ticket_hash CHECK ((ticket_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_ticket_origin CHECK (((portal_origin ~ '^https://[A-Za-z0-9.-]+(:[0-9]{2,5})?$'::text) AND (field_code ~ '^[a-z][a-z0-9_.-]{1,95}$'::text))),
    CONSTRAINT ck_rtm_presenter_ticket_ttl CHECK (((expires_at > issued_at) AND (expires_at <= (issued_at + '00:15:00'::interval)) AND (created_at >= issued_at))),
    CONSTRAINT ck_rtm_presenter_ticket_use CHECK (((used_at IS NULL) OR ((used_at >= issued_at) AND (used_at <= expires_at))))
);


--
-- Name: rtm_presenter_idempotency_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_idempotency_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operator_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    request_sha256 text NOT NULL,
    case_id uuid NOT NULL,
    package_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_presenter_idempotency_hash CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_idempotency_key CHECK ((idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$'::text))
);


--
-- Name: rtm_presenter_package_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_package_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    package_id uuid NOT NULL,
    case_id uuid NOT NULL,
    item_order integer NOT NULL,
    document_version_id uuid NOT NULL,
    document_sha256 text NOT NULL,
    field_code text NOT NULL,
    purpose text NOT NULL,
    portal_filename text NOT NULL,
    required boolean DEFAULT true NOT NULL,
    item_manifest jsonb DEFAULT '{}'::jsonb NOT NULL,
    item_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_presenter_item_field CHECK (((field_code ~ '^[a-z][a-z0-9_.-]{1,95}$'::text) AND (purpose ~ '^[a-z][a-z0-9_.-]{2,63}$'::text))),
    CONSTRAINT ck_rtm_presenter_item_filename CHECK ((((length(portal_filename) >= 1) AND (length(portal_filename) <= 160)) AND (portal_filename !~ '[\/\x00-\x1f]'::text))),
    CONSTRAINT ck_rtm_presenter_item_hashes CHECK (((document_sha256 ~ '^[0-9a-f]{64}$'::text) AND (item_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_rtm_presenter_item_manifest CHECK (((jsonb_typeof(item_manifest) = 'object'::text) AND (NOT (item_manifest ?| ARRAY['b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'password'::text, 'access_token'::text, 'refresh_token'::text, 'cookie'::text, 'secret'::text])))),
    CONSTRAINT ck_rtm_presenter_item_order CHECK (((item_order >= 1) AND (item_order <= 50)))
);


--
-- Name: rtm_presenter_signer_installations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_presenter_signer_installations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operator_id uuid NOT NULL,
    operator_device_id uuid NOT NULL,
    client_instance_id uuid NOT NULL,
    client_binding_sha256 text NOT NULL,
    station_label text NOT NULL,
    platform text NOT NULL,
    client_version text NOT NULL,
    status text DEFAULT 'candidate'::text NOT NULL,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_rtm_presenter_signer_installation_binding CHECK ((client_binding_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_rtm_presenter_signer_installation_label CHECK ((((char_length(station_label) >= 3) AND (char_length(station_label) <= 80)) AND (station_label = btrim(station_label)) AND (station_label !~ '[[:cntrl:]]'::text))),
    CONSTRAINT ck_rtm_presenter_signer_installation_metadata CHECK (((jsonb_typeof(metadata) = 'object'::text) AND (metadata @> '{"synthetic_only": true, "contract_version": "rtm_presenter_local_station_v1_0", "portal_open_allowed": false, "document_bytes_allowed": false, "external_effects_allowed": false, "certificate_access_allowed": false, "managed_attestation_verified": false}'::jsonb) AND (NOT (metadata ?| ARRAY['password'::text, 'secret'::text, 'token'::text, 'cookie'::text, 'certificate'::text, 'private_key'::text, 'b2_bucket'::text, 'b2_key'::text, 'presigned_url'::text, 'portal_session'::text])))),
    CONSTRAINT ck_rtm_presenter_signer_installation_platform CHECK ((platform = 'windows'::text)),
    CONSTRAINT ck_rtm_presenter_signer_installation_status CHECK ((status = 'candidate'::text)),
    CONSTRAINT ck_rtm_presenter_signer_installation_version CHECK (((client_version ~ '^[0-9]+[.][0-9]+[.][0-9]+(?:[-+][A-Za-z0-9.-]+)?$'::text) AND (char_length(client_version) <= 48)))
);


--
-- Name: rtm_validated_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_validated_facts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    sequence integer NOT NULL,
    version text NOT NULL,
    service text NOT NULL,
    extractor_version text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    frozen boolean DEFAULT false NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    frozen_by text,
    frozen_at timestamp with time zone,
    invalidated_by text,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    supersedes_id uuid,
    source_extraction_id uuid,
    CONSTRAINT rtm_validated_facts_sequence_check CHECK ((sequence > 0))
);


--
-- Name: rtm_work_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rtm_work_assignments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid,
    attention_item_id uuid,
    operator_id uuid NOT NULL,
    assignment_role text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    team_code text,
    assigned_by uuid,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    accepted_at timestamp with time zone,
    released_at timestamp with time zone,
    release_reason text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_rtm_assignment_release_state CHECK ((((status = 'active'::text) AND (released_at IS NULL)) OR (status <> 'active'::text))),
    CONSTRAINT ck_rtm_assignment_target CHECK (((case_id IS NOT NULL) OR (attention_item_id IS NOT NULL))),
    CONSTRAINT rtm_work_assignments_assignment_role_check CHECK ((assignment_role = ANY (ARRAY['responsible'::text, 'reviewer'::text, 'supervisor'::text, 'observer'::text]))),
    CONSTRAINT rtm_work_assignments_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT rtm_work_assignments_status_check CHECK ((status = ANY (ARRAY['active'::text, 'released'::text, 'completed'::text, 'reassigned'::text])))
);


--
-- Name: submission_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    submission_id uuid NOT NULL,
    type text NOT NULL,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    case_id uuid NOT NULL,
    channel text DEFAULT 'DGT_DEV'::text NOT NULL,
    remesa_id text,
    notification_id text,
    status text DEFAULT 'queued'::text NOT NULL,
    context_intensity text,
    dry_run boolean DEFAULT true NOT NULL,
    retry_count integer DEFAULT 0 NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cases cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: extractions extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extractions
    ADD CONSTRAINT extractions_pkey PRIMARY KEY (id);


--
-- Name: ops_followups ops_followups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_followups
    ADD CONSTRAINT ops_followups_pkey PRIMARY KEY (id);


--
-- Name: partners partners_api_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_api_token_key UNIQUE (api_token);


--
-- Name: partners partners_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_email_key UNIQUE (email);


--
-- Name: partners partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_pkey PRIMARY KEY (id);


--
-- Name: rtm_attention_engine_runs rtm_attention_engine_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_engine_runs
    ADD CONSTRAINT rtm_attention_engine_runs_pkey PRIMARY KEY (id);


--
-- Name: rtm_attention_engine_runs rtm_attention_engine_runs_run_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_engine_runs
    ADD CONSTRAINT rtm_attention_engine_runs_run_key_key UNIQUE (run_key);


--
-- Name: rtm_attention_events rtm_attention_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_events
    ADD CONSTRAINT rtm_attention_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_attention_items rtm_attention_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_approvals rtm_connect_a1s_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_approvals
    ADD CONSTRAINT rtm_connect_a1s_approvals_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_artifacts rtm_connect_a1s_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT rtm_connect_a1s_artifacts_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_case_bindings rtm_connect_a1s_case_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_case_bindings
    ADD CONSTRAINT rtm_connect_a1s_case_bindings_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_events rtm_connect_a1s_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT rtm_connect_a1s_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_idempotency rtm_connect_a1s_idempotency_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_idempotency
    ADD CONSTRAINT rtm_connect_a1s_idempotency_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_memberships rtm_connect_a1s_memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_memberships
    ADD CONSTRAINT rtm_connect_a1s_memberships_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_representation_evidence rtm_connect_a1s_representation_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_representation_evidence
    ADD CONSTRAINT rtm_connect_a1s_representation_evidence_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_a1s_tenants rtm_connect_a1s_tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_tenants
    ADD CONSTRAINT rtm_connect_a1s_tenants_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_actions rtm_connect_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_actions
    ADD CONSTRAINT rtm_connect_actions_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_assisted_events rtm_connect_assisted_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_events
    ADD CONSTRAINT rtm_connect_assisted_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_attempts rtm_connect_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_attempts
    ADD CONSTRAINT rtm_connect_attempts_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_authorizations rtm_connect_authorizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_authorizations
    ADD CONSTRAINT rtm_connect_authorizations_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_connectors rtm_connect_connectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_connectors
    ADD CONSTRAINT rtm_connect_connectors_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_dispatch_outbox rtm_connect_dispatch_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_outbox
    ADD CONSTRAINT rtm_connect_dispatch_outbox_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_evidence rtm_connect_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_evidence
    ADD CONSTRAINT rtm_connect_evidence_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_idempotency_claims rtm_connect_idempotency_claims_action_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_idempotency_claims
    ADD CONSTRAINT rtm_connect_idempotency_claims_action_id_key UNIQUE (action_id);


--
-- Name: rtm_connect_idempotency_claims rtm_connect_idempotency_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_idempotency_claims
    ADD CONSTRAINT rtm_connect_idempotency_claims_pkey PRIMARY KEY (idempotency_key);


--
-- Name: rtm_connect_manual_events rtm_connect_manual_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_events
    ADD CONSTRAINT rtm_connect_manual_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_production_release_events rtm_connect_production_release_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_release_events
    ADD CONSTRAINT rtm_connect_production_release_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_production_releases rtm_connect_production_releases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releases_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_transitions rtm_connect_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_transitions
    ADD CONSTRAINT rtm_connect_transitions_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_webhook_events rtm_connect_webhook_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_events
    ADD CONSTRAINT rtm_connect_webhook_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_connect_webhook_inbox rtm_connect_webhook_inbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_inbox
    ADD CONSTRAINT rtm_connect_webhook_inbox_pkey PRIMARY KEY (id);


--
-- Name: rtm_core_schema_migrations rtm_core_schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_core_schema_migrations
    ADD CONSTRAINT rtm_core_schema_migrations_pkey PRIMARY KEY (name);


--
-- Name: rtm_deadlines rtm_deadlines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_pkey PRIMARY KEY (id);


--
-- Name: rtm_document_extractions rtm_document_extractions_case_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_document_extractions
    ADD CONSTRAINT rtm_document_extractions_case_id_sequence_key UNIQUE (case_id, sequence);


--
-- Name: rtm_document_extractions rtm_document_extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_document_extractions
    ADD CONSTRAINT rtm_document_extractions_pkey PRIMARY KEY (id);


--
-- Name: rtm_family_resolutions rtm_family_resolutions_case_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_family_resolutions
    ADD CONSTRAINT rtm_family_resolutions_case_id_sequence_key UNIQUE (case_id, sequence);


--
-- Name: rtm_family_resolutions rtm_family_resolutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_family_resolutions
    ADD CONSTRAINT rtm_family_resolutions_pkey PRIMARY KEY (id);


--
-- Name: rtm_generated_resources rtm_generated_resources_case_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_case_id_sequence_key UNIQUE (case_id, sequence);


--
-- Name: rtm_generated_resources rtm_generated_resources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_pkey PRIMARY KEY (id);


--
-- Name: rtm_legal_previews rtm_legal_previews_case_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT rtm_legal_previews_case_id_sequence_key UNIQUE (case_id, sequence);


--
-- Name: rtm_legal_previews rtm_legal_previews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT rtm_legal_previews_pkey PRIMARY KEY (id);


--
-- Name: rtm_management_schema_migrations rtm_management_schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_management_schema_migrations
    ADD CONSTRAINT rtm_management_schema_migrations_pkey PRIMARY KEY (name);


--
-- Name: rtm_operator_access_events rtm_operator_access_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_access_events
    ADD CONSTRAINT rtm_operator_access_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_operator_access_evidence rtm_operator_access_evidence_access_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_access_evidence
    ADD CONSTRAINT rtm_operator_access_evidence_access_event_id_key UNIQUE (access_event_id);


--
-- Name: rtm_operator_access_evidence rtm_operator_access_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_access_evidence
    ADD CONSTRAINT rtm_operator_access_evidence_pkey PRIMARY KEY (id);


--
-- Name: rtm_operator_devices rtm_operator_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_devices
    ADD CONSTRAINT rtm_operator_devices_pkey PRIMARY KEY (id);


--
-- Name: rtm_operator_roles rtm_operator_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_roles
    ADD CONSTRAINT rtm_operator_roles_pkey PRIMARY KEY (id);


--
-- Name: rtm_operator_sessions rtm_operator_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_pkey PRIMARY KEY (id);


--
-- Name: rtm_operator_sessions rtm_operator_sessions_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_token_sha256_key UNIQUE (token_sha256);


--
-- Name: rtm_operators rtm_operators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operators
    ADD CONSTRAINT rtm_operators_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_admin_exports rtm_presenter_admin_exports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_admin_exports
    ADD CONSTRAINT rtm_presenter_admin_exports_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_destination_profiles rtm_presenter_destination_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_destination_profiles
    ADD CONSTRAINT rtm_presenter_destination_profiles_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_document_versions rtm_presenter_document_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_document_versions
    ADD CONSTRAINT rtm_presenter_document_versions_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_idempotency_keys rtm_presenter_idempotency_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_idempotency_keys
    ADD CONSTRAINT rtm_presenter_idempotency_keys_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_package_items rtm_presenter_package_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_package_items
    ADD CONSTRAINT rtm_presenter_package_items_pkey PRIMARY KEY (id);


--
-- Name: rtm_presenter_signer_installations rtm_presenter_signer_installations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_signer_installations
    ADD CONSTRAINT rtm_presenter_signer_installations_pkey PRIMARY KEY (id);


--
-- Name: rtm_validated_facts rtm_validated_facts_case_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_validated_facts
    ADD CONSTRAINT rtm_validated_facts_case_id_sequence_key UNIQUE (case_id, sequence);


--
-- Name: rtm_validated_facts rtm_validated_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_validated_facts
    ADD CONSTRAINT rtm_validated_facts_pkey PRIMARY KEY (id);


--
-- Name: rtm_work_assignments rtm_work_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_work_assignments
    ADD CONSTRAINT rtm_work_assignments_pkey PRIMARY KEY (id);


--
-- Name: submission_events submission_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_events
    ADD CONSTRAINT submission_events_pkey PRIMARY KEY (id);


--
-- Name: submissions submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);


--
-- Name: idx_cases_authorized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_authorized ON public.cases USING btree (authorized);


--
-- Name: idx_cases_authorized_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_authorized_at ON public.cases USING btree (authorized_at);


--
-- Name: idx_cases_department_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_department_status ON public.cases USING btree (department, status);


--
-- Name: idx_cases_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_partner ON public.cases USING btree (partner_id);


--
-- Name: idx_cases_payment_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_payment_status ON public.cases USING btree (payment_status);


--
-- Name: idx_cases_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cases_status ON public.cases USING btree (status);


--
-- Name: idx_documents_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_case ON public.documents USING btree (case_id);


--
-- Name: idx_events_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_events_case ON public.events USING btree (case_id);


--
-- Name: idx_ops_followups_case_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ops_followups_case_due ON public.ops_followups USING btree (case_id, due_at, created_at DESC);


--
-- Name: idx_ops_followups_pending_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ops_followups_pending_due ON public.ops_followups USING btree (due_at, updated_at DESC) WHERE (status = 'pending'::text);


--
-- Name: idx_ops_followups_source_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ops_followups_source_event ON public.ops_followups USING btree (case_id, source_event_type);


--
-- Name: idx_partners_billing_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partners_billing_status ON public.partners USING btree (billing_status);


--
-- Name: idx_partners_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partners_email ON public.partners USING btree (email);


--
-- Name: idx_rtm_assignments_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_assignments_operator ON public.rtm_work_assignments USING btree (operator_id, status, assigned_at DESC);


--
-- Name: idx_rtm_attention_assignee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_attention_assignee ON public.rtm_attention_items USING btree (assigned_operator_id, status, due_at);


--
-- Name: idx_rtm_attention_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_attention_case ON public.rtm_attention_items USING btree (case_id, status, updated_at DESC);


--
-- Name: idx_rtm_attention_events_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_attention_events_case ON public.rtm_attention_events USING btree (case_id, created_at DESC);


--
-- Name: idx_rtm_attention_events_item; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_attention_events_item ON public.rtm_attention_events USING btree (attention_item_id, created_at DESC);


--
-- Name: idx_rtm_attention_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_attention_priority ON public.rtm_attention_items USING btree (status, severity, due_at, created_at);


--
-- Name: idx_rtm_connect_a1s_approval_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_approval_task ON public.rtm_connect_a1s_approvals USING btree (tenant_id, task_id, approved_at);


--
-- Name: idx_rtm_connect_a1s_artifact_task_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_artifact_task_kind ON public.rtm_connect_a1s_artifacts USING btree (tenant_id, task_id, kind, created_at);


--
-- Name: idx_rtm_connect_a1s_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_event_action ON public.rtm_connect_a1s_events USING btree (tenant_id, action_id, created_at);


--
-- Name: idx_rtm_connect_a1s_event_principal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_event_principal ON public.rtm_connect_a1s_events USING btree (tenant_id, principal_id, created_at) WHERE (principal_id IS NOT NULL);


--
-- Name: idx_rtm_connect_a1s_idempotency_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_idempotency_expiry ON public.rtm_connect_a1s_idempotency USING btree (status, expires_at);


--
-- Name: idx_rtm_connect_a1s_representation_binding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_representation_binding ON public.rtm_connect_a1s_representation_evidence USING btree (tenant_id, case_binding_id, status, expires_at);


--
-- Name: idx_rtm_connect_a1s_task_case_binding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_task_case_binding ON public.rtm_connect_a1s_human_tasks USING btree (tenant_id, case_binding_id);


--
-- Name: idx_rtm_connect_a1s_task_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_a1s_task_queue ON public.rtm_connect_a1s_human_tasks USING btree (tenant_id, status, due_at, created_at);


--
-- Name: idx_rtm_connect_action_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_action_case ON public.rtm_connect_actions USING btree (case_id, created_at DESC);


--
-- Name: idx_rtm_connect_action_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_action_queue ON public.rtm_connect_actions USING btree (status, next_attempt_at, created_at);


--
-- Name: idx_rtm_connect_action_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_action_status ON public.rtm_connect_actions USING btree (status, risk_class, updated_at DESC);


--
-- Name: idx_rtm_connect_assisted_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_assisted_event_action ON public.rtm_connect_assisted_events USING btree (action_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_assisted_event_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_assisted_event_operator ON public.rtm_connect_assisted_events USING btree (operator_id, created_at DESC);


--
-- Name: idx_rtm_connect_assisted_task_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_assisted_task_action ON public.rtm_connect_assisted_tasks USING btree (action_id, status, updated_at DESC);


--
-- Name: idx_rtm_connect_assisted_task_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_assisted_task_queue ON public.rtm_connect_assisted_tasks USING btree (assignee_operator_id, status, due_at, created_at);


--
-- Name: idx_rtm_connect_attempt_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_attempt_action ON public.rtm_connect_attempts USING btree (action_id, started_at DESC);


--
-- Name: idx_rtm_connect_attempt_external_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_attempt_external_reference ON public.rtm_connect_attempts USING btree (external_reference) WHERE (external_reference IS NOT NULL);


--
-- Name: idx_rtm_connect_authorization_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_authorization_action ON public.rtm_connect_authorizations USING btree (action_id, authorization_version DESC);


--
-- Name: idx_rtm_connect_connector_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_connector_status ON public.rtm_connect_connectors USING btree (status, environment, code);


--
-- Name: idx_rtm_connect_dispatch_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_dispatch_action ON public.rtm_connect_dispatch_outbox USING btree (action_id, created_at DESC);


--
-- Name: idx_rtm_connect_dispatch_claim_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_dispatch_claim_queue ON public.rtm_connect_dispatch_outbox USING btree (status, created_at, id) WHERE (status = 'prepared'::text);


--
-- Name: idx_rtm_connect_dispatch_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_dispatch_event_action ON public.rtm_connect_dispatch_events USING btree (action_id, sequence_number);


--
-- Name: idx_rtm_connect_dispatch_event_release; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_dispatch_event_release ON public.rtm_connect_dispatch_events USING btree (release_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_dispatch_release; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_dispatch_release ON public.rtm_connect_dispatch_outbox USING btree (release_id, status, created_at DESC);


--
-- Name: idx_rtm_connect_evidence_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_evidence_action ON public.rtm_connect_evidence USING btree (action_id, sequence_number DESC);


--
-- Name: idx_rtm_connect_idempotency_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_idempotency_action ON public.rtm_connect_idempotency_claims USING btree (action_id);


--
-- Name: idx_rtm_connect_manual_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_manual_event_action ON public.rtm_connect_manual_events USING btree (action_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_manual_event_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_manual_event_operator ON public.rtm_connect_manual_events USING btree (operator_id, created_at DESC);


--
-- Name: idx_rtm_connect_manual_task_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_manual_task_action ON public.rtm_connect_manual_tasks USING btree (action_id, status, updated_at DESC);


--
-- Name: idx_rtm_connect_manual_task_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_manual_task_queue ON public.rtm_connect_manual_tasks USING btree (assignee_operator_id, status, due_at, created_at);


--
-- Name: idx_rtm_connect_production_release_event_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_production_release_event_status ON public.rtm_connect_production_release_events USING btree (release_id, to_status, sequence_number);


--
-- Name: idx_rtm_connect_production_release_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_production_release_status ON public.rtm_connect_production_releases USING btree (status, valid_until, created_at DESC);


--
-- Name: idx_rtm_connect_reconciliation_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_reconciliation_action ON public.rtm_connect_reconciliations USING btree (action_id, status, created_at DESC);


--
-- Name: idx_rtm_connect_reconciliation_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_reconciliation_event_action ON public.rtm_connect_reconciliation_events USING btree (action_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_reconciliation_event_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_reconciliation_event_operator ON public.rtm_connect_reconciliation_events USING btree (operator_id, created_at DESC);


--
-- Name: idx_rtm_connect_reconciliation_event_webhook; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_reconciliation_event_webhook ON public.rtm_connect_reconciliation_events USING btree (webhook_inbox_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_reconciliation_external_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_reconciliation_external_reference ON public.rtm_connect_reconciliations USING btree (external_reference, request_sha256, created_at DESC);


--
-- Name: idx_rtm_connect_transition_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_transition_action ON public.rtm_connect_transitions USING btree (action_id, sequence_number);


--
-- Name: idx_rtm_connect_webhook_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_action ON public.rtm_connect_webhook_inbox USING btree (matched_action_id, status, received_at DESC);


--
-- Name: idx_rtm_connect_webhook_dead_letter; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_dead_letter ON public.rtm_connect_webhook_inbox USING btree (dead_letter_reason_code, processed_at, received_at) WHERE (status = 'dead_lettered'::text);


--
-- Name: idx_rtm_connect_webhook_event_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_event_action ON public.rtm_connect_webhook_events USING btree (action_id, created_at, sequence_number);


--
-- Name: idx_rtm_connect_webhook_event_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_event_operator ON public.rtm_connect_webhook_events USING btree (operator_id, created_at DESC);


--
-- Name: idx_rtm_connect_webhook_external_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_external_reference ON public.rtm_connect_webhook_inbox USING btree (origin_connector_code, external_reference, request_sha256, received_at DESC);


--
-- Name: idx_rtm_connect_webhook_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_connect_webhook_queue ON public.rtm_connect_webhook_inbox USING btree (status, received_at, created_at);


--
-- Name: idx_rtm_deadlines_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_deadlines_case ON public.rtm_deadlines USING btree (case_id, created_at DESC);


--
-- Name: idx_rtm_deadlines_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_deadlines_due ON public.rtm_deadlines USING btree (validation_status, due_at, deadline_class);


--
-- Name: idx_rtm_document_extractions_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_document_extractions_case ON public.rtm_document_extractions USING btree (case_id, sequence DESC);


--
-- Name: idx_rtm_document_extractions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_document_extractions_status ON public.rtm_document_extractions USING btree (status, created_at DESC);


--
-- Name: idx_rtm_engine_runs_health; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_engine_runs_health ON public.rtm_attention_engine_runs USING btree (status, started_at DESC, heartbeat_at DESC);


--
-- Name: idx_rtm_facts_source_extraction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_facts_source_extraction ON public.rtm_validated_facts USING btree (source_extraction_id);


--
-- Name: idx_rtm_family_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_family_case ON public.rtm_family_resolutions USING btree (case_id, sequence DESC);


--
-- Name: idx_rtm_generated_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_generated_case ON public.rtm_generated_resources USING btree (case_id, sequence DESC);


--
-- Name: idx_rtm_generated_submission; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_generated_submission ON public.rtm_generated_resources USING btree (case_id, approved_at, status);


--
-- Name: idx_rtm_operator_access_device_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_device_time ON public.rtm_operator_access_events USING btree (device_id, occurred_at DESC);


--
-- Name: idx_rtm_operator_access_evidence_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_evidence_retention ON public.rtm_operator_access_evidence USING btree (retention_until);


--
-- Name: idx_rtm_operator_access_ip_hash_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_ip_hash_time ON public.rtm_operator_access_events USING btree (ip_hash_sha256, occurred_at DESC);


--
-- Name: idx_rtm_operator_access_login_identifier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_login_identifier ON public.rtm_operator_access_events USING btree (login_identifier_sha256, occurred_at DESC);


--
-- Name: idx_rtm_operator_access_operator_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_operator_time ON public.rtm_operator_access_events USING btree (operator_id, occurred_at DESC);


--
-- Name: idx_rtm_operator_access_result_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_result_time ON public.rtm_operator_access_events USING btree (result, event_type, occurred_at DESC);


--
-- Name: idx_rtm_operator_access_session_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_access_session_time ON public.rtm_operator_access_events USING btree (session_id, occurred_at DESC);


--
-- Name: idx_rtm_operator_auth_lockout; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_auth_lockout ON public.rtm_operators USING btree (status, locked_until, failed_login_count);


--
-- Name: idx_rtm_operator_devices_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_devices_status ON public.rtm_operator_devices USING btree (operator_id, status, last_seen_at DESC);


--
-- Name: idx_rtm_operator_sessions_absolute_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_sessions_absolute_expiry ON public.rtm_operator_sessions USING btree (status, absolute_expires_at);


--
-- Name: idx_rtm_operator_sessions_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_sessions_active ON public.rtm_operator_sessions USING btree (operator_id, status, expires_at);


--
-- Name: idx_rtm_operator_sessions_device_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_sessions_device_active ON public.rtm_operator_sessions USING btree (device_id, status, last_seen_at DESC);


--
-- Name: idx_rtm_operator_sessions_epoch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_sessions_epoch ON public.rtm_operator_sessions USING btree (operator_id, auth_epoch, status, expires_at);


--
-- Name: idx_rtm_operator_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_operator_status ON public.rtm_operators USING btree (status, updated_at DESC);


--
-- Name: idx_rtm_presenter_admin_export_admin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_admin_export_admin ON public.rtm_presenter_admin_exports USING btree (admin_operator_id, created_at DESC);


--
-- Name: idx_rtm_presenter_admin_export_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_admin_export_case ON public.rtm_presenter_admin_exports USING btree (case_id, created_at DESC);


--
-- Name: idx_rtm_presenter_audit_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_audit_case ON public.rtm_presenter_audit_events USING btree (case_id, sequence_number DESC);


--
-- Name: idx_rtm_presenter_audit_package; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_audit_package ON public.rtm_presenter_audit_events USING btree (package_id, sequence_number DESC);


--
-- Name: idx_rtm_presenter_destination_profile_resolution; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_destination_profile_resolution ON public.rtm_presenter_destination_profiles USING btree (profile_code, status, version_number DESC);


--
-- Name: idx_rtm_presenter_document_case_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_document_case_state ON public.rtm_presenter_document_versions USING btree (case_id, state, purpose, created_at DESC);


--
-- Name: idx_rtm_presenter_handoff_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_handoff_expiry ON public.rtm_presenter_handoff_tickets USING btree (expires_at, used_at, case_id, package_id);


--
-- Name: idx_rtm_presenter_handoff_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_handoff_session ON public.rtm_presenter_handoff_tickets USING btree (operator_session_id, used_at, expires_at);


--
-- Name: idx_rtm_presenter_package_case_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_package_case_status ON public.rtm_presenter_filing_packages USING btree (case_id, status, created_at DESC);


--
-- Name: idx_rtm_presenter_package_destination; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_package_destination ON public.rtm_presenter_filing_packages USING btree (destination_profile_id, status, created_at DESC);


--
-- Name: idx_rtm_presenter_package_item_field; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_package_item_field ON public.rtm_presenter_package_items USING btree (package_id, field_code, item_order);


--
-- Name: idx_rtm_presenter_signer_installation_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_presenter_signer_installation_operator ON public.rtm_presenter_signer_installations USING btree (operator_id, registered_at DESC);


--
-- Name: idx_rtm_preview_authority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_preview_authority ON public.rtm_legal_previews USING btree (validated_facts_id, family_resolution_id);


--
-- Name: idx_rtm_preview_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_preview_case ON public.rtm_legal_previews USING btree (case_id, sequence DESC);


--
-- Name: idx_rtm_validated_facts_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rtm_validated_facts_case ON public.rtm_validated_facts USING btree (case_id, sequence DESC);


--
-- Name: idx_submission_events_sub; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_submission_events_sub ON public.submission_events USING btree (submission_id);


--
-- Name: idx_submissions_case; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_submissions_case ON public.submissions USING btree (case_id);


--
-- Name: idx_submissions_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_submissions_channel ON public.submissions USING btree (channel);


--
-- Name: idx_submissions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_submissions_status ON public.submissions USING btree (status);


--
-- Name: uq_rtm_active_document_extraction; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_active_document_extraction ON public.rtm_document_extractions USING btree (case_id) WHERE (invalidated_at IS NULL);


--
-- Name: uq_rtm_active_facts; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_active_facts ON public.rtm_validated_facts USING btree (case_id) WHERE (invalidated_at IS NULL);


--
-- Name: uq_rtm_active_family; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_active_family ON public.rtm_family_resolutions USING btree (case_id) WHERE (invalidated_at IS NULL);


--
-- Name: uq_rtm_active_generated_preview; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_active_generated_preview ON public.rtm_generated_resources USING btree (legal_preview_id) WHERE (status <> 'invalidated'::text);


--
-- Name: uq_rtm_active_preview; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_active_preview ON public.rtm_legal_previews USING btree (case_id) WHERE (status = ANY (ARRAY['draft'::text, 'ops_review'::text, 'approved'::text, 'frozen'::text]));


--
-- Name: uq_rtm_assignment_attention_role; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_assignment_attention_role ON public.rtm_work_assignments USING btree (attention_item_id, assignment_role) WHERE ((status = 'active'::text) AND (attention_item_id IS NOT NULL) AND (assignment_role <> 'observer'::text));


--
-- Name: uq_rtm_assignment_case_role; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_assignment_case_role ON public.rtm_work_assignments USING btree (case_id, assignment_role) WHERE ((status = 'active'::text) AND (attention_item_id IS NULL) AND (case_id IS NOT NULL) AND (assignment_role <> 'observer'::text));


--
-- Name: uq_rtm_attention_active_dedupe; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_attention_active_dedupe ON public.rtm_attention_items USING btree (dedupe_key) WHERE (status <> 'resolved'::text);


--
-- Name: uq_rtm_connect_a1s_active_case_binding_case_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_active_case_binding_case_id ON public.rtm_connect_a1s_case_bindings USING btree (case_id) WHERE (status = 'active'::text);


--
-- Name: uq_rtm_connect_a1s_approval_principal; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_approval_principal ON public.rtm_connect_a1s_approvals USING btree (task_id, principal_id);


--
-- Name: uq_rtm_connect_a1s_approval_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_approval_type ON public.rtm_connect_a1s_approvals USING btree (task_id, approval_type);


--
-- Name: uq_rtm_connect_a1s_artifact_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_artifact_code ON public.rtm_connect_a1s_artifacts USING btree (artifact_code);


--
-- Name: uq_rtm_connect_a1s_artifact_content; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_artifact_content ON public.rtm_connect_a1s_artifacts USING btree (task_id, kind, sha256);


--
-- Name: uq_rtm_connect_a1s_case_binding_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_case_binding_code ON public.rtm_connect_a1s_case_bindings USING btree (binding_code);


--
-- Name: uq_rtm_connect_a1s_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_event_sequence ON public.rtm_connect_a1s_events USING btree (task_id, sequence_number);


--
-- Name: uq_rtm_connect_a1s_idempotency_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_idempotency_key ON public.rtm_connect_a1s_idempotency USING btree (tenant_id, idempotency_key);


--
-- Name: uq_rtm_connect_a1s_membership_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_membership_identity ON public.rtm_connect_a1s_memberships USING btree (id, tenant_id, principal_id, operator_id);


--
-- Name: uq_rtm_connect_a1s_membership_operator; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_membership_operator ON public.rtm_connect_a1s_memberships USING btree (tenant_id, operator_id);


--
-- Name: uq_rtm_connect_a1s_membership_principal; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_membership_principal ON public.rtm_connect_a1s_memberships USING btree (tenant_id, principal_id);


--
-- Name: uq_rtm_connect_a1s_representation_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_representation_code ON public.rtm_connect_a1s_representation_evidence USING btree (representation_code);


--
-- Name: uq_rtm_connect_a1s_task_action; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_task_action ON public.rtm_connect_a1s_human_tasks USING btree (action_id);


--
-- Name: uq_rtm_connect_a1s_task_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_task_attempt ON public.rtm_connect_a1s_human_tasks USING btree (attempt_id);


--
-- Name: uq_rtm_connect_a1s_task_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_task_code ON public.rtm_connect_a1s_human_tasks USING btree (task_code);


--
-- Name: uq_rtm_connect_a1s_tenant_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_a1s_tenant_code ON public.rtm_connect_a1s_tenants USING btree (tenant_code);


--
-- Name: uq_rtm_connect_action_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_action_idempotency ON public.rtm_connect_actions USING btree (idempotency_key);


--
-- Name: uq_rtm_connect_assisted_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_assisted_event_sequence ON public.rtm_connect_assisted_events USING btree (task_id, sequence_number);


--
-- Name: uq_rtm_connect_assisted_task_action; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_assisted_task_action ON public.rtm_connect_assisted_tasks USING btree (action_id);


--
-- Name: uq_rtm_connect_assisted_task_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_assisted_task_attempt ON public.rtm_connect_assisted_tasks USING btree (attempt_id);


--
-- Name: uq_rtm_connect_assisted_task_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_assisted_task_code ON public.rtm_connect_assisted_tasks USING btree (task_code);


--
-- Name: uq_rtm_connect_attempt_number; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_attempt_number ON public.rtm_connect_attempts USING btree (action_id, attempt_number);


--
-- Name: uq_rtm_connect_authorization_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_authorization_version ON public.rtm_connect_authorizations USING btree (action_id, authorization_version);


--
-- Name: uq_rtm_connect_connector_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_connector_version ON public.rtm_connect_connectors USING btree (code, version);


--
-- Name: uq_rtm_connect_dispatch_business_command; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_dispatch_business_command ON public.rtm_connect_dispatch_outbox USING btree (business_command_id);


--
-- Name: uq_rtm_connect_dispatch_claim_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_dispatch_claim_token ON public.rtm_connect_dispatch_outbox USING btree (claim_token) WHERE (claim_token IS NOT NULL);


--
-- Name: uq_rtm_connect_dispatch_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_dispatch_event_sequence ON public.rtm_connect_dispatch_events USING btree (outbox_id, sequence_number);


--
-- Name: uq_rtm_connect_dispatch_production_effect; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_dispatch_production_effect ON public.rtm_connect_dispatch_outbox USING btree (production_effect_key);


--
-- Name: uq_rtm_connect_dispatch_release_once; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_dispatch_release_once ON public.rtm_connect_dispatch_outbox USING btree (release_id);


--
-- Name: uq_rtm_connect_evidence_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_evidence_sequence ON public.rtm_connect_evidence USING btree (action_id, sequence_number);


--
-- Name: uq_rtm_connect_manual_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_manual_event_sequence ON public.rtm_connect_manual_events USING btree (task_id, sequence_number);


--
-- Name: uq_rtm_connect_manual_task_action; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_manual_task_action ON public.rtm_connect_manual_tasks USING btree (action_id);


--
-- Name: uq_rtm_connect_manual_task_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_manual_task_attempt ON public.rtm_connect_manual_tasks USING btree (attempt_id);


--
-- Name: uq_rtm_connect_manual_task_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_manual_task_code ON public.rtm_connect_manual_tasks USING btree (task_code);


--
-- Name: uq_rtm_connect_production_release_binding; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_production_release_binding ON public.rtm_connect_production_releases USING btree (release_binding_sha256);


--
-- Name: uq_rtm_connect_production_release_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_production_release_code ON public.rtm_connect_production_releases USING btree (release_code);


--
-- Name: uq_rtm_connect_production_release_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_production_release_event_sequence ON public.rtm_connect_production_release_events USING btree (release_id, sequence_number);


--
-- Name: uq_rtm_connect_reconciliation_action_number; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_reconciliation_action_number ON public.rtm_connect_reconciliations USING btree (action_id, reconciliation_number);


--
-- Name: uq_rtm_connect_reconciliation_active_action; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_reconciliation_active_action ON public.rtm_connect_reconciliations USING btree (action_id) WHERE (status = 'started'::text);


--
-- Name: uq_rtm_connect_reconciliation_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_reconciliation_event_sequence ON public.rtm_connect_reconciliation_events USING btree (reconciliation_id, sequence_number);


--
-- Name: uq_rtm_connect_reconciliation_webhook; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_reconciliation_webhook ON public.rtm_connect_reconciliations USING btree (webhook_inbox_id);


--
-- Name: uq_rtm_connect_transition_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_transition_sequence ON public.rtm_connect_transitions USING btree (action_id, sequence_number);


--
-- Name: uq_rtm_connect_webhook_deduplication; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_webhook_deduplication ON public.rtm_connect_webhook_inbox USING btree (deduplication_key);


--
-- Name: uq_rtm_connect_webhook_event_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_webhook_event_sequence ON public.rtm_connect_webhook_events USING btree (webhook_inbox_id, sequence_number);


--
-- Name: uq_rtm_connect_webhook_source_event; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_connect_webhook_source_event ON public.rtm_connect_webhook_inbox USING btree (ingress_connector_id, source_event_id);


--
-- Name: uq_rtm_operator_device_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_operator_device_key ON public.rtm_operator_devices USING btree (operator_id, device_key_sha256);


--
-- Name: uq_rtm_operator_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_operator_email ON public.rtm_operators USING btree (lower(btrim(email)));


--
-- Name: uq_rtm_operator_role_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_operator_role_code ON public.rtm_operator_roles USING btree (code);


--
-- Name: uq_rtm_presenter_audit_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_audit_sequence ON public.rtm_presenter_audit_events USING btree (sequence_number);


--
-- Name: uq_rtm_presenter_destination_profile_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_destination_profile_version ON public.rtm_presenter_destination_profiles USING btree (profile_code, version_number);


--
-- Name: uq_rtm_presenter_document_source; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_document_source ON public.rtm_presenter_document_versions USING btree (case_id, source_document_id);


--
-- Name: uq_rtm_presenter_document_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_document_version ON public.rtm_presenter_document_versions USING btree (case_id, logical_document_id, version_number);


--
-- Name: uq_rtm_presenter_handoff_ticket_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_handoff_ticket_hash ON public.rtm_presenter_handoff_tickets USING btree (ticket_hash);


--
-- Name: uq_rtm_presenter_idempotency_operator_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_idempotency_operator_key ON public.rtm_presenter_idempotency_keys USING btree (operator_id, idempotency_key);


--
-- Name: uq_rtm_presenter_package_item_document; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_package_item_document ON public.rtm_presenter_package_items USING btree (package_id, document_version_id);


--
-- Name: uq_rtm_presenter_package_item_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_package_item_order ON public.rtm_presenter_package_items USING btree (package_id, item_order);


--
-- Name: uq_rtm_presenter_package_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_package_version ON public.rtm_presenter_filing_packages USING btree (case_id, logical_package_id, package_version);


--
-- Name: uq_rtm_presenter_signer_installation_binding; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_signer_installation_binding ON public.rtm_presenter_signer_installations USING btree (client_binding_sha256);


--
-- Name: uq_rtm_presenter_signer_installation_instance; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_rtm_presenter_signer_installation_instance ON public.rtm_presenter_signer_installations USING btree (operator_id, operator_device_id, client_instance_id);


--
-- Name: rtm_attention_events trg_rtm_attention_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_attention_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_attention_events FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_attention_events_append_only();


--
-- Name: rtm_connect_a1s_approvals trg_rtm_connect_a1s_approval_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_approval_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_a1s_approvals FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_reject_mutation();


--
-- Name: rtm_connect_a1s_approvals trg_rtm_connect_a1s_approval_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_approval_scope_guard BEFORE INSERT ON public.rtm_connect_a1s_approvals FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_approval_scope_guard();


--
-- Name: rtm_connect_a1s_artifacts trg_rtm_connect_a1s_artifact_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_artifact_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_a1s_artifacts FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_reject_mutation();


--
-- Name: rtm_connect_a1s_artifacts trg_rtm_connect_a1s_artifact_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_artifact_scope_guard BEFORE INSERT ON public.rtm_connect_a1s_artifacts FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_artifact_scope_guard();


--
-- Name: rtm_connect_a1s_case_bindings trg_rtm_connect_a1s_case_binding_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_case_binding_frozen BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_connect_a1s_case_bindings FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_binding_frozen_guard();


--
-- Name: rtm_connect_a1s_events trg_rtm_connect_a1s_event_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_event_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_a1s_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_reject_mutation();


--
-- Name: rtm_connect_a1s_events trg_rtm_connect_a1s_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_event_scope_guard BEFORE INSERT ON public.rtm_connect_a1s_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_event_scope_guard();


--
-- Name: rtm_connect_a1s_idempotency trg_rtm_connect_a1s_idempotency_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_idempotency_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_connect_a1s_idempotency FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_idempotency_guard();


--
-- Name: rtm_connect_a1s_memberships trg_rtm_connect_a1s_membership_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_membership_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_connect_a1s_memberships FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_membership_guard();


--
-- Name: rtm_connect_a1s_representation_evidence trg_rtm_connect_a1s_representation_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_representation_frozen BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_connect_a1s_representation_evidence FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_representation_frozen_guard();


--
-- Name: rtm_connect_a1s_human_tasks trg_rtm_connect_a1s_task_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_task_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_connect_a1s_human_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_task_guard();


--
-- Name: rtm_connect_a1s_tenants trg_rtm_connect_a1s_tenant_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_a1s_tenant_frozen BEFORE DELETE OR UPDATE ON public.rtm_connect_a1s_tenants FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_a1s_tenant_frozen_guard();


--
-- Name: rtm_connect_actions trg_rtm_connect_actions_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_actions_state_guard BEFORE UPDATE OF status ON public.rtm_connect_actions FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_connect_action_transition();


--
-- Name: rtm_connect_assisted_events trg_rtm_connect_assisted_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_assisted_event_scope_guard BEFORE INSERT ON public.rtm_connect_assisted_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_assisted_event_scope_guard();


--
-- Name: rtm_connect_assisted_events trg_rtm_connect_assisted_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_assisted_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_assisted_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_assisted_events_append_only();


--
-- Name: rtm_connect_assisted_tasks trg_rtm_connect_assisted_task_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_assisted_task_frozen BEFORE UPDATE ON public.rtm_connect_assisted_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_assisted_task_frozen();


--
-- Name: rtm_connect_assisted_tasks trg_rtm_connect_assisted_task_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_assisted_task_scope_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_assisted_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_assisted_task_scope_guard();


--
-- Name: rtm_connect_assisted_tasks trg_rtm_connect_assisted_task_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_assisted_task_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_assisted_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_assisted_task_state_guard();


--
-- Name: rtm_connect_authorizations trg_rtm_connect_authorizations_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_authorizations_immutable BEFORE DELETE OR UPDATE ON public.rtm_connect_authorizations FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_connect_append_only();


--
-- Name: rtm_connect_dispatch_events trg_rtm_connect_dispatch_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_event_scope_guard BEFORE INSERT ON public.rtm_connect_dispatch_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_dispatch_event_scope_guard();


--
-- Name: rtm_connect_dispatch_events trg_rtm_connect_dispatch_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_dispatch_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_c8_append_only_guard();


--
-- Name: rtm_connect_dispatch_events trg_rtm_connect_dispatch_events_truncate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_events_truncate_guard BEFORE TRUNCATE ON public.rtm_connect_dispatch_events FOR EACH STATEMENT EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_dispatch_outbox trg_rtm_connect_dispatch_outbox_delete_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_outbox_delete_guard BEFORE DELETE ON public.rtm_connect_dispatch_outbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_dispatch_outbox trg_rtm_connect_dispatch_outbox_frozen_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_outbox_frozen_guard BEFORE UPDATE ON public.rtm_connect_dispatch_outbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_dispatch_outbox_frozen_guard();


--
-- Name: rtm_connect_dispatch_outbox trg_rtm_connect_dispatch_outbox_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_outbox_scope_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_dispatch_outbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_dispatch_outbox_scope_guard();


--
-- Name: rtm_connect_dispatch_outbox trg_rtm_connect_dispatch_outbox_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_outbox_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_dispatch_outbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_dispatch_outbox_state_guard();


--
-- Name: rtm_connect_dispatch_outbox trg_rtm_connect_dispatch_outbox_truncate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_dispatch_outbox_truncate_guard BEFORE TRUNCATE ON public.rtm_connect_dispatch_outbox FOR EACH STATEMENT EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_evidence trg_rtm_connect_evidence_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_evidence_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_evidence FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_connect_append_only();


--
-- Name: rtm_connect_manual_events trg_rtm_connect_manual_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_manual_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_manual_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_manual_events_append_only();


--
-- Name: rtm_connect_manual_tasks trg_rtm_connect_manual_task_package_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_manual_task_package_frozen BEFORE UPDATE ON public.rtm_connect_manual_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_manual_task_package_frozen();


--
-- Name: rtm_connect_manual_tasks trg_rtm_connect_manual_task_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_manual_task_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_manual_tasks FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_manual_task_state_guard();


--
-- Name: rtm_connect_production_releases trg_rtm_connect_production_release_delete_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_delete_guard BEFORE DELETE ON public.rtm_connect_production_releases FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_production_release_events trg_rtm_connect_production_release_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_event_scope_guard BEFORE INSERT ON public.rtm_connect_production_release_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_production_release_event_scope_guard();


--
-- Name: rtm_connect_production_release_events trg_rtm_connect_production_release_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_production_release_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_c8_append_only_guard();


--
-- Name: rtm_connect_production_release_events trg_rtm_connect_production_release_events_truncate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_events_truncate_guard BEFORE TRUNCATE ON public.rtm_connect_production_release_events FOR EACH STATEMENT EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_production_releases trg_rtm_connect_production_release_frozen_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_frozen_guard BEFORE UPDATE ON public.rtm_connect_production_releases FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_production_release_frozen_guard();


--
-- Name: rtm_connect_production_releases trg_rtm_connect_production_release_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_production_releases FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_production_release_state_guard();


--
-- Name: rtm_connect_production_releases trg_rtm_connect_production_release_truncate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_production_release_truncate_guard BEFORE TRUNCATE ON public.rtm_connect_production_releases FOR EACH STATEMENT EXECUTE FUNCTION public.rtm_connect_c8_delete_guard();


--
-- Name: rtm_connect_reconciliation_events trg_rtm_connect_reconciliation_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_reconciliation_event_scope_guard BEFORE INSERT ON public.rtm_connect_reconciliation_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_reconciliation_event_scope_guard();


--
-- Name: rtm_connect_reconciliation_events trg_rtm_connect_reconciliation_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_reconciliation_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_reconciliation_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_reconciliation_events_append_only();


--
-- Name: rtm_connect_reconciliations trg_rtm_connect_reconciliation_identity_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_reconciliation_identity_frozen BEFORE UPDATE ON public.rtm_connect_reconciliations FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_reconciliation_identity_frozen();


--
-- Name: rtm_connect_reconciliations trg_rtm_connect_reconciliation_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_reconciliation_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_reconciliations FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_reconciliation_state_guard();


--
-- Name: rtm_connect_transitions trg_rtm_connect_transitions_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_transitions_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_transitions FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_connect_append_only();


--
-- Name: rtm_connect_webhook_events trg_rtm_connect_webhook_event_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_webhook_event_scope_guard BEFORE INSERT ON public.rtm_connect_webhook_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_webhook_event_scope_guard();


--
-- Name: rtm_connect_webhook_events trg_rtm_connect_webhook_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_webhook_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_connect_webhook_events FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_webhook_events_append_only();


--
-- Name: rtm_connect_webhook_inbox trg_rtm_connect_webhook_identity_frozen; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_webhook_identity_frozen BEFORE UPDATE ON public.rtm_connect_webhook_inbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_webhook_identity_frozen();


--
-- Name: rtm_connect_webhook_inbox trg_rtm_connect_webhook_match_scope_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_webhook_match_scope_guard BEFORE UPDATE ON public.rtm_connect_webhook_inbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_webhook_match_scope_guard();


--
-- Name: rtm_connect_webhook_inbox trg_rtm_connect_webhook_state_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_connect_webhook_state_guard BEFORE INSERT OR UPDATE ON public.rtm_connect_webhook_inbox FOR EACH ROW EXECUTE FUNCTION public.rtm_connect_webhook_state_guard();


--
-- Name: rtm_operator_access_events trg_rtm_operator_access_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_operator_access_events_append_only BEFORE DELETE OR UPDATE ON public.rtm_operator_access_events FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_operator_access_events_append_only();


--
-- Name: rtm_operator_access_evidence trg_rtm_operator_access_evidence_retention; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_operator_access_evidence_retention BEFORE DELETE OR UPDATE ON public.rtm_operator_access_evidence FOR EACH ROW EXECUTE FUNCTION public.rtm_guard_operator_access_evidence_retention();


--
-- Name: rtm_presenter_admin_exports trg_rtm_presenter_admin_export_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_admin_export_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_admin_exports FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_admin_exports trg_rtm_presenter_admin_export_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_admin_export_scope BEFORE INSERT ON public.rtm_presenter_admin_exports FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_admin_export_scope_guard();


--
-- Name: rtm_presenter_audit_events trg_rtm_presenter_audit_event_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_audit_event_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_audit_events FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_audit_events trg_rtm_presenter_audit_event_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_audit_event_scope BEFORE INSERT ON public.rtm_presenter_audit_events FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_audit_event_scope_guard();


--
-- Name: rtm_presenter_destination_profiles trg_rtm_presenter_destination_profile_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_destination_profile_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_destination_profiles FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_destination_profiles trg_rtm_presenter_destination_profile_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_destination_profile_scope BEFORE INSERT ON public.rtm_presenter_destination_profiles FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_destination_profile_scope_guard();


--
-- Name: rtm_presenter_document_versions trg_rtm_presenter_document_version_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_document_version_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_document_versions FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_document_versions trg_rtm_presenter_document_version_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_document_version_scope BEFORE INSERT ON public.rtm_presenter_document_versions FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_document_version_scope_guard();


--
-- Name: rtm_presenter_filing_packages trg_rtm_presenter_filing_package_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_filing_package_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_presenter_filing_packages FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_filing_package_guard();


--
-- Name: rtm_presenter_handoff_tickets trg_rtm_presenter_handoff_ticket_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_handoff_ticket_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_presenter_handoff_tickets FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_handoff_ticket_guard();


--
-- Name: rtm_presenter_idempotency_keys trg_rtm_presenter_idempotency_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_idempotency_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_idempotency_keys FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_idempotency_keys trg_rtm_presenter_idempotency_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_idempotency_scope BEFORE INSERT ON public.rtm_presenter_idempotency_keys FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_idempotency_scope_guard();


--
-- Name: rtm_presenter_package_items trg_rtm_presenter_package_item_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_package_item_guard BEFORE INSERT OR DELETE OR UPDATE ON public.rtm_presenter_package_items FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_package_item_guard();


--
-- Name: rtm_presenter_signer_installations trg_rtm_presenter_signer_installation_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_signer_installation_append_only BEFORE DELETE OR UPDATE ON public.rtm_presenter_signer_installations FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_reject_mutation();


--
-- Name: rtm_presenter_signer_installations trg_rtm_presenter_signer_installation_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_rtm_presenter_signer_installation_scope BEFORE INSERT ON public.rtm_presenter_signer_installations FOR EACH ROW EXECUTE FUNCTION public.rtm_presenter_signer_installation_scope_guard();


--
-- Name: cases cases_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cases
    ADD CONSTRAINT cases_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.partners(id);


--
-- Name: documents documents_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: events events_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: extractions extractions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extractions
    ADD CONSTRAINT extractions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_connect_a1s_approvals fk_rtm_connect_a1s_approval_actor; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_approvals
    ADD CONSTRAINT fk_rtm_connect_a1s_approval_actor FOREIGN KEY (membership_id, tenant_id, principal_id, operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_artifacts fk_rtm_connect_a1s_artifact_submitter; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT fk_rtm_connect_a1s_artifact_submitter FOREIGN KEY (submitted_by_membership_id, tenant_id, submitted_by_principal_id, submitted_by_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_artifacts fk_rtm_connect_a1s_artifact_verifier; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT fk_rtm_connect_a1s_artifact_verifier FOREIGN KEY (verified_by_membership_id, tenant_id, verified_by_principal_id, verified_by_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_events fk_rtm_connect_a1s_event_actor; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT fk_rtm_connect_a1s_event_actor FOREIGN KEY (membership_id, tenant_id, principal_id, operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_idempotency fk_rtm_connect_a1s_idempotency_actor; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_idempotency
    ADD CONSTRAINT fk_rtm_connect_a1s_idempotency_actor FOREIGN KEY (claimed_by_membership_id, tenant_id, claimed_by_principal_id, claimed_by_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_representation_evidence fk_rtm_connect_a1s_representation_recorder; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_representation_evidence
    ADD CONSTRAINT fk_rtm_connect_a1s_representation_recorder FOREIGN KEY (recorded_by_membership_id, tenant_id, recorded_by_principal_id, recorded_by_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks fk_rtm_connect_a1s_task_assignee; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT fk_rtm_connect_a1s_task_assignee FOREIGN KEY (assignee_membership_id, tenant_id, assignee_principal_id, assignee_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks fk_rtm_connect_a1s_task_releaser; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT fk_rtm_connect_a1s_task_releaser FOREIGN KEY (release_membership_id, tenant_id, release_principal_id, release_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks fk_rtm_connect_a1s_task_requester; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT fk_rtm_connect_a1s_task_requester FOREIGN KEY (requester_membership_id, tenant_id, requester_principal_id, requester_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks fk_rtm_connect_a1s_task_verifier; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT fk_rtm_connect_a1s_task_verifier FOREIGN KEY (verified_by_membership_id, tenant_id, verified_by_principal_id, verified_by_operator_id) REFERENCES public.rtm_connect_a1s_memberships(id, tenant_id, principal_id, operator_id) ON DELETE RESTRICT;


--
-- Name: rtm_validated_facts fk_rtm_facts_source_extraction; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_validated_facts
    ADD CONSTRAINT fk_rtm_facts_source_extraction FOREIGN KEY (source_extraction_id) REFERENCES public.rtm_document_extractions(id);


--
-- Name: rtm_validated_facts fk_rtm_facts_supersedes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_validated_facts
    ADD CONSTRAINT fk_rtm_facts_supersedes FOREIGN KEY (supersedes_id) REFERENCES public.rtm_validated_facts(id);


--
-- Name: rtm_family_resolutions fk_rtm_family_facts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_family_resolutions
    ADD CONSTRAINT fk_rtm_family_facts FOREIGN KEY (validated_facts_id) REFERENCES public.rtm_validated_facts(id);


--
-- Name: rtm_family_resolutions fk_rtm_family_supersedes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_family_resolutions
    ADD CONSTRAINT fk_rtm_family_supersedes FOREIGN KEY (supersedes_id) REFERENCES public.rtm_family_resolutions(id);


--
-- Name: rtm_legal_previews fk_rtm_preview_facts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT fk_rtm_preview_facts FOREIGN KEY (validated_facts_id) REFERENCES public.rtm_validated_facts(id);


--
-- Name: rtm_legal_previews fk_rtm_preview_family; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT fk_rtm_preview_family FOREIGN KEY (family_resolution_id) REFERENCES public.rtm_family_resolutions(id);


--
-- Name: rtm_legal_previews fk_rtm_preview_supersedes; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT fk_rtm_preview_supersedes FOREIGN KEY (supersedes_id) REFERENCES public.rtm_legal_previews(id);


--
-- Name: ops_followups ops_followups_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ops_followups
    ADD CONSTRAINT ops_followups_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_attention_items rtm_attention_items_assigned_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_assigned_operator_id_fkey FOREIGN KEY (assigned_operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_attention_items rtm_attention_items_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_attention_items rtm_attention_items_in_review_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_in_review_by_fkey FOREIGN KEY (in_review_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_attention_items rtm_attention_items_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_attention_items rtm_attention_items_seen_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_seen_by_fkey FOREIGN KEY (seen_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_attention_items rtm_attention_items_source_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_source_document_id_fkey FOREIGN KEY (source_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: rtm_attention_items rtm_attention_items_source_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_attention_items
    ADD CONSTRAINT rtm_attention_items_source_event_id_fkey FOREIGN KEY (source_event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_a1s_approvals rtm_connect_a1s_approvals_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_approvals
    ADD CONSTRAINT rtm_connect_a1s_approvals_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.rtm_connect_a1s_artifacts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_approvals rtm_connect_a1s_approvals_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_approvals
    ADD CONSTRAINT rtm_connect_a1s_approvals_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_a1s_human_tasks(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_approvals rtm_connect_a1s_approvals_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_approvals
    ADD CONSTRAINT rtm_connect_a1s_approvals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_artifacts rtm_connect_a1s_artifacts_supersedes_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT rtm_connect_a1s_artifacts_supersedes_artifact_id_fkey FOREIGN KEY (supersedes_artifact_id) REFERENCES public.rtm_connect_a1s_artifacts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_artifacts rtm_connect_a1s_artifacts_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT rtm_connect_a1s_artifacts_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_a1s_human_tasks(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_artifacts rtm_connect_a1s_artifacts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_artifacts
    ADD CONSTRAINT rtm_connect_a1s_artifacts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_case_bindings rtm_connect_a1s_case_bindings_bound_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_case_bindings
    ADD CONSTRAINT rtm_connect_a1s_case_bindings_bound_by_operator_id_fkey FOREIGN KEY (bound_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_case_bindings rtm_connect_a1s_case_bindings_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_case_bindings
    ADD CONSTRAINT rtm_connect_a1s_case_bindings_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_case_bindings rtm_connect_a1s_case_bindings_revoked_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_case_bindings
    ADD CONSTRAINT rtm_connect_a1s_case_bindings_revoked_by_operator_id_fkey FOREIGN KEY (revoked_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_case_bindings rtm_connect_a1s_case_bindings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_case_bindings
    ADD CONSTRAINT rtm_connect_a1s_case_bindings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_events rtm_connect_a1s_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT rtm_connect_a1s_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_events rtm_connect_a1s_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT rtm_connect_a1s_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_events rtm_connect_a1s_events_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT rtm_connect_a1s_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_a1s_human_tasks(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_events rtm_connect_a1s_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_events
    ADD CONSTRAINT rtm_connect_a1s_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_assigned_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_assigned_by_operator_id_fkey FOREIGN KEY (assigned_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_authorization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_authorization_id_fkey FOREIGN KEY (authorization_id) REFERENCES public.rtm_connect_authorizations(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_case_binding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_case_binding_id_fkey FOREIGN KEY (case_binding_id) REFERENCES public.rtm_connect_a1s_case_bindings(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_representation_evidence_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_representation_evidence_id_fkey FOREIGN KEY (representation_evidence_id) REFERENCES public.rtm_connect_a1s_representation_evidence(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_human_tasks rtm_connect_a1s_human_tasks_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_human_tasks
    ADD CONSTRAINT rtm_connect_a1s_human_tasks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_idempotency rtm_connect_a1s_idempotency_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_idempotency
    ADD CONSTRAINT rtm_connect_a1s_idempotency_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_idempotency rtm_connect_a1s_idempotency_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_idempotency
    ADD CONSTRAINT rtm_connect_a1s_idempotency_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_a1s_human_tasks(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_idempotency rtm_connect_a1s_idempotency_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_idempotency
    ADD CONSTRAINT rtm_connect_a1s_idempotency_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_memberships rtm_connect_a1s_memberships_granted_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_memberships
    ADD CONSTRAINT rtm_connect_a1s_memberships_granted_by_operator_id_fkey FOREIGN KEY (granted_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_memberships rtm_connect_a1s_memberships_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_memberships
    ADD CONSTRAINT rtm_connect_a1s_memberships_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_memberships rtm_connect_a1s_memberships_revoked_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_memberships
    ADD CONSTRAINT rtm_connect_a1s_memberships_revoked_by_operator_id_fkey FOREIGN KEY (revoked_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_memberships rtm_connect_a1s_memberships_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_memberships
    ADD CONSTRAINT rtm_connect_a1s_memberships_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_representation_evidence rtm_connect_a1s_representation_evid_revoked_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_representation_evidence
    ADD CONSTRAINT rtm_connect_a1s_representation_evid_revoked_by_operator_id_fkey FOREIGN KEY (revoked_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_representation_evidence rtm_connect_a1s_representation_evidence_case_binding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_representation_evidence
    ADD CONSTRAINT rtm_connect_a1s_representation_evidence_case_binding_id_fkey FOREIGN KEY (case_binding_id) REFERENCES public.rtm_connect_a1s_case_bindings(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_a1s_representation_evidence rtm_connect_a1s_representation_evidence_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_a1s_representation_evidence
    ADD CONSTRAINT rtm_connect_a1s_representation_evidence_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.rtm_connect_a1s_tenants(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_actions rtm_connect_actions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_actions
    ADD CONSTRAINT rtm_connect_actions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_actions rtm_connect_actions_current_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_actions
    ADD CONSTRAINT rtm_connect_actions_current_connector_id_fkey FOREIGN KEY (current_connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_actions rtm_connect_actions_requested_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_actions
    ADD CONSTRAINT rtm_connect_actions_requested_by_operator_id_fkey FOREIGN KEY (requested_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_events rtm_connect_assisted_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_events
    ADD CONSTRAINT rtm_connect_assisted_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_events rtm_connect_assisted_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_events
    ADD CONSTRAINT rtm_connect_assisted_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_events rtm_connect_assisted_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_events
    ADD CONSTRAINT rtm_connect_assisted_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_assisted_events rtm_connect_assisted_events_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_events
    ADD CONSTRAINT rtm_connect_assisted_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_assisted_tasks(id) ON DELETE CASCADE;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_assigned_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_assigned_by_operator_id_fkey FOREIGN KEY (assigned_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_assignee_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_assignee_operator_id_fkey FOREIGN KEY (assignee_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_authorization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_authorization_id_fkey FOREIGN KEY (authorization_id) REFERENCES public.rtm_connect_authorizations(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_receipt_evidence_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_receipt_evidence_id_fkey FOREIGN KEY (receipt_evidence_id) REFERENCES public.rtm_connect_evidence(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_release_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_release_operator_id_fkey FOREIGN KEY (release_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_verified_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_verified_by_operator_id_fkey FOREIGN KEY (verified_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_assisted_tasks rtm_connect_assisted_tasks_verified_evidence_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_assisted_tasks
    ADD CONSTRAINT rtm_connect_assisted_tasks_verified_evidence_id_fkey FOREIGN KEY (verified_evidence_id) REFERENCES public.rtm_connect_evidence(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_attempts rtm_connect_attempts_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_attempts
    ADD CONSTRAINT rtm_connect_attempts_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_attempts rtm_connect_attempts_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_attempts
    ADD CONSTRAINT rtm_connect_attempts_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_authorizations rtm_connect_authorizations_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_authorizations
    ADD CONSTRAINT rtm_connect_authorizations_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_authorizations rtm_connect_authorizations_supersedes_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_authorizations
    ADD CONSTRAINT rtm_connect_authorizations_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES public.rtm_connect_authorizations(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_authorization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_authorization_id_fkey FOREIGN KEY (authorization_id) REFERENCES public.rtm_connect_authorizations(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_outbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_outbox_id_fkey FOREIGN KEY (outbox_id) REFERENCES public.rtm_connect_dispatch_outbox(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_events rtm_connect_dispatch_events_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_events
    ADD CONSTRAINT rtm_connect_dispatch_events_release_id_fkey FOREIGN KEY (release_id) REFERENCES public.rtm_connect_production_releases(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_outbox rtm_connect_dispatch_outbox_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_outbox
    ADD CONSTRAINT rtm_connect_dispatch_outbox_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_outbox rtm_connect_dispatch_outbox_authorization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_outbox
    ADD CONSTRAINT rtm_connect_dispatch_outbox_authorization_id_fkey FOREIGN KEY (authorization_id) REFERENCES public.rtm_connect_authorizations(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_dispatch_outbox rtm_connect_dispatch_outbox_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_dispatch_outbox
    ADD CONSTRAINT rtm_connect_dispatch_outbox_release_id_fkey FOREIGN KEY (release_id) REFERENCES public.rtm_connect_production_releases(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_evidence rtm_connect_evidence_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_evidence
    ADD CONSTRAINT rtm_connect_evidence_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_evidence rtm_connect_evidence_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_evidence
    ADD CONSTRAINT rtm_connect_evidence_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_evidence rtm_connect_evidence_verified_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_evidence
    ADD CONSTRAINT rtm_connect_evidence_verified_by_operator_id_fkey FOREIGN KEY (verified_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_idempotency_claims rtm_connect_idempotency_claims_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_idempotency_claims
    ADD CONSTRAINT rtm_connect_idempotency_claims_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_events rtm_connect_manual_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_events
    ADD CONSTRAINT rtm_connect_manual_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_events rtm_connect_manual_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_events
    ADD CONSTRAINT rtm_connect_manual_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_manual_events rtm_connect_manual_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_events
    ADD CONSTRAINT rtm_connect_manual_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_manual_events rtm_connect_manual_events_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_events
    ADD CONSTRAINT rtm_connect_manual_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.rtm_connect_manual_tasks(id) ON DELETE CASCADE;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_assigned_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_assigned_by_operator_id_fkey FOREIGN KEY (assigned_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_assignee_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_assignee_operator_id_fkey FOREIGN KEY (assignee_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_manual_tasks rtm_connect_manual_tasks_verified_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_manual_tasks
    ADD CONSTRAINT rtm_connect_manual_tasks_verified_by_operator_id_fkey FOREIGN KEY (verified_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_releases rtm_connect_production_releas_operations_approved_by_opera_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releas_operations_approved_by_opera_fkey FOREIGN KEY (operations_approved_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_releases rtm_connect_production_releas_security_approved_by_operato_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releas_security_approved_by_operato_fkey FOREIGN KEY (security_approved_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_release_events rtm_connect_production_release_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_release_events
    ADD CONSTRAINT rtm_connect_production_release_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_release_events rtm_connect_production_release_events_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_release_events
    ADD CONSTRAINT rtm_connect_production_release_events_release_id_fkey FOREIGN KEY (release_id) REFERENCES public.rtm_connect_production_releases(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_releases rtm_connect_production_releases_halted_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releases_halted_by_operator_id_fkey FOREIGN KEY (halted_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_releases rtm_connect_production_releases_rejected_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releases_rejected_by_operator_id_fkey FOREIGN KEY (rejected_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_production_releases rtm_connect_production_releases_requested_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_production_releases
    ADD CONSTRAINT rtm_connect_production_releases_requested_by_operator_id_fkey FOREIGN KEY (requested_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_evidence_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES public.rtm_connect_evidence(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_reconciliation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_reconciliation_id_fkey FOREIGN KEY (reconciliation_id) REFERENCES public.rtm_connect_reconciliations(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliation_events rtm_connect_reconciliation_events_webhook_inbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliation_events
    ADD CONSTRAINT rtm_connect_reconciliation_events_webhook_inbox_id_fkey FOREIGN KEY (webhook_inbox_id) REFERENCES public.rtm_connect_webhook_inbox(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_evidence_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES public.rtm_connect_evidence(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_resolved_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_resolved_by_operator_id_fkey FOREIGN KEY (resolved_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_reconciliations rtm_connect_reconciliations_webhook_inbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_reconciliations
    ADD CONSTRAINT rtm_connect_reconciliations_webhook_inbox_id_fkey FOREIGN KEY (webhook_inbox_id) REFERENCES public.rtm_connect_webhook_inbox(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_transitions rtm_connect_transitions_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_transitions
    ADD CONSTRAINT rtm_connect_transitions_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_transitions rtm_connect_transitions_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_transitions
    ADD CONSTRAINT rtm_connect_transitions_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_transitions rtm_connect_transitions_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_transitions
    ADD CONSTRAINT rtm_connect_transitions_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_webhook_events rtm_connect_webhook_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_events
    ADD CONSTRAINT rtm_connect_webhook_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_webhook_events rtm_connect_webhook_events_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_events
    ADD CONSTRAINT rtm_connect_webhook_events_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_webhook_events rtm_connect_webhook_events_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_events
    ADD CONSTRAINT rtm_connect_webhook_events_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_connect_webhook_events rtm_connect_webhook_events_webhook_inbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_events
    ADD CONSTRAINT rtm_connect_webhook_events_webhook_inbox_id_fkey FOREIGN KEY (webhook_inbox_id) REFERENCES public.rtm_connect_webhook_inbox(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_webhook_inbox rtm_connect_webhook_inbox_ingress_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_inbox
    ADD CONSTRAINT rtm_connect_webhook_inbox_ingress_connector_id_fkey FOREIGN KEY (ingress_connector_id) REFERENCES public.rtm_connect_connectors(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_webhook_inbox rtm_connect_webhook_inbox_matched_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_inbox
    ADD CONSTRAINT rtm_connect_webhook_inbox_matched_action_id_fkey FOREIGN KEY (matched_action_id) REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT;


--
-- Name: rtm_connect_webhook_inbox rtm_connect_webhook_inbox_matched_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_connect_webhook_inbox
    ADD CONSTRAINT rtm_connect_webhook_inbox_matched_attempt_id_fkey FOREIGN KEY (matched_attempt_id) REFERENCES public.rtm_connect_attempts(id) ON DELETE RESTRICT;


--
-- Name: rtm_deadlines rtm_deadlines_attention_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_attention_item_id_fkey FOREIGN KEY (attention_item_id) REFERENCES public.rtm_attention_items(id) ON DELETE CASCADE;


--
-- Name: rtm_deadlines rtm_deadlines_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_deadlines rtm_deadlines_source_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_source_document_id_fkey FOREIGN KEY (source_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: rtm_deadlines rtm_deadlines_source_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_source_event_id_fkey FOREIGN KEY (source_event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: rtm_deadlines rtm_deadlines_supersedes_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES public.rtm_deadlines(id) ON DELETE SET NULL;


--
-- Name: rtm_deadlines rtm_deadlines_validated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_deadlines
    ADD CONSTRAINT rtm_deadlines_validated_by_fkey FOREIGN KEY (validated_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_document_extractions rtm_document_extractions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_document_extractions
    ADD CONSTRAINT rtm_document_extractions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_family_resolutions rtm_family_resolutions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_family_resolutions
    ADD CONSTRAINT rtm_family_resolutions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_generated_resources rtm_generated_resources_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_generated_resources rtm_generated_resources_docx_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_docx_document_id_fkey FOREIGN KEY (docx_document_id) REFERENCES public.documents(id);


--
-- Name: rtm_generated_resources rtm_generated_resources_legal_preview_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_legal_preview_id_fkey FOREIGN KEY (legal_preview_id) REFERENCES public.rtm_legal_previews(id);


--
-- Name: rtm_generated_resources rtm_generated_resources_pdf_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_generated_resources
    ADD CONSTRAINT rtm_generated_resources_pdf_document_id_fkey FOREIGN KEY (pdf_document_id) REFERENCES public.documents(id);


--
-- Name: rtm_legal_previews rtm_legal_previews_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_legal_previews
    ADD CONSTRAINT rtm_legal_previews_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_operator_access_evidence rtm_operator_access_evidence_access_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_access_evidence
    ADD CONSTRAINT rtm_operator_access_evidence_access_event_id_fkey FOREIGN KEY (access_event_id) REFERENCES public.rtm_operator_access_events(id) ON DELETE RESTRICT;


--
-- Name: rtm_operator_devices rtm_operator_devices_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_devices
    ADD CONSTRAINT rtm_operator_devices_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE CASCADE;


--
-- Name: rtm_operator_devices rtm_operator_devices_revoked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_devices
    ADD CONSTRAINT rtm_operator_devices_revoked_by_fkey FOREIGN KEY (revoked_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_operator_devices rtm_operator_devices_trusted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_devices
    ADD CONSTRAINT rtm_operator_devices_trusted_by_fkey FOREIGN KEY (trusted_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_operator_sessions rtm_operator_sessions_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.rtm_operator_devices(id) ON DELETE SET NULL;


--
-- Name: rtm_operator_sessions rtm_operator_sessions_login_access_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_login_access_event_id_fkey FOREIGN KEY (login_access_event_id) REFERENCES public.rtm_operator_access_events(id) ON DELETE SET NULL;


--
-- Name: rtm_operator_sessions rtm_operator_sessions_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE CASCADE;


--
-- Name: rtm_operator_sessions rtm_operator_sessions_revoked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operator_sessions
    ADD CONSTRAINT rtm_operator_sessions_revoked_by_fkey FOREIGN KEY (revoked_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_operators rtm_operators_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operators
    ADD CONSTRAINT rtm_operators_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_operators rtm_operators_disabled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operators
    ADD CONSTRAINT rtm_operators_disabled_by_fkey FOREIGN KEY (disabled_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_operators rtm_operators_primary_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_operators
    ADD CONSTRAINT rtm_operators_primary_role_id_fkey FOREIGN KEY (primary_role_id) REFERENCES public.rtm_operator_roles(id) ON DELETE SET NULL;


--
-- Name: rtm_presenter_admin_exports rtm_presenter_admin_exports_admin_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_admin_exports
    ADD CONSTRAINT rtm_presenter_admin_exports_admin_operator_id_fkey FOREIGN KEY (admin_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_admin_exports rtm_presenter_admin_exports_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_admin_exports
    ADD CONSTRAINT rtm_presenter_admin_exports_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_admin_exports rtm_presenter_admin_exports_export_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_admin_exports
    ADD CONSTRAINT rtm_presenter_admin_exports_export_document_id_fkey FOREIGN KEY (export_document_id) REFERENCES public.documents(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_admin_exports rtm_presenter_admin_exports_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_admin_exports
    ADD CONSTRAINT rtm_presenter_admin_exports_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_actor_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_actor_operator_id_fkey FOREIGN KEY (actor_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_admin_export_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_admin_export_id_fkey FOREIGN KEY (admin_export_id) REFERENCES public.rtm_presenter_admin_exports(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_handoff_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_handoff_ticket_id_fkey FOREIGN KEY (handoff_ticket_id) REFERENCES public.rtm_presenter_handoff_tickets(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_audit_events rtm_presenter_audit_events_package_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_audit_events
    ADD CONSTRAINT rtm_presenter_audit_events_package_item_id_fkey FOREIGN KEY (package_item_id) REFERENCES public.rtm_presenter_package_items(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_destination_profiles rtm_presenter_destination_profiles_created_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_destination_profiles
    ADD CONSTRAINT rtm_presenter_destination_profiles_created_by_operator_id_fkey FOREIGN KEY (created_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_destination_profiles rtm_presenter_destination_profiles_verified_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_destination_profiles
    ADD CONSTRAINT rtm_presenter_destination_profiles_verified_by_operator_id_fkey FOREIGN KEY (verified_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_document_versions rtm_presenter_document_versions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_document_versions
    ADD CONSTRAINT rtm_presenter_document_versions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_document_versions rtm_presenter_document_versions_created_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_document_versions
    ADD CONSTRAINT rtm_presenter_document_versions_created_by_operator_id_fkey FOREIGN KEY (created_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_document_versions rtm_presenter_document_versions_source_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_document_versions
    ADD CONSTRAINT rtm_presenter_document_versions_source_document_id_fkey FOREIGN KEY (source_document_id) REFERENCES public.documents(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_document_versions rtm_presenter_document_versions_supersedes_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_document_versions
    ADD CONSTRAINT rtm_presenter_document_versions_supersedes_version_id_fkey FOREIGN KEY (supersedes_version_id) REFERENCES public.rtm_presenter_document_versions(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_authorization_document_versi_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_authorization_document_versi_fkey FOREIGN KEY (authorization_document_version_id) REFERENCES public.rtm_presenter_document_versions(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_created_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_created_by_operator_id_fkey FOREIGN KEY (created_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_destination_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_destination_profile_id_fkey FOREIGN KEY (destination_profile_id) REFERENCES public.rtm_presenter_destination_profiles(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_frozen_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_frozen_by_operator_id_fkey FOREIGN KEY (frozen_by_operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_filing_packages rtm_presenter_filing_packages_supersedes_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_filing_packages
    ADD CONSTRAINT rtm_presenter_filing_packages_supersedes_package_id_fkey FOREIGN KEY (supersedes_package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_operator_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_operator_session_id_fkey FOREIGN KEY (operator_session_id) REFERENCES public.rtm_operator_sessions(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_handoff_tickets rtm_presenter_handoff_tickets_package_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_handoff_tickets
    ADD CONSTRAINT rtm_presenter_handoff_tickets_package_item_id_fkey FOREIGN KEY (package_item_id) REFERENCES public.rtm_presenter_package_items(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_idempotency_keys rtm_presenter_idempotency_keys_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_idempotency_keys
    ADD CONSTRAINT rtm_presenter_idempotency_keys_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_idempotency_keys rtm_presenter_idempotency_keys_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_idempotency_keys
    ADD CONSTRAINT rtm_presenter_idempotency_keys_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_idempotency_keys rtm_presenter_idempotency_keys_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_idempotency_keys
    ADD CONSTRAINT rtm_presenter_idempotency_keys_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_package_items rtm_presenter_package_items_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_package_items
    ADD CONSTRAINT rtm_presenter_package_items_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_package_items rtm_presenter_package_items_document_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_package_items
    ADD CONSTRAINT rtm_presenter_package_items_document_version_id_fkey FOREIGN KEY (document_version_id) REFERENCES public.rtm_presenter_document_versions(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_package_items rtm_presenter_package_items_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_package_items
    ADD CONSTRAINT rtm_presenter_package_items_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.rtm_presenter_filing_packages(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_signer_installations rtm_presenter_signer_installations_operator_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_signer_installations
    ADD CONSTRAINT rtm_presenter_signer_installations_operator_device_id_fkey FOREIGN KEY (operator_device_id) REFERENCES public.rtm_operator_devices(id) ON DELETE RESTRICT;


--
-- Name: rtm_presenter_signer_installations rtm_presenter_signer_installations_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_presenter_signer_installations
    ADD CONSTRAINT rtm_presenter_signer_installations_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: rtm_validated_facts rtm_validated_facts_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_validated_facts
    ADD CONSTRAINT rtm_validated_facts_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_work_assignments rtm_work_assignments_assigned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_work_assignments
    ADD CONSTRAINT rtm_work_assignments_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.rtm_operators(id) ON DELETE SET NULL;


--
-- Name: rtm_work_assignments rtm_work_assignments_attention_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_work_assignments
    ADD CONSTRAINT rtm_work_assignments_attention_item_id_fkey FOREIGN KEY (attention_item_id) REFERENCES public.rtm_attention_items(id) ON DELETE CASCADE;


--
-- Name: rtm_work_assignments rtm_work_assignments_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_work_assignments
    ADD CONSTRAINT rtm_work_assignments_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- Name: rtm_work_assignments rtm_work_assignments_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rtm_work_assignments
    ADD CONSTRAINT rtm_work_assignments_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.rtm_operators(id) ON DELETE RESTRICT;


--
-- Name: submission_events submission_events_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_events
    ADD CONSTRAINT submission_events_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES public.submissions(id) ON DELETE CASCADE;


--
-- Name: submissions submissions_case_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_case_id_fkey FOREIGN KEY (case_id) REFERENCES public.cases(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict fX7xfHjEAbR6by6vrG3trQD4m0r8zf1ouZhUj4aUAt8nIcHwz5BJgwE6k5nO4mh


DO $rtm_local_verify$
DECLARE
    table_count integer;
    function_count integer;
    trigger_count integer;
BEGIN
    SELECT count(*) INTO table_count FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');
    SELECT count(*) INTO function_count FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public';
    SELECT count(*) INTO trigger_count FROM pg_catalog.pg_trigger t
    JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND NOT t.tgisinternal;
    IF table_count <> 63 OR function_count <> 51 OR trigger_count <> 66 THEN
        RAISE EXCEPTION 'Schema inventory mismatch: tables %, functions %, triggers %',
            table_count, function_count, trigger_count;
    END IF;
END
$rtm_local_verify$;

SELECT 'RTM_ESQUEMA_LOCAL_OK' AS resultado,
       current_database() AS base_de_datos, current_user AS usuario,
       63 AS tablas;
