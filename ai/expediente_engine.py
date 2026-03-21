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

MAX_EXCERPT_CHARS = 12000


# ==========================
# LLM JSON helper
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


# ==========================
# DB helpers
# ==========================
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
# Capture mode (se usa como señal contextual; no decide redacción final)
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


# ==========================
# Tipicidad strict transversal (artículo ↔ tipo esperado)
# ==========================
ARTICLE_TYPE_MAP = {
    "RGC": {
        48: "velocidad",
        146: "semaforo",
        18: "atencion",
        167: "marcas_viales",
        12: "condiciones_vehiculo",
        15: "condiciones_vehiculo",
    },
    "RDL 8/2004": {
        2: "seguro",
    },
}

def _norma_key_from_hint(extraction_core: Dict[str, Any]) -> str:
    hint = (extraction_core or {}).get("norma_hint") or ""
    h = str(hint).upper()
    if "RDL 8/2004" in h or "8/2004" in h:
        return "RDL 8/2004"
    if "RGC" in h or "REGLAMENTO GENERAL DE CIRCUL" in h:
        return "RGC"
    return ""

def _get_article_num(extraction_core: Dict[str, Any]) -> Optional[int]:
    art = (extraction_core or {}).get("articulo_infringido_num")
    if isinstance(art, int):
        return art
    if isinstance(art, str) and art.strip().isdigit():
        try:
            return int(art.strip())
        except Exception:
            return None
    return None

def _expected_type_from_article(extraction_core: Dict[str, Any]) -> Optional[str]:
    norma_key = _norma_key_from_hint(extraction_core or {})
    art = _get_article_num(extraction_core or {})
    if not norma_key or art is None:
        return None
    return (ARTICLE_TYPE_MAP.get(norma_key) or {}).get(art)

def _strict_tipicity_check(extraction_core: Dict[str, Any], inferred_type: str) -> Dict[str, Any]:
    expected = _expected_type_from_article(extraction_core or {})
    art = _get_article_num(extraction_core or {})
    norma_key = _norma_key_from_hint(extraction_core or {})
    inferred = (inferred_type or "").lower().strip()
    exp = (expected or "").lower().strip()
    if not expected or not inferred:
        return {"ok": False, "match": None, "expected": expected, "inferred": inferred_type, "article": art, "norma_key": norma_key}
    return {"ok": True, "match": (exp == inferred), "expected": expected, "inferred": inferred_type, "article": art, "norma_key": norma_key}

