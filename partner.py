import base64
import os
import json
import secrets
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from sqlalchemy import text

from database import get_engine
from b2_storage import delete_object, upload_bytes
from email_utils import send_email
from rtm_core.operator_access_runtime_repository import (
    record_operator_access_event,
)
from rtm_core.operator_admin_router import (
    SupervisorContext,
    require_recent_supervisor_context,
)
from rtm_core.operator_auth_request import build_request_fingerprint
from rtm_core.runtime_capabilities import require_http_capability


router = APIRouter(prefix="/partner", tags=["partner"])

MAX_FILES = 5
MAX_PARTNER_FILE_BYTES = 8 * 1024 * 1024
MAX_PARTNER_TOTAL_BYTES = 20 * 1024 * 1024
_PARTNER_TOKEN_DIGEST_PREFIX = "sha256$"
_PARTNER_TOKEN_VERSION = "ps1"
_PARTNER_SESSION_COOKIE = "__Host-rtm_partner_session"
_PARTNER_CSRF_COOKIE = "__Host-rtm_partner_csrf"
_PARTNER_SESSION_TTL_SECONDS = 8 * 60 * 60
_PARTNER_SESSION_TTL_MIN_SECONDS = 5 * 60
_PARTNER_SESSION_TTL_MAX_SECONDS = 7 * 24 * 60 * 60
_PARTNER_TOKEN_FUTURE_SKEW_SECONDS = 60
_PARTNER_CASE_LIST_DEFAULT_LIMIT = 100
_PARTNER_CASE_LIST_MAX_LIMIT = 250
_PARTNER_CASE_CURSOR_VERSION = "pc1"
_PARTNER_CASE_CURSOR_MAX_CHARS = 256
_PARTNER_CASE_STATUSES = frozenset(
    {
        "uploaded",
        "pending_documents",
        "ready_to_pay",
        "ready_to_submit",
        "submitted",
        "closed",
    }
)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
_PARTNER_SIGNUP_FAILURE_DETAIL = (
    "No se pudo procesar la solicitud. Inténtelo de nuevo más tarde."
)
_PARTNER_CASE_INTAKE_DISABLED_DETAIL = {
    "code": "partner_authorization_flow_unavailable",
    "message": (
        "El alta de expedientes partner está temporalmente deshabilitada hasta "
        "disponer de autorización ligada al expediente y revisión humana."
    ),
}


def _cleanup_partner_uploads(coordinates: List[tuple[str, str]]) -> None:
    """Compensa objetos B2 si la unidad SQL del alta no se confirma."""

    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass
_PARTNER_EVENT_FORBIDDEN_KEYS = frozenset(
    {
        "accesstoken",
        "apitoken",
        "authorization",
        "b2bucket",
        "b2key",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "csrftoken",
        "newpassword",
        "oldpassword",
        "password",
        "passwordhash",
        "presignedurl",
        "refreshtoken",
        "secret",
        "sessioncookie",
        "sessiontoken",
        "setcookie",
        "signedurl",
        "storagebucket",
        "storagekey",
        "token",
    }
)
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)
# Se genera una sola vez al iniciar el proceso. Los usuarios ausentes o
# inactivos recorren así una verificación Argon2 equivalente a la de una cuenta
# vigente, sin introducir una contraseña conocida reutilizable.
_DUMMY_PARTNER_PASSWORD_HASH = _PASSWORD_HASHER.hash(
    secrets.token_urlsafe(32)
)
_DUMMY_PARTNER_LEGACY_SALT = secrets.token_urlsafe(24)


async def require_partner_admin_supervisor(
    context: SupervisorContext = Depends(require_recent_supervisor_context),
) -> SupervisorContext:
    """Exige supervisor nominal, dispositivo y step-up persistido reciente."""

    if str(context.session.role_code or "") != "rtm.supervisor":
        raise HTTPException(
            status_code=403,
            detail="Rol de supervisor requerido",
            headers=_NO_STORE_HEADERS,
        )
    return context


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return dk.hex()


