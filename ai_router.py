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


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return None


def _safe_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value == "":
            return None
    try:
        return float(value)
    except Exception:
        return None


def _get_by_path(obj, path, default=None):
    try:
        current = obj
        for part in path.split("."):
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return default
        return current if current is not None else default
    except Exception:
        return default


def _extract_confidence(result):
    raw_confidence = _first_non_empty(
        result.get("confianza"),
        result.get("confidence"),
        result.get("tipo_infraccion_confidence"),
        result.get("score"),
        result.get("probability"),
        _get_by_path(result, "classifier_result.confidence"),
        _get_by_path(result, "classifier_result.score"),
        _get_by_path(result, "raw_result.confianza"),
        _get_by_path(result, "raw_result.confidence"),
        _get_by_path(result, "raw_result.tipo_infraccion_confidence"),
        _get_by_path(result, "raw_result.score"),
        _get_by_path(result, "raw_result.probability"),
        _get_by_path(result, "raw_result.classifier_result.confidence"),
        _get_by_path(result, "raw_result.classifier_result.score"),
    )
    confidence = _safe_float(raw_confidence)
    if confidence is None:
        return None

    if confidence > 1:
        confidence = confidence / 100.0

    if confidence < 0:
        confidence = 0.0
    if confidence > 1:
        confidence = 1.0

    return round(confidence, 4)


def _extract_familia(result):
    return str(_first_non_empty(
        result.get("familia"),
        result.get("tipo_infraccion"),
        result.get("family"),
        _get_by_path(result, "classifier_result.family"),
        _get_by_path(result, "raw_result.familia"),
        _get_by_path(result, "raw_result.tipo_infraccion"),
        _get_by_path(result, "raw_result.family"),
        _get_by_path(result, "raw_result.classifier_result.family"),
        "",
    ))


def _extract_hecho(result):
    return str(_first_non_empty(
        result.get("hecho"),
        result.get("hecho_imputado"),
        _get_by_path(result, "arguments.hecho"),
        _get_by_path(result, "raw_result.hecho"),
        _get_by_path(result, "raw_result.hecho_imputado"),
        "",
    ))


def _parse_date_candidate(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None

    normalized = text_value.replace("Z", "+00:00")
    for candidate in (
        normalized,
        normalized[:10],
    ):
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            pass

    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text_value[:10], fmt)
        except Exception:
            pass

    return None


def _extract_reference_date(result):
    candidates = [
        result.get("fecha_notificacion"),
        result.get("fecha"),
        result.get("notification_date"),
        result.get("notice_date"),
        result.get("boletin_fecha"),
        result.get("fecha_denuncia"),
        _get_by_path(result, "raw_result.fecha_notificacion"),
        _get_by_path(result, "raw_result.fecha"),
        _get_by_path(result, "raw_result.notification_date"),
        _get_by_path(result, "raw_result.notice_date"),
        _get_by_path(result, "raw_result.boletin_fecha"),
        _get_by_path(result, "raw_result.fecha_denuncia"),
    ]
    for candidate in candidates:
        parsed = _parse_date_candidate(candidate)
        if parsed is not None:
            return parsed
    return datetime.utcnow()


def _build_deadlines(result):
    base_date = _extract_reference_date(result)

    before_deadline = base_date + timedelta(days=20)
    after_deadline = base_date + timedelta(days=30)

    return {
        "reference_date": base_date.isoformat(),
        "before_resource_deadline": before_deadline.isoformat(),
        "after_resource_deadline": after_deadline.isoformat(),
        "before_text": "20 días naturales desde la fecha de referencia para alegaciones o pronto pago.",
        "after_text": "1 mes orientativo para recurso administrativo desde la resolución o acto recurrible.",
    }


def _build_delivery(result):
    raw = json.dumps(result, ensure_ascii=False).lower()

    if "ayuntamiento" in raw or "policia local" in raw:
        return {
            "destination": "Ayuntamiento / Policía Local",
            "address": "Registro electrónico municipal o sede electrónica del Ayuntamiento",
            "channel": "registro_electronico",
            "entity": "ayuntamiento",
        }

    if "ministerio del interior" in raw or "trafico" in raw or "dgt" in raw or "jefatura de trafico" in raw:
        return {
            "destination": "DGT - Dirección General de Tráfico",
            "address": "https://sede.dgt.gob.es",
            "channel": "sede_dgt",
            "entity": "dgt",
        }

    return {
        "destination": "Registro Electrónico General",
        "address": "https://rec.redsara.es/registro/action/are/acceso.do",
        "channel": "registro_electronico",
        "entity": "ministerio_interior",
    }


def _normalize_ai_payload(result):
    familia = _extract_familia(result)
    confianza = _extract_confidence(result)
    hecho = _extract_hecho(result)
    admisibilidad = str(_first_non_empty(
        result.get("admisibilidad"),
        result.get("admissibility"),
        _get_by_path(result, "raw_result.admisibilidad"),
        _get_by_path(result, "raw_result.admissibility"),
        "",
    ))
    accion = str(_first_non_empty(
        result.get("accion"),
        result.get("action"),
        _get_by_path(result, "recommended_action.action"),
        _get_by_path(result, "phase.recommended_action.action"),
        _get_by_path(result, "raw_result.accion"),
        _get_by_path(result, "raw_result.action"),
        _get_by_path(result, "raw_result.recommended_action.action"),
        _get_by_path(result, "raw_result.phase.recommended_action.action"),
        "",
    ))

    deadlines = _build_deadlines(result)
    delivery = _build_delivery(result)

    return {
        "familia": familia,
        "confianza": confianza,
        "hecho": hecho,
        "admisibilidad": admisibilidad,
        "accion": accion,
        "classifier_result": {
            "family": familia,
            "confidence": confianza,
        },
        "tipo_infraccion": familia,
        "tipo_infraccion_confidence": confianza,
        "deadlines": deadlines,
        "delivery": delivery,
        "raw_result": result,
    }


def _append_event(conn, case_id: str, event_type: str, payload):
    conn.execute(
        text(
            '''
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:id, :type, CAST(:payload AS JSONB), NOW())
            '''
        ),
        {
            "id": case_id,
            "type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


@router.post("/expediente/run")
def run_ai(req: RunExpedienteAI):
    try:
        result = run_expediente_ai(req.case_id)

        if not isinstance(result, dict):
            result = {"raw_result": result}

        ai_payload = _normalize_ai_payload(result)

        engine = get_engine()

        with engine.begin() as conn:
            _append_event(conn, req.case_id, "ai_expediente_result", ai_payload)

        generation_result = None
        generation_error = None

        try:
            generation_result = generate_dgt_for_case(req.case_id)
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:id"),
                    {"id": req.case_id},
                )
                _append_event(
                    conn,
                    req.case_id,
                    "resource_generated_auto",
                    {
                        "ok": True,
                        "mode": "ai_run_auto_generate",
                        "generated_at": datetime.utcnow().isoformat(),
                        "result": generation_result,
                    },
                )
        except Exception as gen_exc:
            generation_error = str(gen_exc)
            with engine.begin() as conn:
                _append_event(
                    conn,
                    req.case_id,
                    "resource_generation_failed",
                    {
                        "ok": False,
                        "mode": "ai_run_auto_generate",
                        "error": generation_error,
                        "generated_at": datetime.utcnow().isoformat(),
                    },
                )

        return {
            "ok": True,
            "case_id": req.case_id,
            "ai_payload": ai_payload,
            "generation_ok": generation_error is None,
            "generation_result": generation_result,
            "generation_error": generation_error,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error IA: {e}")
