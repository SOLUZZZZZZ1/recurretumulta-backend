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

PROMPT_DRAFT_REPAIR_VELOCIDAD = """
Eres abogado especialista en sancionador (España). Debes REPARAR un borrador de recurso por EXCESO DE VELOCIDAD.

OBJETIVO: reescribir el borrador COMPLETO para que pase una validación estricta.

REGLAS OBLIGATORIAS:
1) La PRIMERA ALEGACIÓN NO puede ser 'Presunción de inocencia'.
2) La PRIMERA ALEGACIÓN debe titularse exactamente:
   'ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)'
3) El cuerpo debe contener literalmente la expresión: 'cadena de custodia'.
4) Debe incluir 'margen' y 'velocidad corregida'.
5) Debe exigir 'certificado' y 'verificación' (metrológica) del cinemómetro.
6) Debe exigir 'captura' o 'fotograma' completo.
7) No inventes hechos. Mantén prudencia: 'no consta acreditado', 'no se aporta'.

ENTRADA: JSON con borrador anterior y contexto.
SALIDA: SOLO JSON con la misma forma {asunto,cuerpo,variables_usadas,checks,notes_for_operator}.
"""

def _velocity_strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")
    # primera alegación
    first = ""
    for line in (body or "").splitlines():
        l = (line or "").strip()
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            first = l.lower()
            break
    if first and ("presunción" in first or "presuncion" in first or "inocencia" in first):
        missing.append("orden_alegaciones")
    # mínimos VSE
    for key, needles in {
        "margen": ["margen"],
        "velocidad_corregida": ["velocidad corregida", "corregida"],
        "metrologia": ["certificado", "verificación", "verificacion"],
        "cinemometro": ["cinemómetro", "cinemometro", "radar"],
        "captura": ["captura", "fotograma", "imagen"],
    }.items():
        if not any(n in b for n in needles):
            missing.append(key)
    # unique
    seen=set(); out=[]
    for x in missing:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def _fix_solicito_newline(body: str) -> str:
    if not body:
        return body
    return re.sub(r"(III\.\s*SOLICITO)\s*(?=1\))", r"\1\n", body, flags=re.IGNORECASE)


def _force_archivo_in_speed_body(body: str) -> str:
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
    ]
    for a, b in reps:
        body = body.replace(a, b)
    return body


