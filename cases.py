import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Header, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from database import get_engine
from b2_storage import (
    B2ObjectTooLargeError,
    delete_object,
    download_bytes_limited,
    upload_bytes,
)
from email_utils import send_email
from rtm_core.runtime_capabilities import capability_state, require_http_capability
from rtm_core.case_state_policy import lock_case_for_public_material_mutation
from rtm_core.service_catalog import validate_public_intake_classification
from rtm_core.trusted_origins import trusted_frontend_origin
from case_authority import (
    AUTHORITY_VERSION,
    build_authorization_signature_candidate_attestation,
    build_case_authority_payload,
    require_authorization_candidate_digest_unused,
    require_dgt_fine_authority_scope,
    require_authority_document_binding,
    verify_active_case_authority,
    verify_active_authority_document_issue,
    verify_authorization_signature_candidate,
    verify_signed_case_authority,
)
from public_case_access import (
    issue_case_access_token,
    require_case_access_token,
    require_operator_case_access,
    require_public_case_access_configured,
)

# Import interno del engine (Modo Dios)
from ai.expediente_engine import run_expediente_ai
from authorization_pdf import ensure_authorization_pdf, get_request_ip
from pdf_builder import build_pdf
from analyze import analyze_existing_case_document
from rtm_core.upload_security import (
    PDF,
    SAFE_DOCUMENT_MIMES,
    SAFE_IMAGE_OR_PDF_MIMES,
    UploadSecurityError,
    ValidatedUpload,
    read_upload_limited,
    validate_document_bytes,
)

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_APPEND_FILES = 5
MAX_IDENTITY_FILE_BYTES = 8 * 1024 * 1024
MAX_APPEND_FILE_BYTES = 8 * 1024 * 1024
MAX_APPEND_TOTAL_BYTES = 20 * 1024 * 1024
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
DOCUMENT_CUSTODY = "rtm_internal_only"
PRIVATE_DOCUMENT_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Vary": "X-RTM-Case-Token",
    "X-Content-Type-Options": "nosniff",
}
def _document_projection(
    document_id: str,
    document_sha256: str,
    mime: str,
    size_bytes: int,
) -> Dict[str, Any]:
    return {
        "id": str(document_id),
        "sha256": str(document_sha256),
        "mime": str(mime),
        "size_bytes": int(size_bytes),
        "custody": DOCUMENT_CUSTODY,
    }

# =========================
# EMAILS AUTOMÁTICOS (SILENCIOSO)
# =========================
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _case_link(case_id: str) -> str:
    base = trusted_frontend_origin()
    token = issue_case_access_token(case_id)
    # The route and case identifier must reach the hosting layer so its
    # no-store/noindex policy applies.  The bearer capability stays in the
    # fragment, which browsers do not send in HTTP requests or referrers; the
    # frontend consumes it once and immediately scrubs it from history.
    return f"{base}/resumen?case={case_id}#access_token={token}"

def _send_email(to_email: str, subject: str, body: str) -> None:
    if not to_email or not capability_state("outbound_email").enabled:
        return
    try:
        send_email(to_email=to_email, subject=subject, body=body)
    except Exception:
        # Las notificaciones no deben romper el flujo principal del expediente.
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
class _StrictPublicInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CaseDetailsIn(_StrictPublicInput):
    full_name: str = Field(min_length=1, max_length=160)
    dni_nie: str = Field(min_length=3, max_length=32)
    matricula: Optional[str] = Field(default=None, max_length=20)
    domicilio_notif: str = Field(min_length=3, max_length=500)
    email: EmailStr = Field(max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=40)
    autorizo_gestion: Optional[bool] = None
    acepto_responsabilidad: Optional[bool] = None

class CaseContactIn(_StrictPublicInput):
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)


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


def _lock_case_for_material_mutation(conn, case_id: str) -> None:
    lock_case_for_public_material_mutation(conn, case_id)

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


def _mark_authorization_evidence_stale(conn, case_id: str) -> None:
    """Preserve prior evidence while making it ineligible for every gate."""

    conn.execute(
        text(
            """
            UPDATE documents
            SET kind = CASE kind
                WHEN 'authorization_signed_candidate'
                    THEN 'authorization_signed_candidate_stale'
                WHEN 'authorization_signed'
                    THEN 'authorization_signed_stale'
                WHEN 'authorization_signed_rejected'
                    THEN 'authorization_signed_rejected_stale'
                ELSE kind
            END
            WHERE case_id=:case_id
              AND kind IN (
                  'authorization_signed_candidate',
                  'authorization_signed',
                  'authorization_signed_rejected'
              )
            """
        ),
        {"case_id": case_id},
    )


