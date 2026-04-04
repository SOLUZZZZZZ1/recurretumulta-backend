import json
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

# Import interno del engine (Modo Dios)
from ai.expediente_engine import run_expediente_ai
from authorization_pdf import ensure_authorization_pdf, get_request_ip, _get_case_snapshot, _authorization_payload_from_case, generate_authorization_pdf

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5

# =========================
# EMAILS AUTOMÁTICOS (SILENCIOSO)
# =========================
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _case_link(case_id: str) -> str:
    base = _env("FRONTEND_BASE_URL", "https://www.recurretumulta.eu").rstrip("/")
    return f"{base}/#/resumen?case={case_id}"

def _smtp_ok() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))

def _send_email(to_email: str, subject: str, body: str) -> None:
    if not to_email or not _smtp_ok():
        return

    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    pwd = _env("SMTP_PASS")
    from_addr = _env("SMTP_FROM")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                try:
                    s.starttls()
                except Exception:
                    pass
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(msg)
    except Exception:
        pass

def _email_contact_saved(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu expediente está guardado · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos guardado tu contacto para este expediente.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Accede aquí para ver el estado y añadir documentación:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_pending(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu expediente está pendiente de documentación · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu documentación y, por ahora, no se puede presentar el recurso.\n"
        f"Suele faltar una notificación o resolución, o el acto recurrible.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Sube la documentación aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_ready(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu recurso puede presentarse ahora · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu expediente y el recurso puede presentarse ahora.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Continúa aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

# =========================
# MODELOS
# =========================
class CaseDetailsIn(BaseModel):
    full_name: str = Field(...)
    dni_nie: str = Field(...)
    domicilio_notif: str = Field(...)
    email: EmailStr
    telefono: Optional[str] = None

class CaseContactIn(BaseModel):
    name: str = Field(...)
    email: EmailStr

# =========================
# HELPERS
# =========================
def _case_exists(conn, case_id: str) -> Dict[str, Any]:
    """
    Devuelve meta del caso y comprueba que existe.
    Incluye flags de prueba: test_mode y override_deadlines.
    """
    row = conn.execute(
        text(
            "SELECT id, status, payment_status, authorized, interested_data, contact_name, contact_email, "
            "COALESCE(test_mode, FALSE) AS test_mode, COALESCE(override_deadlines, FALSE) AS override_deadlines "
            "FROM cases WHERE id=:id"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")
    return {
        "id": str(row[0]),
        "status": row[1],
        "payment_status": row[2],
        "authorized": bool(row[3]),
        "interested_data": row[4] or {},
        "contact_name": row[5] or "",
        "contact_email": row[6] or "",
        "test_mode": bool(row[7]),
        "override_deadlines": bool(row[8]),
    }

def _event(case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
            ),
            {"c": case_id, "t": typ, "p": json.dumps(payload)},
        )

# =========================
# CONTACTO (PRE-PAGO)
# =========================
@router.post("/{case_id}/contact")
def save_case_contact(case_id: str, data: CaseContactIn, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        conn.execute(
            text(
                "UPDATE cases SET contact_name=:n, contact_email=:e, updated_at=NOW() WHERE id=:id"
            ),
            {"id": case_id, "n": data.name.strip(), "e": str(data.email).strip()},
        )

    background_tasks.add_task(
        _email_contact_saved, case_id, data.name.strip(), str(data.email)
    )
    _event(case_id, "contact_saved", {})
    return {"ok": True}

# =========================
# AÑADIR DOCUMENTOS
# =========================
@router.post("/{case_id}/append-documents")
async def append_documents(case_id: str, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_APPEND_FILES} documentos por subida.")

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

    uploaded_docs = []
    for uf in files:
        data = await uf.read()
        if not data:
            continue

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "original",
            data,
            ext=".bin",
            content_type=(uf.content_type or "application/octet-stream"),
        )

        uploaded_docs.append({"bucket": b2_bucket, "key": b2_key})

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
                    "VALUES (:id,'original',:b,:k,:m,:s,NOW())"
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": uf.content_type,
                    "s": len(data),
                },
            )

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status='uploaded', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

    _event(case_id, "expediente_documents_appended", {"documents": uploaded_docs})
    return {"ok": True}

# =========================
# REVIEW
# =========================
@router.post("/{case_id}/review")
def review_case(case_id: str, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        old_status = meta["status"]

    result = run_expediente_ai(case_id)
    admiss = (result.get("admissibility") or {}).get("admissibility")

    new_status = "pending_documents"

    # 🔓 OVERRIDE DE PRUEBA (Opción B):
    # Si el caso está marcado como test_mode+override_deadlines, forzamos ready_to_pay
    if meta.get("test_mode") and meta.get("override_deadlines"):
        new_status = "ready_to_pay"
    else:
        if (admiss or "").upper() == "ADMISSIBLE":
            new_status = "ready_to_pay"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status=:s, updated_at=NOW() WHERE id=:id"),
            {"s": new_status, "id": case_id},
        )

    if meta["contact_email"] and new_status != old_status:
        if new_status == "pending_documents":
            background_tasks.add_task(
                _email_pending, case_id, meta["contact_name"] or "Usuario", meta["contact_email"]
            )
        elif new_status == "ready_to_pay":
            background_tasks.add_task(
                _email_ready, case_id, meta["contact_name"] or "Usuario", meta["contact_email"]
            )

    _event(case_id, "case_reviewed", {"status": new_status})
    return {"ok": True, "status": new_status}

# =========================
# ESTADO PÚBLICO
# =========================
@router.get("/{case_id}/public-status")
def public_status(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status, payment_status, authorized, contact_name, contact_email "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

    status = row[0] or "uploaded"
    payment_status = row[1] or ""
    authorized = bool(row[2])
    contact_name = row[3]
    contact_email = row[4]

    if status == "pending_documents":
        msg = "Aún no se puede presentar el recurso. Falta documentación o el acto recurrible."
    elif status == "ready_to_pay":
        msg = "Tu recurso puede presentarse ahora."
    else:
        msg = "Expediente en revisión."

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "payment_status": payment_status,
        "authorized": authorized,
        "message": msg,
        "contact_name": contact_name,
        "contact_email": contact_email,
    }

# =========================
# AUTORIZACION DEL EXPEDIENTE + PDF
# =========================
@router.post("/{case_id}/authorize")
def authorize_case(case_id: str, request: Request):
    engine = get_engine()

    with engine.begin() as conn:
        _case_exists(conn, case_id)

        # Marcar autorizado
        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": case_id},
        )

        # Capturar IP real
        ip = get_request_ip(request)

        # Evento de autorización
        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:id, 'case_authorized', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "id": case_id,
                "payload": json.dumps(
                    {
                        "ip": ip,
                        "version": "v1_dgt_homologado",
                    }
                ),
            },
        )

        # Generar y guardar PDF de autorización
        try:
            auth_doc = ensure_authorization_pdf(
                conn,
                case_id=case_id,
                request=request,
                version="v1_dgt_homologado",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Autorización registrada, pero falló el PDF: {e}",
            )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "authorization_pdf": auth_doc.get("document"),
    }



