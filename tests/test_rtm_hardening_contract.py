from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock
from uuid import uuid4

from fastapi import HTTPException

import case_authority
import public_case_access
import rtm_staging_guards


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_source(path: str, name: str) -> str:
    source = _source(path)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"No existe {name} en {path}")


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _AuthorityConn:
    def __init__(
        self,
        *,
        case_row,
        event_row,
        invalidated=None,
        issue_row=None,
        signed_row=None,
        view_row=None,
        reauthentication_row=None,
    ):
        self.case_row = (
            (*case_row, "traffic", "fine")
            if case_row is not None and len(case_row) == 3
            else case_row
        )
        self.event_row = event_row
        self.invalidated = invalidated
        self.issue_row = issue_row
        self.signed_row = signed_row
        self.view_row = view_row
        self.reauthentication_row = reauthentication_row
        self.calls = []

    def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append(sql)
        if "SELECT authorized" in sql:
            return _Result(self.case_row)
        if "case_authority_revoked" in sql:
            return _Result(self.invalidated)
        if "authorization_pdf_issued" in sql:
            return _Result(self.issue_row)
        if "authorization_signature_approved" in sql:
            return _Result(self.signed_row)
        if "authorization_signature_candidate_viewed" in sql:
            return _Result(self.view_row)
        if "FROM rtm_operator_access_events" in sql:
            return _Result(self.reauthentication_row)
        if "SELECT payload, created_at FROM events" in sql:
            return _Result(self.event_row)
        raise AssertionError(f"SQL inesperado: {sql}")


class _BillingResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _BillingConn:
    def __init__(self, *, case_row, intent):
        self.case_row = case_row
        self.intent = intent
        self.events = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        if "SELECT payment_status, stripe_session_id" in sql:
            return _BillingResult(self.case_row)
        if "SELECT payload FROM events" in sql:
            return _BillingResult((self.intent,))
        if "UPDATE cases" in sql and "payment_status='paid'" in sql:
            return _BillingResult((parameters.get("id"),))
        if "INSERT INTO events" in sql:
            self.events.append(parameters.get("type"))
            return _BillingResult()
        raise AssertionError(f"SQL inesperado: {sql}")


class _Begin:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return _Begin(self.conn)


class _WebhookRequest:
    headers = {"stripe-signature": "sig_test"}

    async def body(self):
        return b"signed-event"


def _load_billing_for_event(event, engine):
    stripe = types.ModuleType("stripe")
    stripe.api_key = ""
    stripe.Webhook = types.SimpleNamespace(construct_event=lambda *args: event)
    stripe.checkout = types.SimpleNamespace(
        Session=types.SimpleNamespace(create=lambda **kwargs: None)
    )

    database = types.ModuleType("database")
    database.get_engine = lambda: engine

    authority = types.ModuleType("case_authority")
    authority.verify_signed_case_authority = lambda conn, case_id: {
        "material_sha256": "a" * 64,
        "signed_document_attestation": {"material_sha256": "s" * 64},
    }

    rtm_core = types.ModuleType("rtm_core")
    rtm_core.__path__ = [str(ROOT / "rtm_core")]
    repository = types.ModuleType("rtm_core.repository")
    repository.build_case_review_readiness = lambda snapshot: None
    repository.load_case_review_snapshot = lambda conn, case_id: None
    capabilities = types.ModuleType("rtm_core.runtime_capabilities")
    capabilities.require_http_capability = lambda name: None
    catalog = types.ModuleType("rtm_core.service_catalog")
    catalog.normalize_code = lambda value: str(value or "").strip().lower()
    trusted_origins = types.ModuleType("rtm_core.trusted_origins")
    trusted_origins.trusted_frontend_origin = lambda: "https://recurretumulta.eu"

    stubs = {
        "stripe": stripe,
        "database": database,
        "case_authority": authority,
        "rtm_core": rtm_core,
        "rtm_core.repository": repository,
        "rtm_core.runtime_capabilities": capabilities,
        "rtm_core.service_catalog": catalog,
        "rtm_core.trusted_origins": trusted_origins,
    }
    module_name = f"billing_hardening_test_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "billing.py")
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar billing.py")
    module = importlib.util.module_from_spec(spec)
    # ``exec_module`` normally runs with its module registered in
    # ``sys.modules``.  FastAPI resolves postponed annotations through that
    # registry, so the isolated loader must preserve the same import contract.
    stubs[module_name] = module
    with mock.patch.dict(sys.modules, stubs, clear=False):
        spec.loader.exec_module(module)
    return module


