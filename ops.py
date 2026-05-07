# ops.py — Panel Operador (PIN + cola + docs + logs + presentado + justificante + descarga segura)
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
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


# =========================================================
# B2 download helper (NO rompe aunque b2_storage no tenga download_bytes)
# =========================================================
def _download_bytes(bucket: str, key: str) -> bytes:
    import b2_storage

    for fn_name in ("download_bytes", "get_bytes", "b2_download_bytes", "download_file_bytes"):
        fn = getattr(b2_storage, fn_name, None)
        if callable(fn):
            return fn(bucket, key)
    raise HTTPException(status_code=500, detail="No existe función de descarga en b2_storage (download_bytes/get_bytes/...)")



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

    Mantiene el formato que el frontend espera:
    {"ok": True, "status": "...", "count": N, "items": [...]}
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
                SELECT id, kind, b2_bucket, b2_key, mime, size_bytes, created_at
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
                "id": str(r[0]),             # 👈 nuevo: id para descargar
                "kind": r[1],
                "bucket": r[2],
                "key": r[3],
                "mime": r[4],
                "size_bytes": int(r[5] or 0),
                "created_at": r[6],
            }
        )

    return {"ok": True, "case_id": case_id, "documents": items}


# ✅ NUEVO: descarga segura sin exponer B2
@router.get("/documents/{doc_id}/download")
def download_document(
    doc_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT b2_bucket, b2_key, mime FROM documents WHERE id=:id"),
            {"id": doc_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    bucket, key, mime = row
    data = _download_bytes(bucket, key)
    filename = (key or "documento").split("/")[-1] or "documento"

    return StreamingResponse(
        iter([data]),
        media_type=(mime or "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


def _case_exists(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT id FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    return str(row[0])


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


def _clean_kind(kind: str) -> str:
    allowed = {
        "justificante_presentacion",
        "instancia_firmada",
        "csv_registro",
        "resolucion",
        "requerimiento",
        "contestacion_ayuntamiento",
        "prueba_externa",
        "documento_externo",
        "recurso_presentado",
        "multa_presentada",
        "autorizacion_presentada",
    }
    k = (kind or "documento_externo").strip().lower().replace(" ", "_")
    return k if k in allowed else "documento_externo"


def _guess_ext_from_filename(filename: str, content_type: str = "") -> str:
    _, ext = os.path.splitext((filename or "").lower())
    if ext and 2 <= len(ext) <= 10:
        return ext
    ct = (content_type or "").lower().strip()
    if ct == "application/pdf":
        return ".pdf"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/png":
        return ".png"
    if ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return ".docx"
    return ".bin"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

@router.post("/cases/{case_id}/upload-external-document")
async def upload_external_document(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    file: UploadFile = File(...),
    kind: str = Form("documento_externo"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Adjunta documentación externa real al expediente:
    resoluciones, requerimientos, justificantes, instancias, CSV, pruebas externas, etc.

    No exige pago ni autorización: es una acción interna OPS para completar expediente.
    """
    _require_operator(x_operator_token)

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename requerido")

    content_type = (file.content_type or "application/octet-stream").strip()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    clean_kind = _clean_kind(kind)
    ext = _guess_ext_from_filename(filename, content_type)

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "external",
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
                "kind": clean_kind,
                "b2_bucket": b2_bucket,
                "b2_key": b2_key,
                "mime": content_type,
                "size_bytes": len(data),
            },
        )

        _append_event(
            conn,
            case_id,
            "external_document_uploaded",
            {
                "kind": clean_kind,
                "filename": filename,
                "bucket": b2_bucket,
                "key": b2_key,
                "mime": content_type,
                "size_bytes": len(data),
                "note": note or "",
                "at": _now_iso(),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "kind": clean_kind,
        "bucket": b2_bucket,
        "key": b2_key,
        "mime": content_type,
        "size_bytes": len(data),
    }


@router.post("/cases/{case_id}/register-manual-submission")
async def register_manual_submission(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    organismo: str = Form(...),
    registro: str = Form(...),
    csv: Optional[str] = Form(default=None),
    submitted_at: Optional[str] = Form(default=None),
    channel: str = Form("ayuntamiento_manual"),
    note: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
) -> Dict[str, Any]:
    """
    Registra una presentación hecha fuera de OPS, por ejemplo en la sede electrónica
    de un ayuntamiento.

    Diferencia clave:
    - NO llama a submitter.submit()
    - NO requiere automatización DGT/SIR
    - Guarda justificante si se adjunta
    - Marca el expediente como presentado_manual_ayuntamiento
    """
    _require_operator(x_operator_token)

    organismo_clean = (organismo or "").strip()
    registro_clean = (registro or "").strip()
    csv_clean = (csv or "").strip()
    channel_clean = (channel or "ayuntamiento_manual").strip()
    submitted_at_clean = (submitted_at or "").strip()

    if not organismo_clean:
        raise HTTPException(status_code=400, detail="Organismo requerido")
    if not registro_clean:
        raise HTTPException(status_code=400, detail="Número de registro requerido")

    document_info: Optional[Dict[str, Any]] = None

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        row = conn.execute(
            text("SELECT status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        previous_status = row[0] if row else ""

        if file is not None and (file.filename or "").strip():
            filename = (file.filename or "justificante_presentacion").strip()
            content_type = (file.content_type or "application/octet-stream").strip()
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="Justificante vacío")

            ext = _guess_ext_from_filename(filename, content_type)
            b2_bucket, b2_key = upload_bytes(
                case_id,
                "manual_submission",
                data,
                ext=ext,
                content_type=content_type,
            )

            conn.execute(
                text(
                    """
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:case_id, 'justificante_presentacion', :b2_bucket, :b2_key, :mime, :size_bytes, NOW())
                    """
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "mime": content_type,
                    "size_bytes": len(data),
                },
            )

            document_info = {
                "filename": filename,
                "bucket": b2_bucket,
                "key": b2_key,
                "mime": content_type,
                "size_bytes": len(data),
            }

        new_status = "presentado_manual_ayuntamiento"
        conn.execute(
            text("UPDATE cases SET status=:status, updated_at=NOW() WHERE id=:id"),
            {"id": case_id, "status": new_status},
        )

        _append_event(
            conn,
            case_id,
            "manual_submission_registered",
            {
                "from": previous_status,
                "to": new_status,
                "organismo": organismo_clean,
                "registro": registro_clean,
                "csv": csv_clean,
                "submitted_at": submitted_at_clean,
                "channel": channel_clean,
                "note": note or "",
                "document": document_info,
                "at": _now_iso(),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": new_status,
        "organismo": organismo_clean,
        "registro": registro_clean,
        "csv": csv_clean,
        "submitted_at": submitted_at_clean,
        "channel": channel_clean,
        "document": document_info,
    }


@router.post("/cases/{case_id}/force-ready-to-submit")
def force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Empuja un caso a ready_to_submit SOLO para laboratorio de pipeline (submissions/cola),
    sin depender de admisibilidad.
    Reglas:
    - Requiere OPERATOR_TOKEN
    - Requiere paid + authorized
    - NO permite test_mode
    - Deja event auditado
    """
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        # meta mínima
        row = conn.execute(
            text(
                "SELECT status, payment_status, authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()
        payment_status = (row[1] or "").strip()
        authorized = bool(row[2])
        test_mode = bool(row[3])

        if test_mode:
            raise HTTPException(status_code=409, detail="No se permite force-ready-to-submit en test_mode")

        if payment_status != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido (paid)")

        if not authorized:
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")

        if current_status in ("submitted", "closed", "archived"):
            raise HTTPException(status_code=409, detail=f"No se puede forzar desde status={current_status}")

        # actualizar a ready_to_submit
        conn.execute(
            text("UPDATE cases SET status='ready_to_submit', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        # auditar
        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_force_ready_to_submit', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {"from": current_status, "to": "ready_to_submit", "note": note}
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "status": "ready_to_submit"}

@router.post("/cases/{case_id}/lab-force-ready-to-submit")
def lab_force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    LAB (llave de oro): fuerza ready_to_submit SIN pago, solo para pruebas de pipeline.
    Reglas:
    - OPERATOR_TOKEN válido
    - X-Lab-Key == LAB_FORCE_KEY
    - authorized = TRUE
    - NO test_mode
    """
    _require_operator(x_operator_token)

    expected_lab = (os.getenv("LAB_FORCE_KEY") or "").strip()
    if not expected_lab:
        raise HTTPException(status_code=500, detail="LAB_FORCE_KEY no configurado")
    if (x_lab_key or "").strip() != expected_lab:
        raise HTTPException(status_code=401, detail="Unauthorized lab key")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status, authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()
        authorized = bool(row[1])
        test_mode = bool(row[2])

        if test_mode:
            raise HTTPException(status_code=409, detail="No permitido en test_mode")
        if not authorized:
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")

        if current_status in ("submitted", "closed", "archived"):
            raise HTTPException(status_code=409, detail=f"No se puede forzar desde status={current_status}")

        conn.execute(
            text("UPDATE cases SET status='ready_to_submit', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_ready_to_submit', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {"from": current_status, "to": "ready_to_submit", "note": note or ""}
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "status": "ready_to_submit"}

@router.post("/cases/{case_id}/lab-force-authorize")
def lab_force_authorize(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    expected_lab = (os.getenv("LAB_FORCE_KEY") or "").strip()
    if not expected_lab:
        raise HTTPException(status_code=500, detail="LAB_FORCE_KEY no configurado")
    if (x_lab_key or "").strip() != expected_lab:
        raise HTTPException(status_code=401, detail="Unauthorized lab key")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT authorized, COALESCE(test_mode,FALSE) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        if bool(row[1]):
            raise HTTPException(status_code=409, detail="No permitido en test_mode")

        conn.execute(
            text("UPDATE cases SET authorized=TRUE, authorized_at=NOW(), updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_authorize', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps({"note": note or ""}),
            },
        )

    return {"ok": True, "case_id": case_id, "authorized": True}

@router.post("/cases/{case_id}/lab-force-paid")
def lab_force_paid(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    expected_lab = (os.getenv("LAB_FORCE_KEY") or "").strip()
    if not expected_lab:
        raise HTTPException(status_code=500, detail="LAB_FORCE_KEY no configurado")
    if (x_lab_key or "").strip() != expected_lab:
        raise HTTPException(status_code=401, detail="Unauthorized lab key")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(test_mode,FALSE), payment_status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        if bool(row[0]):
            raise HTTPException(status_code=409, detail="No permitido en test_mode")

        conn.execute(
            text("UPDATE cases SET payment_status='paid', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_paid', CAST(:payload AS JSONB), NOW())
                """
            ),
            {"case_id": case_id, "payload": json.dumps({"note": note or ""})},
        )

    return {"ok": True, "case_id": case_id, "payment_status": "paid"}
