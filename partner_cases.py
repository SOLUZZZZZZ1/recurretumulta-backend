import os
import json
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form
from pydantic import EmailStr
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

router = APIRouter(prefix="/partner", tags=["partner"])

MAX_FILES = 5

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _require_partner_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Falta Authorization")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization inválido (usar Bearer)")
    return parts[1].strip()

def _get_partner_by_token(conn, token: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT id, name, email, active FROM partners WHERE api_token=:t"),
        {"t": token},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token partner inválido")
    if not bool(row[3]):
        raise HTTPException(status_code=403, detail="Partner desactivado")
    return {"id": str(row[0]), "name": row[1], "email": row[2]}

def _event(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
        ),
        {"case_id": case_id, "type": typ, "payload": json.dumps(payload)},
    )

@router.post("/cases")
async def create_partner_case(
    authorization: Optional[str] = Header(default=None),
    client_email: Optional[EmailStr] = Form(default=None),
    client_name: Optional[str] = Form(default=None),
    interesado_json: Optional[str] = Form(default=None),
    partner_note: Optional[str] = Form(default=None),
    confirm_client_informed: str = Form(...),
    files: List[UploadFile] = File(...),
) -> Dict[str, Any]:
    """
    Entrada asesorías (B2B):
    - Auth: Bearer <partner_api_token>
    - Crea case con channel='partner' y facturación mensual (sin Stripe)
    - Sube hasta 5 docs a B2 y crea documents(kind='original')
    """
    token = _require_partner_token(authorization)

    if (confirm_client_informed or "").strip().lower() not in ("true", "1", "yes", "si", "sí"):
        raise HTTPException(status_code=400, detail="Debe confirmarse cliente informado (confirm_client_informed=true).")
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FILES} documentos por expediente.")

    interesado: Dict[str, Any] = {}
    if interesado_json:
        try:
            interesado = json.loads(interesado_json)
        except Exception:
            raise HTTPException(status_code=400, detail="interesado_json no es JSON válido")

    engine = get_engine()

    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, token)

        row = conn.execute(
            text(
                """
                INSERT INTO cases (
                    contact_email, contact_name,
                    channel, partner_id, partner_name,
                    payment_status, status,
                    interested_data,
                    created_at, updated_at
                )
                VALUES (
                    :ce, :cn,
                    'partner', :pid, :pname,
                    'monthly', 'uploaded',
                    :idata,
                    NOW(), NOW()
                )
                RETURNING id
                """
            ),
            {
                "ce": str(client_email).strip().lower() if client_email else None,
                "cn": (client_name or "").strip() or None,
                "pid": partner["id"],
                "pname": partner["name"],
                "idata": json.dumps(interesado or {}),
            },
        ).fetchone()

        case_id = str(row[0])

        _event(conn, case_id, "partner_case_created", {
            "partner_id": partner["id"],
            "partner_name": partner["name"],
            "client_email": str(client_email).strip().lower() if client_email else None,
            "client_name": (client_name or "").strip() if client_name else None,
            "partner_note": (partner_note or "").strip()[:1000] if partner_note else None,
        })

    uploaded = []
    for idx, uf in enumerate(files, start=1):
        data = await uf.read()
        if not data:
            continue

        filename = (uf.filename or f"doc_{idx}").replace("/", "_").replace("\\", "_")[:120]
        ext = ".bin"
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
            if len(ext) > 8:
                ext = ".bin"

        b2_bucket, b2_key = upload_bytes(
            case_id,
            "original",
            data,
            ext=ext,
            content_type=(uf.content_type or "application/octet-stream"),
        )
        uploaded.append({
            "filename": filename,
            "bucket": b2_bucket,
            "key": b2_key,
            "mime": uf.content_type or "application/octet-stream",
            "size_bytes": len(data),
        })

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:case_id, 'original', :b, :k, :m, :s, NOW())
                    """
                ),
                {"case_id": case_id, "b": b2_bucket, "k": b2_key, "m": uf.content_type or "application/octet-stream", "s": len(data)},
            )
            _event(conn, case_id, "partner_documents_uploaded", {"count": len(uploaded)})

    return {"ok": True, "case_id": case_id, "uploaded": uploaded}
