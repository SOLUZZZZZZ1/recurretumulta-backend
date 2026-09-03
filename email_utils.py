# email_utils.py
# Utilidades de email para RecurreTuMulta.
# Usa las variables SMTP_* configuradas en Render.

import os
import smtplib
import ssl
import re
from email.message import EmailMessage
from typing import Literal, Optional, cast

from rtm_core.runtime_capabilities import require_capability


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _smtp_port() -> int:
    raw = _env("SMTP_PORT", "587")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("SMTP_PORT no es válido") from exc


def _smtp_security(port: int) -> Literal["ssl", "starttls"]:
    """Resolve the transport without confusing implicit SSL with STARTTLS.

    Nominalia uses implicit SSL on port 465.  The legacy RTM setting
    ``SMTP_USE_TLS`` is still understood for installations that use STARTTLS
    on port 587, while ``SMTP_SECURITY`` is the canonical explicit setting.
    """

    configured = _env("SMTP_SECURITY").lower()
    if configured == "plain":
        raise RuntimeError("SMTP sin TLS está prohibido")
    if configured in {"ssl", "starttls"}:
        security = cast(Literal["ssl", "starttls"], configured)
        expected_port = 465 if security == "ssl" else 587
        if port != expected_port:
            raise RuntimeError("SMTP_SECURITY y SMTP_PORT no coinciden")
        return security
    if port == 465:
        return "ssl"
    smtp_use_tls = _env("SMTP_USE_TLS", "true").lower() in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
    }
    if not smtp_use_tls:
        raise RuntimeError("SMTP sin TLS está prohibido")
    if port != 587:
        raise RuntimeError("STARTTLS requiere el puerto 587")
    return "starttls"


def _validated_smtp_host(value: str) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if (
        not host
        or len(host) > 253
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
            host,
        )
    ):
        raise RuntimeError("SMTP_HOST no es un nombre DNS válido")
    configured = _env("RTM_SMTP_ALLOWED_HOSTS", "authsmtp.securemail.pro")
    allowed = {
        item.strip().lower().rstrip(".")
        for item in configured.split(",")
        if item.strip()
    }
    if host not in allowed:
        raise RuntimeError("SMTP_HOST no pertenece a la allowlist RTM")
    return host


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

    smtp_host = _validated_smtp_host(_env("SMTP_HOST"))
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    smtp_from = _env("SMTP_FROM") or smtp_user
    smtp_port = _smtp_port()
    smtp_security = _smtp_security(smtp_port)

    if not smtp_host or not smtp_user or not smtp_password or not smtp_from:
        raise RuntimeError("Configuración SMTP incompleta")

    msg = EmailMessage()
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(body)

    tls_context = ssl.create_default_context()
    if smtp_security == "ssl":
        smtp_connection = smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=20,
            context=tls_context,
        )
    else:
        smtp_connection = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
    with smtp_connection as smtp:
        if smtp_security == "starttls":
            smtp.ehlo()
            smtp.starttls(context=tls_context)
            smtp.ehlo()
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
