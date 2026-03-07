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
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado", "hecho_denunciado_literal"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts).lower()


def _normalize_text(s: Any) -> str:
    t = str(s or "").lower().strip()
    t = t.replace("semáforo", "semaforo")
    t = t.replace("línea", "linea")
    t = t.replace("vehículo", "vehiculo")
    t = t.replace("móvil", "movil")
    t = t.replace("teléfono", "telefono")
    t = t.replace("años", "anos")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _clean_literal_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.strip()

    t = re.sub(r"^\s*hecho denunciado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*hecho imputado\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*[\(\[]?\s*5[abc]\s*[\)\]]?\s*", "", t, flags=re.IGNORECASE)

    t = re.sub(r"\s*/\s*", " / ", t)
    t = re.sub(r"\s+", " ", t).strip(" :-\t")
    return t


def _is_probably_admin_line(line: str) -> bool:
    l = _normalize_text(line)
    admin_signals = [
        "importe multa",
        "importe con reduccion",
        "fecha limite",
        "lugar de denuncia",
        "puntos a detraer",
        "matricula",
        "marca y modelo",
        "clase vehiculo",
        "datos del vehic",
        "domicilio",
        "provincia",
        "codigo postal",
        "identificacion de la multa",
        "organo",
        "expediente",
        "fecha documento",
        "fecha notificacion",
        "hora",
        "via ",
        "punto km",
        "sentido",
        "titular",
        "boletin",
        "agente denunciante",
        "observaciones internas",
        "bonificacion",
        "reduccion",
        "entidad",
        "fecha decreto",
        "fecha caducidad",
        "importe para ingresar",
    ]
    return any(s in l for s in admin_signals)


def _looks_like_narrative_line(line: str) -> bool:
    l = _normalize_text(line)
    signals = [
        "conducir",
        "circular",
        "circulando",
        "circulaba",
        "no respetar",
        "no respeta",
        "utilizando",
        "bailando",
        "tocando",
        "golpeando",
        "auricular",
        "auriculares",
        "cascos",
        "luz roja",
        "fase roja",
        "marca longitudinal",
        "adelantamiento",
        "sin mantener",
        "atencion permanente",
        "atencion",
        "vehiculo resenado",
        "observado por agentes",
        "observado por agente",
        "interceptado",
        "menor de",
        "ciclistas",
        "arcen",
        "en paralelo",
        "conversando",
        "al realizar una maniobra",
        "tasa de alcohol",
        "alcohol",
        "carril adyacente",
        "libertad de movimientos",
        "mordia las",
        "mordia las unas",
    ]
    return any(k in l for k in signals)


def _extract_literal_from_blob(raw_text: str) -> str:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ""

    text = raw_text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)

    match = re.search(r"hecho denunciado\s*[:\-]?\s*", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"hecho que se notifica\s*[:\-]?\s*", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"hecho imputado\s*[:\-]?\s*", text, flags=re.IGNORECASE)
    if not match:
        return ""

    tail = text[match.end():].strip()
    if not tail:
        return ""

    lines = [ln.strip() for ln in tail.split("\n") if ln.strip()]
    if not lines:
        return ""

    collected: List[str] = []
    started = False

    for ln in lines:
        if _is_probably_admin_line(ln):
            if started:
                break
            continue

        if re.match(r"^\s*5[abc]\b", _normalize_text(ln)):
            started = True
            cleaned = re.sub(r"^\s*5[abc]\s*", "", ln, flags=re.IGNORECASE).strip()
            if cleaned:
                collected.append(cleaned)
            continue

        if not started:
            if _looks_like_narrative_line(ln):
                started = True
                collected.append(ln)
            continue

        collected.append(ln)

        if len(" ".join(collected)) > 700:
            break

    if not collected:
        return ""

    out = " / ".join(collected)
    out = _clean_literal_text(out)

    if len(out) < 30:
        return ""

    if len(out) > 650:
        out = out[:650].rsplit(" ", 1)[0].strip() + "…"

    return out.strip()


def _extract_hecho_literal_robust(core: Dict[str, Any]) -> str:
    core = core or {}

    for key in ("hecho_denunciado_literal",):
        val = core.get(key)
        if isinstance(val, str) and val.strip():
            cleaned = _clean_literal_text(val)
            if len(cleaned) >= 20:
                return cleaned

    for key in ("raw_text_pdf", "raw_text_vision", "raw_text_blob"):
        val = core.get(key)
        if isinstance(val, str) and val.strip():
            extracted = _extract_literal_from_blob(val)
            if extracted:
                return extracted

    hecho = core.get("hecho_imputado")
    if isinstance(hecho, str) and hecho.strip():
        cleaned = _clean_literal_text(hecho)
        if cleaned:
            return cleaned

    return ""


