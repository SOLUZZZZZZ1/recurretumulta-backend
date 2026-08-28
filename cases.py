import hashlib
import json
import os
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Header, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_bytes
from case_authority import (
    AUTHORITY_VERSION,
    build_case_authority_payload,
    build_signed_authority_document_attestation,
    verify_active_case_authority,
    verify_signed_case_authority,
)
from public_case_access import (
    issue_case_access_token,
    require_case_access_token,
    require_operator_case_access,
)
from rtm_core.runtime_capabilities import capability_state

# Import interno del engine (Modo Dios)
from ai.expediente_engine import run_expediente_ai
from authorization_pdf import ensure_authorization_pdf, get_request_ip, _get_case_snapshot, _authorization_payload_from_case, generate_authorization_pdf
from pdf_builder import build_pdf
from analyze import analyze_existing_case_document

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5
MAX_PUBLIC_PDF_BYTES = 10 * 1024 * 1024
PUBLIC_SERVICE_FAMILY_CODES = {
    "trafico",
    "viajes",
    "morosidad",
    "administracion",
    "bancos",
    "energia",
    "telecomunicaciones",
    "seguros",
    "vivienda",
}

# =========================
# EMAILS AUTOMÁTICOS (SILENCIOSO)
# =========================
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _case_link(case_id: str) -> str:
    base = _env("FRONTEND_BASE_URL", "https://www.recurretumulta.eu").rstrip("/")
    token = issue_case_access_token(case_id)
    return f"{base}/#/resumen?case={case_id}&access_token={token}"

def _smtp_ok() -> bool:
    return bool(
        capability_state("outbound_email").enabled
        and _env("SMTP_HOST")
        and _env("SMTP_FROM")
    )

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
    matricula: Optional[str] = None
    domicilio_notif: str = Field(...)
    email: EmailStr
    telefono: Optional[str] = None
    autorizo_gestion: Optional[bool] = None
    acepto_responsabilidad: Optional[bool] = None

class CaseContactIn(BaseModel):
    name: str = Field(...)
    email: EmailStr


class AuthorizationConsentIn(BaseModel):
    authority_version: Literal["v1_dgt_homologado"]
    consent: Literal[True]
    representation_confirmed: Literal[True]

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


def _event_on_conn(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
        ),
        {
            "c": case_id,
            "t": typ,
            "p": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )


def _validate_public_pdf(data: bytes, content_type: str | None) -> str:
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(data) > MAX_PUBLIC_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF demasiado grande")
    if (content_type or "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise HTTPException(status_code=415, detail="Solo se admite application/pdf")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="El contenido no es un PDF válido")
    return hashlib.sha256(data).hexdigest()


def _require_receipt_upload_state(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT status, payment_status, authorized FROM cases "
            "WHERE id=:id FOR UPDATE"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    if str(row[1] or "").strip().lower() != "paid":
        raise HTTPException(status_code=409, detail="El expediente no consta como pagado")
    if not bool(row[2]):
        raise HTTPException(status_code=409, detail="El expediente no está autorizado")
    if str(row[0] or "") != "submission_receipt_pending":
        raise HTTPException(
            status_code=409,
            detail="El expediente no está esperando justificante de presentación",
        )
    submission_row = conn.execute(
        text(
            """
            SELECT payload FROM events
            WHERE case_id=:id
              AND type='dgt_submission_accepted_receipt_pending'
              AND (
                COALESCE(payload->>'registro', '') <> ''
                OR COALESCE(payload->>'csv', '') <> ''
              )
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"id": case_id},
    ).fetchone()
    submission_payload = submission_row[0] if submission_row else None
    if isinstance(submission_payload, str):
        try:
            submission_payload = json.loads(submission_payload)
        except json.JSONDecodeError:
            submission_payload = None
    if not isinstance(submission_payload, dict):
        raise HTTPException(
            status_code=409,
            detail="No existe una presentación externa pendiente verificable",
        )
    return {
        "authority": verify_signed_case_authority(conn, case_id),
        "submission": submission_payload,
    }



# =========================
# RTM CORE — ALTA PREVIA Y AUTORIZACIÓN GENÉRICA
# =========================
def _rtm_next_path(department: str, case_type: str) -> str:
    if department == "traffic":
        return "/eliminar-coche" if case_type == "vehicle_removal" else "/multas/documentos"
    if department == "debt":
        return "/deudas/documentos"
    if department == "administration":
        return "/administracion/documentos"
    if department == "claims":
        return "/reclamaciones/documentos"
    return "/otros/documentos"


def _rtm_auth_scope(department: str) -> str:
    if department == "traffic":
        return (
            "actuar ante la DGT, ayuntamientos y otros organismos sancionadores, "
            "incluyendo alegaciones, recursos y solicitudes vinculadas al expediente."
        )
    if department == "debt":
        return (
            "actuar ante Equifax, ASNEF, Experian, BADEXCUG, acreedores y entidades financieras, "
            "incluyendo derechos de acceso, rectificación, supresión, oposición y reclamación."
        )
    if department == "administration":
        return (
            "actuar ante la AEAT, Seguridad Social, ayuntamientos y demás administraciones "
            "u organismos públicos relacionados con el expediente."
        )
    if department == "claims":
        return (
            "actuar ante compañías aéreas, aseguradoras, empresas, organismos de consumo "
            "y otras entidades relacionadas con la reclamación."
        )
    return "realizar las gestiones extrajudiciales y administrativas necesarias para este expediente."


async def _rtm_store_file(case_id: str, file: UploadFile, kind: str, folder: str):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"El archivo {kind} está vacío")
    filename = (file.filename or kind).replace("/", "_").replace("\\", "_")[:140]
    mime = file.content_type or "application/octet-stream"
    ext = ".bin"
    if "." in filename:
        candidate = "." + filename.rsplit(".", 1)[-1].lower()
        if 2 <= len(candidate) <= 10:
            ext = candidate
    bucket, key = upload_bytes(case_id, folder, content, ext, mime)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
            VALUES (:case_id, :kind, :bucket, :key, :mime, :size_bytes, NOW())
        """), {
            "case_id": case_id, "kind": kind, "bucket": bucket, "key": key,
            "mime": mime, "size_bytes": len(content)
        })
    return {"kind": kind, "bucket": bucket, "key": key, "mime": mime, "size_bytes": len(content)}


@router.post("/intake-draft")
async def create_rtm_intake_draft(
    department: str = Form(...),
    case_type: str = Form(...),
    source_module: str = Form("rtm_web"),
    public_service_family: str = Form(""),
    full_name: str = Form(...),
    dni_nie: str = Form(...),
    domicilio_notif: str = Form(...),
    street: str = Form(...),
    street_number: str = Form(...),
    floor: str = Form(""),
    door: str = Form(""),
    postal_code: str = Form(...),
    city: str = Form(...),
    province: str = Form(...),
    email: EmailStr = Form(...),
    telefono: str = Form(...),
    preferred_contact: str = Form("email"),
    customer_comment: str = Form(...),
    representation_confirmed: bool = Form(...),
    privacy_accepted: bool = Form(...),
    dni_front: UploadFile = File(...),
    dni_back: UploadFile = File(...),
):
    department = (department or "").strip().lower()
    case_type = (case_type or "").strip().lower()
    public_service_family = (public_service_family or "").strip().lower()
    if department not in {"traffic", "debt", "administration", "claims", "other"}:
        raise HTTPException(status_code=400, detail="Departamento RTM no válido")
    if public_service_family and public_service_family not in PUBLIC_SERVICE_FAMILY_CODES:
        raise HTTPException(status_code=400, detail="Familia pública RTM no válida")
    if not representation_confirmed or not privacy_accepted:
        raise HTTPException(status_code=400, detail="Faltan consentimientos obligatorios")

    case_id = str(uuid.uuid4())
    case_access_token = issue_case_access_token(case_id)
    interested = {
        "full_name": full_name.strip(),
        "dni_nie": dni_nie.strip().upper(),
        "dni": dni_nie.strip().upper(),
        "domicilio_notif": domicilio_notif.strip(),
        "domicilio": domicilio_notif.strip(),
        "address": {
            "street": street.strip(), "street_number": street_number.strip(),
            "floor": floor.strip(), "door": door.strip(),
            "postal_code": postal_code.strip(), "city": city.strip(), "province": province.strip()
        },
        "email": str(email).strip(),
        "telefono": telefono.strip(),
        "preferred_contact": preferred_contact.strip().lower(),
        "customer_comment": customer_comment.strip(),
        "department": department,
        "case_type": case_type,
        "source_module": (source_module or "rtm_web").strip().lower(),
    }
    if public_service_family:
        interested["public_service_family"] = public_service_family

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO cases(
                id, contact_email, contact_name, status, payment_status, authorized,
                interested_data, department, case_type, customer_comment, source_module,
                category, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), :email, :name, 'authorization_pending', NULL, FALSE,
                CAST(:interested AS JSONB), :department, :case_type, :comment, :source,
                :category, NOW(), NOW()
            )
        """), {
            "id": case_id, "email": str(email).strip(), "name": full_name.strip(),
            "interested": json.dumps(interested, ensure_ascii=False),
            "department": department, "case_type": case_type,
            "comment": customer_comment.strip(),
            "source": (source_module or "rtm_web").strip().lower(),
            "category": department,
        })
        conn.execute(text("""
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (CAST(:id AS UUID), 'rtm_intake_created', CAST(:payload AS JSONB), NOW())
        """), {
            "id": case_id,
            "payload": json.dumps({
                "department": department,
                "case_type": case_type,
                "public_service_family": public_service_family or None,
            }),
        })

    docs = [
        await _rtm_store_file(case_id, dni_front, "identity_front", "identity"),
        await _rtm_store_file(case_id, dni_back, "identity_back", "identity"),
    ]
    _event(case_id, "rtm_identity_documents_saved", {"documents": docs})

    return {
        "ok": True,
        "case_id": case_id,
        "case_access_token": case_access_token,
        "case_access_token_header": "X-RTM-Case-Token",
        "status": "authorization_pending",
        "authorized": False,
        "authorization_download_url": f"/cases/{case_id}/rtm-authorization-pdf",
        "next_path": _rtm_next_path(department, case_type),
    }


@router.get("/{case_id}/rtm-authorization-pdf")
def download_rtm_authorization_pdf(
    case_id: str,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT COALESCE(interested_data, '{}'::jsonb), COALESCE(department, ''), COALESCE(case_type, '')
            FROM cases WHERE id=:id
        """), {"id": case_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    interested = row[0] if isinstance(row[0], dict) else {}
    department = row[1] or interested.get("department") or "other"
    case_type = row[2] or interested.get("case_type") or ""
    name = interested.get("full_name") or ""
    dni = interested.get("dni_nie") or interested.get("dni") or ""
    address = interested.get("domicilio_notif") or interested.get("domicilio") or ""
    email = interested.get("email") or ""
    phone = interested.get("telefono") or ""

    body = f"""
AUTORIZACIÓN DE REPRESENTACIÓN RTM

Expediente RTM: {case_id}
Departamento: {department}
Tipo de expediente: {case_type}

DATOS DEL INTERESADO

Nombre y apellidos: {name}
DNI/NIE/Pasaporte: {dni}
Domicilio: {address}
Email: {email}
Teléfono: {phone}

AUTORIZACIÓN

Yo, {name}, con documento identificativo {dni}, autorizo expresamente a
LA TALAMANQUINA, S.L. (RTM / RecurreTuMulta), con NIF B75440115, para {_rtm_auth_scope(department)}

Esta autorización queda limitada exclusivamente a las actuaciones necesarias para la gestión
del expediente RTM {case_id} y no comprende facultades ajenas a dicho asunto.

El interesado declara que los datos y documentos aportados son veraces y que dispone de
legitimación suficiente para solicitar la gestión.

Firma del interesado:



____________________________________

Nombre: {name}
DNI/NIE/Pasaporte: {dni}
Fecha: _____________________________
"""
    pdf_bytes = build_pdf("AUTORIZACIÓN DE REPRESENTACIÓN RTM", body)
    headers = {"Content-Disposition": f'attachment; filename="autorizacion_RTM_{case_id}.pdf"'}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


# =========================
# CONTACTO (PRE-PAGO)
# =========================
@router.post("/{case_id}/contact")
def save_case_contact(
    case_id: str,
    data: CaseContactIn,
    background_tasks: BackgroundTasks,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
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
def save_case_details(
    case_id: str,
    data: CaseDetailsIn,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """
    Guarda los datos del interesado antes de generar la autorización.
    Alimenta autorización, pago y recurso.
    """
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()

    with engine.begin() as conn:
        meta = _case_exists(conn, case_id)
        interested = dict(meta.get("interested_data") or {})
        previous_identity = {
            "full_name": str(interested.get("full_name") or "").strip(),
            "dni_nie": str(interested.get("dni_nie") or "").strip().upper(),
            "domicilio_notif": str(interested.get("domicilio_notif") or "").strip(),
            "email": str(interested.get("email") or "").strip().lower(),
        }

        interested.update(
            {
                "full_name": data.full_name.strip(),
                "dni_nie": data.dni_nie.strip().upper(),
                "dni": data.dni_nie.strip().upper(),
                "matricula": (data.matricula or "").strip().upper() or interested.get("matricula"),
                "domicilio_notif": data.domicilio_notif.strip(),
                "domicilio": data.domicilio_notif.strip(),
                "email": str(data.email).strip(),
                "telefono": (data.telefono or "").strip() or None,
                "authorization_checks": {
                    "autorizo_gestion": bool(data.autorizo_gestion),
                    "acepto_responsabilidad": bool(data.acepto_responsabilidad),
                },
            }
        )

        current_identity = {
            "full_name": str(interested.get("full_name") or "").strip(),
            "dni_nie": str(interested.get("dni_nie") or "").strip().upper(),
            "domicilio_notif": str(interested.get("domicilio_notif") or "").strip(),
            "email": str(interested.get("email") or "").strip().lower(),
        }
        authority_invalidated = bool(meta.get("authorized")) and previous_identity != current_identity

        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = CAST(:interested AS JSONB),
                    contact_name = :contact_name,
                    contact_email = :contact_email,
                    authorized = CASE WHEN :authority_invalidated THEN FALSE ELSE authorized END,
                    authorized_at = CASE WHEN :authority_invalidated THEN NULL ELSE authorized_at END,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": case_id,
                "interested": json.dumps(interested, ensure_ascii=False),
                "contact_name": interested.get("full_name"),
                "contact_email": interested.get("email"),
                "authority_invalidated": authority_invalidated,
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
                        "matricula": interested.get("matricula"),
                        "email": interested.get("email"),
                        "telefono": interested.get("telefono"),
                        "authorization_checks": interested.get("authorization_checks"),
                    },
                    ensure_ascii=False,
                ),
            },
        )

        if authority_invalidated:
            _event_on_conn(
                conn,
                case_id,
                "case_authority_invalidated_by_identity_change",
                {
                    "authority_version": AUTHORITY_VERSION,
                    "reason": "identity_material_changed",
                },
            )

    return {"ok": True, "case_id": case_id, "interested_data": interested}


