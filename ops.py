# ops.py — Panel Operador (PIN + cola + docs + logs + presentado + justificante)
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


@router.post("/login")
def ops_login(pin: str = Form(...)) -> Dict[str, Any]:
    expected = (os.getenv("OPERATOR_PIN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_PIN no configurado")
    if pin.strip() != expected:
        raise HTTPException(status_code=401, detail="PIN incorrecto")
    return {"ok": True, "token": _env("OPERATOR_TOKEN")}


@router.get("/queue")
def queue(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    status: str = Query("ready_to_submit"),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Cola de casos para operador.

    IMPORTANTE:
    - Multi-documento recién creado por /analyze/expediente queda en status='uploaded'
      y debe poder verse en OPS para ejecutar Modo Dios y decidir.
    - 'ready_to_submit' se filtra a paid+authorized.
    - 'all' devuelve todo excepto cerrados/archivados.
    """
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        if status == "ready_to_submit":
            rows = conn.execute(
                text(
                    """
                    SELECT id, status, payment_status, product_code, contact_email, created_at, updated_at
                    FROM cases
                    WHERE status = 'ready_to_submit'
                      AND payment_status = 'paid'
                      AND authorized = TRUE
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()

        elif status == "all":
            rows = conn.execute(
                text(
                    """
                    SELECT id, status, payment_status, product_code, contact_email, created_at, updated_at
                    FROM cases
                    WHERE status NOT IN ('closed','archived')
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()

        else:
            # ✅ Aquí entran: uploaded / generated / submitted / etc.
            rows = conn.execute(
                text(
                    """
                    SELECT id, status, payment_status, product_code, contact_email, created_at, updated_at
                    FROM cases
                    WHERE status = :status
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"status": status, "limit": limit},
            ).fetchall()

    items = []
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

    return {"ok": True, "case_id": case_id, "documents": items}


@router.get("/cases/{case_id}/events")
def list_events(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
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

    items = [{"type": r[0], "payload": r[1], "created_at": r[2]} for r in rows]
    return {"ok": True, "case_id": case_id, "events": items}


def _require_paid_and_authorized(conn, case_id: str):
    row = conn.execute(
        text("SELECT payment_status, authorized FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    if (row[0] or "") != "paid":
        raise HTTPException(status_code=402, detail="Pago requerido")
    if not bool(row[1]):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")


@router.post("/cases/{case_id}/mark-submitted")
def mark_submitted(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    channel: str = Form("DGT"),
    registro: Optional[str] = Form(default=None),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        _require_paid_and_authorized(conn, case_id)

        row = conn.execute(
            text("SELECT status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        current_status = row[0] if row else ""

        conn.execute(
            text("UPDATE cases SET status='submitted', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_mark_submitted', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "from": current_status,
                        "to": "submitted",
                        "channel": channel,
                        "registro": registro,
                        "note": note,
                    }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "status": "submitted"}


@router.post("/cases/{case_id}/upload-justificante")
async def upload_justificante(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    file: UploadFile = File(...),
    kind: str = Form("justificante_presentacion"),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename requerido")

    content_type = (file.content_type or "application/octet-stream").strip()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    engine = get_engine()
    with engine.begin() as conn:
        _require_paid_and_authorized(conn, case_id)

        _, ext = os.path.splitext(filename.lower())
        ext = ext or ".bin"

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "justificantes",
            data,
            ext=ext,
            content_type=content_type,
        )

        conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())
                """
            ),
            {
                "case_id": case_id,
                "kind": kind,
                "b2_bucket": b2_bucket,
                "b2_key": b2_key,
                "mime": content_type,
                "size_bytes": len(data),
            },
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'justificante_uploaded', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "kind": kind,
                        "bucket": b2_bucket,
                        "key": b2_key,
                        "mime": content_type,
                        "size_bytes": len(data),
                    }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "kind": kind, "bucket": b2_bucket, "key": b2_key}
