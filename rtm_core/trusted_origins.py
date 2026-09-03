"""Canonical public origins used in security-sensitive redirects and links."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


_TRUSTED_FRONTEND_HOSTS = frozenset(
    {
        "recurretumulta.eu",
        "www.recurretumulta.eu",
        "staging.recurretumulta.eu",
        "recurretumulta.vercel.app",
    }
)


def trusted_frontend_origin() -> str:
    """Return an exact RTM HTTPS origin, rejecting legacy/ambiguous settings.

    These URLs receive browser returns and, in notification links, a bearer
    capability in the fragment.  They must never be selected from a free-form
    environment value or from the legacy ``FRONTEND_BASE_URL`` alias.
    """

    if (os.getenv("FRONTEND_BASE_URL") or "").strip():
        raise RuntimeError(
            "FRONTEND_BASE_URL está retirada; configure únicamente FRONTEND_URL"
        )
    raw = (os.getenv("FRONTEND_URL") or "").strip()
    if not raw:
        raise RuntimeError("Falta variable de entorno: FRONTEND_URL")
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.hostname.lower() not in _TRUSTED_FRONTEND_HOSTS
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("FRONTEND_URL no es un origen HTTPS RTM autorizado")
    host = parsed.hostname.lower()
    return f"https://{host}"