def _modern_password_hash(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def _consume_dummy_argon_work(password: str) -> None:
    """Equalize a legacy credential check with one Argon2 verification."""

    try:
        _PASSWORD_HASHER.verify(_DUMMY_PARTNER_PASSWORD_HASH, password)
    except (InvalidHashError, VerifyMismatchError, VerificationError):
        pass


def _consume_dummy_legacy_work(password: str) -> None:
    """Equalize an Argon2 or absent-account check with legacy PBKDF2 work."""

    _hash_password(password, _DUMMY_PARTNER_LEGACY_SALT)


def _verify_password(password: str, salt: str, expected: str) -> tuple[bool, bool]:
    """Devuelve ``(valida, necesita_migracion)`` sin comparar hashes con ``==``."""

    if expected.startswith("$argon2"):
        try:
            valid = bool(_PASSWORD_HASHER.verify(expected, password))
            return valid, bool(valid and _PASSWORD_HASHER.check_needs_rehash(expected))
        except (InvalidHashError, VerifyMismatchError, VerificationError):
            return False, False
        finally:
            # Accounts that are absent, modern or legacy all pay one Argon2
            # and one PBKDF2-equivalent cost.  This prevents distinguishing
            # legacy accounts by the substantially cheaper verifier alone.
            _consume_dummy_legacy_work(password)
    legacy = _hash_password(password, salt)
    valid = hmac.compare_digest(legacy, expected)
    _consume_dummy_argon_work(password)
    return valid, bool(valid)


def _partner_session_ttl_seconds(
    environ: Mapping[str, str] | None = None,
) -> int:
    source = environ if environ is not None else os.environ
    raw = str(source.get("RTM_PARTNER_SESSION_TTL_SECONDS") or "").strip()
    if not raw:
        return _PARTNER_SESSION_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("RTM_PARTNER_SESSION_TTL_SECONDS no es un entero") from exc
    if not _PARTNER_SESSION_TTL_MIN_SECONDS <= value <= _PARTNER_SESSION_TTL_MAX_SECONDS:
        raise ValueError(
            "RTM_PARTNER_SESSION_TTL_SECONDS fuera del rango permitido"
        )
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_token(*, now: datetime | None = None) -> str:
    issued_at = int((now or _utcnow()).timestamp())
    return f"{_PARTNER_TOKEN_VERSION}.{issued_at}.{secrets.token_urlsafe(32)}"


def _partner_token_expiration(
    token: str,
    *,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> datetime:
    """Valida el formato/edad del token; su digest en BD impide reescribirlo."""

    current = now or _utcnow()
    parts = str(token or "").split(".")
    try:
        if (
            len(parts) != 3
            or parts[0] != _PARTNER_TOKEN_VERSION
            or len(parts[2]) < 32
        ):
            raise ValueError
        issued_at = int(parts[1], 10)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Token partner inválido o caducado",
            headers=_NO_STORE_HEADERS,
        ) from exc
    try:
        ttl = _partner_session_ttl_seconds(environ)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Sesión partner no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc
    now_timestamp = int(current.timestamp())
    if (
        issued_at <= 0
        or issued_at > now_timestamp + _PARTNER_TOKEN_FUTURE_SKEW_SECONDS
        or now_timestamp >= issued_at + ttl
    ):
        raise HTTPException(
            status_code=401,
            detail="Token partner inválido o caducado",
            headers=_NO_STORE_HEADERS,
        )
    return datetime.fromtimestamp(issued_at + ttl, tz=timezone.utc)


def _stored_token(token: str) -> str:
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    return f"{_PARTNER_TOKEN_DIGEST_PREFIX}{digest}"


@dataclass(frozen=True)
class PartnerCredential:
    token: str = field(repr=False)
    via_cookie: bool


def _require_partner_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Falta Authorization",
            headers=_NO_STORE_HEADERS,
        )
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authorization inválido (usar Bearer)",
            headers=_NO_STORE_HEADERS,
        )
    return parts[1].strip()


def _partner_credential(
    authorization: Optional[str],
    session_cookie: Optional[str],
) -> PartnerCredential:
    cookie_token = str(session_cookie or "").strip()
    if authorization is not None:
        bearer = _require_partner_token(authorization)
        if cookie_token and not hmac.compare_digest(bearer, cookie_token):
            raise HTTPException(
                status_code=401,
                detail="Credenciales partner incompatibles",
                headers=_NO_STORE_HEADERS,
            )
        return PartnerCredential(token=bearer, via_cookie=False)
    if not cookie_token:
        raise HTTPException(
            status_code=401,
            detail="Falta credencial partner",
            headers=_NO_STORE_HEADERS,
        )
    return PartnerCredential(token=cookie_token, via_cookie=True)


