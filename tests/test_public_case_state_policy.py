from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import HTTPException

import cases
import ops_operator_router
from rtm_core import intake_router
from rtm_core import case_state_policy


CASE_ID = "11111111-1111-4111-8111-111111111111"


class _Result:
    def __init__(self, row=None) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, case_state: tuple[str, str]) -> None:
        self.case_state = case_state
        self.statements: list[str] = []
        self.event_count = 0
        self.cas_succeeds = True

    def execute(self, statement, _parameters=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if "SELECT COALESCE(payment_status,'') AS payment_status" in sql:
            return _Result(self.case_state)
        if "UPDATE cases SET status=:status" in sql and "RETURNING id" in sql:
            return _Result((CASE_ID,) if self.cas_succeeds else None)
        if "INSERT INTO events" in sql:
            self.event_count += 1
        return _Result()


class _Transaction:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class _Engine:
    def __init__(self, *connections: _Connection) -> None:
        self.connections = list(connections)
        self.begin_count = 0

    def begin(self):
        self.begin_count += 1
        if not self.connections:
            raise AssertionError("Transacción inesperada")
        return _Transaction(self.connections.pop(0))


class PublicCaseStatePolicyTest(unittest.TestCase):
    def test_central_lock_rejects_every_frozen_status_and_presentado_prefix(self):
        blocked = (
            "submitted",
            "closed",
            "archived",
            "resolved",
            "final_ready",
            "manual_review",
            "ready_to_submit",
            "submitting",
            "submission_receipt_pending",
            "submission_outcome_unknown",
            "reanalysis_in_progress",
            "document_extraction_in_progress",
            "vehicle_removal_pending_payment",
            "vehicle_removal_paid",
            "vehicle_removal_assigned",
            "vehicle_removal_completed",
            "presentado_por_nuevo_canal",
        )
        for status in blocked:
            with self.subTest(status=status):
                conn = _Connection(("", status))
                with self.assertRaises(HTTPException) as raised:
                    case_state_policy.lock_case_for_public_material_mutation(
                        conn, CASE_ID
                    )
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(len(conn.statements), 1)
                self.assertIn("FOR UPDATE", conn.statements[0])

    def test_central_lock_rejects_an_open_generic_checkout(self):
        conn = _Connection(("pending", "ready_for_review_payment"))

        with self.assertRaises(HTTPException) as raised:
            case_state_policy.lock_case_for_public_material_mutation(conn, CASE_ID)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("pago en curso", str(raised.exception.detail))

    def test_central_lock_freezes_paid_and_manual_review_on_both_axes(self):
        scenarios = (
            ("paid", "ready_for_review_payment", "payment_status"),
            ("manual_review", "ready_for_review_payment", "payment_status"),
            ("unpaid", "manual_review", "case_status"),
        )
        for payment_status, case_status, axis in scenarios:
            with self.subTest(axis=axis, payment_status=payment_status, status=case_status):
                conn = _Connection((payment_status, case_status))

                with self.assertRaises(HTTPException) as raised:
                    case_state_policy.lock_case_for_public_material_mutation(
                        conn, CASE_ID
                    )

                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(len(conn.statements), 1)

    def test_append_preflight_rejects_pending_checkout_before_document_lookup(self):
        conn = _Connection(("pending", "ready_for_review_payment"))
        engine = _Engine(conn)

        with mock.patch.object(intake_router, "get_engine", return_value=engine):
            with self.assertRaises(HTTPException) as raised:
                intake_router._existing_original_hashes(CASE_ID, ["a" * 64])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(conn.statements), 1)

    def test_append_final_lock_rejects_a_state_flip_before_any_insert(self):
        conn = _Connection(("", "submitted"))
        engine = _Engine(conn)
        prepared = [
            {
                "filename": "fine.pdf",
                "mime": "application/pdf",
                "data": b"x",
                "sha256": "a" * 64,
            }
        ]

        with (
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(
                intake_router, "_invalidate_authority_after_new_document"
            ) as invalidate,
        ):
            with self.assertRaises(HTTPException) as raised:
                intake_router._commit_appended_documents(
                    CASE_ID,
                    prepared,
                    {"a" * 64: ("bucket", "key")},
                )

        self.assertEqual(raised.exception.status_code, 409)
        invalidate.assert_not_called()
        self.assertEqual(len(conn.statements), 1)

    def test_review_rejects_terminal_case_before_loading_snapshot(self):
        conn = _Connection(("", "final_ready"))
        engine = _Engine(conn)

        with (
            mock.patch.object(
                intake_router, "require_case_access_token", return_value=CASE_ID
            ),
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(intake_router, "load_case_review_snapshot") as load,
        ):
            with self.assertRaises(HTTPException) as raised:
                intake_router.review_case_core(CASE_ID, "case-token")

        self.assertEqual(raised.exception.status_code, 409)
        load.assert_not_called()

    def test_review_uses_one_transaction_and_cas_fails_closed(self):
        conn = _Connection(("", "documents_received"))
        conn.cas_succeeds = False
        engine = _Engine(conn)
        snapshot = SimpleNamespace(
            authorized=False,
            document_kinds=(),
            payment_status="unpaid",
        )
        readiness = mock.Mock(ready=False)
        readiness.model_dump.return_value = {
            "ready": False,
            "blocking_issues": [],
        }

        with (
            mock.patch.object(
                intake_router, "require_case_access_token", return_value=CASE_ID
            ),
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(
                intake_router, "load_case_review_snapshot", return_value=snapshot
            ),
            mock.patch.object(
                intake_router,
                "_authorization_evidence_state",
                return_value=(False, False, False),
            ),
            mock.patch.object(
                intake_router, "build_case_review_readiness", return_value=readiness
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                intake_router.review_case_core(CASE_ID, "case-token")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(engine.begin_count, 1)
        self.assertTrue(
            any("COALESCE(status,'')=:expected_status" in sql for sql in conn.statements)
        )
        self.assertEqual(conn.event_count, 0)

    def test_details_rejects_terminal_case_before_reading_identity(self):
        conn = _Connection(("", "closed"))
        engine = _Engine(conn)
        details = SimpleNamespace(
            full_name="Persona",
            dni_nie="12345678Z",
            matricula="1234ABC",
            domicilio_notif="Calle Uno",
            email="persona@example.test",
            telefono=None,
            autorizo_gestion=True,
            acepto_responsabilidad=True,
        )

        with (
            mock.patch.object(cases, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(cases, "get_engine", return_value=engine),
            mock.patch.object(cases, "_case_exists") as load,
        ):
            with self.assertRaises(HTTPException) as raised:
                cases.save_case_details(CASE_ID, details, "case-token")

        self.assertEqual(raised.exception.status_code, 409)
        load.assert_not_called()

    def test_contact_rejects_vehicle_workflow_before_changing_recipient(self):
        conn = _Connection(("", "vehicle_removal_paid"))
        engine = _Engine(conn)
        contact = SimpleNamespace(name="Persona", email="persona@example.test")

        with (
            mock.patch.object(cases, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(cases, "get_engine", return_value=engine),
            mock.patch.object(cases, "_case_exists") as load,
        ):
            with self.assertRaises(HTTPException) as raised:
                cases.save_case_contact(
                    CASE_ID,
                    contact,
                    mock.Mock(),
                    "case-token",
                )

        self.assertEqual(raised.exception.status_code, 409)
        load.assert_not_called()

    def test_authorize_rejects_frozen_case_before_creating_pdf(self):
        conn = _Connection(("", "ready_to_submit"))
        engine = _Engine(conn)

        with mock.patch.object(cases, "ensure_authorization_pdf") as ensure_pdf:
            with self.assertRaises(HTTPException) as raised:
                cases._authorize_case_transaction(
                    engine,
                    case_id=CASE_ID,
                    request=object(),
                    authority_version="v1_dgt_homologado",
                )

        self.assertEqual(raised.exception.status_code, 409)
        ensure_pdf.assert_not_called()
        self.assertEqual(len(conn.statements), 1)

    def test_ops_signature_review_loses_to_pending_checkout_lock(self):
        conn = _Connection(("pending", "ready_for_review_payment"))
        engine = _Engine(conn)
        scope = SimpleNamespace(
            individual_session=True,
            role_code="rtm.supervisor",
            permissions=("ops.view", "ops.supervise"),
        )

        with (
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(ops_operator_router, "get_engine", return_value=engine),
            mock.patch.object(
                ops_operator_router, "load_ops_case_scope", return_value=scope
            ),
            mock.patch.object(
                ops_operator_router, "require_case_in_scope", return_value=CASE_ID
            ),
            mock.patch.object(
                ops_operator_router, "verify_authorization_signature_candidate"
            ) as verify_candidate,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_operator_router.review_authorization_signature(
                    CASE_ID,
                    SimpleNamespace(),
                    SimpleNamespace(),
                    "operator-token",
                )

        self.assertEqual(raised.exception.status_code, 409)
        verify_candidate.assert_not_called()
        self.assertEqual(len(conn.statements), 1)
        self.assertIn("FOR UPDATE", conn.statements[0])


class SignedCandidateStateRaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_lock_rejects_terminal_flip_and_cleans_uploaded_candidate(self):
        initial = _Connection(("", "authorization_pending"))
        replay_check = _Connection(("", "authorization_pending"))
        final = _Connection(("", "submitted"))
        engine = _Engine(initial, replay_check, final)
        coordinate = ("bucket", f"cases/{CASE_ID}/authorization/candidate.pdf")
        read_upload = mock.AsyncMock(return_value=b"synthetic-pdf")

        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        with (
            mock.patch.object(cases, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(cases, "get_engine", return_value=engine),
            mock.patch.object(cases, "verify_active_case_authority", return_value={}),
            mock.patch.object(
                cases, "verify_active_authority_document_issue", return_value={}
            ),
            mock.patch.object(cases, "require_authority_document_binding"),
            mock.patch.object(cases, "require_authorization_candidate_digest_unused"),
            mock.patch.object(cases, "read_upload_limited", new=read_upload),
            mock.patch.object(cases, "_validate_public_pdf", return_value="a" * 64),
            mock.patch.object(cases, "upload_bytes", return_value=coordinate) as upload,
            mock.patch.object(cases, "delete_object") as cleanup,
            mock.patch.object(cases, "run_in_threadpool", new=inline),
        ):
            with self.assertRaises(HTTPException) as raised:
                await cases._store_authorization_signed(
                    CASE_ID,
                    SimpleNamespace(
                        filename="signed.pdf", content_type="application/pdf"
                    ),
                    "case-token",
                    authority_material_sha256="a" * 64,
                    generated_document_id="22222222-2222-4222-8222-222222222222",
                    generated_document_sha256="b" * 64,
                    generated_document_version="v1_dgt_homologado",
                    document_nonce="33333333-3333-4333-8333-333333333333",
                    issuance_attestation_sha256="c" * 64,
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(engine.begin_count, 3)
        read_upload.assert_awaited_once()
        upload.assert_called_once()
        cleanup.assert_called_once_with(*coordinate)
        self.assertEqual(len(final.statements), 1)


if __name__ == "__main__":
    unittest.main()
