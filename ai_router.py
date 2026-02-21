import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from generate import generate_dgt_for_case

# ✅ IMPORTANTE: router debe definirse ANTES de usar @router.post(...)
router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        # 1) Ejecutar IA
        result = run_expediente_ai(req.case_id)

        engine = get_engine()
        always_generate = (os.getenv("ALWAYS_GENERATE_ON_AI_RUN") or "").strip() == "1"

        # 2) Flags test_mode/override (si existen)
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

            # 3) MODO DIOS → GENERAR (flag global o modo prueba)
            if always_generate or (test_mode and override_deadlines):
                generate_dgt_for_case(conn, req.case_id)

                # dejamos el caso en generated para que OPS lo muestre con claridad
                conn.execute(
                    text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:id"),
                    {"id": req.case_id},
                )

                result["note"] = "Modo Dios: recurso generado para revisión (sin presentar)"

        return result

    except HTTPException:
        # ✅ NO convertir 422/400/etc. en 500
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error Modo Dios (inesperado): {e}")