def _csrf_token_for_session(token: str) -> str:
    return hmac.new(
        str(token).encode("utf-8"),
        b"rtm-partner-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


def _require_partner_csrf(
    credential: PartnerCredential,
    *,
    csrf_header: Optional[str],
    csrf_cookie: Optional[str],
) -> None:
    if not credential.via_cookie:
        return
    header_value = str(csrf_header or "").strip().lower()
    cookie_value = str(csrf_cookie or "").strip().lower()
    expected = _csrf_token_for_session(credential.token)
    if not (
        len(header_value) == 64
        and len(cookie_value) == 64
        and hmac.compare_digest(header_value, cookie_value)
        and hmac.compare_digest(header_value, expected)
    ):
        raise HTTPException(
            status_code=403,
            detail="Protección CSRF requerida",
            headers=_NO_STORE_HEADERS,
        )


def _set_partner_session_cookies(
    response: Response,
    *,
    token: str,
    expires_at: datetime,
) -> str:
    max_age = max(0, int((expires_at - _utcnow()).total_seconds()))
    csrf_token = _csrf_token_for_session(token)
    common = {
        "secure": True,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
        "expires": expires_at,
    }
    response.set_cookie(
        _PARTNER_SESSION_COOKIE,
        token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        _PARTNER_CSRF_COOKIE,
        csrf_token,
        httponly=False,
        **common,
    )
    response.headers.update(_NO_STORE_HEADERS)
    response.headers["Vary"] = "Authorization, Cookie"
    return csrf_token


def _clear_partner_session_cookies(response: Response) -> None:
    response.delete_cookie(
        _PARTNER_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        _PARTNER_CSRF_COOKIE,
        path="/",
        secure=True,
        httponly=False,
        samesite="lax",
    )
    response.headers.update(_NO_STORE_HEADERS)
    response.headers["Vary"] = "Authorization, Cookie"


def _get_partner_by_token(conn, token: str) -> Dict[str, Any]:
    expires_at = _partner_token_expiration(token)
    digest = _stored_token(token)
    row = conn.execute(
        text(
            "SELECT id, name, email, active FROM partners "
            "WHERE api_token=:digest LIMIT 1"
        ),
        {"digest": digest},
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=401,
            detail="Token partner inválido",
            headers=_NO_STORE_HEADERS,
        )
    if not bool(row[3]):
        raise HTTPException(
            status_code=403,
            detail="Partner desactivado",
            headers=_NO_STORE_HEADERS,
        )
    return {
        "id": str(row[0]),
        "name": row[1],
        "email": row[2],
        "expires_at": expires_at,
    }


def _partner_case_cursor_material(
    *,
    partner_id: str,
    q: str,
    status: str,
    payload: str,
) -> bytes:
    return "\x00".join(
        (
            "rtm-partner-case-cursor-v1",
            str(partner_id),
            str(q).casefold(),
            str(status),
            str(payload),
        )
    ).encode("utf-8")


def _encode_partner_case_cursor(
    *,
    credential_token: str,
    partner_id: str,
    q: str,
    status: str,
    updated_at: Any,
    case_id: str,
) -> str:
    """Firma la posición de página sin exponer credenciales ni PII."""

    if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
        raise ValueError("La página partner no tiene una fecha estable")
    canonical_case_id = str(uuid.UUID(str(case_id)))
    payload_bytes = json.dumps(
        {
            "u": updated_at.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "i": canonical_case_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = hmac.new(
        credential_token.encode("utf-8"),
        _partner_case_cursor_material(
            partner_id=partner_id,
            q=q,
            status=status,
            payload=encoded,
        ),
        hashlib.sha256,
    ).hexdigest()
    cursor = f"{_PARTNER_CASE_CURSOR_VERSION}.{encoded}.{signature}"
    if len(cursor) > _PARTNER_CASE_CURSOR_MAX_CHARS:
        raise ValueError("La página partner no puede codificarse")
    return cursor


def _decode_partner_case_cursor(
    cursor: str,
    *,
    credential_token: str,
    partner_id: str,
    q: str,
    status: str,
) -> tuple[datetime, str]:
    candidate = str(cursor or "").strip()
    if not candidate or len(candidate) > _PARTNER_CASE_CURSOR_MAX_CHARS:
        raise HTTPException(status_code=422, detail="Cursor de listado no válido")
    parts = candidate.split(".")
    if (
        len(parts) != 3
        or parts[0] != _PARTNER_CASE_CURSOR_VERSION
        or not _BASE64URL_RE.fullmatch(parts[1])
        or not re.fullmatch(r"[0-9a-f]{64}", parts[2])
    ):
        raise HTTPException(status_code=422, detail="Cursor de listado no válido")
    encoded, supplied_signature = parts[1], parts[2]
    expected_signature = hmac.new(
        credential_token.encode("utf-8"),
        _partner_case_cursor_material(
            partner_id=partner_id,
            q=q,
            status=status,
            payload=encoded,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=422, detail="Cursor de listado no válido")
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"i", "u"}:
            raise ValueError
        case_id = str(uuid.UUID(str(payload["i"])))
        updated_at = datetime.fromisoformat(
            str(payload["u"]).replace("Z", "+00:00")
        )
        if updated_at.tzinfo is None:
            raise ValueError
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=422,
            detail="Cursor de listado no válido",
        ) from None
    return updated_at.astimezone(timezone.utc), case_id


def _assert_partner_event_payload_safe(value: Any) -> None:
    """Impide persistir credenciales o coordenadas internas por accidente."""

    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            normalized_key = "".join(
                character
                for character in str(raw_key).casefold()
                if character.isalnum()
            )
            if normalized_key in _PARTNER_EVENT_FORBIDDEN_KEYS:
                raise ValueError("El evento partner contiene un campo reservado")
            _assert_partner_event_payload_safe(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_partner_event_payload_safe(nested)


def _event(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    _assert_partner_event_payload_safe(payload)
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
        ),
        {"case_id": case_id, "type": typ, "payload": json.dumps(payload)},
    )


@router.get("/session")
def get_partner_session(
    response: Response,
    authorization: Optional[str] = Header(default=None),
    rtm_partner_session: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_SESSION_COOKIE,
    ),
) -> Dict[str, Any]:
    """Valida la cookie sin consultar ni devolver expedientes o identidad PII."""

    response.headers.update(_NO_STORE_HEADERS)
    response.headers["Vary"] = "Authorization, Cookie"
    credential = _partner_credential(authorization, rtm_partner_session)
    engine = get_engine()
    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, credential.token)
    return {
        "ok": True,
        "authenticated": True,
        "partner_name": str(partner["name"] or "")[:160],
        "expires_at": partner["expires_at"]
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


@router.get("/cases")
def list_partner_cases(
    response: Response,
    authorization: Optional[str] = Header(default=None),
    rtm_partner_session: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_SESSION_COOKIE,
    ),
    q: Optional[str] = Query(default=None, max_length=160),
    status: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(
        default=_PARTNER_CASE_LIST_DEFAULT_LIMIT,
        ge=1,
        le=_PARTNER_CASE_LIST_MAX_LIMIT,
    ),
    cursor: Optional[str] = Query(
        default=None,
        max_length=_PARTNER_CASE_CURSOR_MAX_CHARS,
    ),
) -> Dict[str, Any]:
    response.headers.update(_NO_STORE_HEADERS)
    response.headers["Vary"] = "Authorization, Cookie"
    credential = _partner_credential(authorization, rtm_partner_session)
    engine = get_engine()

    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, credential.token)

        q_clean = " ".join(str(q or "").split())
        status_clean = str(status or "").strip().casefold()
        if status_clean and status_clean not in _PARTNER_CASE_STATUSES:
            raise HTTPException(status_code=422, detail="Filtro de estado no válido")
        cursor_position = (
            _decode_partner_case_cursor(
                cursor,
                credential_token=credential.token,
                partner_id=partner["id"],
                q=q_clean,
                status=status_clean,
            )
            if cursor
            else None
        )

        sql = """
            SELECT
                c.id,
                c.contact_name,
                c.contact_email,
                c.status,
                COALESCE(c.payment_status, 'monthly') AS payment_status,
                c.updated_at,
                (
                    SELECT COUNT(*)
                    FROM documents d
                    WHERE d.case_id = c.id
                ) AS docs_total,
                EXISTS(
                    SELECT 1
                    FROM documents d2
                    WHERE d2.case_id = c.id
                      AND d2.kind IN (
                          'authorization_signed_candidate',
                          'authorization_signed',
                          'authorization_signed_rejected'
                      )
                ) AS authorization_document_uploaded,
                EXISTS(
                    SELECT 1
                    FROM documents d3
                    WHERE d3.case_id = c.id
                      AND d3.kind = 'authorization_signed_candidate'
                ) AS authorization_pending_review,
                EXISTS(
                    SELECT 1
                    FROM documents d4
                    WHERE d4.case_id = c.id
                      AND d4.kind = 'authorization_signed'
                      AND EXISTS (
                          SELECT 1 FROM events e4
                          WHERE e4.case_id=c.id
                            AND e4.type='authorization_signature_approved'
                            AND e4.payload->'material'->>'candidate_document_id'=
                                d4.id::text
                      )
                ) AS authorization_verified,
                EXISTS(
                    SELECT 1
                    FROM documents d5
                    WHERE d5.case_id = c.id
                      AND d5.kind = 'authorization_signed_rejected'
                ) AS authorization_rejected
            FROM cases c
            WHERE c.partner_id = :pid
        """
        params: Dict[str, Any] = {
            "pid": partner["id"],
            "page_size": int(limit) + 1,
        }

        if status_clean:
            sql += " AND c.status = :status"
            params["status"] = status_clean

        if q_clean:
            sql += (
                " AND (COALESCE(c.contact_name,'') ILIKE :q ESCAPE '!' "
                "OR COALESCE(c.contact_email,'') ILIKE :q ESCAPE '!' "
                "OR CAST(c.id AS TEXT) ILIKE :q ESCAPE '!')"
            )
            escaped_q = (
                q_clean.replace("!", "!!")
                .replace("%", "!%")
                .replace("_", "!_")
            )
            params["q"] = f"%{escaped_q}%"

        if cursor_position is not None:
            cursor_updated_at, cursor_case_id = cursor_position
            sql += (
                " AND (c.updated_at < :cursor_updated_at "
                "OR (c.updated_at = :cursor_updated_at "
                "AND c.id < CAST(:cursor_case_id AS UUID)))"
            )
            params.update(
                {
                    "cursor_updated_at": cursor_updated_at,
                    "cursor_case_id": cursor_case_id,
                }
            )

        sql += " ORDER BY c.updated_at DESC, c.id DESC LIMIT :page_size"

        rows = list(conn.execute(text(sql), params).fetchall())

    items = []
    page_rows = rows[: int(limit)]
    for row in page_rows:
        evidence_status = (
            "verified"
            if bool(row[9])
            else "pending_review"
            if bool(row[8])
            else "rejected"
            if bool(row[10])
            else "not_submitted"
        )
        items.append({
            "case_id": str(row[0]),
            "client_name": row[1] or "",
            "client_email": row[2] or "",
            "status": row[3] or "uploaded",
            "payment_status": row[4] or "monthly",
            "updated_at": str(row[5]) if row[5] else None,
            "authorization_mode": "partner_custody",
            "authorization_received": bool(row[7]),
            "authorization_document_uploaded": bool(row[7]),
            "authorization_verified": bool(row[9]),
            "authorization_evidence_status": evidence_status,
            "docs_total": int(row[6] or 0),
        })

    next_cursor = None
    if len(rows) > int(limit) and page_rows:
        last_row = page_rows[-1]
        next_cursor = _encode_partner_case_cursor(
            credential_token=credential.token,
            partner_id=partner["id"],
            q=q_clean,
            status=status_clean,
            updated_at=last_row[5],
            case_id=str(last_row[0]),
        )

    return {
        "ok": True,
        "partner_name": partner["name"],
        "items": items,
        "next_cursor": next_cursor,
    }


