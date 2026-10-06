"""Authenticated adapters to ordinary intake; only bundled fixture bytes enter."""
from __future__ import annotations

import hashlib
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

import cases
from public_case_access import require_case_access_token
from rtm_core.intake_router import append_documents_core
from rtm_core.staging_rehearsal import (
    FORM_FIELDS, PRIVATE_HEADERS, PROFILE, VERSION, existing_case, fixture, require_supervisor,
)

def no_store(response: Response):
    response.headers.update(PRIVATE_HEADERS)


router = APIRouter(prefix="/ops/rehearsal/radar", tags=["staging-rehearsal"], dependencies=[Depends(no_store)])


async def require_case(request: Request):
    grant = require_supervisor(request)
    case_id = request.path_params.get("case_id")
    if case_id != grant.case_id:
        raise HTTPException(404, "Expediente de ensayo no encontrado")
    require_case_access_token(case_id, request.headers.get("X-RTM-Case-Token"))
    if not await run_in_threadpool(existing_case, grant):
        raise HTTPException(404, "Expediente de ensayo no encontrado")
    request.state.rtm_rehearsal_grant = grant
    return grant


def exact_fields(form, expected: set[str]):
    if set(form) != expected or any(len(form.getlist(key)) != 1 for key in expected):
        raise HTTPException(422, "La entrada no coincide con los campos del ensayo")


async def exact_upload(upload, expected: bytes, filename: str | None = None):
    if not isinstance(upload, UploadFile) or upload.content_type != "application/pdf":
        raise HTTPException(422, "Selecciona el PDF ficticio preparado para este paso")
    content = await upload.read(len(expected) + 1)
    await upload.seek(0)
    if content != expected:
        raise HTTPException(422, "Solo se admite el archivo ficticio exacto de este ensayo")
    if filename is not None:
        upload.filename = filename


async def prepare_intake(request: Request):
    grant = require_supervisor(request)
    form = await request.form()
    exact_fields(form, set(FORM_FIELDS) | {"dni_front", "dni_back"})
    if any(form[key] != value for key, value in FORM_FIELDS.items()):
        raise HTTPException(422, "Utiliza los datos ficticios preparados y confirma personalmente las casillas del ensayo")
    for field, kind in (("dni_front", "identity_front"), ("dni_back", "identity_back")):
        filename, content = await run_in_threadpool(fixture, kind)
        await exact_upload(form[field], content, filename)
    request.state.rtm_rehearsal_grant = grant


async def prepare_append(request: Request):
    await require_case(request)
    form = await request.form()
    exact_fields(form, {"files"})
    filename, content = await run_in_threadpool(fixture, "radar")
    await exact_upload(form["files"], content, filename)


def candidate_pdf(original: bytes) -> bytes:
    """Deterministic test annotation, visibly not a signature or legal power."""
    reader = PdfReader(io.BytesIO(original))
    if not 1 <= len(reader.pages) <= 5:
        raise HTTPException(409, "PDF de ensayo no verificable")
    writer = PdfWriter()
    for source_page in reader.pages:
        page = writer.add_page(source_page)
        mark = io.BytesIO()
        canvas = Canvas(mark, pagesize=(float(page.mediabox.width), float(page.mediabox.height)), invariant=1)
        canvas.setFillColorRGB(0.65, 0.08, 0.08)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(35, 35, "ENSAYO RTM - SIN VALIDEZ - FIRMA FICTICIA RTM TEST")
        canvas.save()
        page.merge_page(PdfReader(io.BytesIO(mark.getvalue())).pages[0])
    writer.add_metadata({"/Title": "Candidato ficticio RTM - sin validez", "/Author": "RTM TEST"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


async def current_candidate(case_id: str, request: Request) -> bytes:
    response = await cases.download_authorization_pdf(
        case_id, request, request.headers.get("X-RTM-Case-Token"))
    return await run_in_threadpool(candidate_pdf, response.body)


async def prepare_candidate(request: Request):
    await require_case(request)
    form = await request.form()
    exact_fields(form, {"file", "authority_material_sha256", "generated_document_id",
        "generated_document_sha256", "generated_document_version", "document_nonce",
        "issuance_attestation_sha256"})
    content = await current_candidate(request.path_params["case_id"], request)
    await exact_upload(form["file"], content, "AUTORIZACION_FICTICIA_CANDIDATO.pdf")


@router.get("/profile")
async def profile(request: Request):
    grant = require_supervisor(request)
    artifacts = {}
    for kind in ("identity_front", "identity_back", "radar"):
        name, content = await run_in_threadpool(fixture, kind)
        artifacts[kind] = {"filename": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    return {"ok": True, "version": VERSION, "synthetic_only": True, "profile": PROFILE,
            "fixtures": artifacts, "existing_case_id": grant.case_id if await run_in_threadpool(existing_case, grant) else None}


@router.get("/fixtures/{kind}")
async def download_fixture(kind: str, request: Request):
    require_supervisor(request)
    name, content = await run_in_threadpool(fixture, kind)
    return Response(content, media_type="application/pdf", headers={**PRIVATE_HEADERS,
        "Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/cases/{case_id}/candidate-fixture")
async def download_candidate(case_id: str, request: Request):
    await require_case(request)
    content = await current_candidate(case_id, request)
    return Response(content, media_type="application/pdf", headers={**PRIVATE_HEADERS,
        "Content-Disposition": 'attachment; filename="AUTORIZACION_FICTICIA_CANDIDATO.pdf"'})


router.add_api_route("/intake-draft", cases.create_rtm_intake_draft,
                     methods=["POST"], dependencies=[Depends(prepare_intake)])
router.add_api_route("/cases/{case_id}/authorize", cases.authorize_case,
                     methods=["POST"], dependencies=[Depends(require_case)])
router.add_api_route("/cases/{case_id}/authorization-pdf", cases.download_authorization_pdf,
                     methods=["GET"], dependencies=[Depends(require_case)])
router.add_api_route("/cases/{case_id}/upload-authorization-signed", cases.upload_authorization_signed,
                     methods=["POST"], dependencies=[Depends(prepare_candidate)])
router.add_api_route("/cases/{case_id}/append-documents", append_documents_core,
                     methods=["POST"], dependencies=[Depends(prepare_append)])
