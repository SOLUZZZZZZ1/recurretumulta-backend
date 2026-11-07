-- Incremental candidate only. No production application is implied.
BEGIN;
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
COMMIT;
