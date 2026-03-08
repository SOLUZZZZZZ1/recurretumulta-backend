import json
import re
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

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

router = APIRouter(tags=[\"generate\"])


def _normalize_spaces(text: str) -> str:
    return re.sub(r\"\\s+\", \" \", str(text or \"")).strip()


def extract_hecho_denunciado_literal(core: Dict[str, Any]) -> str:
    text_parts = []
    for k in (\"raw_text_pdf\", \"raw_text_vision\", \"raw_text_blob\"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)

    text = \"\\n\".join(text_parts)
    if not text:
        return \"\" 

    pattern = re.search(
        r\"(hecho denunciado|hecho que se notifica|hecho imputado)\\s*[:\\-]?\\s*\",
        text,
        re.IGNORECASE,
    )
    if not pattern:
        return \"\" 

    tail = text[pattern.end():]
    lines = [l.strip() for l in tail.split(\"\\n\") if l.strip()]

    collected = []
    stop_contains = [
        \"importe\", \"bonificacion\", \"reduccion\", \"fecha limite\",
        \"fecha documento\", \"fecha notificacion\", \"fecha decreto\",
        \"fecha caducidad\", \"puntos\", \"entidad\", \"matricula\",
        \"marca:\", \"modelo\", \"organismo:\", \"expediente_ref:\",
        \"tipo_sancion:\", \"observaciones:\", \"fecha_documento:\",
        \"fecha_notificacion:\", \"norma_hint:\", \"articulo_infringido_num:\",
        \"apartado_infringido_num:\", \"velocidad_medida_kmh:\",
        \"velocidad_limite_kmh:\", \"facts_phrases:\", \"jurisdiccion:\",
        \"preceptos_detectados:\",
    ]

    for ln in lines:
        low = ln.lower()
        if any(x in low for x in stop_contains):
            break
        collected.append(ln)
        if len(\" \".join(collected)) > 700:
            break

    literal = _normalize_spaces(\" \".join(collected))
    literal = re.sub(r'^\"+', \"\", literal)
    literal = re.sub(r'\"+$', \"\", literal)
    return literal


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        txt = str(value).strip()
        if not txt:
            return None
        m = re.search(r\"\\d+\", txt)
        return int(m.group(0)) if m else None
    except Exception:
        return None


def resolve_kind_from_article(core: Dict[str, Any]) -> Optional[str]:
    art = _safe_int(core.get(\"articulo_infringido_num\"))
    if art is None:
        return None
    if art == 48:
        return \"velocidad\"
    if art == 146:
        return \"semaforo\"
    if art == 167:
        return \"marcas_viales\"
    if art in (12, 15):
        return \"condiciones_vehiculo\"
    if art == 18:
        if is_movil_context(core, \"\"):
            return \"movil\"
        if is_auriculares_context(core, \"\"):
            return \"auriculares\"
        return \"atencion\"
    tipo = str(core.get(\"tipo_infraccion\") or \"\").strip().lower()
    if tipo in {\"seguro\", \"movil\", \"auriculares\", \"atencion\", \"semaforo\", \"velocidad\", \"marcas_viales\", \"condiciones_vehiculo\"}:
        return tipo
    return None


def is_semaforo_context(core: Dict[str, Any]) -> bool:
    blob = \" \".join([
        str(core.get(\"raw_text_pdf\") or \"\"),
        str(core.get(\"raw_text_vision\") or \"\"),
        str(core.get(\"raw_text_blob\") or \"\"),
        str(core.get(\"hecho_denunciado_literal\") or \"\"),
        str(core.get(\"hecho_imputado\") or \"\"),
    ]).lower()
    signals = [\"semáforo\", \"semaforo\", \"luz roja\", \"fase roja\", \"linea de detencion\", \"línea de detención\", \"t/s roja\", \"ts roja\"]
    return any(s in blob for s in signals)


def is_velocity_context(core: Dict[str, Any]) -> bool:
    if core.get(\"velocidad_medida_kmh\") and core.get(\"velocidad_limite_kmh\"):
        return True
    blob = \" \".join([
        str(core.get(\"raw_text_pdf\") or \"\"),
        str(core.get(\"raw_text_vision\") or \"\"),
        str(core.get(\"hecho_denunciado_literal\") or \"\"),
        str(core.get(\"hecho_imputado\") or \"\"),
    ]).lower()
    return \"km/h\" in blob or \"exceso de velocidad\" in blob or \"cinemometro\" in blob or \"radar\" in blob


def _build_tpl_from_kind(kind: str, core: Dict[str, Any], interesado: Optional[Dict[str, str]]) -> Dict[str, str]:
    if kind == \"semaforo\":
        return build_semaforo_strong_template(core)
    if kind == \"movil\":
        return build_movil_strong_template(core)
    if kind == \"auriculares\":
        return build_auriculares_strong_template(core)
    if kind == \"atencion\":
        return build_atencion_strong_template(core)
    if kind == \"marcas_viales\":
        return build_marcas_viales_strong_template(core)
    if kind == \"seguro\":
        return build_seguro_strong_template(core)
    if kind == \"condiciones_vehiculo\":
        return build_condiciones_vehiculo_strong_template(core)
    return build_dgt_alegaciones_text(core, interesado)


def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None, tipo: Optional[str] = None):
    row = conn.execute(
        text(\"SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1\"),
        {\"case_id\": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=\"No hay extracción.\") 

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = wrapper.get(\"extracted\") or {}

    literal = extract_hecho_denunciado_literal(core)
    if literal:
        core[\"hecho_denunciado_literal\"] = literal

    forced_kind = resolve_kind_from_article(core)

    if forced_kind:
        final_kind = forced_kind
        tpl = _build_tpl_from_kind(final_kind, core, interesado)
    else:
        tipo_infraccion = str(core.get(\"tipo_infraccion\") or \"\").strip().lower()
        if tipo_infraccion in {\"semaforo\", \"velocidad\", \"movil\", \"auriculares\", \"atencion\", \"marcas_viales\", \"seguro\", \"condiciones_vehiculo\"}:
            final_kind = tipo_infraccion
            tpl = _build_tpl_from_kind(final_kind, core, interesado)
        elif is_semaforo_context(core):
            final_kind = \"semaforo\"
            tpl = build_semaforo_strong_template(core)
        elif is_velocity_context(core):
            final_kind = \"velocidad\"
            tpl = build_dgt_alegaciones_text(core, interesado)
        elif is_movil_context(core, \"\"):
            final_kind = \"movil\"
            tpl = build_movil_strong_template(core)
        elif is_auriculares_context(core, \"\"):
            final_kind = \"auriculares\"
            tpl = build_auriculares_strong_template(core)
        elif is_atencion_context(core, \"\"):
            final_kind = \"atencion\"
            tpl = build_atencion_strong_template(core)
        elif is_marcas_viales_context(core, \"\"):
            final_kind = \"marcas_viales\"
            tpl = build_marcas_viales_strong_template(core)
        elif is_seguro_context(core, \"\"):
            final_kind = \"seguro\"
            tpl = build_seguro_strong_template(core)
        else:
            final_kind = \"generic\"
            tpl = build_dgt_alegaciones_text(core, interesado)

    cuerpo = tpl.get(\"cuerpo\") or \"\"
    literal = core.get(\"hecho_denunciado_literal\")
    if literal and literal.lower() not in cuerpo.lower():
        cuerpo = f'Extracto literal del boletín:\n\"{literal}\"\n\n{cuerpo}'
    tpl[\"cuerpo\"] = cuerpo

    docx_bytes = build_docx(tpl[\"asunto\"], tpl[\"cuerpo\"])
    b2_bucket, b2_key_docx = upload_bytes(
        case_id,
        \"generated\",
        docx_bytes,
        \".docx\",
        \"application/vnd.openxmlformats-officedocument.wordprocessingml.document\",
    )

    pdf_bytes = build_pdf(tpl[\"asunto\"], tpl[\"cuerpo\"])
    _, b2_key_pdf = upload_bytes(
        case_id,
        \"generated\",
        pdf_bytes,
        \".pdf\",
        \"application/pdf\",
    )

    conn.execute(
        text(\"INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,'generated_docx',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())\"),
        {
            \"case_id\": case_id,
            \"b2_bucket\": b2_bucket,
            \"b2_key\": b2_key_docx,
            \"mime\": \"application/vnd.openxmlformats-officedocument.wordprocessingml.document\",
            \"size_bytes\": len(docx_bytes),
        },
    )

    conn.execute(
        text(\"INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,'generated_pdf',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())\"),
        {
            \"case_id\": case_id,
            \"b2_bucket\": b2_bucket,
            \"b2_key\": b2_key_pdf,
            \"mime\": \"application/pdf\",
            \"size_bytes\": len(pdf_bytes),
        },
    )

    return {\"ok\": True, \"case_id\": case_id, \"final_kind\": final_kind}


class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


@router.post(\"/generate/dgt\")
def generate_dgt(req: GenerateRequest):
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, tipo=req.tipo)
    return {\"ok\": True, \"message\": \"Recurso generado.\", **result}