def _inject_hecho_literal(cuerpo: str, core: Dict[str, Any]) -> str:
    if not cuerpo:
        return cuerpo

    literal = core.get("hecho_denunciado_literal") or _extract_hecho_literal_robust(core)
    if not literal:
        return cuerpo

    if str(literal).lower() in cuerpo.lower():
        return cuerpo

    lines = cuerpo.splitlines()
    out: List[str] = []
    inserted = False

    for line in lines:
        out.append(line)
        if (not inserted) and re.match(r"\s*3[\)\.]?\s*Hecho imputado\s*:", line, flags=re.IGNORECASE):
            out.append(f"Extracto literal del boletín: “{literal}”")
            inserted = True

    if not inserted:
        final_lines: List[str] = []
        added = False
        for line in out:
            if (not added) and re.match(r"^\s*II\.\s*ALEGACIONES\b", line, flags=re.IGNORECASE):
                final_lines.append(f"Extracto literal del boletín: “{literal}”")
                final_lines.append("")
                added = True
            final_lines.append(line)
        out = final_lines

    return "\n".join(out)


def _augment_atencion_body(body: str, core: Dict[str, Any]) -> str:
    if not body:
        return body
    bnorm = _normalize_text(body)
    literal = _extract_hecho_literal_robust(core)
    lnorm = _normalize_text(literal)

    extras: List[str] = []

    if not any(k in bnorm for k in ["intervención tardía", "intervencion tardia"]):
        if any(k in lnorm for k in ["1.5 km", "1,5 km", "tramo", "hasta ser interceptado", "observado por agentes"]):
            extras.append(
                "ALEGACIÓN COMPLEMENTARIA — INTERVENCIÓN TARDÍA DEL AGENTE\n"
                "La propia denuncia afirma que la conducta fue observada durante un tramo antes de proceder a la interceptación del vehículo. "
                "Si existiera realmente un peligro inmediato y grave para la circulación, resultaría lógico que la intervención se produjera de forma prácticamente instantánea. "
                "La continuación de la marcha durante una distancia apreciable resulta difícilmente compatible con la existencia de un riesgo real e inminente."
            )

    if not any(k in bnorm for k in ["interior del vehículo", "interior del vehiculo", "condiciones de observación", "condiciones de observacion"]):
        if any(k in lnorm for k in ["bailando", "tocando las palmas", "golpeando", "volante", "tambor", "mordia", "libertad de movimientos"]):
            extras.append(
                "ALEGACIÓN COMPLEMENTARIA — OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO\n"
                "La denuncia describe conductas realizadas dentro del habitáculo del vehículo, pero no precisa desde qué posición se realizó la observación, "
                "a qué distancia, durante cuánto tiempo, ni con qué condiciones de visibilidad. Sin esos extremos no puede valorarse con seriedad la fiabilidad de la percepción atribuida al agente."
            )

    if not any(k in bnorm for k in ["presencia del menor", "menor en el vehículo", "menor en el vehiculo"]):
        if any(k in lnorm for k in ["menor de", "menor en asiento trasero", "menor de dos anos", "menor de dos años"]):
            extras.append(
                "ALEGACIÓN COMPLEMENTARIA — MENOR EN EL VEHÍCULO\n"
                "La mención a la presencia de un menor en el asiento trasero no basta por sí sola para integrar la infracción denunciada. "
                "No se concreta en qué momento se observó al menor, si la observación se produjo durante la marcha o tras la detención, ni si utilizaba sistema de retención infantil homologado. "
                "Sin esa concreción, dicha referencia no acredita por sí misma una situación objetiva de peligro."
            )

    if not extras:
        return body

    mm = re.search(r"^III\.\s*SOLICITO\b", body, flags=re.IGNORECASE | re.MULTILINE)
    extra_txt = "\n\n".join(extras).strip()
    if not mm:
        return body.rstrip() + "\n\n" + extra_txt
    return body[:mm.start()].rstrip() + "\n\n" + extra_txt + "\n\n" + body[mm.start():].lstrip()


