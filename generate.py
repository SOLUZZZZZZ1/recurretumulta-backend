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

RTM_DGT_GENERATION_MODE = (
    os.getenv("RTM_DGT_GENERATION_MODE") or "AI_FIRST"
).strip().upper()


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

    # Flags override
    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") else "alegaciones"

    tpl = None
    ai_used = False
    ai_error = None

    # ==========================
    # IA PRIMERO
    # ==========================
    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()

            if asunto and cuerpo:

                if override_mode:
                    # Forzar asunto claro
                    asunto = "RECURSO (MODO PRUEBA)"

                    # Eliminar primera línea si contiene 'borrador'
                    lines = cuerpo.splitlines()
                    cleaned = []
                    for i, line in enumerate(lines):
                        if i == 0 and "borrador" in line.lower():
                            continue
                        cleaned.append(line)
                    cuerpo = "\n".join(cleaned).strip()

                tpl = {"asunto": asunto, "cuerpo": cuerpo}
                ai_used = True

        except Exception as e:
            ai_error = str(e)
            tpl = None

    # ==========================
    # FALLBACK PLANTILLA
    # ==========================
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
            filename_base = "recurso_reposicion_dgt"
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)
            filename_base = "alegaciones_dgt"
    else:
        filename_base = (
            "recurso_reposicion_dgt"
            if tipo == "reposicion"
            else "alegaciones_dgt"
        )

    kind_docx = (
        "generated_docx_reposicion"
        if tipo == "reposicion"
        else "generated_docx_alegaciones"
    )

    kind_pdf = (
        "generated_pdf_reposicion"
        if tipo == "reposicion"
        else "generated_pdf_alegaciones"
    )

    # ==========================
    # GENERAR DOCX / PDF
    # ==========================
    docx_bytes = build_docx(tpl["asunto"], tpl["cuerpo"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        "generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    pdf_bytes = build_pdf(tpl["asunto"], tpl["cuerpo"])
    _, b2_key_pdf = upload_bytes(
        case_id,
        "generated",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )

    # Persistir documentos
    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id, :kind, :b2_bucket, :b2_key, :mime, :size_bytes,
)
