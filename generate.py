import json
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

from ai.infractions.semaforo import build_semaforo_strong_template
from ai.infractions.movil import build_movil_strong_template
from ai.infractions.condiciones_vehiculo import build_condiciones_vehiculo_strong_template
from ai.infractions.distracciones import build_auriculares_strong_template
from ai.infractions.atencion import build_atencion_strong_template
from ai.infractions.marcas_viales import build_marcas_viales_strong_template
from ai.infractions.seguro import build_seguro_strong_template
from ai.infractions.itv import build_itv_strong_template
from ai.infractions.generic import build_generic_body

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import build_dgt_alegaciones_text

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

        if any(
            x in low
            for x in [
                "importe",
                "bonificacion",
                "reduccion",
                "fecha limite",
                "fecha límite",
                "puntos",
                "entidad",
                "matricula",
                "marca",
                "modelo",
                "domicilio",
                "provincia",
                "codigo postal",
                "código postal",
                "boletin",
                "boletín",
                "agente",
                "jefatura",
            ]
        ):
            break

        collected.append(ln)

        if len(" ".join(collected)) > 700:
            break

    literal = " ".join(collected)
    literal = re.sub(r"\s+", " ", literal).strip()
    return literal


# ======================================================
# RESOLUCIÓN DEL TIPO DE INFRACCIÓN
# ======================================================

def resolve_infraction_type(core: Dict[str, Any]) -> str:
    tipo = (core.get("tipo_infraccion") or "").lower().strip()

    # ANALYZE manda. Si viene bien informado, se obedece.
    if tipo and tipo not in ("otro", "unknown", "desconocido"):
        return tipo

    blob = json.dumps(core, ensure_ascii=False).lower()

    if any(s in blob for s in ["semaforo", "semáforo", "fase roja", "luz roja", "linea de detencion", "línea de detención"]):
        return "semaforo"

    if any(s in blob for s in ["km/h", "radar", "cinemometro", "cinemómetro", "exceso de velocidad"]):
        return "velocidad"

    if any(s in blob for s in ["telefono movil", "teléfono móvil", "uso manual", "movil", "móvil", "telefono", "teléfono"]):
        return "movil"

    if any(s in blob for s in ["auricular", "auriculares", "cascos conectados", "reproductores de sonido"]):
        return "auriculares"

    if any(s in blob for s in ["itv", "inspeccion tecnica", "inspección técnica"]):
        return "itv"

    if any(s in blob for s in ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehículo no asegurado", "fiva", "8/2004"]):
        return "seguro"

    if any(s in blob for s in ["linea continua", "línea continua", "marca longitudinal continua", "marca vial"]):
        return "marcas_viales"

    if any(s in blob for s in ["atencion permanente", "atención permanente", "conduccion negligente", "conducción negligente", "distraccion", "distracción"]):
        return "atencion"

    if any(s in blob for s in ["condiciones reglamentarias", "alumbrado", "senalizacion optica", "señalización óptica", "homolog", "neumatico", "neumático"]):
        return "condiciones_vehiculo"

    return "generic"


# ======================================================
# VALIDACIÓN PLANTILLA
# ======================================================

def ensure_tpl_dict(tpl: Any, core: Dict[str, Any]) -> Dict[str, str]:
    if isinstance(tpl, dict):
        asunto = tpl.get("asunto")
        cuerpo = tpl.get("cuerpo")
        if isinstance(asunto, str) and asunto.strip() and isinstance(cuerpo, str) and cuerpo.strip():
            return {"asunto": asunto.strip(), "cuerpo": cuerpo.strip()}

    # Fallback ultra seguro
    fallback = build_generic_body(core)
    return {
        "asunto": fallback.get("asunto") or "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fallback.get("cuerpo") or "A la atención del órgano competente.",
    }


# ======================================================
# GENERACIÓN PRINCIPAL
# ======================================================

def generate_dgt_for_case(
    conn,
    case_id: str,
    interesado: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
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

    # -------------------------------------------------
    # EXTRAER HECHO DENUNCIADO
    # -------------------------------------------------
    literal = extract_hecho_denunciado_literal(core)
    if literal:
        core["hecho_denunciado_literal"] = literal

    # -------------------------------------------------
    # RESOLVER TIPO
    # -------------------------------------------------
    final_kind = resolve_infraction_type(core)

    # -------------------------------------------------
    # ROUTING
    # -------------------------------------------------
    if final_kind == "semaforo":
        tpl = build_semaforo_strong_template(core)

    elif final_kind == "velocidad":
        # IMPORTANTE:
        # velocidad debe devolver SIEMPRE un tpl con asunto+cuerpo.
        # No usar aquí decide_modo_velocidad como si fuera plantilla final.
        tpl = build_dgt_alegaciones_text(core, interesado or {})

    elif final_kind == "movil":
        tpl = build_movil_strong_template(core)

    elif final_kind == "auriculares":
        tpl = build_auriculares_strong_template(core)

    elif final_kind == "atencion":
        tpl = build_atencion_strong_template(core)

    elif final_kind == "marcas_viales":
        tpl = build_marcas_viales_strong_template(core)

    elif final_kind == "seguro":
        tpl = build_seguro_strong_template(core)

    elif final_kind == "itv":
        tpl = build_itv_strong_template(core)

    elif final_kind == "condiciones_vehiculo":
        tpl = build_condiciones_vehiculo_strong_template(core)

    else:
        tpl = build_generic_body(core)

    tpl = ensure_tpl_dict(tpl, core)

    # -------------------------------------------------
    # INSERTAR EXTRACTO LITERAL
    # -------------------------------------------------
    cuerpo = tpl.get("cuerpo") or ""
    literal = core.get("hecho_denunciado_literal")

    if isinstance(literal, str) and literal.strip() and literal.lower() not in cuerpo.lower():
        cuerpo = (
            'Extracto literal del boletín:\n'
            f'"{literal.strip()}"\n\n'
            f"{cuerpo}"
        )

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


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()

    with engine.begin() as conn:
        result = generate_dgt_for_case(
            conn,
            req.case_id,
            interesado=req.interesado,
        )

    return {"ok": True, "message": "Recurso generado.", **result}