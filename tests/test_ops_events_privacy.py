from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ops


CASE_ID = "22222222-2222-4222-8222-222222222222"


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, payload):
        self.payload = payload
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement), dict(parameters or {})))
        return _Result(
            [
                (
                    "client_authorized",
                    self.payload,
                    datetime(2026, 9, 2, tzinfo=timezone.utc),
                )
            ]
        )


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def _read_events(payload, *, individual_session):
    connection = _Connection(payload)
    scope = SimpleNamespace(individual_session=individual_session)
    scope_gate = Mock()
    with (
        patch.object(ops, "_require_operator", return_value=None),
        patch.object(ops, "load_ops_case_scope", return_value=scope),
        patch.object(ops, "require_case_in_scope", scope_gate),
        patch.object(ops, "get_engine", return_value=_Engine(connection)),
    ):
        response = ops.list_events(
            case_id=CASE_ID,
            request=SimpleNamespace(state=SimpleNamespace()),
            x_operator_token="internal-adapted-token",
            limit=200,
        )
    scope_gate.assert_called_once()
    return response


class OpsEventsPrivacyTest(unittest.TestCase):
    def test_individual_timeline_recursively_removes_pii_evidence_and_secrets(self):
        payload = {
            "safeStatus": "authorized",
            "client-IP": "203.0.113.42",
            "rawUserAgent": "Sensitive Browser/1.0",
            "authorizationIp": "203.0.113.43",
            "identity": {
                "DNI_NIE": "00000000T",
                "domicilio-Notif": "Calle Privada 123",
                "phoneNumber": "+34 600 000 000",
                "contactEmail": "persona@example.invalid",
            },
            "storage": {
                "b2Bucket": "private-case-bucket",
                "object-key": "cases/private/original.pdf",
            },
            "proof": {
                "authorizationEvidence": "raw-consent-evidence",
                "nested-secret": "nested-secret-value",
            },
            "opaqueLocation": "s3://private-case-bucket/private/object",
            "operational": {
                "registro": "REG-SYNTHETIC-1",
                "receipt_sha256": "a" * 64,
                "accepted": True,
            },
        }

        response = _read_events(payload, individual_session=True)
        projected = response["events"][0]["payload"]

        self.assertEqual(projected["safeStatus"], "authorized")
        self.assertEqual(
            projected["operational"],
            {
                "registro": "REG-SYNTHETIC-1",
                "receipt_sha256": "a" * 64,
                "accepted": True,
            },
        )
        self.assertEqual(projected["opaqueLocation"], "<redacted>")

        rendered = json.dumps(response, ensure_ascii=False, default=str)
        for canary in (
            "203.0.113.42",
            "203.0.113.43",
            "Sensitive Browser/1.0",
            "00000000T",
            "Calle Privada 123",
            "+34 600 000 000",
            "persona@example.invalid",
            "private-case-bucket",
            "cases/private/original.pdf",
            "raw-consent-evidence",
            "nested-secret-value",
            "s3://private-case-bucket/private/object",
        ):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, rendered)

    def test_legacy_timeline_keeps_its_previous_projection(self):
        payload = {
            "safeStatus": "authorized",
            "client-IP": "203.0.113.42",
            "rawUserAgent": "Legacy Browser/1.0",
            "DNI_NIE": "00000000T",
            "domicilio-Notif": "Calle Legacy 1",
            "phoneNumber": "+34 600 000 001",
        }

        response = _read_events(payload, individual_session=False)

        self.assertEqual(response["events"][0]["payload"], payload)


if __name__ == "__main__":
    unittest.main()
