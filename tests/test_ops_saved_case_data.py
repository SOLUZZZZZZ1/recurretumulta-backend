"""HTTP intake -> recorded SQL parameters -> OPS, with a simulated DB/B2.

No PostgreSQL, B2, login bridge or remote persistence is exercised here.
The existing local intake guard and OPS scope code run against synthetic rows.
"""
import copy
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import cases
from rtm_core import ops_case_scope, workspace_router, workspace_service, workspace_service_v2
from tests import test_local_intake as local

CASE_ID = "22222222-2222-4222-8222-222222222222"
FAMILIES = {
    "trafico": ("traffic", "fine"), "viajes": ("claims", "airline"),
    "morosidad": ("debt", "asnef_equifax"), "administracion": ("administration", "aeat"),
    **{key: ("claims", "consumer") for key in ("bancos", "energia", "telecomunicaciones", "seguros")},
}


class Connection(local._Connection):
    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        params = parameters or {}
        if sql.startswith("INSERT INTO cases"):
            result = super().execute(statement, parameters)
            self.engine.rows[params["id"]] = dict(
                id=params["id"], interested_data=json.loads(params["interested"]),
                contact_name=params["name"], contact_email=params["email"],
                customer_comment=params["comment"], department=params["department"],
                case_type=params["case_type"], category=params["category"],
                source_module=params["source"], status="authorization_pending",
                payment_status=None, authorized=False, test_mode=params["local_test"],
            )
            return result
        if sql.startswith("SELECT current_database()") or not sql.startswith("SELECT"):
            return super().execute(statement, parameters)
        self.engine.reads.append((sql, copy.deepcopy(params)))
        if sql.startswith("SELECT c.id"):
            row = self.engine.rows.get(params["rtm_ops_case_id"])
            return local._Result((row["id"],) if row and self.engine.allowed else None)
        if sql.startswith("SELECT to_jsonb(c)"):
            row = self.engine.rows.get(params["case_id"])
            return local._Result((copy.deepcopy(row),) if row else None)
        if sql.startswith("SELECT to_regclass"):
            return local._Result((None,))
        raise AssertionError(f"Unexpected SQL in synthetic read: {sql}")


class Context(local._Context):
    def __enter__(self):
        super().__enter__()
        return Connection(self.engine)


class Engine(local._Engine):
    def __init__(self):
        super().__init__()
        self.rows, self.reads, self.allowed = {}, [], True

    def connect(self):
        return Context(self, False)

    def begin(self):
        return Context(self, True)