class PublicCaseAccessTest(TestCase):
    def test_token_is_case_scoped_and_secret_is_mandatory(self):
        case_id = str(uuid4())
        other_case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            {"RTM_PUBLIC_CASE_ACCESS_SECRET": "p" * 48},
            clear=False,
        ):
            token = public_case_access.issue_case_access_token(case_id)
            self.assertTrue(token.startswith("v2."))
            self.assertTrue(public_case_access.verify_case_access_token(case_id, token))
            self.assertFalse(
                public_case_access.verify_case_access_token(other_case_id, token)
            )
            with self.assertRaises(HTTPException) as wrong:
                public_case_access.require_case_access_token(case_id, token + "0")
            self.assertEqual(wrong.exception.status_code, 401)

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as missing:
                public_case_access.issue_case_access_token(case_id)
            self.assertEqual(missing.exception.status_code, 503)

    def test_public_case_token_is_unique_expiring_and_future_safe(self):
        case_id = str(uuid4())
        issued_at = 2_000_000_000
        with mock.patch.dict(
            os.environ,
            {
                "RTM_PUBLIC_CASE_ACCESS_SECRET": "p" * 48,
                "RTM_PUBLIC_CASE_TOKEN_TTL_SECONDS": "3600",
            },
            clear=True,
        ):
            first = public_case_access.issue_case_access_token(case_id, now=issued_at)
            second = public_case_access.issue_case_access_token(case_id, now=issued_at)
            self.assertNotEqual(first, second)
            self.assertTrue(
                public_case_access.verify_case_access_token(
                    case_id, first, now=issued_at + 3599
                )
            )
            self.assertFalse(
                public_case_access.verify_case_access_token(
                    case_id, first, now=issued_at + 3601
                )
            )
            future = public_case_access.issue_case_access_token(
                case_id, now=issued_at + 301
            )
            self.assertFalse(
                public_case_access.verify_case_access_token(
                    case_id, future, now=issued_at
                )
            )

    def test_operator_can_read_billing_status_without_public_capability(self):
        case_id = str(uuid4())
        with mock.patch.dict(
            os.environ,
            {"OPERATOR_TOKEN": "operator-secret-48-bytes-xxxxxxxxxxxxxxxx"},
            clear=True,
        ):
            canonical = public_case_access.require_case_or_operator_access(
                case_id,
                None,
                "operator-secret-48-bytes-xxxxxxxxxxxxxxxx",
            )
        self.assertEqual(canonical, case_id)

    def test_receipt_upload_requires_operator_not_public_capability(self):
        case_id = str(uuid4())
        operator_token = "operator-secret-48-bytes-xxxxxxxxxxxxxxxx"
        with mock.patch.dict(
            os.environ,
            {
                "OPERATOR_TOKEN": operator_token,
                "RTM_PUBLIC_CASE_ACCESS_SECRET": "p" * 48,
            },
            clear=True,
        ):
            public_token = public_case_access.issue_case_access_token(case_id)
            with self.assertRaises(HTTPException) as denied:
                public_case_access.require_operator_case_access(case_id, public_token)
            self.assertEqual(denied.exception.status_code, 401)
            self.assertEqual(
                public_case_access.require_operator_case_access(case_id, operator_token),
                case_id,
            )


