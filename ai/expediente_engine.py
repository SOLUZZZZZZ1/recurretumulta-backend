# ai/expediente_engine.py
# RTM — VELOCIDAD PRO (Referencia Nacional)
# - Determinismo (temperature=0.0)
# - Primary determinista (velocidad no nace con presunción de inocencia)
# - Auto-repair (1 intento) si el borrador no cumple mínimos VSE-1
# - VELOCIDAD_PRO_ENRICH: inyección determinista de módulos técnicos si el borrador es corto/flojo
# - Petitum forzado: en VELOCIDAD siempre ARCHIVO
# - Párrafo de cálculo (margen/velocidad corregida/banda) obligatorio si velocity_calc.ok

import json
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from database import get_engine
from openai import OpenAI

from ai.text_loader import load_text_from_b2
from ai.prompts.classify_documents import PROMPT as PROMPT_CLASSIFY
from ai.prompts.timeline_builder import PROMPT as PROMPT_TIMELINE
from ai.prompts.procedure_phase import PROMPT as PROMPT_PHASE
from ai.prompts.admissibility_guard import PROMPT as PROMPT_GUARD
from ai.prompts.draft_recurso_v2 import PROMPT as PROMPT_DRAFT
from ai.prompts.module_semaforo import module_semaforo

MAX_EXCERPT_CHARS = 12000


# ==========================
# REPAIR PROMPT (VELOCIDAD)
# ==========================

PROMPT_DRAFT_REPAIR_VELOCIDAD = """
Eres abogado especialista en Derecho Administrativo Sancionador (España). Debes REPARAR un borrador de alegaciones por EXCESO DE VELOCIDAD.

OBJETIVO: reescribir el borrador COMPLETO para que pase una validación estricta.

REGLAS OBLIGATORIAS:
1) La PRIMERA ALEGACIÓN NO puede ser 'Presunción de inocencia'.
2) La PRIMERA ALEGACIÓN debe titularse exactamente:
   'ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)'
3) El cuerpo debe contener literalmente: 'cadena de custodia'.
4) Debe incluir: 'margen' y 'velocidad corregida'.
5) Debe exigir 'certificado' y 'verificación' (metrológica) del cinemómetro.
6) Debe exigir 'captura' o 'fotograma' completo.
7) El SOLICITO en velocidad debe pedir ARCHIVO (no "revisión").
8) No inventes hechos. Mantén prudencia: 'no consta acreditado', 'no se aporta', 'no resulta acreditado'.

ENTRADA: JSON con borrador anterior y contexto.
SALIDA: SOLO JSON con la forma {asunto,cuerpo,variables_usadas,checks,notes_for_operator}.
"""


# ==========================
# UTILIDADES VSE / SVL
# ==========================

def _velocity_strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")

    first = ""
    for line in (body or "").splitlines():
        l = (line or "").strip()
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            first = l.lower()
            break
    if first and ("presunción" in first or "presuncion" in first or "inocencia" in first):
        missing.append("orden_alegaciones")

    required = {
        "margen": ["margen"],
        "velocidad_corregida": ["velocidad corregida", "corregida"],
        "metrologia": ["certificado", "verificación", "verificacion", "metrológ", "metrolog", "control metrológico", "control metrologico"],
        "cinemometro": ["cinemómetro", "cinemometro", "radar"],
        "captura": ["captura", "fotograma", "imagen"],
    }
    for key, needles in required.items():
        if not any(n in b for n in needles):
            missing.append(key)

    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _force_archivo_in_speed_body(body: str) -> str:
    """Sustituye 'revisión' por 'ARCHIVO' en velocidad."""
    if not body:
        return body

    reps = [
        ("Que se acuerde la revisión del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la revisión del Expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del Expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del Expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del Expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la revisión del expediente por insuficiencia probatoria", "Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria"),
    ]
    for a, b in reps:
        body = body.replace(a, b)

    # Si aparece "revisión exhaustiva", no tocar
    return body


def _ensure_speed_antecedentes(body: str, velocity_calc: Dict[str, Any]) -> str:
    """Fuerza 'Hecho imputado' con (XXX km/h) si disponemos de la medida."""
    try:
        if not body:
            return body
        if not (velocity_calc or {}).get("ok"):
            return body
        measured = velocity_calc.get("measured")
        if not isinstance(measured, int):
            return body
        body = re.sub(
            r"(Hecho imputado:\s*EXCESO DE VELOCIDAD)\s*(?:\([^)]+\))?\s*\.",
            rf"\1 ({measured} km/h).",
            body,
            flags=re.IGNORECASE,
        )
        return body
    except Exception:
        return body


