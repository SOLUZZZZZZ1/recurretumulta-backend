-- CANDIDATE ONLY. Do not run at app startup, pre-deploy or automatically.
-- Seven additive tables; no existing case/document/payment updates or accounts.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = public;
DO $$ BEGIN
IF to_regclass('public.rtm_operator_roles') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_operators') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_operator_sessions') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_operator_devices') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_operator_access_events') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_operator_access_evidence') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
IF to_regclass('public.rtm_study_assessments') IS NOT NULL THEN RAISE EXCEPTION 'Casework schema already exists; inspect before applying'; END IF;
END $$;

-- October reusable block: operator_roles
CREATE TABLE IF NOT EXISTS rtm_operator_roles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                permissions JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(permissions) = 'array'),
                system_role BOOLEAN NOT NULL DEFAULT FALSE,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_role_code
                    CHECK (code ~ '^[a-z][a-z0-9_.-]{2,63}$')
            );

-- October reusable block: operators
CREATE TABLE IF NOT EXISTS rtm_operators (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT,
                status TEXT NOT NULL DEFAULT 'invited'
                    CHECK (status IN ('invited', 'active', 'suspended', 'disabled')),
                primary_role_id UUID REFERENCES rtm_operator_roles(id)
                    ON DELETE SET NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
                mfa_required BOOLEAN NOT NULL DEFAULT FALSE,
                profile JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(profile) = 'object'),
                last_login_at TIMESTAMPTZ,
                created_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                disabled_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                disabled_at TIMESTAMPTZ,
                CONSTRAINT ck_rtm_operator_disabled_state CHECK (
                    (status = 'disabled' AND disabled_at IS NOT NULL)
                    OR (status <> 'disabled')
                )
            );

-- October reusable block: operator_sessions
CREATE TABLE IF NOT EXISTS rtm_operator_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE CASCADE,
                token_sha256 TEXT NOT NULL UNIQUE
                    CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed', 'revoked', 'expired')),
                login_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                logout_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                revoked_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                close_reason TEXT,
                ip_address TEXT,
                user_agent TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_session_expiry
                    CHECK (expires_at > login_at),
                CONSTRAINT ck_rtm_operator_session_closed CHECK (
                    (status = 'active' AND logout_at IS NULL AND revoked_at IS NULL)
                    OR status <> 'active'
                )
            );

-- October reusable block: uq_operator_email
CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_email
            ON rtm_operators(lower(btrim(email)));

-- October reusable block: uq_operator_role_code
CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_role_code
            ON rtm_operator_roles(code);

-- October reusable block: idx_operator_status
CREATE INDEX IF NOT EXISTS idx_rtm_operator_status
            ON rtm_operators(status, updated_at DESC);

-- October reusable block: idx_operator_sessions_active
CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_active
            ON rtm_operator_sessions(operator_id, status, expires_at);

-- October reusable block: operator_auth_columns
ALTER TABLE rtm_operators
                ADD COLUMN IF NOT EXISTS failed_login_count INTEGER
                    NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS last_failed_login_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS password_algorithm TEXT
                    NOT NULL DEFAULT 'argon2id',
                ADD COLUMN IF NOT EXISTS password_version INTEGER
                    NOT NULL DEFAULT 1,
                ADD COLUMN IF NOT EXISTS auth_epoch INTEGER
                    NOT NULL DEFAULT 1;

-- October reusable block: operator_session_auth_columns
ALTER TABLE rtm_operator_sessions
                ADD COLUMN IF NOT EXISTS auth_epoch INTEGER
                    NOT NULL DEFAULT 1,
                ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS absolute_expires_at TIMESTAMPTZ;

-- October reusable block: operator_auth_constraints
DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_failed_login_count'
                ) THEN
                    ALTER TABLE rtm_operators
                    ADD CONSTRAINT ck_rtm_operator_failed_login_count
                    CHECK (failed_login_count >= 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_password_version'
                ) THEN
                    ALTER TABLE rtm_operators
                    ADD CONSTRAINT ck_rtm_operator_password_version
                    CHECK (password_version > 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_auth_epoch'
                ) THEN
                    ALTER TABLE rtm_operators
                    ADD CONSTRAINT ck_rtm_operator_auth_epoch
                    CHECK (auth_epoch > 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_password_algorithm'
                ) THEN
                    ALTER TABLE rtm_operators
                    ADD CONSTRAINT ck_rtm_operator_password_algorithm
                    CHECK (password_algorithm IN ('argon2id'));
                END IF;
            END $$;

-- October reusable block: operator_session_auth_constraints
DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_session_auth_epoch'
                ) THEN
                    ALTER TABLE rtm_operator_sessions
                    ADD CONSTRAINT ck_rtm_operator_session_auth_epoch
                    CHECK (auth_epoch > 0);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_session_absolute_expiry'
                ) THEN
                    ALTER TABLE rtm_operator_sessions
                    ADD CONSTRAINT ck_rtm_operator_session_absolute_expiry
                    CHECK (
                        absolute_expires_at IS NULL
                        OR absolute_expires_at >= expires_at
                    );
                END IF;
            END $$;

