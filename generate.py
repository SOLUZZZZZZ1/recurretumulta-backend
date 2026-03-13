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
from ai.infractions.cinturon import build_cinturon_strong_template
from ai.infractions.itv import build_itv_strong_template
from ai.infractions.generic import build_generic_body
from ai.infractions.municipal_semaforo import build_municipal_semaforo_template
from ai.infractions.casco import build_casco_strong_template
from ai.infractions.municipal_sentido_contrario import build_municipal_sentido_contrario_template
from ai.infractions.municipal_generic import build_municipal_generic_template
from ai.infractions.velocidad import (
    build_velocity_calc_paragraph,
    build_tramo_error_paragraph,
)

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf

router = APIRouter(tags=["generate"])


_ADMIN_PREFIXES = [
    "organismo:",
    "expediente_ref:",
    "tipo_sancion:",
    "observaciones:",
    "vision_raw_text:",
    "raw_text_pdf:",
    "raw_text_vision:",
    "raw_text_blob:",
    "fecha_documento:",
    "fecha_notificacion:",
    "importe:",
    "jurisdiccion:",
    "tipo_infraccion:",
    "facts_phrases:",
    "preceptos_detectados:",
    "articulo_infringido_num:",
    "apartado_infringido_num:",
    "norma_hint:",
]


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def _clean_hecho_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r", " ").replace("\n", " ")
    low = t.lower()

    for p in _ADMIN_PREFIXES:
        idx = low.find(p)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    stop_signals = [
        " datos vehiculo",
        " datos vehículo",
        " importe",
        " puntos",
        " fecha limite",
        " fecha límite",
        " boletin",
        " boletín",
        " agente denunciante",
        " telefono de informacion",
        " teléfono de información",
        " telefono de atencion",
        " teléfono de atención",
        " fax",
        " correo ordinario",
        " remitir el presente",
        " impreso relleno",
        " total principal",
        " precepto infringido",
    ]
    for s in stop_signals:
        idx = low.find(s)
        if idx > 0:
            t = t[:idx]
            low = t.lower()

    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    t = re.sub(r'^[\"“”]+|[\"“”]+$', "", t).strip()
    t = re.sub(r"^(movil|m[oó]vil)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(5a|5b|5c)\s+", "", t, flags=re.IGNORECASE)
    return t


def get_hecho_para_recurso(core: Dict[str, Any]) -> str:
    raw = (
        core.get("hecho_denunciado_resumido")
        or core.get("hecho_denunciado_literal")
        or core.get("hecho_imputado")
        or ""
    )
    return _clean_hecho_text(_safe_str(raw))


def extract_hecho_denunciado_literal(core: Dict[str, Any]) -> str:
    text_parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "vision_raw_text"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            text_parts.append(v)

    text = "\n".join(text_parts)
    if not text:
        return ""

    pattern = re.search(
        r"(hecho denunciado|hecho que se notifica|hecho imputado|hecho infringido)\s*[:\-]?\s*",
        text,
        re.IGNORECASE,
    )
    tail = text[pattern.end():] if pattern else text
    lines = [l.strip() for l in tail.split("\n") if l.strip()]

    collected = []
    started = False

    for ln in lines:
        low = ln.lower()

        if any(
            x in low for x in [
                "datos vehiculo", "datos vehículo", "importe", "bonificacion", "reduccion",
                "fecha limite", "fecha límite", "puntos", "entidad", "matricula", "marca:",
                "modelo", "domicilio", "boletin", "boletín", "telefono de informacion",
                "teléfono de información", "telefono de atencion", "teléfono de atención",
                "fax", "correo ordinario", "remitir el presente", "impreso relleno",
                "motivo de no notificacion", "motivo de no notificación",
            ]
        ):
            if started:
                break
            continue

        if not started:
            if any(
                s in low for s in [
                    "circular a", "circulaba a", "conducir", "cruce", "fase roja", "luz roja",
                    "semaforo", "utilizando", "auricular", "auriculares", "cascos", "bail",
                    "palmas", "volante", "km/h", "velocidad", "linea continua", "línea continua",
                    "itv", "seguro", "alumbrado", "detencion", "detención"
                ]
            ):
                started = True
                collected.append(ln)
        else:
            collected.append(ln)

        if len(" ".join(collected)) > 900:
            break

    return _clean_hecho_text(" ".join(collected))


