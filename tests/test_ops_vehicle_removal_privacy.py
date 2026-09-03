from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError
from starlette.requests import Request

import ops_vehicle_removal_router as vehicle


CASE_ID = "33333333-3333-4333-8333-333333333333"
OPERATOR_TOKEN = "server-only-operator-token"


def _assign_body(**changes):
    values = {
        "desguace_name": "CAT autorizado",
        "human_review_attested": True,
        "authorization_version": "rtm-core-vehicle-removal-v3",
        "authorization_sha256": (
            "b8c54b902450421ba7b4754e50f79ffc6bb83aaf77de480989fe350adfaf621d"
        ),
    }
    values.update(changes)
    return vehicle.AssignBody(**values)


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
                    "Persona desde expediente",
                    {
                        "full_name": "Persona desde expediente",
                        "telefono": "+34000000000",
                        "matricula": "1234ABC",
                        "vehicle_removal_city": "Madrid",
                        "vehicle_removal_notes": "Acceso lateral",
                    },
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

    def test_projection_is_sanitized_in_every_environment_and_session_mode(self):
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

        self.assertEqual(production, {"safe": "visible"})
        self.assertEqual(no_individual_session, {"safe": "visible"})
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
            "name": "Persona duplicada",
            "full_name": "Persona duplicada",
            "phone": "+34999999999",
            "email": "duplicated@example.invalid",
            "plate": "9999ZZZ",
            "stripe_session_id": "cs_private_duplicate",
            "session_id": "cs_private_duplicate",
            "payment_intent": "pi_private_duplicate",
            "stripe_event_id": "evt_private_duplicate",
            "note": "Nota duplicada no debe salir",
            "note_recorded": True,
            "status": "received",
            "target_status": "Persona duplicada <duplicated@example.invalid>",
            "network": {
                "raw_ip": "203.0.113.42",
                "raw_user_agent": "Sensitive Browser",
            },
            "auth": {
                "api_key": "secret-api-key",
                "session_token": "secret-session-token",
            },
            "authorization": {
                "accepted": True,
                "ip": "203.0.113.99",
                "user_agent": "Private Authorization Browser",
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
        self.assertEqual(response["case"]["name"], "Persona desde expediente")
        self.assertEqual(response["case"]["phone"], "+34000000000")
        self.assertEqual(response["case"]["plate"], "1234ABC")
        self.assertEqual(response["case"]["city"], "Madrid")
        self.assertEqual(response["case"]["notes"], "Acceso lateral")
        self.assertEqual(
            response["case"]["contact_email"],
            "operator-needed@example.invalid",
        )
        self.assertEqual(response["case"]["case_id"], CASE_ID)
        self.assertEqual(response["case"]["status"], "vehicle_removal_paid")
        self.assertEqual(response["case"]["payment_status"], "paid")
        self.assertEqual(response["events"][0]["payload"]["status"], "received")
        self.assertIs(response["events"][0]["payload"]["note_recorded"], True)
        self.assertNotIn("target_status", response["events"][0]["payload"])
        self.assertNotIn("person", response["events"][0]["payload"])
        event_rendered = json.dumps(
            response["events"],
            default=str,
            ensure_ascii=False,
        )
        for duplicated in (
            "Persona duplicada",
            "+34999999999",
            "duplicated@example.invalid",
            "9999ZZZ",
            "cs_private_duplicate",
            "pi_private_duplicate",
            "evt_private_duplicate",
            "Nota duplicada no debe salir",
            "203.0.113.99",
            "Private Authorization Browser",
        ):
            self.assertNotIn(duplicated, event_rendered)
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


class _WriteConnection:
    def __init__(self, *, update_succeeds=True):
        self.calls = []
        self.update_succeeds = update_succeeds

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, dict(parameters or {})))
        if "RETURNING id" in sql:
            return _Result(
                row=(CASE_ID,) if self.update_succeeds else None,
            )
        return _Result()