class SavedCaseDataTest(unittest.TestCase):
    def setUp(self):
        # Keep only OS process-launch settings: Windows parser workers need
        # SystemRoot. Application secrets/remote DB settings are not inherited.
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC") if key in os.environ}
        environment["OPERATOR_TOKEN"] = "synthetic-ops-test-only"
        self.enterContext(mock.patch.dict(os.environ, environment, clear=True))
        self.enterContext(mock.patch("socket.create_connection", side_effect=AssertionError("No network in synthetic tests")))
        self.enterContext(mock.patch("database.get_engine", side_effect=AssertionError("No real database")))
        self.engine = Engine()
        self.context = SimpleNamespace(
            operator_id="11111111-1111-4111-8111-111111111111",
            session_id="33333333-3333-4333-8333-333333333333",
            role_code="rtm.operator", permissions=("ops.view",),
        )
        for name, value in [("local_operator_auth_requested", True), ("local_document_storage_enabled", True)]:
            self.enterContext(mock.patch.object(cases, name, return_value=value))
        for name in ("require_http_document_storage", "require_public_case_access_configured"):
            self.enterContext(mock.patch.object(cases, name))
        self.enterContext(mock.patch.object(cases, "issue_case_access_token", return_value="synthetic-case-access"))
        self.enterContext(mock.patch.object(cases, "get_engine", return_value=self.engine))
        self.upload = self.enterContext(mock.patch.object(cases, "upload_bytes", side_effect=lambda *args: ("synthetic", f"fixture/{len(self.engine.writes)}")))
        self.enterContext(mock.patch.object(workspace_router, "get_engine", return_value=self.engine))
        self.enterContext(mock.patch.object(workspace_router, "presenter_available_for_scope", return_value=False))
        self.enterContext(mock.patch.object(ops_case_scope, "operator_auth_environment_mode", return_value="individual"))
        self.enterContext(mock.patch.object(workspace_service_v2, "list_document_extractions", return_value=[]))
        app = FastAPI()
        @app.middleware("http")
        async def synthetic_session(request, call_next):
            if self.context is not None:
                request.state.rtm_operator_context = self.context
            return await call_next(request)
        app.include_router(cases.router)
        app.include_router(workspace_router.router)
        self.client = self.enterContext(TestClient(app, raise_server_exceptions=False))
        png = io.BytesIO(); Image.new("RGB", (2, 2), "white").save(png, format="PNG")
        self.png = png.getvalue()

    def post(self, family="bancos", **overrides):
        department, case_type = FAMILIES.get(family, ("claims", "consumer"))
        data = dict(department=department, case_type=case_type, source_module="rtm_web",
                    public_service_family=family, full_name="Ángela Peña FICTICIA", dni_nie="RTMTEST001",
                    domicilio_notif="Calle Ficticia del Ñandú, 007, Piso 0, Puerta 02 · 00123 Ávila",
                    street="Calle Ficticia del Ñandú", street_number="007", floor="0", door="02",
                    postal_code="00123", city="Ávila", province="Ávila",
                    email="contacto.ficticio@example.com", telefono="000000000", preferred_contact="whatsapp",
                    customer_comment="Relato ficticio: comisión de 30 €.\nSegunda línea con acentos.",
                    representation_confirmed="false", privacy_accepted="true")
        data.update(overrides)
        return self.client.post("/cases/intake-draft", data=data, files={
            "dni_front": ("front.png", self.png, "image/png"), "dni_back": ("back.png", self.png, "image/png")})

    def get(self, case_id, token="synthetic-ops-test-only"):
        return self.client.get(f"/ops/core/cases/{case_id}/workspace", headers={"X-Operator-Token": token})

    def test_eight_families_read_stored_fields_while_authorization_pending(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                created = self.post(family)
                self.assertEqual(created.status_code, 200, created.text)
                case_id = created.json()["case_id"]
                row = self.engine.rows[case_id]
                row["contact_name"] = "Óscar Contacto FICTICIO"
                row["contact_email"] = "otro.contacto@example.com"
                row["customer_comment"] = "Relato editado y guardado: áéíóú €"
                row["interested_data"]["authorization_checks"] = {"secret": "not-for-ops"}
                before_writes, before_uploads = len(self.engine.writes), self.upload.call_count
                response = self.get(case_id)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json(); data = body["case"]
                self.assertEqual(body["case_id"], case_id)
                self.assertEqual(data["saved_data_version"], workspace_service.SAVED_CASE_DATA_VERSION)
                self.assertEqual(data["identity"]["full_name"], "Ángela Peña FICTICIA")
                self.assertEqual(data["identity"]["dni_nie"], "RTMTEST001")
                self.assertEqual(data["identity"]["address_parts"], {
                    "street": "Calle Ficticia del Ñandú", "street_number": "007", "floor": "0",
                    "door": "02", "postal_code": "00123", "city": "Ávila", "province": "Ávila"})
                self.assertEqual(data["contact"], {"name": "Óscar Contacto FICTICIO", "email": "otro.contacto@example.com", "phone": "000000000", "preferred_contact": "whatsapp"})
                self.assertNotIn("email", data["identity"])
                self.assertNotIn("phone", data["identity"])
                self.assertEqual(data["customer_comment"], row["customer_comment"])
                self.assertEqual(data["public_service_family"], family)
                self.assertFalse(data["authorized"])
                self.assertIsNone(data["payment_status"])
                self.assertEqual(data["status"], "authorization_pending")
                self.assertTrue(row["test_mode"])
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertNotIn("not-for-ops", response.text)
                self.assertNotIn("interested_data", response.text)
                self.assertEqual(len(self.engine.writes), before_writes)
                self.assertEqual(self.upload.call_count, before_uploads)
                for authority in body["authority"].values():
                    self.assertIsNone(authority["latest_active"])
                event_types = {params.get("t") for _, params in self.engine.writes if params.get("t")}
                self.assertLessEqual(event_types, {"rtm_intake_created", "rtm_identity_documents_saved"})

    def test_absent_identity_is_not_reconstructed_from_contact_or_other_roles(self):
        created = self.post(); case_id = created.json()["case_id"]
        row = self.engine.rows[case_id]
        row["interested_data"] = {"driver": {"full_name": "CONDUCTOR"}, "representative": "RTM", "payer": "PAGADOR"}
        row["contact_name"] = "SOLO CONTACTO"
        data = self.get(case_id).json()["case"]
        self.assertIsNone(data["identity"]["full_name"])
        self.assertIsNone(data["identity"]["dni_nie"])
        self.assertIsNone(data["identity"]["address"])
        self.assertTrue(all(value is None for value in data["identity"]["address_parts"].values()))
        self.assertEqual(data["contact"]["name"], "SOLO CONTACTO")
        self.assertIsNone(data["public_service_family"])
        self.assertIsNone(data["contact"]["preferred_contact"])

    def test_scope_and_session_rejections_do_not_read_case_payload(self):
        created = self.post(); case_id = created.json()["case_id"]
        for mode, expected in [("outside_scope", 404), ("missing_session", 401), ("no_permission", 403), ("bad_token", 401)]:
            with self.subTest(mode=mode):
                self.engine.reads.clear(); context = self.context
                self.engine.allowed = mode != "outside_scope"
                if mode == "missing_session": self.context = None
                if mode == "no_permission": self.context = SimpleNamespace(**{**vars(context), "permissions": ()})
                response = self.get(case_id, token="invalid" if mode == "bad_token" else "synthetic-ops-test-only")
                self.assertEqual(response.status_code, expected, response.text)
                self.assertFalse(any("to_jsonb(c)" in sql for sql, _ in self.engine.reads))
                self.context = context

    def test_wrong_or_missing_case_does_not_leak_other_row(self):
        created = self.post(); self.assertEqual(created.status_code, 200)
        for case_id in (CASE_ID, "not-a-uuid"):
            response = self.get(case_id)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Ángela", response.text)

    def test_stored_identifier_zeros_and_bad_json_are_not_coerced(self):
        created = self.post(); case_id = created.json()["case_id"]
        row = self.engine.rows[case_id]
        row["interested_data"]["dni_nie"] = "00000123X"
        self.assertEqual(self.get(case_id).json()["case"]["identity"]["dni_nie"], "00000123X")
        for malformed in (None, [], "invalid-json", 42):
            row["interested_data"] = malformed
            data = self.get(case_id).json()["case"]
            self.assertIsNone(data["identity"]["full_name"])
            self.assertIsNone(data["identity"]["dni_nie"])

    def test_housing_remains_outside_common_intake(self):
        response = self.post("vivienda")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.engine.rows)
        self.upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