-- October reusable block: idx_operator_auth_lockout
CREATE INDEX IF NOT EXISTS idx_rtm_operator_auth_lockout
            ON rtm_operators(status, locked_until, failed_login_count);

-- October reusable block: idx_operator_sessions_epoch
CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_epoch
            ON rtm_operator_sessions(
                operator_id, auth_epoch, status, expires_at
            );

-- October reusable block: idx_operator_sessions_absolute_expiry
CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_absolute_expiry
            ON rtm_operator_sessions(status, absolute_expires_at);

-- October reusable block: extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- October reusable block: operator_devices
CREATE TABLE IF NOT EXISTS rtm_operator_devices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE CASCADE,
                device_key_sha256 TEXT NOT NULL
                    CHECK (device_key_sha256 ~ '^[0-9a-f]{64}$'),
                status TEXT NOT NULL DEFAULT 'known' CHECK (
                    status IN ('known', 'trusted', 'revoked')
                ),
                display_name TEXT,
                device_type TEXT NOT NULL DEFAULT 'unknown' CHECK (
                    device_type IN (
                        'desktop', 'mobile', 'tablet', 'bot', 'other', 'unknown'
                    )
                ),
                os_family TEXT,
                os_version TEXT,
                browser_family TEXT,
                browser_version TEXT,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                first_ip_hash_sha256 TEXT CHECK (
                    first_ip_hash_sha256 IS NULL
                    OR first_ip_hash_sha256 ~ '^[0-9a-f]{64}$'
                ),
                last_ip_hash_sha256 TEXT CHECK (
                    last_ip_hash_sha256 IS NULL
                    OR last_ip_hash_sha256 ~ '^[0-9a-f]{64}$'
                ),
                trusted_at TIMESTAMPTZ,
                trusted_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                revoked_at TIMESTAMPTZ,
                revoked_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                revocation_reason TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_device_seen_order
                    CHECK (last_seen_at >= first_seen_at),
                CONSTRAINT ck_rtm_operator_device_status CHECK (
                    (status = 'trusted' AND trusted_at IS NOT NULL)
                    OR (status = 'revoked' AND revoked_at IS NOT NULL)
                    OR status = 'known'
                )
            );

-- October reusable block: operator_access_events
CREATE TABLE IF NOT EXISTS rtm_operator_access_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                -- Identificadores históricos sin FK: el evento debe conservar
                -- quién y qué sesión figuraban aunque posteriormente se depuren
                -- sesiones sintéticas o se desactive un operador.
                operator_id UUID,
                session_id UUID,
                device_id UUID,

                event_type TEXT NOT NULL
                    CHECK (event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'),
                result TEXT NOT NULL DEFAULT 'success' CHECK (
                    result IN ('success', 'failure', 'denied', 'noop')
                ),
                auth_method TEXT,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                -- Correlación no reversible para intentos sin operador resuelto.
                login_identifier_sha256 TEXT CHECK (
                    login_identifier_sha256 IS NULL
                    OR login_identifier_sha256 ~ '^[0-9a-f]{64}$'
                ),

                -- El historial normalizado no conserva la IP completa.
                ip_masked TEXT,
                ip_hash_sha256 TEXT CHECK (
                    ip_hash_sha256 IS NULL
                    OR ip_hash_sha256 ~ '^[0-9a-f]{64}$'
                ),
                ip_family SMALLINT CHECK (ip_family IS NULL OR ip_family IN (4, 6)),
                ip_source TEXT NOT NULL DEFAULT 'unknown' CHECK (
                    ip_source IN (
                        'x_vercel_forwarded_for', 'x_forwarded_for',
                        'x_real_ip', 'render_proxy', 'direct', 'unknown'
                    )
                ),
                ip_trusted BOOLEAN NOT NULL DEFAULT FALSE,

                -- Identificador opaco RTM: no es fingerprint del hardware.
                device_key_sha256 TEXT CHECK (
                    device_key_sha256 IS NULL
                    OR device_key_sha256 ~ '^[0-9a-f]{64}$'
                ),
                device_type TEXT NOT NULL DEFAULT 'unknown' CHECK (
                    device_type IN (
                        'desktop', 'mobile', 'tablet', 'bot', 'other', 'unknown'
                    )
                ),
                os_family TEXT,
                os_version TEXT,
                browser_family TEXT,
                browser_version TEXT,

                -- Ubicación aproximada derivada de cabeceras confiables o IP.
                country_code TEXT CHECK (
                    country_code IS NULL OR country_code ~ '^[A-Z]{2}$'
                ),
                region TEXT,
                city TEXT,
                timezone TEXT,
                location_source TEXT,

                request_id TEXT,
                reason_code TEXT,
                reason_detail TEXT,
                risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(risk_flags) = 'array'),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

