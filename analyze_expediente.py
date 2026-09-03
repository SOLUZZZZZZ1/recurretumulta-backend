# analyze_expediente.py — subida múltiple (hasta 5) + creación de expediente
# VERSIÓN COMPLETA CORREGIDA
import json
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from database import get_engine
from b2_storage import delete_object, upload_bytes
from public_case_access import (
    issue_case_access_token,
    require_public_case_access_configured,
)
from rtm_core.upload_security import (
    SAFE_DOCUMENT_MIMES,
    UploadSecurityError,
    read_upload_limited,
    validate_document_bytes,
)
from rtm_core.runtime_capabilities import require_http_capability

router = APIRouter(tags=["analyze"])

MAX_FILES = 5
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
ANALYZE_PRIVACY_VERSION = "document-analysis-ai-v1"


def _safe_filename(name: str) -> str:
    return (name or "documento").replace("\\", "_").replace("/", "_")[:120]


def _cleanup_b2_objects(coordinates: List[tuple[str, str]]) -> None:
    """Retira objetos confirmados sin ocultar la excepción que causó el rollback."""

    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass


def _persist_expediente(
    case_id: str,
    stored_documents: List[tuple[str, str, int, bytes, Any]],
    uploaded_docs: List[Dict[str, Any]],
    ai_payload: Dict[str, Any],
) -> None:
    """Registra caso, documentos y eventos como una sola unidad SQL."""

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cases(id, status, created_at, updated_at) "
                "VALUES (CAST(:case_id AS UUID), 'uploaded', NOW(), NOW())"
            ),
            {"case_id": case_id},
        )
        for b2_bucket, b2_key, _idx, data, meta in stored_documents:
            conn.execute(
                text(
                    """INSERT INTO documents(
                           case_id, kind, b2_bucket, b2_key, sha256, mime,
                           size_bytes, created_at
                       ) VALUES (
                           CAST(:case_id AS UUID), 'original', :b2_bucket,
                           :b2_key, :sha256, :mime, :size_bytes, NOW()
                       )"""
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "sha256": meta.sha256,
                    "mime": meta.mime,
                    "size_bytes": len(data),
                },
            )
        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (
                       CAST(:case_id AS UUID), 'expediente_uploaded',
                       CAST(:payload AS JSONB), NOW()
                   )"""
            ),
            {
                "case_id": case_id,
                "payload": json.dumps({"documents": uploaded_docs}),
            },
        )
        conn.execute(
            text(
                """INSERT INTO events(case_id, type, payload, created_at)
                   VALUES (
                       CAST(:case_id AS UUID), 'ai_expediente_result',
                       CAST(:payload AS JSONB), NOW()
                   )"""
            ),
            {"case_id": case_id, "payload": json.dumps(ai_payload)},
        )


@router.post("/analyze/expediente")
async def analyze_expediente(
    files: List[UploadFile] = File(...),
    ai_processing_consent: bool = Form(False),
    privacy_version: str = Form("", max_length=80),
) -> Dict[str, Any]:
    """
    MVP multi-documento:
    - Crea un case_id
    - Sube hasta 5 archivos a B2 (folder: original)
    - Inserta documents(kind='original') para cada archivo
    - Inserta event 'expediente_uploaded' con lista de documentos
    - Inserta event 'ai_expediente_result' para que el panel tenga datos visibles
    """
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FILES} documentos por expediente.")

    if not ai_processing_consent or privacy_version != ANALYZE_PRIVACY_VERSION:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ai_processing_consent_required",
                "required_privacy_version": ANALYZE_PRIVACY_VERSION,
            },
        )

    require_public_case_access_configured()
    require_http_capability("b2")

    prepared = []
    total_bytes = 0
    try:
        for idx, upload in enumerate(files, start=1):
            data = await read_upload_limited(upload, max_bytes=MAX_FILE_BYTES)
            meta = await run_in_threadpool(
                validate_document_bytes,
                filename=upload.filename or f"documento_{idx}.pdf",
                declared_mime=upload.content_type,
                data=data,
                max_bytes=MAX_FILE_BYTES,
                allowed_mimes=SAFE_DOCUMENT_MIMES,
            )
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise UploadSecurityError(
                    "El conjunto documental supera el límite permitido",
                    status_code=413,
                )
            prepared.append((idx, data, meta))
    except UploadSecurityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    case_id = str(uuid.uuid4())
    case_access_token = issue_case_access_token(case_id)
    stored_documents: List[tuple[str, str, int, bytes, Any]] = []
    stored_coordinates: List[tuple[str, str]] = []
    try:
        for idx, data, meta in prepared:
            b2_bucket, b2_key = await run_in_threadpool(
                upload_bytes,
                case_id,
                "original",
                data,
                meta.extension,
                meta.mime,
            )
            stored_coordinates.append((b2_bucket, b2_key))
            stored_documents.append((b2_bucket, b2_key, idx, data, meta))
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise HTTPException(
            status_code=502,
            detail="No se pudo custodiar el conjunto documental",
        ) from exc

    uploaded_docs = [
        {
            "idx": idx,
            "filename": meta.filename,
            "sha256": meta.sha256,
            "mime": meta.mime,
            "size_bytes": len(data),
        }
        for _bucket, _key, idx, data, meta in stored_documents
    ]
    ai_payload = {
        "familia": "pendiente_clasificacion",
        "confianza": 0.0,
        "hecho": "",
        "admisibilidad": "",
        "accion": "",
    }

    try:
        # El caso no es visible hasta que todos los documentos y eventos quedan
        # registrados en la misma transacción.
        await run_in_threadpool(
            _persist_expediente,
            case_id,
            stored_documents,
            uploaded_docs,
            ai_payload,
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
        "documents": uploaded_docs,
        "ai_result_seeded": ai_payload,
        "message": "Expediente creado. Ya puedes continuar al resumen.",
    }
