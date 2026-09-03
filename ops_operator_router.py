from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import uuid
from typing import Optional, Any, Dict, List, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from database import get_engine
from case_authority import (
    build_authorization_signature_view_attestation,
    build_rejected_authorization_signature_attestation,
    build_reviewed_signed_authority_attestation,
    verify_authorization_signature_candidate,
    verify_authorization_signature_view_attestation,
    verify_signed_case_authority,
)
from generate import GenerateRequest, generate_dgt
from b2_storage import (
    B2ObjectTooLargeError,
    delete_object,
    download_bytes_limited,
    get_b2_bucket,
    upload_bytes,
)
from docx_builder import build_docx
from pdf_builder import build_pdf
from reanalysis import reanalyze_traffic_fine_case
from rtm_presenter_policy import (
    PresenterPolicyError,
    PresenterRuntimeDisabled,
    load_presenter_runtime_configuration,
)
from rtm_core.ops_case_scope import (
    load_ops_case_scope,
    require_case_in_scope,
    require_current_case_scope,
)
from rtm_core.case_state_policy import lock_case_for_material_mutation
from rtm_core.upload_security import PDF, UploadSecurityError, validate_document_bytes

router = APIRouter(
    prefix="/ops/cases",
    tags=["ops-operator"],
    dependencies=[Depends(require_current_case_scope)],
)


def _cleanup_final_resource_uploads(
    coordinates: List[tuple[str, str]],
) -> None:
    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass


@contextmanager
def _final_resource_transaction(engine, coordinates: List[tuple[str, str]]):
    try:
        with engine.begin() as conn:
            yield conn
    except Exception:
        _cleanup_final_resource_uploads(coordinates)
        raise


def _utcnow():
    return datetime.now(timezone.utc)


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    token = (x_operator_token or "").strip()
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={"code": "operator_auth_unavailable"},
        )
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized operator")
    return token


class _StrictOpsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApproveBody(_StrictOpsInput):
    note: Optional[str] = Field(default=None, max_length=4000)


class AuthorizationSignatureReviewBody(_StrictOpsInput):
    decision: Literal["approve", "reject"]
    candidate_document_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )
    candidate_attestation_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    reviewed_entire_document: Literal[True]
    generated_document_matches: bool
    identity_matches: bool
    signature_present: bool
    reason_code: Optional[
        Literal[
            "document_mismatch",
            "identity_mismatch",
            "signature_missing",
            "illegible",
            "suspected_tampering",
        ]
    ] = None


class ManualBody(_StrictOpsInput):
    motivo: str = Field(min_length=3, max_length=4000)


class NoteBody(_StrictOpsInput):
    note: str = Field(min_length=1, max_length=4000)


class OverrideFamilyBody(_StrictOpsInput):
    familia: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_-]+$")
    motivo: str = Field(min_length=3, max_length=4000)


class OverrideAndRegenerateBody(_StrictOpsInput):
    familia: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_-]+$")
    motivo: str = Field(min_length=3, max_length=4000)


class RewriteHechoBody(_StrictOpsInput):
    hecho: str = Field(min_length=5, max_length=20_000)
    motivo: str = Field(min_length=3, max_length=4000)
    familia: Optional[str] = Field(
        default=None,
        max_length=96,
        pattern=r"^[a-z0-9_-]+$",
    )


class SubmitDGTBody(_StrictOpsInput):
    document_url: Optional[str] = Field(default=None, max_length=2048)
    force: bool = False


class SaveAiOverridesBody(_StrictOpsInput):
    familia: Optional[str] = Field(
        default=None,
        max_length=96,
        pattern=r"^[a-z0-9_-]+$",
    )
    hecho: Optional[str] = Field(default=None, max_length=20_000)
    motivo: str = Field(min_length=3, max_length=4000)


class FinalResourceBody(_StrictOpsInput):
    content: str = Field(min_length=1, max_length=500_000)


