"""Individual OPS access to existing working-document custody and local revisions."""
from typing import Optional
import json
from fastapi import APIRouter, Header, Request, Response, HTTPException, File, Form, UploadFile
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from database import get_engine
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import require_operator_token
from rtm_core.staging_rehearsal import require_supervisor
from rtm_core import working_document_versions as versions
from rtm_core.local_operator_auth import local_operator_auth_requested
from rtm_core.case_working_document import local_grant
from rtm_core.upload_security import read_upload_limited, validate_draft_correction, MAX_DRAFT_TEXT_BYTES, UploadSecurityError

router = APIRouter()
PATH = "/{case_id}/study/working-document/versions"
HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


def _scope(request, token):
    require_operator_token(token)
    if local_operator_auth_requested():
        scope = load_ops_case_scope(request)
        return scope, local_grant(scope)
    grant = require_supervisor(request)
    return load_ops_case_scope(request), grant


@router.get(PATH)
def get_versions(case_id: str, request: Request, response: Response,
                 x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return versions.list_versions(conn, case_id=case_id, grant=grant)


@router.post(PATH)
def post_version(case_id: str, body: versions.SaveWorkingDocumentBody | versions.SaveCaseDocumentBody, request: Request, response: Response,
                 x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    engine, uploaded = get_engine(), []
    try:
        with engine.begin() as conn:
            case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
            return versions.save_version(conn, case_id=case_id, grant=grant, body=body, uploaded=uploaded)
    except Exception:
        # Import lazily to reuse the existing lost-COMMIT-safe compensation.
        from rtm_core.study_router import _cleanup_uncommitted_documents
        _cleanup_uncommitted_documents(engine, uploaded)
        raise


@router.post(PATH + "/selection")
def post_selection(case_id: str, body: versions.SelectCaseVersionBody, request: Request, response: Response,
                   x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return versions.select_version(conn, case_id=case_id, grant=grant, body=body)


@router.post(PATH + "/correction")
async def post_correction(case_id: str, request: Request, response: Response,
        metadata: str = Form(max_length=10000), file: UploadFile = File(),
        x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    if not versions._local(grant):
        raise HTTPException(404, "Corrección no disponible en este flujo")
    response.headers.update(HEADERS)
    try:
        meta = json.loads(metadata)
        if not isinstance(meta, dict) or "content" in meta or not versions._valid_uuid(meta.get("based_on_id")):
            raise ValueError("Metadatos inválidos")
        data = await read_upload_limited(file, max_bytes=MAX_DRAFT_TEXT_BYTES)
        content, validated = validate_draft_correction(data, filename=file.filename or "",
            declared_mime=file.content_type or "", case_id=case_id, based_on_id=meta["based_on_id"])
        body = versions.SaveCaseDocumentBody.model_validate({**meta, "content": content})
    except UploadSecurityError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Los metadatos de la corrección no son válidos") from exc
    finally:
        await file.close()
    def commit():
        engine, uploaded = get_engine(), []
        try:
            with engine.begin() as conn:
                scoped_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
                return versions.save_case_version(conn, case_id=scoped_id, grant=grant, body=body,
                                                  uploaded=uploaded, correction=(data, validated))
        except Exception:
            from rtm_core.study_router import _cleanup_uncommitted_documents
            _cleanup_uncommitted_documents(engine, uploaded)
            raise
    return await run_in_threadpool(commit)


@router.get(PATH + "/{version_id}")
def get_version(case_id: str, version_id: str, request: Request, response: Response,
                x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return versions.read_version(conn, case_id=case_id, version_id=version_id, grant=grant)


@router.get(PATH + "/{version_id}/pdf")
def get_version_pdf(case_id: str, version_id: str, request: Request,
                    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        content, entry = versions.read_pdf(conn, case_id=case_id, version_id=version_id, grant=grant)
    return Response(content, media_type="application/pdf", headers={**HEADERS,
        "Content-Disposition": 'inline; filename="rtm-borrador-guardado.pdf"',
        "X-RTM-Version-ID": entry["id"], "X-RTM-Source-SHA256": entry["source_sha256"],
        "X-RTM-Document-SHA256": entry["pdf"]["sha256"],
        "Access-Control-Expose-Headers": "X-RTM-Version-ID, X-RTM-Source-SHA256, X-RTM-Document-SHA256"})
