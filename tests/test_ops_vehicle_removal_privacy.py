from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.requests import Request

import ops_vehicle_removal_router as vehicle


CASE_ID = "33333333-3333-4333-8333-333333333333"
OPERATOR_TOKEN = "server-only-operator-token"


def _request(*, individual_session: bool) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/ops/vehicle-removal/{CASE_ID}",
            "raw_path": f"/ops/vehicle-removal/{CASE_ID}".encode("ascii"),
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 443),
            "scheme": "https",
            "http_version": "1.1",
        }
    )
    if individual_session:
        request.state.rtm_operator_context = SimpleNamespace(
            operator_id="11111111-1111-4111-8111-111111111111"
        )
    return request


def _mapping_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield vehicle._response_key(key)
            yield from _mapping_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _mapping_keys(child)


class _Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = list(rows or ())

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, vehicle_payload, event_payload):
        self.vehicle_payload = vehicle_payload
        self.event_payload = event_payload

    def execute(self, statement, parameters=None):
        del parameters
        sql = " ".join(str(statement).split()).casefold()
        if "select id, status, payment_status" in sql:
            now = datetime(2026, 9, 2, tzinfo=timezone.utc)
            return _Result(
                row=(
                    CASE_ID,
                    "vehicle_removal_paid",
                    "paid",
                    "operator-needed@example.invalid",
                    now,
                    now,
                )
            )
        if "and type in" in sql:
            return _Result(rows=[(self.vehicle_payload,)])
        if "select type, payload, created_at" in sql:
            return _Result(
                rows=[
                    (
                        "vehicle_removal_request_created",
                        self.event_payload,
                        datetime(2026, 9, 2, tzinfo=timezone.utc),
                    )
                ]
            )
        raise AssertionError(f"SQL inesperado: {sql}")


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


class VehicleRemovalPayloadSanitizerTest(unittest.TestCase):
    def test_nested_storage_secrets_telemetry_and_identity_are_removed(self):
        payload = {
            "name": "Persona sintética",
            "storage_bucket": "private-bucket",
            "nested": {
                "storageKey": "private/object.pdf",
                "legacy_storage_key": "private/legacy.pdf",
                "receipt_b2_key": "private/receipt.pdf",
                "credential_ref": "vault://secret",
                "portal_session": "opaque-private-session",
                "private_key": "private-key-material",
                "provider_url": "https://provider.invalid/private",
                "client_ip": "203.0.113.42",
                "user-agent": "Sensitive Browser",
                "dni_nie": "00000000T",
                "providerAccessToken": "very-secret-token",
                "safe": [
                    {"city": "Madrid", "passport_number": "SECRET-ID"}
                ],
                "authorization": {
                    "accepted": True,
                    "version": "v1",
                    "text": "Consentimiento sintético",
                    "ip": "203.0.113.43",
                    "user_agent": "Private Browser",
                },
            },
        }

        sanitized = vehicle._sanitize_vehicle_response_payload(payload)

        self.assertEqual(
            sanitized,
            {
                "name": "Persona sintética",
                "nested": {
                    "safe": [{"city": "Madrid"}],
                    "authorization": {
                        "accepted": True,
                        "version": "v1",
                        "text": "Consentimiento sintético",
                    },
                },
            },
        )
        self.assertFalse(
            set(_mapping_keys(sanitized)) & vehicle._PRIVATE_RESPONSE_KEYS
        )

    def test_locator_and_secret_values_are_removed_even_under_innocent_keys(self):
        payload = {
            "first": "s3://private-bucket/cases/document.pdf",
            "second": "https://private-bucket.s3.amazonaws.com/document.pdf",
            "third": "https://files.invalid/document.pdf?X-Amz-Signature=secret",
            "fourth": "Bearer opaque.private.token",
            "fifth": "sk_synthetic_placeholder_never_real",
            "sixth": "eyJheader.payload.signature",
            "safe": "Presentación presencial en Madrid",
            "values": [
                "visible",
                "b2://private-bucket/private-object",
                {"label": "gs://private-bucket/private-object"},
            ],
        }

        self.assertEqual(
            vehicle._sanitize_vehicle_response_payload(payload),
            {
                "safe": "Presentación presencial en Madrid",
                "values": ["visible", {}],
            },
        )

    def test_projection_is_limited_to_individual_staging_session(self):
        payload = {"storage_key": "legacy-coordinate", "safe": "visible"}

        with patch.dict(os.environ, {"RTM_ENV": "production"}, clear=True):
            production = vehicle._project_vehicle_response_payload(
                _request(individual_session=True),
                payload,
            )
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            no_individual_session = vehicle._project_vehicle_response_payload(
                _request(individual_session=False),
                payload,
            )
            individual_session = vehicle._project_vehicle_response_payload(
                _request(individual_session=True),
                payload,
            )

        self.assertEqual(production, payload)
        self.assertEqual(no_individual_session, payload)
        self.assertEqual(individual_session, {"safe": "visible"})


class VehicleRemovalDetailPrivacyTest(unittest.TestCase):
    def test_detail_response_sanitizes_case_payload_and_every_event(self):
        vehicle_payload = {
            "name": "Persona sintética",
            "phone": "+34000000000",
            "case_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "status": "payload-spoofed-status",
            "payment_status": "payload-spoofed-payment",
            "storage_bucket": "private-bucket",
            "nested": {
                "storage_key": "cases/private/document.pdf",
                "dni": "00000000T",
                "safe": "visible",
            },
        }
        event_payload = {
            "status": "received",
            "network": {
                "raw_ip": "203.0.113.42",
                "raw_user_agent": "Sensitive Browser",
            },
            "auth": {
                "api_key": "secret-api-key",
                "session_token": "secret-session-token",
            },
            "person": {
                "nif": "00000000T",
                "city": "Madrid",
            },
        }
        connection = _Connection(vehicle_payload, event_payload)
        scope_gate = Mock()

        with (
            patch.dict(
                os.environ,
                {"RTM_ENV": "staging", "OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(
                vehicle,
                "get_engine",
                return_value=_Engine(connection),
            ),
            patch.object(
                vehicle,
                "load_ops_case_scope",
                return_value=SimpleNamespace(scope_all=False),
            ),
            patch.object(vehicle, "require_case_in_scope", scope_gate),
        ):
            response = vehicle.get_vehicle_removal(
                CASE_ID,
                _request(individual_session=True),
                OPERATOR_TOKEN,
            )

        scope_gate.assert_called_once()
        self.assertEqual(response["case"]["name"], "Persona sintética")
        self.assertEqual(response["case"]["case_id"], CASE_ID)
        self.assertEqual(response["case"]["status"], "vehicle_removal_paid")
        self.assertEqual(response["case"]["payment_status"], "paid")
        self.assertEqual(response["case"]["nested"], {"safe": "visible"})
        self.assertEqual(response["events"][0]["payload"]["status"], "received")
        self.assertEqual(
            response["events"][0]["payload"]["person"],
            {"city": "Madrid"},
        )
        rendered = json.dumps(response, default=str, ensure_ascii=False)
        for secret in (
            "private-bucket",
            "cases/private/document.pdf",
            "203.0.113.42",
            "Sensitive Browser",
            "secret-api-key",
            "secret-session-token",
            "00000000T",
        ):
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
