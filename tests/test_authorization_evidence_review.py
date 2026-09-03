from __future__ import annotations

import asyncio
import hashlib
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException

import ops
import ops_operator_router
import cases
import billing
from rtm_core import intake_router


CASE_ID = "22222222-2222-4222-8222-222222222222"
CANDIDATE_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_DIGEST = "c" * 64


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _ReviewConnection:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(parameters or {})))
        if "SELECT COALESCE(payment_status,'') AS payment_status" in sql:
            return _Result(("", "authorization_pending"))
        if sql.startswith("UPDATE documents"):
            return _Result((CANDIDATE_ID,))
        raise AssertionError(f"SQL inesperado: {sql}")


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def _request(*, role="rtm.supervisor", permissions=("ops.view", "ops.supervise")):
    operator_id = "11111111-1111-4111-8111-111111111111"
    return SimpleNamespace(
        state=SimpleNamespace(
            rtm_operator_context=SimpleNamespace(
                actor=f"operator:{operator_id}",
                operator_id=operator_id,
                session_id="55555555-5555-4555-8555-555555555555",
                role_code=role,
                permissions=permissions,
            )
        )
    )


def _scope(*, role="rtm.supervisor", permissions=("ops.view", "ops.supervise")):
    return SimpleNamespace(
        individual_session=True,
        role_code=role,
        permissions=permissions,
    )


def _body(**overrides):
    values = {
        "decision": "approve",
        "candidate_document_id": CANDIDATE_ID,
        "candidate_attestation_sha256": CANDIDATE_DIGEST,
        "reviewed_entire_document": True,
        "generated_document_matches": True,
        "identity_matches": True,
        "signature_present": True,
        "reason_code": None,
    }
    values.update(overrides)
    return ops_operator_router.AuthorizationSignatureReviewBody(**values)


def _chain():
    return {
        "authority": {"material_sha256": "a" * 64},
        "issuance": {"material_sha256": "b" * 64},
        "candidate": {"material_sha256": CANDIDATE_DIGEST},
    }


