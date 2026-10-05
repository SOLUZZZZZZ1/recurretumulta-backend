"""Append-only documentary additions and explicit replacement of a facts revision."""
from __future__ import annotations

import hmac
from fastapi import HTTPException
from sqlalchemy import text
from rtm_core import authority_repository as authority
from rtm_core.contracts import PreviewStatus

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


def revision_available(state, facts, preview):
    return bool(facts and not state["blockers"] and
                (not preview or preview.status in {PreviewStatus.DRAFT, PreviewStatus.INVALIDATED,
                                                  PreviewStatus.CHANGES_REQUIRED}))


def replace_facts(conn, *, case_id, facts, actor, reason, document_ids=None):
    """The old frozen payload is preserved, with downstream invalidation and lineage."""
    if len(reason.strip()) < 10:
        raise HTTPException(422, "Describe el motivo de la nueva revisión")
    ids = list(facts.facts.source_document_ids)
    ids.extend(doc for doc in (document_ids or []) if doc not in ids)
    snapshot = authority.validated_model_copy(
        facts.facts, frozen=False, source_document_ids=ids,
        created_at=authority.utcnow(), supersedes_version=facts.facts.version)
    authority.invalidate_validated_facts(conn, case_id, facts.id, actor, reason)
    saved = authority.create_validated_facts(conn, case_id=case_id, facts=snapshot,
                                             created_by=actor, supersedes_id=facts.id)
    authority._append_event(conn, case_id, "rtm_study_facts_revision_opened", {
        "actor": actor, "reason": reason, "previous_facts_id": facts.id,
        "previous_payload_sha256": facts.payload_sha256, "facts_id": saved.id,
        "payload_sha256": saved.payload_sha256, "new_source_document_ids": document_ids or [],
    })
    return saved


def append_document(conn, *, case_id, body, actor, data, validated, uploaded):
    from rtm_core.study import load_study
    from b2_storage import upload_bytes
    before, facts, _, previous = load_study(conn, case_id, for_update=True)
    if not hmac.compare_digest(before["state_sha256"], body.expected_state_sha256):
        raise HTTPException(409, "El estudio ha cambiado. Recarga antes de incorporar el documento")
    if not revision_available(before, facts, previous):
        raise HTTPException(409, "El estado del estudio no admite documentación adicional")
    duplicate = conn.execute(text(
        "SELECT id FROM documents WHERE case_id=:case AND kind='original' AND sha256=:sha LIMIT 1"
    ), {"case": case_id, "sha": validated.sha256}).fetchone()
    if duplicate:
        raise HTTPException(409, "Ese documento ya está incorporado; no se ha abierto otra versión")
    bucket, key = upload_bytes(case_id, "original", data, validated.extension, validated.mime)
    uploaded.append((bucket, key))
    row = conn.execute(text("""
        INSERT INTO documents(case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
        VALUES (:case,'original',:bucket,:key,:sha,:mime,:size,NOW()) RETURNING id
    """), {"case": case_id, "bucket": bucket, "key": key, "sha": validated.sha256,
           "mime": validated.mime, "size": validated.size_bytes}).fetchone()
    document_id = str(row[0])
    saved = replace_facts(conn, case_id=case_id, facts=facts, actor=actor, reason=body.reason,
                          document_ids=[document_id])
    authority._append_event(conn, case_id, "rtm_study_document_added", {
        "actor": actor, "document_id": document_id, "sha256": validated.sha256,
        "size_bytes": validated.size_bytes, "mime": validated.mime, "reason": body.reason,
        "previous_facts_id": facts.id, "facts_id": saved.id,
        "analysis_deferred": True, "personal_review_pending": True,
    })
    after, *_ = load_study(conn, case_id)
    return {**after, "completed_action": "add_document", "previous_state_sha256": before["state_sha256"],
            "added_document": {"id": document_id, "sha256": validated.sha256}}
