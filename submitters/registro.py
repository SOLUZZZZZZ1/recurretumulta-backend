from __future__ import annotations

import os
import json
import base64
import urllib.request
import urllib.error
from typing import Any, Dict

from .base import SubmitterNotReady


class RegistroSubmitter:
    """
    Canal multi-administración.

    IMPORTANTE (sin humanos):
    - No automatiza navegación web.
    - Se integra con un proveedor/servicio oficial de registro (SIR/GEISER o equivalente)
      o con una API intermediaria que devuelva justificante oficial.
    """

    name: str = "registro_general"

    def submit(self, *, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        provider_url = (os.getenv("REG_PROVIDER_URL") or "").strip()
        provider_token = (os.getenv("REG_PROVIDER_TOKEN") or "").strip()

        if not provider_url:
            raise SubmitterNotReady(
                "REG_PROVIDER_URL no configurado. Necesitas integración SIR/GEISER o proveedor API de registro."
            )

        # API simple: POST {provider_url}/submit con JSON base64
        payload = {
            "case_id": case_id,
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "mime": "application/pdf",
        }
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            provider_url.rstrip("/") + "/submit",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {provider_token}"} if provider_token else {}),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_body = resp.read()
                ct = (resp.headers.get("content-type") or "").lower()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else str(e)
            raise RuntimeError(f"Proveedor registro error {e.code}: {body[:500]}")
        except Exception as e:
            raise RuntimeError(f"Error llamando proveedor registro: {e}")

        if "application/json" not in ct:
            raise RuntimeError("Proveedor registro no devolvió JSON")

        out = json.loads(resp_body.decode("utf-8"))
        b64 = (out.get("justificante_pdf_base64") or "").strip()
        if not b64:
            raise RuntimeError("Proveedor no devolvió justificante_pdf_base64")

        justificante_pdf = base64.b64decode(b64)
        meta = out.get("meta") or {}
        return {"justificante_pdf": justificante_pdf, "meta": meta}
