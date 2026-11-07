-- REVIEW CANDIDATE: not executed on production. No startup DDL.
-- Requires a reviewed production connection with this explicit application_name.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='30s';
SET LOCAL search_path=public,pg_catalog;
DO $rtm_guard$
BEGIN
 IF current_database() <> 'recurretumulta'
 OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
 OR current_setting('application_name') <> 'rtm-production-journey-reviewed'
 THEN RAISE EXCEPTION 'RTM_PRODUCTION_TARGET_REQUIRED'; END IF;
 IF NOT pg_try_advisory_xact_lock(20261107,1)
 THEN RAISE EXCEPTION 'RTM_PRODUCTION_MIGRATION_BUSY'; END IF;
 IF to_regclass('public.cases') IS NULL OR to_regclass('public.rtm_operators') IS NULL
 OR to_regclass('public.rtm_study_assessments') IS NULL
 THEN RAISE EXCEPTION 'RTM_PRODUCTION_PREREQUISITES_MISSING'; END IF;
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN ('rtm_case_mail_participants','rtm_case_messages','rtm_case_mail_originals','rtm_case_mail_jobs','rtm_case_mail_events','rtm_case_mail_sync','rtm_case_mail_attachment_checks','rtm_case_mail_ingest_issues','rtm_management_receipts','rtm_management_quotes','rtm_management_acceptances','rtm_management_obligations','rtm_management_credit_applications','rtm_management_operations','rtm_management_events','rtm_management_webhooks','rtm_client_links','rtm_client_sessions','rtm_client_throttles','rtm_management_draft_versions','rtm_management_generation_jobs'))
 OR to_regprocedure('public.rtm_management_immutable()') IS NOT NULL
 OR to_regprocedure('public.rtm_management_quote_immutable()') IS NOT NULL
 THEN RAISE EXCEPTION 'RTM_PRODUCTION_SCHEMA_ALREADY_PRESENT_REVIEW_REQUIRED'; END IF;
END;
$rtm_guard$;
-- Source: 20261025_case_mail_candidate.sql
-- CANDIDATE ONLY. Separate from document/study rollout. No automatic application.

SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
SET LOCAL search_path=public;
CREATE TABLE rtm_case_mail_participants (
  id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
  email TEXT NOT NULL, verified_at TIMESTAMPTZ NOT NULL,
  verified_by UUID NOT NULL REFERENCES rtm_operators(id) ON DELETE RESTRICT,
  verification_reference TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE(case_id,email)
);
CREATE TABLE rtm_case_messages (
  id UUID PRIMARY KEY, case_id UUID REFERENCES cases(id) ON DELETE RESTRICT,
  mailbox TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing','outgoing_external')),
  original_id TEXT NOT NULL, operation_id UUID, request_sha256 TEXT,
  state TEXT NOT NULL CHECK(state IN ('prepared','queued','sending','provider_accepted','delivered','bounced','failed','unknown','received','pending_assignment')),
  payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
  relation JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(relation)='object'),
  author_id UUID REFERENCES rtm_operators(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(mailbox,original_id), UNIQUE(case_id,operation_id)
);
CREATE INDEX idx_case_messages_timeline ON rtm_case_messages(case_id,created_at,id);
CREATE TABLE rtm_case_mail_originals (
  id UUID PRIMARY KEY, message_id UUID NOT NULL REFERENCES rtm_case_messages(id) ON DELETE RESTRICT,
  sha256 TEXT NOT NULL, custody_key TEXT NOT NULL, source_key TEXT NOT NULL UNIQUE,
  size_bytes INTEGER NOT NULL CHECK(size_bytes>0), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_case_mail_jobs (
  message_id UUID PRIMARY KEY REFERENCES rtm_case_messages(id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN ('queued','sending','closed','unknown')),
  lease_token UUID, lease_until TIMESTAMPTZ, started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
CREATE TABLE rtm_case_mail_events (
  id UUID PRIMARY KEY, message_id UUID NOT NULL REFERENCES rtm_case_messages(id) ON DELETE RESTRICT,
  state TEXT NOT NULL, actor_id UUID REFERENCES rtm_operators(id) ON DELETE RESTRICT,
  evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_case_mail_sync (
  mailbox TEXT NOT NULL, folder TEXT NOT NULL, uidvalidity BIGINT NOT NULL,
  last_uid BIGINT NOT NULL CHECK(last_uid>=0), last_attempt_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ, error_code TEXT,
  PRIMARY KEY(mailbox,folder)
);
CREATE TABLE rtm_case_mail_attachment_checks (
  message_id UUID NOT NULL REFERENCES rtm_case_messages(id) ON DELETE RESTRICT,
  part INTEGER NOT NULL CHECK(part>=0), sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('clean','rejected','unavailable')),
  scanner TEXT NOT NULL, scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(message_id,part,sha256)
);
-- No participant inferred from case contact data. No accounts, mail or DNS changes.

-- Source: 20261102_mail_ingestion_recovery_candidate.sql
-- Incremental candidate only. No production application is implied.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
CREATE TABLE rtm_case_mail_ingest_issues (
    mailbox TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity BIGINT NOT NULL CHECK (uidvalidity > 0),
    uid BIGINT NOT NULL CHECK (uid > 0),
    code TEXT NOT NULL CHECK (length(code) BETWEEN 1 AND 100),
    disposition TEXT NOT NULL CHECK (disposition IN ('remote_retained','quarantined')),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    raw_sha256 TEXT CHECK (raw_sha256 ~ '^[a-f0-9]{64}$'),
    custody_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox,folder,uidvalidity,uid),
    CHECK ((disposition='remote_retained' AND raw_sha256 IS NULL AND custody_key IS NULL)
        OR (disposition='quarantined' AND raw_sha256 IS NOT NULL AND custody_key IS NOT NULL))
);

-- Source: 20261102_management_journey_candidate.sql
-- CANDIDATE ONLY: no startup DDL. Requires the paid-study schema and separately
-- reviewed case-mail migration. No existing case, payment, document or account update.

SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
SET LOCAL search_path=public;

CREATE TABLE rtm_management_receipts (
 id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
 mode TEXT NOT NULL CHECK(mode IN ('test','live')), account_id TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('initial_study','management_balance')),
 charge_id TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 proof JSONB NOT NULL, proof_sha256 CHAR(64) NOT NULL,
 verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(mode,account_id,charge_id), UNIQUE(case_id,mode,stage)
);
CREATE TABLE rtm_management_quotes (
 id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
 revision INTEGER NOT NULL CHECK(revision>0), previous_id UUID REFERENCES rtm_management_quotes(id),
 study_operation_id UUID NOT NULL, author_id UUID NOT NULL REFERENCES rtm_operators(id),
 snapshot JSONB NOT NULL, snapshot_sha256 CHAR(64) NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','communicated','accepted','retiring','superseded','expired','settled')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(case_id,revision), FOREIGN KEY(case_id,study_operation_id)
 REFERENCES rtm_study_assessments(case_id,operation_id) ON DELETE RESTRICT
);
CREATE TABLE rtm_management_acceptances (
 quote_id UUID PRIMARY KEY REFERENCES rtm_management_quotes(id) ON DELETE RESTRICT,
 case_id UUID NOT NULL REFERENCES cases(id), operation_id UUID NOT NULL UNIQUE,
 recipient_hash CHAR(64) NOT NULL, snapshot JSONB NOT NULL, snapshot_sha256 CHAR(64) NOT NULL,
 accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_management_obligations (
 id UUID PRIMARY KEY, quote_id UUID NOT NULL UNIQUE REFERENCES rtm_management_quotes(id),
 case_id UUID NOT NULL REFERENCES cases(id), mode TEXT NOT NULL CHECK(mode IN ('test','live')),
 account_id TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 material_sha256 CHAR(64) NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('reserved','open','unknown','failed','retiring','expired','confirmed','review_required')),
 stripe_session_id TEXT UNIQUE, reservation JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_management_credit_applications (
 receipt_id UUID PRIMARY KEY REFERENCES rtm_management_receipts(id),
 quote_id UUID NOT NULL UNIQUE REFERENCES rtm_management_quotes(id),
 obligation_id UUID NOT NULL UNIQUE REFERENCES rtm_management_obligations(id),
 applied_cents INTEGER NOT NULL CHECK(applied_cents=1000), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_management_operations (
 case_id UUID NOT NULL REFERENCES cases(id), operation_id UUID NOT NULL,
 kind TEXT NOT NULL, actor TEXT NOT NULL, request_sha256 CHAR(64) NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','committed','cancelled','unknown')),
 result JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(case_id,operation_id)
);
CREATE TABLE rtm_management_events (
 id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id),
 event_type TEXT NOT NULL, actor TEXT NOT NULL, reference_id TEXT NOT NULL,
 evidence JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE rtm_management_webhooks (
 event_id TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('test','live')), account_id TEXT NOT NULL,
 obligation_id UUID NOT NULL REFERENCES rtm_management_obligations(id),
 processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(event_id,mode,account_id)
);
-- Same hashed, one-use link/session contract as October; no credential material.
CREATE TABLE rtm_client_links (
 verifier CHAR(64) PRIMARY KEY, recipient_hash CHAR(64) NOT NULL, case_ids TEXT NOT NULL,
 issued_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, consumed_at BIGINT,
 CHECK(expires_at>issued_at)
);
CREATE TABLE rtm_client_sessions (
 verifier CHAR(64) PRIMARY KEY, recipient_hash CHAR(64) NOT NULL, case_ids TEXT NOT NULL,
 issued_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, revoked_at BIGINT,
 CHECK(expires_at>issued_at)
);
CREATE TABLE rtm_client_throttles (
 bucket_key CHAR(64) PRIMARY KEY, window_start BIGINT NOT NULL,
 attempts INTEGER NOT NULL CHECK(attempts>0)
);
-- Content and source snapshots are immutable. Corrections append a new version.
CREATE TABLE rtm_management_draft_versions (
 id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id),
 operation_id UUID NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 previous_id UUID REFERENCES rtm_management_draft_versions(id), based_on_id UUID REFERENCES rtm_management_draft_versions(id),
 quote_id UUID NOT NULL REFERENCES rtm_management_quotes(id), author_id UUID NOT NULL REFERENCES rtm_operators(id),
 snapshot JSONB NOT NULL, snapshot_sha256 CHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(case_id,operation_id), UNIQUE(case_id,revision)
);
CREATE INDEX idx_management_quotes_case ON rtm_management_quotes(case_id,revision DESC);
CREATE INDEX idx_management_obligation_case ON rtm_management_obligations(case_id,state);
CREATE INDEX idx_management_events_case ON rtm_management_events(case_id,created_at,id);