def _claim_contact_notification(conn, case_id: str, *, changed: bool) -> bool:
    """Reserva como máximo un correo de contacto cada quince minutos.

    El bloqueo transaccional evita que dos peticiones simultáneas superen el
    cooldown. La marca no contiene nombre, correo ni otro dato personal.
    """

    if not changed:
        return False
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"rtm:contact-email:{case_id}"},
    )
    recent = conn.execute(
        text(
            """
            SELECT 1
            FROM events
            WHERE case_id = :id
              AND type = 'contact_notification_queued'
              AND created_at >= NOW() - INTERVAL '15 minutes'
            LIMIT 1
            """
        ),
        {"id": case_id},
    ).fetchone()
    if recent:
        return False
    _event_on_conn(conn, case_id, "contact_notification_queued", {})
    return True


def _upload_http_error(exc: UploadSecurityError) -> HTTPException:
    safe_detail = {
        400: "Archivo vacío o no recibido",
        413: "Archivo demasiado grande",
        415: "Formato de archivo no permitido",
        422: "El archivo no ha superado la validación de seguridad",
        503: "Validación de archivos no disponible",
    }.get(exc.status_code, "El archivo no ha superado la validación de seguridad")
    return HTTPException(status_code=exc.status_code, detail=safe_detail)


async def _read_validated_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    allowed_mimes,
) -> tuple[bytes, ValidatedUpload]:
    try:
        data = await read_upload_limited(file, max_bytes=max_bytes)
        validated = await run_in_threadpool(
            validate_document_bytes,
            filename=file.filename,
            declared_mime=file.content_type,
            data=data,
            max_bytes=max_bytes,
            allowed_mimes=allowed_mimes,
        )
    except UploadSecurityError as exc:
        raise _upload_http_error(exc) from exc
    return data, validated


def _validate_public_pdf(
    data: bytes,
    content_type: str | None,
    filename: str | None = "documento.pdf",
) -> str:
    try:
        validated = validate_document_bytes(
            filename=filename or "documento.pdf",
            declared_mime=content_type,
            data=data,
            max_bytes=MAX_PUBLIC_PDF_BYTES,
            allowed_mimes={PDF},
        )
    except UploadSecurityError as exc:
        raise _upload_http_error(exc) from exc
    return validated.sha256


def _bounded_form_text(
    value: str | None,
    *,
    field: str,
    max_length: int,
    required: bool = True,
    multiline: bool = False,
) -> str:
    raw_value = str(value or "")
    if len(raw_value) > max_length:
        raise HTTPException(status_code=422, detail=f"El campo {field} es demasiado largo")
    normalized = raw_value.strip()
    if required and not normalized:
        raise HTTPException(status_code=400, detail=f"El campo {field} es obligatorio")
    allowed_controls = {"\r", "\n", "\t"} if multiline else set()
    if any((ord(ch) < 32 or ord(ch) == 127) and ch not in allowed_controls for ch in normalized):
        raise HTTPException(status_code=422, detail=f"El campo {field} contiene caracteres no válidos")
    return normalized


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


def _rtm_store_validated_file(
    case_id: str,
    *,
    content: bytes,
    validated: ValidatedUpload,
    kind: str,
    folder: str,
):
    try:
        bucket, key = upload_bytes(
            case_id,
            folder,
            content,
            validated.extension,
            validated.mime,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="No se pudo custodiar el documento",
        ) from exc
    engine = get_engine()
    try:
        with engine.begin() as conn:
            document_row = conn.execute(text("""
                INSERT INTO documents(
                    case_id, kind, b2_bucket, b2_key, mime, size_bytes, sha256, created_at
                )
                VALUES (
                    :case_id, :kind, :bucket, :key, :mime, :size_bytes, :sha256, NOW()
                )
                RETURNING id
            """), {
                "case_id": case_id, "kind": kind, "bucket": bucket, "key": key,
                "mime": validated.mime, "size_bytes": len(content), "sha256": validated.sha256,
            }).fetchone()
            if not document_row:
                raise RuntimeError("El documento no fue registrado")
    except Exception as exc:
        _cleanup_b2_objects([(bucket, key)])
        raise HTTPException(
            status_code=503,
            detail="No se pudo registrar el documento custodiado",
        ) from exc
    return {
        "kind": kind,
        **_document_projection(
            str(document_row[0]), validated.sha256, validated.mime, len(content)
        ),
    }


async def _rtm_store_file(
    case_id: str,
    file: UploadFile,
    kind: str,
    folder: str,
    *,
    max_bytes: int = MAX_IDENTITY_FILE_BYTES,
    allowed_mimes=SAFE_IMAGE_OR_PDF_MIMES,
):
    content, validated = await _read_validated_upload(
        file,
        max_bytes=max_bytes,
        allowed_mimes=allowed_mimes,
    )
    return _rtm_store_validated_file(
        case_id,
        content=content,
        validated=validated,
        kind=kind,
        folder=folder,
    )