def resolve_jurisdiction(core: Dict[str, Any]) -> str:
    j = _safe_str(core.get("jurisdiccion")).lower().strip()
    if j in ("municipal", "estatal", "desconocida"):
        return j

    blob = json.dumps(core, ensure_ascii=False).lower()
    if any(s in blob for s in ["ayuntamiento", "policia local", "policía local", "guardia urbana"]):
        return "municipal"
    if any(s in blob for s in ["direccion general de trafico", "dirección general de tráfico", "dgt", "guardia civil", "ministerio del interior"]):
        return "estatal"
    return "desconocida"


def _looks_like_semaforo(core: Dict[str, Any]) -> bool:
    blob = json.dumps(core, ensure_ascii=False).lower()
    blob = blob.replace("semáforo", "semaforo").replace("línea", "linea")

    sema_signals = [
        "semaforo",
        "fase roja",
        "luz roja",
        "cruce en rojo",
        "cruce con fase roja",
        "señal luminosa roja",
        "senal luminosa roja",
        "linea de detencion",
        "línea de detención",
        "rebase la linea de detencion",
        "rebasar la linea de detencion",
        "semaforo en rojo",
        "paso en rojo",
        "cruce fase roja",
        "articulo 146",
        "art. 146",
    ]
    if any(s in blob for s in sema_signals):
        return True

    if ("roja" in blob and "cruce" in blob) or ("roja" in blob and "detencion" in blob):
        return True

    return False


def resolve_infraction_type(core: Dict[str, Any]) -> str:
    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    if tipo == "semaforo":
        return "semaforo"
    if tipo and tipo not in ("otro", "unknown", "desconocido", "generic"):
        return tipo

    if _looks_like_semaforo(core):
        return "semaforo"

    blob = json.dumps(core, ensure_ascii=False).lower()

    if any(s in blob for s in ["km/h", "radar", "cinemometro", "cinemómetro", "exceso de velocidad"]):
        return "velocidad"
    if any(s in blob for s in ["telefono movil", "teléfono móvil", "uso manual", "movil", "móvil", "telefono", "teléfono"]):
        return "movil"
    if any(s in blob for s in ["auricular", "auriculares", "cascos conectados", "reproductores de sonido", "porta auricular"]):
        return "auriculares"
    if any(s in blob for s in ["itv", "inspeccion tecnica", "inspección técnica"]):
        return "itv"
    if any(s in blob for s in ["seguro obligatorio", "sin seguro", "vehiculo no asegurado", "vehículo no asegurado", "fiva", "8/2004"]):
        return "seguro"
    if any(s in blob for s in ["linea continua", "línea continua", "marca longitudinal continua", "marca vial"]):
        return "marcas_viales"
    if any(s in blob for s in ["atencion permanente", "atención permanente", "conduccion negligente", "conducción negligente", "distraccion", "distracción", "mordia las unas", "mordía las uñas"]):
        return "atencion"
    if any(s in blob for s in ["condiciones reglamentarias", "alumbrado", "senalizacion optica", "señalización óptica", "homolog", "neumatico", "neumático", "reflect", "espejo"]):
        return "condiciones_vehiculo"
    return "generic"


def fix_roman_headings(text: str) -> str:
    replacements = {
        r"\bi\.\s*antecedentes": "I. ANTECEDENTES",
        r"\bii\.\s*alegaciones": "II. ALEGACIONES",
        r"\biii\.\s*solicito": "III. SOLICITO",
    }
    out = text or ""
    for pattern, repl in replacements.items():
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def ensure_tpl_dict(tpl: Any, core: Dict[str, Any]) -> Dict[str, str]:
    if isinstance(tpl, dict):
        asunto = tpl.get("asunto")
        cuerpo = tpl.get("cuerpo")
        if isinstance(asunto, str) and asunto.strip() and isinstance(cuerpo, str) and cuerpo.strip():
            return {"asunto": asunto.strip(), "cuerpo": fix_roman_headings(cuerpo.strip())}

    fallback = build_generic_body(core)
    return {
        "asunto": fallback.get("asunto") or "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(fallback.get("cuerpo") or "A la atención del órgano competente."),
    }


