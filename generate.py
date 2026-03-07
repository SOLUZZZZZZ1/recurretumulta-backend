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
from ai.infractions.helpers import extract_hecho_literal, build_extra_attack_paragraphs

# ✅ NUEVO: auriculares (art.18.2)
from ai.infractions.distracciones import is_auriculares_context, build_auriculares_strong_template

# ✅ NUEVO: atención / negligente (art.3.1 / 18.1) — con IA opcional (RTM_ATENCION_AI=1)
from ai.infractions.atencion import is_atencion_context, build_atencion_strong_template
from ai.infractions.marcas_viales import is_marcas_viales_context, build_marcas_viales_strong_template
from ai.infractions.seguro import is_seguro_context, build_seguro_strong_template

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import build_dgt_alegaciones_text, build_dgt_reposicion_text

router = APIRouter(tags=["generate"])

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
    out = dict(fallback)
    for k, v in primary.items():
        if v not in (None, ""):
            out[k] = v
    return out


def _missing_interested_fields(interesado: Dict[str, Any]) -> List[str]:
    interesado = interesado or {}
    missing: List[str] = []
    for k in ("nombre", "dni_nie", "domicilio_notif"):
        v = interesado.get(k)
        if not v or not str(v).strip():
            missing.append(k)
    return missing


def _load_case_flags(conn, case_id: str) -> Dict[str, bool]:
    row = conn.execute(
        text("SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    return {
        "test_mode": bool(row[0]) if row else False,
        "override_deadlines": bool(row[1]) if row else False,
    }


def _strip_borrador_prefix_from_body(body: str) -> str:
    body = (body or "").lstrip()
    if not body:
        return body

    lines = body.splitlines()
    if lines and ("borrador" in (lines[0] or "").lower()):
        lines = lines[1:]

    while lines and not (lines[0] or "").strip():
        lines = lines[1:]

    return "\n".join(lines).strip()


def _first_alegacion_title(body: str) -> str:
    if not body:
        return ""
    for line in (body.splitlines() or []):
        l = (line or "").strip()
        if not l:
            continue
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            return l
    return ""


def _raw_blob(core: Dict[str, Any]) -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts).lower()


# ==========================
# TIPICIDAD HARD LOCK (estable)
# ==========================
def _norma_key_from_hint(core: Dict[str, Any]) -> str:
    h = str((core or {}).get("norma_hint") or "").upper()
    if "RDL 8/2004" in h or "8/2004" in h or "LSOA" in h:
        return "RDL 8/2004"
    if "RGC" in h or "REGLAMENTO GENERAL DE CIRCUL" in h or "CIR" in h:
        return "RGC"
    return ""


def _expected_kind_from_article(core: Dict[str, Any]) -> Optional[str]:
    core = core or {}
    norma = _norma_key_from_hint(core)
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None and str(art).strip().isdigit() else None
    except Exception:
        art_i = None
    if not norma or art_i is None:
        return None

    if norma == "RGC":
        if art_i == 48:
            return "velocidad"
        if art_i == 146:
            return "semaforo"
        if art_i in (12, 15):
            return "condiciones_vehiculo"
        if art_i == 167:
            return "marcas_viales"
        # art. 18 se resuelve por subdetectores (móvil/auriculares/atención)
        if art_i == 18:
            return "cluster_18"
    if norma == "RDL 8/2004":
        if art_i == 2:
            return "seguro"
    return None


# ==========================
# CONTEXT DETECTORS
# ==========================

def _is_velocity_context(core: Dict[str, Any], cuerpo: str) -> bool:
    """✅ Velocidad SOLO si es explícita.

    Regla:
    - True si tipo_infraccion == 'velocidad'
    - True si hay campos estructurados reales (velocidad_medida_kmh y velocidad_limite_kmh)
    - ❌ NO usa el 'cuerpo' (texto IA/plantilla) como señal (evita falsos positivos)
    """
    core = core or {}

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "velocidad":
        return True

    measured = core.get("velocidad_medida_kmh")
    limit = core.get("velocidad_limite_kmh")

    if isinstance(measured, (int, float)) and isinstance(limit, (int, float)):
        return True

    if isinstance(measured, str) and measured.strip().isdigit() and isinstance(limit, str) and limit.strip().isdigit():
        return True

    return False