class _StrictPartnerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PartnerCreateIn(_StrictPartnerInput):
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=12, max_length=256, repr=False)


class PartnerLoginIn(_StrictPartnerInput):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256, repr=False)


class PartnerChangePasswordIn(_StrictPartnerInput):
    email: EmailStr = Field(max_length=254)
    old_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=12, max_length=256, repr=False)


@router.post("/admin-create")
def admin_create_partner(
    payload: PartnerCreateIn,
    request: Request,
    response: Response,
    context: SupervisorContext = Depends(require_partner_admin_supervisor),
) -> Dict[str, Any]:
    response.headers.update(_NO_STORE_HEADERS)
    name = payload.name.strip()
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    if len(password) < 12:
        raise HTTPException(
            status_code=400,
            detail="Password mínimo 12 caracteres",
            headers=_NO_STORE_HEADERS,
        )

    salt = ""
    pwd_hash = _modern_password_hash(password)
    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM partners WHERE email=:e"), {"e": email}).fetchone()
        if exists:
            raise HTTPException(
                status_code=409,
                detail="Ya existe un partner con ese email",
                headers=_NO_STORE_HEADERS,
            )
        row = conn.execute(
            text(
                "INSERT INTO partners("
                "name, email, password_salt, password_hash, api_token, active, "
                "must_change_password, created_at, updated_at"
                ") VALUES ("
                ":n,:e,:s,:h,NULL,TRUE,TRUE,NOW(),NOW()"
                ") RETURNING id"
            ),
            {"n": name, "e": email, "s": salt, "h": pwd_hash},
        ).fetchone()
        client_host = request.client.host if request.client else None
        request_context = build_request_fingerprint(
            request.headers,
            client_host=client_host,
            hmac_key=context.config.auth.hmac_key,
            trust_proxy_headers=context.config.auth.trust_proxy_headers,
            trusted_proxy_cidrs=context.config.auth.trusted_proxy_cidrs,
        )
        audit_event_id = record_operator_access_event(
            conn,
            context=request_context,
            event_type="admin.partner_created",
            result="success",
            auth_method="bearer",
            retention_days=context.config.auth.evidence_retention_days,
            operator_id=context.session.operator_id,
            session_id=context.session.session_id,
            device_id=getattr(context.session, "device_id", None),
            reason_code="partner_created_with_temporary_credential",
            reason_detail=f"partner_id={row[0]}",
            risk_flags=("supervisor_action",),
        )
    return {
        "ok": True,
        "partner_id": str(row[0]),
        "must_change_password": True,
        "temporary_credential": True,
        "token_returned": False,
        "created_by_operator_id": context.session.operator_id,
        "audit_event_id": audit_event_id,
    }


