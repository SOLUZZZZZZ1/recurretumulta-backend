"""Immutable, pending-review working documents in existing event/document custody.

This is custody, not legal approval: it neither changes facts nor invokes the
final generator. Metadata is signed separately so history never loads every
full document. Staging saves only the server proposal; the explicit local mode
also accepts operator text/corrections with frozen sources and head preconditions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from uuid import UUID, uuid4
from typing import Literal

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
CASE_VERSION = "rtm_working_document_versions_v2"
SELECTION_EVENT = "rtm_working_document_selected"


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


class SelectCaseVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_latest_id: str | None
    expected_selection_revision: str | None
    version_id: str

    @field_validator("expected_latest_id", "expected_selection_revision", "version_id")
    @classmethod
    def canonical(cls, value):
        return SaveWorkingDocumentBody.canonical_id(value)


class SaveCaseDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["case_draft"]
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_latest_id: str | None
    expected_selection_revision: str | None
    based_on_id: str | None
    content: str = Field(min_length=1, max_length=65536)
    used_document_ids: list[str] = Field(max_length=100)
    change_reason: str = Field(min_length=5, max_length=1000)
    draft_acknowledged: Literal[True]

    @field_validator("expected_latest_id", "expected_selection_revision", "based_on_id")
    @classmethod
    def canonical(cls, value):
        return SaveWorkingDocumentBody.canonical_id(value)

    @field_validator("used_document_ids")
    @classmethod
    def documents(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Referencias documentales duplicadas")
        for value in values:
            SaveWorkingDocumentBody.canonical_id(value)
        return values

    @field_validator("content", "change_reason")
    @classmethod
    def plain_text(cls, value):
        from rtm_core.upload_security import validate_draft_text, UploadSecurityError
        try:
            return validate_draft_text(value)
        except UploadSecurityError as exc:
            raise ValueError(str(exc)) from exc


def _local(grant):
    from rtm_core.case_working_document import CaseDraftGrant
    return isinstance(grant, CaseDraftGrant)


def _selection(conn, case_id, history):
    rows = conn.execute(text("""
        SELECT id,payload FROM events WHERE case_id=:case_id AND type=:event
        ORDER BY created_at,id LIMIT 1001
    """), {"case_id": case_id, "event": SELECTION_EVENT}).fetchall()
    if len(rows) > 1000:
        raise HTTPException(409, "Se ha alcanzado el límite de selecciones de esta prueba")
    previous, previous_hash, selected = None, None, None
    ids = {entry["id"] for entry in history}
    for row in rows:
        item = _verify_envelope(row[1] or {}, detail="La selección guardada no es verificable")
        if (item.get("format") != CASE_VERSION or item.get("case_id") != case_id
                or item.get("id") != str(row[0]) or not _valid_uuid(item.get("id"))
                or item.get("previous_id") != previous or item.get("previous_sha256") != previous_hash
                or item.get("version_id") not in ids or item.get("latest_id") not in ids
                or not str(item.get("created_by", "")).startswith("operator:")
                or not _valid_uuid(str(item.get("created_by", ""))[9:])):
            raise HTTPException(409, "El historial de selección no es verificable")
        previous, previous_hash, selected = item["id"], _digest(item), item["version_id"]
    return {"revision": previous, "sha256": previous_hash, "version_id": selected, "count": len(rows)}


def _case_history(conn, case_id, history):
    if any(entry["format"] != CASE_VERSION for entry in history):
        raise HTTPException(409, "El expediente tiene versiones de otro flujo de revisión")
    selection = _selection(conn, case_id, history)
    return {"ok": True, "version": CASE_VERSION, "case_id": case_id,
        "latest_id": history[-1]["id"] if history else None,
        "selected_id": selection["version_id"], "selection_revision": selection["revision"],
        "can_save": len(history) < MAX_VERSIONS and selection["count"] < 1000,
        "history": [{**item, "persisted": True} for item in reversed(history)],
        "synthetic_only": True, "final_resource_generated": False, "operator_export_allowed": False}


def _check_head(history, selection, expected_latest_id, expected_selection_revision):
    if ((history[-1]["id"] if history else None) != expected_latest_id
            or selection["revision"] != expected_selection_revision):
        raise HTTPException(409, "Hay una versión o selección posterior. Conserva tu texto y actualiza el historial")


def _append_selection(conn, case_id, grant, history, selection, version_id):
    if selection["count"] >= 1000:
        raise HTTPException(409, "Se ha alcanzado el límite de selecciones de esta prueba")
    event_id, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
    material = {"format": CASE_VERSION, "id": event_id, "case_id": case_id,
        "previous_id": selection["revision"], "previous_sha256": selection["sha256"],
        "version_id": version_id, "latest_id": history[-1]["id"],
        "created_at": now, "created_by": f"operator:{grant.operator_id}"}
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at)
        VALUES (:id,:case_id,:event,CAST(:payload AS JSONB),:now)
    """), {"id": event_id, "case_id": case_id, "event": SELECTION_EVENT,
            "payload": json.dumps(_signed_envelope(material)), "now": now})


