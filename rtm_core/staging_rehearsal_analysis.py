"""One opt-in synthetic source, bound to the paid operator-owned rehearsal."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from fastapi import HTTPException
from sqlalchemy import text
from rtm_core.staging_rehearsal import (
    RADAR_SHA, RehearsalGrant, existing_case, fixture, require_profile,
)
from rtm_core.reanalysis_adapter import load_latest_reanalysis_snapshot


@dataclass(frozen=True)
class RehearsalAnalysisDocument:
    case_id: str
    document_id: str
    bucket: str
    key: str
    size_bytes: int

    def verify_documents(self, case_id, documents):
        expected = {"id": self.document_id, "bucket": self.bucket, "key": self.key,
                    "size_bytes": self.size_bytes, "mime": "application/pdf", "sha256": RADAR_SHA}
        if (case_id != self.case_id or len(documents) != 1
                or any(documents[0].get(key) != value for key, value in expected.items())):
            raise HTTPException(409, "El original del ensayo ha cambiado")

    def verify_bytes(self, content):
        # A database flag or hash supplied by a caller is never sufficient.
        _, expected = fixture("radar")
        if content != expected:
            raise HTTPException(409, "Solo puede analizarse la notificacion ficticia exacta")


def prepare_rehearsal_analysis_document(conn, *, case_id, grant, scope):
    require_profile()
    if (not isinstance(grant, RehearsalGrant) or case_id != grant.case_id
            or not scope.individual_session or scope.role_code != "rtm.supervisor"
            or scope.operator_id != grant.operator_id
            or not {"ops.view", "ops.supervise"}.issubset(scope.permissions)):
        raise HTTPException(403, "La lectura requiere al supervisor titular del ensayo")
    if not existing_case(grant, conn):
        raise HTTPException(404, "Expediente de ensayo no encontrado")
    _, expected = fixture("radar")
    rows = conn.execute(text(
        "SELECT CAST(id AS TEXT) AS id, b2_bucket, b2_key, mime, size_bytes, sha256 "
        "FROM documents WHERE case_id=:case_id AND kind='original' FOR UPDATE"
    ), {"case_id": case_id}).mappings().all()
    if (len(rows) != 1 or rows[0]["sha256"] != RADAR_SHA
            or rows[0]["size_bytes"] != len(expected) or rows[0]["mime"] != "application/pdf"
            or not rows[0]["b2_bucket"] or not rows[0]["b2_key"]):
        raise HTTPException(409, "El ensayo requiere un unico original ficticio verificado")
    doc = rows[0]
    return RehearsalAnalysisDocument(case_id, doc["id"], doc["b2_bucket"], doc["b2_key"], len(expected))


def previous_rehearsal_analysis(conn, document):
    try:
        wrapper, event = load_latest_reanalysis_snapshot(conn, document.case_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    if (wrapper.get("completion_status") != "completed"
            or wrapper.get("source_document_ids") != [document.document_id]
            or wrapper.get("size_bytes") != document.size_bytes
            or wrapper.get("sha256") != hashlib.sha256(RADAR_SHA.encode()).hexdigest()
            or event.get("ok") is not True):
        raise HTTPException(409, "La lectura anterior no corresponde al original del ensayo")
    return event