@router.post("/login")
def partner_login(payload: PartnerLoginIn, response: Response) -> Dict[str, Any]:
    response.headers.update(_NO_STORE_HEADERS)
    email = str(payload.email).strip().lower()
    password = payload.password.strip()
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, email, password_salt, password_hash, active, "
                "must_change_password FROM partners WHERE email=:e"
            ),
            {"e": email},
        ).fetchone()
        if not row or not bool(row[5]):
            _verify_password(
                password,
                "",
                _DUMMY_PARTNER_PASSWORD_HASH,
            )
            raise HTTPException(
                status_code=401,
                detail="Credenciales incorrectas",
                headers=_NO_STORE_HEADERS,
            )
        salt = row[3] or ""
        expected = row[4] or ""
        valid, needs_upgrade = _verify_password(password, salt, expected)
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Credenciales incorrectas",
                headers=_NO_STORE_HEADERS,
            )
        if bool(row[6]):
            # La contraseña inicial solo habilita su sustitución; nunca concede
            # una sesión capaz de leer o crear expedientes.
            conn.execute(
                text(
                    "UPDATE partners SET api_token=NULL, updated_at=NOW() "
                    "WHERE id=:id"
                ),
                {"id": row[0]},
            )
            _clear_partner_session_cookies(response)
            return {
                "ok": True,
                "partner_name": row[1],
                "must_change_password": True,
                "token_returned": False,
            }
        token = _make_token()
        expires_at = _partner_token_expiration(token)
        if needs_upgrade:
            conn.execute(
                text(
                    "UPDATE partners SET api_token=:t, password_salt='', "
                    "password_hash=:h, updated_at=NOW() WHERE id=:id"
                ),
                {"t": _stored_token(token), "h": _modern_password_hash(password), "id": row[0]},
            )
        else:
            conn.execute(
                text("UPDATE partners SET api_token=:t, updated_at=NOW() WHERE id=:id"),
                {"t": _stored_token(token), "id": row[0]},
            )
    _set_partner_session_cookies(
        response,
        token=token,
        expires_at=expires_at,
    )
    return {
        "ok": True,
        "authenticated": True,
        "partner_name": row[1],
        "must_change_password": False,
        "expires_at": expires_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "token_returned": False,
    }