def select_version(conn, *, case_id, grant, body: SelectCaseVersionBody):
    if not _local(grant):
        raise HTTPException(404, "Selección no disponible en este flujo")
    _context(conn, case_id, grant)
    history = _history(conn, case_id)
    state = _selection(conn, case_id, history)
    _check_head(history, state, body.expected_latest_id, body.expected_selection_revision)
    # Read verifies snapshot AND custody before selecting an existing version.
    read_version(conn, case_id=case_id, version_id=body.version_id, grant=grant)
    _append_selection(conn, case_id, grant, history, state, body.version_id)
    return _case_history(conn, case_id, history)


def save_case_version(conn, *, case_id, grant, body, uploaded, correction=None):
    from rtm_core.case_working_document import load_case_document
    if not _local(grant):
        raise HTTPException(404, "Corrección no disponible en este flujo")
    if correction:
        from rtm_core.upload_security import validate_draft_correction, UploadSecurityError
        data, supplied = correction
        try:
            exact_content, validated = validate_draft_correction(data, filename=supplied.filename,
                declared_mime=supplied.mime, case_id=case_id, based_on_id=body.based_on_id)
        except UploadSecurityError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        if body.based_on_id is None or exact_content != body.content or validated != supplied:
            raise HTTPException(409, "La corrección no coincide con el contenido o su procedencia")
    context_hash = _context(conn, case_id, grant)
    history = _history(conn, case_id)
    _case_history(conn, case_id, history)
    state = _selection(conn, case_id, history)
    _check_head(history, state, body.expected_latest_id, body.expected_selection_revision)
    if len(history) >= MAX_VERSIONS or state["count"] >= 1000:
        raise HTTPException(409, "Se ha alcanzado el límite de versiones o selecciones")
    if body.based_on_id is None:
        projection = load_case_document(conn, case_id, grant)
    else:
        projection = read_version(conn, case_id=case_id, version_id=body.based_on_id, grant=grant)["snapshot"]
    if projection["source_sha256"] != body.expected_source_sha256:
        raise HTTPException(409, "Las fuentes no corresponden al borrador que se está editando")
    if not set(body.used_document_ids).issubset({doc["id"] for doc in projection["sources"]["documents"]}):
        raise HTTPException(409, "Una referencia documental no pertenece a las fuentes del expediente")
    snapshot = deepcopy(projection)
    snapshot.update(content=body.content, used_document_ids=list(body.used_document_ids))
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, "Las fuentes del borrador superan el límite permitido")
    pdf = working_document_pdf(snapshot, body.expected_source_sha256)
    if not pdf.startswith(b"%PDF-") or not 5 < len(pdf) <= MAX_PDF_BYTES:
        raise HTTPException(409, "No se ha podido preparar un PDF verificable")
    latest = history[-1] if history else None
    material = {"format": CASE_VERSION, "id": str(uuid4()), "case_id": case_id,
        "sequence": len(history) + 1, "previous_id": latest["id"] if latest else None,
        "previous_material_sha256": _digest(latest) if latest else None,
        "based_on_id": body.based_on_id, "status": "working_draft", "synthetic_only": True,
        "final_resource_generated": False, "operator_export_allowed": False,
        "title": snapshot["title"], "document_kind": snapshot["document_kind"],
        "source_sha256": snapshot["source_sha256"], "snapshot_sha256": _digest(snapshot),
        "content_sha256": hashlib.sha256(body.content.encode("utf-8")).hexdigest(),
        "context_sha256": context_hash, "created_by": f"operator:{grant.operator_id}",
        "created_at": datetime.now(timezone.utc).isoformat(), "change_reason": body.change_reason,
        "provenance": "external_correction" if correction else "operator_edit",
        "pdf": {"document_id": str(uuid4()), "sha256": hashlib.sha256(pdf).hexdigest(), "size_bytes": len(pdf)}}
    if correction:
        data, validated = correction
        material["correction"] = {"document_id": str(uuid4()), "filename": validated.filename,
            "sha256": validated.sha256, "size_bytes": validated.size_bytes, "mime": validated.mime}
        _signed_envelope(material)  # Fail before uploading if signing is unavailable.
        bucket, key = storage.upload_bytes(case_id, "external_revision", data, ".txt", "text/plain")
        uploaded.append((bucket, key))
        conn.execute(text("""
            INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
            VALUES (:id,:case_id,:kind,:bucket,:key,:sha,:mime,:size,:now)
        """), {"id": material["correction"]["document_id"], "case_id": case_id, "kind": "external_revision",
            "bucket": bucket, "key": key, "sha": validated.sha256, "mime": "text/plain",
            "size": len(data), "now": material["created_at"]})
    _persist(conn, case_id, material, snapshot, pdf, uploaded)
    history = _history(conn, case_id)
    if history[-1]["id"] != material["id"]:
        raise HTTPException(409, "No se pudo confirmar el guardado")
    _append_selection(conn, case_id, grant, history, state, material["id"])
    return {**_case_history(conn, case_id, history), "saved_id": material["id"], "reused": False}


