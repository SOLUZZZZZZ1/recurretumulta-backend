import os
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from database import get_engine
from b2_storage import download_bytes

router = APIRouter(prefix="/ops", tags=["ops"])


# =========================================================
# SEGURIDAD OPERADOR
# =========================================================
def _require_operator(x_operator_token: str | None):
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if not x_operator_token or x_operator_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


# =========================================================
# LISTADO DE DOCUMENTOS DE UN EXPEDIENTE
# =========================================================
@router.get("/cases/{case_id}/documents")
def list_case_documents(
    case_id: str,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, kind, mime, size_bytes, created_at
                FROM documents
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "mime": r[2],
            "size_bytes": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


# =========================================================
# 🔥 DESCARGA SEGURA DE DOCUMENTOS (NUEVO)
# =========================================================
@router.get("/documents/{doc_id}/download")
def download_document(
    doc_id: str,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT b2_bucket, b2_key, mime
                FROM documents
                WHERE id = :id
                """
            ),
            {"id": doc_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    bucket, key, mime = row
    data = download_bytes(bucket, key)

    filename = key.split("/")[-1]

    return StreamingResponse(
        iter([data]),
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# =========================================================
# LISTADO DE EVENTOS DE UN EXPEDIENTE
# =========================================================
@router.get("/cases/{case_id}/events")
def list_case_events(
    case_id: str,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT type, payload, created_at
                FROM events
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    return [
        {
            "type": r[0],
            "payload": r[1],
            "created_at": r[2],
        }
        for r in rows
    ]


# =========================================================
# COLA OPS (READY TO SUBMIT, ETC.)
# =========================================================
@router.get("/queue")
def ops_queue(
    status: str | None = None,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    query = "SELECT id, status, created_at FROM cases"
    params = {}

    if status:
        query += " WHERE status = :status"
        params["status"] = status

    query += " ORDER BY created_at DESC"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return [
        {
            "case_id": str(r[0]),
            "status": r[1],
            "created_at": r[2],
        }
        for r in rows
    ]
