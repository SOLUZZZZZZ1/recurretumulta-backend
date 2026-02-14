import json
import hashlib
import mimetypes
import re
from typing import Any, Dict, List, Tuple

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


def _flatten_text(extracted_core: Dict[str, Any], text_content: str = "") -> str:
    parts: List[str] = []
    if isinstance(extracted_core, dict):
        for k, v in extracted_core.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                parts.append(f"{k}: {v}")
            else:
                try:
                    parts.append(f"{k}: {str(v)}")
                except Exception:
                    pass
    if text_content:
        parts.append(text_content)
    return "\n".join(parts)


def _detect_facts_and_type(text_blob: str) -> Tuple[str, str, List[str]]:
    t = (text_blob or "").lower()
    facts: List[str] = []

    # SEMÁFORO
    sema_patterns = [
        r"circular\s+con\s+luz\s+roja",
        r"luz\s+roja",
        r"sem[aá]foro\s+en\s+rojo",
        r"no\s+respetar\s+.*sem[aá]foro",
    ]
    for p in sema_patterns:
        if re.search(p, t):
            facts.append("CIRCULAR CON LUZ ROJA")
            return ("semaforo", facts[0], facts)

    # VELOCIDAD
    m = re.search(r"\b(\d{2,3})\s*km\s*/?\s*h\b", t)
    if m:
        vel = m.group(1)
        facts.append(f"EXCESO DE VELOCIDAD ({vel} km/h)")
        return ("velocidad", facts[0], facts)
    if "exceso de velocidad" in t or "radar" in t or "cinemómetro" in t or "cinemometro" in t:
        facts.append("EXCESO DE VELOCIDAD")
        return ("velocidad", facts[0], facts)

    # MÓVIL
    if "utilizando manualmente" in t and ("teléfono" in t or "telefono" in t or "móvil" in t or "movil" in t):
        facts.append("USO MANUAL DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)
    if "teléfono móvil" in t or "telefono movil" in t or "uso del teléfono" in t or "uso del telefono" in t:
        facts.append("USO DEL TELÉFONO MÓVIL")
        return ("movil", facts[0], facts)

    # ATENCIÓN / DISTRACCIÓN
    if "atención permanente" in t or "atencion permanente" in t or "distracción" in t or "distraccion" in t:
        facts.append("NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN")
        return ("atencion", facts[0], facts)

    # PARKING básico
    if "doble fila" in t:
        facts.append("ESTACIONAR EN DOBLE FILA")
        return ("parking", facts[0], facts)
    if "minusválid" in t or "minusvalid" in t or "pmr" in t:
        facts.append("ESTACIONAR EN PLAZA RESERVADA (PMR)")
        return ("parking", facts[0], facts)
    if "zona azul" in t or "ora" in t or "ticket" in t:
        facts.append("ESTACIONAMIENTO REGULADO (ZONA ORA)")
        return ("parking", facts[0], facts)

    return ("otro", "", [])


def _enrich_with_triage(extracted_core: Dict[str, Any], text_blob: str) -> Dict[str, Any]:
    tipo, hecho, facts = _detect_facts_and_type(text_blob)
    out = dict(extracted_core or {})
    out["tipo_infraccion"] = tipo
    out["hecho_imputado"] = hecho or None
    out["facts_phrases"] = facts
    return out


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> Dict[str, Any]:
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
                text("INSERT INTO documents (case_id, kind, b2_bucket, b2_key, sha256, mime, size_bytes, created_at) VALUES (:case_id, 'original', :b2_bucket, :b2_key, :sha256, :mime, :size_bytes, NOW())"),
                {"case_id": case_id, "b2_bucket": b2_bucket, "b2_key": b2_key, "sha256": sha256, "mime": mime, "size_bytes": size_bytes},
            )

            model_used = "mock"
            confidence = 0.1
            extracted_core: Dict[str, Any] = {}
            text_content = ""

            if mime.startswith("image/"):
                extracted_core = extract_from_image_bytes(content, mime, file.filename)
                model_used = "openai_vision"
                confidence = 0.7

            elif mime == "application/pdf":
                text_content = extract_text_from_pdf_bytes(content)
                if has_enough_text(text_content):
                    extracted_core = extract_from_text(text_content)
                    model_used = "openai_text"
                    confidence = 0.8
                else:
                    extracted_core = extract_from_image_bytes(content, mime, file.filename)
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

            blob = _flatten_text(extracted_core, text_content=text_content)
            extracted_core = _enrich_with_triage(extracted_core, blob)

            wrapper = {
                "filename": file.filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "storage": {"bucket": b2_bucket, "key": b2_key},
                "extracted": extracted_core,
            }

            conn.execute(
                text("INSERT INTO extractions (case_id, extracted_json, confidence, model, created_at) VALUES (:case_id, CAST(:json AS JSONB), :confidence, :model, NOW())"),
                {"case_id": case_id, "json": json.dumps(wrapper, ensure_ascii=False), "confidence": confidence, "model": model_used},
            )

            conn.execute(
                text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id, 'analyze_ok', CAST(:payload AS JSONB), NOW())"),
                {"case_id": case_id, "payload": json.dumps({"model": model_used, "confidence": confidence})},
            )

            conn.execute(text("UPDATE cases SET status='analyzed', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

        return {"ok": True, "message": "Análisis completo generado.", "case_id": str(case_id), "extracted": wrapper}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en /analyze: {e}")