def _ensure_velocity_calc_paragraph(body: str, velocity_calc: Dict[str, Any]) -> str:
    """Inserta SIEMPRE un párrafo de cálculo si velocity_calc.ok y no existe ya."""
    try:
        if not body:
            return body
        vc = velocity_calc or {}
        if not vc.get("ok"):
            return body

        # ya existe
        if "a efectos ilustrativos" in body.lower() and "velocidad corregida" in body.lower():
            return body

        limit = vc.get("limit")
        measured = vc.get("measured")
        margin = vc.get("margin_value")
        corrected = vc.get("corrected")
        expected = vc.get("expected") or {}
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")

        parts = ["A efectos ilustrativos,"]
        if isinstance(limit, int) and isinstance(measured, int):
            parts.append(f"con un límite de {limit} km/h y una medición de {measured} km/h,")
        if isinstance(margin, (int, float)) and isinstance(corrected, (int, float)):
            parts.append(f"la aplicación del margen situaría la velocidad corregida en torno a {float(corrected):.2f} km/h,")
        parts.append("extremo cuya acreditación corresponde a la Administración (margen aplicado, velocidad corregida y banda/tramo resultante).")
        if band:
            tail = f"De acuerdo con la tabla orientativa, ello podría encajar en la banda: {band}"
            if isinstance(fine, int) or isinstance(pts, int):
                tail += f" (multa {fine}€ / puntos {pts})."
            else:
                tail += "."
            parts.append(tail)

        paragraph = " ".join(parts)

        # Insertar tras el título de la Alegación Primera si existe, si no antes del Solicito
        m = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if m:
            i = m.end(1)
            return body[:i] + paragraph + "\n" + body[i:]
        return re.sub(r"(\nIII\.\s*SOLICITO)", "\n" + paragraph + r"\n\1", body, flags=re.IGNORECASE)

    except Exception:
        return body