class AuthorizationEvidenceReviewTest(unittest.TestCase):
    def test_download_serves_exact_issued_pdf_and_rejects_storage_tampering(self):
        pdf = b"%PDF-1.4\nissued-authorization\n%%EOF"
        digest = hashlib.sha256(pdf).hexdigest()

        class DocumentConnection:
            @staticmethod
            def execute(statement, parameters=None):
                del parameters
                sql = " ".join(str(statement).split())
                if "SELECT b2_bucket, b2_key" in sql:
                    return _Result(
                        ("private-bucket", "cases/id/authorization/doc.pdf", digest, len(pdf), "application/pdf")
                    )
                raise AssertionError(f"SQL inesperado: {sql}")

        issuance = {
            "material": {
                "document_id": "44444444-4444-4444-8444-444444444444",
                "document_sha256": digest,
            }
        }
        with (
            mock.patch.object(cases, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(cases, "get_engine", return_value=_Engine(DocumentConnection())),
            mock.patch.object(cases, "verify_active_case_authority", return_value={}),
            mock.patch.object(
                cases,
                "verify_active_authority_document_issue",
                return_value=issuance,
            ),
            mock.patch.object(
                cases, "download_bytes_limited", return_value=pdf
            ) as download,
        ):
            response = asyncio.run(
                cases.download_authorization_pdf(CASE_ID, SimpleNamespace(), "case-token")
            )
        self.assertEqual(response.body, pdf)
        self.assertEqual(response.headers["cache-control"], "no-store, private, max-age=0")
        download.assert_called_once_with(
            "private-bucket",
            "cases/id/authorization/doc.pdf",
            max_bytes=cases.MAX_PUBLIC_PDF_BYTES,
            case_id=CASE_ID,
        )

        with (
            mock.patch.object(cases, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(cases, "get_engine", return_value=_Engine(DocumentConnection())),
            mock.patch.object(cases, "verify_active_case_authority", return_value={}),
            mock.patch.object(
                cases,
                "verify_active_authority_document_issue",
                return_value=issuance,
            ),
            mock.patch.object(cases, "download_bytes_limited", return_value=pdf + b"tampered"),
            self.assertRaises(HTTPException) as denied,
        ):
            asyncio.run(
                cases.download_authorization_pdf(CASE_ID, SimpleNamespace(), "case-token")
            )
        self.assertEqual(denied.exception.status_code, 409)

    def test_scoped_individual_supervisor_can_approve_exact_candidate(self):
        connection = _ReviewConnection()
        events = []
        with (
            mock.patch.object(ops_operator_router, "get_engine", return_value=_Engine(connection)),
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(ops_operator_router, "load_ops_case_scope", return_value=_scope()),
            mock.patch.object(
                ops_operator_router,
                "require_case_in_scope",
                return_value=CASE_ID,
            ) as require_scope,
            mock.patch.object(ops_operator_router, "_case_or_404", return_value={"id": CASE_ID}),
            mock.patch.object(
                ops_operator_router,
                "_require_recent_authorization_reauthentication",
                return_value=(
                    f"operator:{_request().state.rtm_operator_context.operator_id}",
                    "66666666-6666-4666-8666-666666666666",
                ),
            ),
            mock.patch.object(
                ops_operator_router,
                "verify_authorization_signature_candidate",
                return_value=_chain(),
            ),
            mock.patch.object(
                ops_operator_router,
                "_download_verified_candidate_pdf",
                return_value=b"%PDF-1.4\n%%EOF",
            ),
            mock.patch.object(
                ops_operator_router,
                "_require_recent_candidate_view",
                return_value=(
                    "77777777-7777-4777-8777-777777777777",
                    {"material_sha256": "e" * 64},
                ),
            ),
            mock.patch.object(
                ops_operator_router,
                "build_reviewed_signed_authority_attestation",
                return_value={"material_sha256": "d" * 64},
            ),
            mock.patch.object(
                ops_operator_router,
                "_append_event",
                side_effect=lambda _conn, _case_id, typ, payload: events.append(
                    (typ, payload)
                ),
            ),
        ):
            result = ops_operator_router.review_authorization_signature(
                CASE_ID,
                _body(),
                _request(),
                "internal-legacy-token",
            )

        require_scope.assert_called_once()
        self.assertTrue(result["signed_authority_verified"])
        self.assertEqual(result["authorization_evidence_status"], "verified")
        self.assertEqual(events[0][0], "authorization_signature_approved")
        self.assertTrue(
            any("SET kind='authorization_signed'" in sql for sql, _ in connection.calls)
        )

    def test_shared_or_non_supervisor_identity_cannot_review(self):
        denied_scopes = (
            SimpleNamespace(
                individual_session=False,
                role_code="legacy.operator",
                permissions=("ops.view",),
            ),
            _scope(role="rtm.operator", permissions=("ops.view",)),
            _scope(role="rtm.supervisor", permissions=("ops.view",)),
        )
        for scope in denied_scopes:
            with self.subTest(scope=scope), self.assertRaises(HTTPException) as denied:
                ops_operator_router._require_individual_authorization_reviewer(scope)
            self.assertEqual(denied.exception.status_code, 403)

    def test_out_of_tenant_case_fails_before_candidate_is_loaded(self):
        connection = _ReviewConnection()
        with (
            mock.patch.object(ops_operator_router, "get_engine", return_value=_Engine(connection)),
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(ops_operator_router, "load_ops_case_scope", return_value=_scope()),
            mock.patch.object(
                ops_operator_router,
                "require_case_in_scope",
                side_effect=HTTPException(status_code=404, detail="Expediente no encontrado"),
            ),
            mock.patch.object(
                ops_operator_router,
                "verify_authorization_signature_candidate",
            ) as load_candidate,
            self.assertRaises(HTTPException) as denied,
        ):
            ops_operator_router.review_authorization_signature(
                CASE_ID,
                _body(),
                _request(),
                "internal-legacy-token",
            )
        self.assertEqual(denied.exception.status_code, 404)
        load_candidate.assert_not_called()

    def test_tampered_candidate_digest_cannot_be_approved(self):
        connection = _ReviewConnection()
        with (
            mock.patch.object(ops_operator_router, "get_engine", return_value=_Engine(connection)),
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(ops_operator_router, "load_ops_case_scope", return_value=_scope()),
            mock.patch.object(ops_operator_router, "require_case_in_scope", return_value=CASE_ID),
            mock.patch.object(ops_operator_router, "_case_or_404", return_value={"id": CASE_ID}),
            mock.patch.object(
                ops_operator_router,
                "_require_recent_authorization_reauthentication",
                return_value=(
                    f"operator:{_request().state.rtm_operator_context.operator_id}",
                    "66666666-6666-4666-8666-666666666666",
                ),
            ),
            mock.patch.object(
                ops_operator_router,
                "verify_authorization_signature_candidate",
                return_value=_chain(),
            ),
            self.assertRaises(HTTPException) as denied,
        ):
            ops_operator_router.review_authorization_signature(
                CASE_ID,
                _body(candidate_attestation_sha256="f" * 64),
                _request(),
                "internal-legacy-token",
            )
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(len(connection.calls), 1)
        self.assertIn("FOR UPDATE", connection.calls[0][0])

    def test_signature_review_requires_exact_recent_reauthentication_event(self):
        operator_id = "11111111-1111-4111-8111-111111111111"

        class StepUpConnection:
            def __init__(self, row):
                self.row = row
                self.calls = []

            def execute(self, statement, parameters=None):
                self.calls.append((" ".join(str(statement).split()), parameters))
                return _Result(self.row)

        denied_connection = StepUpConnection(None)
        with self.assertRaises(HTTPException) as denied:
            ops_operator_router._require_recent_authorization_reauthentication(
                denied_connection,
                _request(),
            )
        self.assertEqual(denied.exception.status_code, 403)
        sql = denied_connection.calls[0][0]
        self.assertIn("e.event_type='auth.reauthenticated'", sql)
        self.assertIn("e.occurred_at=s.last_verified_at", sql)
        self.assertIn("INTERVAL '5 minutes'", sql)
        self.assertIn("s.last_verified_at > s.login_at", sql)

        event_id = "66666666-6666-4666-8666-666666666666"
        allowed_connection = StepUpConnection((event_id,))
        actor, verified_event_id = ops_operator_router._require_recent_authorization_reauthentication(
            allowed_connection,
            _request(),
        )
        self.assertEqual(actor, f"operator:{operator_id}")
        self.assertEqual(verified_event_id, event_id)

    def test_signature_review_rejects_forged_operator_context_before_sql(self):
        connection = mock.Mock()
        forged = _request()
        forged.state.rtm_operator_context.actor = "operator:legacy-local"
        with self.assertRaises(HTTPException) as denied:
            ops_operator_router._require_recent_authorization_reauthentication(
                connection,
                forged,
            )
        self.assertEqual(denied.exception.status_code, 403)
        connection.execute.assert_not_called()

    def test_unreviewed_candidate_cannot_reach_presenter_authority_gate(self):
        class GateConnection:
            @staticmethod
            def execute(statement, parameters=None):
                del parameters
                sql = " ".join(str(statement).split())
                if "SELECT payment_status, authorized" in sql:
                    return _Result(("paid", True, False))
                raise AssertionError(f"SQL inesperado: {sql}")

        with (
            mock.patch.object(
                ops,
                "verify_signed_case_authority",
                side_effect=HTTPException(
                    status_code=409,
                    detail="Revisión firmada de autoridad no verificable",
                ),
            ) as verify,
            self.assertRaises(HTTPException) as denied,
        ):
            ops._require_paid_and_authorized(GateConnection(), str(uuid4()))
        self.assertEqual(denied.exception.status_code, 409)
        verify.assert_called_once()

    def test_unreviewed_candidate_cannot_create_generic_checkout(self):
        class GateConnection:
            @staticmethod
            def execute(statement, parameters=None):
                del parameters
                sql = " ".join(str(statement).split())
                if "SELECT COALESCE(payment_status, '') AS payment_status" in sql:
                    return _Result(
                        ("unpaid", "", "", "ready_for_review_payment")
                    )
                raise AssertionError(f"SQL inesperado: {sql}")

        connection = GateConnection()
        snapshot = SimpleNamespace(
            authorized=True,
            payment_status="unpaid",
            document_kinds=("authorization_signed",),
        )
        readiness = SimpleNamespace(ready=True, version="test-readiness")
        request = billing.CheckoutRequest(
            case_id=CASE_ID,
            payment_stage="review",
            product="traffic_review",
        )
        with (
            mock.patch.object(billing, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(
                billing,
                "trusted_frontend_origin",
                return_value="https://recurretumulta.eu",
            ),
            mock.patch.object(
                billing,
                "_env",
                side_effect=lambda name: (
                    "sk_test_synthetic" if name == "STRIPE_SECRET_KEY" else "https://example.test"
                ),
            ),
            mock.patch.object(billing, "get_engine", return_value=_Engine(connection)),
            mock.patch.object(billing, "load_case_review_snapshot", return_value=snapshot),
            mock.patch.object(billing, "build_case_review_readiness", return_value=readiness),
            mock.patch.object(
                billing,
                "_review_product",
                return_value={
                    "service_code": "traffic",
                    "billing_code": "TRAFFIC_REVIEW",
                    "payment_stage": "review",
                    "authority_version": "v1",
                    "amount_cents": 1000,
                    "currency": "EUR",
                },
            ),
            mock.patch.object(
                billing,
                "verify_signed_case_authority",
                side_effect=HTTPException(
                    status_code=409,
                    detail="Revisión firmada de autoridad no verificable",
                ),
            ) as verify,
            mock.patch.object(billing.stripe.checkout.Session, "create") as stripe_create,
            self.assertRaises(HTTPException) as denied,
        ):
            billing.create_checkout(request, "case-token")
        self.assertEqual(denied.exception.status_code, 409)
        verify.assert_called_once_with(connection, CASE_ID)
        stripe_create.assert_not_called()

    def test_identity_invalidation_projects_old_candidate_as_not_submitted(self):
        class StatusConnection:
            def __init__(self):
                self.calls = 0

            def execute(self, statement, parameters=None):
                del statement, parameters
                self.calls += 1
                return _Result((CANDIDATE_ID,) if self.calls == 1 else None)

        snapshot = SimpleNamespace(
            authorized=False,
            case_type="fine",
            document_kinds=(
                "authorization_signed_candidate_stale",
                "identity_front",
                "identity_back",
                "original",
            ),
            payment_status="unpaid",
            status="authorization_pending",
        )
        readiness = SimpleNamespace(
            ready=False,
            blocking_issues=(
                SimpleNamespace(area="authorization", blocking=True),
            ),
            quote=SimpleNamespace(
                department="traffic",
                billing_code="TRAFFIC_REVIEW",
                amount_cents=1000,
                currency="EUR",
                authority_version="v1",
            ),
        )
        connection = StatusConnection()
        with (
            mock.patch.object(
                intake_router, "require_case_access_token", return_value=CASE_ID
            ),
            mock.patch.object(
                intake_router, "get_engine", return_value=_Engine(connection)
            ),
            mock.patch.object(
                intake_router, "load_case_review_snapshot", return_value=snapshot
            ),
            mock.patch.object(
                intake_router, "build_case_review_readiness", return_value=readiness
            ),
        ):
            result = intake_router.public_status_core(CASE_ID, "case-token")

        self.assertEqual(result["authorization_evidence_status"], "not_submitted")
        self.assertFalse(result["signed_authority_verified"])
        self.assertFalse(result["progress"]["authorization_candidate_received"])


if __name__ == "__main__":
    unittest.main()