-- October reusable block: operator_access_evidence
CREATE TABLE IF NOT EXISTS rtm_operator_access_evidence (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                access_event_id UUID NOT NULL UNIQUE
                    REFERENCES rtm_operator_access_events(id) ON DELETE RESTRICT,

                -- Evidencia sensible temporal. El historial permanente usa
                -- ip_masked e ip_hash_sha256, no este valor completo.
                ip_address INET,
                raw_user_agent TEXT,
                trusted_headers JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(trusted_headers) = 'object'),

                retention_until TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_access_evidence_retention
                    CHECK (retention_until > created_at)
            );

-- October reusable block: operator_session_access_columns
ALTER TABLE rtm_operator_sessions
                ADD COLUMN IF NOT EXISTS device_id UUID
                    REFERENCES rtm_operator_devices(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS login_access_event_id UUID
                    REFERENCES rtm_operator_access_events(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS ip_source TEXT,
                ADD COLUMN IF NOT EXISTS ip_trusted BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS country_code TEXT,
                ADD COLUMN IF NOT EXISTS region TEXT,
                ADD COLUMN IF NOT EXISTS city TEXT,
                ADD COLUMN IF NOT EXISTS timezone TEXT,
                ADD COLUMN IF NOT EXISTS risk_flags JSONB
                    NOT NULL DEFAULT '[]'::jsonb;

-- October reusable block: operator_session_risk_flags_constraint
DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'ck_rtm_operator_session_risk_flags'
                ) THEN
                    ALTER TABLE rtm_operator_sessions
                    ADD CONSTRAINT ck_rtm_operator_session_risk_flags
                    CHECK (jsonb_typeof(risk_flags) = 'array');
                END IF;
            END $$;

-- October reusable block: uq_operator_device_key
CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_device_key
            ON rtm_operator_devices(operator_id, device_key_sha256);

-- October reusable block: idx_operator_devices_status
CREATE INDEX IF NOT EXISTS idx_rtm_operator_devices_status
            ON rtm_operator_devices(operator_id, status, last_seen_at DESC);

-- October reusable block: idx_operator_access_operator_time
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_operator_time
            ON rtm_operator_access_events(operator_id, occurred_at DESC);

-- October reusable block: idx_operator_access_session_time
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_session_time
            ON rtm_operator_access_events(session_id, occurred_at DESC);

-- October reusable block: idx_operator_access_device_time
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_device_time
            ON rtm_operator_access_events(device_id, occurred_at DESC);

-- October reusable block: idx_operator_access_ip_hash_time
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_ip_hash_time
            ON rtm_operator_access_events(ip_hash_sha256, occurred_at DESC);

-- October reusable block: idx_operator_access_result_time
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_result_time
            ON rtm_operator_access_events(result, event_type, occurred_at DESC);

-- October reusable block: idx_operator_access_login_identifier
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_login_identifier
            ON rtm_operator_access_events(
                login_identifier_sha256,
                occurred_at DESC
            );

-- October reusable block: idx_operator_access_evidence_retention
CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_evidence_retention
            ON rtm_operator_access_evidence(retention_until);

-- October reusable block: idx_operator_sessions_device_active
CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_device_active
            ON rtm_operator_sessions(device_id, status, last_seen_at DESC);

-- October reusable block: operator_access_events_append_only_function
CREATE OR REPLACE FUNCTION
                rtm_guard_operator_access_events_append_only()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_operator_access_events is append-only; % is not permitted',
                    TG_OP
                    USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;

-- October reusable block: operator_access_events_append_only_trigger
DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname = 'trg_rtm_operator_access_events_append_only'
                      AND tgrelid = 'rtm_operator_access_events'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_operator_access_events_append_only
                    BEFORE UPDATE OR DELETE ON rtm_operator_access_events
                    FOR EACH ROW
                    EXECUTE FUNCTION
                        rtm_guard_operator_access_events_append_only();
                END IF;
            END $$;

-- October reusable block: operator_access_evidence_retention_function
CREATE OR REPLACE FUNCTION
                rtm_guard_operator_access_evidence_retention()
            RETURNS TRIGGER AS $$
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
            $$ LANGUAGE plpgsql;

-- October reusable block: operator_access_evidence_retention_trigger
DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_operator_access_evidence_retention'
                      AND tgrelid = 'rtm_operator_access_evidence'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_operator_access_evidence_retention
                    BEFORE UPDATE OR DELETE ON rtm_operator_access_evidence
                    FOR EACH ROW
                    EXECUTE FUNCTION
                        rtm_guard_operator_access_evidence_retention();
                END IF;
            END $$;

CREATE TABLE rtm_study_assessments (
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    author_id UUID NOT NULL REFERENCES rtm_operators(id) ON DELETE RESTRICT,
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    snapshot_sha256 TEXT NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(case_id, operation_id),
    UNIQUE(case_id, revision)
);
-- No fabricated entries in historical migration ledgers.
CREATE UNIQUE INDEX uq_rtm_device_logout_operation
    ON rtm_operator_access_events(device_key_sha256, request_id)
    WHERE event_type='auth.device_logout';
COMMIT;