def _velocity_pro_enrich(body: str, velocity_calc: Dict[str, Any]) -> str:
    """
    ENRIQUECIMIENTO DETERMINISTA (VELOCIDAD):
    - Si el borrador es corto o no contiene checklist completo, inyecta módulos técnicos.
    - Garantiza: Orden ICT/155/2020, control metrológico, checklist 1–7, cadena de custodia, captura, margen/velocidad corregida,
      bloque de limitación/señalización, y SOLICITO de ARCHIVO.
    """
    if not body:
        return body

    b = body.lower()
    too_short = len(body.strip()) < 1400  # umbral práctico para evitar "minirrecurso"
    must_have = ["orden ict/155/2020", "control metrol", "cadena de custodia", "velocidad corregida", "margen", "cinemómetro", "certificado", "verificación", "captura"]
    missing_key = any(k not in b for k in must_have)

    # Si ya es largo y completo, no meter “ruido”
    if not (too_short or missing_key):
        return body

    # Construimos un bloque PRO (se inserta dentro de alegaciones, no como texto suelto)
    calc_sentence = ""
    if (velocity_calc or {}).get("ok"):
        corrected = velocity_calc.get("corrected")
        limit = velocity_calc.get("limit")
        measured = velocity_calc.get("measured")
        expected = (velocity_calc.get("expected") or {})
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")
        if isinstance(corrected, (int, float)):
            calc_sentence = f"A efectos ilustrativos, la aplicación del margen legal podría situar la velocidad corregida en torno a {float(corrected):.2f} km/h."
        if isinstance(limit, int) and isinstance(measured, int):
            calc_sentence = f"A efectos ilustrativos, con un límite de {limit} km/h y una medición de {measured} km/h, la aplicación del margen legal podría situar la velocidad corregida en torno a {float(corrected):.2f} km/h."
        if band:
            calc_sentence += f" Ello podría encajar en la banda: {band}"
            if isinstance(fine, int) or isinstance(pts, int):
                calc_sentence += f" (multa {fine}€ / puntos {pts})."
            else:
                calc_sentence += "."

    pro_block = f"""
ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)

La validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación documental del control metrológico conforme a la normativa aplicable (Orden ICT/155/2020). No basta una afirmación genérica de verificación: debe aportarse soporte documental verificable.

No consta acreditado en el expediente:

1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y emplazamiento exacto (vía, punto kilométrico y sentido).
2) Certificado de verificación metrológica vigente a la fecha del hecho, así como constancia de la última verificación periódica o, en su caso, tras reparación.
3) Captura o fotograma COMPLETO, sin recortes y legible, que permita asociar inequívocamente la medición al vehículo denunciado.
4) Margen aplicado y determinación de la velocidad corregida (velocidad medida vs velocidad corregida), con motivación técnica suficiente.
5) Acreditación de la cadena de custodia del dato (integridad del registro, sistema de almacenamiento y correspondencia inequívoca con el vehículo).
6) Acreditación del límite aplicable y su señalización en el punto exacto (genérica vs específica) y su coherencia con la ubicación consignada.
7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y tramo sancionador resultante.

{calc_sentence if calc_sentence else "A efectos ilustrativos, la aplicación del margen legal puede alterar la velocidad corregida y, por tanto, el tramo sancionador; corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda/tramo resultante."}

ALEGACIÓN SEGUNDA — INSUFICIENCIA PROBATORIA Y MOTIVACIÓN TÉCNICA

La falta de documentación técnica impide verificar la fiabilidad del instrumento, la integridad del dato y la correcta determinación del tramo sancionador. La motivación no puede ser estereotipada: debe permitir comprobar la coherencia entre medición, margen aplicado, velocidad corregida y sanción/puntos, evitando indefensión.

ALEGACIÓN TERCERA — PRESUNCIÓN DE INOCENCIA (REFUERZO)

En virtud del artículo 24 CE, la carga de la prueba corresponde a la Administración. Ante la insuficiencia probatoria descrita, no puede considerarse acreditada la infracción en términos exigibles.

"""

    # Insertar/normalizar:
    # Si ya existe ALEGACIÓN PRIMERA, no duplicar título; en ese caso, añadimos solo el “cuerpo pro” después del primer título.
    if re.search(r"ALEGACIÓN\s+PRIMERA", body, flags=re.IGNORECASE):
        # Si ya hay 'Orden ICT/155/2020' y checklist, no meter todo. Si falta, inserta el bloque PRO justo después del título.
        if "orden ict/155/2020" in b and "1)" in b and "2)" in b and "cadena de custodia" in b:
            return body
        m = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if m:
            i = m.end(1)
            # Insertar el cuerpo pro (sin repetir el título)
            pro_body_only = "\n".join(pro_block.strip().splitlines()[2:]).strip()  # quita título y línea en blanco
            return body[:i] + pro_body_only + "\n" + body[i:]
        return body

    # Si no hay alegaciones estructuradas, insertamos bloque completo antes de SOLICITO
    body = re.sub(r"(\nIII\.\s*SOLICITO)", "\n" + pro_block.strip() + r"\n\1", body, flags=re.IGNORECASE)
    return body


# ==========================
# OVERRIDE MODE
# ==========================

def _override_mode() -> str:
    m = (os.getenv("RTM_OVERRIDE_MODE") or "TEST_REALISTA").strip().upper()
    if m not in ("TEST_REALISTA", "SANDBOX_DEMO"):
        m = "TEST_REALISTA"
    return m


