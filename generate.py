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
from ai.infractions.velocidad import (
    compute_velocity_calc_from_core,
    build_velocity_calc_paragraph,
    should_inject_tramo_error,
    build_tramo_error_paragraph,
)

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf

router = APIRouter(tags=["generate"])


# ======================================================
# HELPERS SEGUROS
# ======================================================

def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _safe_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


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
                "bonificación",
                "reduccion",
                "reducción",
                "fecha limite",
                "fecha límite",
                "puntos",
                "entidad",
                "matricula",
                "matrícula",
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
    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()

    # ANALYZE manda.
    if tipo and tipo not in ("otro", "unknown", "desconocido"):
        return tipo

    try:
        blob = json.dumps(core, ensure_ascii=False).lower()
    except Exception:
        blob = ""

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
# PLANTILLA VELOCIDAD (FUERTE Y SEGURA)
# ======================================================

def build_velocidad_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = _safe_dict(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_denunciado_literal") or core.get("hecho_imputado") or "EXCESO DE VELOCIDAD."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")
    radar_model = core.get("radar_modelo_hint") or "cinemómetro (no especificado)"

    calc = compute_velocity_calc_from_core(core, capture_mode="AUTO")
    calc_paragraph = build_velocity_calc_paragraph(core, capture_mode="AUTO") if isinstance(calc, dict) and calc.get("ok") else ""
    tramo_paragraph = build_tramo_error_paragraph(core, capture_mode="AUTO") if should_inject_tramo_error(core, capture_mode="AUTO") else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA DEL CINEMÓMETRO\n\n"
        "La imputación por exceso de velocidad exige acreditación técnica completa y verificable. No basta una referencia genérica al radar o cinemómetro: debe constar de forma precisa el dispositivo utilizado, su situación exacta, su verificación metrológica vigente y la trazabilidad íntegra del dato captado.\n\n"
        "No consta acreditado de forma completa en el expediente:\n"
        f"1) Identificación completa del cinemómetro utilizado (marca/modelo/número de serie). En la documentación solo se aprecia: {radar_model}.\n"
        "2) Certificado de verificación metrológica vigente en la fecha del hecho.\n"
        "3) Acreditación del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020 o la normativa metrológica que corresponda en la fecha del hecho).\n"
        "4) Captura o fotograma completo y legible, con identificación inequívoca del vehículo.\n"
        "5) Aplicación concreta del margen y determinación de la velocidad corregida.\n"
        "6) Acreditación de la cadena de custodia del dato y su correspondencia inequívoca con el vehículo denunciado.\n"
        "7) Acreditación del límite aplicable y de su señalización en el punto exacto.\n\n"
    )

    if isinstance(measured, (int, float)) or isinstance(limit, (int, float)):
        cuerpo += (
            "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n\n"
            f"• Velocidad medida: {measured if measured is not None else 'No consta'} km/h\n"
            f"• Velocidad límite: {limit if limit is not None else 'No consta'} km/h\n"
            f"• Dispositivo/radar: {radar_model}\n\n"
        )

    if calc_paragraph:
        cuerpo += calc_paragraph + "\n\n"

    if tramo_paragraph:
        cuerpo += tramo_paragraph + "\n\n"

    cuerpo += (
        "ALEGACIÓN SEGUNDA — DEFECTOS DE MOTIVACIÓN Y FALTA DE SOPORTE COMPLETO\n\n"
        "La Administración debe motivar de forma individualizada por qué la velocidad atribuida, una vez aplicado el margen correspondiente, encaja exactamente en el tramo sancionador impuesto. Sin fotograma completo, certificado metrológico, identificación técnica del equipo y acreditación de la cadena de custodia, no puede enervarse la presunción de inocencia con el rigor exigible en Derecho sancionador.\n\n"
        "ALEGACIÓN TERCERA — SOLICITUD DE EXPEDIENTE ÍNTEGRO Y PRUEBA TÉCNICA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo: boletín/denuncia completa, fotograma o secuencia completa, certificado de verificación metrológica, identificación del equipo, documentación técnica del control y motivación detallada del tramo sancionador aplicado.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba técnica completa para contradicción efectiva.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


# ======================================================
# VALIDACIÓN PLANTILLA
# ======================================================

def ensure_tpl_dict(tpl: Any, core: Dict[str, Any]) -> Dict[str, str]:
    if isinstance(tpl, dict):
        asunto = tpl.get("asunto")
        cuerpo = tpl.get("cuerpo")
        if isinstance(asunto, str) and asunto.strip() and isinstance(cuerpo, str) and cuerpo.strip():
            return {"asunto": asunto.strip(), "cuerpo": cuerpo.strip()}

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
    core = _safe_dict(wrapper.get("extracted"))

    literal = extract_hecho_denunciado_literal(core)
    if literal:
        core["hecho_denunciado_literal"] = literal

    final_kind = resolve_infraction_type(core)

    if final_kind == "semaforo":
        tpl = build_semaforo_strong_template(core)
    elif final_kind == "velocidad":
        tpl = build_velocidad_template(core)
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

    cuerpo = tpl.get("cuerpo") or ""
    literal = core.get("hecho_denunciado_literal")
    if isinstance(literal, str) and literal.strip() and literal.lower() not in cuerpo.lower():
        cuerpo = (
            'Extracto literal del boletín:\n'
            f'"{literal.strip()}"\n\n'
            f"{cuerpo}"
        )
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