def _augment_semaforo_body(body: str, core: Dict[str, Any]) -> str:
    if not body:
        return body
    bnorm = _normalize_text(body)

    extras: List[str] = []

    if "linea de detencion" not in bnorm:
        extras.append(
            "ALEGACIÓN COMPLEMENTARIA — LÍNEA DE DETENCIÓN Y MOMENTO DEL REBASE\n"
            "La infracción por semáforo en fase roja exige acreditar que el vehículo rebasó efectivamente la línea de detención cuando la fase roja estaba ya activada. "
            "No basta una referencia genérica al semáforo; debe concretarse la posición del vehículo respecto de la línea de detención en el instante relevante."
        )

    if "secuencia" not in bnorm and "fotogram" not in bnorm and "fotograf" not in bnorm:
        extras.append(
            "ALEGACIÓN COMPLEMENTARIA — SECUENCIA PROBATORIA COMPLETA\n"
            "Debe aportarse la secuencia completa de captación, ya sea mediante fotografías, fotogramas o grabación, que permita verificar de forma objetiva el instante del supuesto rebase y la fase semafórica concurrente."
        )

    if "sincronizacion" not in bnorm and "sincronización" not in bnorm:
        extras.append(
            "ALEGACIÓN COMPLEMENTARIA — SINCRONIZACIÓN Y DATOS TÉCNICOS DEL SISTEMA\n"
            "Si la denuncia se apoya en un sistema automático de captación, debe acreditarse su correcto funcionamiento, la sincronización horaria, la identificación del cruce, el carril afectado y el semáforo concreto aplicable al vehículo denunciado."
        )

    if not extras:
        return body

    mm = re.search(r"^III\.\s*SOLICITO\b", body, flags=re.IGNORECASE | re.MULTILINE)
    extra_txt = "\n\n".join(extras).strip()
    if not mm:
        return body.rstrip() + "\n\n" + extra_txt
    return body[:mm.start()].rstrip() + "\n\n" + extra_txt + "\n\n" + body[mm.start():].lstrip()


# ==========================
# TIPICIDAD HARD LOCK (estable)
# ==========================

def _is_velocity_context(core: Dict[str, Any], cuerpo: str) -> bool:
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
# ROUTING (sin nonlocal, estable)
# ==========================

def _dispatch_kind(kind: str, core: Dict[str, Any], asunto_seed: str = "", cuerpo_seed: str = "") -> Tuple[Dict[str, str], str, str, Dict[str, Any]]:
    kind = (kind or "").strip().lower()
    decision_mode = "unknown"
    decision: Dict[str, Any] = {"mode": "unknown", "reasons": ["not_computed"]}

    if kind == "velocidad":
        asunto_v, cuerpo_v = _velocity_vse1_template(core)
        try:
            decision = decide_modo_velocidad(core, body=cuerpo_v, capture_mode="UNKNOWN") or decision
            decision_mode = (decision.get("mode") or "unknown") if isinstance(decision, dict) else "unknown"
        except Exception:
            pass
        cuerpo_v = _inject_bucket_paragraph(cuerpo_v, decision)
        cuerpo_v = _inject_tramo_error_paragraph(cuerpo_v, _compute_velocity_calc_from_core(core))
        return {"asunto": asunto_v, "cuerpo": cuerpo_v}, "velocidad", decision_mode, decision

    if kind == "semaforo":
        tpl = build_semaforo_strong_template(core)
        tpl["cuerpo"] = _augment_semaforo_body(tpl.get("cuerpo") or "", core)
        return tpl, "semaforo", decision_mode, decision

    if kind == "movil":
        return build_movil_strong_template(core), "movil", decision_mode, decision

    if kind == "auriculares":
        return build_auriculares_strong_template(core), "auriculares", decision_mode, decision

    if kind == "atencion":
        tpl = build_atencion_strong_template(core, body=cuerpo_seed or "")
        tpl["cuerpo"] = _augment_atencion_body(tpl.get("cuerpo") or "", core)
        return tpl, "atencion", decision_mode, decision

    if kind == "condiciones_vehiculo":
        return build_condiciones_vehiculo_strong_template(core), "condiciones_vehiculo", decision_mode, decision

    if kind == "seguro":
        return build_seguro_strong_template(core), "seguro", decision_mode, decision

    if kind == "marcas_viales":
        return build_marcas_viales_strong_template(core), "marcas_viales", decision_mode, decision

    return {"asunto": asunto_seed or "ALEGACIONES — SOLICITA REVISIÓN DEL EXPEDIENTE", "cuerpo": cuerpo_seed or ""}, "generic", decision_mode, decision


