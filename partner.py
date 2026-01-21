import os
import json
import secrets
import hashlib
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

router = APIRouter(prefix="/partner", tags=["partner"])

MAX_FILES = 5

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _require_admin(x_admin_token: Optional[str]) -> None:
    expected = _env("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return dk.hex()

def _make_token() -> str:
    return secrets.token_urlsafe(32)

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
        text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"),
        {"case_id": case_id, "type": typ, "payload": json.dumps(payload)},
    )

class PartnerCreateIn(BaseModel):
    name: str
    email: EmailStr
    password: str

class PartnerLoginIn(BaseModel):
    email: EmailStr
    password: str

@router.post("/admin-create")
def admin_create_partner(
    payload: PartnerCreateIn,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    name = payload.name.strip()
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password mínimo 8 caracteres")

    salt = secrets.token_hex(16)
    pwd_hash = _hash_password(password, salt)
    token = _make_token()

    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM partners WHERE email=:e"), {"e": email}).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Ya existe un partner con ese email")
        row = conn.execute(
            text("INSERT INTO partners(name, email, password_salt, password_hash, api_token, active, created_at, updated_at) VALUES (:n,:e,:s,:h,:t,TRUE,NOW(),NOW()) RETURNING id"),
            {"n": name, "e": email, "s": salt, "h": pwd_hash, "t": token},
        ).fetchone()
    return {"ok": True, "partner_id": str(row[0]), "token": token}

@router.post("/login")
def partner_login(payload: PartnerLoginIn) -> Dict[str, Any]:
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, name, email, password_salt, password_hash, active FROM partners WHERE email=:e"),
            {"e": email},
        ).fetchone()
        if not row or not bool(row[5]):
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
        salt = row[3] or ""
        expected = row[4] or ""
        got = _hash_password(password, salt)
        if got != expected:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")
        token = _make_token()
        conn.execute(text("UPDATE partners SET api_token=:t, updated_at=NOW() WHERE id=:id"), {"t": token, "id": row[0]})
    return {"ok": True, "token": token, "partner_name": row[1]}

@router.post("/cases")
async def create_partner_case(
    authorization: Optional[str] = Header(default=None),
    client_email: EmailStr = Form(...),
    client_name: str = Form(...),
    partner_note: Optional[str] = Form(default=None),
    confirm_client_informed: str = Form(...),
    files: List[UploadFile] = File(...),
) -> Dict[str, Any]:
    token = _require_partner_token(authorization)

    if (confirm_client_informed or "").strip().lower() not in ("true", "1", "yes", "si", "sí"):
        raise HTTPException(status_code=400, detail="Debe confirmarse que el cliente ha sido informado (confirm_client_informed=true).")
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FILES} documentos por expediente.")

    engine = get_engine()
    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, token)
        row = conn.execute(
            text("INSERT INTO cases (contact_email, contact_name, channel, partner_id, partner_name, status, created_at, updated_at) VALUES (:ce, :cn, 'partner', :pid, :pname, 'uploaded', NOW(), NOW()) RETURNING id"),
            {"ce": str(client_email).strip().lower(), "cn": client_name.strip(), "pid": partner["id"], "pname": partner["name"]},
        ).fetchone()
        case_id = str(row[0])
        _event(conn, case_id, "partner_case_created", {
            "partner_id": partner["id"],
            "partner_name": partner["name"],
            "client_email": str(client_email).strip().lower(),
            "client_name": client_name.strip(),
            "partner_note": (partner_note or "").strip()[:1000] if partner_note else None
        })

    uploaded = []
    for idx, uf in enumerate(files, start=1):
        data = await uf.read()
        if not data:
            continue
        filename = (uf.filename or f"doc_{idx}").replace("/", "_").replace("\\", "_")
        ext = ".bin"
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
            if len(ext) > 8:
                ext = ".bin"

        b2_bucket, b2_key = upload_bytes(case_id, "original", data, ext=ext, content_type=(uf.content_type or "application/octet-stream"))
        uploaded.append({"filename": filename, "bucket": b2_bucket, "key": b2_key, "mime": uf.content_type, "size_bytes": len(data)})

        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id, 'original', :b, :k, :m, :s, NOW())"),
                {"case_id": case_id, "b": b2_bucket, "k": b2_key, "m": uf.content_type or "application/octet-stream", "s": len(data)},
            )
            _event(conn, case_id, "partner_documents_uploaded", {"count": len(uploaded)})
    with engine.begin() as conn:
        _event(conn, case_id, "client_authorization_requested", {"channel": "email", "to": str(client_email).strip().lower()})

    return {"ok": True, "case_id": case_id, "uploaded": uploaded}
