import json
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

from ai.velocity_decision import decide_modo_velocidad

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import build_movil_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template
from ai.infractions.distracciones import build_auriculares_strong_template
from ai.infractions.atencion import build_atencion_strong_template
from ai.infractions.marcas_viales import build_marcas_viales_strong_template
from ai.infractions.seguro import build_seguro_strong_template
from ai.infractions.itv import build_itv_strong_template

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf

router = APIRouter(tags=["generate"])


# ======================================================
# EXTRACTOR HECHO DENUNCIADO
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
        ]):
            break

        collected.append(ln)

        if len(" ".join(collected)) > 500:
            break

    literal = " ".join(collected)
    literal = re.sub(r"\s+", " ", literal).strip()

    return literal


# ======================================================
# RESOLUCIÓN DEL TIPO DE INFRACCIÓN
# ======================================================

def resolve_infraction_type(core: Dict[str, Any]) -> str:

    tipo = (core.get("tipo_infraccion") or "").lower().strip()

    if tipo and tipo != "otro":
        return tipo

    blob = json.dumps(core, ensure_ascii=False).lower()

    if "semaforo" in blob or "fase roja" in blob:
        return "semaforo"

    if "km/h" in blob or "radar" in blob:
        return "velocidad"

    if "movil" in blob or "telefono" in blob:
        return "movil"

    if "auricular" in blob:
        return "auriculares"

    if "itv" in blob:
        return "itv"

    if "seguro" in blob:
        return "seguro"

    return "generic"


# ======================================================
# GENERACIÓN PRINCIPAL
# ======================================================

def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
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

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    literal = extract_hecho_denunciado_literal(core)

    if literal:
        core["hecho_denunciado_literal"] = literal

    tipo = resolve_infraction_type(core)

    # =================================================
    # ROUTING
    # =================================================

    if tipo == "semaforo":

        tpl = build_semaforo_strong_template(core)

    elif tipo == "velocidad":

        tpl = decide_modo_velocidad(core)

    elif tipo == "movil":

        tpl = build_movil_strong_template(core)

    elif tipo == "auriculares":

        tpl = build_auriculares_strong_template(core)

    elif tipo == "atencion":

        tpl = build_atencion_strong_template(core)

    elif tipo == "marcas_viales":

        tpl = build_marcas_viales_strong_template(core)

    elif tipo == "seguro":

        tpl = build_seguro_strong_template(core)

    elif tipo == "itv":

        tpl = build_itv_strong_template(core)

    else:

        tpl = build_condiciones_vehiculo_strong_template(core)

    cuerpo = tpl.get("cuerpo") or ""

    literal = core.get("hecho_denunciado_literal")

    if literal and literal.lower() not in cuerpo.lower():

        cuerpo = f"""
Extracto literal del boletín:
"{literal}"

{cuerpo}
"""

    tpl["cuerpo"] = cuerpo

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
        "final_kind": tipo,
    }


class GenerateRequest(BaseModel):

    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest):

    engine = get_engine()

    with engine.begin() as conn:

        result = generate_dgt_for_case(
            conn,
            req.case_id,
            interesado=req.interesado,
        )

    return {"ok": True, "message": "Recurso generado.", **result}