def _is_semaforo_context_robust(core: Dict[str, Any], cuerpo: str) -> bool:
    core = core or {}
    parts: List[str] = []

    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    parts.append(str(core.get("hecho_imputado") or ""))
    parts.append(cuerpo or "")

    blob = " ".join(parts).lower()

    sema_signals = [
        "semáforo", "semaforo",
        "fase roja",
        "cruce en rojo", "cruce con fase roja",
        "t/s roja", "ts roja",
        "línea de detención", "linea de detencion",
        "no respeta la luz roja",
        "rebase la linea de detencion", "rebasar la linea de detencion",
    ]
    if any(s in blob for s in sema_signals):
        return True

    if re.search(r"\bart\.?\s*146\b", blob) or re.search(r"\bart[ií]culo\s*146\b", blob) or re.search(r"\b146\s*[\.,]\s*1\b", blob):
        return True

    return False


def _is_condiciones_context_robust(core: Dict[str, Any], cuerpo: str) -> bool:
    core = core or {}

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "condiciones_vehiculo":
        return True

    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None else None
    except Exception:
        art_i = None
    if art_i in (12, 15):
        return True

    blob = _raw_blob(core) + "\n" + (cuerpo or "").lower()

    signals = [
        "alumbrado",
        "señalización óptica", "senalizacion optica",
        "dispositivos de alumbrado",
        "reglamento general de vehículos", "reglamento general de vehiculos",
        "rd 2822/98", "2822/98",
        "anexo ii",
        "condiciones reglamentarias",
        "no cumplan las exigencias",
        "luz roja en la parte trasera",
        "emite luz en forma de destellos",
        "dispositivo obligatorio",
        "modificación no autorizada", "modificacion no autorizada",
        "deslumbr", "reflect", "reflej", "pulid", "como un espejo",
        "itv", "inspección técnica", "inspeccion tecnica", "caducad",
        "neumático", "neumatico", "banda de rodadura", "dibujo", "desgastad", "liso",
        "reforma", "homolog", "proyecto", "certificado",
    ]
    if any(s in blob for s in signals):
        return True

    if re.search(r"\bart\.?\s*12\b", blob) or re.search(r"\bart[ií]culo\s*12\b", blob):
        return True
    if re.search(r"\bart\.?\s*15\b", blob) or re.search(r"\bart[ií]culo\s*15\b", blob):
        return True

    return False


# ==========================
# VELOCIDAD — VSE-1 DETERMINISTA
# ==========================

def _speed_margin_value(measured: int) -> float:
    if measured <= 100:
        return 5.0
    return round(measured * 0.05, 2)