def _ensure_speed_antecedentes(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if not body or not (velocity_calc or {}).get("ok"):
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
    try:
        if not body or not (velocity_calc or {}).get("ok"):
            return body
        if "a efectos ilustrativos" in body.lower() and "velocidad corregida" in body.lower():
            return body

        limit = velocity_calc.get("limit")
        measured = velocity_calc.get("measured")
        corrected = velocity_calc.get("corrected")
        expected = velocity_calc.get("expected") or {}
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")

        parts = ["A efectos ilustrativos,"]
        if isinstance(limit, int) and isinstance(measured, int):
            parts.append(f"con un límite de {limit} km/h y una medición de {measured} km/h,")
        if isinstance(corrected, (int, float)):
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
        mm = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if mm:
            i = mm.end(1)
            return body[:i] + paragraph + "\n" + body[i:]
        return re.sub(r"(\nIII\.\s*SOLICITO)", "\n" + paragraph + r"\n\1", body, flags=re.IGNORECASE)
    except Exception:
        return body


def _velocity_pro_enrich(body: str, velocity_calc: Dict[str, Any]) -> str:
    if not body:
        return body
    b = body.lower()
    too_short = len(body.strip()) < 1400
    must_have = ["orden ict/155/2020", "control metrol", "cadena de custodia", "velocidad corregida", "margen", "cinemómetro", "certificado", "verificación", "captura"]
    missing_key = any(k not in b for k in must_have)
    if not (too_short or missing_key):
        return body

    calc_sentence = ""
    if (velocity_calc or {}).get("ok"):
        corrected = velocity_calc.get("corrected")
        limit = velocity_calc.get("limit")
        measured = velocity_calc.get("measured")
        expected = (velocity_calc.get("expected") or {})
        band = expected.get("band")
        fine = expected.get("fine")
        pts = expected.get("points")
        if isinstance(limit, int) and isinstance(measured, int) and isinstance(corrected, (int, float)):
            calc_sentence = f"A efectos ilustrativos, con un límite de {limit} km/h y una medición de {measured} km/h, la aplicación del margen legal podría situar la velocidad corregida en torno a {float(corrected):.2f} km/h."
            if band:
                calc_sentence += f" Ello podría encajar en la banda: {band}"
                if isinstance(fine, int) or isinstance(pts, int):
                    calc_sentence += f" (multa {fine}€ / puntos {pts})."
                else:
                    calc_sentence += "."
        else:
            calc_sentence = "A efectos ilustrativos, la aplicación del margen legal puede alterar la velocidad corregida y, por tanto, el tramo sancionador; corresponde a la Administración acreditar margen aplicado, velocidad corregida y banda/tramo resultante."

    # ==========================
    # VELOCIDAD_PRO_ENRICH 2.0 (PARCHE NUEVO)
    # ==========================
    pro_body = f"""\nLa validez de una sanción por exceso de velocidad basada en cinemómetro exige la acreditación documental íntegra del control metrológico del instrumento, conforme a la normativa de control metrológico aplicable (Orden ICT/155/2020), así como el cumplimiento de exigencias de trazabilidad y verificación periódica de instrumentos de medida en servicio.\n\nNo basta una afirmación estereotipada de “verificación vigente”. La Administración debe acreditar documentalmente:\n\n1) Identificación completa del cinemómetro utilizado (marca, modelo y número de serie) y su emplazamiento exacto (vía, punto kilométrico y sentido de circulación).\n2) Certificado de verificación metrológica vigente en la fecha del hecho, incluyendo fecha de última verificación periódica o, en su caso, tras reparación.\n3) Captura o fotograma COMPLETO, sin recortes ni superposiciones, que permita asociar inequívocamente la medición al vehículo denunciado.\n4) Margen aplicado conforme al tipo de instalación (fija o móvil) y determinación expresa de la velocidad corregida.\n5) Acreditación de la cadena de custodia del dato desde su captación hasta su incorporación al expediente sancionador.\n6) Acreditación del límite aplicable y su señalización concreta en el punto kilométrico indicado.\n7) Motivación técnica individualizada que vincule medición, margen aplicado, velocidad corregida y tramo sancionador resultante.\n\nLa ausencia de cualquiera de estos elementos impide considerar acreditado el hecho en términos constitucionalmente exigibles, produciéndose vulneración del principio de legalidad sancionadora y del derecho a la presunción de inocencia.\n\n{calc_sentence}\n\nEn consecuencia, sin acreditación técnica suficiente, no puede tenerse por válidamente probado el exceso imputado.\n"""

    if re.search(r"ALEGACIÓN\s+PRIMERA", body, flags=re.IGNORECASE):
        if "orden ict/155/2020" in b and "1)" in b and "7)" in b and "cadena de custodia" in b:
            return body
        mm = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if mm:
            i = mm.end(1)
            return body[:i] + pro_body.strip() + "\n" + body[i:]
        return body

    return re.sub(r"(\nIII\.\s*SOLICITO)", "\nALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)\n" + pro_body.strip() + r"\n\1", body, flags=re.IGNORECASE)


def _normalize_velocity_titles_and_remove_tipicity(body: str, attack_plan: Dict[str, Any]) -> str:
    if not body:
        return body
    inf = ((attack_plan or {}).get("infraction_type") or "").lower().strip()
    meta = (attack_plan or {}).get("meta") or {}
    has_mismatch = isinstance(meta, dict) and ("tipicity_mismatch" in meta)
    if inf != "velocidad" or has_mismatch:
        return body

    body = re.sub(
        r"ALEGACIÓN\s+PRIMERA\s+—\s+VULNERACIÓN\s+DEL\s+PRINCIPIO\s+DE\s+TIPICIDAD\s+Y\s+SUBSUNCIÓN",
        "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)",
        body,
        flags=re.IGNORECASE,
    )
    for ptn in [r"[^\n]*subsunción típica[\s\S]*$", r"[^\n]*artículo\s+300[\s\S]*$", r"[^\n]*tipicidad y subsunción[\s\S]*$"]:
        body = re.sub(ptn, "", body, flags=re.IGNORECASE)
    return body.strip() + "\n"


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

    pr = plan.get("proof_requests") or []
    if isinstance(pr, list):
        pr2 = []
        for x in pr:
            xl = (x or "").lower()
            if any(k in xl for k in ["actuaciones interrupt", "firmeza", "estado actual del expediente", "acreditación de la notificación", "acreditacion de la notificacion"]):
                continue
            pr2.append(x)
        plan["proof_requests"] = pr2

    plan.setdefault("meta", {})
    plan["meta"]["sandbox_demo_sanitized"] = True
    return plan


def _extract_speed_pair_from_blob(blob: str) -> Dict[str, Any]:
    t = (blob or "").replace("\n", " ").lower()
    m_meas = re.search(r"circular\s+a\s+(\d{2,3})\s*km\s*/?h", t) or re.search(r"\b(\d{2,3})\s*km\s*/?h\b", t)
    m_lim = re.search(r"(?:limitad[ao]a?|l[ií]mit[eé])\s*(?:la\s*velocidad\s*)?(?:a\s*)?(\d{2,3})\s*km\s*/?h", t) or re.search(r"estando\s+limitad[ao]a?\s+la\s+velocidad\s+a\s+(\d{2,3})\s*km\s*/?h", t)

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
    mobile = (cm == "MOBILE") or (cm == "MOVING") or (cm == "VEHICLE") or (cm == "AGENT")
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
    lim = int(limit) if limit in tbl else None
    if lim is None:
        return {"fine": None, "points": None, "band": None, "table_limit": None}
    v = int(round(corrected))
    for lo, hi, fine, pts, label in tbl[lim]:
        if v >= lo and v <= hi:
            return {"fine": fine, "points": pts, "band": label, "table_limit": lim, "corrected_int": v}
    return {"fine": None, "points": None, "band": None, "table_limit": lim, "corrected_int": v}


def _compute_velocity_calc(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], capture_mode: str) -> Dict[str, Any]:
    blob_parts = []
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
        "capture_mode_for_margin": ("MOBILE" if (capture_mode or "").upper()=="AGENT" else (capture_mode or "UNKNOWN")),
        "margin_value": margin,
        "corrected": round(corrected, 2),
        "expected": expected,
        "extraction_confidence": pair.get("confidence", 0.0),
    }


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

    signals = ["semáforo", "semaforo", "fase roja", "no respetar la luz roja", "circular con luz roja"]
    return any(s in blob for s in signals)


