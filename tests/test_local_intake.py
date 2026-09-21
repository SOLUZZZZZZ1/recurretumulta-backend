"""Exercise HTTP intake boundaries and compensation without a network database.

Database identity validation and file validation are real; the engine records
transaction outcomes and the storage boundary records writes/deletions.
"""
from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import ExitStack
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import cases


def _identity():
    return dict(database_name="rtm_local", user_name="rtm_local_app",
                session_name="rtm_local_app", server_address="127.0.0.1",
                client_address="127.0.0.1", server_port=5432,
                owner_name="rtm_local_app", version=170011, rolsuper=False,
                rolcreatedb=False, rolcreaterole=False, rolreplication=False)


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT current_database()"):
            self.engine.identity_reads += 1
            self.engine.order.append("identity")
            row = copy.deepcopy(self.engine.identity)
            if self.engine.identity_reads == self.engine.change_target_on_read:
                row["database_name"] = "rtm_staging"
            return _Result(row)
        if not self.engine.in_transaction:
            raise AssertionError("Mutation outside the intake transaction")
        self.engine.order.append("sql_write")
        self.engine.writes.append((sql, copy.deepcopy(parameters)))
        if "INSERT INTO documents" in sql:
            return _Result((f"document-{len(self.engine.writes)}",))
        if self.engine.fail_final_event and parameters.get("t") == "rtm_identity_documents_saved":
            raise RuntimeError("synthetic SQL failure with private details")
        return _Result()


class _Context:
    def __init__(self, engine, transaction):
        self.engine = engine
        self.transaction = transaction

    def __enter__(self):
        if self.transaction:
            self.engine.in_transaction = True
        return _Connection(self.engine)

    def __exit__(self, exc_type, _value, _traceback):
        if self.transaction:
            self.engine.in_transaction = False
            self.engine.committed = exc_type is None
            self.engine.rolled_back = exc_type is not None
        return False


class _Engine:
    def __init__(self):
        self.identity = _identity()
        self.identity_reads = 0
        self.change_target_on_read = None
        self.in_transaction = False
        self.committed = False
        self.rolled_back = False
        self.fail_final_event = False
        self.writes = []
        self.order = []

    def connect(self):
        return _Context(self, False)

    def begin(self):
        return _Context(self, True)


class LocalIntakeTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.engine = _Engine()
        self.local_flag = stack.enter_context(mock.patch.object(
            cases, "local_operator_auth_requested", return_value=True))
        self.local_storage = stack.enter_context(mock.patch.object(
            cases, "local_document_storage_enabled", return_value=True))
        self.storage_gate = stack.enter_context(mock.patch.object(
            cases, "require_http_document_storage"))
        self.remote_gate = stack.enter_context(mock.patch.object(
            cases, "require_http_capability"))
        stack.enter_context(mock.patch.object(cases, "get_engine", return_value=self.engine))
        stack.enter_context(mock.patch.object(cases, "require_public_case_access_configured"))
        stack.enter_context(mock.patch.object(cases, "issue_case_access_token", return_value="test-case-token"))
        self.objects = []
        self.upload = stack.enter_context(mock.patch.object(
            cases, "upload_bytes", side_effect=self._store))
        self.cleanup = stack.enter_context(mock.patch.object(cases, "delete_object"))
        app = FastAPI()
        app.include_router(cases.router)
        self.client = stack.enter_context(TestClient(app, raise_server_exceptions=False))
        image = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, format="PNG")
        self.image = image.getvalue()

    def _store(self, case_id, folder, _data, extension, _mime):
        coordinate = ("rtm-local", f"cases/{case_id}/{folder}/{len(self.objects)}{extension}")
        self.engine.order.append("upload")
        self.objects.append(coordinate)
        return coordinate

    def _post(self, *, overrides=None, back=None):
        data = dict(
            department="claims", case_type="consumer", source_module="rtm_web",
            public_service_family="bancos", full_name="Persona de prueba RTM",
            dni_nie="RTMTEST001", domicilio_notif="Calle de pruebas 1",
            street="Calle de pruebas", street_number="1", postal_code="08240",
            city="Manresa", province="Barcelona", email="prueba@example.com",
            telefono="000000000", preferred_contact="email",
            customer_comment="Caso ficticio: comisión bancaria de 30 euros.",
            representation_confirmed="false", privacy_accepted="true",
        )
        data.update(overrides or {})
        return self.client.post("/cases/intake-draft", data=data, files={
            "dni_front": ("front.png", self.image, "image/png"),
            "dni_back": ("back.png", self.image if back is None else back, "image/png"),
        })

    def _case_insert(self):
        return next(params for sql, params in self.engine.writes if "INSERT INTO cases" in sql)

    def test_local_case_is_test_only_and_banks_classification_survives(self):
        response = self._post(overrides={"test_mode": "false", "local_test": "false"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["test_mode"])
        self.assertFalse(response.json()["authorized"])
        self.assertTrue(self.engine.committed)
        self.assertEqual(self.engine.identity_reads, 2)
        self.assertEqual(self.engine.order[:4], ["identity", "upload", "upload", "identity"])
        stored = self._case_insert()
        self.assertTrue(stored["local_test"])
        interested = json.loads(stored["interested"])
        self.assertEqual(interested["public_service_family"], "bancos")
        self.assertEqual(interested["case_type"], "consumer")
        self.assertEqual(interested["local_test"], {"synthetic": True, "local_only": True})
        self.storage_gate.assert_called_once_with()
        self.remote_gate.assert_not_called()
        self.cleanup.assert_not_called()

    def test_real_contact_or_identity_is_refused_before_reads_or_writes(self):
        for fields in ({"email": "cliente@correo.es"}, {"dni_nie": "12345678Z"},
                       {"email": "prueba@example.com.untrusted.com"}):
            with self.subTest(fields=fields):
                response = self._post(overrides=fields)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.engine.identity_reads, 0)
                self.assertEqual(self.engine.writes, [])
                self.upload.assert_not_called()

    def test_wrong_database_is_refused_before_any_document_or_row_write(self):
        self.engine.identity["database_name"] = "rtm_staging"
        response = self._post()
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.engine.identity_reads, 1)
        self.assertEqual(self.engine.writes, [])
        self.upload.assert_not_called()
        self.cleanup.assert_not_called()

    def test_local_storage_is_required_even_when_remote_gate_would_allow(self):
        self.local_storage.return_value = False
        response = self._post()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.engine.identity_reads, 0)
        self.upload.assert_not_called()
        self.remote_gate.assert_not_called()

    def test_bad_second_identity_upload_prevents_first_storage_write(self):
        response = self._post(back=b"this is not an image")
        self.assertEqual(response.status_code, 415, response.text)
        self.assertEqual(self.engine.writes, [])
        self.upload.assert_not_called()

    def test_second_upload_failure_removes_first_object_without_sql_writes(self):
        first = ("rtm-local", "cases/synthetic/identity/front.png")
        self.upload.side_effect = [first, RuntimeError("private local path")]
        response = self._post()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn("private local path", response.text)
        self.assertEqual(self.engine.writes, [])
        self.cleanup.assert_called_once_with(*first)

    def test_database_target_is_rechecked_inside_transaction_and_compensated(self):
        self.engine.change_target_on_read = 2
        response = self._post()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertTrue(self.engine.rolled_back)
        self.assertEqual(self.engine.writes, [])
        self.assertEqual(self.cleanup.call_args_list, [mock.call(*x) for x in reversed(self.objects)])
        self.assertEqual(len(self.objects), 2)

    def test_final_sql_failure_rolls_back_and_removes_both_objects(self):
        self.engine.fail_final_event = True
        response = self._post()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("private details", response.text)
        self.assertTrue(self.engine.rolled_back)
        self.assertFalse(self.engine.committed)
        self.assertEqual(self.cleanup.call_args_list, [mock.call(*x) for x in reversed(self.objects)])
        self.assertEqual(len(self.objects), 2)

    def test_client_cannot_select_test_mode_for_legacy_environment(self):
        self.local_flag.return_value = False
        response = self._post(overrides={"test_mode": "true", "local_test": "true",
                                        "email": "cliente@correo.es", "dni_nie": "12345678Z"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["test_mode"])
        self.assertFalse(self._case_insert()["local_test"])
        self.assertNotIn("local_test", json.loads(self._case_insert()["interested"]))
        self.assertEqual(self.engine.identity_reads, 0)
        self.remote_gate.assert_called_once_with("b2")
        self.storage_gate.assert_not_called()

    def test_legacy_b2_capability_still_precedes_all_writes(self):
        self.local_flag.return_value = False
        self.remote_gate.side_effect = HTTPException(status_code=503, detail="B2 disabled")
        response = self._post()
        self.assertEqual(response.status_code, 503, response.text)
        self.upload.assert_not_called()
        self.assertEqual(self.engine.writes, [])


if __name__ == "__main__":
    unittest.main()