def _cleanup_b2_objects(coordinates: List[tuple[str, str]]) -> None:
    """Compensación best-effort; nunca oculta la causa original del fallo."""

    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass


def _persist_rtm_intake_draft(
    case_id: str,
    case_record: Dict[str, Any],
    stored_identity: List[tuple[str, str, bytes, ValidatedUpload, str]],
    intake_event: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Publica caso, documentos y eventos en una sola transacción SQL."""

    docs: List[Dict[str, Any]] = []
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
        """), {"id": case_id, **case_record})
        for bucket, key, content, validated, kind in stored_identity:
            document_row = conn.execute(text("""
                INSERT INTO documents(
                    case_id, kind, b2_bucket, b2_key, mime,
                    size_bytes, sha256, created_at
                ) VALUES (
                    CAST(:case_id AS UUID), :kind, :bucket, :key, :mime,
                    :size_bytes, :sha256, NOW()
                )
                RETURNING id
            """), {
                "case_id": case_id,
                "kind": kind,
                "bucket": bucket,
                "key": key,
                "mime": validated.mime,
                "size_bytes": len(content),
                "sha256": validated.sha256,
            }).fetchone()
            if not document_row:
                raise RuntimeError("No se registró el documento de identidad")
            docs.append({
                "kind": kind,
                **_document_projection(
                    str(document_row[0]),
                    validated.sha256,
                    validated.mime,
                    len(content),
                ),
            })
        _event_on_conn(conn, case_id, "rtm_intake_created", intake_event)
        _event_on_conn(
            conn,
            case_id,
            "rtm_identity_documents_saved",
            {"documents": docs},
        )
    return docs


