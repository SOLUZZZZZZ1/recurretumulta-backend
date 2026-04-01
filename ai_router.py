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

    familia_str = _as_string(familia)
    confianza_num = _as_confidence(confianza)
    hecho_str = _as_string(hecho)
    admisibilidad_str = _as_string(admisibilidad)

    return {
        "familia": familia_str,
        "confianza": confianza_num,
        "hecho": hecho_str,
        "admisibilidad": admisibilidad_str,
        "accion": accion,
        # Compatibilidad con el panel OPS PRO
        "classifier_result": {
            "family": familia_str,
            "confidence": confianza_num,
        },
        "tipo_infraccion": familia_str,
        "tipo_infraccion_confidence": confianza_num,
        "hecho_imputado": hecho_str,
        "raw_result": result,
    }


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)
        if not isinstance(result, dict):
            result = {"raw_result": result}

        engine = get_engine()
        ai_payload = _normalize_ai_payload(result)

        with engine.begin() as conn:
            # 1) Guardar resultado IA
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

            # 2) MODO DIOS: generar SIEMPRE para revisión
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

                conn.execute(
                    text(
                        '''
                        INSERT INTO events(case_id, type, payload, created_at)
                        VALUES (:id, 'resource_generated_auto', CAST(:payload AS JSONB), NOW())
                        '''
                    ),
                    {
                        "id": req.case_id,
                        "payload": json.dumps(
                            {
                                "ok": True,
                                "mode": "modo_dios",
                                "note": "Recurso generado automáticamente para revisión",
                            },
                            ensure_ascii=False,
                        ),
                    },
                )

                result["note"] = "Modo Dios: recurso generado para revisión (sin presentar)"
            except Exception as gen_err:
                conn.execute(
                    text(
                        '''
                        INSERT INTO events(case_id, type, payload, created_at)
                        VALUES (:id, 'resource_generation_failed', CAST(:payload AS JSONB), NOW())
                        '''
                    ),
                    {
                        "id": req.case_id,
                        "payload": json.dumps(
                            {
                                "ok": False,
                                "mode": "modo_dios",
                                "error": str(gen_err),
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
                result["warning"] = f"Generación falló: {gen_err}"

        return {
            "ok": True,
            "case_id": req.case_id,
            "ai_payload": ai_payload,
            "result": result,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")