def _verify_correction(conn, case_id, material):
    if material["provenance"] != "external_correction":
        return
    from rtm_core.upload_security import MAX_DRAFT_TEXT_BYTES
    doc = material.get("correction") or {}
    row = conn.execute(text("""
        SELECT b2_bucket,b2_key,sha256,size_bytes,mime FROM documents
        WHERE id=:id AND case_id=:case_id AND kind=:kind
    """), {"id": doc.get("document_id"), "case_id": case_id, "kind": "external_revision"}).first()
    if (row is None or row[2] != doc.get("sha256") or row[3] != doc.get("size_bytes")
            or row[4] != "text/plain" or not str(row[1]).startswith(f"cases/{case_id}/external_revision/")):
        raise HTTPException(409, "La corrección no pertenece a la versión o al expediente")
    raw = storage.download_bytes_limited(row[0], row[1], max_bytes=MAX_DRAFT_TEXT_BYTES, case_id=case_id)
    if len(raw) != row[3] or hashlib.sha256(raw).hexdigest() != row[2]:
        raise HTTPException(409, "La corrección guardada no supera la comprobación de integridad")


def _context(conn, case_id: str, grant: RehearsalGrant) -> str:
    from rtm_core.case_working_document import CaseDraftGrant, context
    if isinstance(grant, CaseDraftGrant):
        return context(conn, case_id, grant)
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
            material.get("format") in {VERSION, CASE_VERSION} and material.get("case_id") == case_id
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
                    ("source_sha256", "snapshot_sha256", "content_sha256",
                     "context_sha256" if material.get("format") == CASE_VERSION else "authority_sha256"))
            and _valid_uuid(pdf.get("document_id")) and HASH.fullmatch(str(pdf.get("sha256", "")))
            and type(pdf.get("size_bytes")) is int and 5 < pdf["size_bytes"] <= MAX_PDF_BYTES
        )
        if not valid:
            raise HTTPException(409, "La cadena de versiones guardadas no es verificable")
        if material["format"] == CASE_VERSION:
            if (material.get("operator_export_allowed") is not False
                    or material.get("based_on_id") not in {None, *(item["id"] for item in history)}
                    or material.get("provenance") not in {"operator_edit", "external_correction"}
                    or not isinstance(material.get("change_reason"), str)):
                raise HTTPException(409, "La procedencia de la versión no es verificable")
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
    if _local(grant):
        return _case_history(conn, case_id, _history(conn, case_id))
    return _public_history(case_id, _history(conn, case_id), authority_hash)