def _sanitize_for_sandbox_demo(attack_plan: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    sec = plan.get("secondary") or []
    if isinstance(sec, list):
        sec2 = []
        for item in sec:
            title = (item or {}).get("title") if isinstance(item, dict) else ""
            tl = (title or "").lower()
            if any(k in tl for k in ["antigüedad", "actos interrupt", "firmeza", "notificación válida", "notificacion valida"]):
                continue
            sec2.append(item)
        plan["secondary"] = sec2
    plan.setdefault("meta", {})
    plan["meta"]["sandbox_demo_sanitized"] = True
    return plan


# ==========================
# VSE-1: cálculo velocidad
# ==========================

def _extract_speed_pair_from_blob(blob: str) -> Dict[str, Any]:
    t = (blob or "").replace("\n", " ").lower()
    m_meas = re.search(r"circular\s+a\s+(\d{2,3})\s*km\s*/?h", t) or re.search(r"\b(\d{2,3})\s*km\s*/?h\b", t)
    m_lim = (
        re.search(r"(?:limitad[ao]a?|l[ií]mit[eé])\s*(?:la\s*velocidad\s*)?(?:a\s*)?(\d{2,3})\s*km\s*/?h", t)
        or re.search(r"estando\s+limitad[ao]a?\s+la\s+velocidad\s+a\s+(\d{2,3})\s*km\s*/?h", t)
    )
    out: Dict[str, Any] = {"measured": None, "limit": None, "confidence": 0.0}
    try:
        if m_meas:
            out["measured"] = int(m_meas.group(1))
        if m_lim:
            out["limit"] = int(m_lim.group(1))
    except Exception:
        pass
    conf = 0.0
    if out["measured"] is not None:
        conf += 0.4
    if out["limit"] is not None:
        conf += 0.4
    if out["measured"] and out["limit"] and (20 <= out["limit"] <= 130) and (out["measured"] >= out["limit"]):
        conf += 0.2
    out["confidence"] = round(conf, 2)
    return out


def _speed_margin_value(measured: int, capture_mode: str) -> float:
    cm = (capture_mode or "").upper()
    mobile = (cm in ("MOBILE", "MOVING", "VEHICLE", "AGENT"))
    if measured <= 100:
        return 7.0 if mobile else 5.0
    pct = 0.07 if mobile else 0.05
    return round(measured * pct, 2)


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
    v = int(round(corrected))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}


def _compute_velocity_calc(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], capture_mode: str) -> Dict[str, Any]:
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass
    for d in docs or []:
        if d.get("text_excerpt"):
            blob_parts.append(d["text_excerpt"])
    blob = "\n".join(blob_parts)

    pair = _extract_speed_pair_from_blob(blob)
    measured = pair.get("measured")
    limit = pair.get("limit")
    if not measured or not limit:
        return {"ok": False, "reason": "No se pudieron extraer velocidades de forma fiable.", "raw": pair}

    margin = _speed_margin_value(int(measured), capture_mode)
    corrected = max(0.0, float(measured) - float(margin))
    expected = _expected_speed_sanction(int(limit), corrected)

    return {
        "ok": True,
        "limit": int(limit),
        "measured": int(measured),
        "capture_mode_for_margin": ("MOBILE" if (capture_mode or "").upper() == "AGENT" else (capture_mode or "UNKNOWN")),
        "margin_value": margin,
        "corrected": round(corrected, 2),
        "expected": expected,
        "extraction_confidence": pair.get("confidence", 0.0),
    }


# ==========================
# LLM + DB HELPERS
# ==========================

def _llm_json(prompt: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _save_event(case_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
            ),
            {"case_id": case_id, "type": event_type, "payload": json.dumps(payload)},
        )


def _load_latest_extraction(case_id: str) -> Optional[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
            {"case_id": case_id},
        ).fetchone()
    return row[0] if row else None


def _load_interested_data(case_id: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return (row[0] if row and row[0] else {}) or {}


def _load_case_flags(case_id: str) -> Dict[str, bool]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) FROM cases WHERE id=:id"),
            {"id": case_id},
        ).fetchone()
    return {"test_mode": bool(row[0]) if row else False, "override_deadlines": bool(row[1]) if row else False}


def _load_case_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT kind, b2_bucket, b2_key, mime, size_bytes, created_at "
                "FROM documents WHERE case_id=:case_id ORDER BY created_at ASC"
            ),
            {"case_id": case_id},
        ).fetchall()

    docs: List[Dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        kind, bucket, key, mime, size_bytes, created_at = r
        text_excerpt = load_text_from_b2(bucket, key, mime)
        if text_excerpt:
            text_excerpt = text_excerpt[:MAX_EXCERPT_CHARS]
        docs.append(
            {
                "doc_index": i,
                "kind": kind,
                "bucket": bucket,
                "key": key,
                "mime": mime,
                "size_bytes": int(size_bytes or 0),
                "created_at": str(created_at),
                "text_excerpt": text_excerpt or "",
            }
        )
    return docs


# ==========================
# SIGNALS
# ==========================

def _detect_capture_mode(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]]) -> str:
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False))
    except Exception:
        pass
    for d in docs or []:
        t = (d.get("text_excerpt") or "")
        if t:
            blob_parts.append(t)
    blob = "\n".join(blob_parts).lower()

    auto_signals = [
        "cámara", "camara", "fotograma", "fotogramas", "secuencia", "foto", "fotografía", "fotografia",
        "captación automática", "captacion automatica", "sistema automático", "sistema automatico",
        "dispositivo", "sensor", "instalación", "instalacion", "vídeo", "video"
    ]
    agent_signals = [
        "agente", "policía", "policia", "guardia civil", "denunciante", "observó", "observo",
        "manifestó", "manifesto", "presencial", "in situ"
    ]

    auto_score = sum(1 for s in auto_signals if s in blob)
    agent_score = sum(1 for s in agent_signals if s in blob)

    if auto_score >= 2 and auto_score >= agent_score + 1:
        return "AUTO"
    if agent_score >= 2 and agent_score >= auto_score + 1:
        return "AGENT"
    return "UNKNOWN"


