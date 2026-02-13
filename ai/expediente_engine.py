import json
from typing import Any, Dict, Optional, List
from datetime import datetime

from sqlalchemy import text
from database import get_engine
from ai.prompts.domain_selector import detect_domain
from ai.prompts.traffic.module_base import base_attack_plan
from ai.prompts.traffic.module_movil import module_movil
from ai.prompts.traffic.module_velocidad import module_velocidad
from ai.prompts.traffic.module_antiguedad import module_antiguedad
from ai.prompts.draft_recurso import PROMPT
from ai.llm_utils import llm_json  # usa tu helper real


# =====================================================
# DETECTAR TIPO DE INFRACCIÓN (SIMPLE Y DETERMINISTA)
# =====================================================

def detect_infraction_type(latest_extraction: Dict[str, Any]) -> str:
    text_blob = json.dumps(latest_extraction or {}).lower()

    if "móvil" in text_blob or "telefono" in text_blob:
        return "movil"

    if "km/h" in text_blob or "radar" in text_blob or "cinemómetro" in text_blob:
        return "velocidad"

    return "generic"


# =====================================================
# CONSTRUIR ATTACK PLAN COMBINABLE
# =====================================================

def build_attack_plan(payload: Dict[str, Any]) -> Dict[str, Any]:

    attack_plan = base_attack_plan()

    domain = detect_domain(payload)
    latest_extraction = payload.get("latest_extraction") or {}
    timeline = payload.get("timeline") or {}

    if domain == "traffic":

        infraction_type = detect_infraction_type(latest_extraction)

        # Módulo principal
        if infraction_type == "movil":
            module = module_movil()
        elif infraction_type == "velocidad":
            module = module_velocidad()
        else:
            module = {}

        # Combinar principal
        if module:
            attack_plan["primary"] = module.get("primary_attack", attack_plan["primary"])
            attack_plan["secondary"] += module.get("secondary_attacks", [])
            attack_plan["proof_requests"] += module.get("proof_requests", [])

        # Módulo antigüedad transversal
        antig = module_antiguedad(timeline)
        if antig:
            attack_plan["secondary"].insert(0, antig["primary_attack"])
            attack_plan["proof_requests"] += antig.get("proof_requests", [])

    return attack_plan


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================

def run_expediente_ai(case_id: str) -> Dict[str, Any]:

    engine = get_engine()

    with engine.begin() as conn:

        # ==============================
        # Extraer última extracción
        # ==============================
        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:id ORDER BY created_at DESC LIMIT 1"),
            {"id": case_id},
        ).fetchone()

        if not row:
            return {"ok": False, "error": "No extraction available"}

        extracted_json = row[0]
        wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)

    # ====================================
    # Mantener estructura original intacta
    # ====================================
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

    # ====================================
    # Construcción modular del attack_plan
    # ====================================
    attack_plan = build_attack_plan(payload)

    llm_payload = {
        **payload,
        "attack_plan": attack_plan,
    }

    # ====================================
    # Llamada LLM (sin tocar tu lógica)
    # ====================================
    draft = llm_json(PROMPT, llm_payload)

    return {
        "ok": True,
        "draft": draft,
        "classification": classification,
        "timeline": timeline,
        "admissibility": admissibility,
    }