def _dispatch_from_detectors(core: Dict[str, Any], asunto_seed: str, cuerpo_seed: str) -> Tuple[Dict[str, str], str, str, Dict[str, Any]]:
    if _is_velocity_context(core, cuerpo_seed):
        return _dispatch_kind("velocidad", core, asunto_seed, cuerpo_seed)

    if _is_semaforo_context_robust(core, cuerpo_seed):
        return _dispatch_kind("semaforo", core, asunto_seed, cuerpo_seed)

    if is_movil_context(core, cuerpo_seed):
        return _dispatch_kind("movil", core, asunto_seed, cuerpo_seed)

    if is_auriculares_context(core, cuerpo_seed):
        return _dispatch_kind("auriculares", core, asunto_seed, cuerpo_seed)

    if is_marcas_viales_context(core, _raw_blob(core)):
        return _dispatch_kind("marcas_viales", core, asunto_seed, cuerpo_seed)

    if is_seguro_context(core, _raw_blob(core)):
        return _dispatch_kind("seguro", core, asunto_seed, cuerpo_seed)

    if is_atencion_context(core, cuerpo_seed):
        return _dispatch_kind("atencion", core, asunto_seed, cuerpo_seed)

    if _is_condiciones_context_robust(core, cuerpo_seed):
        return _dispatch_kind("condiciones_vehiculo", core, asunto_seed, cuerpo_seed)

    final_kind_local = str(core.get("tipo_infraccion") or "").lower().strip()
    if final_kind_local in ("semaforo", "movil", "auriculares", "atencion", "condiciones_vehiculo", "seguro", "marcas_viales", "velocidad"):
        return _dispatch_kind(final_kind_local, core, asunto_seed, cuerpo_seed)

    return {"asunto": asunto_seed, "cuerpo": cuerpo_seed}, "generic", "unknown", {"mode": "unknown", "reasons": ["not_computed"]}


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

    literal = _extract_hecho_literal_robust(core)
    if literal and not core.get("hecho_denunciado_literal"):
        core["hecho_denunciado_literal"] = literal

    interesado_db = _load_interested_data_from_cases(conn, case_id)
    interesado = _merge_interesado(interesado or {}, interesado_db)

    flags = _load_case_flags(conn, case_id)
    override_mode = bool(flags.get("test_mode")) and bool(flags.get("override_deadlines"))

    if not tipo:
        tipo = "reposicion" if core.get("pone_fin_via_administrativa") is True else "alegaciones"

    tpl: Optional[Dict[str, str]] = None
    ai_used = False
    ai_error: Optional[str] = None
    final_kind = "generic"
    decision_mode = "unknown"
    decision: Dict[str, Any] = {"mode": "unknown", "reasons": ["not_computed"]}

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

                locked = _expected_kind_from_article(core)
                if locked == "cluster_18":
                    if is_movil_context(core, cuerpo):
                        tpl, final_kind, decision_mode, decision = _dispatch_kind("movil", core, asunto, cuerpo)
                    elif is_auriculares_context(core, cuerpo):
                        tpl, final_kind, decision_mode, decision = _dispatch_kind("auriculares", core, asunto, cuerpo)
                    else:
                        tpl, final_kind, decision_mode, decision = _dispatch_kind("atencion", core, asunto, cuerpo)
                elif locked:
                    tpl, final_kind, decision_mode, decision = _dispatch_kind(locked, core, asunto, cuerpo)
                else:
                    tpl, final_kind, decision_mode, decision = _dispatch_from_detectors(core, asunto, cuerpo)

                ai_used = True

        except Exception as e:
            ai_error = str(e)
            tpl = None

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
                tpl, final_kind, decision_mode, decision = _dispatch_kind("movil", core, asunto0, cuerpo0)
            elif is_auriculares_context(core, cuerpo0):
                tpl, final_kind, decision_mode, decision = _dispatch_kind("auriculares", core, asunto0, cuerpo0)
            else:
                tpl, final_kind, decision_mode, decision = _dispatch_kind("atencion", core, asunto0, cuerpo0)
        elif locked:
            tpl, final_kind, decision_mode, decision = _dispatch_kind(locked, core, asunto0, cuerpo0)
        else:
            tpl, final_kind, decision_mode, decision = _dispatch_from_detectors(core, asunto0, cuerpo0)

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

    kind_docx = "generated_docx_reposicion" if tipo == "reposicion" else "generated_docx_alegaciones"
    kind_pdf = "generated_pdf_reposicion" if tipo == "reposicion" else "generated_pdf_alegaciones"

    tpl["cuerpo"] = _inject_hecho_literal(tpl.get("cuerpo") or "", core)

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