def _dgt_speed_sanction_table() -> Dict[int, list]:
    return {
        20: [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],
        30: [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],
        40: [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],
        50: [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (121,999,600,6,'600€ 6 puntos')],
        60: [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],
        70: [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],
        80: [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],
        90: [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],
        100:[(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],
        110:[(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],
        120:[(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
    }


def _expected_speed_sanction(limit: int, corrected: float) -> Dict[str, Any]:
    tbl = _dgt_speed_sanction_table()
    lim = int(limit) if int(limit) in tbl else None
    if lim is None:
        return {"fine": None, "points": None, "band": None, "table_limit": None}
    v = int(round(float(corrected)))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}


def sanitize_imposed_fine(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            if re.search(r"[A-Za-z]", v):
                return None
            v = v.replace(".", "").replace(",", "").replace("€", "").strip()
            if not v.isdigit():
                return None
            value = int(v)
        if isinstance(value, (int, float)):
            iv = int(round(float(value)))
        else:
            return None
        allowed = {100, 200, 300, 400, 500, 600}
        return iv if iv in allowed else None
    except Exception:
        return None


def sanitize_imposed_points(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            if not v.isdigit():
                return None
            value = int(v)
        if isinstance(value, (int, float)):
            iv = int(round(float(value)))
        else:
            return None
        return iv if 0 <= iv <= 6 else None
    except Exception:
        return None


def _compute_velocity_calc_from_core(core: Dict[str, Any]) -> Dict[str, Any]:
    try:
        measured = core.get("velocidad_medida_kmh")
        limit = core.get("velocidad_limite_kmh")
        if isinstance(measured, str) and measured.strip().isdigit():
            measured = int(measured.strip())
        if isinstance(limit, str) and limit.strip().isdigit():
            limit = int(limit.strip())
        if not isinstance(measured, int) or not isinstance(limit, int):
            return {"ok": False, "reason": "missing_measured_or_limit"}

        margin = _speed_margin_value(int(measured))
        corrected = max(0.0, float(measured) - float(margin))
        expected = _expected_speed_sanction(int(limit), corrected)

        imposed_fine = sanitize_imposed_fine(core.get("sancion_importe_eur"))
        imposed_pts = sanitize_imposed_points(core.get("puntos_detraccion"))

        mismatch = False
        mismatch_reasons: List[str] = []
        if isinstance(imposed_fine, int) and isinstance(expected.get("fine"), int) and imposed_fine != expected.get("fine"):
            mismatch = True
            mismatch_reasons.append("fine_mismatch")
        if isinstance(imposed_pts, int) and isinstance(expected.get("points"), int) and imposed_pts != expected.get("points"):
            mismatch = True
            mismatch_reasons.append("points_mismatch")

        return {
            "ok": True,
            "limit": int(limit),
            "measured": int(measured),
            "margin_value": float(margin),
            "corrected": round(float(corrected), 2),
            "expected": expected,
            "imposed": {"fine": imposed_fine, "points": imposed_pts},
            "mismatch": mismatch,
            "mismatch_reasons": mismatch_reasons,
        }
    except Exception as e:
        return {"ok": False, "reason": f"error:{e}"}


def _build_velocity_calc_paragraph(core: Dict[str, Any]) -> str:
    vc = _compute_velocity_calc_from_core(core)
    if not vc.get("ok"):
        return ""
    limit = vc.get("limit")
    measured = vc.get("measured")
    margin = vc.get("margin_value")
    corrected = vc.get("corrected")
    exceso = float(corrected) - float(limit)
    if exceso <= 0:
        return (
            "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
            f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
            f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, lo que la situaría por debajo del límite máximo permitido. "
            "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
        )
    return (
        "A efectos ilustrativos y sin perjuicio de la prueba que corresponde a la Administración, "
        f"con un límite de {limit} km/h y una medición de {measured} km/h, aplicando un margen de {margin:.2f} km/h, "
        f"la velocidad corregida se situaría en torno a {corrected:.2f} km/h, lo que supondría un exceso efectivo aproximado de {exceso:.2f} km/h sobre el límite. "
        "Debe acreditarse documentalmente el margen efectivamente aplicado, la velocidad corregida resultante y su encaje en el tramo sancionador."
    )


def _velocity_vse1_template(core: Dict[str, Any]) -> Tuple[str, str]:
    expediente = (core or {}).get("expediente_ref") or (core or {}).get("numero_expediente") or "No consta acreditado."
    organo = (core or {}).get("organo") or (core or {}).get("organismo") or "No consta acreditado."
    hecho = (core or {}).get("hecho_imputado") or "EXCESO DE VELOCIDAD."

    vc = _compute_velocity_calc_from_core(core)
    if vc.get("ok") and isinstance(vc.get("measured"), int) and isinstance(vc.get("limit"), int):
        hecho = f"EXCESO DE VELOCIDAD ({vc.get('measured')} km/h; límite {vc.get('limit')} km/h)."

    calc = _build_velocity_calc_paragraph(core)
    calc = (calc + "\n") if calc else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n"
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)\n\n"
        "La validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación documental del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020). "
        "No basta una afirmación genérica de verificación: debe aportarse soporte documental verificable.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y emplazamiento exacto (vía, punto kilométrico y sentido).\n"
        "2) Certificado de verificación metrológica vigente a la fecha del hecho, así como constancia de la última verificación periódica o, en su caso, tras reparación.\n"
        "3) Captura o fotograma COMPLETO, sin recortes y legible, que permita asociar inequívocamente la medición al vehículo denunciado.\n"
        "4) Margen aplicado y determinación de la velocidad corregida (velocidad medida vs velocidad corregida), con motivación técnica suficiente.\n"
        "5) Acreditación de la cadena de custodia del dato (integridad del registro, sistema de almacenamiento y correspondencia inequívoca con el vehículo denunciado).\n"
        "6) Acreditación del límite aplicable y su señalización en el punto exacto (genérica vs específica) y su coherencia con la ubicación consignada.\n"
        "7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y tramo sancionador resultante.\n\n"
        f"{calc}"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica suficiente.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.\n"
    ).strip()
    return asunto, cuerpo


def _inject_tramo_error_paragraph(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if "posible error de tramo sancionador" in (body or "").lower():
            return body
        if not body or not isinstance(velocity_calc, dict) or not velocity_calc.get("ok") or not velocity_calc.get("mismatch"):
            return body
        imposed = (velocity_calc.get("imposed") or {})
        if not isinstance(imposed.get("fine"), int):
            return body

        exp = velocity_calc.get("expected") or {}
        imp = imposed
        if exp.get("fine") is None and exp.get("points") is None:
            return body

        parts = []
        parts.append("De forma adicional, se aprecia posible error de tramo sancionador.")
        if isinstance(imp.get("fine"), int) and isinstance(exp.get("fine"), int) and imp.get("fine") != exp.get("fine"):
            parts.append(f"Consta un importe impuesto de {imp.get('fine')}€, mientras que, atendida la velocidad corregida, el tramo orientativo podría corresponder a {exp.get('fine')}€.")
        if isinstance(imp.get("points"), int) and isinstance(exp.get("points"), int) and imp.get("points") != exp.get("points"):
            parts.append(f"Asímismo, constan {imp.get('points')} puntos, cuando el tramo orientativo podría implicar {exp.get('points')} puntos.")
        if exp.get("band"):
            parts.append(f"Banda orientativa considerada: {exp.get('band')}.")
        parts.append("En todo caso, corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda/tramo aplicado, con motivación técnica verificable.")
        extra = " ".join(parts) + "\n"

        mm = re.search(r"^III\.\s*SOLICITO\b", body, flags=re.IGNORECASE | re.MULTILINE)
        if not mm:
            return body + "\n\n" + extra
        return body[:mm.start()] + extra + "\n" + body[mm.start():]
    except Exception:
        return body


def _inject_bucket_paragraph(body: str, decision: Dict[str, Any]) -> str:
    if not body or not isinstance(decision, dict):
        return body
    if (decision.get("mode") or "") != "probatorio_puro":
        return body

    bucket = decision.get("bucket")
    if bucket not in ("leve", "grave"):
        return body

    if bucket == "leve":
        extra = (
            "A mayor abundamiento, aun en hipótesis de que se tuviera por acreditada la medición, se trataría de un exceso mínimo, "
            "sin constancia de riesgo concreto, por lo que procede extremar las exigencias de motivación y prueba y ponderar la proporcionalidad de la reacción sancionadora.\n"
        )
    else:
        extra = (
            "Dada la gravedad potencial atribuida, la exigencia de prueba técnica completa, trazabilidad e integridad/cadena de custodia del dato debe ser máxima, "
            "evitando fórmulas estereotipadas y aportando soporte documental verificable.\n"
        )

    m = re.search(r"^III\.\s*SOLICITO\b", body, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return body + "\n\n" + extra
    return body[:m.start()] + extra + "\n" + body[m.start():]


def _velocity_strict_validate(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    has_alegacion = bool(_first_alegacion_title(body))
    has_section = bool(re.search(r"^II\.\s*ALEGACIONES\b", body or "", re.IGNORECASE | re.MULTILINE))
    if not (has_alegacion or has_section):
        missing.append("estructura_alegaciones")
    if "margen" not in b:
        missing.append("margen")
    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")
    if not any(k in b for k in ["cinemómetro", "cinemometro", "radar"]):
        missing.append("cinemometro")

    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _strict_validate_or_raise(conn, case_id: str, tpl: Dict[str, str], final_kind: str) -> None:
    if final_kind != "velocidad":
        return
    body = (tpl or {}).get("cuerpo") or ""
    missing = _velocity_strict_validate(body)
    if missing:
        raise HTTPException(status_code=422, detail=f"Velocity Strict no cumplido. Faltan/errores: {missing}.")


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
        text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No hay extracción para ese case_id.")

    extracted_json = row[0]
    wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
    core = (wrapper.get("extracted") or {}) if isinstance(wrapper, dict) else {}

    # Congelar el hecho literal y el tipo desde analyze
literal = extract_hecho_literal(core)

if literal and not core.get("hecho_denunciado_literal"):
    core["hecho_denunciado_literal"] = literal

# No permitir que generate cambie el tipo decidido por analyze
if core.get("tipo_infraccion"):
    core["tipo_infraccion"] = core["tipo_infraccion"]

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    tpl: Optional[Dict[str, str]] = None
    ai_used = False
    ai_error: Optional[str] = None
    final_kind = "generic"

    decision_mode = "unknown"
    decision: Dict[str, Any] = {"mode": "unknown", "reasons": ["not_computed"]}

    # --------------------------
    # Dispatch helpers (planos, sin indentación frágil)
    # --------------------------
    def _dispatch(kind: str, asunto_seed: str = "", cuerpo_seed: str = "") -> Dict[str, str]:
        nonlocal final_kind, decision_mode, decision

        kind = (kind or "").strip().lower()

        if kind == "velocidad":
            asunto_v, cuerpo_v = _velocity_vse1_template(core)
            final_kind = "velocidad"
            try:
                decision = decide_modo_velocidad(core, body=cuerpo_v, capture_mode="UNKNOWN") or decision
                decision_mode = (decision.get("mode") or "unknown") if isinstance(decision, dict) else "unknown"
            except Exception:
                pass
            cuerpo_v = _inject_bucket_paragraph(cuerpo_v, decision)
            cuerpo_v = _inject_tramo_error_paragraph(cuerpo_v, _compute_velocity_calc_from_core(core))
            return {"asunto": asunto_v, "cuerpo": cuerpo_v}

        if kind == "semaforo":
            final_kind = "semaforo"
            return build_semaforo_strong_template(core)

        if kind == "movil":
            final_kind = "movil"
            return build_movil_strong_template(core)

        if kind == "auriculares":
            final_kind = "auriculares"
            return build_auriculares_strong_template(core)

        if kind == "atencion":
            final_kind = "atencion"
            return build_atencion_strong_template(core, body=cuerpo_seed or "")

        if kind == "condiciones_vehiculo":
            final_kind = "condiciones_vehiculo"
            return build_condiciones_vehiculo_strong_template(core)

        if kind == "seguro":
            final_kind = "seguro"
            return build_seguro_strong_template(core)

        if kind == "marcas_viales":
            final_kind = "marcas_viales"
            return build_marcas_viales_strong_template(core)

        final_kind = "generic"
        return {"asunto": asunto_seed or "ALEGACIONES — SOLICITA REVISIÓN DEL EXPEDIENTE", "cuerpo": cuerpo_seed or ""}

    def _dispatch_from_detectors(asunto_seed: str, cuerpo_seed: str) -> Dict[str, str]:
        # Orden quirúrgico, pero SOLO si no hay hard-lock claro
        if _is_velocity_context(core, cuerpo_seed):
            return _dispatch("velocidad", asunto_seed, cuerpo_seed)

        if _is_semaforo_context_robust(core, cuerpo_seed):
            return _dispatch("semaforo", asunto_seed, cuerpo_seed)

        if is_movil_context(core, cuerpo_seed):
            return _dispatch("movil", asunto_seed, cuerpo_seed)

        if is_auriculares_context(core, cuerpo_seed):
            return _dispatch("auriculares", asunto_seed, cuerpo_seed)

        if is_marcas_viales_context(core, _raw_blob(core)):
            return _dispatch("marcas_viales", asunto_seed, cuerpo_seed)

        if is_seguro_context(core, _raw_blob(core)):
            return _dispatch("seguro", asunto_seed, cuerpo_seed)

        if is_atencion_context(core, cuerpo_seed):
            return _dispatch("atencion", asunto_seed, cuerpo_seed)

        if _is_condiciones_context_robust(core, cuerpo_seed):
            return _dispatch("condiciones_vehiculo", asunto_seed, cuerpo_seed)

        final_kind_local = str(core.get("tipo_infraccion") or "").lower().strip()
        # fallback por tipo_infraccion si viene limpio
        if final_kind_local in ("semaforo", "movil", "auriculares", "atencion", "condiciones_vehiculo", "seguro", "marcas_viales", "velocidad"):
            return _dispatch(final_kind_local, asunto_seed, cuerpo_seed)

        return {"asunto": asunto_seed, "cuerpo": cuerpo_seed}

    # --------------------------
    # IA primero (si procede)
    # --------------------------
    if RTM_DGT_GENERATION_MODE != "TEMPLATES_ONLY":
        try:
            ai_result = run_expediente_ai(case_id)
            draft = (ai_result or {}).get("draft") or {}
            asunto = (draft.get("asunto") or "").strip()
            cuerpo = (draft.get("cuerpo") or "").strip()

            if asunto and cuerpo:
                if override_mode:
                    asunto = "RECURSO (MODO PRUEBA)"
                    cuerpo = _strip_borrador_prefix_from_body(cuerpo)

                # 1) Tipicidad hard lock
                locked = _expected_kind_from_article(core)
                if locked == "cluster_18":
                    # decide dentro del cluster 18
                    if is_movil_context(core, cuerpo):
                        tpl = _dispatch("movil", asunto, cuerpo)
                    elif is_auriculares_context(core, cuerpo):
                        tpl = _dispatch("auriculares", asunto, cuerpo)
                    else:
                        tpl = _dispatch("atencion", asunto, cuerpo)
                elif locked:
                    tpl = _dispatch(locked, asunto, cuerpo)
                else:
                    # 2) Detectores
                    tpl = _dispatch_from_detectors(asunto, cuerpo)

                ai_used = True

        except Exception as e:
            ai_error = str(e)
            tpl = None

    # --------------------------
    # Fallback a plantillas deterministas
    # --------------------------
    if not tpl:
        if tipo == "reposicion":
            tpl = build_dgt_reposicion_text(core, interesado)
        else:
            tpl = build_dgt_alegaciones_text(core, interesado)

        cuerpo0 = tpl.get("cuerpo") or ""
        asunto0 = tpl.get("asunto") or ""

        locked = _expected_kind_from_article(core)
        if locked == "cluster_18":
            if is_movil_context(core, cuerpo0):
                tpl = _dispatch("movil", asunto0, cuerpo0)
            elif is_auriculares_context(core, cuerpo0):
                tpl = _dispatch("auriculares", asunto0, cuerpo0)
            else:
                tpl = _dispatch("atencion", asunto0, cuerpo0)
        elif locked:
            tpl = _dispatch(locked, asunto0, cuerpo0)
        else:
            tpl = _dispatch_from_detectors(asunto0, cuerpo0)

    # STRICT (solo velocidad)
    try:
        _strict_validate_or_raise(conn, case_id, tpl, final_kind=final_kind)
    except HTTPException as e:
        if override_mode:
            try:
                conn.execute(
                    text(
                        "INSERT INTO events(case_id, type, payload, created_at) "
                        "VALUES (:case_id,'strict_bypassed_override',CAST(:payload AS JSONB),NOW())"
                    ),
                    {"case_id": case_id, "payload": json.dumps({"detail": str(e.detail), "final_kind": final_kind})},
                )
            except Exception:
                pass
        else:
            raise

    # DOCX/PDF persist
    kind_docx = "generated_docx_reposicion" if tipo == "reposicion" else "generated_docx_alegaciones"
    kind_pdf = "generated_pdf_reposicion" if tipo == "reposicion" else "generated_pdf_alegaciones"

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
            "VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
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
            "VALUES (:case_id,:kind,:b2_bucket,:b2_key,:mime,:size_bytes,NOW())"
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
        text("INSERT INTO events(case_id, type, payload, created_at) VALUES (:case_id,'resource_generated',CAST(:payload AS JSONB),NOW())"),
        {
            "case_id": case_id,
            "payload": json.dumps(
                {
                    "tipo": tipo,
                    "ai_used": ai_used,
                    "ai_error": ai_error,
                    "generation_mode": RTM_DGT_GENERATION_MODE,
                    "override_mode": override_mode,
                    "final_kind": final_kind,
                    "missing_interested_fields": _missing_interested_fields(interesado),
                    "velocity_decision_mode": decision_mode,
                    "velocity_decision": decision,
                }
            ),
        },
    )

    conn.execute(text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:case_id"), {"case_id": case_id})

    return {
        "ok": True,
        "case_id": case_id,
        "tipo": tipo,
        "final_kind": final_kind,
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
        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, tipo=req.tipo)
    return {"ok": True, "message": "Recurso generado en DOCX y PDF.", **result}