# ai/expediente_engine.py
# Versión determinista VELOCIDAD (Referencia Nacional)
# - Primary determinista (no nace en "presunción de inocencia")
# - Temperature=0.0 (reduce variación)
# - Auto-repair (1 intento) para VELOCIDAD si no cumple mínimos VSE-1
# - Petitum forzado: en VELOCIDAD siempre ARCHIVO
# - Inclusión determinista de párrafo de cálculo (margen/velocidad corregida/banda) si velocity_calc.ok

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

PROMPT_DRAFT_REPAIR_VELOCIDAD = """Eres abogado especialista en sancionador (España). Debes REPARAR un borrador de recurso por EXCESO DE VELOCIDAD.

OBJETIVO: reescribir el borrador COMPLETO para que pase una validación estricta.

REGLAS OBLIGATORIAS:
1) La PRIMERA ALEGACIÓN NO puede ser 'Presunción de inocencia'.
2) La PRIMERA ALEGACIÓN debe titularse exactamente:
   'ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)'
3) El cuerpo debe contener literalmente la expresión: 'cadena de custodia'.
4) Debe incluir 'margen' y 'velocidad corregida'.
5) Debe exigir 'certificado' y 'verificación' (metrológica) del cinemómetro.
6) Debe exigir 'captura' o 'fotograma' completo.
7) El SOLICITO en velocidad debe pedir ARCHIVO (no "revisión").
8) No inventes hechos. Mantén prudencia: 'no consta acreditado', 'no se aporta'.

ENTRADA: JSON con borrador anterior y contexto.
SALIDA: SOLO JSON con la misma forma {asunto,cuerpo,variables_usadas,checks,notes_for_operator}.
"""

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
        "metrologia": ["certificado", "verificación", "verificacion", "metrológ", "metrolog"],
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
    if not body:
        return body
    reps = [
        ("Que se acuerde la revisión del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
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
            r"(Hecho imputado:\s*EXCESO DE VELOCIDAD)\s*\.\s*",
            rf"\1 ({measured} km/h).",
            body,
            flags=re.IGNORECASE,
        )
        return body
    except Exception:
        return body


def _ensure_velocity_calc_paragraph(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if not body:
            return body
        vc = velocity_calc or {}
        if not vc.get("ok"):
            return body
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
            parts.append(f"la aplicación del margen situaría la velocidad corregida en torno a {corrected:.2f} km/h,")
        parts.append("extremo cuya acreditación corresponde a la Administración (margen aplicado, velocidad corregida y banda/tramo resultante).")
        if band:
            tail = f"De acuerdo con la tabla orientativa, ello podría encajar en la banda: {band}"
            if isinstance(fine, int) or isinstance(pts, int):
                tail += f" (multa {fine}€ / puntos {pts})."
            else:
                tail += "."
            parts.append(tail)

        paragraph = " ".join(parts)
        m = re.search(r"(ALEGACIÓN\s+PRIMERA[^\n]*\n)", body, flags=re.IGNORECASE)
        if not m:
            return re.sub(r"(\nIII\.\s*SOLICITO)", "\n" + paragraph + r"\n\1", body, flags=re.IGNORECASE)
        insert_at = m.end(1)
        return body[:insert_at] + paragraph + "\n" + body[insert_at:]
    except Exception:
        return body


def _override_mode() -> str:
    m = (os.getenv("RTM_OVERRIDE_MODE") or "TEST_REALISTA").strip().upper()
    if m not in ("TEST_REALISTA", "SANDBOX_DEMO"):
        m = "TEST_REALISTA"
    return m


def _sanitize_for_sandbox_demo(attack_plan: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    sec = plan.get("secondary") or []
    if isinstance(sec, list):
        plan["secondary"] = [it for it in sec if not any(k in ((it or {}).get("title") or "").lower() for k in ["antigüedad", "actos interrupt", "firmeza", "notificación válida", "notificacion valida"]) ]
    pr = plan.get("proof_requests") or []
    if isinstance(pr, list):
        plan["proof_requests"] = [x for x in pr if not any(k in (x or "").lower() for k in ["actuaciones interrupt", "firmeza", "estado actual del expediente", "acreditación de la notificación", "acreditacion de la notificacion"]) ]
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
    mobile = cm in ("MOBILE", "MOVING", "VEHICLE", "AGENT")
    if measured <= 100:
        return 7.0 if mobile else 5.0
    pct = 0.07 if mobile else 0.05
    return round(measured * pct, 2)


def _dgt_speed_sanction_table() -> Dict[int, list]:
    return {
        90: [(91,120,100,0,'100€ sin puntos'), (121,140,300,2,'300€ 2 puntos'), (141,150,400,4,'400€ 4 puntos'), (151,160,500,6,'500€ 6 puntos'), (161,999,600,6,'600€ 6 puntos')],
        50: [(51,70,100,0,'100€ sin puntos'), (71,80,300,2,'300€ 2 puntos'), (81,90,400,4,'400€ 4 puntos'), (91,100,500,6,'500€ 6 puntos'), (121,999,600,6,'600€ 6 puntos')],
        120:[(121,150,100,0,'100€ sin puntos'), (151,170,300,2,'300€ 2 puntos'), (171,180,400,4,'400€ 4 puntos'), (181,190,500,6,'500€ 6 puntos'), (191,999,600,6,'600€ 6 puntos')],
        110:[(111,140,100,0,'100€ sin puntos'), (141,160,300,2,'300€ 2 puntos'), (161,170,400,4,'400€ 4 puntos'), (171,180,500,6,'500€ 6 puntos'), (181,999,600,6,'600€ 6 puntos')],
        100:[(101,130,100,0,'100€ sin puntos'), (131,150,300,2,'300€ 2 puntos'), (151,160,400,4,'400€ 4 puntos'), (161,170,500,6,'500€ 6 puntos'), (171,999,600,6,'600€ 6 puntos')],
        80: [(81,110,100,0,'100€ sin puntos'), (111,130,300,2,'300€ 2 puntos'), (131,140,400,4,'400€ 4 puntos'), (141,150,500,6,'500€ 6 puntos'), (151,999,600,6,'600€ 6 puntos')],
        70: [(71,100,100,0,'100€ sin puntos'), (101,120,300,2,'300€ 2 puntos'), (121,130,400,4,'400€ 4 puntos'), (131,140,500,6,'500€ 6 puntos'), (141,999,600,6,'600€ 6 puntos')],
        60: [(61,90,100,0,'100€ sin puntos'), (91,110,300,2,'300€ 2 puntos'), (111,120,400,4,'400€ 4 puntos'), (121,130,500,6,'500€ 6 puntos'), (131,999,600,6,'600€ 6 puntos')],
        40: [(41,60,100,0,'100€ sin puntos'), (61,70,300,2,'300€ 2 puntos'), (71,80,400,4,'400€ 4 puntos'), (81,90,500,6,'500€ 6 puntos'), (91,999,600,6,'600€ 6 puntos')],
        30: [(31,50,100,0,'100€ sin puntos'), (51,60,300,2,'300€ 2 puntos'), (61,70,400,4,'400€ 4 puntos'), (71,80,500,6,'500€ 6 puntos'), (81,999,600,6,'600€ 6 puntos')],
        20: [(21,40,100,0,'100€ sin puntos'), (41,50,300,2,'300€ 2 puntos'), (51,60,400,4,'400€ 4 puntos'), (61,70,500,6,'500€ 6 puntos'), (71,999,600,6,'600€ 6 puntos')],
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

    auto_signals = ["cámara", "camara", "fotograma", "fotogramas", "secuencia", "foto", "fotografía", "fotografia",
                    "captación automática", "captacion automatica", "sistema automático", "sistema automatico",
                    "dispositivo", "sensor", "instalación", "instalacion", "vídeo", "video"]
    agent_signals = ["agente", "policía", "policia", "guardia civil", "denunciante", "observó", "observo",
                     "manifestó", "manifesto", "presencial", "in situ"]

    auto_score = sum(1 for s in auto_signals if s in blob)
    agent_score = sum(1 for s in agent_signals if s in blob)

    if auto_score >= 2 and auto_score >= agent_score + 1:
        return "AUTO"
    if agent_score >= 2 and agent_score >= auto_score + 1:
        return "AGENT"
    return "UNKNOWN"


def _has_semaforo_signals(docs: List[Dict[str, Any]], extraction_core: Optional[Dict[str, Any]], classify: Optional[Dict[str, Any]] = None) -> bool:
    # simple
    blob_parts: List[str] = []
    try:
        blob_parts.append(json.dumps(extraction_core or {}, ensure_ascii=False).lower())
    except Exception:
        pass
    for d in docs or []:
        blob_parts.append((d.get("text_excerpt") or "").lower())
    blob = "\n".join(blob_parts)
    return any(s in blob for s in ["semáforo", "semaforo", "fase roja", "no respetar la luz roja", "circular con luz roja"])


def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    # Determinista: velocidad => primary técnico
    blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    infraction_type = "velocidad" if any(s in blob for s in ["km/h", "radar", "cinemómetro", "cinemometro", "velocidad"]) else "generic"
    primary = {"title": "Insuficiencia probatoria específica", "points": []}
    if infraction_type == "velocidad":
        primary = {"title": "Prueba técnica, metrología y cadena de custodia (cinemómetro)", "points": []}
    return {
        "infraction_type": infraction_type,
        "primary": primary,
        "secondary": [],
        "proof_requests": [],
        "petition": {"main": "Archivo / estimación íntegra", "subsidiary": "Subsidiariamente, práctica de prueba y aportación documental completa"},
    }


def _compute_context_intensity(timeline: Dict[str, Any], extraction_core: Dict[str, Any], classify: Dict[str, Any]) -> str:
    return "critico" if "km/h" in json.dumps(extraction_core or {}, ensure_ascii=False).lower() else "normal"


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

    admissibility = _llm_json(PROMPT_GUARD, {"case_id": case_id, "recommended_action": phase, "timeline": timeline, "classification": classify, "latest_extraction": extraction_wrapper})

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
        attack_plan = {"infraction_type": "semaforo", "primary": {"title": "Insuficiencia probatoria", "points": []}, "secondary": [], "proof_requests": [], "petition": {"main": "Archivo", "subsidiary": "Prueba"}, "meta": {"forced": True}}
    else:
        attack_plan = _build_attack_plan(classify, timeline, extraction_core or {})

    context_intensity = _compute_context_intensity(timeline, extraction_core, classify)

    velocity_calc: Dict[str, Any] = {}
    if (attack_plan or {}).get("infraction_type") == "velocidad":
        velocity_calc = _compute_velocity_calc(docs, extraction_core, capture_mode)

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
                "facts_summary": "",
                "context_intensity": context_intensity,
                "velocity_calc": velocity_calc,
                "sandbox": {"override_applied": bool(admissibility.get("override_applied")), "override_mode": admissibility.get("override_mode")},
            },
        )

        try:
            if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
                cuerpo = draft.get("cuerpo") or ""
                cuerpo = _ensure_speed_antecedentes(cuerpo, velocity_calc)
                cuerpo = _ensure_velocity_calc_paragraph(cuerpo, velocity_calc)
                cuerpo = _force_archivo_in_speed_body(cuerpo)
                draft["cuerpo"] = cuerpo
        except Exception:
            pass

        try:
            if isinstance(draft, dict) and ((attack_plan or {}).get("infraction_type") == "velocidad"):
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
                            "facts_summary": "",
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
        "facts_summary": "",
        "context_intensity": context_intensity,
        "velocity_calc": velocity_calc,
    }
    _save_event(case_id, "ai_expediente_result", result)
    return result
