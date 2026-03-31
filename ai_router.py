import os
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from generate import generate_dgt_for_case

router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


# -------------------------------------------------------
# 🧠 NUEVO: cálculo de plazos
# -------------------------------------------------------

def _build_deadlines():
    today = datetime.utcnow()

    before_deadline = today + timedelta(days=20)
    after_deadline = today + timedelta(days=45)

    return {
        "before_resource_deadline": before_deadline.isoformat(),
        "after_resource_deadline": after_deadline.isoformat(),
        "before_text": "Plazo orientativo de 20 días para alegaciones",
        "after_text": "Plazo orientativo tras resolución",
    }


# -------------------------------------------------------
# 🧠 NUEVO: destino envío
# -------------------------------------------------------

def _build_delivery(result):
    raw = json.dumps(result, ensure_ascii=False).lower()

    if "ayuntamiento" in raw or "policia local" in raw:
        return {
            "destination": "Ayuntamiento / Policía Local",
            "address": "Registro electrónico municipal o sede electrónica del Ayuntamiento",
        }

    return {
        "destination": "DGT - Dirección General de Tráfico",
        "address": "https://sede.dgt.gob.es",
    }


# -------------------------------------------------------
# NORMALIZACIÓN EXISTENTE + EXTENDIDA
# -------------------------------------------------------

def _normalize_ai_payload(result):

    familia = result.get("familia") or result.get("tipo_infraccion") or ""
    confianza = result.get("confianza") or 0
    hecho = result.get("hecho") or result.get("hecho_imputado") or ""
    admisibilidad = result.get("admisibilidad") or ""
    accion = result.get("accion") or ""

    # 🔥 AQUÍ METEMOS LOS NUEVOS CAMPOS
    deadlines = _build_deadlines()
    delivery = _build_delivery(result)

    return {
        "familia": str(familia),
        "confianza": float(confianza),
        "hecho": str(hecho),
        "admisibilidad": str(admisibilidad),
        "accion": str(accion),

        # 👇 NUEVO
        "deadlines": deadlines,
        "delivery": delivery,

        "raw_result": result,
    }


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------

@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)

        if not isinstance(result, dict):
            result = {"raw_result": result}

        ai_payload = _normalize_ai_payload(result)

        engine = get_engine()

        with engine.begin() as conn:
            conn.execute(
                text(
                    '''
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (:id, 'ai_expediente_result', CAST(:payload AS JSONB), NOW())
                    '''
                ),
                {
                    "id": req.case_id,
                    "payload": json.dumps(ai_payload, ensure_ascii=False),
                },
            )

        return {
            "ok": True,
            "case_id": req.case_id,
            "ai_payload": ai_payload,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")