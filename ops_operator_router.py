from datetime import datetime, timezone
import json
import os
from typing import Optional, Any, Dict

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from generate import GenerateRequest, generate_dgt

router = APIRouter(prefix="/ops/cases", tags=["ops-operator"])


def _utcnow():
    return datetime.now(timezone.utc)


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")
    return token


class ApproveBody(BaseModel):
    note: Optional[str] = None


class ManualBody(BaseModel):
    motivo: str = Field(..., min_length=3)


class NoteBody(BaseModel):
    note: str = Field(..., min_length=1)


class OverrideFamilyBody(BaseModel):
    familia: str = Field(..., min_length=1)
    motivo: str = Field(..., min_length=3)


class OverrideAndRegenerateBody(BaseModel):
    familia: str = Field(..., min_length=1)
    motivo: str = Field(..., min_length=3)


class SubmitDGTBody(BaseModel):
    document_url: Optional[str] = None
    force: bool = False


def _case_or_404(conn, case_id: str):
    row = conn.execute(
        text(
            """
            SELECT id, status, updated_at
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return {
        "id": str(row[0]),
        "status": row[1] or "pending_review",
        "updated_at": row[2],
    }


def _get_status(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row[0] or "pending_review"


def _set_status(conn, case_id: str, status: str):
    conn.execute(
        text(
            """
            UPDATE cases
            SET status = :status, updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": case_id, "status": status},
    )


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}),
        },
    )


def _load_interesado(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row or not row[0]:
        return {}
    data = row[0]
    return data if isinstance(data, dict) else {}


@router.get("/{case_id}")
def get_case_detail(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        case = _case_or_404(conn, case_id)

        evs = conn.execute(
            text(
                """
                SELECT payload
                FROM events
                WHERE case_id = :id AND type = 'ai_expediente_result'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()

        payload = evs[0] if evs and evs[0] else {}
        if not isinstance(payload, dict):
            payload = {}

        familia = (
            payload.get("classifier_result", {}).get("family")
            or payload.get("familia_detectada")
            or payload.get("familia")
            or payload.get("family")
        )
        confianza = (
            payload.get("classifier_result", {}).get("confidence")
            or payload.get("confianza")
            or payload.get("confidence")
        )
        hecho = (
            payload.get("arguments", {}).get("hecho")
            or payload.get("hecho")
            or payload.get("hecho_para_recurso")
            or payload.get("facts")
            or payload.get("detected_facts")
        )

        return {
            "id": case["id"],
            "status": case["status"],
            "familia_detectada": familia,
            "confianza": confianza,
            "hecho": hecho,
            "updated_at": case["updated_at"],
        }


@router.post("/{case_id}/approve")
def approve_case(
    case_id: str,
    body: ApproveBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "ready_to_submit")
        _append_event(
            conn,
            case_id,
            "operator_approved",
            {"note": body.note, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/manual")
def send_to_manual_review(
    case_id: str,
    body: ManualBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "manual_review")
        _append_event(
            conn,
            case_id,
            "manual_review_required",
            {"motivo": body.motivo, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/note")
def add_operator_note(
    case_id: str,
    body: NoteBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _append_event(
            conn,
            case_id,
            "operator_note",
            {"note": body.note, "at": _utcnow().isoformat()},
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/override-family")
def override_family(
    case_id: str,
    body: OverrideFamilyBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "at": _utcnow().isoformat(),
            },
        )
        status = _get_status(conn, case_id)

    return {"ok": True, "case_id": case_id, "status": status}


@router.post("/{case_id}/override-family-and-regenerate")
def override_family_and_regenerate(
    case_id: str,
    body: OverrideAndRegenerateBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _append_event(
            conn,
            case_id,
            "operator_override_family",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "at": _utcnow().isoformat(),
            },
        )
        interesado = _load_interesado(conn, case_id)

    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=interesado,
            tipo=body.familia,
        )
        generate_dgt(req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error regenerando recurso: {e}")

    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        _set_status(conn, case_id, "generated")

        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "classifier_result": {
                    "family": body.familia,
                    "confidence": 1.0,
                },
                "arguments": {
                    "hecho": f"Recurso regenerado manualmente. Motivo: {body.motivo}",
                },
                "admissibility": {
                    "admissibility": "REGENERATED",
                },
                "phase": {
                    "recommended_action": {
                        "action": "REVIEW_AND_SUBMIT",
                    }
                },
                "source": "operator_override",
                "at": _utcnow().isoformat(),
            },
        )

        _append_event(
            conn,
            case_id,
            "resource_regenerated",
            {
                "familia": body.familia,
                "motivo": body.motivo,
                "at": _utcnow().isoformat(),
                "mode": "generate_dgt",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "familia_correcta": body.familia,
    }


@router.post("/{case_id}/submit")
def submit_to_dgt(
    case_id: str,
    body: SubmitDGTBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        _case_or_404(conn, case_id)
        current_status = _get_status(conn, case_id)

        if current_status != "ready_to_submit" and not body.force:
            raise HTTPException(
                status_code=400,
                detail="El expediente debe estar en ready_to_submit antes de enviarse a DGT",
            )

        dgt_id = f"DGT-{case_id}-{int(datetime.now().timestamp())}"
        submitted_at = _utcnow()

        _set_status(conn, case_id, "submitted")

        try:
            conn.execute(
                text("UPDATE cases SET submitted_at = NOW() WHERE id = :id"),
                {"id": case_id},
            )
        except Exception:
            pass

        try:
            conn.execute(
                text("UPDATE cases SET dgt_id = :dgt_id WHERE id = :id"),
                {"id": case_id, "dgt_id": dgt_id},
            )
        except Exception:
            pass

        try:
            conn.execute(
                text("UPDATE cases SET dgt_submission_id = :dgt_id WHERE id = :id"),
                {"id": case_id, "dgt_id": dgt_id},
            )
        except Exception:
            pass

        _append_event(
            conn,
            case_id,
            "submitted_to_dgt",
            {
                "document_url": body.document_url,
                "dgt_id": dgt_id,
                "submitted_at": submitted_at.isoformat(),
                "mode": "stub",
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "dgt_id": dgt_id,
        "submitted_at": submitted_at.isoformat(),
        "mode": "stub",
    }