# =========================
# AÑADIR DOCUMENTOS
# =========================
@router.post("/{case_id}/append-documents")
async def append_documents(
    case_id: str,
    files: List[UploadFile] = File(...),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_APPEND_FILES} documentos por subida.")

    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        case_row = conn.execute(
            text(
                "SELECT COALESCE(department,''), COALESCE(case_type,'') "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

    department = (case_row[0] or "").strip().lower() if case_row else ""
    case_type = (case_row[1] or "").strip().lower() if case_row else ""
    is_traffic_fine = (
        department == "traffic"
        and case_type in {"fine", "multa", "multas", "sanction", "sancion", "sanción"}
    )

    _event(case_id, "append_documents_case_type_detected", {
        "department": department,
        "case_type": case_type,
        "is_traffic_fine": is_traffic_fine,
    })

    uploaded_docs = []
    analyzed_docs = []
    for uf in files:
        data = await uf.read()
        if not data:
            continue

        filename = (uf.filename or "documento").replace("/", "_").replace("\\", "_")[:140]
        ext = ".bin"
        if "." in filename:
            candidate = "." + filename.rsplit(".", 1)[-1].lower()
            if 2 <= len(candidate) <= 10:
                ext = candidate
        mime = uf.content_type or "application/octet-stream"

        b2_bucket, b2_key = upload_bytes(case_id, "original", data, ext, mime)

        uploaded_docs.append({"bucket": b2_bucket, "key": b2_key, "filename": filename, "mime": mime})

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

        # RTM CORE -> motor legacy de multas, SOLO para traffic/fine.
        # El mismo case_id recibe la extraction que luego consume generate.py.
        if is_traffic_fine:
            try:
                analysis_result = analyze_existing_case_document(
                    case_id=case_id,
                    content=data,
                    filename=filename,
                    mime=mime,
                    b2_bucket=b2_bucket,
                    b2_key=b2_key,
                )
                analyzed_docs.append({
                    "filename": filename,
                    "ok": True,
                    "tipo_infraccion": (
                        ((analysis_result.get("extracted") or {}).get("extracted") or {}).get("tipo_infraccion")
                    ),
                })
            except HTTPException:
                raise
            except Exception as exc:
                _event(case_id, "traffic_fine_analysis_failed", {
                    "filename": filename,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                raise HTTPException(
                    status_code=500,
                    detail=f"No se pudo analizar la multa {filename}: {type(exc).__name__}: {exc}",
                )

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status='uploaded', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )

    _event(case_id, "expediente_documents_appended", {
        "documents": uploaded_docs,
        "traffic_fine_analysis": analyzed_docs if is_traffic_fine else [],
    })
    return {
        "ok": True,
        "traffic_fine_analyzed": bool(is_traffic_fine and analyzed_docs),
        "analyzed_documents": analyzed_docs,
    }

# =========================
# REVIEW
# =========================
@router.post("/{case_id}/review")
def review_case(
    case_id: str,
    background_tasks: BackgroundTasks,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
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
def public_status(
    case_id: str,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
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
async def authorize_case(
    case_id: str,
    consent: AuthorizationConsentIn,
    request: Request,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
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

        ip = get_request_ip(request)
        accepted_at = datetime.now(timezone.utc).isoformat()
        authority_payload = build_case_authority_payload(
            case_id=case_id,
            interested=interested,
            accepted_at=accepted_at,
            request_ip=ip,
        )
        authority_material = authority_payload["material"]

        try:
            auth_doc = ensure_authorization_pdf(
                conn,
                case_id=case_id,
                request=request,
                version=consent.authority_version,
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error generando PDF de autorización: {type(e).__name__}: {e}",
            )

        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = CAST(:accepted_at AS TIMESTAMPTZ),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": case_id, "accepted_at": accepted_at},
        )
        _event_on_conn(conn, case_id, "case_authorized", authority_payload)

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "authority_id": authority_material["authority_id"],
        "authority_version": authority_material["authority_version"],
        "authority_material_sha256": authority_payload["material_sha256"],
        "authorization_pdf": auth_doc.get("document"),
        "download_url": f"/cases/{case_id}/authorization-pdf",
    }

@router.get("/{case_id}/authorization-pdf")
def download_authorization_pdf(
    case_id: str,
    request: Request,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """
    Devuelve el PDF de autorización ya relleno para descargar y firmar.
    """
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)
        ip = get_request_ip(request)
        case_meta = _get_case_snapshot(conn, case_id)
        payload = _authorization_payload_from_case(case_meta, ip=ip, version=AUTHORITY_VERSION)

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
async def _store_authorization_signed(
    case_id: str,
    file: UploadFile,
    x_case_token: Optional[str],
):
    """Adjunta el PDF firmado a una autorización digital ya verificable."""
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()

    with engine.begin() as conn:
        verify_active_case_authority(conn, case_id)

    data = await file.read()
    content_type = file.content_type or ""
    document_sha256 = _validate_public_pdf(data, content_type)

    try:
        b2_bucket, b2_key = upload_bytes(
            case_id, "authorization_signed", data, ".pdf", "application/pdf"
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo custodiar el PDF firmado: {type(e).__name__}",
        ) from e

    with engine.begin() as conn:
        conn.execute(
            text("SELECT id FROM cases WHERE id=:id FOR UPDATE"),
            {"id": case_id},
        )
        authority_payload = verify_active_case_authority(conn, case_id)
        document_row = conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:id, 'authorization_signed', :b, :k, 'application/pdf', :s, NOW())
                RETURNING id
                """
            ),
            {
                "id": case_id,
                "b": b2_bucket,
                "k": b2_key,
                "s": len(data),
            },
        ).fetchone()
        if not document_row:
            raise HTTPException(status_code=409, detail="No se registró el PDF firmado")
        uploaded_at = datetime.now(timezone.utc).isoformat()
        signed_attestation = build_signed_authority_document_attestation(
            case_id=case_id,
            authority_payload=authority_payload,
            document_id=str(document_row[0]),
            document_sha256=document_sha256,
            size_bytes=len(data),
            storage_bucket=b2_bucket,
            storage_key=b2_key,
            uploaded_at=uploaded_at,
        )
        _event_on_conn(
            conn,
            case_id,
            "authorization_signed_uploaded",
            signed_attestation,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "document": {
            "kind": "authorization_signed",
            "mime": "application/pdf",
            "size_bytes": len(data),
            "sha256": document_sha256,
            "attestation_sha256": signed_attestation["material_sha256"],
        },
    }


@router.post("/{case_id}/upload-authorization-signed")
async def upload_authorization_signed_legacy(
    case_id: str,
    file: UploadFile = File(...),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    return await _store_authorization_signed(case_id, file, x_case_token)


@router.post("/{case_id}/authorization-signed")
async def upload_authorization_signed(
    case_id: str,
    file: UploadFile = File(...),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    return await _store_authorization_signed(case_id, file, x_case_token)


@router.post("/{case_id}/upload-receipt")
async def upload_receipt(
    case_id: str,
    file: UploadFile = File(...),
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    case_id = require_operator_case_access(case_id, x_operator_token)
    engine = get_engine()

    data = await file.read()
    receipt_sha256 = _validate_public_pdf(data, file.content_type or "")

    with engine.begin() as conn:
        _require_receipt_upload_state(conn, case_id)

    try:
        b2_bucket, b2_key = upload_bytes(
            case_id, "receipt", data, ".pdf", "application/pdf"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo custodiar el justificante: {type(exc).__name__}",
        ) from exc

    with engine.begin() as conn:
        state_evidence = _require_receipt_upload_state(conn, case_id)

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

        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET status='submitted', updated_at=NOW()
                WHERE id=:id AND status='submission_receipt_pending'
                RETURNING id
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Transición de presentación en conflicto")

        _event_on_conn(
            conn,
            case_id,
            "submission_receipt_uploaded",
            {
                "evidence_kind": "submission_receipt",
                "receipt_sha256": receipt_sha256,
                "size_bytes": len(data),
                "registro": state_evidence["submission"].get("registro"),
                "csv": state_evidence["submission"].get("csv"),
                "resource_id": state_evidence["submission"].get("resource_id"),
                "authority_material_sha256": state_evidence["authority"]["material_sha256"],
                "transition": {
                    "from": "submission_receipt_pending",
                    "to": "submitted",
                },
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": "submitted",
        "receipt_sha256": receipt_sha256,
    }
