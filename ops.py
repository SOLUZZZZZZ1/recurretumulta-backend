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
    status: str = Query("all"),
    limit: int = Query(300, ge=1, le=500),
) -> Dict[str, Any]:
    """OPS CORE v1: cola común enriquecida con familia, tipo y datos humanos."""
    _require_operator(x_operator_token)

    select_sql = """
        SELECT id, status, payment_status, product_code, contact_email,
               created_at, updated_at, contact_name, department, case_type,
               category, organismo, expediente_ref,
               COALESCE(interested_data, '{}'::jsonb) AS interested_data,
               customer_comment, source_module
        FROM cases
    """

    engine = get_engine()
    with engine.begin() as conn:
        if status == "ready_to_submit":
            rows = conn.execute(text(select_sql + """
                WHERE status='ready_to_submit'
                  AND payment_status='paid'
                  AND authorized=TRUE
                ORDER BY created_at ASC LIMIT :limit
            """), {"limit": limit}).fetchall()
        elif status == "all":
            rows = conn.execute(text(select_sql + """
                WHERE COALESCE(status,'') <> 'archived_test'
                ORDER BY updated_at DESC LIMIT :limit
            """), {"limit": limit}).fetchall()
        else:
            rows = conn.execute(text(select_sql + """
                WHERE status=:status
                ORDER BY updated_at DESC LIMIT :limit
            """), {"status": status, "limit": limit}).fetchall()

    items = []
    for r in rows:
        interested = r[13] if isinstance(r[13], dict) else {}
        department = (r[8] or interested.get("department") or "").strip().lower()
        case_type = (r[9] or interested.get("case_type") or "").strip().lower()
        category = (r[10] or "").strip().lower()

        # Compatibilidad con expedientes anteriores al RTM CORE.
        if not department:
            if category == "vehicle_removal" or str(r[1] or "").startswith("vehicle_removal"):
                department = "traffic"
                case_type = case_type or "vehicle_removal"
            elif category in ("traffic", "debt", "administration", "claims", "other"):
                department = category
            else:
                department = "other"

        if department == "traffic" and not case_type:
            case_type = "vehicle_removal" if category == "vehicle_removal" else "fine"

        items.append({
            "case_id": str(r[0]),
            "status": r[1],
            "payment_status": r[2],
            "product_code": r[3],
            "contact_email": r[4] or interested.get("email"),
            "created_at": r[5],
            "updated_at": r[6],
            "contact_name": r[7] or interested.get("full_name") or interested.get("name"),
            "department": department,
            "case_type": case_type or "other",
            "category": r[10],
            "organismo": r[11] or interested.get("organismo"),
            "expediente_ref": r[12] or interested.get("expediente_ref"),
            "customer_comment": r[14] or interested.get("customer_comment"),
            "source_module": r[15] or interested.get("source_module"),
            "matricula": interested.get("matricula") or interested.get("plate"),
            "phone": interested.get("telefono") or interested.get("phone"),
        })

    return {"ok": True, "status": status, "count": len(items), "items": items}


