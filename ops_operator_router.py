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
        text("SELECT id, status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row


def _get_status(conn, case_id: str) -> str:
    row = conn.execute(
        text("SELECT status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    return row[0] if row else "pending_review"


def _set_status(conn, case_id: str, status: str):
    conn.execute(
        text("UPDATE cases SET status = :status WHERE id = :id"),
        {"id": case_id, "status": status},
    )


def _append_event(conn, case_id: str, event_type: str, payload: Dict[str, Any]):
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload),
        },
    )


def _load_interesado(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interested_data,'{}'::jsonb) FROM cases WHERE id = :id"),
        {"id": case_id},
    ).fetchone()
    return row[0] if row and isinstance(row[0], dict) else {}


# =========================================
# 🔥 NUEVO ENDPOINT CLAVE (COMPLETO)
# =========================================

@router.post("/{case_id}/override-family-and-regenerate")
def override_family_and_regenerate(
    case_id: str,
    body: OverrideAndRegenerateBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    # 1️⃣ Guardar override
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

    # 2️⃣ 🔥 GENERACIÓN REAL
    try:
        req = GenerateRequest(
            case_id=case_id,
            interesado=interesado,
            tipo=body.familia,  # 👈 CLAVE
        )
        generate_dgt(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error regenerando recurso: {e}")

    # 3️⃣ Guardar estado + evento + RESULTADO IA PARA PANEL
    with engine.begin() as conn:
        _set_status(conn, case_id, "generated")

        # 👉 ESTO ARREGLA EL PANEL
        _append_event(
            conn,
            case_id,
            "ai_expediente_result",
            {
                "family": body.familia,
                "confidence": 1.0,
                "hecho": "Recurso regenerado manualmente",
                "recommended_action": "SUBMIT",
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
            },
        )

        status = _get_status(conn, case_id)

    return {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "familia_correcta": body.familia,
    }