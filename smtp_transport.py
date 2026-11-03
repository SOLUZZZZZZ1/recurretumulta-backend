"""SMTP transport shared by the existing mail senders; no startup side effects."""
import os
import smtplib
import ssl
from contextlib import contextmanager


def smtp_password() -> str:
    """Use the configured canonical credential, retaining the old alias fallback."""
    return os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS") or ""


@contextmanager
def smtp_connection(host: str, port: int, *, starttls: bool = True, timeout: int = 20):
    """465 uses verified implicit TLS; other ports retain explicit STARTTLS policy.

    Authentication is performed by callers only after this context is entered.
    A failed TLS handshake therefore never falls back to cleartext credentials.
    """
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as server:
            yield server
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            if starttls:
                server.starttls(context=ssl.create_default_context())
            yield server
