# email_utils.py
# Utilidades de email para RecurreTuMulta.
# Usa las variables SMTP_* configuradas en Render.

import os
import smtplib
from email.message import EmailMessage
from typing import Literal, Optional, cast

from rtm_core.runtime_capabilities import require_capability


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _smtp_port() -> int:
    raw = _env("SMTP_PORT", "587")
    try:
        return int(raw)
    except Exception:
        return 587


def _smtp_security(port: int) -> Literal["ssl", "starttls", "plain"]:
    """Resolve the transport without confusing implicit SSL with STARTTLS.

    Nominalia uses implicit SSL on port 465.  The legacy RTM setting
    ``SMTP_USE_TLS`` is still understood for installations that use STARTTLS
    on port 587, while ``SMTP_SECURITY`` is the canonical explicit setting.
    """

    configured = _env("SMTP_SECURITY").lower()
    if configured in {"ssl", "starttls", "plain"}:
        return cast(Literal["ssl", "starttls", "plain"], configured)
    if port == 465:
        return "ssl"
    smtp_use_tls = _env("SMTP_USE_TLS", "true").lower() in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
    }
    return "starttls" if smtp_use_tls else "plain"


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
) -> bool:
    # El interruptor se comprueba antes de leer SMTP o abrir una conexión. En
    # staging el correo saliente permanece necesariamente desactivado.
    require_capability("outbound_email")

    smtp_host = _env("SMTP_HOST")
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    smtp_from = _env("SMTP_FROM") or smtp_user
    smtp_port = _smtp_port()
    smtp_security = _smtp_security(smtp_port)

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

    smtp_class = smtplib.SMTP_SSL if smtp_security == "ssl" else smtplib.SMTP
    with smtp_class(smtp_host, smtp_port, timeout=20) as smtp:
        if smtp_security == "starttls":
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