CREATE FUNCTION rtm_management_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Immutable management record: append a new operation';
END;
$$;
CREATE TRIGGER trg_management_receipt_immutable BEFORE UPDATE OR DELETE ON rtm_management_receipts
 FOR EACH ROW EXECUTE FUNCTION rtm_management_immutable();
CREATE TRIGGER trg_management_acceptance_immutable BEFORE UPDATE OR DELETE ON rtm_management_acceptances
 FOR EACH ROW EXECUTE FUNCTION rtm_management_immutable();
CREATE TRIGGER trg_management_credit_immutable BEFORE UPDATE OR DELETE ON rtm_management_credit_applications
 FOR EACH ROW EXECUTE FUNCTION rtm_management_immutable();
CREATE TRIGGER trg_management_draft_immutable BEFORE UPDATE OR DELETE ON rtm_management_draft_versions
 FOR EACH ROW EXECUTE FUNCTION rtm_management_immutable();
CREATE TRIGGER trg_management_event_immutable BEFORE UPDATE OR DELETE ON rtm_management_events
 FOR EACH ROW EXECUTE FUNCTION rtm_management_immutable();
CREATE FUNCTION rtm_management_quote_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Quote history cannot be deleted'; END IF;
 IF (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN
  RAISE EXCEPTION 'Quote content cannot be replaced';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER trg_management_quote_immutable BEFORE UPDATE OR DELETE ON rtm_management_quotes
 FOR EACH ROW EXECUTE FUNCTION rtm_management_quote_immutable();

-- Source: 20261104_management_generation_candidate.sql
-- Explicit candidate migration. No startup DDL or modifications to payment records.

SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
SET LOCAL search_path=public;
-- A generated version is authored by the system, never impersonates an operator.
ALTER TABLE rtm_management_draft_versions ALTER COLUMN author_id DROP NOT NULL;
CREATE TABLE rtm_management_generation_jobs (
 id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES cases(id),
 obligation_id UUID NOT NULL UNIQUE REFERENCES rtm_management_obligations(id),
 quote_id UUID NOT NULL UNIQUE REFERENCES rtm_management_quotes(id),
 mode TEXT NOT NULL CHECK(mode IN ('test','live')), account_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','running','retry','blocked','completed')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 next_attempt_at BIGINT NOT NULL DEFAULT 0, lease_token UUID, lease_until BIGINT,
 last_code TEXT, version_id UUID REFERENCES rtm_management_draft_versions(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_management_generation_due ON rtm_management_generation_jobs(state,next_attempt_at);

COMMIT;