def _build_facts_summary(extraction_core: Optional[Dict[str, Any]], attack_plan: Dict[str, Any]) -> str:
    inf = ((attack_plan or {}).get("infraction_type") or "").lower()
    try:
        hecho = (extraction_core or {}).get("hecho_imputado")
        if isinstance(hecho, str) and hecho.strip():
            hl = hecho.lower()

            def consistent() -> bool:
                if inf == "semaforo":
                    return any(k in hl for k in ["semáforo", "semaforo", "fase roja", "rojo"])
                if inf == "velocidad":
                    return any(k in hl for k in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"])
                if inf == "movil":
                    return any(k in hl for k in ["móvil", "movil", "teléfono", "telefono"])
                return True

            if consistent():
                return hecho.strip()
    except Exception:
        pass
    return ""


def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()
    traffic = ("tráfico" in organism) or ("dgt" in organism)

    blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    inferred = _infer_infraction_from_facts_phrases(classify)

    triage_tipo = None
    try:
        triage_tipo = (extraction_core or {}).get("tipo_infraccion")
    except Exception:
        triage_tipo = None

    infraction_type = inferred or "generic"

    if infraction_type == "generic" and triage_tipo in ("semaforo", "velocidad", "movil", "atencion", "parking"):
        infraction_type = triage_tipo

    if infraction_type == "generic":
        if any(s in blob for s in ["semáforo", "semaforo", "fase roja", "circular con luz roja", "no respetar la luz roja"]):
            infraction_type = "semaforo"
        elif any(s in blob for s in ["teléfono", "telefono", "móvil", "movil"]):
            infraction_type = "movil"
        elif any(s in blob for s in ["km/h", "radar", "cinemómetro", "cinemometro", "velocidad"]):
            infraction_type = "velocidad"

    plan = {
        "infraction_type": infraction_type,
        "primary": {
            "title": "Insuficiencia probatoria específica",
            "points": [
                "La carga de la prueba corresponde a la Administración.",
                "No cabe sancionar sin prueba suficiente y concreta del hecho infractor.",
            ],
        },
        "secondary": [],
        "proof_requests": [],
        "petition": {
            "main": "Archivo / estimación íntegra",
            "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa",
        },
    }

    if traffic:
        if infraction_type == "movil":
            plan["secondary"].append(
                {
                    "title": "Uso manual del móvil: prueba objetiva y motivación reforzada",
                    "points": [
                        "Debe acreditarse de forma concreta el uso manual (circunstancias y descripción suficiente).",
                        "Si no consta prueba objetiva o descripción detallada, procede el archivo por insuficiencia probatoria.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Boletín/denuncia/acta completa, con identificación del agente si consta.",
                "Descripción detallada del hecho y circunstancias (lugar/hora/forma de observación).",
                "Si existiera: fotografía/vídeo/capturas completas.",
            ]

        if infraction_type == "velocidad":
            plan["secondary"].append(
                {
                    "title": "Velocidad: prueba técnica completa (cinemómetro/radar)",
                    "points": [
                        "Debe constar identificación del cinemómetro y certificado vigente de verificación/calibración.",
                        "Debe constar margen aplicado y capturas completas.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Capturas/fotografías completas del hecho infractor.",
                "Identificación del cinemómetro (marca/modelo/nº serie) y ubicación exacta.",
                "Certificado de verificación/calibración vigente y constancia del margen aplicado.",
            ]

    return plan


def _map_precept_to_type(extraction_core: Dict[str, Any]) -> Optional[str]:
    if not isinstance(extraction_core, dict):
        return None
    norma_hint = (extraction_core.get("norma_hint") or "").upper()
    precepts = extraction_core.get("preceptos_detectados") or []

    if "8/2004" in norma_hint or any("8/2004" in (p or "") for p in precepts) or any("LSOA" in (p or "").upper() for p in precepts):
        return "seguro"

    art = extraction_core.get("articulo_infringido_num")
    if isinstance(art, str) and art.isdigit():
        art = int(art)
    if isinstance(art, int):
        if art in (12, 15):
            return "condiciones_vehiculo"
        if art == 18:
            return "atencion"
        if art == 167:
            return "marcas_viales"

    if any("2822/98" in (p or "") for p in precepts) or "2822/98" in norma_hint:
        return "condiciones_vehiculo"

    blob = json.dumps(extraction_core, ensure_ascii=False).lower()
    if "9.1 bis" in blob or "9,1 bis" in blob:
        return "no_identificar"

    return None


def _apply_tipicity_guard(attack_plan: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    inferred = (plan.get("infraction_type") or "").lower().strip()
    mapped = (_map_precept_to_type(extraction_core) or "").lower().strip()

    if mapped and inferred in ("", "generic", "otro"):
        plan["infraction_type"] = mapped
        plan.setdefault("meta", {})
        plan["meta"]["precept_forced_type"] = mapped
        return plan

    if mapped and inferred and mapped != inferred:
        plan.setdefault("meta", {})
        plan["meta"]["tipicity_mismatch"] = {"mapped": mapped, "inferred": inferred}

    return plan


def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    extraction_wrapper = _load_latest_extraction(case_id) or {}
    extraction_core = (extraction_wrapper.get("extracted") or {}) if isinstance(extraction_wrapper, dict) else {}

    capture_mode = _detect_capture_mode(docs, extraction_core)

    classify = _llm_json(PROMPT_CLASSIFY, {"case_id": case_id, "documents": docs, "latest_extraction": extraction_wrapper})
    timeline = _llm_json(PROMPT_TIMELINE, {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": extraction_wrapper})
    phase = _llm_json(PROMPT_PHASE, {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": extraction_wrapper})
    admissibility = _llm_json(PROMPT_GUARD, {"case_id": case_id, "recommended_action": phase, "timeline": timeline, "classification": classify, "latest_extraction": extraction_wrapper})

    attack_plan = _build_attack_plan(classify, timeline, extraction_core or {})
    attack_plan = _apply_tipicity_guard(attack_plan, extraction_core)

    velocity_calc = {}
    if (attack_plan or {}).get("infraction_type") == "velocidad":
        velocity_calc = _compute_velocity_calc(docs, extraction_core, capture_mode)

    draft = _llm_json(
        PROMPT_DRAFT,
        {
            "case_id": case_id,
            "interested_data": _load_interested_data(case_id),
            "classification": classify,
            "timeline": timeline,
            "recommended_action": phase,
            "admissibility": admissibility,
            "latest_extraction": extraction_wrapper,
            "extraction_core": extraction_core,
            "attack_plan": attack_plan,
            "facts_summary": _build_facts_summary(extraction_core, attack_plan),
            "context_intensity": "critico" if (attack_plan or {}).get("infraction_type") == "velocidad" else "normal",
            "velocity_calc": velocity_calc,
            "sandbox": {"override_applied": bool((admissibility or {}).get("override_applied")), "override_mode": (admissibility or {}).get("override_mode")},
        },
    )

    # Post-procesado determinista VELOCIDAD (potencia + coherencia + archivo)
    try:
        if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
            cuerpo = draft.get("cuerpo") or ""
            cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
            cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
            cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
            cuerpo = _force_archivo_in_speed_body(cuerpo)
            cuerpo = _normalize_velocity_titles_and_remove_tipicity(cuerpo, attack_plan)
            cuerpo = _fix_solicito_newline(cuerpo)
            draft["cuerpo"] = cuerpo
    except Exception as _e:
        _save_event(case_id, "postprocess_speed_failed", {"error": str(_e)})

    # Auto-repair (1 intento) para VELOCIDAD si el borrador no cumple mínimos VSE-1
    try:
        if isinstance(draft, dict):
            cuerpo0 = (draft.get("cuerpo") or "")
            missing = _velocity_strict_missing(cuerpo0) if cuerpo0 else []
            if missing and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                _save_event(case_id, "draft_repair_triggered", {"missing": missing})
                draft = _llm_json(
                    PROMPT_DRAFT_REPAIR_VELOCIDAD,
                    {
                        "case_id": case_id,
                        "missing": missing,
                        "previous_draft": draft,
                        "attack_plan": attack_plan,
                        "facts_summary": _build_facts_summary(extraction_core, attack_plan),
                        "context_intensity": "critico",
                        "velocity_calc": velocity_calc,
                        "latest_extraction": extraction_wrapper,
                        "classification": classify,
                        "timeline": timeline,
                        "admissibility": admissibility,
                    },
                )

                # Post-procesado tras REPAIR (velocidad)
                try:
                    if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                        cuerpo = draft.get("cuerpo") or ""
                        cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                        cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                        cuerpo = _velocity_pro_enrich(cuerpo, velocity_calc)
                        cuerpo = _force_archivo_in_speed_body(cuerpo)
                        cuerpo = _normalize_velocity_titles_and_remove_tipicity(cuerpo, attack_plan)
                        cuerpo = _fix_solicito_newline(cuerpo)
                        draft["cuerpo"] = cuerpo
                except Exception as _e:
                    _save_event(case_id, "postprocess_speed_failed", {"error": str(_e)})
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
        "velocity_calc": velocity_calc,
    }
    _save_event(case_id, "ai_expediente_result", result)
    return result
