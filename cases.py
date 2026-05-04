import json
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes

# Import interno del engine (Modo Dios)
from ai.expediente_engine import run_expediente_ai
from authorization_pdf import ensure_authorization_pdf, get_request_ip, _get_case_snapshot, _authorization_payload_from_case, generate_authorization_pdf

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5

# =========================
# EMAILS AUTOMÁTICOS (SILENCIOSO)
# =========================
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _case_link(case_id: str) -> str:
    base = _env("FRONTEND_BASE_URL", "https://www.recurretumulta.eu").rstrip("/")
    return f"{base}/#/resumen?case={case_id}"

def _smtp_ok() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))

def _send_email(to_email: str, subject: str, body: str) -> None:
    if not to_email or not _smtp_ok():
        return

    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    pwd = _env("SMTP_PASS")
    from_addr = _env("SMTP_FROM")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                try:
                    s.starttls()
                except Exception:
                    pass
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(msg)
    except Exception:
        pass

def _email_contact_saved(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu expediente está guardado · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos guardado tu contacto para este expediente.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Accede aquí para ver el estado y añadir documentación:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_pending(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu expediente está pendiente de documentación · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu documentación y, por ahora, no se puede presentar el recurso.\n"
        f"Suele faltar una notificación o resolución, o el acto recurrible.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Sube la documentación aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

def _email_ready(case_id: str, name: str, email: str) -> None:
    _send_email(
        email,
        "Tu recurso puede presentarse ahora · RecurreTuMulta",
        f"Hola {name},\n\n"
        f"Hemos revisado tu expediente y el recurso puede presentarse ahora.\n\n"
        f"Número de expediente:\n{case_id}\n\n"
        f"Continúa aquí:\n{_case_link(case_id)}\n\n"
        f"— RecurreTuMulta",
    )

# =========================
# MODELOS
# =========================
class CaseDetailsIn(BaseModel):
    full_name: str = Field(...)
    dni_nie: str = Field(...)
    domicilio_notif: str = Field(...)
    email: EmailStr
    telefono: Optional[str] = None

class CaseContactIn(BaseModel):
    name: str = Field(...)
    email: EmailStr

# =========================
# HELPERS
# =========================
def _case_exists(conn, case_id: str) -> Dict[str, Any]:
    """
    Devuelve meta del caso y comprueba que existe.
    Incluye flags de prueba: test_mode y override_deadlines.
    """
    row = conn.execute(
        text(
            "SELECT id, status, payment_status, authorized, interested_data, contact_name, contact_email, "
            "COALESCE(test_mode, FALSE) AS test_mode, COALESCE(override_deadlines, FALSE) AS override_deadlines "
            "FROM cases WHERE id=:id"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")
    return {
        "id": str(row[0]),
        "status": row[1],
        "payment_status": row[2],
        "authorized": bool(row[3]),
        "interested_data": row[4] or {},
        "contact_name": row[5] or "",
        "contact_email": row[6] or "",
        "test_mode": bool(row[7]),
        "override_deadlines": bool(row[8]),
    }

def _event(case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
            ),
            {"c": case_id, "t": typ, "p": json.dumps(payload)},
        )

# =========================
# CONTACTO (PRE-PAGO)
# =========================
@router.post("/{case_id}/contact")
def save_case_contact(case_id: str, data: CaseContactIn, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        conn.execute(
            text(
                "UPDATE cases SET contact_name=:n, contact_email=:e, updated_at=NOW() WHERE id=:id"
            ),
            {"id": case_id, "n": data.name.strip(), "e": str(data.email).strip()},
        )

    background_tasks.add_task(
        _email_contact_saved, case_id, data.name.strip(), str(data.email)
    )
    _event(case_id, "contact_saved", {})
    return {"ok": True}


# =========================
# DATOS DEL INTERESADO
# =========================
@router.post("/{case_id}/details")
def save_case_details(case_id: str, data: CaseDetailsIn):
    """
    Guarda los datos del interesado antes de generar la autorización.
    Alimenta autorización, pago y recurso.
    """
    engine = get_engine()

    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        interested = dict(meta.get("interested_data") or {})

        interested.update(
            {
                "full_name": data.full_name.strip(),
                "dni_nie": data.dni_nie.strip().upper(),
                "domicilio_notif": data.domicilio_notif.strip(),
                "email": str(data.email).strip(),
                "telefono": (data.telefono or "").strip() or None,
            }
        )

        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = CAST(:interested AS JSONB),
                    contact_name = :contact_name,
                    contact_email = :contact_email,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": case_id,
                "interested": json.dumps(interested, ensure_ascii=False),
                "contact_name": interested.get("full_name"),
                "contact_email": interested.get("email"),
            },
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:id, 'case_details_saved', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "id": case_id,
                "payload": json.dumps(
                    {
                        "full_name": interested.get("full_name"),
                        "dni_nie": interested.get("dni_nie"),
                        "email": interested.get("email"),
                        "telefono": interested.get("telefono"),
                    },
                    ensure_ascii=False,
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "interested_data": interested}


# =========================
# AÑADIR DOCUMENTOS
# =========================
@router.post("/{case_id}/append-documents")
async def append_documents(case_id: str, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_APPEND_FILES} documentos por subida.")

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

    uploaded_docs = []
    for uf in files:
        data = await uf.read()
        if not data:
            continue

        b2_bucket, b2_key = upload_bytes(case_id, "original", data, ".bin", (uf.content_type or "application/octet-stream"))

        uploaded_docs.append({"bucket": b2_bucket, "key": b2_key})

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
                    "VALUES (:id,'original',:b,:k,:m,:s,NOW())"
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": uf.content_type,
                    "s": len(data),
                },
            )

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status='uploaded', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

    _event(case_id, "expediente_documents_appended", {"documents": uploaded_docs})
    return {"ok": True}

# =========================
# REVIEW
# =========================
@router.post("/{case_id}/review")
def review_case(case_id: str, background_tasks: BackgroundTasks):
    engine = get_engine()
    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        old_status = meta["status"]

    result = run_expediente_ai(case_id)
    admiss = (result.get("admissibility") or {}).get("admissibility")

    new_status = "pending_documents"

    # 🔓 OVERRIDE DE PRUEBA (Opción B):
    # Si el caso está marcado como test_mode+override_deadlines, forzamos ready_to_pay
    if meta.get("test_mode") and meta.get("override_deadlines"):
        new_status = "ready_to_pay"
    else:
        if (admiss or "").upper() == "ADMISSIBLE":
            new_status = "ready_to_pay"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status=:s, updated_at=NOW() WHERE id=:id"),
            {"s": new_status, "id": case_id},
        )

    if meta["contact_email"] and new_status != old_status:
        if new_status == "pending_documents":
            background_tasks.add_task(
                _email_pending, case_id, meta["contact_name"] or "Usuario", meta["contact_email"]
            )
        elif new_status == "ready_to_pay":
            background_tasks.add_task(
                _email_ready, case_id, meta["contact_name"] or "Usuario", meta["contact_email"]
            )

    _event(case_id, "case_reviewed", {"status": new_status})
    return {"ok": True, "status": new_status}

