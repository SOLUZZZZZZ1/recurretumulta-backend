"""Client-scoped adapters; normal business handlers and immutable synthetic input."""
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
import cases
from public_case_access import issue_case_access_token, require_case_access_token
from rtm_core.intake_router import append_documents_core
from rtm_core.staging_rehearsal import existing_case, require_supervisor, PRIVATE_HEADERS
from rtm_core.staging_rehearsal_router import exact_fields, exact_upload, current_candidate
from rtm_core.staging_client import PREFIX, VERSION, configured_grant, require_client_pass, fixture

def private_client(response: Response):
    response.headers.update({**PRIVATE_HEADERS, "Vary": "X-RTM-Client-Pass, X-RTM-Case-Token, Authorization, Cookie", "Referrer-Policy": "no-referrer"})


router = APIRouter(prefix=PREFIX, tags=["staging-client"], dependencies=[Depends(private_client)])
ops_router = APIRouter(prefix="/ops/rehearsal/client", tags=["staging-client-access"], dependencies=[Depends(private_client)])


@ops_router.post("/access")
async def client_access(request: Request):
    supervisor = require_supervisor(request)
    grant = configured_grant()
    if supervisor.operator_id != grant.operator_id:
        raise HTTPException(403, "Supervisor fuera del ensayo configurado")
    if await request.body():
        raise HTTPException(422, "El pase utiliza solo el ámbito configurado")
    return {"ok": True, "version": VERSION, "case_id": grant.case_id,
            "client_pass": issue_case_access_token(grant.case_id), "synthetic_only": True}


@router.get("/profile")
async def profile(request: Request):
    grant = require_client_pass(request)
    artifacts = {}
    for kind in ("identity_front", "identity_back", "original"):
        name, content = await run_in_threadpool(fixture, kind)
        artifacts[kind] = {"filename": name, "sha256": hashlib.sha256(content).hexdigest()}
    exists = await run_in_threadpool(existing_case, grant)
    return {"ok": True, "version": VERSION, "case_id": grant.case_id,
            "existing": exists, "fields": grant.form_fields, "fixtures": artifacts,
            "synthetic_only": True, "payment_mode": "test"}


async def require_case(request: Request):
    grant = require_client_pass(request)
    if request.path_params.get("case_id") != grant.case_id:
        raise HTTPException(404, "Expediente fuera de este ensayo")
    require_case_access_token(grant.case_id, request.headers.get("X-RTM-Case-Token"))
    if not await run_in_threadpool(existing_case, grant):
        raise HTTPException(404, "Expediente no creado todavía")
    request.state.rtm_rehearsal_grant = grant
    return grant


async def prepare_intake(request: Request):
    grant = require_client_pass(request)
    form = await request.form()
    expected = grant.form_fields
    exact_fields(form, set(expected) | {"dni_front", "dni_back"})
    if any(form[key] not in {value, value.replace("\n", "\r\n")} for key, value in expected.items()):
        raise HTTPException(422, "Usa los datos del ensayo y confirma personalmente las casillas")
    for field, kind in (("dni_front", "identity_front"), ("dni_back", "identity_back")):
        name, content = await run_in_threadpool(fixture, kind)
        await exact_upload(form[field], content, name)
    request.state.rtm_rehearsal_grant = grant


async def prepare_append(request: Request):
    await require_case(request)
    form = await request.form()
    exact_fields(form, {"files"})
    name, content = await run_in_threadpool(fixture, "original")
    await exact_upload(form["files"], content, name)


async def prepare_candidate(request: Request):
    await require_case(request)
    form = await request.form()
    exact_fields(form, {"file", "authority_material_sha256", "generated_document_id",
        "generated_document_sha256", "generated_document_version", "document_nonce", "issuance_attestation_sha256"})
    content = await current_candidate(request.path_params["case_id"], request)
    await exact_upload(form["file"], content, "AUTORIZACION_FICTICIA_CANDIDATO.pdf")


@router.get("/fixtures/{kind}")
async def download_fixture(kind: str, request: Request):
    require_client_pass(request)
    name, content = await run_in_threadpool(fixture, kind)
    return Response(content, media_type="application/pdf", headers={**PRIVATE_HEADERS,
        "Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/cases/{case_id}/candidate-fixture")
async def download_candidate(case_id: str, request: Request):
    await require_case(request)
    return Response(await current_candidate(case_id, request), media_type="application/pdf", headers={**PRIVATE_HEADERS,
        "Content-Disposition": 'attachment; filename="AUTORIZACION_FICTICIA_CANDIDATO.pdf"'})


router.add_api_route("/intake-draft", cases.create_rtm_intake_draft, methods=["POST"], dependencies=[Depends(prepare_intake)])
router.add_api_route("/cases/{case_id}/authorize", cases.authorize_case, methods=["POST"], dependencies=[Depends(require_case)])
router.add_api_route("/cases/{case_id}/authorization-pdf", cases.download_authorization_pdf, methods=["GET"], dependencies=[Depends(require_case)])
router.add_api_route("/cases/{case_id}/upload-authorization-signed", cases.upload_authorization_signed, methods=["POST"], dependencies=[Depends(prepare_candidate)])
router.add_api_route("/cases/{case_id}/append-documents", append_documents_core, methods=["POST"], dependencies=[Depends(prepare_append)])
