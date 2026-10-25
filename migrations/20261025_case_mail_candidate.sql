-- CANDIDATE ONLY. Separate from document/study rollout. No automatic application.
BEGIN;
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
COMMIT;
