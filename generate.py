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
# - TEMPLATES_ONLY: fuerza plantillas (rollback rápido)
RTM_DGT_GENERATION_MODE = (os.getenv("RTM_DGT_GENERATION_MODE") or "AI_FIRST").strip().upper()


# ==========================
# HELPERS
# ==========================
def _load_interested_data_from_cases(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data, '{}'::jsonb) FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    return (row[0] if row and row[0] else {}) or {}


def _merge_interesado(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    primary = primary or {}
    fallback = fallback or {}
    result = dict(fallback)
    for k, v in primary.items():
        if v not in (None, ""):
            result[k] = v
    return result


def _missing_interested_fields(interesado: Dict[str, Any]) -> list:
    interesado = interesado or {}
    missing = []
    for k in ("nombre", "dni_nie", "domicilio_notif"):
        v = interesado.get(k)
        if not v or not str(v).strip():
            missing.append(k)
    return missing


def _load_case_flags(conn, case_id: str) -> Dict[str, bool]:
    """Flags de prueba/override por case (para MODO PRUEBA)."""
    row = conn.execute(
        text(
            "SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) " 
            "FROM cases WHERE id=:id"
        ),
        {"id": case_id},
    ).fetchone()
    return {
        "test_mode": bool(row[0]) if row else False,
        "override_deadlines": bool(row[1]) if row else False,
    }


def _apply_override_mode_b(asunto: str, cuerpo: str) -> tuple[str, str]:
    """Modo B: generar recurso completo, pero marcado como (MODO PRUEBA) y sin prefijo 'Borrador...'"""
    asunto = (asunto or "").strip()
    cuerpo = (cuerpo or "").strip()

    asunto = asunto.replace("Borrador para revisión (no presentar sin verificar plazos/datos)", "").strip()
    asunto = asunto.replace("Borrador para revisión", "").strip()
    if not asunto:
        asunto = "RECURSO"
    if "(MODO PRUEBA)" not in asunto:
        asunto = f"{asunto} (MODO PRUEBA)"

    if cuerpo.lower().startswith("borrador para revisión"):
        parts = cuerpo.splitlines()
        if len(parts) > 1:
            cuerpo = "\n".join(parts[1:]).lstrip()

    return asunto, cuerpo


# ==========================
# FUNCIÓN PRINCIPAL
# ==========================
def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
    tipo: Optional[str] = None,
) -> Dict[str, Any]:

    row = conn.execute(
        text(
            "SELECT extracted_json FROM extractions " 
            "WHERE case_id = :case_id " 
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = wrapper.get("extracted") or {}

    # Merge interesado desde DB
    interesado_db = _load_interested_data_from_cases(conn, case_id)
    interesado = _merge_interesado(interesado or {}, interesado_db)

    # Flags override (modo pruebas)
    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    tpl = None
    ai_used = False
    ai_error = None

    # IA PRIMERO
    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()

            if asunto and cuerpo:
                if override_mode:
                    asunto, cuerpo = _apply_override_mode_b(asunto, cuerpo)
                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True

        except Exception as e:
            ai_error = str(e)
            tpl = None

    # FALLBACK PLANTILLA
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)
            filename_base = "alegaciones_dgt"
    else:
        filename_base = "recurso_reposicion_dgt" if tipo == "reposicion" else "alegaciones_dgt"

    kind_docx = "generated_docx_reposicion" if tipo == "reposicion" else "generated_docx_alegaciones"
    kind_pdf = "generated_pdf_reposicion" if tipo == "reposicion" else "generated_pdf_alegaciones"

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

    # Persistir documents
    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())"
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
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes, NOW())"
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

    # Evento auditable
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:case_id, 'resource_generated', CAST(:payload AS JSONB), NOW())"
        ),
        {
            "case_id": case_id,
            "payload": json.dumps(
                {
                    "tipo": tipo,
                    "ai_used": ai_used,
                    "ai_error": ai_error,
                    "generation_mode": RTM_DGT_GENERATION_MODE,
                    "override_mode": override_mode,
                    "missing_interested_fields": _missing_interested_fields(interesado),
                }
            ),
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
        "override_mode": override_mode,
    }


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