def _apply_tipicity_strict(attack_plan: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(attack_plan or {})
    inferred = (plan.get("infraction_type") or "").lower().strip()
    check = _strict_tipicity_check(extraction_core or {}, inferred)
    plan.setdefault("meta", {})
    plan["meta"]["tipicity_check"] = check

    if check.get("ok") and check.get("match") is False:
        plan["meta"]["tipicity_mismatch_strict"] = True
        plan["meta"]["expected_type"] = check.get("expected")
        plan["meta"]["inferred_type"] = check.get("inferred")

        plan["primary"] = {
            "title": "Vulneración del principio de tipicidad y errónea subsunción normativa",
            "points": [
                "El Derecho sancionador exige subsunción exacta entre el hecho descrito y el precepto aplicado (principio de tipicidad y legalidad sancionadora).",
                "Si el artículo citado no se corresponde con la conducta efectivamente imputada, la sanción carece de cobertura típica suficiente y genera indefensión.",
                "Procede el ARCHIVO por ausencia de adecuada subsunción normativa, sin perjuicio de la práctica de prueba y aportación íntegra del expediente."
            ],
        }

        pr = list(plan.get("proof_requests") or [])
        pr += [
            "Copia íntegra del expediente administrativo (denuncia/boletín, propuesta y resolución, si existieran).",
            "Identificación expresa del precepto aplicado (artículo/apartado) y motivación del encaje con el hecho descrito.",
            "Aportación de la norma aplicable y fundamentos jurídicos utilizados."
        ]
        # unique
        seen = set()
        pr2 = []
        for x in pr:
            if x not in seen:
                seen.add(x)
                pr2.append(x)
        plan["proof_requests"] = pr2

    return plan


# ==========================
# Infraction type (soft) desde extraction_core / classify facts_phrases
# ==========================


def _has_any(text: str, signals: List[str]) -> bool:
    return any(s in (text or "") for s in signals)


SEMAFORO_STRONG_SIGNALS = [
    "semaforo", "semáforo", "fase roja", "fase del rojo",
    "luz roja no intermitente", "cruce en rojo", "cruce con fase del rojo",
    "articulo 146", "art. 146", "t/s roja", "ts roja",
]

SEMAFORO_FALSE_FRIENDS = [
    "ordenarle la detencion",
    "al ordenarle la detencion",
    "orden de detencion",
    "no se para en el lugar",
    "no se para",
    "parar en el lugar",
    "detencion policial",
]

VEHICLE_LIGHT_SIGNALS = [
    "dispositivos de alumbrado",
    "dispositivos de señalizacion",
    "dispositivos de senalizacion",
    "senalizacion optica",
    "señalizacion optica",
    "luz en la parte trasera",
    "parte trasera",
    "destellos",
    "anexo i",
    "reglamentacion del anexo",
    "reglamentación del anexo",
    "alumbrado",
]

ATENCION_TEMERARIA_SIGNALS = [
    "conducir de forma temeraria",
    "conduccion temeraria",
    "conducción temeraria",
    "conducir de forma negligente",
    "no mantener la atencion",
    "no mantener la atención",
    "atencion permanente",
    "atención permanente",
    "temeraria",
    "temerario",
]

def _looks_like_semaforo_context(text: str) -> bool:
    t = (text or "").lower()
    if _has_any(t, VEHICLE_LIGHT_SIGNALS):
        return False
    if _has_any(t, ATENCION_TEMERARIA_SIGNALS):
        return False
    if _has_any(t, SEMAFORO_FALSE_FRIENDS) and not _has_any(
        t,
        [
            "articulo 146", "art. 146", "semaforo", "semáforo",
            "fase roja", "fase del rojo", "luz roja no intermitente",
            "cruce en rojo", "cruce con fase del rojo",
        ],
    ):
        return False
    return _has_any(t, SEMAFORO_STRONG_SIGNALS)

def _infer_infraction_from_facts_phrases(classify: Dict[str, Any]) -> Optional[str]:
    phrases = (classify or {}).get("facts_phrases") or []
    if not phrases:
        return None

    joined = "\n".join([str(p) for p in phrases if p]).lower()

    if _has_any(joined, VEHICLE_LIGHT_SIGNALS):
        return "condiciones_vehiculo"

    if _has_any(joined, ATENCION_TEMERARIA_SIGNALS):
        return "atencion"

    if _looks_like_semaforo_context(joined):
        return "semaforo"

    movil_signals = [
        "uso manual del teléfono móvil",
        "uso manual del telefono movil",
        "uso manual del teléfono",
        "uso manual del telefono",
        "manipulando el móvil",
        "manipulando el movil",
        "interactuando con la pantalla",
        "sujetando con la mano el dispositivo",
        "utilizando manualmente el teléfono móvil",
        "utilizando manualmente el telefono movil",
    ]
    if any(s in joined for s in movil_signals):
        return "movil"

    if any(s in joined for s in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro", "exceso de velocidad"]):
        return "velocidad"

    return None

    joined = "\n".join([str(p) for p in phrases if p]).lower()

    semaforo_signals = [
        "semáforo", "semaforo", "fase roja", "fase del rojo", "luz roja",
        "luz roja no intermitente", "t/s roja", "ts roja", "cruce en rojo",
        "cruce con fase del rojo", "articulo 146", "art. 146",
    ]
    if any(s in joined for s in semaforo_signals):
        return "semaforo"

    movil_signals = [
        "uso manual del teléfono móvil",
        "uso manual del telefono movil",
        "uso manual del teléfono",
        "uso manual del telefono",
        "manipulando el móvil",
        "manipulando el movil",
        "interactuando con la pantalla",
        "sujetando con la mano el dispositivo",
        "utilizando manualmente el teléfono móvil",
        "utilizando manualmente el telefono movil",
    ]
    if any(s in joined for s in movil_signals):
        return "movil"

    if any(s in joined for s in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro", "exceso de velocidad"]):
        return "velocidad"

    return None


def _infer_infraction_from_extraction(extraction_core: Dict[str, Any]) -> str:
    extraction_core = extraction_core or {}

    # CONFIAR PRIMERO en analyze.py si ya clasificó el caso
    tipo = extraction_core.get("tipo_infraccion")
    if isinstance(tipo, str) and tipo.strip() and tipo.strip().lower() not in ("", "otro", "generic", "unknown"):
        return tipo.strip().lower()

    t = ""
    try:
        t = json.dumps(extraction_core, ensure_ascii=False).lower()
    except Exception:
        t = ""

    if _has_any(t, VEHICLE_LIGHT_SIGNALS):
        return "condiciones_vehiculo"

    if _has_any(t, ATENCION_TEMERARIA_SIGNALS):
        return "atencion"

    if _looks_like_semaforo_context(t):
        return "semaforo"

    movil_strong_signals = [
        "telefono movil",
        "teléfono móvil",
        "uso manual del movil",
        "uso manual del móvil",
        "uso manual del telefono",
        "uso manual del teléfono",
        "manipulando el movil",
        "manipulando el móvil",
        "interactuando con la pantalla",
        "sujetando con la mano el dispositivo",
        "utilizando manualmente",
    ]
    if any(s in t for s in movil_strong_signals):
        return "movil"

    if any(s in t for s in ["km/h", "radar", "cinemómetro", "cinemometro", "exceso de velocidad"]):
        return "velocidad"

    return "generic"


def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], extraction_core: Dict[str, Any]) -> Dict[str, Any]:
    inferred = str((extraction_core or {}).get("tipo_infraccion") or "").strip().lower()
if inferred in ("", "otro", "unknown"):
    inferred = "generic"

    plan: Dict[str, Any] = {
        "infraction_type": inferred,
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
        "meta": {},
    }
    return plan


def _build_facts_summary(extraction_core: Optional[Dict[str, Any]], attack_plan: Dict[str, Any]) -> str:
    inf = ((attack_plan or {}).get("infraction_type") or "").lower()
    hecho = (extraction_core or {}).get("hecho_imputado")
    if isinstance(hecho, str) and hecho.strip():
        hl = hecho.lower()
        if inf == "semaforo" and any(k in hl for k in ["semáforo", "semaforo", "fase roja", "rojo", "luz roja"]):
            return hecho.strip()
        if inf == "velocidad" and any(k in hl for k in ["velocidad", "km/h", "radar", "cinemómetro", "cinemometro"]):
            return hecho.strip()
        if inf == "movil" and any(k in hl for k in ["móvil", "movil", "teléfono", "telefono"]):
            return hecho.strip()
        if inf not in ("semaforo", "velocidad", "movil"):
            return hecho.strip()
    return ""


def _compute_context_intensity(timeline: Dict[str, Any], extraction_core: Dict[str, Any], classify: Dict[str, Any]) -> str:
    blob = ""
    try:
        blob = json.dumps(extraction_core or {}, ensure_ascii=False).lower()
    except Exception:
        blob = ""

    precepts = (extraction_core or {}).get("preceptos_detectados") or []
    pre_blob = " ".join([str(p) for p in precepts]).lower()

    has_lsoa = ("lsoa" in pre_blob) or ("8/2004" in pre_blob) or ("8/2004" in blob)
    has_speed = any(k in blob for k in ["km/h", "cinemómetro", "cinemometro", "radar", "velocidad"])
    if has_lsoa and has_speed:
        return "critico"

    # antigüedad
    dates: List[str] = []
    tl = (timeline or {}).get("timeline") or []
    for ev in tl:
        d = ev.get("date")
        if isinstance(d, str) and len(d) >= 10:
            dates.append(d[:10])
    for k in ("fecha_documento", "fecha_notificacion"):
        v = (extraction_core or {}).get(k)
        if isinstance(v, str) and len(v) >= 10:
            dates.append(v[:10])

    if dates:
        oldest = sorted(dates)[0]
        if oldest[:4].isdigit() and int(oldest[:4]) <= 2015:
            return "reforzado"

    return "normal"


def _override_mode() -> str:
    m = (os.getenv("RTM_OVERRIDE_MODE") or "TEST_REALISTA").strip().upper()
    if m not in ("TEST_REALISTA", "SANDBOX_DEMO"):
        m = "TEST_REALISTA"
    return m


def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    extraction_wrapper = _load_latest_extraction(case_id) or {}
    extraction_core = (extraction_wrapper.get("extracted") or {}) if isinstance(extraction_wrapper, dict) else {}

    capture_mode = _detect_capture_mode(docs, extraction_core)

    classify = _llm_json(
        PROMPT_CLASSIFY,
        {"case_id": case_id, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    timeline = _llm_json(
        PROMPT_TIMELINE,
        {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": extraction_wrapper},
    )

    phase = _llm_json(
        PROMPT_PHASE,
        {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": extraction_wrapper},
    )

    admissibility = _llm_json(
        PROMPT_GUARD,
        {
            "case_id": case_id,
            "recommended_action": phase,
            "timeline": timeline,
            "classification": classify,
            "latest_extraction": extraction_wrapper,
        },
    )

    flags = _load_case_flags(case_id)
    override_mode = _override_mode()
    if flags.get("test_mode") and flags.get("override_deadlines"):
        original_adm = admissibility.get("admissibility")
        admissibility["original_admissibility"] = original_adm
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["deadline_status"] = admissibility.get("deadline_status") or "UNKNOWN"
        if override_mode == "SANDBOX_DEMO":
            admissibility["deadline_status"] = "OK"
        admissibility["required_constraints"] = admissibility.get("required_constraints") or []
        admissibility["override_applied"] = True
        admissibility["override_mode"] = override_mode
        _save_event(case_id, "test_override_applied", {"flags": flags, "override_mode": override_mode, "original_admissibility": original_adm})

    # Attack plan (soft)
    attack_plan = _build_attack_plan(classify, timeline, extraction_core or {})
    # Tipicidad strict transversal
    attack_plan = _apply_tipicity_strict(attack_plan, extraction_core or {})

    facts_summary = _build_facts_summary(extraction_core, attack_plan)
    context_intensity = _compute_context_intensity(timeline, extraction_core, classify)

    # Si mismatch strict, subimos intensidad
    try:
        if (attack_plan or {}).get("meta", {}).get("tipicity_mismatch_strict"):
            context_intensity = "critico"
    except Exception:
        pass

    # Draft (IA) — el texto final determinista de tráfico se decide en generate.py / módulos
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
                "velocity_calc": {},  # generate.py/módulos lo calculan determinista
                "sandbox": {
                    "override_applied": bool(admissibility.get("override_applied")),
                    "override_mode": admissibility.get("override_mode"),
                },
            },
        )

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
        "velocity_calc": {},
        "extraction_debug": {
            "wrapper_keys": list(extraction_wrapper.keys()) if isinstance(extraction_wrapper, dict) else [],
            "core_keys": list(extraction_core.keys()) if isinstance(extraction_wrapper, dict) else [],
        },
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result