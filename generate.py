import json
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import (
    build_dgt_alegaciones_text,
    build_dgt_reposicion_text,
)

router = APIRouter(tags=["generate"])

# RTM: Modo de generación DGT
# - AI_FIRST (default): intenta usar el borrador IA (draft.asunto + draft.cuerpo)
#   Si no hay draft usable o falla -> fallback a plantillas dgt_templates
# - TEMPLATES_ONLY: fuerza el comportamiento anterior (plantillas)
RTM_DGT_GENERATION_MODE = (os.getenv("RTM_DGT_GENERATION_MODE") or "AI_FIRST").strip().upper()


# =========================================================
# FUNCIÓN CENTRAL REUTILIZABLE
# =========================================================
def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
    tipo: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT extracted_json
            FROM extractions
            WHERE case_id = :case_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = wrapper.get("extracted") or {}

    # Determinar tipo si no viene forzado
    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    # ---------------------------------------------------------
    # RTM: AI_FIRST con fallback a plantillas
    # ---------------------------------------------------------
    tpl: Optional[Dict[str, str]] = None
    ai_used = False
    ai_error: Optional[str] = None

    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()
            if asunto and cuerpo:
                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True
        except Exception as e:
            ai_error = str(e)
            tpl = None

    # Fallback (comportamiento anterior)
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado or {})
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado or {})
            filename_base = "alegaciones_dgt"
    else:
        # Mantener naming por tipo aunque venga de IA
        filename_base = "recurso_reposicion_dgt" if tipo == "reposicion" else "alegaciones_dgt"

    # Kinds (se mantienen, para compatibilidad con OPS/automation)
    if tipo == "reposicion":
        kind_docx = "generated_docx_reposicion"
        kind_pdf = "generated_pdf_reposicion"
    else:
        kind_docx = "generated_docx_alegaciones"
        kind_pdf = "generated_pdf_alegaciones"

    # DOCX
    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # PDF
    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(
        case_id,
        "generated",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )

    # Guardar documentos
    conn.execute(
        text(
            """
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
            VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())
            """
        ),
        {
            "case_id": case_id,
            "kind": kind_docx,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_docx,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(docx_bytes),
        },
    )

    conn.execute(
        text(
            """
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
            VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())
            """
        ),
        {
            "case_id": case_id,
            "kind": kind_pdf,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_pdf,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
    )

    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, 'resource_generated', CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "payload": json.dumps({"tipo": tipo, "ai_used": ai_used, "ai_error": ai_error}),
        },
    )

    conn.execute(
        text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )

    return {
        "ok": True,
        "case_id": case_id,
        "tipo": tipo,
        "filename_base": filename_base,
        "ai_used": ai_used,
        "ai_error": ai_error,
    }


# =========================================================
# ENDPOINT ORIGINAL (SE MANTIENE)
# =========================================================
class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(
            conn,
            req.case_id,
            interesado=req.interesado,
            tipo=req.tipo,
        )

    return {
        "ok": True,
        "message": "Recurso generado en DOCX y PDF.",
        **result,
    }
