# ai_router.py
# Endpoint para ejecutar el Modo Dios sobre un expediente

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai.expediente_engine import run_expediente_ai

router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        return run_expediente_ai(req.case_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error Modo Dios: {e}"
        )
