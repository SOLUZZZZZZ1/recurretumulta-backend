"""Esquema aditivo de historial de accesos y dispositivos RTM.

Esta unidad amplía RTM Management Core sin sustituir todavía el login OPS.
Separa el historial normalizado e inmutable de la evidencia sensible temporal:
la IP completa y el user-agent bruto viven en una tabla con retención controlada.
No recoge GPS, MAC, IMEI ni números de serie.
"""

from __future__ import annotations


RTM_OPERATOR_ACCESS_SCHEMA_VERSION = "rtm_operator_access_schema_v1_0"


def operator_access_v1_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL aditivo, idempotente y no destructivo."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "operator_devices",
            """
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
            """,
        ),
        (
            "operator_access_events",
            """
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
            """,
        ),
        (
            "operator_access_evidence",
            """
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
            """,
        ),
        (
            "operator_session_access_columns",
            """
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
            """,
        ),
        (
            "operator_session_risk_flags_constraint",
            """
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
            """,
        ),
        (
            "uq_operator_device_key",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_device_key
            ON rtm_operator_devices(operator_id, device_key_sha256);
            """,
        ),
        (
            "idx_operator_devices_status",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_devices_status
            ON rtm_operator_devices(operator_id, status, last_seen_at DESC);
            """,
        ),
        (
            "idx_operator_access_operator_time",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_operator_time
            ON rtm_operator_access_events(operator_id, occurred_at DESC);
            """,
        ),
        (
            "idx_operator_access_session_time",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_session_time
            ON rtm_operator_access_events(session_id, occurred_at DESC);
            """,
        ),
        (
            "idx_operator_access_device_time",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_device_time
            ON rtm_operator_access_events(device_id, occurred_at DESC);
            """,
        ),
        (
            "idx_operator_access_ip_hash_time",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_ip_hash_time
            ON rtm_operator_access_events(ip_hash_sha256, occurred_at DESC);
            """,
        ),
        (
            "idx_operator_access_result_time",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_result_time
            ON rtm_operator_access_events(result, event_type, occurred_at DESC);
            """,
        ),
        (
            "idx_operator_access_login_identifier",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_login_identifier
            ON rtm_operator_access_events(
                login_identifier_sha256,
                occurred_at DESC
            );
            """,
        ),
        (
            "idx_operator_access_evidence_retention",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_access_evidence_retention
            ON rtm_operator_access_evidence(retention_until);
            """,
        ),
        (
            "idx_operator_sessions_device_active",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_device_active
            ON rtm_operator_sessions(device_id, status, last_seen_at DESC);
            """,
        ),
        (
            "operator_access_events_append_only_function",
            """
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
            """,
        ),
        (
            "operator_access_events_append_only_trigger",
            """
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
            """,
        ),
        (
            "operator_access_evidence_retention_function",
            """
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
            """,
        ),
        (
            "operator_access_evidence_retention_trigger",
            """
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
            """,
        ),
    ]


__all__ = [
    "RTM_OPERATOR_ACCESS_SCHEMA_VERSION",
    "operator_access_v1_ddl",
]