class VehicleRemovalTransitionMinimizationTest(unittest.TestCase):
    def test_assign_requires_exact_explicit_human_attestation(self):
        for changes in (
            {"human_review_attested": False},
            {"authorization_version": "ai-edited"},
            {"authorization_sha256": "0" * 64},
            {"unexpected": "mass-assignment"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                _assign_body(**changes)

    def test_preparation_consent_projection_requires_exact_marker(self):
        marker = vehicle.build_vehicle_removal_preparation_consent()
        exact = vehicle._case_operational_projection(
            contact_email=None,
            contact_name=None,
            interested_data={"vehicle_removal_preparation_consent": marker},
        )
        self.assertTrue(exact["vehicle_preparation_consent"])
        self.assertEqual(
            exact["authorization_sha256"],
            marker["sha256"],
        )

        tampered = vehicle._case_operational_projection(
            contact_email=None,
            contact_name=None,
            interested_data={
                "vehicle_removal_preparation_consent": {
                    **marker,
                    "legal_representation": True,
                }
            },
        )
        self.assertFalse(tampered["vehicle_preparation_consent"])
        self.assertIsNone(tampered["authorization_version"])
        self.assertIsNone(tampered["authorization_sha256"])

    def test_transitions_are_delta_only_and_keep_case_scope_gate(self):
        request = _request(individual_session=True)
        scope = SimpleNamespace(scope_all=False)
        invocations = (
            (
                vehicle.assign_vehicle_removal,
                (
                    CASE_ID,
                    _assign_body(
                        desguace_phone="+34000000001",
                        desguace_email="cat@example.com",
                        note="Recogida acordada",
                    ),
                    request,
                    OPERATOR_TOKEN,
                ),
                {
                    "case_id": CASE_ID,
                    "status": "vehicle_removal_paid",
                    "payment_status": "paid",
                    "vehicle_preparation_consent": True,
                },
                {
                    "from": "vehicle_removal_paid",
                    "to": "vehicle_removal_assigned",
                    "assignment_recorded": True,
                    "human_review_attested": True,
                    "preparation_consent_version": "rtm-core-vehicle-removal-v3",
                    "preparation_consent_sha256": (
                        "b8c54b902450421ba7b4754e50f79ffc6bb83aaf77de480989fe350adfaf621d"
                    ),
                },
            ),
            (
                vehicle.complete_vehicle_removal,
                (
                    CASE_ID,
                    vehicle.CompleteBody(
                        certificate_ref="CERT-0001",
                        note="Baja completada",
                    ),
                    request,
                    OPERATOR_TOKEN,
                ),
                {
                    "case_id": CASE_ID,
                    "status": "vehicle_removal_assigned",
                    "payment_status": "paid",
                },
                {
                    "from": "vehicle_removal_assigned",
                    "to": "vehicle_removal_completed",
                    "completion_recorded": True,
                },
            ),
        )

        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "load_ops_case_scope", return_value=scope),
            patch.object(vehicle, "require_case_in_scope") as scope_gate,
            patch.object(vehicle, "_latest_vehicle_payload") as legacy_payload,
        ):
            for endpoint, args, case, expected_payload in invocations:
                with self.subTest(endpoint=endpoint.__name__):
                    connection = _WriteConnection()
                    with (
                        patch.object(
                            vehicle,
                            "get_engine",
                            return_value=_Engine(connection),
                        ),
                        patch.object(
                            vehicle,
                            "_case_or_404",
                            return_value=case,
                        ) as case_loader,
                    ):
                        endpoint(*args)
                    self.assertTrue(case_loader.call_args.kwargs["for_update"])
                    event_parameters = next(
                        parameters
                        for statement, parameters in connection.calls
                        if "INSERT INTO events" in statement
                    )
                    self.assertEqual(
                        json.loads(event_parameters["payload"]),
                        expected_payload,
                    )
                    event_dump = event_parameters["payload"]
                    for private_value in (
                        "CAT autorizado",
                        "+34000000001",
                        "cat@example.com",
                        "Recogida acordada",
                        "CERT-0001",
                        "Baja completada",
                    ):
                        self.assertNotIn(private_value, event_dump)
                    update_sql, update_parameters = next(
                        (statement, parameters)
                        for statement, parameters in connection.calls
                        if "UPDATE cases" in statement
                    )
                    if endpoint is vehicle.assign_vehicle_removal:
                        self.assertIn("vehicle_removal_desguace_name", update_sql)
                        self.assertEqual(
                            update_parameters["desguace_email"],
                            "cat@example.com",
                        )
                    if endpoint is vehicle.complete_vehicle_removal:
                        self.assertIn("vehicle_removal_certificate_ref", update_sql)
                        self.assertEqual(
                            update_parameters["certificate_ref"],
                            "CERT-0001",
                        )

        self.assertEqual(scope_gate.call_count, len(invocations))
        for call in scope_gate.call_args_list:
            self.assertEqual(call.kwargs["case_id"], CASE_ID)
            self.assertIs(call.kwargs["scope"], scope)
        legacy_payload.assert_not_called()

    def test_even_valid_operator_cannot_mark_payment_manually(self):
        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "get_engine") as engine,
            patch.object(vehicle, "_append_event") as append_event,
        ):
            with self.assertRaises(vehicle.HTTPException) as caught:
                vehicle.mark_vehicle_paid(
                    CASE_ID,
                    _request(individual_session=True),
                    OPERATOR_TOKEN,
                )

        self.assertEqual(caught.exception.status_code, 410)
        engine.assert_not_called()
        append_event.assert_not_called()

    def test_operator_note_is_stored_once_in_case_and_event_is_delta_only(self):
        connection = _WriteConnection()
        note = "Recogida coordinada con la persona interesada"
        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "get_engine", return_value=_Engine(connection)),
            patch.object(
                vehicle,
                "load_ops_case_scope",
                return_value=SimpleNamespace(scope_all=False),
            ),
            patch.object(vehicle, "require_case_in_scope"),
            patch.object(
                vehicle,
                "_case_or_404",
                return_value={
                    "case_id": CASE_ID,
                    "status": "vehicle_removal_paid",
                    "payment_status": "paid",
                },
            ),
        ):
            result = vehicle.add_vehicle_removal_note(
                CASE_ID,
                vehicle.NoteBody(note=note),
                _request(individual_session=True),
                OPERATOR_TOKEN,
            )

        self.assertTrue(result["ok"])
        update_sql, update_parameters = next(
            (statement, parameters)
            for statement, parameters in connection.calls
            if "UPDATE cases" in statement
        )
        self.assertIn("vehicle_removal_operator_note", update_sql)
        self.assertEqual(update_parameters["note"], note)
        event_parameters = next(
            parameters
            for statement, parameters in connection.calls
            if "INSERT INTO events" in statement
        )
        self.assertEqual(
            json.loads(event_parameters["payload"]),
            {"note_recorded": True},
        )
        self.assertNotIn(note, event_parameters["payload"])

    def test_case_loader_is_locked_and_requires_exact_vehicle_classification(self):
        connection = Mock()
        connection.execute.return_value = _Result(row=None)

        with self.assertRaises(vehicle.HTTPException) as caught:
            vehicle._case_or_404(connection, CASE_ID, for_update=True)

        self.assertEqual(caught.exception.status_code, 404)
        statement, parameters = connection.execute.call_args.args
        sql = " ".join(str(statement).split()).casefold()
        self.assertIn("for update", sql)
        self.assertIn("department, '') = 'traffic'", sql)
        self.assertIn("case_type, '') = 'vehicle_removal'", sql)
        self.assertIn("category, '') = 'vehicle_removal'", sql)
        self.assertEqual(parameters, {"id": CASE_ID})

    def test_foreign_tenant_is_rejected_before_case_load_or_write(self):
        connection = _WriteConnection()
        foreign_scope = SimpleNamespace(scope_all=False)
        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "get_engine", return_value=_Engine(connection)),
            patch.object(
                vehicle,
                "load_ops_case_scope",
                return_value=foreign_scope,
            ),
            patch.object(
                vehicle,
                "require_case_in_scope",
                side_effect=vehicle.HTTPException(
                    status_code=404,
                    detail="Expediente no encontrado",
                ),
            ),
            patch.object(vehicle, "_case_or_404") as case_loader,
        ):
            with self.assertRaises(vehicle.HTTPException) as caught:
                vehicle.assign_vehicle_removal(
                    CASE_ID,
                    _assign_body(),
                    _request(individual_session=True),
                    OPERATOR_TOKEN,
                )

        self.assertEqual(caught.exception.status_code, 404)
        case_loader.assert_not_called()
        self.assertEqual(connection.calls, [])

    def test_out_of_order_unpaid_and_repeated_transitions_fail_without_writes(self):
        request = _request(individual_session=True)
        scope = SimpleNamespace(scope_all=False)
        assign_body = _assign_body()
        complete_body = vehicle.CompleteBody(certificate_ref="CERT-0001")
        scenarios = (
            (
                vehicle.assign_vehicle_removal,
                (CASE_ID, assign_body, request, OPERATOR_TOKEN),
                "vehicle_removal_paid",
                "paid",
            ),
            (
                vehicle.assign_vehicle_removal,
                (CASE_ID, assign_body, request, OPERATOR_TOKEN),
                "vehicle_removal_pending_payment",
                "pending",
            ),
            (
                vehicle.assign_vehicle_removal,
                (CASE_ID, assign_body, request, OPERATOR_TOKEN),
                "vehicle_removal_assigned",
                "paid",
            ),
            (
                vehicle.complete_vehicle_removal,
                (CASE_ID, complete_body, request, OPERATOR_TOKEN),
                "vehicle_removal_assigned",
                "pending",
            ),
            (
                vehicle.complete_vehicle_removal,
                (CASE_ID, complete_body, request, OPERATOR_TOKEN),
                "vehicle_removal_paid",
                "paid",
            ),
            (
                vehicle.complete_vehicle_removal,
                (CASE_ID, complete_body, request, OPERATOR_TOKEN),
                "vehicle_removal_completed",
                "paid",
            ),
        )

        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "load_ops_case_scope", return_value=scope),
            patch.object(vehicle, "require_case_in_scope"),
        ):
            for endpoint, args, status, payment_status in scenarios:
                with self.subTest(
                    endpoint=endpoint.__name__,
                    status=status,
                    payment_status=payment_status,
                ):
                    connection = _WriteConnection()
                    case = {
                        "case_id": CASE_ID,
                        "status": status,
                        "payment_status": payment_status,
                    }
                    with (
                        patch.object(
                            vehicle,
                            "get_engine",
                            return_value=_Engine(connection),
                        ),
                        patch.object(
                            vehicle,
                            "_case_or_404",
                            return_value=case,
                        ),
                        patch.object(vehicle, "_append_event") as append_event,
                    ):
                        with self.assertRaises(vehicle.HTTPException) as caught:
                            endpoint(*args)

                    self.assertEqual(caught.exception.status_code, 409)
                    self.assertEqual(connection.calls, [])
                    append_event.assert_not_called()

    def test_compare_and_set_failure_rolls_back_without_event(self):
        connection = _WriteConnection(update_succeeds=False)
        case = {
            "case_id": CASE_ID,
            "status": "vehicle_removal_paid",
            "payment_status": "paid",
            "vehicle_preparation_consent": True,
        }
        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=True,
            ),
            patch.object(vehicle, "get_engine", return_value=_Engine(connection)),
            patch.object(
                vehicle,
                "load_ops_case_scope",
                return_value=SimpleNamespace(scope_all=False),
            ),
            patch.object(vehicle, "require_case_in_scope"),
            patch.object(vehicle, "_case_or_404", return_value=case),
            patch.object(vehicle, "_append_event") as append_event,
        ):
            with self.assertRaises(vehicle.HTTPException) as caught:
                vehicle.assign_vehicle_removal(
                    CASE_ID,
                    _assign_body(),
                    _request(individual_session=True),
                    OPERATOR_TOKEN,
                )

        self.assertEqual(caught.exception.status_code, 409)
        append_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