@router.post("/intake-draft")
async def create_rtm_intake_draft(
    department: str = Form(..., max_length=32),
    case_type: str = Form(..., max_length=64),
    source_module: str = Form("rtm_web", max_length=64),
    public_service_family: str = Form(""),
    full_name: str = Form(..., max_length=160),
    dni_nie: str = Form(..., max_length=32),
    domicilio_notif: str = Form(..., max_length=500),
    street: str = Form(..., max_length=200),
    street_number: str = Form(..., max_length=20),
    floor: str = Form("", max_length=20),
    door: str = Form("", max_length=20),
    postal_code: str = Form(..., max_length=20),
    city: str = Form(..., max_length=120),
    province: str = Form(..., max_length=120),
    email: EmailStr = Form(..., max_length=254),
    telefono: str = Form(..., max_length=40),
    preferred_contact: str = Form("email", max_length=20),
    customer_comment: str = Form(..., max_length=5000),
    representation_confirmed: bool = Form(...),
    prejudicial_counsel_requested: bool = Form(False),
    privacy_accepted: bool = Form(...),
    dni_front: UploadFile = File(...),
    dni_back: UploadFile = File(...),
):
    department = _bounded_form_text(
        department, field="department", max_length=32
    ).lower()
    case_type = _bounded_form_text(
        case_type, field="case_type", max_length=64
    ).lower()
    source_module = _bounded_form_text(
        source_module or "rtm_web", field="source_module", max_length=64
    ).lower()
    public_service_family = _bounded_form_text(
        public_service_family,
        field="public_service_family",
        max_length=32,
        required=False,
    ).lower()
    full_name = _bounded_form_text(full_name, field="full_name", max_length=160)
    dni_nie = _bounded_form_text(dni_nie, field="dni_nie", max_length=32).upper()
    domicilio_notif = _bounded_form_text(
        domicilio_notif, field="domicilio_notif", max_length=500
    )
    street = _bounded_form_text(street, field="street", max_length=200)
    street_number = _bounded_form_text(
        street_number, field="street_number", max_length=20
    )
    floor = _bounded_form_text(
        floor, field="floor", max_length=20, required=False
    )
    door = _bounded_form_text(
        door, field="door", max_length=20, required=False
    )
    postal_code = _bounded_form_text(
        postal_code, field="postal_code", max_length=20
    )
    city = _bounded_form_text(city, field="city", max_length=120)
    province = _bounded_form_text(province, field="province", max_length=120)
    email_text = _bounded_form_text(str(email), field="email", max_length=254)
    telefono = _bounded_form_text(telefono, field="telefono", max_length=40)
    preferred_contact = _bounded_form_text(
        preferred_contact, field="preferred_contact", max_length=20
    ).lower()
    customer_comment = _bounded_form_text(
        customer_comment,
        field="customer_comment",
        max_length=5000,
        required=False,
        multiline=True,
    )
    if department not in {"traffic", "debt", "administration", "claims", "other"}:
        raise HTTPException(status_code=400, detail="Departamento RTM no válido")
    if public_service_family and public_service_family not in PUBLIC_SERVICE_FAMILY_CODES:
        raise HTTPException(status_code=400, detail="Familia pública RTM no válida")
    try:
        department, case_type = validate_public_intake_classification(
            department,
            case_type,
            public_service_family,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="La clasificación del servicio no es válida",
        ) from exc
    if preferred_contact not in {"email", "phone", "whatsapp"}:
        raise HTTPException(status_code=400, detail="Preferencia de contacto no válida")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", source_module):
        raise HTTPException(status_code=400, detail="Módulo de origen no válido")
    # Este valor legacy expresa como máximo intención de continuar: no se
    # persiste ni se usa como apoderamiento. La representación DGT solo nace en
    # /authorize y queda ligada a su PDF/candidato/revisión firmada. Otros
    # servicios no deben verse obligados a afirmar una representación DGT.
    del representation_confirmed
    if not privacy_accepted:
        raise HTTPException(status_code=400, detail="Falta aceptar la privacidad")

    # Las capacidades de custodia y token se comprueban antes de materializar
    # uploads o abrir una transacción. Una configuración incompleta no puede
    # dejar PII ni filas huérfanas.
    require_public_case_access_configured()
    require_http_capability("b2")

    # Ambos documentos se validan antes de crear el caso o escribir en B2.
    prepared_identity = [
        await _read_validated_upload(
            dni_front,
            max_bytes=MAX_IDENTITY_FILE_BYTES,
            allowed_mimes=SAFE_IMAGE_OR_PDF_MIMES,
        ),
        await _read_validated_upload(
            dni_back,
            max_bytes=MAX_IDENTITY_FILE_BYTES,
            allowed_mimes=SAFE_IMAGE_OR_PDF_MIMES,
        ),
    ]

    case_id = str(uuid.uuid4())
    case_access_token = issue_case_access_token(case_id)
    interested = {
        "full_name": full_name,
        "dni_nie": dni_nie,
        "dni": dni_nie,
        "domicilio_notif": domicilio_notif,
        "domicilio": domicilio_notif,
        "address": {
            "street": street, "street_number": street_number,
            "floor": floor, "door": door,
            "postal_code": postal_code, "city": city, "province": province
        },
        "email": email_text,
        "telefono": telefono,
        "preferred_contact": preferred_contact,
        "customer_comment": customer_comment,
        "department": department,
        "case_type": case_type,
        "source_module": source_module,
        # Es una solicitud informativa, no consentimiento ni apoderamiento.
        "prejudicial_counsel_requested": bool(
            prejudicial_counsel_requested
        ),
    }
    if public_service_family:
        interested["public_service_family"] = public_service_family

    stored_identity: List[tuple[str, str, bytes, ValidatedUpload, str]] = []
    stored_coordinates: List[tuple[str, str]] = []
    try:
        for (content, validated), kind in zip(
            prepared_identity,
            ("identity_front", "identity_back"),
        ):
            bucket, key = await run_in_threadpool(
                upload_bytes,
                case_id,
                "identity",
                content,
                validated.extension,
                validated.mime,
            )
            stored_coordinates.append((bucket, key))
            stored_identity.append((bucket, key, content, validated, kind))
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise HTTPException(
            status_code=502,
            detail="No se pudieron custodiar los documentos de identidad",
        ) from exc

    case_record = {
        "email": email_text,
        "name": full_name,
        "interested": json.dumps(interested, ensure_ascii=False),
        "department": department,
        "case_type": case_type,
        "comment": customer_comment,
        "source": source_module,
        "category": department,
    }
    intake_event = {
        "department": department,
        "case_type": case_type,
        "public_service_family": public_service_family or None,
        "prejudicial_counsel_requested": bool(
            prejudicial_counsel_requested
        ),
    }
    try:
        # Caso, documentos y eventos forman una sola unidad de persistencia. B2
        # se ha preparado antes; cualquier rollback de BD retira esos objetos.
        await run_in_threadpool(
            _persist_rtm_intake_draft,
            case_id,
            case_record,
            stored_identity,
            intake_event,
        )
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise HTTPException(
            status_code=503,
            detail="No se pudo registrar el expediente de forma segura",
        ) from exc

    return {
        "ok": True,
        "case_id": case_id,
        "case_access_token": case_access_token,
        "case_access_token_header": "X-RTM-Case-Token",
        "status": "authorization_pending",
        "authorized": False,
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
    headers = {
        **PRIVATE_DOCUMENT_HEADERS,
        "Content-Disposition": f'attachment; filename="autorizacion_RTM_{case_id}.pdf"',
    }
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
    name = data.name.strip()
    email = str(data.email).strip().lower()
    engine = get_engine()
    notify = False
    with engine.begin() as conn:
        _lock_case_for_material_mutation(conn, case_id)
        meta = _case_exists(conn, case_id)
        changed = (
            str(meta.get("contact_name") or "").strip() != name
            or str(meta.get("contact_email") or "").strip().lower() != email
        )
        conn.execute(
            text(
                "UPDATE cases SET contact_name=:n, contact_email=:e, updated_at=NOW() WHERE id=:id"
            ),
            {"id": case_id, "n": name, "e": email},
        )
        _event_on_conn(conn, case_id, "contact_saved", {"changed": changed})
        notify = _claim_contact_notification(conn, case_id, changed=changed)

    if notify:
        background_tasks.add_task(_email_contact_saved, case_id, name, email)
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
        _lock_case_for_material_mutation(conn, case_id)
        meta = _case_exists(conn, case_id)
        interested = dict(meta.get("interested_data") or {})
        previous_identity = {
            "full_name": str(interested.get("full_name") or "").strip(),
            "dni_nie": str(interested.get("dni_nie") or "").strip().upper(),
            "domicilio_notif": str(interested.get("domicilio_notif") or "").strip(),
            "email": str(interested.get("email") or "").strip().lower(),
            "telefono": str(interested.get("telefono") or "").strip(),
            "matricula": str(interested.get("matricula") or "").strip().upper(),
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
            "telefono": str(interested.get("telefono") or "").strip(),
            "matricula": str(interested.get("matricula") or "").strip().upper(),
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
                        "fields_present": sorted(
                            key
                            for key in (
                                "full_name",
                                "dni_nie",
                                "matricula",
                                "domicilio_notif",
                                "email",
                                "telefono",
                            )
                            if interested.get(key)
                        ),
                        "authorization_checks_recorded": bool(
                            interested.get("authorization_checks")
                        ),
                        "authority_invalidated": authority_invalidated,
                    },
                    ensure_ascii=False,
                ),
            },
        )

        if authority_invalidated:
            _mark_authorization_evidence_stale(conn, case_id)
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

    prepared_files: list[tuple[bytes, ValidatedUpload]] = []
    total_bytes = 0
    for upload in files:
        data, validated = await _read_validated_upload(
            upload,
            max_bytes=MAX_APPEND_FILE_BYTES,
            allowed_mimes=SAFE_DOCUMENT_MIMES,
        )
        total_bytes += len(data)
        if total_bytes > MAX_APPEND_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail="La subida supera el límite total permitido",
            )
        prepared_files.append((data, validated))

    # No se produce ningún evento, escritura en B2 o INSERT hasta que todo el lote
    # ha superado los límites y la validación por contenido/estructura.
    _event(case_id, "append_documents_case_type_detected", {
        "department": department,
        "case_type": case_type,
        "is_traffic_fine": is_traffic_fine,
    })

    uploaded_docs = []
    analyzed_docs = []
    for data, validated in prepared_files:
        filename = validated.filename
        mime = validated.mime
        try:
            b2_bucket, b2_key = upload_bytes(
                case_id,
                "original",
                data,
                validated.extension,
                mime,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="No se pudo custodiar el documento",
            ) from exc

        with engine.begin() as conn:
            document_row = conn.execute(
                text(
                    "INSERT INTO documents("
                    "case_id, kind, b2_bucket, b2_key, mime, size_bytes, sha256, created_at"
                    ") VALUES (:id,'original',:b,:k,:m,:s,:sha256,NOW()) "
                    "RETURNING id"
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": mime,
                    "s": len(data),
                    "sha256": validated.sha256,
                },
            ).fetchone()
        if not document_row:
            raise HTTPException(status_code=409, detail="No se registró el documento")
        uploaded_docs.append(
            _document_projection(
                str(document_row[0]), validated.sha256, mime, len(data)
            )
        )

        # RTM CORE -> motor legacy de multas, SOLO para traffic/fine.
        # El mismo case_id recibe la extraction que luego consume generate.py.
        if is_traffic_fine:
            try:
                analysis_result = await run_in_threadpool(
                    analyze_existing_case_document,
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
                    "error_type": type(exc).__name__,
                })
                raise HTTPException(
                    status_code=500,
                    detail="No se pudo analizar el documento",
                ) from exc

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

        candidate_row = conn.execute(
            text(
                """
                SELECT id FROM documents
                WHERE case_id=:id
                  AND kind='authorization_signed_candidate'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()
        pending_signature_candidate = False
        signed_authority_verified = False
        if bool(row[2]):
            try:
                verify_signed_case_authority(conn, case_id)
                signed_authority_verified = True
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
            if not signed_authority_verified and candidate_row:
                try:
                    verify_authorization_signature_candidate(
                        conn, case_id, str(candidate_row[0])
                    )
                    pending_signature_candidate = True
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise

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
    elif signed_authority_verified:
        msg = "Tu autorización firmada ha sido verificada."
    elif pending_signature_candidate:
        msg = "Tu autorización firmada está pendiente de revisión humana."
    elif authorized:
        msg = "La autorización digital está registrada; falta revisar la firma."
    else:
        msg = "Hemos analizado tu multa. Para continuar, necesitamos tus datos y autorización."

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "payment_status": payment_status,
        "authorized": authorized,
        "signed_authority_verified": signed_authority_verified,
        "authorization_evidence_status": (
            "verified"
            if signed_authority_verified
            else "pending_review"
            if pending_signature_candidate
            else "not_submitted"
        ),
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
def _authorize_case_transaction(
    engine,
    *,
    case_id: str,
    request: Request,
    authority_version: str,
) -> Dict[str, Any]:
    """Genera y registra autoridad como una unidad SQL con compensación B2."""

    uploaded_coordinates: List[tuple[str, str]] = []
    try:
        with engine.begin() as conn:
            _lock_case_for_material_mutation(conn, case_id)
            row = conn.execute(
                text(
                    """
                    SELECT COALESCE(interested_data, '{}'::jsonb),
                           COALESCE(department, ''), COALESCE(case_type, ''),
                           COALESCE(status, '')
                    FROM cases
                    WHERE id = :id
                    FOR UPDATE
                    """
                ),
                {"id": case_id},
            ).fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Expediente no encontrado")

            require_dgt_fine_authority_scope(row[1], row[2])
            interested = row[0] if isinstance(row[0], dict) else {}
            missing = [
                field
                for field in ("full_name", "dni_nie", "domicilio_notif", "email")
                if not interested.get(field)
            ]
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
            _mark_authorization_evidence_stale(conn, case_id)
            auth_doc = ensure_authorization_pdf(
                conn,
                case_id=case_id,
                request=request,
                version=authority_version,
                authority_payload=authority_payload,
                uploaded_coordinates=uploaded_coordinates,
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
    except HTTPException:
        _cleanup_b2_objects(uploaded_coordinates)
        raise
    except Exception as exc:
        _cleanup_b2_objects(uploaded_coordinates)
        raise HTTPException(
            status_code=500,
            detail="No se pudo generar el PDF de autorización",
        ) from exc

    return {
        "authority_payload": authority_payload,
        "authority_material": authority_material,
        "auth_doc": auth_doc,
    }


@router.post("/{case_id}/authorize")
async def authorize_case(
    case_id: str,
    consent: AuthorizationConsentIn,
    request: Request,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    result = await run_in_threadpool(
        _authorize_case_transaction,
        engine,
        case_id=case_id,
        request=request,
        authority_version=consent.authority_version,
    )
    authority_payload = result["authority_payload"]
    authority_material = result["authority_material"]
    auth_doc = result["auth_doc"]
    issuance = auth_doc["issuance"]
    issuance_material = issuance["material"]

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "authority_id": authority_material["authority_id"],
        "authority_version": authority_material["authority_version"],
        "authority_material_sha256": authority_payload["material_sha256"],
        "authorization_pdf": auth_doc.get("document"),
        "authorization_document_binding": {
            "authority_material_sha256": authority_payload["material_sha256"],
            "generated_document_id": issuance_material["document_id"],
            "generated_document_sha256": issuance_material["document_sha256"],
            "generated_document_version": issuance_material["document_version"],
            "document_nonce": issuance_material["document_nonce"],
            "issuance_attestation_sha256": issuance["material_sha256"],
        },
    }

@router.get("/{case_id}/authorization-pdf")
async def download_authorization_pdf(
    case_id: str,
    request: Request,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Devuelve exactamente el PDF emitido y ligado a la autoridad activa."""
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        authority = verify_active_case_authority(conn, case_id)
        issuance = verify_active_authority_document_issue(
            conn, case_id, authority=authority
        )
        issued = issuance["material"]
        row = conn.execute(
            text(
                """
                SELECT b2_bucket, b2_key, sha256, size_bytes, mime
                FROM documents
                WHERE case_id=:case_id
                  AND id=CAST(:document_id AS UUID)
                  AND kind='authorization_pdf'
                LIMIT 1
                """
            ),
            {"case_id": case_id, "document_id": issued["document_id"]},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="PDF de autorización no disponible")
    try:
        pdf_bytes = await run_in_threadpool(
            download_bytes_limited,
            str(row[0]),
            str(row[1]),
            max_bytes=MAX_PUBLIC_PDF_BYTES,
            case_id=case_id,
        )
    except B2ObjectTooLargeError as exc:
        raise HTTPException(status_code=409, detail="PDF de autorización no verificable") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo recuperar el PDF de autorización") from exc
    if (
        str(row[4] or "") != "application/pdf"
        or len(pdf_bytes) != int(row[3] or 0)
        or not hmac.compare_digest(
            hashlib.sha256(pdf_bytes).hexdigest(), str(row[2] or "").lower()
        )
        or not hmac.compare_digest(
            hashlib.sha256(pdf_bytes).hexdigest(),
            str(issued.get("document_sha256") or "").lower(),
        )
    ):
        raise HTTPException(status_code=409, detail="PDF de autorización no verificable")

    headers = {
        **PRIVATE_DOCUMENT_HEADERS,
        "Content-Disposition": f'attachment; filename="autorizacion_{case_id}.pdf"',
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)



# =========================
# SUBIR AUTORIZACIÓN FIRMADA
# =========================
async def _store_authorization_signed(
    case_id: str,
    file: UploadFile,
    x_case_token: Optional[str],
    *,
    authority_material_sha256: str,
    generated_document_id: str,
    generated_document_sha256: str,
    generated_document_version: str,
    document_nonce: str,
    issuance_attestation_sha256: str,
):
    """Custodia un candidato; nunca convierte una subida pública en firma válida."""
    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()

    with engine.begin() as conn:
        _lock_case_for_material_mutation(conn, case_id)
        authority_payload = verify_active_case_authority(conn, case_id)
        issuance_payload = verify_active_authority_document_issue(
            conn, case_id, authority=authority_payload
        )
        require_authority_document_binding(
            issuance_payload,
            authority_material_sha256=authority_material_sha256,
            generated_document_id=generated_document_id,
            generated_document_sha256=generated_document_sha256,
            generated_document_version=generated_document_version,
            document_nonce=document_nonce,
            issuance_attestation_sha256=issuance_attestation_sha256,
        )

    try:
        data = await read_upload_limited(file, max_bytes=MAX_PUBLIC_PDF_BYTES)
    except UploadSecurityError as exc:
        raise _upload_http_error(exc) from exc
    content_type = file.content_type or ""
    document_sha256 = await run_in_threadpool(
        _validate_public_pdf, data, content_type, file.filename
    )

    with engine.begin() as conn:
        _lock_case_for_material_mutation(conn, case_id)
        authority_payload = verify_active_case_authority(conn, case_id)
        issuance_payload = verify_active_authority_document_issue(
            conn, case_id, authority=authority_payload
        )
        require_authority_document_binding(
            issuance_payload,
            authority_material_sha256=authority_material_sha256,
            generated_document_id=generated_document_id,
            generated_document_sha256=generated_document_sha256,
            generated_document_version=generated_document_version,
            document_nonce=document_nonce,
            issuance_attestation_sha256=issuance_attestation_sha256,
        )
        require_authorization_candidate_digest_unused(
            conn,
            case_id,
            authority_payload=authority_payload,
            issuance_payload=issuance_payload,
            candidate_document_sha256=document_sha256,
        )

    try:
        b2_bucket, b2_key = await run_in_threadpool(
            upload_bytes,
            case_id,
            "authorization_signature_candidate",
            data,
            ".pdf",
            "application/pdf",
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail="No se pudo custodiar el PDF firmado",
        ) from e

    try:
        with engine.begin() as conn:
            _lock_case_for_material_mutation(conn, case_id)
            authority_payload = verify_active_case_authority(conn, case_id)
            issuance_payload = verify_active_authority_document_issue(
                conn, case_id, authority=authority_payload
            )
            require_authority_document_binding(
                issuance_payload,
                authority_material_sha256=authority_material_sha256,
                generated_document_id=generated_document_id,
                generated_document_sha256=generated_document_sha256,
                generated_document_version=generated_document_version,
                document_nonce=document_nonce,
                issuance_attestation_sha256=issuance_attestation_sha256,
            )
            require_authorization_candidate_digest_unused(
                conn,
                case_id,
                authority_payload=authority_payload,
                issuance_payload=issuance_payload,
                candidate_document_sha256=document_sha256,
            )
            document_row = conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, mime, size_bytes, sha256, created_at
                    )
                    VALUES (
                        :id, 'authorization_signed_candidate', :b, :k,
                        'application/pdf', :s, :sha256, NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "s": len(data),
                    "sha256": document_sha256,
                },
            ).fetchone()
            if not document_row:
                raise HTTPException(status_code=409, detail="No se registró el candidato")
            uploaded_at = datetime.now(timezone.utc).isoformat()
            candidate_attestation = build_authorization_signature_candidate_attestation(
                case_id=case_id,
                authority_payload=authority_payload,
                issuance_payload=issuance_payload,
                document_id=str(document_row[0]),
                document_sha256=document_sha256,
                size_bytes=len(data),
                uploaded_at=uploaded_at,
            )
            _event_on_conn(
                conn,
                case_id,
                "authorization_signature_candidate_uploaded",
                candidate_attestation,
            )
    except HTTPException:
        await run_in_threadpool(_cleanup_b2_objects, [(b2_bucket, b2_key)])
        raise
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, [(b2_bucket, b2_key)])
        raise HTTPException(
            status_code=503,
            detail="No se pudo registrar el PDF firmado",
        ) from exc

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "signed_authority_verified": False,
        "authorization_evidence": {
            "status": "pending_review",
            "candidate_document": _document_projection(
                str(document_row[0]), document_sha256, "application/pdf", len(data)
            ),
            "candidate_attestation_sha256": candidate_attestation[
                "material_sha256"
            ],
        },
    }


