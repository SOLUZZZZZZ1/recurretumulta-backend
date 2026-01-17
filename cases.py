import json
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes
from ai.expediente_engine import run_expediente_ai

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5

# =========================
# EMAIL (SILENCIOSO)
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
    port = int(_env("SMTP_PORT", "587"))
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

def _email_contact_saved(case_id: str, name: str, email: str):
    _send_email(
        email,
        "Tu expediente está guardado · RecurreTuMulta",
        f"Hola {name},\n\nHemos guardado tu contacto para este expediente.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Accede aquí para ver el estado y añadir documentación:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_pending(case_id: str, name: str, email: str):
    _send_email(
        email,
        "Tu expediente está pendiente de documentación · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu documentación y, por ahora, no se puede presentar el recurso.\n"
        f"Suele faltar una notificación o resolución.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Sube la documentación aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_ready(case_id: str, name: str, email: str):
    _send_email(
        email,
        "Tu recurso puede presentarse ahora · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu expediente y el recurso puede presentarse ahora.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Continúa aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

class CaseContactIn(BaseModel):
    name: str
    email: EmailStr

def _case_exists(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT id, status, contact_name, contact_email FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")
    return {
        "id": str(row[0]),
        "status": row[1],
        "contact_name": row[2] or "",
        "contact_email": row[3] or "",
    }

def _event(case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
            ),
            {"c": case_id, "t": typ, "p": json.dumps(payload)},
        )

@router.post("/{case_id}/contact")
def save_case_contact(case_id: str, data: CaseContactIn, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        conn.execute(
            text(
                "UPDATE cases SET contact_name=:n, contact_email=:e, updated_at=NOW() WHERE id=:id"
            ),
            {"id": case_id, "n": data.name.strip(), "e": str(data.email).strip()},
        )
    background_tasks.add_task(_email_contact_saved, case_id, data.name.strip(), str(data.email))
    _event(case_id, "contact_saved", {})
    return {"ok": True}

@router.post("/{case_id}/review")
def review_case(case_id: str, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)

    result = run_expediente_ai(case_id)
    admiss = (result.get("admissibility") or {}).get("admissibility")
    new_status = "pending_documents"
    if (admiss or "").upper() == "ADMISSIBLE":
        new_status = "ready_to_pay"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status=:s, updated_at=NOW() WHERE id=:id"),
            {"s": new_status, "id": case_id},
        )

    if meta["contact_email"]:
        if new_status == "pending_documents":
            background_tasks.add_task(
                _email_pending, case_id, meta["contact_name"], meta["contact_email"]
            )
        elif new_status == "ready_to_pay":
            background_tasks.add_task(
                _email_ready, case_id, meta["contact_name"], meta["contact_email"]
            )

    _event(case_id, "case_reviewed", {"status": new_status})
    return {"ok": True, "status": new_status}
