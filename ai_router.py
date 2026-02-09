# Endpoint para ejecutar el Modo Dios sobre un expediente
# Incluye override de prueba (TEST_ONLY) para forzar admisibilidad

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai

router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        # Ejecutamos la IA normal
        result = run_expediente_ai(req.case_id)

        # 🔓 OVERRIDE DE PRUEBA (Opción B)
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false) "
                    "FROM cases WHERE id=:id"
                ),
                {"id": req.case_id},
            ).fetchone()

        test_mode = bool(row[0]) if row else False
        override_deadlines = bool(row[1]) if row else False

        if test_mode and override_deadlines:
            # Forzamos admisibilidad SOLO PARA PRUEBA
            result.setdefault("admissibility", {})
            result["admissibility"]["admissibility"] = "ADMISSIBLE"

            # Ajustamos acción recomendada para OPS
            result.setdefault("phase", {})
            result["phase"].setdefault("recommended_action", {})
            result["phase"]["recommended_action"]["action"] = "GENERATE_RESOURCE_TEST"

            # Marcamos el expediente como listo para generar (sin presentar)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE cases SET status='ready_to_pay', updated_at=NOW() WHERE id=:id"
                    ),
                    {"id": req.case_id},
                )

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error Modo Dios: {e}"
        )