class SendCompleteBody(_StrictOpsInput):
    destination: Optional[str] = Field(default=None, max_length=500)
    channel: str = Field(default="ops", min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    note: Optional[str] = Field(default=None, max_length=4000)


def _trusted_operator_actor(request: Request) -> str:
    context = getattr(request.state, "rtm_operator_context", None)
    actor = getattr(context, "actor", None)
    if isinstance(actor, str) and actor.startswith("operator:") and len(actor) <= 80:
        return actor
    return "operator:legacy-local"


def _require_individual_authorization_reviewer(scope) -> None:
    """A signed grant is high impact: only a named supervisor may attest it."""

    if (
        not scope.individual_session
        or scope.role_code != "rtm.supervisor"
        or "ops.supervise" not in set(scope.permissions)
    ):
        raise HTTPException(
            status_code=403,
            detail="Revisión individual supervisora requerida",
        )


def _reviewer_identity(request: Request) -> tuple[str, str, str]:
    context = getattr(request.state, "rtm_operator_context", None)
    operator_id = str(getattr(context, "operator_id", "") or "")
    session_id = str(getattr(context, "session_id", "") or "")
    actor = str(getattr(context, "actor", "") or "")
    try:
        canonical_operator_id = str(uuid.UUID(operator_id))
        canonical_session_id = str(uuid.UUID(session_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=403,
            detail="Identidad individual de revisión requerida",
        ) from exc
    if actor != f"operator:{canonical_operator_id}":
        raise HTTPException(
            status_code=403,
            detail="Identidad individual de revisión requerida",
        )
    return canonical_operator_id, canonical_session_id, actor


def _require_recent_authorization_reauthentication(
    conn, request: Request
) -> tuple[str, str]:
    """Bind a high-impact signature decision to a recent password step-up.

    The request context is injected by the individual-session bridge, but the
    authoritative proof is reloaded from PostgreSQL in the same transaction as
    the review.  A mere login timestamp, a client-supplied header, or a touched
    session therefore cannot satisfy this gate.
    """

    canonical_operator_id, canonical_session_id, actor = _reviewer_identity(request)

    verified = conn.execute(
        text(
            """
            SELECT e.id
            FROM rtm_operator_sessions s
            JOIN rtm_operators o
              ON o.id=s.operator_id
            JOIN rtm_operator_access_events e
              ON e.session_id=s.id
             AND e.operator_id=s.operator_id
             AND e.event_type='auth.reauthenticated'
             AND e.result='success'
             AND e.reason_code='password_reverified'
             AND e.occurred_at=s.last_verified_at
            WHERE s.id=CAST(:session_id AS UUID)
              AND s.operator_id=CAST(:operator_id AS UUID)
              AND s.status='active'
              AND s.expires_at > NOW()
              AND (
                    s.absolute_expires_at IS NULL
                    OR s.absolute_expires_at > NOW()
                  )
              AND s.last_verified_at > s.login_at
              AND s.last_verified_at >= NOW() - INTERVAL '5 minutes'
              AND s.last_verified_at <= NOW() + INTERVAL '30 seconds'
              AND o.status='active'
              AND s.auth_epoch=o.auth_epoch
            LIMIT 1
            """
        ),
        {
            "session_id": canonical_session_id,
            "operator_id": canonical_operator_id,
        },
    ).fetchone()
    if not verified:
        raise HTTPException(
            status_code=403,
            detail="Reautenticación reciente requerida",
        )
    try:
        reauthentication_event_id = str(uuid.UUID(str(verified[0])))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=403,
            detail="Reautenticación reciente requerida",
        ) from exc
    return actor, reauthentication_event_id


def _candidate_document_record(conn, case_id: str, candidate_document_id: str):
    row = conn.execute(
        text(
            """
            SELECT b2_bucket, b2_key, sha256, mime, size_bytes
            FROM documents
            WHERE case_id=:case_id
              AND id=CAST(:document_id AS UUID)
              AND kind='authorization_signed_candidate'
            LIMIT 1
            """
        ),
        {"case_id": case_id, "document_id": candidate_document_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="Candidato de firma no disponible")
    expected_prefix = f"cases/{case_id}/authorization_signature_candidate/"
    if (
        str(row[0] or "") != get_b2_bucket()
        or not str(row[1] or "").startswith(expected_prefix)
        or str(row[3] or "") != PDF
        or int(row[4] or 0) < 1
        or int(row[4] or 0) > 10 * 1024 * 1024
    ):
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable")
    return row


def _download_verified_candidate_pdf(
    conn,
    *,
    case_id: str,
    candidate_document_id: str,
    candidate_payload: dict[str, Any],
) -> bytes:
    row = _candidate_document_record(conn, case_id, candidate_document_id)
    try:
        data = download_bytes_limited(
            str(row[0]),
            str(row[1]),
            max_bytes=10 * 1024 * 1024,
            case_id=case_id,
        )
    except B2ObjectTooLargeError as exc:
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo recuperar el candidato") from exc
    expected_digest = str(
        (candidate_payload.get("material") or {}).get("candidate_document_sha256")
        or ""
    )
    actual_digest = hashlib.sha256(data).hexdigest()
    if (
        len(data) != int(row[4] or 0)
        or not hmac.compare_digest(actual_digest, str(row[2] or "").lower())
        or not hmac.compare_digest(actual_digest, expected_digest.lower())
    ):
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable")
    try:
        validate_document_bytes(
            filename="authorization-signed.pdf",
            declared_mime=PDF,
            data=data,
            max_bytes=10 * 1024 * 1024,
            allowed_mimes={PDF},
        )
    except UploadSecurityError as exc:
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable") from exc
    return data


def _require_recent_candidate_view(
    conn,
    *,
    case_id: str,
    candidate_payload: dict[str, Any],
    reviewer_actor: str,
    operator_session_id: str,
) -> tuple[str, dict[str, Any]]:
    candidate_id = str(
        (candidate_payload.get("material") or {}).get("candidate_document_id") or ""
    )
    row = conn.execute(
        text(
            """
            SELECT id, payload, created_at
            FROM events
            WHERE case_id=:case_id
              AND type='authorization_signature_candidate_viewed'
              AND payload->'material'->>'candidate_document_id'=:document_id
              AND created_at >= NOW() - INTERVAL '15 minutes'
              AND created_at <= NOW() + INTERVAL '30 seconds'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id, "document_id": candidate_id},
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Debe visualizarse el candidato exacto antes de revisarlo",
        )
    payload = row[1] if isinstance(row[1], dict) else json.loads(str(row[1]))
    verify_authorization_signature_view_attestation(
        payload,
        case_id=case_id,
        candidate_payload=candidate_payload,
        reviewer_actor=reviewer_actor,
        operator_session_id=operator_session_id,
    )
    try:
        return str(uuid.UUID(str(row[0]))), payload
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Visualización del candidato no verificable",
        ) from exc


def _case_or_404(conn, case_id: str):
    row = conn.execute(
        text(
            '''
            SELECT id, status, updated_at, COALESCE(test_mode,FALSE)
            FROM cases
            WHERE id = :id
            '''
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return {
        "id": str(row[0]),
        "status": row[1] or "pending_review",
        "updated_at": row[2],
        "test_mode": bool(row[3]),
    }


def _presenter_available(case: Dict[str, Any]) -> bool:
    if case.get("test_mode") is not True:
        return False
    try:
        load_presenter_runtime_configuration(require_enabled=True)
    except (PresenterRuntimeDisabled, PresenterPolicyError):
        return False
    return True


def _get_status(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row[0] or "pending_review"


def _set_status(conn, case_id: str, status: str):
    conn.execute(
        text(
            '''
            UPDATE cases
            SET status = :status, updated_at = NOW()
            WHERE id = :id
            '''
        ),
        {"id": case_id, "status": status},
    )


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            '''
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            '''
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}),
        },
    )


def _load_interesado(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row or not row[0]:
        return {}
    data = row[0]
    return data if isinstance(data, dict) else {}


def _save_ai_overrides_in_interested_data(
    conn,
    case_id: str,
    *,
    familia: Optional[str] = None,
    hecho: Optional[str] = None,
    motivo: Optional[str] = None,
):
    current = _load_interesado(conn, case_id)
    current = dict(current or {})

    ai_overrides = dict(current.get("ai_overrides") or {})
    if familia is not None:
        ai_overrides["familia"] = familia
        current["manual_family"] = familia
    if hecho is not None:
        ai_overrides["hecho"] = hecho
        current["manual_hecho_denunciado"] = hecho
    if motivo is not None:
        ai_overrides["motivo"] = motivo
        current["manual_hecho_motivo"] = motivo

    ai_overrides["saved_at"] = _utcnow().isoformat()
    current["ai_overrides"] = ai_overrides

    conn.execute(
        text(
            '''
            UPDATE cases
            SET interested_data = CAST(:data AS JSONB),
                updated_at = NOW()
            WHERE id = :id
            '''
        ),
        {"id": case_id, "data": json.dumps(current)},
    )

    return ai_overrides




def _stringify_generate_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _prepare_interesado_for_generate(interesado: Dict[str, Any]) -> Dict[str, str]:
    raw = dict(interesado or {})
    prepared: Dict[str, str] = {}
    for key, value in raw.items():
        key_str = str(key).strip()
        if not key_str:
            continue
        val_str = _stringify_generate_value(value)
        if val_str is not None:
            prepared[key_str] = val_str
    return prepared


def _load_ai_overrides(conn, case_id: str) -> Dict[str, Any]:
    interesado = _load_interesado(conn, case_id)
    overrides = dict((interesado or {}).get("ai_overrides") or {})

    familia = overrides.get("familia") or (interesado or {}).get("manual_family")
    hecho = overrides.get("hecho") or (interesado or {}).get("manual_hecho_denunciado")
    motivo = overrides.get("motivo") or (interesado or {}).get("manual_hecho_motivo")
    saved_at = overrides.get("saved_at")

    return {
        "familia": familia,
        "hecho": hecho,
        "motivo": motivo,
        "saved_at": saved_at,
    }



def _next_final_resource_version(conn, case_id: str) -> int:
    row = conn.execute(
        text("SELECT COALESCE(MAX(version), 0) + 1 FROM ops_final_resources WHERE case_id = :id"),
        {"id": case_id},
    ).fetchone()
    return int(row[0] or 1)


def _latest_final_resource(conn, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            '''
            SELECT id, content, version, is_final, created_by, created_at, updated_at
            FROM ops_final_resources
            WHERE case_id = :id
            ORDER BY version DESC, updated_at DESC
            LIMIT 1
            '''
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        return None
    return {
        "id": str(row[0]),
        "content": row[1] or "",
        "version": int(row[2] or 1),
        "is_final": bool(row[3]),
        "created_by": row[4] or "",
        "created_at": row[5],
        "updated_at": row[6],
    }


@router.post("/{case_id}/reanalyze")
def reanalyze_case(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Reanaliza los originales existentes sin crear caso ni repetir pago.

    V1: especialista Tráfico / Multa. El resultado final es una única extraction
    consolidada que queda lista para el generate existente.
    """
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
    return reanalyze_traffic_fine_case(case_id)


@router.get("/{case_id}/final-resource")
def get_final_resource(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        resource = _latest_final_resource(conn, case_id)
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": resource,
    }


@router.post("/{case_id}/final-resource")
def save_final_resource_draft(
    case_id: str,
    body: FinalResourceBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    created_by = _trusted_operator_actor(request)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="El recurso no puede estar vacío")

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        version = _next_final_resource_version(conn, case_id)

        row = conn.execute(
            text(
                '''
                INSERT INTO ops_final_resources(case_id, content, version, is_final, created_by, created_at, updated_at)
                VALUES (:case_id, :content, :version, FALSE, :created_by, NOW(), NOW())
                RETURNING id, created_at, updated_at
                '''
            ),
            {
                "case_id": case_id,
                "content": content,
                "version": version,
                "created_by": created_by,
            },
        ).fetchone()

        _append_event(
            conn,
            case_id,
            "ops_final_resource_draft_saved",
            {
                "resource_id": str(row[0]),
                "version": version,
                "chars": len(content),
                "created_by": created_by,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": {
            "id": str(row[0]),
            "content": content,
            "version": version,
            "is_final": False,
            "created_by": created_by,
            "created_at": row[1],
            "updated_at": row[2],
        },
    }


@router.post("/{case_id}/finalize-resource")
def finalize_resource(
    case_id: str,
    body: FinalResourceBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    created_by = _trusted_operator_actor(request)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="El recurso final no puede estar vacío")

    engine = get_engine()
    uploaded_coordinates: List[tuple[str, str]] = []
    with _final_resource_transaction(engine, uploaded_coordinates) as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        version = _next_final_resource_version(conn, case_id)

        conn.execute(
            text("UPDATE ops_final_resources SET is_final = FALSE, updated_at = NOW() WHERE case_id = :id"),
            {"id": case_id},
        )

        row = conn.execute(
            text(
                '''
                INSERT INTO ops_final_resources(case_id, content, version, is_final, created_by, created_at, updated_at)
                VALUES (:case_id, :content, :version, TRUE, :created_by, NOW(), NOW())
                RETURNING id, created_at, updated_at
                '''
            ),
            {
                "case_id": case_id,
                "content": content,
                "version": version,
                "created_by": created_by,
            },
        ).fetchone()

        txt_bytes = content.encode("utf-8")
        docx_bytes = build_docx("", content)
        pdf_bytes = build_pdf("", content)

        b2_bucket, b2_key_txt = upload_bytes(
            case_id,
            "final_resources",
            txt_bytes,
            ".txt",
            "text/plain; charset=utf-8",
        )
        uploaded_coordinates.append((b2_bucket, b2_key_txt))
        docx_bucket, b2_key_docx = upload_bytes(
            case_id,
            "final_resources",
            docx_bytes,
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        uploaded_coordinates.append((docx_bucket, b2_key_docx))
        pdf_bucket, b2_key_pdf = upload_bytes(
            case_id,
            "final_resources",
            pdf_bytes,
            ".pdf",
            "application/pdf",
        )
        uploaded_coordinates.append((pdf_bucket, b2_key_pdf))

        stored_documents = [
            {
                "kind": "final_resource_text",
                "bucket": b2_bucket,
                "key": b2_key_txt,
                "sha256": hashlib.sha256(txt_bytes).hexdigest(),
                "mime": "text/plain; charset=utf-8",
                "size_bytes": len(txt_bytes),
            },
            {
                "kind": "final_resource_docx",
                "bucket": docx_bucket,
                "key": b2_key_docx,
                "sha256": hashlib.sha256(docx_bytes).hexdigest(),
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": len(docx_bytes),
            },
            {
                "kind": "final_resource_pdf",
                "bucket": pdf_bucket,
                "key": b2_key_pdf,
                "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "mime": "application/pdf",
                "size_bytes": len(pdf_bytes),
            },
        ]

        documents = []
        for doc in stored_documents:
            document_row = conn.execute(
                text(
                    '''
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, sha256,
                        mime, size_bytes, created_at
                    ) VALUES (
                        :case_id, :kind, :bucket, :key, :sha256,
                        :mime, :size_bytes, NOW()
                    )
                    RETURNING id
                    '''
                ),
                {
                    "case_id": case_id,
                    "kind": doc["kind"],
                    "bucket": doc["bucket"],
                    "key": doc["key"],
                    "sha256": doc["sha256"],
                    "mime": doc["mime"],
                    "size_bytes": doc["size_bytes"],
                },
            ).fetchone()
            documents.append(
                {
                    "id": str(document_row[0]),
                    "kind": doc["kind"],
                    "sha256": doc["sha256"],
                    "mime": doc["mime"],
                    "size_bytes": doc["size_bytes"],
                    "custody": "rtm_internal_only",
                    "operator_export_allowed": False,
                }
            )

        _set_status(conn, case_id, "final_ready")
        _append_event(
            conn,
            case_id,
            "ops_final_resource_finalized",
            {
                "resource_id": str(row[0]),
                "version": version,
                "chars": len(content),
                "documents": documents,
                "created_by": created_by,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource": {
            "id": str(row[0]),
            "content": content,
            "version": version,
            "is_final": True,
            "created_by": created_by,
            "created_at": row[1],
            "updated_at": row[2],
        },
        "documents": documents,
    }


@router.post("/{case_id}/send-complete")
def send_complete_case_file(
    case_id: str,
    body: SendCompleteBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        resource = _latest_final_resource(conn, case_id)
        if not resource or not resource.get("is_final"):
            raise HTTPException(status_code=409, detail="Antes de enviar hay que guardar una versión final del recurso")

        docs_row = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE case_id = :id"),
            {"id": case_id},
        ).fetchone()
        docs_count = int(docs_row[0] or 0) if docs_row else 0

        _set_status(conn, case_id, "ready_for_delivery")
        _append_event(
            conn,
            case_id,
            "ops_complete_file_delivery_prepared",
            {
                "resource_id": resource.get("id"),
                "resource_version": resource.get("version"),
                "documents_count": docs_count,
                "destination": body.destination,
                "channel": body.channel or "ops",
                "note": body.note,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "resource_version": resource.get("version"),
        "documents_count": docs_count,
        "message": "Expediente completo preparado; falta evidencia externa de entrega.",
    }


@router.get("/{case_id}")
def get_case_detail(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)

        evs = conn.execute(
            text(
                '''
                SELECT payload
                FROM events
                WHERE case_id = :id AND type = 'ai_expediente_result'
                ORDER BY created_at DESC
                LIMIT 1
                '''
            ),
            {"id": case_id},
        ).fetchone()

        payload = evs[0] if evs and evs[0] else {}
        if not isinstance(payload, dict):
            payload = {}

        overrides = _load_ai_overrides(conn, case_id)

        familia = (
            overrides.get("familia")
            or payload.get("familia_resuelta")
            or payload.get("tipo_infraccion")
            or payload.get("classifier_result", {}).get("family")
            or payload.get("familia_detectada")
            or payload.get("familia")
            or payload.get("family")
        )
        confianza = (
            payload.get("tipo_infraccion_confidence")
            or payload.get("classifier_result", {}).get("confidence")
            or payload.get("confianza")
            or payload.get("confidence")
        )
        hecho = (
            overrides.get("hecho")
            or payload.get("hecho_imputado")
            or payload.get("hecho")
            or payload.get("hecho_para_recurso")
            or payload.get("arguments", {}).get("hecho")
            or payload.get("facts")
            or payload.get("detected_facts")
        )

        return {
            "id": case["id"],
            "status": case["status"],
            "familia_detectada": familia,
            "confianza": confianza,
            "hecho": hecho,
            "ai_overrides": overrides,
            "updated_at": case["updated_at"],
            "actions": {
                "presenter_available": _presenter_available(case),
            },
        }


@router.get("/{case_id}/ai-overrides")
def get_ai_overrides(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        overrides = _load_ai_overrides(conn, case_id)

    return {"ok": True, "case_id": case_id, "overrides": overrides}


@router.post("/{case_id}/save-ai-overrides")
def save_ai_overrides(
    case_id: str,
    body: SaveAiOverridesBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            hecho=body.hecho,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_ai_override_saved",
            {
                "familia": overrides.get("familia"),
                "hecho": overrides.get("hecho"),
                "motivo": overrides.get("motivo"),
                "saved_at": overrides.get("saved_at"),
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "overrides": overrides,
    }


@router.get(
    "/{case_id}/authorization-signature-candidate/{candidate_document_id}"
)
def view_authorization_signature_candidate(
    case_id: str,
    candidate_document_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Render only the exact, validated candidate and record who fetched it."""

    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        _require_individual_authorization_reviewer(scope)
        canonical_case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, canonical_case_id)
        _, operator_session_id, reviewer_actor = _reviewer_identity(request)
        chain = verify_authorization_signature_candidate(
            conn, canonical_case_id, candidate_document_id
        )
        data = _download_verified_candidate_pdf(
            conn,
            case_id=canonical_case_id,
            candidate_document_id=candidate_document_id,
            candidate_payload=chain["candidate"],
        )
        viewed_at = _utcnow().isoformat()
        view_attestation = build_authorization_signature_view_attestation(
            case_id=canonical_case_id,
            candidate_payload=chain["candidate"],
            reviewer_actor=reviewer_actor,
            operator_session_id=operator_session_id,
            viewed_at=viewed_at,
        )
        event_row = conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (
                    :case_id,
                    'authorization_signature_candidate_viewed',
                    CAST(:payload AS JSONB),
                    NOW()
                )
                RETURNING id
                """
            ),
            {
                "case_id": canonical_case_id,
                "payload": json.dumps(
                    view_attestation, ensure_ascii=False, sort_keys=True
                ),
            },
        ).fetchone()
        if not event_row:
            raise HTTPException(
                status_code=503,
                detail="No se pudo registrar la visualización segura",
            )

    headers = {
        "Cache-Control": "no-store, private, max-age=0",
        "Pragma": "no-cache",
        "Content-Disposition": (
            f'inline; filename="authorization_candidate_{candidate_document_id}.pdf"'
        ),
        "Content-Security-Policy": (
            "sandbox; default-src 'none'; object-src 'none'; frame-ancestors 'self'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }
    return Response(content=data, media_type=PDF, headers=headers)


@router.post("/{case_id}/authorization-signature-review")
def review_authorization_signature(
    case_id: str,
    body: AuthorizationSignatureReviewBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Approve/reject a bound candidate after an individual human review."""

    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        _require_individual_authorization_reviewer(scope)
        canonical_case_id = require_case_in_scope(
            conn, scope=scope, case_id=case_id
        )
        # La decisión cambia cuál es la evidencia firmada activa. El lock la
        # serializa con la apertura/liquidación de Stripe y congela terminales.
        lock_case_for_material_mutation(conn, canonical_case_id)
        _case_or_404(conn, canonical_case_id)
        reviewer_actor, reauthentication_event_id = (
            _require_recent_authorization_reauthentication(
                conn, request
            )
        )
        _, operator_session_id, _ = _reviewer_identity(request)
        chain = verify_authorization_signature_candidate(
            conn, canonical_case_id, body.candidate_document_id
        )
        candidate = chain["candidate"]
        if not hmac.compare_digest(
            body.candidate_attestation_sha256,
            str(candidate.get("material_sha256") or ""),
        ):
            raise HTTPException(
                status_code=409,
                detail="El candidato revisado ha cambiado",
            )
        _download_verified_candidate_pdf(
            conn,
            case_id=canonical_case_id,
            candidate_document_id=body.candidate_document_id,
            candidate_payload=candidate,
        )
        view_event_id, view_payload = _require_recent_candidate_view(
            conn,
            case_id=canonical_case_id,
            candidate_payload=candidate,
            reviewer_actor=reviewer_actor,
            operator_session_id=operator_session_id,
        )
        reviewed_at = _utcnow().isoformat()
        if body.decision == "approve":
            if not all(
                (
                    body.generated_document_matches,
                    body.identity_matches,
                    body.signature_present,
                )
            ):
                raise HTTPException(
                    status_code=422,
                    detail="La aprobación exige coincidencia, identidad y firma verificadas",
                )
            if body.reason_code is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Una aprobación no admite motivo de rechazo",
                )
            review_attestation = build_reviewed_signed_authority_attestation(
                case_id=canonical_case_id,
                authority_payload=chain["authority"],
                issuance_payload=chain["issuance"],
                candidate_payload=candidate,
                reviewer_actor=reviewer_actor,
                operator_session_id=operator_session_id,
                view_event_id=view_event_id,
                view_payload=view_payload,
                reauthentication_event_id=reauthentication_event_id,
                review_checklist={
                    "reviewed_entire_document": body.reviewed_entire_document,
                    "generated_document_matches": body.generated_document_matches,
                    "identity_matches": body.identity_matches,
                    "signature_present": body.signature_present,
                },
                reviewed_at=reviewed_at,
            )
            updated = conn.execute(
                text(
                    """
                    UPDATE documents
                    SET kind='authorization_signed'
                    WHERE case_id=:case_id
                      AND id=CAST(:document_id AS UUID)
                      AND kind='authorization_signed_candidate'
                    RETURNING id
                    """
                ),
                {
                    "case_id": canonical_case_id,
                    "document_id": body.candidate_document_id,
                },
            ).fetchone()
            if not updated:
                raise HTTPException(status_code=409, detail="El candidato ya fue revisado")
            _append_event(
                conn,
                canonical_case_id,
                "authorization_signature_approved",
                review_attestation,
            )
            return {
                "ok": True,
                "case_id": canonical_case_id,
                "candidate_document_id": body.candidate_document_id,
                "authorization_evidence_status": "verified",
                "signed_authority_verified": True,
            }

        if body.reason_code is None:
            raise HTTPException(
                status_code=422,
                detail="El rechazo exige un motivo estructurado",
            )
        rejection_attestation = build_rejected_authorization_signature_attestation(
            case_id=canonical_case_id,
            authority_payload=chain["authority"],
            candidate_payload=candidate,
            reviewer_actor=reviewer_actor,
            reviewed_at=reviewed_at,
            reason_code=body.reason_code,
        )
        updated = conn.execute(
            text(
                """
                UPDATE documents
                SET kind='authorization_signed_rejected'
                WHERE case_id=:case_id
                  AND id=CAST(:document_id AS UUID)
                  AND kind='authorization_signed_candidate'
                RETURNING id
                """
            ),
            {
                "case_id": canonical_case_id,
                "document_id": body.candidate_document_id,
            },
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="El candidato ya fue revisado")
        _append_event(
            conn,
            canonical_case_id,
            "authorization_signature_rejected",
            rejection_attestation,
        )
        return {
            "ok": True,
            "case_id": canonical_case_id,
            "candidate_document_id": body.candidate_document_id,
            "authorization_evidence_status": "rejected",
            "signed_authority_verified": False,
        }


@router.post("/{case_id}/approve")
def approve_case(
    case_id: str,
    body: ApproveBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)
        gate = conn.execute(
            text(
                "SELECT payment_status, authorized, COALESCE(test_mode,FALSE) "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()
        if not gate or str(gate[0] or "") != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido")
        if not bool(gate[1]):
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")
        if bool(gate[2]):
            raise HTTPException(
                status_code=409,
                detail="La aprobación operativa no admite expedientes test_mode",
            )
        authority = verify_signed_case_authority(conn, case_id)
        resource = _latest_final_resource(conn, case_id)
        if not resource or not resource.get("is_final"):
            raise HTTPException(status_code=409, detail="Falta un recurso final congelado")
        if case["status"] not in {"final_ready", "ready_for_delivery"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "case_status_not_approvable"},
            )
        _set_status(conn, case_id, "ready_to_submit")
        _append_event(
            conn,
            case_id,
            "operator_approved",
            {
                "note": body.note,
                "at": _utcnow().isoformat(),
                "resource_id": resource.get("id"),
                "resource_version": resource.get("version"),
                "authority_material_sha256": authority["material_sha256"],
            },
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/manual")
def send_to_manual_review(
    case_id: str,
    body: ManualBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "manual_review")
        _append_event(
            conn,
            case_id,
            "manual_review_required",
            {"motivo": body.motivo, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/note")
def add_operator_note(
    case_id: str,
    body: NoteBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        _append_event(
            conn,
            case_id,
            "operator_note",
            {"note": body.note, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/override-family")
def override_family(
    case_id: str,
    body: OverrideFamilyBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status, "overrides": overrides}


@router.post("/{case_id}/override-family-and-regenerate")
def override_family_and_regenerate(
    case_id: str,
    body: OverrideAndRegenerateBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            motivo=body.motivo,
        )

        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )
        interesado = _load_interesado(conn, case_id)

    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=_prepare_interesado_for_generate(interesado),
            tipo=body.familia,
        )
        generate_dgt(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "resource_generation_failed"},
        ) from exc

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "generated")

        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "classifier_result": {
                    "family": body.familia,
                    "confidence": 1.0,
                },
                "familia_resuelta": body.familia,
                "tipo_infraccion": body.familia,
                "hecho_imputado": _load_ai_overrides(conn, case_id).get("hecho"),
                "arguments": {
                    "hecho": f"Recurso regenerado manualmente. Motivo: {body.motivo}",
                },
                "admissibility": {
                    "admissibility": "REGENERATED",
                },
                "phase": {
                    "recommended_action": {
                        "action": "REVIEW_AND_SUBMIT",
                    }
                },
                "source": "operator_override",
                "at": _utcnow().isoformat(),
            },
        )

        _append_event(
            conn,
            case_id,
            "resource_regenerated",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "at": _utcnow().isoformat(),
                "mode": "generate_dgt",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "familia_correcta": body.familia,
    }


@router.post("/{case_id}/rewrite-hecho-and-regenerate")
def rewrite_hecho_and_regenerate(
    case_id: str,
    body: RewriteHechoBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)

        overrides = _save_ai_overrides_in_interested_data(
            conn,
            case_id,
            familia=body.familia,
            hecho=body.hecho,
            motivo=body.motivo,
        )

        interesado = _load_interesado(conn, case_id)

        _append_event(
            conn,
            case_id,
            "operator_rewrite_hecho",
            {
                "hecho": body.hecho,
                "motivo": body.motivo,
                "familia": body.familia,
                "saved_at": overrides.get("saved_at"),
                "at": _utcnow().isoformat(),
            },
        )

    interesado = dict(interesado or {})
    interesado["manual_hecho_denunciado"] = body.hecho
    interesado["manual_hecho_motivo"] = body.motivo
    if body.familia:
        interesado["manual_family"] = body.familia

    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=_prepare_interesado_for_generate(interesado),
            tipo=body.familia,
        )
        generate_dgt(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "resource_generation_failed"},
        ) from exc

    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "generated")

        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "classifier_result": {
                    "family": body.familia or "manual_hecho",
                    "confidence": 1.0,
                },
                "familia_resuelta": body.familia or _load_ai_overrides(conn, case_id).get("familia"),
                "tipo_infraccion": body.familia or _load_ai_overrides(conn, case_id).get("familia"),
                "hecho_imputado": body.hecho,
                "arguments": {
                    "hecho": body.hecho,
                },
                "admissibility": {
                    "admissibility": "REGENERATED",
                },
                "phase": {
                    "recommended_action": {
                        "action": "REVIEW_AND_SUBMIT",
                    }
                },
                "source": "manual_hecho",
                "at": _utcnow().isoformat(),
            },
        )

        _append_event(
            conn,
            case_id,
            "resource_regenerated_from_hecho",
            {
                "hecho": body.hecho,
                "motivo": body.motivo,
                "familia": body.familia,
                "at": _utcnow().isoformat(),
                "mode": "generate_dgt",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "hecho_final": body.hecho,
        "familia_forzada": body.familia,
    }


@router.post("/{case_id}/submit")
def submit_to_dgt(
    case_id: str,
    body: SubmitDGTBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    raise HTTPException(
        status_code=410,
        detail=(
            "Ruta de presentación simulada retirada. Use una vía con justificante "
            "externo verificable: automatización autorizada o registro manual OPS."
        ),
    )
