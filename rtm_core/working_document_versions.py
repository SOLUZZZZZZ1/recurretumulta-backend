"""Immutable, pending-review document snapshots for the isolated staging rehearsal.

This is custody, not legal approval: it neither changes facts nor invokes the
final generator. Metadata is signed separately so history never loads every
full document. Only the server's current projection can be saved.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

import b2_storage as storage
from case_authority import _signed_envelope, _verify_envelope, verify_signed_case_authority
from rtm_core import authority_repository as authority
from rtm_core.staging_rehearsal import RehearsalGrant, require_profile, verify_case_row
from rtm_core.working_document import _digest, load_working_document, working_document_pdf

VERSION = "rtm_working_document_versions_v1"
EVENT = "rtm_working_document_saved"
DOCUMENT_KIND = "rtm_working_document_pdf"
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_VERSIONS = 200
HASH = re.compile(r"^[a-f0-9]{64}$")


class SaveWorkingDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_latest_id: str | None

    @field_validator("expected_latest_id")
    @classmethod
    def canonical_id(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Identificador de versión no válido")
        return value


def _context(conn, case_id: str, grant: RehearsalGrant) -> str:
    require_profile()
    if not isinstance(grant, RehearsalGrant) or case_id != grant.case_id:
        raise HTTPException(404, "Expediente de ensayo no encontrado")
    row = conn.execute(text("""
        SELECT id,test_mode,department,case_type,interested_data,contact_email,
               contact_name,payment_status,authorized,status
        FROM cases WHERE id=:case_id FOR UPDATE
    """), {"case_id": case_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "Expediente de ensayo no encontrado")
    verify_case_row(row, grant)
    authority._require_authority_work_allowed(row)
    return _digest(verify_signed_case_authority(conn, case_id))


def _valid_uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _history(conn, case_id):
    rows = conn.execute(text("""
        SELECT id,payload->'envelope' AS envelope FROM events
        WHERE case_id=:case_id AND type=:event ORDER BY created_at,id LIMIT :limit
    """), {"case_id": case_id, "event": EVENT, "limit": MAX_VERSIONS + 1}).fetchall()
    if len(rows) > MAX_VERSIONS:
        raise HTTPException(409, "El historial requiere una revisión de custodia")
    history, previous, previous_hash = [], None, None
    for row in rows:
        material = _verify_envelope(row[1] or {}, detail="La versión guardada no es verificable")
        pdf = material.get("pdf") or {}
        valid = (
            material.get("format") == VERSION and material.get("case_id") == case_id
            and material.get("id") == str(row[0]) and _valid_uuid(material.get("id"))
            and material.get("previous_id") == previous
            and material.get("previous_material_sha256") == previous_hash
            and type(material.get("sequence")) is int and material["sequence"] == len(history) + 1
            and material.get("status") == "working_draft"
            and material.get("final_resource_generated") is False
            and material.get("synthetic_only") is True
            and isinstance(material.get("created_by"), str)
            and material["created_by"].startswith("operator:")
            and _valid_uuid(material["created_by"][9:])
            and all(HASH.fullmatch(str(material.get(key, ""))) for key in
                    ("source_sha256", "snapshot_sha256", "content_sha256", "authority_sha256"))
            and _valid_uuid(pdf.get("document_id")) and HASH.fullmatch(str(pdf.get("sha256", "")))
            and type(pdf.get("size_bytes")) is int and 5 < pdf["size_bytes"] <= MAX_PDF_BYTES
        )
        if not valid:
            raise HTTPException(409, "La cadena de versiones guardadas no es verificable")
        previous, previous_hash = material["id"], _digest(material)
        history.append(material)
    return history


def _public_history(case_id, history, authority_hash):
    return {"ok": True, "version": VERSION, "case_id": case_id,
        "latest_id": history[-1]["id"] if history else None,
        "final_resource_generated": False, "synthetic_only": True, "can_save": len(history) < MAX_VERSIONS,
        "history": [{**entry, "persisted": True,
                     "current_authority": entry["authority_sha256"] == authority_hash}
                    for entry in reversed(history)]}


def list_versions(conn, *, case_id, grant):
    authority_hash = _context(conn, case_id, grant)
    return _public_history(case_id, _history(conn, case_id), authority_hash)


def save_version(conn, *, case_id, grant, body: SaveWorkingDocumentBody, uploaded):
    # The case lock also serializes the normal facts/authority writers.
    authority_hash = _context(conn, case_id, grant)
    projection = load_working_document(conn, case_id)
    if (projection.get("case_id") != case_id or projection.get("status") != "working_draft"
            or projection.get("final_resource_generated") is not False
            or not hmac.compare_digest(projection["source_sha256"], body.expected_source_sha256)):
        raise HTTPException(409, "El escrito ha cambiado. Actualiza antes de guardar")
    history = _history(conn, case_id)
    latest = history[-1] if history else None
    # A retry after a lost response recovers the existing version, not a second PDF.
    if latest and latest["source_sha256"] == projection["source_sha256"] and latest["authority_sha256"] == authority_hash:
        return {**_public_history(case_id, history, authority_hash), "saved_id": latest["id"], "reused": True}
    if (latest["id"] if latest else None) != body.expected_latest_id:
        raise HTTPException(409, "Hay otra versión guardada. Actualiza el historial")
    if len(history) >= MAX_VERSIONS:
        raise HTTPException(409, "Se ha alcanzado el límite de versiones de este ensayo")
    snapshot = deepcopy(projection)
    serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, "El borrador supera el límite de esta vista")
    pdf = working_document_pdf(snapshot, body.expected_source_sha256)
    if not pdf.startswith(b"%PDF-") or not 5 < len(pdf) <= MAX_PDF_BYTES:
        raise HTTPException(409, "No se ha podido preparar un PDF verificable")
    version_id, document_id = str(uuid4()), str(uuid4())
    now = datetime.now(timezone.utc)
    material = {"format": VERSION, "id": version_id, "case_id": case_id,
        "sequence": len(history) + 1, "previous_id": latest["id"] if latest else None,
        "previous_material_sha256": _digest(latest) if latest else None,
        "status": "working_draft", "synthetic_only": True, "final_resource_generated": False,
        "title": snapshot["title"], "document_kind": snapshot["document_kind"],
        "source_sha256": snapshot["source_sha256"], "snapshot_sha256": _digest(snapshot),
        "content_sha256": hashlib.sha256(snapshot["content"].encode("utf-8")).hexdigest(),
        "authority_sha256": authority_hash, "created_by": f"operator:{grant.operator_id}",
        "created_at": now.isoformat(),
        "pdf": {"document_id": document_id, "sha256": hashlib.sha256(pdf).hexdigest(), "size_bytes": len(pdf)}}
    envelope = _signed_envelope(material)  # Check signing capability before uploading.
    bucket, key = storage.upload_bytes(case_id, DOCUMENT_KIND, pdf, ".pdf", "application/pdf")
    uploaded.append((bucket, key))
    conn.execute(text("""
        INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
        VALUES (:id,:case_id,:kind,:bucket,:key,:sha,'application/pdf',:size,:now)
    """), {"id": document_id, "case_id": case_id, "kind": DOCUMENT_KIND, "bucket": bucket,
           "key": key, "sha": material["pdf"]["sha256"], "size": len(pdf), "now": now})
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at)
        VALUES (:id,:case_id,:event,CAST(:payload AS JSONB),:now)
    """), {"id": version_id, "case_id": case_id, "event": EVENT,
           "payload": json.dumps({"envelope": envelope, "snapshot": snapshot}, ensure_ascii=False), "now": now})
    after = _history(conn, case_id)
    if not after or after[-1]["id"] != version_id:
        raise HTTPException(409, "No se pudo verificar la versión registrada")
    return {**_public_history(case_id, after, authority_hash), "saved_id": version_id, "reused": False}