class CaseAuthorityTest(TestCase):
    def test_dgt_authority_is_rejected_for_vehicle_and_non_traffic_cases(self):
        case_authority.require_dgt_fine_authority_scope("traffic", "fine")
        for department, case_type in (
            ("traffic", "vehicle_removal"),
            ("debt", "credit_file"),
            ("claims", "travel"),
        ):
            with self.subTest(department=department, case_type=case_type):
                with self.assertRaises(HTTPException) as denied:
                    case_authority.require_dgt_fine_authority_scope(
                        department, case_type
                    )
                self.assertEqual(denied.exception.status_code, 409)

    def test_active_authority_binds_case_identity_time_and_signature(self):
        case_id = str(uuid4())
        interested = {
            "full_name": "Ana Ejemplo",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "email": "ana@example.test",
        }
        accepted_at = datetime.now(timezone.utc)
        with mock.patch.dict(
            os.environ,
            {"RTM_AUTHORITY_SIGNING_SECRET": "a" * 48},
            clear=False,
        ):
            payload = case_authority.build_case_authority_payload(
                case_id=case_id,
                interested=interested,
                accepted_at=accepted_at.isoformat(),
                request_ip="192.0.2.10",
            )
            conn = _AuthorityConn(
                case_row=(True, interested, accepted_at),
                event_row=(payload, accepted_at),
            )
            self.assertEqual(
                case_authority.verify_active_case_authority(conn, case_id),
                payload,
            )

            changed = dict(interested, email="changed@example.test")
            tampered_conn = _AuthorityConn(
                case_row=(True, changed, accepted_at),
                event_row=(payload, accepted_at),
            )
            with self.assertRaises(HTTPException) as tampered:
                case_authority.verify_active_case_authority(tampered_conn, case_id)
            self.assertEqual(tampered.exception.status_code, 409)

    def test_signed_authority_requires_bound_candidate_and_human_review(self):
        case_id = str(uuid4())
        generated_document_id = str(uuid4())
        candidate_document_id = str(uuid4())
        interested = {
            "full_name": "Ana Ejemplo",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "email": "ana@example.test",
        }
        accepted_at = datetime.now(timezone.utc)
        with mock.patch.dict(
            os.environ,
            {"RTM_AUTHORITY_SIGNING_SECRET": "a" * 48},
            clear=False,
        ):
            authority = case_authority.build_case_authority_payload(
                case_id=case_id,
                interested=interested,
                accepted_at=accepted_at.isoformat(),
                request_ip="192.0.2.10",
            )
            issuance = case_authority.build_authority_document_issue_attestation(
                case_id=case_id,
                authority_payload=authority,
                document_id=generated_document_id,
                document_sha256="e" * 64,
                size_bytes=654,
                document_version="v1_dgt_homologado",
                document_nonce=str(uuid4()),
                issued_at=accepted_at.isoformat(),
            )
            candidate = case_authority.build_authorization_signature_candidate_attestation(
                case_id=case_id,
                authority_payload=authority,
                issuance_payload=issuance,
                document_id=candidate_document_id,
                document_sha256="d" * 64,
                size_bytes=321,
                uploaded_at=accepted_at.isoformat(),
            )
            reviewer_operator_id = str(uuid4())
            reviewer_session_id = str(uuid4())
            view_event_id = str(uuid4())
            reauthentication_event_id = str(uuid4())
            view = case_authority.build_authorization_signature_view_attestation(
                case_id=case_id,
                candidate_payload=candidate,
                reviewer_actor=f"operator:{reviewer_operator_id}",
                operator_session_id=reviewer_session_id,
                viewed_at=accepted_at.isoformat(),
            )
            signed = case_authority.build_reviewed_signed_authority_attestation(
                case_id=case_id,
                authority_payload=authority,
                issuance_payload=issuance,
                candidate_payload=candidate,
                reviewer_actor=f"operator:{reviewer_operator_id}",
                operator_session_id=reviewer_session_id,
                view_event_id=view_event_id,
                view_payload=view,
                reauthentication_event_id=reauthentication_event_id,
                review_checklist={
                    "reviewed_entire_document": True,
                    "generated_document_matches": True,
                    "identity_matches": True,
                    "signature_present": True,
                },
                reviewed_at=accepted_at.isoformat(),
            )
            conn = _AuthorityConn(
                case_row=(True, interested, accepted_at),
                event_row=(authority, accepted_at),
                issue_row=(
                    issuance,
                    accepted_at,
                    generated_document_id,
                    "e" * 64,
                    "application/pdf",
                    654,
                ),
                signed_row=(
                    signed,
                    accepted_at,
                    candidate,
                    accepted_at,
                    candidate_document_id,
                    "d" * 64,
                    "application/pdf",
                    321,
                    "authorization_signed",
                ),
                view_row=(view, accepted_at),
                reauthentication_row=(
                    reviewer_operator_id,
                    reviewer_session_id,
                    accepted_at,
                ),
            )
            verified = case_authority.verify_signed_case_authority(conn, case_id)
            self.assertEqual(verified["material_sha256"], authority["material_sha256"])
            self.assertEqual(verified["signed_document_attestation"], signed)

            conn.signed_row = (*conn.signed_row[:7], 999, conn.signed_row[8])
            with self.assertRaises(HTTPException) as tampered:
                case_authority.verify_signed_case_authority(conn, case_id)
            self.assertEqual(tampered.exception.status_code, 409)

    def test_legacy_public_upload_event_is_never_accepted_as_signed_authority(self):
        case_id = str(uuid4())
        interested = {
            "full_name": "Ana Ejemplo",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "email": "ana@example.test",
        }
        accepted_at = datetime.now(timezone.utc)
        with mock.patch.dict(
            os.environ,
            {"RTM_AUTHORITY_SIGNING_SECRET": "a" * 48},
            clear=False,
        ):
            authority = case_authority.build_case_authority_payload(
                case_id=case_id,
                interested=interested,
                accepted_at=accepted_at.isoformat(),
                request_ip="192.0.2.10",
            )
            issuance = case_authority.build_authority_document_issue_attestation(
                case_id=case_id,
                authority_payload=authority,
                document_id=str(uuid4()),
                document_sha256="e" * 64,
                size_bytes=654,
                document_version="v1_dgt_homologado",
                document_nonce=str(uuid4()),
                issued_at=accepted_at.isoformat(),
            )
            conn = _AuthorityConn(
                case_row=(True, interested, accepted_at),
                event_row=(authority, accepted_at),
                issue_row=(
                    issuance,
                    accepted_at,
                    issuance["material"]["document_id"],
                    "e" * 64,
                    "application/pdf",
                    654,
                ),
                signed_row=None,
            )
            with self.assertRaises(HTTPException) as denied:
                case_authority.verify_signed_case_authority(conn, case_id)
        self.assertEqual(denied.exception.status_code, 409)
        self.assertFalse(any("authorization_signed_uploaded" in sql for sql in conn.calls))

    def test_browser_binding_rejects_tampering_and_stale_authority(self):
        case_id = str(uuid4())
        accepted_at = datetime.now(timezone.utc)
        interested = {
            "full_name": "Ana Ejemplo",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "email": "ana@example.test",
        }
        with mock.patch.dict(
            os.environ,
            {"RTM_AUTHORITY_SIGNING_SECRET": "a" * 48},
            clear=False,
        ):
            authority = case_authority.build_case_authority_payload(
                case_id=case_id,
                interested=interested,
                accepted_at=accepted_at.isoformat(),
                request_ip="192.0.2.10",
            )
            issuance = case_authority.build_authority_document_issue_attestation(
                case_id=case_id,
                authority_payload=authority,
                document_id=str(uuid4()),
                document_sha256="e" * 64,
                size_bytes=654,
                document_version="v1_dgt_homologado",
                document_nonce=str(uuid4()),
                issued_at=accepted_at.isoformat(),
            )
            material = issuance["material"]
            binding = {
                "authority_material_sha256": authority["material_sha256"],
                "generated_document_id": material["document_id"],
                "generated_document_sha256": material["document_sha256"],
                "generated_document_version": material["document_version"],
                "document_nonce": material["document_nonce"],
                "issuance_attestation_sha256": issuance["material_sha256"],
            }
            self.assertIsNone(
                case_authority.require_authority_document_binding(
                    issuance, **binding
                )
            )
            with self.assertRaises(HTTPException) as tampered:
                case_authority.require_authority_document_binding(
                    issuance,
                    **{**binding, "generated_document_sha256": "f" * 64},
                )
            self.assertEqual(tampered.exception.status_code, 409)

            replacement_authority = case_authority.build_case_authority_payload(
                case_id=case_id,
                interested=interested,
                accepted_at=accepted_at.isoformat(),
                request_ip="192.0.2.10",
            )
            stale_conn = _AuthorityConn(
                case_row=(True, interested, accepted_at),
                event_row=(replacement_authority, accepted_at),
                issue_row=(
                    issuance,
                    accepted_at,
                    material["document_id"],
                    material["document_sha256"],
                    "application/pdf",
                    654,
                ),
            )
            with self.assertRaises(HTTPException) as stale:
                case_authority.verify_active_authority_document_issue(
                    stale_conn,
                    case_id,
                    authority=replacement_authority,
                )
            self.assertEqual(stale.exception.status_code, 409)


