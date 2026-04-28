# email_utils.py
# Utilidades de email para RecurreTuMulta.
# Usa las variables SMTP_* que ya tienes en Render.

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _smtp_port() -> int:
    raw = _env("SMTP_PORT", "587")
    try:
        return int(raw)
    except Exception:
        return 587


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
) -> bool:
    smtp_host = _env("SMTP_HOST")
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    smtp_from = _env("SMTP_FROM") or smtp_user
    smtp_use_tls = _env("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "si", "sí")

    if not smtp_host or not smtp_user or not smtp_password or not smtp_from:
        # No rompemos el flujo si falta SMTP; devolvemos False.
        return False

    msg = EmailMessage()
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(body)

    with smtplib.SMTP(smtp_host, _smtp_port(), timeout=20) as smtp:
        if smtp_use_tls:
            smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)

    return True


def build_vehicle_removal_paid_email(
    *,
    case_id: str,
    full_name: str,
    plate: str,
    city: str,
) -> tuple[str, str]:
    subject = "Solicitud recibida - Eliminación de vehículo"

    body = f"""Hola {full_name},

Hemos recibido correctamente el pago y la solicitud para gestionar la baja/retirada del vehículo.

Datos de la solicitud:
- Matrícula: {plate}
- Municipio: {city}
- Referencia interna: {case_id}

Siguiente paso:
Revisaremos la documentación y contactaremos contigo para continuar la gestión con un centro autorizado, cuando proceda.

Importante:
Este servicio no elimina deudas, embargos o sanciones previas asociadas al vehículo. La gestión se orienta a tramitar la baja/retirada del vehículo y obtener la documentación justificativa correspondiente.

Un saludo,
RecurreTuMulta
"""

    return subject, body
