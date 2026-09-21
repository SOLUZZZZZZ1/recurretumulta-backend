"""Explicit local generic-document endpoints, separate from verified DGT power."""
from typing import Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from public_case_access import require_case_access_token
from rtm_core.generic_authorization import (
    MAX_PDF_BYTES, PRIVATE_HEADERS, issue_generic_authorization,
    read_generic_authorization, require_local_generic_profile, store_generic_candidate,
)
from rtm_core.upload_security import UploadSecurityError, read_upload_limited, validate_document_bytes

router = APIRouter(prefix="/cases", tags=["rtm-local-generic-authorization"])
ops_router = APIRouter(prefix="/ops/core/cases", tags=["rtm-local-recovery"])


@ops_router.post("/{case_id}/recover-local-access")
def recover_local_access(case_id: str, request: Request):
    from database import get_engine
    from public_case_access import issue_case_access_token
    from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
    from rtm_core.generic_authorization import load_snapshot, _append_event
    from scripts.rtm_local_operator_setup import require_local_database

    require_local_generic_profile()
    scope = load_ops_case_scope(request)
    if not (scope.individual_session and scope.role_code == "rtm.supervisor"
            and "ops.supervise" in scope.permissions):
        raise HTTPException(403, "Se requiere una sesión individual de supervisor")
    with get_engine().begin() as conn:
        require_local_database(conn)
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        load_snapshot(conn, case_id, mutate=True)
        token = issue_case_access_token(case_id)
        _append_event(conn, case_id, "local_case_access_recovered", {
            "operator_id": scope.operator_id, "local_only": True,
            "purpose": "resume_synthetic_intake",
        })
    return JSONResponse({"ok": True, "case_id": case_id,
                         "local_only": True, "case_access_token": token},
                        headers=PRIVATE_HEADERS)


class GenericConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]


def authorized_local_case(case_id, token):
    require_local_generic_profile()
    return require_case_access_token(case_id, token)


@router.post("/{case_id}/rtm-authorization")
async def issue(case_id: str, consent: GenericConsent,
                x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token")):
    case_id = authorized_local_case(case_id, x_case_token)
    return await run_in_threadpool(issue_generic_authorization, case_id)


def download_generic_authorization(case_id: str, x_case_token: str | None = None):
    """Called only by the existing PDF route's explicit local branch."""
    case_id = authorized_local_case(case_id, x_case_token)
    data = read_generic_authorization(case_id)
    return Response(content=data, media_type="application/pdf", headers={
        **PRIVATE_HEADERS, "Content-Disposition": f'attachment; filename="autorizacion_RTM_LOCAL_{case_id}.pdf"',
    })


@router.post("/{case_id}/rtm-authorization-signed")
async def candidate(
    case_id: str,
    file: UploadFile = File(...),
    generated_document_id: str = Form(..., max_length=36),
    generated_document_sha256: str = Form(..., max_length=64),
    generated_document_version: str = Form(..., max_length=64),
    document_nonce: str = Form(..., max_length=36),
    issuance_attestation_sha256: str = Form(..., max_length=64),
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = authorized_local_case(case_id, x_case_token)
    try:
        data = await read_upload_limited(file, max_bytes=MAX_PDF_BYTES)
        await run_in_threadpool(
            validate_document_bytes, filename=file.filename, declared_mime=file.content_type,
            data=data, max_bytes=MAX_PDF_BYTES, allowed_mimes={"application/pdf"},
        )
    except UploadSecurityError as exc:
        raise HTTPException(exc.status_code, "El candidato no ha superado la validación de PDF") from exc
    binding = dict(generated_document_id=generated_document_id, generated_document_sha256=generated_document_sha256,
                   generated_document_version=generated_document_version, document_nonce=document_nonce,
                   issuance_attestation_sha256=issuance_attestation_sha256)
    return await run_in_threadpool(store_generic_candidate, case_id, data, binding)
