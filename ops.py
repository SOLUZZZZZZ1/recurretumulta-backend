# ops.py — Panel Operador (PIN + cola + docs + logs + presentado + justificante + descarga segura)
import hashlib
import hmac
import json
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form, Query, Request
from sqlalchemy import text

from database import get_engine
from case_authority import verify_signed_case_authority
from rtm_core.ops_case_scope import (
    load_ops_case_scope,
    ops_case_scope_filter,
    require_case_in_scope,
    require_current_case_scope,
)
from rtm_staging_guards import require_isolated_synthetic_staging

router = APIRouter(prefix="/ops", tags=["ops"])

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
PUBLIC_SERVICE_FAMILY_MARKERS = {
    "trafico": "trafico",
    "viajes": "viajes",
    "deudas y asnef": "morosidad",
    "administracion": "administracion",
    "bancos": "bancos",
    "energia": "energia",
    "telecomunicaciones": "telecomunicaciones",
    "seguros": "seguros",
    "vivienda": "vivienda",
}
TRAVEL_CASE_TYPES = {
    "airline",
    "flight_cancelled",
    "flight_delayed",
    "baggage",
    "overbooking",
    "cruise",
    "travel_agency",
}
SUBMISSION_SOURCE_STATES = {
    "ready_to_submit",
    "submission_receipt_pending",
}
MANUAL_SUBMISSION_CHANNELS = {
    "ayuntamiento_manual",
    "correo_administrativo",
    "dgt",
    "presencial",
    "registro_electronico",
    "sede_electronica",
    "sir",
}
PRESENTED_EVIDENCE_SQL = """
    EXISTS (
        SELECT 1
        FROM events pe
        WHERE pe.case_id = c.id
          AND (
            (
              pe.type = 'dgt_submitted'
              AND COALESCE(pe.payload->>'receipt_sha256', '') <> ''
              AND (
                COALESCE(pe.payload->>'registro', '') <> ''
                OR COALESCE(pe.payload->>'csv', '') <> ''
              )
            )
            OR (
              pe.type = 'manual_submission_registered'
              AND COALESCE(pe.payload->>'registro', '') <> ''
            )
            OR (
              pe.type = 'ops_mark_submitted'
              AND COALESCE(pe.payload->>'registro', '') <> ''
            )
            OR (
              pe.type = 'submission_receipt_uploaded'
              AND COALESCE(pe.payload->>'receipt_sha256', '') <> ''
              AND (
                COALESCE(pe.payload->>'registro', '') <> ''
                OR COALESCE(pe.payload->>'csv', '') <> ''
              )
            )
          )
    )
"""


def _fold_public_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _public_service_family(
    *,
    department: str,
    case_type: str,
    interested: Dict[str, Any],
    customer_comment: Any,
) -> str:
    """Resuelve el área pública sin confundirla con la familia jurídica CORE."""

    explicit = str(
        interested.get("public_service_family")
        or interested.get("public_family")
        or ""
    ).strip().lower()
    if explicit in PUBLIC_SERVICE_FAMILY_CODES:
        return explicit

    folded_comment = _fold_public_text(
        customer_comment or interested.get("customer_comment")
    )
    for marker, family_code in PUBLIC_SERVICE_FAMILY_MARKERS.items():
        if f"area publica seleccionada: {marker}" in folded_comment:
            return family_code

    normalized_department = str(department or "").strip().lower()
    normalized_case_type = str(case_type or "").strip().lower()
    if normalized_department == "traffic":
        return "trafico"
    if normalized_department == "debt":
        return "morosidad"
    if normalized_department == "administration":
        return "administracion"
    if normalized_department == "claims" and normalized_case_type in TRAVEL_CASE_TYPES:
        return "viajes"
    return "other"


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _require_operator(x_operator_token: Optional[str]):
    token = (x_operator_token or "").strip()
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPERATOR_TOKEN no configurado")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized operator")


_INTERNAL_EVENT_KEYS = {
    "b2_bucket",
    "b2_key",
    "bucket",
    "key",
    "object_key",
    "original_bucket",
    "original_key",
    "source_bucket",
    "source_key",
    "source_keys",
    "storage_bucket",
    "storage_coordinates",
    "storage_locator",
    "storage_key",
    "storage_path",
    "internal_path",
    "download_endpoint",
    "download_url",
    "document_url",
    "presigned_url",
    "signed_url",
    "access_token",
    "token",
    "secret",
}

# La timeline de una sesión individual no necesita identidad civil,
# telemetría, evidencia cruda ni credenciales. Las claves se comparan sin
# separadores para cubrir por igual snake_case, kebab-case y camelCase.
_PRIVATE_INDIVIDUAL_EVENT_KEYS = {
    # Coordenadas de almacenamiento y enlaces internos.
    "b2",
    "b2bucket",
    "b2key",
    "bucket",
    "key",
    "objectkey",
    "originalbucket",
    "originalkey",
    "sourcebucket",
    "sourcekey",
    "sourcekeys",
    "storage",
    "storagebucket",
    "storagecoordinates",
    "storagelocator",
    "storagekey",
    "storagepath",
    "internalpath",
    "downloadendpoint",
    "downloadurl",
    "documenturl",
    "providerurl",
    "presignedurl",
    "signedurl",
    # Credenciales, sesiones y evidencia sin minimizar.
    "accesstoken",
    "apikey",
    "applicationkey",
    "authorization",
    "authorizationheader",
    "authorizationip",
    "bearer",
    "cookie",
    "credential",
    "credentialref",
    "credentials",
    "evidence",
    "evidencepayload",
    "headers",
    "httpauthorization",
    "password",
    "portalsession",
    "privatekey",
    "rawevidence",
    "rawheaders",
    "rawpayload",
    "rawrequest",
    "rawresponse",
    "secret",
    "sessiontoken",
    "setcookie",
    "signature",
    "signaturebytes",
    "signaturedata",
    "token",
    # IP y agente de usuario.
    "cfconnectingip",
    "clientip",
    "clientipaddress",
    "forwardedfor",
    "ip",
    "ipaddress",
    "rawip",
    "rawuseragent",
    "remoteip",
    "sourceip",
    "ua",
    "useragent",
    "useragentsummary",
    "xforwardedfor",
    # Identidad y contacto personal, innecesarios en el historial operativo.
    "address",
    "cif",
    "contactemail",
    "contactname",
    "customeremail",
    "customername",
    "dni",
    "dnie",
    "dninie",
    "documentnumber",
    "domicilio",
    "domicilionotif",
    "email",
    "firstname",
    "fullname",
    "identitydocument",
    "identitydocumentnumber",
    "lastname",
    "mobile",
    "mobilenumber",
    "movil",
    "nationalid",
    "nie",
    "nif",
    "notificationaddress",
    "passport",
    "passportnumber",
    "phone",
    "phonenumber",
    "postaladdress",
    "streetaddress",
    "taxid",
    "telephone",
    "telefono",
}
_PRIVATE_INDIVIDUAL_EVENT_SUFFIXES = (
    "accesstoken",
    "address",
    "apikey",
    "applicationkey",
    "bucket",
    "clientip",
    "contactemail",
    "contactname",
    "credential",
    "credentialref",
    "dninie",
    "documenturl",
    "domicilio",
    "domicilionotif",
    "downloadurl",
    "email",
    "evidence",
    "fullname",
    "identitydocumentnumber",
    "internalpath",
    "ipaddress",
    "mobile",
    "movil",
    "nif",
    "objectkey",
    "passportnumber",
    "password",
    "phone",
    "phonenumber",
    "portalsession",
    "presignedurl",
    "privatekey",
    "providerurl",
    "rawip",
    "secret",
    "sessiontoken",
    "signedurl",
    "storagebucket",
    "storagecoordinates",
    "storagekey",
    "storagelocator",
    "storagepath",
    "storageref",
    "telephone",
    "telefono",
    "token",
    "useragent",
)