def read_version(conn, *, case_id, version_id, grant):
    authority_hash = _context(conn, case_id, grant)
    material = next((item for item in _history(conn, case_id) if item["id"] == version_id), None)
    if material is None:
        raise HTTPException(404, "Versión no encontrada en este expediente")
    row = conn.execute(text("""
        SELECT payload->'snapshot' FROM events WHERE id=:id AND case_id=:case_id AND type=:event
    """), {"id": version_id, "case_id": case_id, "event": EVENT}).first()
    snapshot = row[0] if row else None
    if (not isinstance(snapshot, dict) or _digest(snapshot) != material["snapshot_sha256"]
            or snapshot.get("case_id") != case_id or snapshot.get("source_sha256") != material["source_sha256"]
            or not isinstance(snapshot.get("content"), str)
            or hashlib.sha256(snapshot["content"].encode("utf-8")).hexdigest() != material["content_sha256"]):
        raise HTTPException(409, "El contenido guardado no coincide con su versión")
    return {"ok": True, "version": VERSION, "case_id": case_id, "entry": material,
        "snapshot": snapshot, "persisted": True, "final_resource_generated": False,
        "current_authority": material["authority_sha256"] == authority_hash}


def read_pdf(conn, *, case_id, version_id, grant):
    result = read_version(conn, case_id=case_id, version_id=version_id, grant=grant)
    entry = result["entry"]
    row = conn.execute(text("""
        SELECT b2_bucket,b2_key,sha256,size_bytes,mime FROM documents
        WHERE id=:id AND case_id=:case_id AND kind=:kind
    """), {"id": entry["pdf"]["document_id"], "case_id": case_id, "kind": DOCUMENT_KIND}).first()
    if (row is None or row[2] != entry["pdf"]["sha256"] or row[3] != entry["pdf"]["size_bytes"]
            or row[4] != "application/pdf" or not str(row[1]).startswith(f"cases/{case_id}/{DOCUMENT_KIND}/")):
        raise HTTPException(409, "El PDF guardado no pertenece a esta versión")
    data = storage.download_bytes_limited(row[0], row[1], max_bytes=MAX_PDF_BYTES, case_id=case_id)
    if not data.startswith(b"%PDF-") or len(data) != row[3] or hashlib.sha256(data).hexdigest() != row[2]:
        raise HTTPException(409, "El PDF guardado no supera la comprobación de integridad")
    return data, entry
