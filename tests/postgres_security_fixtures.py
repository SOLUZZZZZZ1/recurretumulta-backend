"""Real, disposable operator credentials for PostgreSQL integration tests."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from rtm_core.management_schema import management_v1_ddl
from rtm_core.operator_access_schema import operator_access_v1_ddl
from rtm_core.operator_auth_crypto import (
    generate_device_secret,
    generate_session_token,
    hash_device_secret,
)
from rtm_core.operator_auth_repository import create_operator_session
from rtm_core.operator_auth_schema import operator_auth_v1_ddl
from rtm_core.operator_provisioning import provision_synthetic_operator


def apply_operator_security_schema(conn) -> None:
    for migration in (
        management_v1_ddl,
        operator_access_v1_ddl,
        operator_auth_v1_ddl,
    ):
        for _, statement in migration():
            conn.execute(text(statement))


@dataclass(frozen=True)
class IntegrationOperatorSession:
    operator_id: str
    session_id: str
    token: str
    device_secret: str

    @property
    def actor(self) -> str:
        return f"operator:{self.operator_id}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-RTM-Device": self.device_secret,
        }


def seed_supervisor_session(conn) -> IntegrationOperatorSession:
    """Provision a synthetic supervisor and persist device-bound session hashes.

    Request authentication, session revocation, role loading and case scope are
    still performed by production middleware and SQL on every HTTP request.
    """
    operator = provision_synthetic_operator(
        conn,
        email=f"rtm-staging-ci-{uuid.uuid4().hex}@example.com",
        display_name="Supervisión sintética de integración",
        role_key="supervisor",
        password=secrets.token_urlsafe(32),
    )
    device_secret = generate_device_secret()
    device_id = str(
        conn.execute(
            text(
                """
                INSERT INTO rtm_operator_devices(
                    operator_id, device_key_sha256, status, metadata
                ) VALUES (
                    CAST(:operator_id AS UUID), :device_digest, 'known',
                    '{"synthetic": true}'::jsonb
                ) RETURNING id
                """
            ),
            {
                "operator_id": operator.operator_id,
                "device_digest": hash_device_secret(device_secret),
            },
        ).scalar_one()
    )
    token = generate_session_token()
    session_id = create_operator_session(
        conn,
        operator_id=operator.operator_id,
        raw_token=token,
        auth_epoch=1,
        device_id=device_id,
        metadata_json='{"synthetic": true}',
    )
    return IntegrationOperatorSession(
        operator_id=operator.operator_id,
        session_id=session_id,
        token=token,
        device_secret=device_secret,
    )


def operator_http_environment() -> dict[str, str]:
    """Explicit staging auth profile with no external credentials or effects."""
    return {
        "RTM_ENV": "staging",
        "RTM_ALLOWED_HOSTS": "testserver",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY": secrets.token_urlsafe(48),
        "RTM_TRUST_PROXY_HEADERS": "0",
        "OPERATOR_TOKEN": secrets.token_urlsafe(48),
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
    }