class SyntheticStagingGuardTest(TestCase):
    def test_lab_mutations_require_isolated_synthetic_staging(self):
        safe = {
            "RTM_ENV": "staging",
            "RTM_DATA_NAMESPACE": "rtm-staging-synthetic",
            "RTM_SIDE_EFFECT_POLICY": "isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
        }
        with mock.patch.dict(os.environ, safe, clear=True):
            self.assertIsNone(
                rtm_staging_guards.require_isolated_synthetic_staging()
            )
        unsafe = dict(safe, RTM_ENV="production")
        with mock.patch.dict(os.environ, unsafe, clear=True):
            with self.assertRaises(HTTPException) as blocked:
                rtm_staging_guards.require_isolated_synthetic_staging()
            self.assertEqual(blocked.exception.status_code, 503)


class StripeSettlementBehaviorTest(TestCase):
    def _event(self, case_id: str, session_id: str) -> dict:
        return {
            "id": "evt_settlement_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_id,
                    "mode": "payment",
                    "payment_status": "paid",
                    "payment_intent": {"id": "pi_settlement_1"},
                    "amount_total": 5000,
                    "currency": "eur",
                    "metadata": {
                        "case_id": case_id,
                        "payment_stage": "final",
                        "billing_code": "DGT",
                        "service_code": "fine",
                        "authority_version": "legacy_final_catalog_v1",
                        "amount_cents": "",
                        "currency": "EUR",
                        "authority_material_sha256": "a" * 64,
                        "signed_document_attestation_sha256": "s" * 64,
                    },
                }
            },
        }

    def _intent(self, session_id: str) -> dict:
        return {
            "session": session_id,
            "payment_stage": "final",
            "billing_code": "DGT",
            "authoritative_service_code": "fine",
            "authority_version": "legacy_final_catalog_v1",
            "amount_cents": None,
            "stripe_amount_total": 5000,
            "currency": "EUR",
            "authority_material_sha256": "a" * 64,
            "signed_document_attestation_sha256": "s" * 64,
        }

    def test_legacy_final_session_requires_manual_reconciliation(self):
        case_id = str(uuid4())
        session_id = "cs_settlement_1"
        event = self._event(case_id, session_id)
        env = {
            "STRIPE_SECRET_KEY": "sk_test",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
        }

        connection = _BillingConn(
            case_row=("pending", session_id, "DGT"),
            intent=self._intent(session_id),
        )
        billing = _load_billing_for_event(event, _Engine(connection))
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(billing.stripe_webhook(_WebhookRequest()))
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertIn("conciliación manual", str(rejected.exception.detail))
        self.assertEqual(connection.events, [])

    def test_unbound_session_is_rejected(self):
        case_id = str(uuid4())
        incoming_session = "cs_incoming"
        conn = _BillingConn(
            case_row=("pending", "cs_other", "DGT"),
            intent=self._intent(incoming_session),
        )
        billing = _load_billing_for_event(
            self._event(case_id, incoming_session), _Engine(conn)
        )
        with mock.patch.dict(
            os.environ,
            {
                "STRIPE_SECRET_KEY": "sk_test",
                "STRIPE_WEBHOOK_SECRET": "whsec_test",
            },
            clear=False,
        ):
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(billing.stripe_webhook(_WebhookRequest()))
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(conn.events, [])