def _infer_infraction_from_facts_phrases(classify: Dict[str, Any]) -> Optional[str]:
    phrases = (classify or {}).get("facts_phrases") or []
    if not phrases:
        return None
    joined = "\n".join([str(p) for p in phrases if p]).lower()
    if any(s in joined for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
        return "semaforo"
    if any(s in joined for s in ["móvil", "movil", "teléfono", "telefono"]):
        return "movil"
    if any(s in joined for s in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"]):
        return "velocidad"
    return None


def _has_semaforo_signals(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], classify: Optional[Dict[str, Any]] = None) -> bool:
    phrases = (classify or {}).get("facts_phrases") or []
    for p in phrases:
        pl = (p or "").lower()
        if any(s in pl for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            return True
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False).lower())
    except Exception:
        pass
    for d in docs or []:
        blob_parts.append((d.get("text_excerpt") or "").lower())
    blob = "\n".join(blob_parts)
    return any(s in blob for s in ["semáforo", "semaforo", "fase roja", "no respetar la luz roja", "circular con luz roja"])


def _build_facts_summary(extraction_core: Optional[Dict[str, Any]], infraction_type: str) -> str:
    try:
        hecho = (extraction_core or {}).get("hecho_imputado")
        if isinstance(hecho, str) and hecho.strip():
            return hecho.strip()
    except Exception:
        pass
    # fallback
    if infraction_type == "velocidad":
        return "Exceso de velocidad."
    return ""


# ==========================
# ATTACK PLAN (PRIMARY DETERMINISTA)
# ==========================

