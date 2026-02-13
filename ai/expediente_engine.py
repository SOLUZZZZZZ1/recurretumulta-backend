import json
from typing import Any, Dict

from sqlalchemy import text
from database import get_engine
from ai.prompts.draft_recurso import PROMPT
from ai.llm_utils import llm_json


# =====================================================
# DETECTAR SI ES TRÁFICO
# =====================================================

def is_traffic_case(classification: Dict[str, Any]) -> bool:
    global_refs = (classification or {}).get("global_refs") or {}
    organism = (global_refs.get("main_organism") or "").lower()

    if "tráfico" in organism or "dgt" in organism:
        return True

    return False


# =====================================================
# DETECTAR TIPO INFRACCIÓN SIMPLE
# =====================================================

def detect_infraction_type(latest_extraction: Dict[str, Any]) -> str:
    text_blob = json.dumps(latest_extraction or {}).lower()

    if "móvil" in text_blob or "telefono" in text_blob:
        return "movil"

    if "km/h" in text_blob or "radar" in text_blob:
        return "velocidad"

    return "generic"


# =====================================================
# CONSTRUIR ATTACK PLAN SIMPLE
# =====================================================

def build_attack_plan(payload: Dict[str, Any]) -> Dict[str, Any]:

    attack_plan = {
        "primary": {
            "title": "Presunción de inocencia e insuficiencia probatoria",
            "points": [
                "La carga de la prueba corresponde a la Administración (art. 24 CE).",
                "No cabe sancionar sin prueba suficiente y concreta del hecho infractor."
            ]
        },
        "secondary": [],
        "proof_requests": [],
    }

    classification = payload.get("classification")
    latest_extraction = payload.get("latest_extraction")
    timeline = payload.get("timeline")

    if is_traffic_case(classification):

        infraction_type = detect_infraction_type(latest_extraction)

        if infraction_type == "movil":
            attack_plan["secondary"].append({
                "title": "Insuficiencia probatoria en sanción por uso del móvil",
                "points": [
                    "Debe acreditarse de forma concreta el uso manual.",
                    "Si no existe prueba objetiva, la sanción vulnera la presunción de inocencia."
                ]
            })
            attack_plan["proof_requests"] += [
                "Boletín/acta completa del agente.",
                "Descripción detallada del hecho.",
                "Material gráfico si existiera."
            ]

        if infraction_type == "velocidad":
            attack_plan["secondary"].append({
                "title": "Prueba técnica insuficiente (radar)",
                "points": [
                    "Debe constar identificación del cinemómetro y certificación vigente.",
                    "Debe acreditarse margen aplicado."
                ]
            })
            attack_plan["proof_requests"] += [
                "Capturas completas.",
                "Certificado de verificación/calibración.",
                "Identificación del equipo."
            ]

        # Antigüedad simple
        tl = (timeline or {}).get("timeline") or []
        if tl:
            first_date = None
            for ev in tl:
                if ev.get("date"):
                    first_date = ev["date"]
                    break

            if first_date and first_date.startswith("2010"):
                attack_plan["secondary"].insert(0, {
                    "title": "Antigüedad del expediente",
                    "points": [
                        "Corresponde acreditar notificación válida y actos interruptivos.",
                        "En su defecto, procede el archivo."
                    ]
                })

    return attack_plan


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================

def run_expediente_ai(case_id: str) -> Dict[str, Any]:

    engine = get_engine()

    with engine.begin() as conn:

        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:id ORDER BY created_at DESC LIMIT 1"),
            {"id": case_id},
        ).fetchone()

        if not row:
            return {"ok": False, "error": "No extraction available"}

        extracted_json = row[0]
        wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)

    classification = wrapper.get("classification")
    timeline = wrapper.get("timeline")
    admissibility = wrapper.get("admissibility")
    latest_extraction = wrapper.get("extracted")

    payload = {
        "classification": classification,
        "timeline": timeline,
        "admissibility": admissibility,
        "latest_extraction": latest_extraction,
    }

    attack_plan = build_attack_plan(payload)

    llm_payload = {
        **payload,
        "attack_plan": attack_plan,
    }

    draft = llm_json(PROMPT, llm_payload)

    return {
        "ok": True,
        "draft": draft,
        "classification": classification,
        "timeline": timeline,
        "admissibility": admissibility,
    }
