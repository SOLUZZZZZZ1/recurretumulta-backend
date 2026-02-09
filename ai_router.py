from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from generate import generate_dgt_for_case

router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        # 1) Ejecutar IA
        result = run_expediente_ai(req.case_id)

        engine = get_engine()

        # 2) Comprobar modo prueba
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false)
                    FROM cases WHERE id=:id
                    """
                ),
                {"id": req.case_id},
            ).fetchone()

            test_mode = bool(row[0]) if row else False
            override_deadlines = bool(row[1]) if row else False

            # 3) MODO DIOS → GENERAR SIEMPRE
            if test_mode and override_deadlines:
                generate_dgt_for_case(conn, req.case_id)

                # Ajustar estado solo para test
                conn.execute(
                    text(
                        "UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:id"
                    ),
                    {"id": req.case_id},
                )

                result.setdefault("note", "")
                result["note"] = "Modo Dios: recurso generado para revisión (sin presentar)"

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error Modo Dios: {e}"
        )
