# ops.py — Panel Operador (cola + docs + logs + justificante)
import json
import os
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

router = APIRouter(prefix="/ops", tags=["ops"])


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _require_operator(x_operator_token: Optional[str]):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


@router.get("/queue")
def queue(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    status: str = Query("ready_to_submit"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    c.id,
                    c.status,
                    c.payment_status,
                    c.product_code,
                    c.contact_email,
                    c.created_at,
                    c.updated_at
                FROM cases c
                WHERE c.status = :status
                ORDER BY c.updated_at ASC
                LIMIT :limit
                """
            ),
            {"status": status, "limit": limit},
        ).fetchall()

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "case_id": str(r[0]),
                "status": r[1],
                "payment_status": r[2],
                "product_code": r[3],
                "contact_email": r[4],
                "created_at": r[5],
                "updated_at": r[6],
            }
        )

    return {"ok": True, "status": status, "count": len(items), "items": items}


@router.get("/cases/{case_id}/documents")
def list_documents(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
) -> Dict[str, Any]:
    """Lista documentos del expediente (operador)."""
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at
                FROM documents
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "kind": r[0],
                "bucket": r[1],
                "key": r[2],
                "mime": r[3],
                "size_bytes": int(r[4] or 0),
                "created_at": r[5],
            }
        )

    return {"ok": True, "case_id": case_id, "items": items}


@router.get("/cases/{case_id}/events")
def list_events(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(200, ge=1, le=500),
) -> Dict[str, Any]:
    """Logs del expediente (operador)."""
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
                LIMIT :limit
                """
            ),
            {"case_id": case_id, "limit": limit},
        ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "type": r[0],
                "payload": r[1],
                "created_at": r[2],
            }
        )

    return {"ok": True, "case_id": case_id, "items": items}


@router.post("/cases/{case_id}/mark-ready")
def mark_ready(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """Marca listo para presentar (requiere paid)."""
    _require_operator(x_operator_token)
@router.post("/login")
def ops_login(pin: str = Form(...)):
    expected = (os.getenv("OPERATOR_PIN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_PIN no configurado")

    if pin.strip() != expected:
        raise HTTPException(status_code=401, detail="PIN incorrecto")

    # Devuelve el token real para guardarlo en localStorage
    return {"ok": True, "token": _env("OPERATOR_TOKEN")}


    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payment_status, status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        payment_status, current_status = row[0], row[1]
        if payment_status != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido")

        conn.execute(
            text("UPDATE cases SET status='ready_to_submit', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (:case_id, 'ops_mark_ready', CAST(:payload AS JSONB), NOW())"""
            ),
            {"case_id": case_id, "payload": json.dumps({"from": current_status, "to": "ready_to_submit", "note": note})},
        )

    return {"ok": True, "case_id": case_id, "status": "ready_to_submit"}


@router.post("/cases/{case_id}/mark-submitted")
def mark_submitted(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    channel: str = Form("DGT"),
    registro: Optional[str] = Form(default=None),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """Marca presentado (requiere paid)."""
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payment_status, status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        payment_status, current_status = row[0], row[1]
        if payment_status != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido")

        conn.execute(
            text("UPDATE cases SET status='submitted', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (:case_id, 'ops_mark_submitted', CAST(:payload AS JSONB), NOW())"""
            ),
            {"case_id": case_id, "payload": json.dumps({"from": current_status, "to": "submitted", "channel": channel, "registro": registro, "note": note})},
        )

    return {"ok": True, "case_id": case_id, "status": "submitted"}


@router.post("/cases/{case_id}/upload-justificante")
async def upload_justificante(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    file: UploadFile = File(...),
    kind: str = Form("justificante_presentacion"),
) -> Dict[str, Any]:
    """Sube justificante a B2 y lo registra en documents + event (requiere paid)."""
    _require_operator(x_operator_token)

    filename = (file.filename or "").strip().lower()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename requerido")

    content_type = (file.content_type or "application/octet-stream").strip()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payment_status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")
        if (row[0] or "") != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido")

        ext = os.path.splitext(filename)[1] or ".bin"
        b2_bucket, b2_key = upload_bytes(
            case_id,
            "justificantes",
            data,
            ext=ext,
            content_type=content_type,
        )

        conn.execute(
            text(
                """INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                   VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())"""
            ),
            {"case_id": case_id, "kind": kind, "b2_bucket": b2_bucket, "b2_key": b2_key, "mime": content_type, "size_bytes": len(data)},
        )

        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (:case_id, 'justificante_uploaded', CAST(:payload AS JSONB), NOW())"""
            ),
            {"case_id": case_id, "payload": json.dumps({"kind": kind, "bucket": b2_bucket, "key": b2_key, "mime": content_type, "size_bytes": len(data)})},
        )

    return {"ok": True, "case_id": case_id, "kind": kind, "bucket": b2_bucket, "key": b2_key}
