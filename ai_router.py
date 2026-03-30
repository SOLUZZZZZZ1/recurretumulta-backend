import os
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from generate import generate_dgt_for_case

router = APIRouter(prefix="/ai", tags=["ai"])


class RunExpedienteAI(BaseModel):
    case_id: str = Field(...)


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        # 1️⃣ Ejecutar IA
        result = run_expediente_ai(req.case_id)

        engine = get_engine()

        # 🔥 2️⃣ GUARDAR RESULTADO PARA EL PANEL
        ai_payload = {
            "familia": result.get("familia_resuelta") or result.get("tipo_infraccion"),
            "confianza": result.get("tipo_infraccion_confidence"),
            "hecho": result.get("hecho_para_recurso") or result.get("hecho_imputado"),
            "admisibilidad": result.get("resultado_estrategico"),
            "accion": result.get("modelo_defensa"),
        }

        with engine.begin() as conn:

            # 👉 ESTE ES EL EVENTO QUE FALTABA
            conn.execute(
                text("""
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (:id, 'ai_expediente_result', CAST(:payload AS JSONB), NOW())
                """),
                {
                    "id": req.case_id,
                    "payload": json.dumps(ai_payload),
                }
            )

        # 3️⃣ Generación (modo Dios si aplica)
        always_generate = (os.getenv("ALWAYS_GENERATE_ON_AI_RUN") or "").strip() == "1"

        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false)
                    FROM cases WHERE id=:id
                """),
                {"id": req.case_id},
            ).fetchone()

            test_mode = bool(row[0]) if row else False
            override_deadlines = bool(row[1]) if row else False

            if always_generate or (test_mode and override_deadlines):
                try:
                    generate_dgt_for_case(conn, req.case_id)

                    conn.execute(
                        text("""
                            UPDATE cases
                            SET status='generated', updated_at=NOW()
                            WHERE id=:id
                        """),
                        {"id": req.case_id},
                    )

                    result["note"] = "Modo Dios: recurso generado"
                except Exception as gen_err:
                    result["warning"] = f"Generación falló: {gen_err}"

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")