class SourceHardeningContractTest(TestCase):
    def test_public_case_mutators_require_capability_and_signed_authority(self):
        cases = _source("cases.py")
        core_intake = _source("rtm_core/intake_router.py")
        self.assertGreaterEqual(cases.count('alias="X-RTM-Case-Token"'), 10)
        self.assertEqual(core_intake.count('alias="X-RTM-Case-Token"'), 3)
        self.assertEqual(core_intake.count("require_case_access_token("), 3)
        self.assertIn("class AuthorizationConsentIn", cases)
        self.assertIn("consent: Literal[True]", cases)
        self.assertIn("representation_confirmed: Literal[True]", cases)
        self.assertIn("build_case_authority_payload", cases)
        self.assertIn("case_authority_invalidated_by_identity_change", cases)
        self.assertIn("authorization_signed_candidate_stale", cases)
        self.assertIn("_mark_authorization_evidence_stale", cases)
        self.assertIn('capability_state("outbound_email").enabled', cases)

        for intake_path in ("analyze.py", "analyze_expediente.py"):
            intake_source = _source(intake_path)
            self.assertIn("issue_case_access_token", intake_source)
            self.assertIn('"case_access_token"', intake_source)

        signed = _function_source("cases.py", "_store_authorization_signed")
        self.assertIn("verify_active_case_authority", signed)
        self.assertIn("verify_active_authority_document_issue", signed)
        self.assertIn("require_authority_document_binding", signed)
        self.assertIn("build_authorization_signature_candidate_attestation", signed)
        self.assertIn("authorization_signature_candidate_uploaded", signed)
        self.assertNotIn("authorization_signed_uploaded", signed)
        self.assertIn("_validate_public_pdf", signed)
        self.assertNotIn("SET authorized", signed)

        receipt = _function_source("cases.py", "upload_receipt")
        self.assertIn("require_operator_case_access", receipt)
        self.assertNotIn("require_case_access_token", receipt)
        self.assertIn("_require_receipt_upload_state", receipt)
        self.assertIn("receipt_sha256", receipt)
        self.assertIn("_event_on_conn", receipt)
        self.assertIn("RETURNING id", receipt)

    def test_checkout_and_webhook_are_bound_and_replay_safe(self):
        checkout = _function_source("billing.py", "create_checkout")
        webhook = _function_source("billing.py", "stripe_webhook")
        self.assertIn("require_case_access_token", checkout)
        self.assertIn('require_http_capability("final_payments")', checkout)
        self.assertIn("la creación legacy está retirada", checkout)
        self.assertNotIn("_resolve_final_stripe_product", checkout)
        self.assertIn("stripe_session_id=:session_id", checkout)
        self.assertIn("checkout_session_created", checkout)
        self.assertIn("idempotency_key", checkout)
        self.assertNotIn("access_token=", checkout)
        self.assertIn('session_payment_status != "paid"', webhook)
        self.assertIn("pago final legacy requiere conciliación manual", webhook)
        self.assertIn("stored_session_id != session_id", webhook)
        self.assertIn("stripe_amount_total", webhook)
        self.assertIn('"replayed": True', webhook)
        self.assertIn("payment_status='pending'", webhook)

    def test_legacy_submitters_cannot_claim_external_success(self):
        active = _function_source("ops_operator_router.py", "submit_to_dgt")
        legacy = _source("ops_operator_submit_router.py")
        delivery = _function_source(
            "ops_operator_router.py", "send_complete_case_file"
        )
        approve = _function_source("ops_operator_router.py", "approve_case")
        self.assertIn("status_code=410", active)
        self.assertNotIn("_set_status", active)
        self.assertNotIn("requests", legacy)
        self.assertNotIn("pick_submitter", legacy)
        self.assertIn("status_code=410", legacy)
        self.assertIn("ready_for_delivery", delivery)
        self.assertNotIn('"sent"', delivery)
        self.assertIn("verify_signed_case_authority", approve)
        self.assertIn("Falta un recurso final congelado", approve)
        self.assertIn("test_mode", approve)

    def test_presented_views_and_lab_mutators_are_evidence_gated(self):
        ops = _source("ops.py")
        self.assertIn("PRESENTED_EVIDENCE_SQL", ops)
        self.assertIn("receipt_sha256", ops)
        self.assertIn("verify_signed_case_authority", ops)
        self.assertIn("operator_registration_attestation", ops)
        self.assertIn("require_isolated_synthetic_staging()", ops)
        self.assertIn("Se requiere un expediente test_mode", ops)
        self.assertNotIn("No permitido en test_mode", ops)

    def test_automation_rehashes_pdf_and_blocks_ambiguous_retries(self):
        automation = _source("ops_automation.py")
        self.assertIn('require_http_capability("external_submission")', automation)
        self.assertIn(
            "_require_external_submission_capability()",
            _function_source("ops_automation.py", "submit_case_fully_automatic"),
        )
        self.assertIn(
            "_require_external_submission_capability()",
            _function_source("ops_automation.py", "tick"),
        )
        self.assertIn('state.reason != "explicitly_enabled"', automation)
        self.assertIn("actual_pdf_sha256 = hashlib.sha256(pdf_bytes)", automation)
        self.assertIn('pdf["pdf_sha256"]', automation)
        self.assertIn('"rendered_content_sha256"', automation)
        self.assertIn('"pdf_sha256"', automation)
        self.assertIn("verify_signed_case_authority", automation)
        self.assertIn("hmac.compare_digest", automation)
        self.assertIn("_verified_submission_evidence", automation)
        self.assertIn("receipt_sha256", automation)
        self.assertIn("submission_outcome_unknown", automation)
        self.assertIn("reconciliation_required", automation)

    def test_core_generation_revalidates_authority_before_ready_to_submit(self):
        gateway = _source("rtm_core/generation_gateway.py")
        case_meta = _function_source("rtm_core/generation_gateway.py", "_case_meta")
        approve = _function_source(
            "rtm_core/generation_gateway.py", "approve_resource_for_submission"
        )
        self.assertIn("verify_signed_case_authority", case_meta)
        self.assertIn('"_active_case_authority"', case_meta)
        self.assertIn('{"final_ready", "ready_to_submit"}', approve)
        self.assertIn("AND approved_at IS NULL", approve)
        self.assertIn("AND status='final_ready' RETURNING id", approve)
        self.assertIn("case_authority_material_sha256", gateway)
        self.assertIn("pdf_hash = _sha256(pdf_bytes)", gateway)
        self.assertIn('"pdf_sha256": pdf_hash', gateway)
        self.assertIn("size_bytes, sha256, created_at", gateway)

    def test_a1s_operational_steps_revalidate_latest_authority(self):
        service = _source("rtm_connect/human_filing_service.py")
        begin = _function_source(
            "rtm_connect/human_filing_service.py", "begin_review"
        )
        attest = _function_source(
            "rtm_connect/human_filing_service.py", "attest_review"
        )
        outcome = _function_source(
            "rtm_connect/human_filing_service.py", "record_outcome"
        )
        self.assertIn("_task_authority(conn, row)", begin)
        self.assertIn("_task_authority(conn, row)", attest)
        self.assertIn("authorization_version", attest)
        self.assertIn("request_sha256", attest)
        self.assertIn("_task_authority(conn, row)", outcome)
        self.assertIn("authorization_id", outcome)
        self.assertIn("authorization_version_changed", service)


if __name__ == "__main__":
    import unittest

    unittest.main()
