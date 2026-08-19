"""Esquema aditivo del núcleo de autenticación individual RTM.

Añade bloqueo de intentos, versión de credenciales y época de autenticación.
No crea operadores reales, no sustituye el PIN OPS y no publica rutas HTTP.
"""

from __future__ import annotations


RTM_OPERATOR_AUTH_SCHEMA_VERSION = "rtm_operator_auth_schema_v1_0"


def operator_auth_v1_ddl() -> list[tuple[str, str]]:
    return [
        (
            "operator_auth_columns",
            """
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
            """,
        ),
        (
            "operator_session_auth_columns",
            """
            ALTER TABLE rtm_operator_sessions
                ADD COLUMN IF NOT EXISTS auth_epoch INTEGER
                    NOT NULL DEFAULT 1,
                ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS absolute_expires_at TIMESTAMPTZ;
            """,
        ),
        (
            "operator_auth_constraints",
            """
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
            """,
        ),
        (
            "operator_session_auth_constraints",
            """
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
            """,
        ),
        (
            "idx_operator_auth_lockout",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_auth_lockout
            ON rtm_operators(status, locked_until, failed_login_count);
            """,
        ),
        (
            "idx_operator_sessions_epoch",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_epoch
            ON rtm_operator_sessions(
                operator_id, auth_epoch, status, expires_at
            );
            """,
        ),
        (
            "idx_operator_sessions_absolute_expiry",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_absolute_expiry
            ON rtm_operator_sessions(status, absolute_expires_at);
            """,
        ),
    ]


__all__ = [
    "RTM_OPERATOR_AUTH_SCHEMA_VERSION",
    "operator_auth_v1_ddl",
]
