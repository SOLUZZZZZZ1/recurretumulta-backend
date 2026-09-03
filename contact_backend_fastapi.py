import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from email_utils import send_email
from rtm_core.runtime_capabilities import require_http_capability


router = APIRouter()


class ContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tipo_consulta: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    nombre: str = Field(
        min_length=2,
        max_length=120,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    email: EmailStr = Field(max_length=254)
    mensaje: str = Field(..., min_length=10, max_length=5000)


@router.post("/contact")
def send_contact_email(payload: ContactRequest):
    require_http_capability("outbound_email")

    subject = f"[Contacto RTM] {payload.tipo_consulta} — {payload.nombre}"

    body = (
        "Nueva consulta enviada desde la página de contacto de RecurreTuMulta.\n\n"
        f"Tipo de consulta: {payload.tipo_consulta}\n"
        f"Nombre: {payload.nombre}\n"
        f"Email: {payload.email}\n\n"
        "Mensaje:\n"
        f"{payload.mensaje}\n"
    )

    try:
        sent = send_email(
            to_email=(os.getenv("CONTACT_TO") or "info@recurretumulta.eu").strip(),
            subject=subject,
            body=body,
            reply_to=str(payload.email),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="No se pudo enviar la consulta. Inténtelo de nuevo más tarde.",
        )
    if not sent:
        raise HTTPException(
            status_code=500,
            detail="No se pudo enviar la consulta. Inténtelo de nuevo más tarde.",
        )

    return {"ok": True, "message": "Consulta enviada correctamente."}
