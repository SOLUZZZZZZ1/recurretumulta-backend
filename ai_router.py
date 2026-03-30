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
    case_id: str = Field(..., description="UUID del expediente (cases.id)")


def _pick(mapping, *paths):
    for path in paths:
        current = mapping
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current.get(part)
            else:
                ok = False
                break
        if ok and current not in (None, "", [], {}):
            return current
    return None


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)
        if not isinstance(result, dict):
            result = {"raw_result": result}

        engine = get_engine()
        always_generate = (os.getenv("ALWAYS_GENERATE_ON_AI_RUN") or "").strip() == "1"

        familia = _pick(
            result,
            "familia_resuelta",
            "tipo_infraccion",
            "classification.family",
            "classification.familia",
            "classifier_result.family",
            "classifier_result.familia",
            "arguments.family",
            "arguments.familia",
            "result.family",
            "result.familia",
        )

        confianza = _pick(
            result,
            "tipo_infraccion_confidence",
            "classification.confidence",
            "classification.confianza",
            "classifier_result.confidence",
            "classifier_result.score",
            "arguments.confidence",
            "arguments.score",
            "result.confidence",
            "result.confianza",
        )

        hecho = _pick(
            result,
            "hecho_para_recurso",
            "hecho_imputado",
            "hecho_limpio",
            "arguments.hecho",
            "arguments.hecho_imputado",
            "arguments.fact",
            "arguments.facts",
            "result.hecho",
            "result.fact",
        )

        admisibilidad = _pick(
            result,
            "resultado_estrategico",
            "admissibility.admissibility",
            "phase.admissibility",
            "result.admissibility",
            "result.admisibilidad",
        )

        accion = _pick(
            result,
            "modelo_defensa",
            "phase.recommended_action.action",
            "recommended_action.action",
            "result.recommended_action",
            "result.accion_recomendada",
        )

        ai_payload = {
            "familia": familia or "",
            "confianza": confianza if confianza is not None else "",
            "hecho": hecho or "",
            "admisibilidad": admisibilidad or "",
            "accion": accion or "",
            "raw_result": result,
        }

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

            row = conn.execute(
                text(
                    '''
                    SELECT COALESCE(test_mode,false), COALESCE(override_deadlines,false)
                    FROM cases
                    WHERE id=:id
                    '''
                ),
                {"id": req.case_id},
            ).fetchone()

            test_mode = bool(row[0]) if row else False
            override_deadlines = bool(row[1]) if row else False

            if always_generate or (test_mode and override_deadlines):
                try:
                    generate_dgt_for_case(conn, req.case_id)

                    conn.execute(
                        text(
                            '''
                            UPDATE cases
                            SET status='generated', updated_at=NOW()
                            WHERE id=:id
                            '''
                        ),
                        {"id": req.case_id},
                    )

                    result["note"] = "Modo Dios: recurso generado para revisión (sin presentar)"
                except Exception as gen_err:
                    result["warning"] = f"Generación falló: {gen_err}"

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")