def _sanitize_operator_payload(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if isinstance(value, list):
        return [_sanitize_operator_payload(item, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        str(key): _sanitize_operator_payload(child, depth + 1)
        for key, child in value.items()
        if str(key).strip().lower() not in _INTERNAL_EVENT_KEYS
    }


def _normalized_operator_payload_key(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(character for character in folded if character.isalnum())


def _private_individual_event_key(value: Any) -> bool:
    normalized = _normalized_operator_payload_key(value)
    return normalized in _PRIVATE_INDIVIDUAL_EVENT_KEYS or any(
        normalized.endswith(suffix)
        for suffix in _PRIVATE_INDIVIDUAL_EVENT_SUFFIXES
    )


def _private_individual_event_value(value: str) -> bool:
    normalized = str(value or "").strip().casefold()
    return (
        normalized.startswith(("b2://", "s3://", "gs://", "vault://"))
        or normalized.startswith(("bearer ", "basic "))
        or "-----begin private key-----" in normalized
        or "/workspace/" in normalized
        or "/home/" in normalized
        or "x-amz-credential=" in normalized
        or "x-amz-signature=" in normalized
        or "x-goog-credential=" in normalized
        or "x-goog-signature=" in normalized
    )


def _sanitize_individual_operator_payload(value: Any, depth: int = 0) -> Any:
    """Proyecta solo metadatos operativos seguros para sesiones individuales."""

    if depth > 8:
        return "<truncated>"
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_individual_operator_payload(item, depth + 1)
            for item in value
        ]
    if isinstance(value, str) and _private_individual_event_value(value):
        return "<redacted>"
    if not isinstance(value, dict):
        return value
    return {
        str(key): _sanitize_individual_operator_payload(child, depth + 1)
        for key, child in value.items()
        if not _private_individual_event_key(key)
    }


def _upload_bytes(
    case_id: str,
    kind_folder: str,
    content: bytes,
    ext: str,
    mime: str,
):
    # La dependencia con credenciales/red se carga solo al ejecutar una subida.
    from b2_storage import upload_bytes

    return upload_bytes(case_id, kind_folder, content, ext, mime)



@router.post("/login")
def ops_login(pin: str = Form(...)) -> Dict[str, Any]:
    expected = (os.getenv("OPERATOR_PIN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_PIN no configurado")
    if not hmac.compare_digest(pin.strip(), expected):
        raise HTTPException(status_code=401, detail="PIN incorrecto")
    return {"ok": True, "token": _env("OPERATOR_TOKEN")}


@router.get("/queue")
def queue(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    status: str = Query("all"),
    limit: int = Query(300, ge=1, le=500),
) -> Dict[str, Any]:
    """OPS CORE v1: cola común enriquecida con familia, tipo y datos humanos."""
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)

    select_sql = """
        SELECT c.id, c.status, c.payment_status, c.product_code, c.contact_email,
               c.created_at, c.updated_at, c.contact_name, c.department,
               c.case_type, c.category, c.organismo, c.expediente_ref,
               COALESCE(c.interested_data, '{}'::jsonb) AS interested_data,
               c.customer_comment, c.source_module
        FROM cases c
    """
    scope_sql, scope_params = ops_case_scope_filter(scope)

    engine = get_engine()
    with engine.begin() as conn:
        if status == "ready_to_submit":
            rows = conn.execute(text(select_sql + """
                WHERE c.status='ready_to_submit'
                  AND c.payment_status='paid'
                  AND c.authorized=TRUE
                  AND """ + scope_sql + """
                ORDER BY c.created_at ASC LIMIT :limit
            """), {**scope_params, "limit": limit}).fetchall()
        elif status == "all":
            rows = conn.execute(text(select_sql + """
                WHERE COALESCE(c.status,'') <> 'archived_test'
                  AND """ + scope_sql + """
                ORDER BY c.updated_at DESC LIMIT :limit
            """), {**scope_params, "limit": limit}).fetchall()
        else:
            rows = conn.execute(text(select_sql + """
                WHERE c.status=:status
                  AND """ + scope_sql + """
                ORDER BY c.updated_at DESC LIMIT :limit
            """), {**scope_params, "status": status, "limit": limit}).fetchall()

    items = []
    for r in rows:
        interested = r[13] if isinstance(r[13], dict) else {}
        department = (r[8] or interested.get("department") or "").strip().lower()
        case_type = (r[9] or interested.get("case_type") or "").strip().lower()
        category = (r[10] or "").strip().lower()

        # Compatibilidad con expedientes anteriores al RTM CORE.
        if not department:
            if category == "vehicle_removal" or str(r[1] or "").startswith("vehicle_removal"):
                department = "traffic"
                case_type = case_type or "vehicle_removal"
            elif category in ("traffic", "debt", "administration", "claims", "other"):
                department = category
            else:
                department = "other"

        if department == "traffic" and not case_type:
            case_type = "vehicle_removal" if category == "vehicle_removal" else "fine"

        items.append({
            "case_id": str(r[0]),
            "status": r[1],
            "payment_status": r[2],
            "product_code": r[3],
            "contact_email": r[4] or interested.get("email"),
            "created_at": r[5],
            "updated_at": r[6],
            "contact_name": r[7] or interested.get("full_name") or interested.get("name"),
            "department": department,
            "case_type": case_type or "other",
            "public_service_family": _public_service_family(
                department=department,
                case_type=case_type,
                interested=interested,
                customer_comment=r[14],
            ),
            "category": r[10],
            "organismo": r[11] or interested.get("organismo"),
            "expediente_ref": r[12] or interested.get("expediente_ref"),
            "customer_comment": r[14] or interested.get("customer_comment"),
            "source_module": r[15] or interested.get("source_module"),
            "matricula": interested.get("matricula") or interested.get("plate"),
            "phone": interested.get("telefono") or interested.get("phone"),
        })

    return {"ok": True, "status": status, "count": len(items), "items": items}


@router.get("/presented-cases")
def list_presented_cases_safe(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    q: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Histórico operativo de expedientes presentados / en seguimiento.
    Ruta segura: /ops/presented-cases
    Evita conflictos con /ops/cases/{case_id}.
    """
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)
    scope_sql, scope_params = ops_case_scope_filter(scope)

    term = (q or "").strip()

    engine = get_engine()
    with engine.begin() as conn:
        if term:
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.id, c.expediente_ref, c.status, c.payment_status,
                           c.contact_email, c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.status = 'submitted'
                        OR c.status ILIKE 'presentado%%'
                        OR c.status ILIKE '%%presentado%%'
                    )
                    AND {PRESENTED_EVIDENCE_SQL}
                    AND {scope_sql}
                    AND (
                        CAST(c.id AS TEXT) ILIKE :term
                        OR COALESCE(c.expediente_ref, '') ILIKE :term
                        OR COALESCE(c.contact_email, '') ILIKE :term
                        OR COALESCE(c.status, '') ILIKE :term
                    )
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**scope_params, "term": f"%{term}%", "limit": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.id, c.expediente_ref, c.status, c.payment_status,
                           c.contact_email, c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.status = 'submitted'
                        OR c.status ILIKE 'presentado%%'
                        OR c.status ILIKE '%%presentado%%'
                    )
                    AND {PRESENTED_EVIDENCE_SQL}
                    AND {scope_sql}
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**scope_params, "limit": limit},
            ).fetchall()

    items = [
        {
            "case_id": str(r[0]),
            "expediente_ref": r[1],
            "status": r[2],
            "payment_status": r[3],
            "contact_email": r[4],
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]

    return {"ok": True, "count": len(items), "items": items}


@router.get(
    "/cases/{case_id}/documents",
    dependencies=[Depends(require_current_case_scope)],
)
def list_documents(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        rows = conn.execute(
            text(
                """
                SELECT id, kind, sha256, mime, size_bytes, created_at
                FROM documents
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "id": str(r[0]),
                "kind": r[1],
                "sha256": str(r[2] or ""),
                "mime": r[3],
                "size_bytes": int(r[4] or 0),
                "created_at": r[5],
                "custody": "rtm_internal_only",
                "operator_export_allowed": False,
            }
        )

    return {"ok": True, "case_id": case_id, "documents": items}


@router.get("/documents/{doc_id}/download")
def download_document(
    doc_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)
    del doc_id
    raise HTTPException(
        status_code=403,
        detail=(
            "La descarga directa está desactivada para operadores. "
            "Utiliza RTM Presenter; la exportación excepcional requiere "
            "sesión individual y capacidad administrativa específica."
        ),
    )


@router.get(
    "/cases/{case_id}/events",
    dependencies=[Depends(require_current_case_scope)],
)
def list_events(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        rows = conn.execute(
            text(
                """
                SELECT type, payload, created_at
                FROM events
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"case_id": case_id, "limit": limit},
        ).fetchall()

    sanitize_payload = (
        _sanitize_individual_operator_payload
        if getattr(scope, "individual_session", False)
        else _sanitize_operator_payload
    )
    items = [
        {
            "type": r[0],
            "payload": sanitize_payload(r[1]),
            "created_at": r[2],
        }
        for r in rows
    ]
    return {"ok": True, "case_id": case_id, "events": items}


def _payload_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _require_lab_key(x_lab_key: Optional[str]) -> None:
    expected = (os.getenv("LAB_FORCE_KEY") or "").strip()
    candidate = (x_lab_key or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="LAB_FORCE_KEY no configurado")
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="Unauthorized lab key")


def _require_paid_and_authorized(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            "SELECT payment_status, authorized, COALESCE(test_mode,FALSE) "
            "FROM cases WHERE id=:id FOR UPDATE"
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    if (row[0] or "") != "paid":
        raise HTTPException(status_code=402, detail="Pago requerido")
    if not bool(row[1]):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")
    if bool(row[2]):
        require_isolated_synthetic_staging()
        synthetic_authority = conn.execute(
            text(
                """
                SELECT payload FROM events
                WHERE case_id=:id
                  AND type='ops_lab_force_authorize'
                  AND COALESCE(payload->>'synthetic', '')='true'
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not synthetic_authority:
            raise HTTPException(status_code=409, detail="Autoridad sintética no verificable")
        return {
            "synthetic": True,
            "event": _payload_dict(synthetic_authority[0]),
        }
    return verify_signed_case_authority(conn, case_id)


def _case_exists(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT id FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    return str(row[0])


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


def _clean_kind(kind: str) -> str:
    allowed = {
        "justificante_presentacion",
        "instancia_firmada",
        "csv_registro",
        "resolucion",
        "requerimiento",
        "contestacion_ayuntamiento",
        "prueba_externa",
        "documento_externo",
        "recurso_presentado",
        "multa_presentada",
        "autorizacion_presentada",
    }
    k = (kind or "documento_externo").strip().lower().replace(" ", "_")
    return k if k in allowed else "documento_externo"


def _guess_ext_from_filename(filename: str, content_type: str = "") -> str:
    _, ext = os.path.splitext((filename or "").lower())
    if ext and 2 <= len(ext) <= 10:
        return ext
    ct = (content_type or "").lower().strip()
    if ct == "application/pdf":
        return ".pdf"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/png":
        return ".png"
    if ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return ".docx"
    return ".bin"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated_submitted_at(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return _now_iso()
    for candidate in (raw, raw.replace(" ", "T"), raw.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise HTTPException(status_code=400, detail="submitted_at no puede estar en el futuro")
        return parsed.isoformat()
    raise HTTPException(status_code=400, detail="submitted_at no tiene formato ISO válido")


@router.post(
    "/cases/{case_id}/mark-submitted",
    dependencies=[Depends(require_current_case_scope)],
)
def mark_submitted(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    channel: str = Form("DGT"),
    registro: Optional[str] = Form(default=None),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)
    registro_clean = (registro or "").strip()
    channel_clean = (channel or "").strip().lower().replace(" ", "_")
    if len(registro_clean) < 3:
        raise HTTPException(status_code=400, detail="Número de registro verificable requerido")
    if channel_clean not in MANUAL_SUBMISSION_CHANNELS:
        raise HTTPException(status_code=400, detail="Canal de presentación no reconocido")

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        authority = _require_paid_and_authorized(conn, case_id)

        row = conn.execute(
            text("SELECT status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        current_status = row[0] if row else ""
        if current_status == "submitted":
            existing = conn.execute(
                text(
                    """
                    SELECT 1 FROM events
                    WHERE case_id=:id
                      AND type='ops_mark_submitted'
                      AND payload->>'registro'=:registro
                      AND payload->>'channel'=:channel
                    LIMIT 1
                    """
                ),
                {
                    "id": case_id,
                    "registro": registro_clean,
                    "channel": channel_clean,
                },
            ).fetchone()
            if existing:
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "status": "submitted",
                    "registro": registro_clean,
                }
            raise HTTPException(status_code=409, detail="El expediente ya consta como presentado")
        if current_status not in SUBMISSION_SOURCE_STATES:
            raise HTTPException(
                status_code=409,
                detail=f"No se puede registrar presentación desde status={current_status}",
            )

        updated = conn.execute(
            text(
                "UPDATE cases SET status='submitted', updated_at=NOW() "
                "WHERE id=:id AND status IN ('ready_to_submit','submission_receipt_pending') "
                "RETURNING id"
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Transición de presentación en conflicto")

        _append_event(
            conn,
            case_id,
            "ops_mark_submitted",
            {
                "from": current_status,
                "to": "submitted",
                "evidence_kind": "operator_registration_attestation",
                "channel": channel_clean,
                "registro": registro_clean,
                "submitted_at": _now_iso(),
                "authority_material_sha256": authority.get("material_sha256"),
                "synthetic": bool(authority.get("synthetic")),
                "note": note or "",
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": "submitted",
        "registro": registro_clean,
        "channel": channel_clean,
    }


@router.post(
    "/cases/{case_id}/upload-justificante",
    dependencies=[Depends(require_current_case_scope)],
)
async def upload_justificante(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    file: UploadFile = File(...),
    kind: str = Form("justificante_presentacion"),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename requerido")

    content_type = (file.content_type or "application/octet-stream").strip()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    document_sha256 = hashlib.sha256(data).hexdigest()

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _require_paid_and_authorized(conn, case_id)

        _, ext = os.path.splitext(filename.lower())
        ext = ext or ".bin"

        b2_bucket, b2_key = _upload_bytes(case_id, "justificantes", data, ext, content_type)

        document_row = conn.execute(
            text(
                """
                INSERT INTO documents(
                    case_id, kind, b2_bucket, b2_key, sha256,
                    mime, size_bytes, created_at
                ) VALUES (
                    :case_id, :kind, :b2_bucket, :b2_key, :sha256,
                    :mime, :size_bytes, NOW()
                )
                RETURNING id
                """
            ),
            {
                "case_id": case_id,
                "kind": kind,
                "b2_bucket": b2_bucket,
                "b2_key": b2_key,
                "sha256": document_sha256,
                "mime": content_type,
                "size_bytes": len(data),
            },
        ).fetchone()
        document_id = str(document_row[0])

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'justificante_uploaded', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "document_id": document_id,
                        "kind": kind,
                        "filename": filename,
                        "sha256": document_sha256,
                        "mime": content_type,
                        "size_bytes": len(data),
                    }
                ),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "document_id": document_id,
        "kind": kind,
        "sha256": document_sha256,
        "mime": content_type,
        "size_bytes": len(data),
        "custody": "rtm_internal_only",
    }

@router.post(
    "/cases/{case_id}/upload-external-document",
    dependencies=[Depends(require_current_case_scope)],
)
async def upload_external_document(
    case_id: str,
) -> None:
    """Cierra el ingreso OPS compartido; no procesa el cuerpo multipart."""

    raise HTTPException(
        status_code=410,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        detail={
            "code": "presenter.external_ingest_required",
            "message": "Usa la sesión individual y el ingreso versionado de Presenter",
            "replacement": (
                f"/ops/presenter/cases/{case_id}/documents/external"
            ),
        },
    )


@router.post(
    "/cases/{case_id}/register-manual-submission",
    dependencies=[Depends(require_current_case_scope)],
)
async def register_manual_submission(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    organismo: str = Form(...),
    registro: str = Form(...),
    csv: Optional[str] = Form(default=None),
    submitted_at: Optional[str] = Form(default=None),
    channel: str = Form("ayuntamiento_manual"),
    note: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
) -> Dict[str, Any]:
    """
    Registra una presentación hecha fuera de OPS, por ejemplo en la sede electrónica
    de un ayuntamiento.

    Diferencia clave:
    - NO llama a submitter.submit()
    - NO requiere automatización DGT/SIR
    - Guarda justificante si se adjunta
    - Marca el expediente como presentado_manual_ayuntamiento
    """
    _require_operator(x_operator_token)

    organismo_clean = (organismo or "").strip()
    registro_clean = (registro or "").strip()
    csv_clean = (csv or "").strip()
    channel_clean = (channel or "ayuntamiento_manual").strip().lower().replace(" ", "_")
    submitted_at_clean = _validated_submitted_at(submitted_at or "")

    if not organismo_clean:
        raise HTTPException(status_code=400, detail="Organismo requerido")
    if len(registro_clean) < 3:
        raise HTTPException(status_code=400, detail="Número de registro requerido")
    if channel_clean not in MANUAL_SUBMISSION_CHANNELS:
        raise HTTPException(status_code=400, detail="Canal de presentación no reconocido")

    document_info: Optional[Dict[str, Any]] = None

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        authority = _require_paid_and_authorized(conn, case_id)

        row = conn.execute(
            text("SELECT status FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
        previous_status = row[0] if row else ""
        if previous_status == "presentado_manual_ayuntamiento":
            existing = conn.execute(
                text(
                    """
                    SELECT 1 FROM events
                    WHERE case_id=:id
                      AND type='manual_submission_registered'
                      AND payload->>'registro'=:registro
                      AND payload->>'channel'=:channel
                    LIMIT 1
                    """
                ),
                {
                    "id": case_id,
                    "registro": registro_clean,
                    "channel": channel_clean,
                },
            ).fetchone()
            if existing:
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "status": previous_status,
                    "registro": registro_clean,
                }
            raise HTTPException(status_code=409, detail="El expediente ya consta como presentado")
        if previous_status not in SUBMISSION_SOURCE_STATES:
            raise HTTPException(
                status_code=409,
                detail=f"No se puede registrar presentación desde status={previous_status}",
            )

        if file is not None and (file.filename or "").strip():
            filename = (file.filename or "justificante_presentacion").strip()
            content_type = (file.content_type or "application/octet-stream").strip()
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="Justificante vacío")

            ext = _guess_ext_from_filename(filename, content_type)
            document_sha256 = hashlib.sha256(data).hexdigest()
            b2_bucket, b2_key = _upload_bytes(
                case_id, "manual_submission", data, ext, content_type
            )

            document_row = conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, sha256,
                        mime, size_bytes, created_at
                    ) VALUES (
                        :case_id, 'justificante_presentacion',
                        :b2_bucket, :b2_key, :sha256,
                        :mime, :size_bytes, NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "sha256": document_sha256,
                    "mime": content_type,
                    "size_bytes": len(data),
                },
            ).fetchone()
            document_id = str(document_row[0])

            document_info = {
                "document_id": document_id,
                "filename": filename,
                "mime": content_type,
                "size_bytes": len(data),
                "sha256": document_sha256,
            }

        new_status = "presentado_manual_ayuntamiento"
        updated = conn.execute(
            text(
                "UPDATE cases SET status=:status, updated_at=NOW() "
                "WHERE id=:id AND status IN ('ready_to_submit','submission_receipt_pending') "
                "RETURNING id"
            ),
            {"id": case_id, "status": new_status},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Transición de presentación en conflicto")

        _append_event(
            conn,
            case_id,
            "manual_submission_registered",
            {
                "from": previous_status,
                "to": new_status,
                "organismo": organismo_clean,
                "registro": registro_clean,
                "csv": csv_clean,
                "submitted_at": submitted_at_clean,
                "channel": channel_clean,
                "evidence_kind": "manual_registration",
                "authority_material_sha256": authority.get("material_sha256"),
                "synthetic": bool(authority.get("synthetic")),
                "note": note or "",
                "document": document_info,
                "at": _now_iso(),
            },
        )

        _ensure_standard_followups_after_manual_submission(
            conn,
            case_id,
            organismo_clean,
            submitted_at_clean,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": new_status,
        "organismo": organismo_clean,
        "registro": registro_clean,
        "csv": csv_clean,
        "submitted_at": submitted_at_clean,
        "channel": channel_clean,
        "document": document_info,
    }



# =========================================================
# Seguimiento de plazos / follow-ups OPS
# =========================================================

def _parse_submitted_at(value: str = ""):
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)

    # Acepta ISO, "YYYY-MM-DD HH:MM" o "YYYY-MM-DD"
    candidates = [
        raw,
        raw.replace(" ", "T"),
        raw.replace("/", "-"),
    ]

    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    return datetime.now(timezone.utc)


def _create_followup(
    conn,
    case_id: str,
    *,
    kind: str,
    title: str,
    description: str = "",
    due_at,
    source_event_type: str = "",
    created_by: str = "ops",
):
    conn.execute(
        text(
            """
            INSERT INTO ops_followups(
              case_id, kind, status, title, description, due_at,
              source_event_type, created_by, created_at, updated_at
            )
            VALUES (
              :case_id, :kind, 'pending', :title, :description, :due_at,
              :source_event_type, :created_by, NOW(), NOW()
            )
            """
        ),
        {
            "case_id": case_id,
            "kind": kind,
            "title": title,
            "description": description,
            "due_at": due_at,
            "source_event_type": source_event_type,
            "created_by": created_by,
        },
    )


def _ensure_standard_followups_after_manual_submission(conn, case_id: str, organismo: str, submitted_at_raw: str):
    """
    Crea alertas conservadoras tras una presentación manual.
    No son afirmaciones jurídicas automáticas; son hitos operativos para que OPS revise.
    """
    from datetime import timedelta

    submitted_dt = _parse_submitted_at(submitted_at_raw)

    checks = [
        (
            "revision_30_dias",
            "Revisar estado del expediente",
            "Han pasado aproximadamente 30 días desde la presentación. Comprobar si hay resolución, requerimiento o nueva notificación.",
            submitted_dt + timedelta(days=30),
        ),
        (
            "alerta_60_dias",
            "Alerta: expediente sin resolución registrada",
            "Si no consta respuesta, revisar sede electrónica, buzón/notificaciones y estado administrativo.",
            submitted_dt + timedelta(days=60),
        ),
        (
            "revision_90_dias",
            "Revisión avanzada: posible silencio / siguiente acción",
            "Si no consta respuesta, revisar jurídicamente silencio administrativo, ejecutiva o siguiente escrito. No automatizar sin revisión humana.",
            submitted_dt + timedelta(days=90),
        ),
    ]

    for kind, title, description, due_at in checks:
        _create_followup(
            conn,
            case_id,
            kind=kind,
            title=title,
            description=f"{description} Organismo: {organismo or 'no indicado'}.",
            due_at=due_at,
            source_event_type="manual_submission_registered",
            created_by="ops",
        )


@router.get(
    "/cases/{case_id}/followups",
    dependencies=[Depends(require_current_case_scope)],
)
def list_case_followups(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_exists(conn, case_id)

        rows = conn.execute(
            text(
                """
                SELECT id, kind, status, title, description, due_at,
                       resolved_at, resolution_note, created_at, updated_at
                FROM ops_followups
                WHERE case_id = :case_id
                ORDER BY
                  CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                  due_at ASC,
                  created_at DESC
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    items = []
    now = datetime.now(timezone.utc)

    for r in rows:
        due_at = r[5]
        overdue = False
        days_left = None
        if due_at:
            try:
                dta = due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
                delta = dta - now
                days_left = int(delta.total_seconds() // 86400)
                overdue = delta.total_seconds() < 0 and (r[2] or "") == "pending"
            except Exception:
                pass

        items.append(
            {
                "id": str(r[0]),
                "kind": r[1],
                "status": r[2],
                "title": r[3],
                "description": r[4],
                "due_at": r[5],
                "resolved_at": r[6],
                "resolution_note": r[7],
                "created_at": r[8],
                "updated_at": r[9],
                "overdue": overdue,
                "days_left": days_left,
            }
        )

    return {"ok": True, "case_id": case_id, "followups": items}


@router.get("/followups")
def list_all_followups(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    status: str = Query("all"),
    limit: int = Query(500, ge=1, le=500),
) -> Dict[str, Any]:
    """Bandeja global de seguimientos para OPS, protegida por token de operador."""
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)

    normalized_status = (status or "all").strip().lower()
    if normalized_status not in {"all", "pending", "resolved"}:
        raise HTTPException(
            status_code=400,
            detail="Estado de seguimiento no válido. Usa all, pending o resolved.",
        )

    where_status = ""
    scope_sql, scope_params = ops_case_scope_filter(scope)
    params: Dict[str, Any] = {**scope_params, "limit": limit}
    if normalized_status != "all":
        where_status = "AND f.status = :followup_status"
        params["followup_status"] = normalized_status

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT f.id, f.case_id, f.kind, f.status, f.title, f.description,
                       f.due_at, f.resolved_at, f.resolution_note,
                       f.created_at, f.updated_at,
                       c.status AS case_status, c.payment_status,
                       c.contact_email, c.contact_name,
                       c.department, c.case_type, c.category,
                       c.organismo, c.expediente_ref,
                       COALESCE(c.interested_data, '{{}}'::jsonb) AS interested_data,
                       c.customer_comment
                FROM ops_followups f
                JOIN cases c ON c.id = f.case_id
                WHERE COALESCE(c.status, '') <> 'archived_test'
                  AND {scope_sql}
                  {where_status}
                ORDER BY
                  CASE WHEN f.status = 'pending' THEN 0 ELSE 1 END,
                  f.due_at ASC NULLS LAST,
                  f.updated_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).fetchall()

    now = datetime.now(timezone.utc)
    items = []
    for r in rows:
        interested = r[20] if isinstance(r[20], dict) else {}
        department = (r[15] or interested.get("department") or "").strip().lower()
        case_type = (r[16] or interested.get("case_type") or "").strip().lower()
        category = (r[17] or "").strip().lower()

        if not department:
            if category == "vehicle_removal" or str(r[11] or "").startswith("vehicle_removal"):
                department = "traffic"
                case_type = case_type or "vehicle_removal"
            elif category in ("traffic", "debt", "administration", "claims", "other"):
                department = category
            else:
                department = "other"

        if department == "traffic" and not case_type:
            case_type = "vehicle_removal" if category == "vehicle_removal" else "fine"

        due_at = r[6]
        overdue = False
        days_left = None
        if due_at:
            try:
                normalized_due = due_at if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
                delta = normalized_due - now
                days_left = int(delta.total_seconds() // 86400)
                overdue = delta.total_seconds() < 0 and (r[3] or "") == "pending"
            except Exception:
                pass

        items.append(
            {
                "id": str(r[0]),
                "case_id": str(r[1]),
                "kind": r[2],
                "status": r[3],
                "title": r[4],
                "description": r[5],
                "due_at": due_at,
                "resolved_at": r[7],
                "resolution_note": r[8],
                "created_at": r[9],
                "updated_at": r[10],
                "overdue": overdue,
                "days_left": days_left,
                "case_status": r[11],
                "payment_status": r[12],
                "contact_email": r[13] or interested.get("email"),
                "contact_name": r[14] or interested.get("full_name") or interested.get("name"),
                "department": department,
                "case_type": case_type or "other",
                "public_service_family": _public_service_family(
                    department=department,
                    case_type=case_type,
                    interested=interested,
                    customer_comment=r[21],
                ),
                "category": r[17],
                "organismo": r[18] or interested.get("organismo"),
                "expediente_ref": r[19] or interested.get("expediente_ref"),
                "matricula": interested.get("matricula") or interested.get("plate"),
                "customer_comment": r[21] or interested.get("customer_comment"),
            }
        )

    return {
        "ok": True,
        "status": normalized_status,
        "count": len(items),
        "items": items,
    }


@router.get("/followups/due")
def list_due_followups(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    days: int = Query(7, ge=0, le=365),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Alertas pendientes vencidas o próximas.
    Útil para dashboard OPS.
    """
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)
    scope_sql, scope_params = ops_case_scope_filter(scope)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT f.id, f.case_id, f.kind, f.status, f.title, f.description,
                       f.due_at, c.status AS case_status, c.contact_email
                FROM ops_followups f
                JOIN cases c ON c.id = f.case_id
                WHERE f.status = 'pending'
                  AND f.due_at <= NOW() + (:days || ' days')::interval
                  AND """ + scope_sql + """
                ORDER BY f.due_at ASC
                LIMIT :limit
                """
            ),
            {
                **scope_params,
                "days": days,
                "limit": limit,
            },
        ).fetchall()

    return {
        "ok": True,
        "days": days,
        "items": [
            {
                "id": str(r[0]),
                "case_id": str(r[1]),
                "kind": r[2],
                "status": r[3],
                "title": r[4],
                "description": r[5],
                "due_at": r[6],
                "case_status": r[7],
                "contact_email": r[8],
            }
            for r in rows
        ],
    }


@router.post(
    "/cases/{case_id}/followups",
    dependencies=[Depends(require_current_case_scope)],
)
def create_case_followup(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    kind: str = Form("seguimiento"),
    title: str = Form(...),
    description: Optional[str] = Form(default=None),
    due_at: str = Form(...),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    title_clean = (title or "").strip()
    if not title_clean:
        raise HTTPException(status_code=400, detail="Título requerido")

    due_dt = _parse_submitted_at(due_at)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_exists(conn, case_id)

        _create_followup(
            conn,
            case_id,
            kind=(kind or "seguimiento").strip(),
            title=title_clean,
            description=(description or "").strip(),
            due_at=due_dt,
            source_event_type="manual_followup",
            created_by="ops",
        )

        _append_event(
            conn,
            case_id,
            "followup_created",
            {
                "kind": kind,
                "title": title_clean,
                "description": description or "",
                "due_at": due_dt.isoformat(),
                "at": _now_iso(),
            },
        )

    return {"ok": True, "case_id": case_id}


@router.post(
    "/cases/{case_id}/followups/{followup_id}/resolve",
    dependencies=[Depends(require_current_case_scope)],
)
def resolve_case_followup(
    case_id: str,
    followup_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_exists(conn, case_id)

        res = conn.execute(
            text(
                """
                UPDATE ops_followups
                SET status='resolved',
                    resolved_at=NOW(),
                    resolved_by='ops',
                    resolution_note=:note,
                    updated_at=NOW()
                WHERE id=:id AND case_id=:case_id
                RETURNING id
                """
            ),
            {"id": followup_id, "case_id": case_id, "note": note or ""},
        ).fetchone()

        if not res:
            raise HTTPException(status_code=404, detail="Follow-up no encontrado")

        _append_event(
            conn,
            case_id,
            "followup_resolved",
            {
                "followup_id": followup_id,
                "note": note or "",
                "at": _now_iso(),
            },
        )

    return {"ok": True, "case_id": case_id, "followup_id": followup_id, "status": "resolved"}


@router.post(
    "/cases/{case_id}/restore-real-case",
    dependencies=[Depends(require_current_case_scope)],
)
def restore_real_case(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Restaura un expediente real marcado accidentalmente como archived_test.

    NO borra nada.
    NO toca documentos.
    NO toca eventos anteriores.

    Solo restaura archived_test al estado presentado acreditado por un evento
    previo con registro, CSV o hash de justificante.
    """

    _require_operator(x_operator_token)

    engine = get_engine()

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        row = conn.execute(
            text(
                """
                SELECT status, expediente_ref
                FROM cases
                WHERE id = :id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        previous_status = (row[0] or "").strip()
        expediente_ref = row[1]
        if previous_status != "archived_test":
            raise HTTPException(
                status_code=409,
                detail="Solo se puede restaurar un expediente archived_test",
            )

        evidence_row = conn.execute(
            text(
                """
                SELECT type, payload FROM events
                WHERE case_id=:id
                  AND (
                    (
                      type IN ('dgt_submitted','manual_submission_registered','ops_mark_submitted')
                      AND (
                        COALESCE(payload->>'registro', '') <> ''
                        OR COALESCE(payload->>'csv', '') <> ''
                      )
                      AND (
                        type <> 'dgt_submitted'
                        OR COALESCE(payload->>'receipt_sha256', '') <> ''
                      )
                    )
                    OR (
                      type='submission_receipt_uploaded'
                      AND COALESCE(payload->>'receipt_sha256', '') <> ''
                      AND (
                        COALESCE(payload->>'registro', '') <> ''
                        OR COALESCE(payload->>'csv', '') <> ''
                      )
                    )
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not evidence_row:
            raise HTTPException(
                status_code=409,
                detail="No existe evidencia de presentación para restaurar el expediente",
            )
        evidence_type = str(evidence_row[0])
        evidence_payload = _payload_dict(evidence_row[1])
        restored_status = (
            "presentado_manual_ayuntamiento"
            if evidence_type == "manual_submission_registered"
            else "submitted"
        )

        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET status = :restored_status,
                    updated_at = NOW()
                WHERE id = :id AND status='archived_test'
                RETURNING id
                """
            ),
            {"id": case_id, "restored_status": restored_status},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Restauración concurrente en conflicto")

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (
                  :case_id,
                  'ops_restore_real_case',
                  CAST(:payload AS JSONB),
                  NOW()
                )
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "from": previous_status,
                        "to": restored_status,
                        "expediente_ref": expediente_ref,
                        "evidence_event_type": evidence_type,
                        "registro": evidence_payload.get("registro"),
                        "receipt_sha256": evidence_payload.get("receipt_sha256"),
                        "note": note or "Restauración expediente real",
                    },
                    ensure_ascii=False,
                ),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": restored_status,
        "message": "Expediente real restaurado correctamente.",
    }




@router.get("/cases/presented")
def list_presented_cases(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    q: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Historico operativo de expedientes presentados / en seguimiento.
    Query robusta sin ANY(:lista), para evitar problemas de binding.
    """
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)
    scope_sql, scope_params = ops_case_scope_filter(scope)

    term = (q or "").strip()

    engine = get_engine()
    with engine.begin() as conn:
        if term:
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.id, c.expediente_ref, c.status, c.payment_status,
                           c.contact_email, c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.status = 'submitted'
                        OR c.status ILIKE 'presentado%%'
                        OR c.status ILIKE '%%presentado%%'
                    )
                    AND {PRESENTED_EVIDENCE_SQL}
                    AND {scope_sql}
                    AND (
                        CAST(c.id AS TEXT) ILIKE :term
                        OR COALESCE(c.expediente_ref, '') ILIKE :term
                        OR COALESCE(c.contact_email, '') ILIKE :term
                        OR COALESCE(c.status, '') ILIKE :term
                    )
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**scope_params, "term": f"%{term}%", "limit": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.id, c.expediente_ref, c.status, c.payment_status,
                           c.contact_email, c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.status = 'submitted'
                        OR c.status ILIKE 'presentado%%'
                        OR c.status ILIKE '%%presentado%%'
                    )
                    AND {PRESENTED_EVIDENCE_SQL}
                    AND {scope_sql}
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**scope_params, "limit": limit},
            ).fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "case_id": str(r[0]),
                "expediente_ref": r[1],
                "status": r[2],
                "payment_status": r[3],
                "contact_email": r[4],
                "created_at": r[5],
                "updated_at": r[6],
            }
        )

    return {"ok": True, "count": len(items), "items": items}


@router.post(
    "/cases/{case_id}/rebuild-followups",
    dependencies=[Depends(require_current_case_scope)],
)
def rebuild_followups(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """
    Regenera automáticamente los followups 30/60/90 para un expediente
    ya presentado manualmente.

    Útil cuando el expediente fue restaurado después de la limpieza
    o cuando no se crearon los seguimientos al registrar la presentación.
    """
    _require_operator(x_operator_token)

    engine = get_engine()

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        row = conn.execute(
            text(
                """
                SELECT status, expediente_ref
                FROM cases
                WHERE id = :id
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()

        if current_status != "presentado_manual_ayuntamiento":
            raise HTTPException(
                status_code=409,
                detail="El expediente no está en presentado_manual_ayuntamiento",
            )

        event_row = conn.execute(
            text(
                """
                SELECT payload
                FROM events
                WHERE case_id = :case_id
                  AND type = 'manual_submission_registered'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"case_id": case_id},
        ).fetchone()

        if not event_row:
            raise HTTPException(
                status_code=404,
                detail="No existe evento manual_submission_registered",
            )

        payload = event_row[0] or {}

        organismo = payload.get("organismo") or "Organismo"
        submitted_at = payload.get("submitted_at") or ""

        conn.execute(
            text(
                """
                DELETE FROM ops_followups
                WHERE case_id = :case_id
                  AND source_event_type = 'manual_submission_registered'
                """
            ),
            {"case_id": case_id},
        )

        _ensure_standard_followups_after_manual_submission(
            conn,
            case_id,
            organismo,
            submitted_at,
        )

        _append_event(
            conn,
            case_id,
            "followups_rebuilt",
            {
                "organismo": organismo,
                "submitted_at": submitted_at,
                "at": _now_iso(),
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "message": "Followups 30/60/90 regenerados correctamente.",
    }


@router.post(
    "/cases/{case_id}/force-ready-to-submit",
    dependencies=[Depends(require_current_case_scope)],
)
def force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    Empuja un caso a ready_to_submit SOLO para laboratorio de pipeline (submissions/cola),
    sin depender de admisibilidad.
    Reglas:
    - Requiere OPERATOR_TOKEN
    - Requiere paid + authorized
    - Requiere staging aislado y test_mode
    - Deja event auditado
    """
    _require_operator(x_operator_token)
    require_isolated_synthetic_staging()

    engine = get_engine()
    with engine.begin() as conn:
        # meta mínima
        row = conn.execute(
            text(
                "SELECT status, payment_status, authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()
        payment_status = (row[1] or "").strip()
        authorized = bool(row[2])
        test_mode = bool(row[3])

        if not test_mode:
            raise HTTPException(status_code=409, detail="Se requiere un expediente test_mode")

        if payment_status != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido (paid)")

        if not authorized:
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")
        _require_paid_and_authorized(conn, case_id)

        if current_status in ("submitted", "closed", "archived"):
            raise HTTPException(status_code=409, detail=f"No se puede forzar desde status={current_status}")

        # actualizar a ready_to_submit
        updated = conn.execute(
            text(
                "UPDATE cases SET status='ready_to_submit', updated_at=NOW() "
                "WHERE id=:id AND COALESCE(test_mode,FALSE)=TRUE RETURNING id"
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Mutación sintética concurrente en conflicto")

        # auditar
        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_force_ready_to_submit', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                        {
                            "from": current_status,
                            "to": "ready_to_submit",
                            "synthetic": True,
                            "data_namespace": os.getenv("RTM_DATA_NAMESPACE") or "",
                            "note": note or "",
                        }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "status": "ready_to_submit"}

@router.post(
    "/cases/{case_id}/lab-force-ready-to-submit",
    dependencies=[Depends(require_current_case_scope)],
)
def lab_force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """
    LAB (llave de oro): fuerza ready_to_submit SIN pago, solo para pruebas de pipeline.
    Reglas:
    - OPERATOR_TOKEN válido
    - X-Lab-Key == LAB_FORCE_KEY
    - authorized = TRUE
    - staging aislado y test_mode = TRUE
    """
    _require_operator(x_operator_token)
    require_isolated_synthetic_staging()
    _require_lab_key(x_lab_key)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT status, authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        current_status = (row[0] or "").strip()
        authorized = bool(row[1])
        test_mode = bool(row[2])

        if not test_mode:
            raise HTTPException(status_code=409, detail="Se requiere un expediente test_mode")
        if not authorized:
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")
        synthetic_authority = conn.execute(
            text(
                """
                SELECT 1 FROM events
                WHERE case_id=:id
                  AND type='ops_lab_force_authorize'
                  AND COALESCE(payload->>'synthetic', '')='true'
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not synthetic_authority:
            raise HTTPException(status_code=409, detail="Autoridad sintética no verificable")

        if current_status in ("submitted", "closed", "archived"):
            raise HTTPException(status_code=409, detail=f"No se puede forzar desde status={current_status}")

        updated = conn.execute(
            text(
                "UPDATE cases SET status='ready_to_submit', updated_at=NOW() "
                "WHERE id=:id AND COALESCE(test_mode,FALSE)=TRUE RETURNING id"
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Mutación sintética concurrente en conflicto")

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_ready_to_submit', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "from": current_status,
                        "to": "ready_to_submit",
                        "synthetic": True,
                        "data_namespace": os.getenv("RTM_DATA_NAMESPACE") or "",
                        "note": note or "",
                    }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "status": "ready_to_submit"}

@router.post(
    "/cases/{case_id}/lab-force-authorize",
    dependencies=[Depends(require_current_case_scope)],
)
def lab_force_authorize(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)
    require_isolated_synthetic_staging()
    _require_lab_key(x_lab_key)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        if not bool(row[1]):
            raise HTTPException(status_code=409, detail="Se requiere un expediente test_mode")

        updated = conn.execute(
            text(
                "UPDATE cases SET authorized=TRUE, authorized_at=NOW(), updated_at=NOW() "
                "WHERE id=:id AND COALESCE(test_mode,FALSE)=TRUE RETURNING id"
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Mutación sintética concurrente en conflicto")

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_authorize', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "synthetic": True,
                        "previous_authorized": bool(row[0]),
                        "data_namespace": os.getenv("RTM_DATA_NAMESPACE") or "",
                        "note": note or "",
                    }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "authorized": True}

@router.post(
    "/cases/{case_id}/lab-force-paid",
    dependencies=[Depends(require_current_case_scope)],
)
def lab_force_paid(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_lab_key: Optional[str] = Header(default=None, alias="X-Lab-Key"),
    note: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    _require_operator(x_operator_token)
    require_isolated_synthetic_staging()
    _require_lab_key(x_lab_key)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COALESCE(test_mode,FALSE), payment_status "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        if not bool(row[0]):
            raise HTTPException(status_code=409, detail="Se requiere un expediente test_mode")

        updated = conn.execute(
            text(
                "UPDATE cases SET payment_status='paid', updated_at=NOW() "
                "WHERE id=:id AND COALESCE(test_mode,FALSE)=TRUE RETURNING id"
            ),
            {"id": case_id},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Mutación sintética concurrente en conflicto")

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'ops_lab_force_paid', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "synthetic": True,
                        "previous_payment_status": row[1],
                        "data_namespace": os.getenv("RTM_DATA_NAMESPACE") or "",
                        "note": note or "",
                    }
                ),
            },
        )

    return {"ok": True, "case_id": case_id, "payment_status": "paid"}