@router.get("/{case_id}/authorization-pdf")
def download_authorization_pdf(case_id: str, request: Request):
    """
    Devuelve el PDF de autorización ya relleno para descargar y firmar.
    """
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        ip = get_request_ip(request)
        case_meta = _get_case_snapshot(conn, case_id)
        payload = _authorization_payload_from_case(case_meta, ip=ip, version="v1_dgt_homologado")

        payload["representante_nombre"] = "LA TALAMANQUINA, S.L."
        payload["representante_nif"] = "B75440115"
        payload["representante_domicilio"] = "Calle Velázquez, 15 – 28001 Madrid (España)"

        pdf_bytes = generate_authorization_pdf(payload)

    headers = {
        "Content-Disposition": f'attachment; filename="autorizacion_{case_id}.pdf"'
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.post("/{case_id}/upload-receipt")
async def upload_receipt(case_id: str, file: UploadFile = File(...)):
    engine = get_engine()

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    b2_bucket, b2_key = upload_bytes(
        case_id,
        "receipt",
        data,
        ext=".pdf",
        content_type="application/pdf",
    )

    with engine.begin() as conn:
        _case_exists(conn, case_id)

        conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:id, 'submission_receipt', :b, :k, :m, :s, NOW())
                """
            ),
            {
                "id": case_id,
                "b": b2_bucket,
                "k": b2_key,
                "m": "application/pdf",
                "s": len(data),
            },
        )

        conn.execute(
            text(
                """
                UPDATE cases
                SET status='submitted', updated_at=NOW()
                WHERE id=:id
                """
            ),
            {"id": case_id},
        )

        _event(case_id, "submission_receipt_uploaded", {
            "file": b2_key
        })

    return {"ok": True}