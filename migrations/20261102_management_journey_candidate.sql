-- CANDIDATE ONLY: no startup DDL. Requires the paid-study schema and separately
-- reviewed case-mail migration. No existing case, payment, document or account update.
BEGIN;
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
COMMIT;