def build_velocity_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "[EXPEDIENTE]"
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."

    hecho = get_hecho_para_recurso(core) or "EXCESO DE VELOCIDAD"
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")
    radar = core.get("radar_modelo_hint") or "cinemometro (no especificado)"

    tech_lines = []
    if measured:
        tech_lines.append(f"• Velocidad medida: {measured} km/h")
    if limit:
        tech_lines.append(f"• Velocidad límite: {limit} km/h")
    if radar:
        tech_lines.append(f"• Dispositivo/radar: {radar}")

    tech_block = ""
    if tech_lines:
        tech_block = "DATOS TÉCNICOS EXTRAÍDOS DEL EXPEDIENTE\n" + "\n".join(tech_lines) + "\n\n"

    calc_paragraph = build_velocity_calc_paragraph(core)
    tramo_paragraph = build_tramo_error_paragraph(core)

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
        "1) Identificación completa del cinemómetro utilizado (marca/modelo/número de serie).\n"
        "2) Certificado de verificación metrológica vigente en la fecha del hecho.\n"
        "3) Acreditación del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020 o la normativa metrológica que corresponda en la fecha del hecho).\n"
        "4) Captura o fotograma completo y legible, con identificación inequívoca del vehículo.\n"
        "5) Aplicación concreta del margen y determinación de la velocidad corregida.\n"
        "6) Acreditación de la cadena de custodia del dato y su correspondencia inequívoca con el vehículo denunciado.\n"
        "7) Acreditación del límite aplicable y de su señalización en el punto exacto.\n\n"
        f"{tech_block}"
        f"{calc_paragraph}\n\n"
    )

    if tramo_paragraph:
        cuerpo += f"{tramo_paragraph}\n\n"

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

    return {
        "asunto": "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE",
        "cuerpo": fix_roman_headings(cuerpo),
    }


def generate_dgt_for_case(conn, case_id: str, interesado: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción.")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or {}

    tipo = resolve_infraction_type(core)
jurisdiccion = resolve_jurisdiction(core)

if tipo == "semaforo" and jurisdiccion == "municipal":
    tpl = build_municipal_semaforo_template(core)
    final_kind = "municipal_semaforo"

elif tipo == "semaforo":
    tpl = build_semaforo_strong_template(core)
    final_kind = "semaforo"

elif tipo == "velocidad":
    tpl = build_velocity_strong_template(core)
    final_kind = "velocidad"

elif tipo == "movil":
    tpl = build_movil_strong_template(core)
    final_kind = "movil"

elif tipo == "auriculares":
    tpl = build_auriculares_strong_template(core)
    final_kind = "auriculares"

elif tipo == "cinturon":
    tpl = build_cinturon_strong_template(core)
    final_kind = "cinturon"

elif tipo == "casco":
    tpl = build_casco_strong_template(core)
    final_kind = "casco"

elif tipo == "atencion":
    tpl = build_atencion_strong_template(core)
    final_kind = "atencion"

elif tipo == "marcas_viales":
    tpl = build_marcas_viales_strong_template(core)
    final_kind = "marcas_viales"

elif tipo == "seguro":
    tpl = build_seguro_strong_template(core)
    final_kind = "seguro"

elif tipo == "itv":
    tpl = build_itv_strong_template(core)
    final_kind = "itv"

elif tipo == "condiciones_vehiculo":
    tpl = build_condiciones_vehiculo_strong_template(core)
    final_kind = "condiciones_vehiculo"

elif tipo == "carril":
    tpl = build_generic_body(core)
    final_kind = "carril"

elif jurisdiccion == "municipal":
    blob = json.dumps(core, ensure_ascii=False).lower()

    if "sentido contrario" in blob or "direccion prohibida" in blob or "dirección prohibida" in blob:
        tpl = build_municipal_sentido_contrario_template(core)
        final_kind = "municipal_sentido_contrario"

    elif _looks_like_semaforo(core):
        tpl = build_municipal_semaforo_template(core)
        final_kind = "municipal_semaforo_fallback"

    else:
        tpl = build_municipal_generic_template(core)
        final_kind = "municipal_generic"

else:
    tpl = build_generic_body(core)
    final_kind = "generic"

    else:
    tpl = build_generic_body(core)
    final_kind = "generic"

    tpl = ensure_tpl_dict(tpl, core)

    cuerpo = tpl.get("cuerpo") or ""
    hecho = get_hecho_para_recurso(core)

    if hecho and hecho.lower() not in cuerpo.lower():
        cuerpo = "Extracto literal del boletín:\n" + f"“{hecho}”\n\n" + cuerpo

    tpl["cuerpo"] = fix_roman_headings(cuerpo)

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
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,'generated_docx',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_docx,
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(docx_bytes),
        },
    )

    conn.execute(
        text("INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at) VALUES (:case_id,'generated_pdf',:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"),
        {
            "case_id": case_id,
            "b2_bucket": b2_bucket,
            "b2_key": b2_key_pdf,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
    )

    return {"ok": True, "case_id": case_id, "final_kind": final_kind}


class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)


@router.post("/generate/dgt")
def generate_dgt(req: GenerateRequest) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado)
    return {"ok": True, "message": "Recurso generado.", **result}