def save_version(conn, *, case_id, grant, body: SaveWorkingDocumentBody, uploaded):
    if _local(grant):
        if not isinstance(body, SaveCaseDocumentBody):
            raise HTTPException(422, "El borrador local requiere contenido y procedencia explícitos")
        return save_case_version(conn, case_id=case_id, grant=grant, body=body, uploaded=uploaded)
    if not isinstance(body, SaveWorkingDocumentBody):
        raise HTTPException(422, "Este ensayo solo admite la propuesta del servidor")
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
    _persist(conn, case_id, material, snapshot, pdf, uploaded)
    after = _history(conn, case_id)
    if not after or after[-1]["id"] != version_id:
        raise HTTPException(409, "No se pudo verificar la versión registrada")
    return {**_public_history(case_id, after, authority_hash), "saved_id": version_id, "reused": False}


def _persist(conn, case_id, material, snapshot, pdf, uploaded):
    """Shared append-only event and custody write for both working-document modes."""
    envelope = _signed_envelope(material)  # Check signing capability before uploading.
    bucket, key = storage.upload_bytes(case_id, DOCUMENT_KIND, pdf, ".pdf", "application/pdf")
    uploaded.append((bucket, key))
    conn.execute(text("""
        INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
        VALUES (:id,:case_id,:kind,:bucket,:key,:sha,'application/pdf',:size,:now)
    """), {"id": material["pdf"]["document_id"], "case_id": case_id, "kind": DOCUMENT_KIND, "bucket": bucket,
           "key": key, "sha": material["pdf"]["sha256"], "size": len(pdf), "now": material["created_at"]})
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at)
        VALUES (:id,:case_id,:event,CAST(:payload AS JSONB),:now)
    """), {"id": material["id"], "case_id": case_id, "event": EVENT,
           "payload": json.dumps({"envelope": envelope, "snapshot": snapshot}, ensure_ascii=False), "now": material["created_at"]})


def read_version(conn, *, case_id, version_id, grant):
    authority_hash = _context(conn, case_id, grant)
    material = next((item for item in _history(conn, case_id) if item["id"] == version_id), None)
    if material is None:
        raise HTTPException(404, "Versión no encontrada en este expediente")
    if material["format"] != (CASE_VERSION if _local(grant) else VERSION):
        raise HTTPException(409, "La versión pertenece a otro flujo de revisión")
    row = conn.execute(text("""
        SELECT payload->'snapshot' FROM events WHERE id=:id AND case_id=:case_id AND type=:event
    """), {"id": version_id, "case_id": case_id, "event": EVENT}).first()
    snapshot = row[0] if row else None
    if (not isinstance(snapshot, dict) or _digest(snapshot) != material["snapshot_sha256"]
            or snapshot.get("case_id") != case_id or snapshot.get("source_sha256") != material["source_sha256"]
            or not isinstance(snapshot.get("content"), str)
            or hashlib.sha256(snapshot["content"].encode("utf-8")).hexdigest() != material["content_sha256"]):
        raise HTTPException(409, "El contenido guardado no coincide con su versión")
    if _local(grant):
        _stored_pdf(conn, case_id, material)  # Verify custody without exposing binary exports.
        _verify_correction(conn, case_id, material)
        return {"ok": True, "version": CASE_VERSION, "case_id": case_id, "entry": material,
            "snapshot": snapshot, "persisted": True, "final_resource_generated": False,
            "operator_export_allowed": False, "custody_verified": True}
    return {"ok": True, "version": VERSION, "case_id": case_id, "entry": material,
        "snapshot": snapshot, "persisted": True, "final_resource_generated": False,
        "current_authority": material["authority_sha256"] == authority_hash}


def read_pdf(conn, *, case_id, version_id, grant):
    if _local(grant):
        raise HTTPException(403, "La exportación documental por operadores no está autorizada")
    result = read_version(conn, case_id=case_id, version_id=version_id, grant=grant)
    entry = result["entry"]
    return _stored_pdf(conn, case_id, entry), entry


def _stored_pdf(conn, case_id, entry):
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
    return data