@router.get("/presented-cases")
def list_presented_cases_safe(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    q: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Histórico operativo de expedientes presentados / en seguimiento.
    Ruta segura: /ops/presented-cases
    Evita conflictos con /ops/cases/{case_id}.
    """
    _require_operator(x_operator_token)

    term = (q or "").strip()

    engine = get_engine()
    with engine.begin() as conn:
        if term:
            rows = conn.execute(
                text(
                    """
                    SELECT id, expediente_ref, status, payment_status, contact_email, created_at, updated_at
                    FROM cases
                    WHERE (
                        status = 'submitted'
                        OR status ILIKE 'presentado%%'
                        OR status ILIKE '%%presentado%%'
                    )
                    AND (
                        CAST(id AS TEXT) ILIKE :term
                        OR COALESCE(expediente_ref, '') ILIKE :term
                        OR COALESCE(contact_email, '') ILIKE :term
                        OR COALESCE(status, '') ILIKE :term
                    )
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"term": f"%{term}%", "limit": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT id, expediente_ref, status, payment_status, contact_email, created_at, updated_at
                    FROM cases
                    WHERE (
                        status = 'submitted'
                        OR status ILIKE 'presentado%%'
                        OR status ILIKE '%%presentado%%'
                    )
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()

    items = [
        {
            "case_id": str(r[0]),
            "expediente_ref": r[1],
            "status": r[2],
            "payment_status": r[3],
            "contact_email": r[4],
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]

    return {"ok": True, "count": len(items), "items": items}


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

        b2_bucket, b2_key = upload_bytes(case_id, "justificantes", data, ext, content_type)

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

        b2_bucket, b2_key = upload_bytes(case_id, "external", data, ext, content_type)

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
            b2_bucket, b2_key = upload_bytes(case_id, "manual_submission", data, ext, content_type)

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

        _ensure_standard_followups_after_manual_submission(
            conn,
            case_id,
            organismo_clean,
            submitted_at_clean,
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



# =========================================================
# Seguimiento de plazos / follow-ups OPS
# =========================================================

def _parse_submitted_at(value: str = ""):
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)

    # Acepta ISO, "YYYY-MM-DD HH:MM" o "YYYY-MM-DD"
    candidates = [
        raw,
        raw.replace(" ", "T"),
        raw.replace("/", "-"),
    ]

    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    return datetime.now(timezone.utc)


def _create_followup(
    conn,
    case_id: str,
    *,
    kind: str,
    title: str,
    description: str = "",
    due_at,
    source_event_type: str = "",
    created_by: str = "ops",
):
    conn.execute(
        text(
            """
            INSERT INTO ops_followups(
              case_id, kind, status, title, description, due_at,
              source_event_type, created_by, created_at, updated_at
            )
            VALUES (
              :case_id, :kind, 'pending', :title, :description, :due_at,
              :source_event_type, :created_by, NOW(), NOW()
            )
            """
        ),
        {
            "case_id": case_id,
            "kind": kind,
            "title": title,
            "description": description,
            "due_at": due_at,
            "source_event_type": source_event_type,
            "created_by": created_by,
        },
    )


def _ensure_standard_followups_after_manual_submission(conn, case_id: str, organismo: str, submitted_at_raw: str):
    """
    Crea alertas conservadoras tras una presentación manual.
    No son afirmaciones jurídicas automáticas; son hitos operativos para que OPS revise.
    """
    from datetime import timedelta

    submitted_dt = _parse_submitted_at(submitted_at_raw)

    checks = [
        (
            "revision_30_dias",
            "Revisar estado del expediente",
            "Han pasado aproximadamente 30 días desde la presentación. Comprobar si hay resolución, requerimiento o nueva notificación.",
            submitted_dt + timedelta(days=30),
        ),
        (
            "alerta_60_dias",
            "Alerta: expediente sin resolución registrada",
            "Si no consta respuesta, revisar sede electrónica, buzón/notificaciones y estado administrativo.",
            submitted_dt + timedelta(days=60),
        ),
        (
            "revision_90_dias",
            "Revisión avanzada: posible silencio / siguiente acción",
            "Si no consta respuesta, revisar jurídicamente silencio administrativo, ejecutiva o siguiente escrito. No automatizar sin revisión humana.",
            submitted_dt + timedelta(days=90),
        ),
    ]

    for kind, title, description, due_at in checks:
        _create_followup(
            conn,
            case_id,
            kind=kind,
            title=title,
            description=f"{description} Organismo: {organismo or 'no indicado'}.",
            due_at=due_at,
            source_event_type="manual_submission_registered",
            created_by="ops",
        )


@router.get("/cases/{case_id}/followups")
def list_case_followups(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        rows = conn.execute(
            text(
                """
                SELECT id, kind, status, title, description, due_at,
                       resolved_at, resolution_note, created_at, updated_at
                FROM ops_followups
                WHERE case_id = :case_id
                ORDER BY
                  CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                  due_at ASC,
                  created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    items = []
    now = datetime.now(timezone.utc)

    for r in rows:
        due_at = r[5]
        overdue = False
        days_left = None
        if due_at:
            try:
                dta = due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
                delta = dta - now
                days_left = int(delta.total_seconds() // 86400)
                overdue = delta.total_seconds() < 0 and (r[2] or "") == "pending"
            except Exception:
                pass

        items.append(
            {
                "id": str(r[0]),
                "kind": r[1],
                "status": r[2],
                "title": r[3],
                "description": r[4],
                "due_at": r[5],
                "resolved_at": r[6],
                "resolution_note": r[7],
                "created_at": r[8],
                "updated_at": r[9],
                "overdue": overdue,
                "days_left": days_left,
            }
        )

    return {"ok": True, "case_id": case_id, "followups": items}


@router.get("/followups/due")
def list_due_followups(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    days: int = Query(7, ge=0, le=365),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Alertas pendientes vencidas o próximas.
    Útil para dashboard OPS.
    """
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT f.id, f.case_id, f.kind, f.status, f.title, f.description,
                       f.due_at, c.status AS case_status, c.contact_email
                FROM ops_followups f
                JOIN cases c ON c.id = f.case_id
                WHERE f.status = 'pending'
                  AND f.due_at <= NOW() + (:days || ' days')::interval
                ORDER BY f.due_at ASC
                LIMIT :limit
                """
            ),
            {"days": days, "limit": limit},
        ).fetchall()

    return {
        "ok": True,
        "days": days,
        "items": [
            {
                "id": str(r[0]),
                "case_id": str(r[1]),
                "kind": r[2],
                "status": r[3],
                "title": r[4],
                "description": r[5],
                "due_at": r[6],
                "case_status": r[7],
                "contact_email": r[8],
            }
            for r in rows
        ],
    }


@router.post("/cases/{case_id}/followups")
def create_case_followup(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    kind: str = Form("seguimiento"),
    title: str = Form(...),
    description: Optional[str] = Form(default=None),
    due_at: str = Form(...),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    title_clean = (title or "").strip()
    if not title_clean:
        raise HTTPException(status_code=400, detail="Título requerido")

    due_dt = _parse_submitted_at(due_at)

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        _create_followup(
            conn,
            case_id,
            kind=(kind or "seguimiento").strip(),
            title=title_clean,
            description=(description or "").strip(),
            due_at=due_dt,
            source_event_type="manual_followup",
            created_by="ops",
        )

        _append_event(
            conn,
            case_id,
            "followup_created",
            {
                "kind": kind,
                "title": title_clean,
                "description": description or "",
                "due_at": due_dt.isoformat(),
                "at": _now_iso(),
            },
        )

    return {"ok": True, "case_id": case_id}


@router.post("/cases/{case_id}/followups/{followup_id}/resolve")
def resolve_case_followup(
    case_id: str,
    followup_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        res = conn.execute(
            text(
                """
                UPDATE ops_followups
                SET status='resolved',
                    resolved_at=NOW(),
                    resolved_by='ops',
                    resolution_note=:note,
                    updated_at=NOW()
                WHERE id=:id AND case_id=:case_id
                RETURNING id
                """
            ),
            {"id": followup_id, "case_id": case_id, "note": note or ""},
        ).fetchone()

        if not res:
            raise HTTPException(status_code=404, detail="Follow-up no encontrado")

        _append_event(
            conn,
            case_id,
            "followup_resolved",
            {
                "followup_id": followup_id,
                "note": note or "",
                "at": _now_iso(),
            },
        )

    return {"ok": True, "case_id": case_id, "followup_id": followup_id, "status": "resolved"}


@router.post("/cases/{case_id}/restore-real-case")
def restore_real_case(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Restaura un expediente real marcado accidentalmente como archived_test.

    NO borra nada.
    NO toca documentos.
    NO toca eventos anteriores.

    Solo:
    archived_test -> presentado_manual_ayuntamiento
    """

    _require_operator(x_operator_token)

    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, expediente_ref
                FROM cases
                WHERE id = :id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        previous_status = (row[0] or "").strip()
        expediente_ref = row[1]

        conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'presentado_manual_ayuntamiento',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (
                  :case_id,
                  'ops_restore_real_case',
                  CAST(:payload AS JSONB),
                  NOW()
                )
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "from": previous_status,
                        "to": "presentado_manual_ayuntamiento",
                        "expediente_ref": expediente_ref,
                        "note": note or "Restauración expediente real",
                    },
                    ensure_ascii=False,
                ),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": "presentado_manual_ayuntamiento",
        "message": "Expediente real restaurado correctamente.",
    }




@router.get("/cases/presented")
def list_presented_cases(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    q: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Historico operativo de expedientes presentados / en seguimiento.
    Query robusta sin ANY(:lista), para evitar problemas de binding.
    """
    _require_operator(x_operator_token)

    term = (q or "").strip()

    engine = get_engine()
    with engine.begin() as conn:
        if term:
            rows = conn.execute(
                text(
                    """
                    SELECT id, expediente_ref, status, payment_status, contact_email, created_at, updated_at
                    FROM cases
                    WHERE (
                        status = 'submitted'
                        OR status ILIKE 'presentado%%'
                        OR status ILIKE '%%presentado%%'
                    )
                    AND (
                        CAST(id AS TEXT) ILIKE :term
                        OR COALESCE(expediente_ref, '') ILIKE :term
                        OR COALESCE(contact_email, '') ILIKE :term
                        OR COALESCE(status, '') ILIKE :term
                    )
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"term": f"%{term}%", "limit": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT id, expediente_ref, status, payment_status, contact_email, created_at, updated_at
                    FROM cases
                    WHERE (
                        status = 'submitted'
                        OR status ILIKE 'presentado%%'
                        OR status ILIKE '%%presentado%%'
                    )
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "case_id": str(r[0]),
                "expediente_ref": r[1],
                "status": r[2],
                "payment_status": r[3],
                "contact_email": r[4],
                "created_at": r[5],
                "updated_at": r[6],
            }
        )

    return {"ok": True, "count": len(items), "items": items}


@router.post("/cases/{case_id}/rebuild-followups")
def rebuild_followups(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """
    Regenera automáticamente los followups 30/60/90 para un expediente
    ya presentado manualmente.

    Útil cuando el expediente fue restaurado después de la limpieza
    o cuando no se crearon los seguimientos al registrar la presentación.
    """
    _require_operator(x_operator_token)

    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, expediente_ref
                FROM cases
                WHERE id = :id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()

        if current_status != "presentado_manual_ayuntamiento":
            raise HTTPException(
                status_code=409,
                detail="El expediente no está en presentado_manual_ayuntamiento",
            )

        event_row = conn.execute(
            text(
                """
                SELECT payload
                FROM events
                WHERE case_id = :case_id
                  AND type = 'manual_submission_registered'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"case_id": case_id},
        ).fetchone()

        if not event_row:
            raise HTTPException(
                status_code=404,
                detail="No existe evento manual_submission_registered",
            )

        payload = event_row[0] or {}

        organismo = payload.get("organismo") or "Organismo"
        submitted_at = payload.get("submitted_at") or ""

        conn.execute(
            text(
                """
                DELETE FROM ops_followups
                WHERE case_id = :case_id
                  AND source_event_type = 'manual_submission_registered'
                """
            ),
            {"case_id": case_id},
        )

        _ensure_standard_followups_after_manual_submission(
            conn,
            case_id,
            organismo,
            submitted_at,
        )

        _append_event(
            conn,
            case_id,
            "followups_rebuilt",
            {
                "organismo": organismo,
                "submitted_at": submitted_at,
                "at": _now_iso(),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "message": "Followups 30/60/90 regenerados correctamente.",
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
