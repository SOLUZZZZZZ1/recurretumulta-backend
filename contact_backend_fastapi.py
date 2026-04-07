import os
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

router = APIRouter()

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "info@recurretumulta.eu")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", "info@recurretumulta.eu")
CONTACT_TO = os.getenv("CONTACT_TO", "info@recurretumulta.eu")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"


class ContactRequest(BaseModel):
    tipo_consulta: str = Field(..., min_length=3, max_length=120)
    nombre: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    mensaje: str = Field(..., min_length=10, max_length=5000)


@router.post("/contact")
def send_contact_email(payload: ContactRequest):
    if not SMTP_HOST or not SMTP_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Falta configuración SMTP en el servidor.",
        )

    subject = f"[Contacto RTM] {payload.tipo_consulta} — {payload.nombre}"

    body = (
        "Nueva consulta enviada desde la página de contacto de RecurreTuMulta.\n\n"
        f"Tipo de consulta: {payload.tipo_consulta}\n"
        f"Nombre: {payload.nombre}\n"
        f"Email: {payload.email}\n\n"
        "Mensaje:\n"
        f"{payload.mensaje}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = CONTACT_TO
    msg["Reply-To"] = payload.email
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="No se pudo enviar la consulta. Inténtelo de nuevo más tarde.",
        )

    return {"ok": True, "message": "Consulta enviada correctamente."}
