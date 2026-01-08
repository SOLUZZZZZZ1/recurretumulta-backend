import hashlib
import mimetypes
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import text

from database import get_engine
from b2_storage import upload_original

router = APIRouter(tags=["analyze"])

def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()

@router.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
    """STEP B:
    - Sube el archivo original a Backblaze B2
    - Guarda case + document (con b2_bucket/b2_key) + extraction(mock) + events en Postgres
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 12MB).")

        sha256 = _sha256_bytes(content)
        mime = file.content_type or (mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream")
        size_bytes = len(content)

        engine = get_engine()

        with engine.begin() as conn:
            case_id = conn.execute(
                text("INSERT INTO cases(status, created_at, updated_at) VALUES ('uploaded', NOW(), NOW()) RETURNING id")
            ).scalar()

            b2_bucket, b2_key = upload_original(str(case_id), content, file.filename, mime)

            conn.execute(
                text(
                    """INSERT INTO documents(case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at)
                         VALUES (:case_id, 'original', :b2_bucket, :b2_key, :sha256, :mime, :size_bytes, NOW())"""
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

            extracted = {
                "filename": file.filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage": {"bucket": b2_bucket, "key": b2_key},
                "note": "Extracción mock (Step B). En Step C se reemplaza por GPT-4o visión.",
                "detected": {
                    "organismo": None,
                    "expediente": None,
                    "importe": None,
                    "fecha_notificacion": None,
                },
            }

            conn.execute(
                text(
                    """INSERT INTO extractions(case_id, extracted_json, confidence, model, created_at)
                         VALUES (:case_id, CAST(:json AS JSONB), :confidence, :model, NOW())"""
                ),
                {"case_id": case_id, "json": __import__("json").dumps(extracted), "confidence": 0.1, "model": "mock"},
            )

            conn.execute(
                text(
                    """INSERT INTO events(case_id, type, payload, created_at)
                         VALUES (:case_id, 'upload_ok', CAST(:payload AS JSONB), NOW())"""
                ),
                {
                    "case_id": case_id,
                    "payload": __import__("json").dumps(
                        {"sha256": sha256, "mime": mime, "size_bytes": size_bytes, "b2_bucket": b2_bucket, "b2_key": b2_key}
                    ),
                },
            )
            conn.execute(
                text(
                    """INSERT INTO events(case_id, type, payload, created_at)
                         VALUES (:case_id, 'analyze_ok', CAST(:payload AS JSONB), NOW())"""
                ),
                {"case_id": case_id, "payload": __import__("json").dumps({"model": "mock", "confidence": 0.1})},
            )

            conn.execute(text("UPDATE cases SET status='analyzed', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

        return {
            "ok": True,
            "message": "Archivo guardado en Backblaze B2 y expediente creado en Postgres.",
            "case_id": str(case_id),
            "extracted": extracted,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /analyze: {e}")
