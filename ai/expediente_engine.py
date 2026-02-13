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
from ai.prompts.draft_recurso import PROMPT as PROMPT_DRAFT

MAX_EXCERPT_CHARS = 12000


# =========================================================
# OpenAI JSON helper (igual que tu versión estable)
# =========================================================
def _llm_json(prompt: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


# =========================================================
# DB helpers
# =========================================================
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


# =========================================================
# CIP — Capa de Inteligencia Procesal (sin carpeta modules)
# =========================================================
def _build_procedural_view(
    classify: Dict[str, Any],
    timeline: Dict[str, Any],
    phase: Dict[str, Any],
    admissibility: Dict[str, Any]
) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = global_refs.get("main_organism")

    tl = (timeline or {}).get("timeline") or []
    tl_sorted = sorted(tl, key=lambda e: (e.get("date") or ""))

    current_stage = None
    recommended_action = None
    reason = None

    # Intentar extraer de procedure_phase (tu prompt)
    # Puede variar según versión, así que lo hacemos robusto:
    phase_obj = (phase or {}).get("phase") or {}
    rec_obj = (phase or {}).get("recommended_action") or (phase or {}).get("recommended_action", {}) or {}
    # Hay versiones donde recommended_action está dentro de phase["recommended_action"] o phase["recommended_action"]["action"]
    if isinstance(phase.get("phase"), dict) and phase.get("phase", {}).get("stage"):
        current_stage = phase["phase"].get("stage")
    elif isinstance(phase_obj, dict) and phase_obj.get("stage"):
        current_stage = phase_obj.get("stage")
    else:
        current_stage = "desconocido"

    # Acción recomendada (si tu procedure_phase la devuelve)
    if isinstance(rec_obj, dict) and rec_obj.get("action"):
        recommended_action = rec_obj.get("action")
        reason = rec_obj.get("notes") or rec_obj.get("reason") or ""
    elif isinstance(phase.get("recommended_action"), dict) and phase["recommended_action"].get("action"):
        recommended_action = phase["recommended_action"]["action"]
        reason = phase["recommended_action"].get("notes") or phase["recommended_action"].get("reason") or ""
    else:
        # Fallback conservador
        recommended_action = "ALEGACIONES"
        reason = "Acción por defecto (falta señal clara de fase)."

    missing_critical = []
    for md in (admissibility or {}).get("missing_data") or []:
        missing_critical.append({"name": str(md), "impact": "alto"})

    deadline_status = (admissibility or {}).get("deadline_status") or "UNKNOWN"

    return {
        "domain": "traffic" if ("tráfico" in (str(organism or "").lower()) or "dgt" in (str(organism or "").lower())) else "administrativo_general",
        "organism": organism,
        "current_stage": current_stage,
        "recommended_action": recommended_action,
        "action_reason": reason,
        "timeline_sorted": tl_sorted,
        "missing_critical": missing_critical,
        "deadlines": {
            "deadline_status": deadline_status,
            "notes": (admissibility or {}).get("reason") or ""
        }
    }


# =========================================================
# Attack plan — Detector robusto por scoring + pruebas
# =========================================================
def _build_attack_plan(classify: Dict[str, Any], timeline: Dict[str, Any], latest_extraction: Dict[str, Any]) -> Dict[str, Any]:
    global_refs = (classify or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()
    traffic = ("tráfico" in organism) or ("dgt" in organism) or ("ayuntamiento" in organism)

    blob = json.dumps(
        {
            "latest_extraction": latest_extraction or {},
            "classification": classify or {},
            "timeline": timeline or {},
            "organism": organism,
        },
        ensure_ascii=False,
    ).lower()

    scores = {"semaforo": 0, "velocidad": 0, "movil": 0}

    # SEMÁFORO (fuertes)
    if "circular con luz roja" in blob:
        scores["semaforo"] += 8
    if "luz roja" in blob:
        scores["semaforo"] += 6
    if "semáforo" in blob or "semaforo" in blob:
        scores["semaforo"] += 4
    if "foto-rojo" in blob or "foto rojo" in blob:
        scores["semaforo"] += 3
    if "fase roja" in blob:
        scores["semaforo"] += 3

    # VELOCIDAD (fuertes)
    if "exceso de velocidad" in blob or "superar la velocidad" in blob:
        scores["velocidad"] += 6
    if "cinemómetro" in blob or "cinemometro" in blob:
        scores["velocidad"] += 4
    if "radar" in blob:
        scores["velocidad"] += 4
    if re.search(r"\b\d{2,3}\s*km\s*/?\s*h\b", blob):
        scores["velocidad"] += 7

    # MÓVIL (fuertes)
    if "utilizando manualmente" in blob:
        scores["movil"] += 6
    if "teléfono móvil" in blob or "telefono movil" in blob:
        scores["movil"] += 6
    if "teléfono" in blob or "telefono" in blob:
        scores["movil"] += 3
    if "móvil" in blob or "movil" in blob:
        scores["movil"] += 2
    if "auriculares" in blob or "manos libres" in blob:
        scores["movil"] += 2

    # Desempate: semaforo > velocidad > movil
    best = max(scores, key=lambda k: scores[k])
    infraction_type = "generic"
    if scores[best] > 0:
        if best == "semaforo":
            infraction_type = "semaforo"
        elif best == "velocidad":
            infraction_type = "velocidad"
        elif best == "movil":
            infraction_type = "movil"

    plan: Dict[str, Any] = {
        "infraction_type": infraction_type,
        "detection_scores": scores,
        "primary": {
            "title": "Presunción de inocencia e insuficiencia probatoria (art. 24 CE)",
            "points": [
                "La carga de la prueba corresponde a la Administración.",
                "No cabe sancionar sin prueba suficiente, concreta y válida del hecho infractor.",
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

        if infraction_type == "semaforo":
            plan["secondary"].append(
                {
                    "title": "Semáforo en fase roja: fase roja efectiva, prueba suficiente y motivación reforzada",
                    "points": [
                        "Debe acreditarse la fase roja efectiva en el instante del cruce (no basta fórmula genérica).",
                        "Debe constar identificación clara del vehículo y su posición respecto de la línea de detención.",
                        "Si es captación automática, debe constar secuencia y funcionamiento; si es agente, circunstancias de observación.",
                    ],
                }
            )
            plan["proof_requests"] += [
                "Copia íntegra y legible del boletín/acta de denuncia.",
                "Si captación automática: secuencia completa de fotografías/fotogramas y metadatos/hora exacta.",
                "Acreditación del correcto funcionamiento del sistema de captación (si existe).",
                "Detalle de fase semafórica en el momento de la infracción y ubicación exacta.",
                "Si denuncia presencial: identificación del agente y circunstancias de observación (visibilidad/distancia/posición).",
            ]

        # Antigüedad transversal
        tl = (timeline or {}).get("timeline") or []
        dates: List[str] = []
        for ev in tl:
            d = ev.get("date")
            if isinstance(d, str) and len(d) >= 10:
                dates.append(d[:10])
        if dates:
            oldest = sorted(dates)[0]
            if oldest.startswith("201") or oldest.startswith("200"):
                plan["secondary"].insert(
                    0,
                    {
                        "title": "Antigüedad del expediente: acreditación de notificación, firmeza y actos interruptivos",
                        "points": [
                            "Dada la antigüedad, corresponde acreditar notificación válida, firmeza y, en su caso, actos interruptivos.",
                            "Si no consta acreditación suficiente, procede el archivo.",
                        ],
                    },
                )
                plan["proof_requests"] += [
                    "Acreditación de la notificación válida (fecha de recepción/acuse/medio).",
                    "Acreditación de firmeza y actuaciones interruptivas, si existieran.",
                    "Estado actual del expediente y fundamento de su vigencia.",
                ]

    # Blindajes
    if not plan.get("proof_requests"):
        plan["proof_requests"] = [
            "Copia íntegra y foliada del expediente administrativo.",
            "Acreditación de la notificación válida (fecha de recepción/acuse/medio).",
            "Acreditación de la firmeza y estado actual del expediente.",
            "Boletín/denuncia/acta completa y legible.",
        ]

    has_motiv = any("motiv" in (b.get("title", "").lower()) for b in plan.get("secondary", []))
    if not has_motiv:
        plan["secondary"].append(
            {
                "title": "Motivación suficiente y derecho de defensa (Ley 39/2015)",
                "points": [
                    "La motivación no puede ser estereotipada: debe conectar hechos, prueba y razonamiento.",
                    "La falta de motivación adecuada genera indefensión y refuerza la procedencia del archivo.",
                ],
            }
        )

    return plan


# =========================================================
# MAIN ORCHESTRATOR (flujo intacto)
# =========================================================
def run_expediente_ai(case_id: str) -> Dict[str, Any]:
    docs = _load_case_documents(case_id)
    if not docs:
        raise RuntimeError("No hay documentos asociados al expediente.")

    latest_extraction = _load_latest_extraction(case_id)

    classify = _llm_json(PROMPT_CLASSIFY, {"case_id": case_id, "documents": docs, "latest_extraction": latest_extraction})
    timeline = _llm_json(PROMPT_TIMELINE, {"case_id": case_id, "classification": classify, "documents": docs, "latest_extraction": latest_extraction})
    phase = _llm_json(PROMPT_PHASE, {"case_id": case_id, "classification": classify, "timeline": timeline, "latest_extraction": latest_extraction})

    admissibility = _llm_json(
        PROMPT_GUARD,
        {"case_id": case_id, "recommended_action": phase, "timeline": timeline, "classification": classify, "latest_extraction": latest_extraction},
    )

    # Override pruebas
    flags = _load_case_flags(case_id)
    if flags.get("test_mode") and flags.get("override_deadlines"):
        admissibility["admissibility"] = "ADMISSIBLE"
        admissibility["can_generate_draft"] = True
        admissibility["deadline_status"] = admissibility.get("deadline_status") or "UNKNOWN"
        admissibility["required_constraints"] = admissibility.get("required_constraints") or []
        _save_event(case_id, "test_override_applied", {"flags": flags})

    # CIP (visión procesal)
    procedural_view = _build_procedural_view(classify, timeline, phase, admissibility)
    _save_event(case_id, "procedural_view_generated", {"procedural_view": procedural_view})

    # Attack plan robusto
    attack_plan = _build_attack_plan(classify, timeline, latest_extraction or {})

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
                "latest_extraction": latest_extraction,
                "attack_plan": attack_plan,
                "procedural_view": procedural_view,
            },
        )

    result = {
        "ok": True,
        "case_id": case_id,
        "classify": classify,
        "timeline": timeline,
        "phase": phase,
        "admissibility": admissibility,
        "procedural_view": procedural_view,
        "attack_plan": attack_plan,
        "draft": draft,
    }

    _save_event(case_id, "ai_expediente_result", result)
    return result