def _build_attack_plan(classify: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    inferred = _infer_infraction_from_facts_phrases(classify) or "generic"

    if inferred == "generic":
        if any(s in blob for s in ["km/h", "radar", "cinemómetro", "cinemometro", "velocidad"]):
            inferred = "velocidad"
        elif any(s in blob for s in ["teléfono", "telefono", "móvil", "movil"]):
            inferred = "movil"
        elif any(s in blob for s in ["semáforo", "semaforo", "fase roja"]):
            inferred = "semaforo"

    primary = {"title": "Insuficiencia probatoria específica", "points": []}
    if inferred == "velocidad":
        primary = {"title": "Prueba técnica, metrología y cadena de custodia (cinemómetro)", "points": []}

    return {
        "infraction_type": inferred,
        "primary": primary,
        "secondary": [],
        "proof_requests": [],
        "petition": {
            "main": "Archivo / estimación íntegra",
            "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
        },
    }


def _compute_context_intensity(extraction_core: Dict[str, Any], infraction_type: str) -> str:
    if infraction_type == "velocidad":
        return "critico"
    return "normal"


# ==========================
# MAIN
# ==========================

def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    extraction_wrapper = _load_latest_extraction(case_id) or {}
    extraction_core = (extraction_wrapper.get("extracted") or {}) if isinstance(extraction_wrapper, dict) else {}

    capture_mode = _detect_capture_mode(docs, extraction_core)

    classify = _llm_json(PROMPT_CLASSIFY, {"case_id": case_id, "documents": docs, "latest_extraction": extraction_wrapper})
    timeline = _llm_json(PROMPT_TIMELINE, {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": extraction_wrapper})
    phase = _llm_json(PROMPT_PHASE, {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": extraction_wrapper})

    admissibility = _llm_json(
        PROMPT_GUARD,
        {"case_id": case_id, "recommended_action": phase, "timeline": timeline, "classification": classify, "latest_extraction": extraction_wrapper},
    )

    flags = _load_case_flags(case_id)
    override_mode = _override_mode()
    if flags.get("test_mode") and flags.get("override_deadlines"):
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["override_applied"] = True
        admissibility["override_mode"] = override_mode

    force_semaforo = _has_semaforo_signals(docs, extraction_core, classify)

    if force_semaforo:
        sem = module_semaforo()
        attack_plan = {
            "infraction_type": "semaforo",
            "primary": {"title": (sem.get("primary_attack") or {}).get("title") or "Insuficiencia probatoria", "points": (sem.get("primary_attack") or {}).get("points") or []},
            "secondary": [{"title": sa.get("title"), "points": sa.get("points") or []} for sa in (sem.get("secondary_attacks") or [])],
            "proof_requests": sem.get("proof_requests") or [],
            "petition": {"main": "Archivo / estimación íntegra", "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa"},
            "meta": {"capture_mode": capture_mode, "forced": True},
        }
    else:
        attack_plan = _build_attack_plan(classify, extraction_core or {})

    infraction_type = (attack_plan or {}).get("infraction_type") or "generic"
    facts_summary = _build_facts_summary(extraction_core, infraction_type)
    context_intensity = _compute_context_intensity(extraction_core, infraction_type)

    # VSE-1
    velocity_calc: Dict[str, Any] = {}
    try:
        if infraction_type == "velocidad":
            velocity_calc = _compute_velocity_calc(docs, extraction_core, capture_mode)
    except Exception:
        velocity_calc = {"ok": False, "reason": "error_interno_velocity_calc"}

    draft = None
    if bool(admissibility.get("can_generate_draft")) or (admissibility.get("admissibility") or "").upper() == "ADMISSIBLE":
        interested_data = _load_interested_data(case_id)

        draft = _llm_json(
            PROMPT_DRAFT,
            {
                "case_id": case_id,
                "interested_data": interested_data,
                "classification": classify,
                "timeline": timeline,
                "recommended_action": phase,
                "admissibility": admissibility,
                "latest_extraction": extraction_wrapper,
                "extraction_core": extraction_core,
                "attack_plan": attack_plan,
                "facts_summary": facts_summary,
                "context_intensity": context_intensity,
                "velocity_calc": velocity_calc,
                "sandbox": {"override_applied": bool(admissibility.get("override_applied")), "override_mode": admissibility.get("override_mode")},
            },
        )

        # POSTPROCESADO DETERMINISTA (VELOCIDAD)
        try:
            if isinstance(draft, dict) and infraction_type == "velocidad":
                cuerpo = draft.get("cuerpo") or ""
                cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
                cuerpo = _force_archivo_in_speed_body(cuerpo)
                draft["cuerpo"] = cuerpo
        except Exception:
            pass

        # AUTO-REPAIR (1 intento) si no cumple mínimos
        try:
            if isinstance(draft, dict) and infraction_type == "velocidad":
                missing = _velocity_strict_missing(draft.get("cuerpo") or "")
                if missing:
                    _save_event(case_id, "draft_repair_triggered", {"missing": missing})
                    draft = _llm_json(
                        PROMPT_DRAFT_REPAIR_VELOCIDAD,
                        {
                            "case_id": case_id,
                            "missing": missing,
                            "previous_draft": draft,
                            "attack_plan": attack_plan,
                            "facts_summary": facts_summary,
                            "context_intensity": context_intensity,
                            "velocity_calc": velocity_calc,
                            "latest_extraction": extraction_wrapper,
                            "classification": classify,
                            "timeline": timeline,
                            "admissibility": admissibility,
                        },
                    )
                    if isinstance(draft, dict):
                        cuerpo = draft.get("cuerpo") or ""
                        cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                        cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                        cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
                        cuerpo = _force_archivo_in_speed_body(cuerpo)
                        draft["cuerpo"] = cuerpo
        except Exception as _e:
            _save_event(case_id, "draft_repair_failed", {"error": str(_e)})

    result = {
        "ok": True,
        "case_id": case_id,
        "classify": classify,
        "timeline": timeline,
        "phase": phase,
        "admissibility": admissibility,
        "attack_plan": attack_plan,
        "draft": draft,
        "capture_mode": capture_mode,
        "facts_summary": facts_summary,
        "context_intensity": context_intensity,
        "velocity_calc": velocity_calc,
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
