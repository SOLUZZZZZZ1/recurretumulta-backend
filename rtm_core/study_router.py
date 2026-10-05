"""Individual, scoped OPS continuation; no actor supplied by the browser."""
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response, File, Form, UploadFile

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from rtm_core.parking_check_review import CheckReviewBody, save_review
from rtm_core.study_documents import append_document, revision_available, MAX_DOCUMENT_BYTES
from rtm_core.upload_security import read_upload_limited, validate_document_bytes, UploadSecurityError
from database import get_engine
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import require_operator_token
from rtm_core.study import StudyActionBody, advance_study, load_study

router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-study"])


@router.get("/{case_id}/study")
def get_case_study(case_id: str, request: Request, response: Response,
                   x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    require_operator_token(x_operator_token)
    scope = load_ops_case_scope(request)
    response.headers["Cache-Control"] = "no-store"
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        result, *_ = load_study(conn, case_id)
    return result


@router.post("/{case_id}/study/actions")
def post_case_study(case_id: str, body: StudyActionBody, request: Request, response: Response,
                    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    require_operator_token(x_operator_token)
    scope = load_ops_case_scope(request)
    if not scope.individual_session or not scope.scope_all or not scope.operator_id:
        raise HTTPException(403, "Se requiere una sesión individual de supervisor.")
    response.headers["Cache-Control"] = "no-store"
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return advance_study(conn, case_id=case_id, body=body, actor=f"operator:{scope.operator_id}")


class StudyDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    expected_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=2000)
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Confirma la incorporación y la apertura de nueva revisión")
        return value


def _supervisor_scope(request, token):
    require_operator_token(token)
    scope = load_ops_case_scope(request)
    if not scope.individual_session or not scope.scope_all or not scope.operator_id:
        raise HTTPException(403, "Se requiere una sesión individual de supervisor.")
    return scope


@router.post("/{case_id}/study/check-reviews")
def post_parking_check_review(case_id: str, body: CheckReviewBody, request: Request, response: Response,
                             x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope = _supervisor_scope(request, x_operator_token)
    response.headers["Cache-Control"] = "no-store"
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        before, facts, family, previous = load_study(conn, case_id, for_update=True)
        if not hmac.compare_digest(before["state_sha256"], body.expected_state_sha256):
            raise HTTPException(409, "El estudio ha cambiado. Recarga antes de revisar.")
        actor = f"operator:{scope.operator_id}"
        save_review(conn, case_id=case_id, body=body, actor=actor, before=before,
                    facts=facts, family=family, previous=previous, documents=before["documents"])
        after, *_ = load_study(conn, case_id)
    return {**after, "completed_action": "review_check", "previous_state_sha256": before["state_sha256"]}


def _cleanup_uncommitted_documents(engine, uploaded):
    from b2_storage import delete_object
    # A lost COMMIT reply is ambiguous: retain any referenced object, and also
    # retain it if the database cannot confirm that it is unreferenced.
    for bucket, key in uploaded:
        try:
            with engine.connect() as conn:
                referenced = conn.execute(text(
                    "SELECT 1 FROM documents WHERE b2_bucket=:bucket AND b2_key=:key LIMIT 1"
                ), {"bucket": bucket, "key": key}).fetchone()
            if referenced is None:
                delete_object(bucket, key)
        except Exception:
            logging.getLogger(__name__).warning("No se pudo confirmar la compensación documental")


def _append_study_document(case_id, scope, body, data, validated):
    engine = get_engine()
    uploaded = []
    try:
        with engine.begin() as conn:
            case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
            return append_document(conn, case_id=case_id, body=body, actor=f"operator:{scope.operator_id}",
                                   data=data, validated=validated, uploaded=uploaded)
    except Exception:
        _cleanup_uncommitted_documents(engine, uploaded)
        raise


@router.post("/{case_id}/study/documents")
async def post_study_document(case_id: str, request: Request, response: Response,
                              metadata: str = Form(..., max_length=12000),
                              file: UploadFile = File(...),
                              x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope = _supervisor_scope(request, x_operator_token)
    response.headers["Cache-Control"] = "no-store"
    try:
        body = StudyDocumentBody.model_validate_json(metadata)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Revisa la confirmación, el motivo y la versión del documento") from exc
    # Authenticate, scope and snapshot checks precede file validation/storage.
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        before, facts, _, previous = load_study(conn, case_id)
        if (not hmac.compare_digest(before["state_sha256"], body.expected_state_sha256)
                or not revision_available(before, facts, previous)):
            raise HTTPException(409, "Recarga el estudio antes de incorporar documentación")
    from b2_storage import local_document_storage_enabled
    if local_document_storage_enabled():
        from rtm_core.local_document_storage import assert_local_document_storage_ready
        assert_local_document_storage_ready()
    else:
        from rtm_core.runtime_capabilities import require_http_capability
        require_http_capability("b2")
    try:
        data = await read_upload_limited(file, max_bytes=MAX_DOCUMENT_BYTES)
        validated = await run_in_threadpool(validate_document_bytes, filename=file.filename,
            declared_mime=file.content_type, data=data, max_bytes=MAX_DOCUMENT_BYTES,
            allowed_mimes=("application/pdf",))
    except UploadSecurityError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    finally:
        await file.close()
    return await run_in_threadpool(_append_study_document, case_id, scope, body, data, validated)