@router.post("/{case_id}/upload-authorization-signed")
async def upload_authorization_signed_legacy(
    case_id: str,
    file: UploadFile = File(...),
    authority_material_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    generated_document_id: str = Form(..., min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
    generated_document_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    generated_document_version: Literal["v1_dgt_homologado"] = Form(...),
    document_nonce: str = Form(..., min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
    issuance_attestation_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    return await _store_authorization_signed(
        case_id,
        file,
        x_case_token,
        authority_material_sha256=authority_material_sha256,
        generated_document_id=generated_document_id,
        generated_document_sha256=generated_document_sha256,
        generated_document_version=generated_document_version,
        document_nonce=document_nonce,
        issuance_attestation_sha256=issuance_attestation_sha256,
    )


@router.post("/{case_id}/authorization-signed")
async def upload_authorization_signed(
    case_id: str,
    file: UploadFile = File(...),
    authority_material_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    generated_document_id: str = Form(..., min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
    generated_document_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    generated_document_version: Literal["v1_dgt_homologado"] = Form(...),
    document_nonce: str = Form(..., min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
    issuance_attestation_sha256: str = Form(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    return await _store_authorization_signed(
        case_id,
        file,
        x_case_token,
        authority_material_sha256=authority_material_sha256,
        generated_document_id=generated_document_id,
        generated_document_sha256=generated_document_sha256,
        generated_document_version=generated_document_version,
        document_nonce=document_nonce,
        issuance_attestation_sha256=issuance_attestation_sha256,
    )


@router.post("/{case_id}/upload-receipt")
async def upload_receipt(
    case_id: str,
    file: UploadFile = File(...),
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    case_id = require_operator_case_access(case_id, x_operator_token)
    engine = get_engine()

    try:
        data = await read_upload_limited(file, max_bytes=MAX_PUBLIC_PDF_BYTES)
    except UploadSecurityError as exc:
        raise _upload_http_error(exc) from exc
    receipt_sha256 = await run_in_threadpool(
        _validate_public_pdf,
        data,
        file.content_type or "",
        file.filename,
    )

    with engine.begin() as conn:
        _require_receipt_upload_state(conn, case_id)

    try:
        b2_bucket, b2_key = await run_in_threadpool(
            upload_bytes,
            case_id,
            "receipt",
            data,
            ".pdf",
            "application/pdf",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="No se pudo custodiar el justificante",
        ) from exc

    try:
        with engine.begin() as conn:
            state_evidence = _require_receipt_upload_state(conn, case_id)

            conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, mime, size_bytes, sha256, created_at
                    )
                    VALUES (:id, 'submission_receipt', :b, :k, :m, :s, :sha256, NOW())
                    """
                ),
                {
                    "id": case_id,
                    "b": b2_bucket,
                    "k": b2_key,
                    "m": "application/pdf",
                    "s": len(data),
                    "sha256": receipt_sha256,
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
    except HTTPException:
        await run_in_threadpool(_cleanup_b2_objects, [(b2_bucket, b2_key)])
        raise
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, [(b2_bucket, b2_key)])
        raise HTTPException(
            status_code=503,
            detail="No se pudo registrar el justificante",
        ) from exc

    return {
        "ok": True,
        "case_id": case_id,
        "status": "submitted",
        "receipt_sha256": receipt_sha256,
    }
