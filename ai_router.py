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


def _as_string(value):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_confidence(value):
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except Exception:
        return 0


def _normalize_ai_payload(result):
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
        "extracted.tipo_infraccion",
        "extracted.familia_resuelta",
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
        "extracted.tipo_infraccion_confidence",
    )

    hecho = _pick(
        result,
        "hecho_para_recurso",
        "hecho_imputado",
        "hecho_limpio",
        "hecho_reconstruido",
        "hecho_crudo",
        "arguments.hecho",
        "arguments.hecho_imputado",
        "arguments.fact",
        "arguments.facts",
        "result.hecho",
        "result.fact",
        "extracted.hecho_para_recurso",
        "extracted.hecho_imputado",
        "extracted.hecho_limpio",
        "extracted.hecho_denunciado_literal",
        "extracted.hecho_denunciado_resumido",
    )

    admisibilidad = _pick(
        result,
        "resultado_estrategico",
        "admissibility.admissibility",
        "phase.admissibility",
        "result.admissibility",
        "result.admisibilidad",
        "extracted.resultado_estrategico",
        "extracted.admissibility.admissibility",
    )

    accion_raw = _pick(
        result,
        "phase.recommended_action",
        "recommended_action",
        "phase.recommended_action.action",
        "recommended_action.action",
        "result.recommended_action",
        "result.accion_recomendada",
        "modelo_defensa",
        "extracted.modelo_defensa",
    )

    if isinstance(accion_raw, dict):
        accion = _pick(
            {"x": accion_raw},
            "x.action",
            "x.accion",
            "x.name",
            "x.tipo",
        ) or _as_string(accion_raw)
    else:
        accion = _as_string(accion_raw)

    return {
        "familia": _as_string(familia),
        "confianza": _as_confidence(confianza),
        "hecho": _as_string(hecho),
        "admisibilidad": _as_string(admisibilidad),
        "accion": accion,
        "raw_result": result,
    }


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)
        if not isinstance(result, dict):
            result = {"raw_result": result}

        engine = get_engine()
        always_generate = (os.getenv("ALWAYS_GENERATE_ON_AI_RUN") or "").strip() == "1"

        ai_payload = _normalize_ai_payload(result)

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

        return {
            "ok": True,
            "case_id": req.case_id,
            "ai_payload": ai_payload,
            "result": result,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")
