-- Explicit candidate migration. No startup DDL or modifications to payment records.
BEGIN;
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