# =========================
# ESTADO PÚBLICO
# =========================
@router.get("/{case_id}/public-status")
def public_status(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    status,
                    payment_status,
                    authorized,
                    contact_name,
                    contact_email,
                    COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                    organismo,
                    expediente_ref
                FROM cases
                WHERE id=:id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

        ex_row = conn.execute(
            text(
                """
                SELECT extracted_json
                FROM extractions
                WHERE case_id=:id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()

    status = row[0] or "uploaded"
    payment_status = row[1] or ""
    authorized = bool(row[2])
    contact_name = row[3] or ""
    contact_email = row[4] or ""
    interested_data = row[5] if isinstance(row[5], dict) else {}
    organismo = row[6] or ""
    expediente_ref = row[7] or ""
    extracted = ex_row[0] if ex_row and isinstance(ex_row[0], dict) else {}

    if contact_name and not interested_data.get("full_name"):
        interested_data["full_name"] = contact_name
    if contact_email and not interested_data.get("email"):
        interested_data["email"] = contact_email
    if organismo and not interested_data.get("organismo"):
        interested_data["organismo"] = organismo
    if expediente_ref and not interested_data.get("expediente_ref"):
        interested_data["expediente_ref"] = expediente_ref

    if payment_status == "paid":
        msg = "Gestión iniciada correctamente."
    elif authorized:
        msg = "Ya tenemos tu autorización. Puedes continuar para iniciar la gestión."
    else:
        msg = "Hemos analizado tu multa. Para continuar, necesitamos tus datos y autorización."

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "payment_status": payment_status,
        "authorized": authorized,
        "message": msg,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "interested_data": interested_data,
        "organismo": organismo,
        "expediente_ref": expediente_ref,
        "extracted": extracted,
    }


# =========================
# AUTORIZACION DEL EXPEDIENTE + PDF
# =========================
@router.post("/{case_id}/authorize")
async def authorize_case(case_id: str, request: Request):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT COALESCE(interested_data, '{}'::jsonb)
                FROM cases
                WHERE id = :id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        interested = row[0] if isinstance(row[0], dict) else {}

        missing = []
        if not interested.get("full_name"):
            missing.append("full_name")
        if not interested.get("dni_nie"):
            missing.append("dni_nie")
        if not interested.get("domicilio_notif"):
            missing.append("domicilio_notif")
        if not interested.get("email"):
            missing.append("email")

        if missing:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Faltan datos del interesado para generar la autorización",
                    "missing_fields": missing,
                },
            )

        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": case_id},
        )

        ip = get_request_ip(request)

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:id, 'case_authorized', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "id": case_id,
                "payload": json.dumps(
                    {"ip": ip, "version": "v1_dgt_homologado"},
                    ensure_ascii=False,
                ),
            },
        )

        try:
            auth_doc = ensure_authorization_pdf(
                conn,
                case_id=case_id,
                request=request,
                version="v1_dgt_homologado",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error generando PDF de autorización: {type(e).__name__}: {e}",
            )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "authorization_pdf": auth_doc.get("document"),
        "download_url": f"/cases/{case_id}/authorization-pdf",
    }

@router.get("/{case_id}/authorization-pdf")
def download_authorization_pdf(case_id: str, request: Request):
    """
    Devuelve el PDF de autorización ya relleno para descargar y firmar.
    """
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        ip = get_request_ip(request)
        case_meta = _get_case_snapshot(conn, case_id)
        payload = _authorization_payload_from_case(case_meta, ip=ip, version="v1_dgt_homologado")

        payload["representante_nombre"] = "LA TALAMANQUINA, S.L."
        payload["representante_nif"] = "B75440115"
        payload["representante_domicilio"] = "Calle Velázquez, 15 – 28001 Madrid (España)"

        pdf_bytes = generate_authorization_pdf(payload)

    headers = {
        "Content-Disposition": f'attachment; filename="autorizacion_{case_id}.pdf"'
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)



# =========================
# SUBIR AUTORIZACIÓN FIRMADA
# =========================
async def _store_authorization_signed(case_id: str, file: UploadFile):
    """
    Guarda la autorización firmada del cliente.
    Endpoint robusto:
    - comprueba primero que el expediente existe
    - lee el archivo
    - sube a B2
    - inserta en documents como authorization_signed
    - marca authorized = true
    - devuelve errores claros si falla B2 o BD
    """
    engine = get_engine()

    with engine.begin() as conn:
        _case_exists(conn, case_id)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    filename = (file.filename or "autorizacion_firmada").replace("/", "_").replace("\\", "_")[:140]

    ext = ".pdf"
    if "." in filename:
        candidate = "." + filename.rsplit(".", 1)[-1].lower()
        if 2 <= len(candidate) <= 10:
            ext = candidate

    content_type = file.content_type or "application/octet-stream"

    try:
        b2_bucket, b2_key = upload_bytes(case_id, "authorization_signed", data, ext, content_type)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el archivo en Backblaze B2: {type(e).__name__}: {e}",
        )

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                    VALUES (:id, 'authorization_signed', :b, :k, :m, :s, NOW())
                    """
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": content_type,
                    "s": len(data),
                },
            )

            conn.execute(
                text(
                    """
                    UPDATE cases
                    SET authorized = TRUE,
                        authorized_at = COALESCE(authorized_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": case_id},
            )

            conn.execute(
                text(
                    """
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (:id, 'authorization_signed_uploaded', CAST(:payload AS JSONB), NOW())
                    """
                ),
                {
                    "id": case_id,
                    "payload": json.dumps(
                        {
                            "filename": filename,
                            "bucket": b2_bucket,
                            "key": b2_key,
                            "mime": content_type,
                            "size_bytes": len(data),
                        },
                        ensure_ascii=False,
                    ),
                },
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Archivo subido a B2, pero falló el registro en base de datos: {type(e).__name__}: {e}",
        )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "document": {
            "kind": "authorization_signed",
            "bucket": b2_bucket,
            "key": b2_key,
            "mime": content_type,
            "size_bytes": len(data),
        },
    }


@router.post("/{case_id}/upload-authorization-signed")
async def upload_authorization_signed_legacy(case_id: str, file: UploadFile = File(...)):
    return await _store_authorization_signed(case_id, file)


@router.post("/{case_id}/authorization-signed")
async def upload_authorization_signed(case_id: str, file: UploadFile = File(...)):
    return await _store_authorization_signed(case_id, file)


@router.post("/{case_id}/upload-receipt")
async def upload_receipt(case_id: str, file: UploadFile = File(...)):
    engine = get_engine()

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    b2_bucket, b2_key = upload_bytes(case_id, "receipt", data, ".pdf", "application/pdf")

    with engine.begin() as conn:
        _case_exists(conn, case_id)

        conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:id, 'submission_receipt', :b, :k, :m, :s, NOW())
                """
            ),
            {
                "id": case_id,
                "b": b2_bucket,
                "k": b2_key,
                "m": "application/pdf",
                "s": len(data),
            },
        )

        conn.execute(
            text(
                """
                UPDATE cases
                SET status='submitted', updated_at=NOW()
                WHERE id=:id
                """
            ),
            {"id": case_id},
        )

        _event(case_id, "submission_receipt_uploaded", {
            "file": b2_key
        })

    return {"ok": True}