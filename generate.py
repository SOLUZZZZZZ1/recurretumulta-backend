import json
import os
import re
from typing import Any, Dict, Optional, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from ai.velocity_decision import decide_modo_velocidad

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import is_movil_context, build_movil_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template
from ai.infractions.distracciones import is_auriculares_context, build_auriculares_strong_template
from ai.infractions.atencion import is_atencion_context, build_atencion_strong_template
from ai.infractions.marcas_viales import is_marcas_viales_context, build_marcas_viales_strong_template
from ai.infractions.seguro import is_seguro_context, build_seguro_strong_template

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import build_dgt_alegaciones_text, build_dgt_reposicion_text

router = APIRouter(tags=["generate"])


# ======================================================
# EXTRACTOR ROBUSTO DE HECHO DENUNCIADO
# ======================================================

def extract_hecho_denunciado_literal(core: Dict[str, Any]) -> str:

    text_parts = []

    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)

    text = "\n".join(text_parts)

    if not text:
        return ""

    pattern = re.search(
        r"(hecho denunciado|hecho que se notifica|hecho imputado)\s*[:\-]?\s*",
        text,
        re.IGNORECASE,
    )

    if not pattern:
        return ""

    tail = text[pattern.end():]

    lines = [l.strip() for l in tail.split("\n") if l.strip()]

    collected = []

    for ln in lines:

        low = ln.lower()

        if any(x in low for x in [
            "importe",
            "bonificacion",
            "reduccion",
            "fecha",
            "puntos",
            "entidad",
            "matricula",
            "marca",
            "modelo",
            "fecha limite",
        ]):
            break

        collected.append(ln)

        if len(" ".join(collected)) > 500:
            break

    literal = " ".join(collected)

    literal = re.sub(r"\s+", " ", literal).strip()

    return literal


# ======================================================
# DETECTORES DE CONTEXTO
# ======================================================

def is_semaforo_context(core: Dict[str, Any]) -> bool:

    blob = " ".join([
        str(core.get("raw_text_pdf") or ""),
        str(core.get("raw_text_vision") or ""),
        str(core.get("raw_text_blob") or ""),
        str(core.get("hecho_denunciado_literal") or ""),
    ]).lower()

    signals = [
        "semáforo",
        "semaforo",
        "luz roja",
        "fase roja",
        "linea de detencion",
        "línea de detención",
        "no respetar",
    ]

    return any(s in blob for s in signals)


def is_velocity_context(core: Dict[str, Any]) -> bool:

    if core.get("velocidad_medida_kmh") and core.get("velocidad_limite_kmh"):
        return True

    blob = " ".join([
        str(core.get("raw_text_pdf") or ""),
        str(core.get("raw_text_vision") or ""),
    ]).lower()

    return "km/h" in blob or "velocidad" in blob


# ======================================================
# GENERACIÓN PRINCIPAL
# ======================================================

def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
    tipo: Optional[str] = None,
):

    row = conn.execute(
        text(
            "SELECT extracted_json FROM extractions "
            "WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"
        ),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    extracted_json = row[0]

    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)

    core = (wrapper.get("extracted") or {})

    # -------------------------------------------------
    # EXTRAER HECHO DENUNCIADO
    # -------------------------------------------------

    literal = extract_hecho_denunciado_literal(core)

    if literal:
        core["hecho_denunciado_literal"] = literal

    # -------------------------------------------------
    # ROUTING
    # -------------------------------------------------

    if is_semaforo_context(core):

        tpl = build_semaforo_strong_template(core)
        final_kind = "semaforo"

    elif is_velocity_context(core):

        tpl = build_dgt_alegaciones_text(core, interesado)
        final_kind = "velocidad"

    elif is_movil_context(core, ""):

        tpl = build_movil_strong_template(core)
        final_kind = "movil"

    elif is_auriculares_context(core, ""):

        tpl = build_auriculares_strong_template(core)
        final_kind = "auriculares"

    elif is_atencion_context(core, ""):

        tpl = build_atencion_strong_template(core)
        final_kind = "atencion"

    elif is_marcas_viales_context(core, ""):

        tpl = build_marcas_viales_strong_template(core)
        final_kind = "marcas_viales"

    elif is_seguro_context(core, ""):

        tpl = build_seguro_strong_template(core)
        final_kind = "seguro"

    else:

        tpl = build_dgt_alegaciones_text(core, interesado)
        final_kind = "generic"

    # -------------------------------------------------
    # INSERTAR EXTRACTO LITERAL
    # -------------------------------------------------

    cuerpo = tpl.get("cuerpo") or ""

    literal = core.get("hecho_denunciado_literal")

    if literal and literal.lower() not in cuerpo.lower():

        cuerpo = f"""
Extracto literal del boletín:
"{literal}"

{cuerpo}
"""

    tpl["cuerpo"] = cuerpo

    # -------------------------------------------------
    # GENERAR DOCUMENTOS
    # -------------------------------------------------

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

    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id,'generated_docx',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
        ),
        {
            "case_id": case_id,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_docx,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(docx_bytes),
        },
    )

    conn.execute(
        text(
            "INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) "
            "VALUES (:case_id,'generated_pdf',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
        ),
        {
            "case_id": case_id,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_pdf,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
    )

    return {
        "ok": True,
        "case_id": case_id,
        "final_kind": final_kind,
    }


class GenerateRequest(BaseModel):

    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest):

    engine = get_engine()

    with engine.begin() as conn:

        result = generate_dgt_for_case(
            conn,
            req.case_id,
            interesado=req.interesado,
            tipo=req.tipo,
        )

    return {"ok": True, "message": "Recurso generado.", **result}