@router.post("/change-password")
def partner_change_initial_password(
    payload: PartnerChangePasswordIn,
    response: Response,
) -> Dict[str, Any]:
    """Consume una contraseña temporal sin emitir sesión en el mismo paso."""

    response.headers.update(_NO_STORE_HEADERS)
    email = str(payload.email).strip().lower()
    old_password = payload.old_password.strip()
    new_password = payload.new_password.strip()
    if len(new_password) < 12:
        raise HTTPException(
            status_code=400,
            detail="Password mínimo 12 caracteres",
            headers=_NO_STORE_HEADERS,
        )
    if hmac.compare_digest(old_password, new_password):
        raise HTTPException(
            status_code=400,
            detail="La nueva contraseña debe ser distinta de la temporal",
            headers=_NO_STORE_HEADERS,
        )

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, password_salt, password_hash, active, "
                "must_change_password FROM partners WHERE email=:e"
            ),
            {"e": email},
        ).fetchone()
        if not row or not bool(row[3]) or not bool(row[4]):
            _verify_password(old_password, "", _DUMMY_PARTNER_PASSWORD_HASH)
            raise HTTPException(
                status_code=401,
                detail="Credenciales incorrectas",
                headers=_NO_STORE_HEADERS,
            )
        expected = str(row[2] or "")
        valid, _ = _verify_password(old_password, str(row[1] or ""), expected)
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Credenciales incorrectas",
                headers=_NO_STORE_HEADERS,
            )
        result = conn.execute(
            text(
                "UPDATE partners SET password_salt='', password_hash=:new_hash, "
                "must_change_password=FALSE, api_token=NULL, updated_at=NOW() "
                "WHERE id=:id AND must_change_password=TRUE "
                "AND password_hash=:expected_hash"
            ),
            {
                "id": row[0],
                "new_hash": _modern_password_hash(new_password),
                "expected_hash": expected,
            },
        )
        if int(result.rowcount or 0) != 1:
            raise HTTPException(
                status_code=409,
                detail="La credencial temporal ya fue utilizada",
                headers=_NO_STORE_HEADERS,
            )
    _clear_partner_session_cookies(response)
    return {
        "ok": True,
        "must_change_password": False,
        "login_required": True,
    }


