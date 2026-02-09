import hashlib
import mimetypes
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_original
from openai_vision import extract_from_image_bytes
from text_extractors import (
    extract_text_from_pdf_bytes,
    extract_text_from_docx_bytes,
    has_enough_text,
)
from openai_text import extract_from_text

router = APIRouter(tags=["analyze"])

DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 12MB).")

        sha256 = _sha256_bytes(content)
        mime = file.content_type or (
            mimetypes.guess_type(file.filename or "")[0]
            or "application/octet-stream"
        )
        size_bytes = len(content)

        engine = get_engine()

        with engine.begin() as conn:
            # 1) Crear expediente
            case_id = conn.execute(
                text(
                    "INSERT INTO cases(status, created_at, updated_at) "
                    "VALUES ('uploaded', NOW(), NOW()) RETURNING id"
                )
            ).scalar()

            # 2) Subir original a Backblaze B2
            b2_bucket, b2_key = upload_original(
                str(case_id), content, file.filename, mime
            )

            # 3) Registrar documento
            conn.execute(
                text(
                    """INSERT INTO documents
                       (case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at)
                       VALUES
                       (:case_id, 'original', :b2_bucket, :b2_key, :sha256, :mime, :size_bytes, NOW())"""
                ),
                {
                    "case_id": case_id,
                    "b2_bucket": b2_bucket,
                    "b2_key": b2_key,
                    "sha256": sha256,
                    "mime": mime,
                    "size_bytes": size_bytes,
                },
            )

            # 4) Extracción inteligente
            model_used = "mock"
            confidence = 0.1

            extracted_core: Dict[str, Any]

            if mime.startswith("image/"):
                extracted_core = extract_from_image_bytes(
                    content, mime, file.filename
                )
                model_used = "openai_vision"
                confidence = 0.7

            elif mime == "application/pdf":
                text_content = extract_text_from_pdf_bytes(content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = extract_from_image_bytes(
                        content, mime, file.filename
                    )
                    model_used = "openai_vision"
                    confidence = 0.6

            elif mime in DOCX_MIMES:
                text_content = extract_text_from_docx_bytes(content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = {
                        "organismo": None,
                        "expediente_ref": None,
                        "importe": None,
                        "fecha_notificacion": None,
                        "fecha_documento": None,
                        "tipo_sancion": None,
                        "pone_fin_via_administrativa": None,
                        "plazo_recurso_sugerido": None,
                        "observaciones": "DOCX sin texto suficiente.",
                    }

            else:
                extracted_core = {
                    "organismo": None,
                    "expediente_ref": None,
                    "importe": None,
                    "fecha_notificacion": None,
                    "fecha_documento": None,
                    "tipo_sancion": None,
                    "pone_fin_via_administrativa": None,
                    "plazo_recurso_sugerido": None,
                    "observaciones": "Tipo de archivo no soportado.",
                }

            wrapper = {
                "filename": file.filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage": {"bucket": b2_bucket, "key": b2_key},
                "extracted": extracted_core,
            }

            # 5) Guardar extracción
            conn.execute(
                text(
                    """INSERT INTO extractions
                       (case_id, extracted_json, confidence, model, created_at)
                       VALUES
                       (:case_id, CAST(:json AS JSONB), :confidence, :model, NOW())"""
                ),
                {
                    "case_id": case_id,
                    "json": __import__("json").dumps(wrapper),
                    "confidence": confidence,
                    "model": model_used,
                },
            )

            # 6) Eventos + estado
            conn.execute(
                text(
                    """INSERT INTO events(case_id, type, payload, created_at)
                       VALUES (:case_id, 'analyze_ok', CAST(:payload AS JSONB), NOW())"""
                ),
                {
                    "case_id": case_id,
                    "payload": __import__("json").dumps(
                        {"model": model_used, "confidence": confidence}
                    ),
                },
            )

            conn.execute(
                text(
                    "UPDATE cases SET status='analyzed', updated_at=NOW() WHERE id=:case_id"
                ),
                {"case_id": case_id},
            )

        return {
            "ok": True,
            "message": "Análisis completo generado.",
            "case_id": str(case_id),
            "extracted": wrapper,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /analyze: {e}")
