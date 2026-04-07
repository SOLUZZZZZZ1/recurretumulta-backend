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


def send_email_message(msg: EmailMessage):
    if not SMTP_HOST or not SMTP_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Falta configuración SMTP en el servidor.",
        )

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


@router.post("/contact")
def send_contact_email(payload: ContactRequest):
    # Correo interno para RecurreTuMulta
    subject_internal = f"[Contacto RTM] {payload.tipo_consulta} — {payload.nombre}"
    body_internal = (
        "Nueva consulta enviada desde la página de contacto de RecurreTuMulta.\n\n"
        f"Tipo de consulta: {payload.tipo_consulta}\n"
        f"Nombre: {payload.nombre}\n"
        f"Email: {payload.email}\n\n"
        "Mensaje:\n"
        f"{payload.mensaje}\n"
    )

    internal_msg = EmailMessage()
    internal_msg["Subject"] = subject_internal
    internal_msg["From"] = SMTP_FROM
    internal_msg["To"] = CONTACT_TO
    internal_msg["Reply-To"] = payload.email
    internal_msg.set_content(body_internal)

    send_email_message(internal_msg)

    # Copia automática al usuario
    subject_user = "Hemos recibido tu consulta | RecurreTuMulta"
    body_user = (
        f"Hola {payload.nombre},\n\n"
        "Hemos recibido correctamente tu consulta en RecurreTuMulta.\n\n"
        "Resumen de tu envío:\n"
        f"- Tipo de consulta: {payload.tipo_consulta}\n"
        f"- Email: {payload.email}\n\n"
        "Mensaje recibido:\n"
        f"{payload.mensaje}\n\n"
        "Este canal no ofrece atención inmediata, pero responderemos lo antes posible si procede.\n\n"
        "Si lo que deseas es comprobar si tu multa puede recurrirse, te recomendamos utilizar directamente el proceso de análisis en la web.\n\n"
        "Un saludo,\n"
        "RecurreTuMulta\n"
        "info@recurretumulta.eu"
    )

    user_msg = EmailMessage()
    user_msg["Subject"] = subject_user
    user_msg["From"] = SMTP_FROM
    user_msg["To"] = payload.email
    user_msg["Reply-To"] = CONTACT_TO
    user_msg.set_content(body_user)

    send_email_message(user_msg)

    return {"ok": True, "message": "Consulta enviada correctamente."}