@router.post("/logout")
def partner_logout(
    response: Response,
    authorization: Optional[str] = Header(default=None),
    rtm_partner_session: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_SESSION_COOKIE,
    ),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    rtm_partner_csrf: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_CSRF_COOKIE,
    ),
) -> Dict[str, Any]:
    credential = _partner_credential(authorization, rtm_partner_session)
    _require_partner_csrf(
        credential,
        csrf_header=x_csrf_token,
        csrf_cookie=rtm_partner_csrf,
    )
    engine = get_engine()
    with engine.begin() as conn:
        partner = _get_partner_by_token(conn, credential.token)
        conn.execute(
            text(
                "UPDATE partners SET api_token=NULL, updated_at=NOW() "
                "WHERE id=:id AND api_token=:digest"
            ),
            {
                "id": partner["id"],
                "digest": _stored_token(credential.token),
            },
        )
    _clear_partner_session_cookies(response)
    return {"ok": True}


def _persist_partner_case(
    engine,
    *,
    partner: Mapping[str, Any],
    client_email: Optional[str],
    client_name: str,
    partner_note: str,
    interesado: Mapping[str, Any],
    auth_data: bytes,
    auth_meta: Any,
    prepared_files: List[tuple[bytes, Any]],
) -> Dict[str, Any]:
    """Sube el lote validado y lo publica en una única transacción SQL."""

    case_id = str(uuid.uuid4())
    coordinates: List[tuple[str, str]] = []
    stored_files: List[tuple[str, str, bytes, Any]] = []
    uploaded: List[Dict[str, Any]] = []
    try:
        auth_bucket, auth_key = upload_bytes(
            case_id,
            "authorization_signature_candidate",
            auth_data,
            ext=auth_meta.extension,
            mime=auth_meta.mime,
        )
        coordinates.append((auth_bucket, auth_key))

        for data, meta in prepared_files:
            bucket, key = upload_bytes(
                case_id,
                "original",
                data,
                ext=meta.extension,
                mime=meta.mime,
            )
            coordinates.append((bucket, key))
            stored_files.append((bucket, key, data, meta))
            uploaded.append(
                {
                    "filename": meta.filename,
                    "mime": meta.mime,
                    "size_bytes": len(data),
                    "sha256": meta.sha256,
                }
            )

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO cases (
                        id, contact_email, contact_name,
                        channel, partner_id, partner_name,
                        payment_status, status, interested_data,
                        created_at, updated_at
                    )
                    VALUES (
                        CAST(:id AS UUID), :ce, :cn,
                        'partner', :pid, :pname,
                        'monthly', 'uploaded', CAST(:idata AS JSONB),
                        NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": case_id,
                    "ce": client_email,
                    "cn": client_name or None,
                    "pid": partner["id"],
                    "pname": partner["name"],
                    "idata": json.dumps(dict(interesado or {})),
                },
            ).fetchone()
            if not row or str(row[0]) != case_id:
                raise RuntimeError("El expediente partner no fue registrado")

            _event(
                conn,
                case_id,
                "partner_case_created",
                {
                    "partner_id": partner["id"],
                    "partner_name": partner["name"],
                    "client_email": client_email,
                    "client_name": client_name or None,
                    "partner_note": partner_note or None,
                },
            )
            authorization_row = conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, sha256, mime,
                        size_bytes, created_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'authorization_signed_candidate', :b, :k,
                        :sha256, :m, :s, NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "case_id": case_id,
                    "b": auth_bucket,
                    "k": auth_key,
                    "sha256": auth_meta.sha256,
                    "m": auth_meta.mime,
                    "s": len(auth_data),
                },
            ).fetchone()
            if not authorization_row:
                raise RuntimeError("La evidencia de autorización no fue registrada")
            _event(
                conn,
                case_id,
                "authorization_signature_candidate_unbound_uploaded",
                {
                    "source": "partner",
                    "candidate_document_id": str(authorization_row[0]),
                    "review_status": "pending_review",
                    "binding_status": "unbound_partner_submission",
                },
            )

            for bucket, key, data, meta in stored_files:
                conn.execute(
                    text(
                        """
                        INSERT INTO documents(
                            case_id, kind, b2_bucket, b2_key, sha256, mime,
                            size_bytes, created_at
                        ) VALUES (
                            CAST(:case_id AS UUID), 'original', :b, :k,
                            :sha256, :m, :s, NOW()
                        )
                        """
                    ),
                    {
                        "case_id": case_id,
                        "b": bucket,
                        "k": key,
                        "sha256": meta.sha256,
                        "m": meta.mime,
                        "s": len(data),
                    },
                )
            _event(
                conn,
                case_id,
                "partner_documents_uploaded",
                {"count": len(uploaded)},
            )
    except Exception:
        _cleanup_partner_uploads(coordinates)
        raise

    return {
        "ok": True,
        "case_id": case_id,
        "uploaded": uploaded,
        "authorization_evidence": {
            "status": "pending_review",
            "binding_status": "unbound_partner_submission",
            "candidate_document": {
                "id": str(authorization_row[0]),
                "filename": auth_meta.filename,
                "mime": auth_meta.mime,
                "size_bytes": len(auth_data),
                "sha256": auth_meta.sha256,
            },
        },
    }


