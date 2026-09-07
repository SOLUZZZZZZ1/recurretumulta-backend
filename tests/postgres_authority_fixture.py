"""Synthetic signed authority evidence for real PostgreSQL integration tests.

The fixture writes the same linked envelopes consumed by the runtime verifier.
It never patches the verifier or accepts an ``authorized`` flag as evidence.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from case_authority import (
    AUTHORITY_VERSION,
    build_authority_document_issue_attestation,
    build_authorization_signature_candidate_attestation,
    build_authorization_signature_view_attestation,
    build_case_authority_payload,
    build_reviewed_signed_authority_attestation,
    verify_signed_case_authority,
)
from tests.postgres_security_fixtures import seed_supervisor_session
from rtm_core.operator_auth_repository import mark_operator_session_verified


def seed_signed_case_authority(conn, case_id: str) -> dict:
    """Seed a synthetic reviewed grant, then validate its complete SQL chain."""

    reviewer = seed_supervisor_session(conn)
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    interested = conn.execute(
        text("SELECT interested_data FROM cases WHERE id=CAST(:id AS UUID)"),
        {"id": case_id},
    ).scalar_one()
    conn.execute(
        text(
            "UPDATE cases SET authorized=TRUE, authorized_at=:now "
            "WHERE id=CAST(:id AS UUID)"
        ),
        {"id": case_id, "now": now},
    )

    def event(kind: str, payload: dict, event_id: str | None = None) -> str:
        event_id = event_id or str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO events(id, case_id, type, payload, created_at) "
                "VALUES (CAST(:id AS UUID), CAST(:case_id AS UUID), :kind, "
                "CAST(:payload AS JSONB), :now)"
            ),
            {
                "id": event_id,
                "case_id": case_id,
                "kind": kind,
                "payload": json.dumps(payload),
                "now": now,
            },
        )
        return event_id

    def document(kind: str) -> tuple[str, str, int]:
        document_id = str(uuid.uuid4())
        data = f"%PDF-1.4\nSynthetic CI {case_id} {kind}\n%%EOF\n".encode()
        digest = hashlib.sha256(data).hexdigest()
        conn.execute(
            text(
                "INSERT INTO documents(id, case_id, kind, b2_bucket, b2_key, "
                "sha256, mime, size_bytes, created_at) "
                "VALUES (CAST(:id AS UUID), CAST(:case_id AS UUID), :kind, "
                "'ci', :key, :digest, 'application/pdf', :size, :now)"
            ),
            {
                "id": document_id,
                "case_id": case_id,
                "kind": kind,
                "key": f"authority/{case_id}/{document_id}.pdf",
                "digest": digest,
                "size": len(data),
                "now": now,
            },
        )
        return document_id, digest, len(data)

    authority = build_case_authority_payload(
        case_id=case_id,
        interested=interested,
        accepted_at=timestamp,
        request_ip="192.0.2.10",
    )
    event("case_authorized", authority)

    issued_id, issued_digest, issued_size = document("authorization_pdf")
    issuance = build_authority_document_issue_attestation(
        case_id=case_id,
        authority_payload=authority,
        document_id=issued_id,
        document_sha256=issued_digest,
        size_bytes=issued_size,
        document_version=AUTHORITY_VERSION,
        document_nonce=str(uuid.uuid4()),
        issued_at=timestamp,
    )
    event("authorization_pdf_issued", issuance)

    signed_id, signed_digest, signed_size = document("authorization_signed")
    candidate = build_authorization_signature_candidate_attestation(
        case_id=case_id,
        authority_payload=authority,
        issuance_payload=issuance,
        document_id=signed_id,
        document_sha256=signed_digest,
        size_bytes=signed_size,
        uploaded_at=timestamp,
    )
    event("authorization_signature_candidate_uploaded", candidate)

    view = build_authorization_signature_view_attestation(
        case_id=case_id,
        candidate_payload=candidate,
        reviewer_actor=reviewer.actor,
        operator_session_id=reviewer.session_id,
        viewed_at=timestamp,
    )
    view_id = event("authorization_signature_candidate_viewed", view)
    if not mark_operator_session_verified(
        conn,
        session_id=reviewer.session_id,
        operator_id=reviewer.operator_id,
        now=now,
    ):
        raise AssertionError("Synthetic reviewer session must remain active")
    reauthentication_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO rtm_operator_access_events("
            "id, operator_id, session_id, event_type, result, auth_method, "
            "reason_code, occurred_at, created_at) "
            "VALUES (CAST(:id AS UUID), CAST(:operator_id AS UUID), "
            "CAST(:session_id AS UUID), 'auth.reauthenticated', 'success', "
            "'bearer+password', 'password_reverified', :now, :now)"
        ),
        {
            "id": reauthentication_id,
            "operator_id": reviewer.operator_id,
            "session_id": reviewer.session_id,
            "now": now,
        },
    )
    approval = build_reviewed_signed_authority_attestation(
        case_id=case_id,
        authority_payload=authority,
        issuance_payload=issuance,
        candidate_payload=candidate,
        reviewer_actor=reviewer.actor,
        operator_session_id=reviewer.session_id,
        view_event_id=view_id,
        view_payload=view,
        reauthentication_event_id=reauthentication_id,
        review_checklist={
            "reviewed_entire_document": True,
            "generated_document_matches": True,
            "identity_matches": True,
            "signature_present": True,
        },
        reviewed_at=timestamp,
    )
    event("authorization_signature_approved", approval)
    return verify_signed_case_authority(conn, case_id)