@router.post("/cases")
def create_partner_case(
    response: Response,
    authorization: Optional[str] = Header(default=None),
    rtm_partner_session: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_SESSION_COOKIE,
    ),
    x_csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    rtm_partner_csrf: Optional[str] = Cookie(
        default=None,
        alias=_PARTNER_CSRF_COOKIE,
    ),
) -> Dict[str, Any]:
    """Fail closed until partner authority has a case-bound review chain.

    This route deliberately declares no body parameters.  Starlette can reject
    an authenticated request without parsing attacker-controlled multipart
    files, creating a case, or touching document custody.
    """
    response.headers.update(_NO_STORE_HEADERS)
    response.headers["Vary"] = "Authorization, Cookie"
    credential = _partner_credential(authorization, rtm_partner_session)
    _require_partner_csrf(
        credential,
        csrf_header=x_csrf_token,
        csrf_cookie=rtm_partner_csrf,
    )
    engine = get_engine()
    with engine.begin() as conn:
        _get_partner_by_token(conn, credential.token)
    raise HTTPException(
        status_code=503,
        detail=_PARTNER_CASE_INTAKE_DISABLED_DETAIL,
        headers={**_NO_STORE_HEADERS, "Vary": "Authorization, Cookie"},
    )


class PartnerSignupRequest(_StrictPartnerInput):
    empresa: str = Field(min_length=1, max_length=160)
    contacto: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=40)
    provincia: Optional[str] = Field(default=None, max_length=100)
    volumen: Optional[str] = Field(default=None, max_length=100)
    mensaje: Optional[str] = Field(default=None, max_length=4000)


@router.post("/signup")
def partner_signup(payload: PartnerSignupRequest):
    require_http_capability("outbound_email")

    body = f"""
Nueva solicitud de asesoría:

Empresa: {payload.empresa}
Contacto: {payload.contacto}
Email: {payload.email}
Teléfono: {payload.telefono}
Provincia: {payload.provincia}
Volumen: {payload.volumen}

Mensaje:
{payload.mensaje}
    """

    try:
        sent = send_email(
            to_email=(os.getenv("CONTACT_TO") or "info@recurretumulta.eu").strip(),
            subject="Nueva solicitud de alta asesoría",
            body=body,
            reply_to=str(payload.email),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=_PARTNER_SIGNUP_FAILURE_DETAIL,
        ) from None
    if not sent:
        raise HTTPException(
            status_code=500,
            detail=_PARTNER_SIGNUP_FAILURE_DETAIL,
        )

    return {"ok": True}
