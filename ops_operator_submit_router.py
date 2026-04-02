from datetime import datetime, timezone
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, get_engine
from models import Case, CaseEvent

# 🔥 NUEVO
from connectors import pick_submitter

router = APIRouter(prefix="/ops/cases", tags=["ops-operator"])


def _utcnow():
    return datetime.now(timezone.utc)


def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    if not x_operator_token:
        raise HTTPException(status_code=401, detail="Falta X-Operator-Token")
    return x_operator_token


class ApproveBody(BaseModel):
    note: Optional[str] = None


class SubmitBody(BaseModel):
    document_url: Optional[str] = None
    force: bool = False


class GenericOk(BaseModel):
    ok: bool = True
    case_id: str
    status: str


class SubmitOut(BaseModel):
    ok: bool = True
    case_id: str
    status: str
    submitted_at: datetime
    mode: str


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return case


def _set_status(case: Case, status: str):
    if hasattr(case, "status"):
        case.status = status
    elif hasattr(case, "estado"):
        case.estado = status

    if hasattr(case, "updated_at"):
        case.updated_at = _utcnow()


def _append_event(db: Session, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    evt = CaseEvent(
        case_id=case_id,
        type=event_type,
        payload=payload or {},
        created_at=_utcnow(),
    )
    db.add(evt)


@router.post("/{case_id}/approve", response_model=GenericOk)
def approve_case(
    case_id: str,
    body: ApproveBody,
    db: Session = Depends(get_db),
    _: str = Depends(require_operator_token),
):
    case = _get_case_or_404(db, case_id)

    _set_status(case, "ready_to_submit")

    _append_event(
        db,
        case_id,
        "operator_approved",
        {"note": body.note, "at": _utcnow().isoformat()},
    )

    db.commit()
    db.refresh(case)

    return GenericOk(case_id=case_id, status=case.status)


@router.post("/{case_id}/submit", response_model=SubmitOut)
def submit_case(
    case_id: str,
    body: SubmitBody,
    db: Session = Depends(get_db),
    _: str = Depends(require_operator_token),
):
    case = _get_case_or_404(db, case_id)

    if case.status != "ready_to_submit" and not body.force:
        raise HTTPException(
            status_code=400,
            detail="El expediente debe estar en ready_to_submit",
        )

    # 🔥 DESCARGAR PDF
    import requests

    if not body.document_url:
        raise HTTPException(status_code=400, detail="Falta document_url")

    resp = requests.get(body.document_url)
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="No se pudo descargar el PDF")

    pdf_bytes = resp.content

    # 🔥 ELEGIR SUBMITTER AUTOMÁTICO
    engine = get_engine()
    submitter = pick_submitter(case_id=case_id, engine=engine)

    try:
        result = submitter.submit(
            case_id=case_id,
            pdf_bytes=pdf_bytes,
        )
    except Exception as e:
        _set_status(case, "error_submission")

        _append_event(
            db,
            case_id,
            "submission_failed",
            {
                "error": str(e),
                "at": _utcnow().isoformat(),
            },
        )

        db.commit()
        raise HTTPException(status_code=502, detail=f"Error enviando: {e}")

    submitted_at = _utcnow()

    # 🔥 ESTADO FINAL
    _set_status(case, "submitted")

    _append_event(
        db,
        case_id,
        "submitted",
        {
            "submitted_at": submitted_at.isoformat(),
            "channel": getattr(submitter, "name", "unknown"),
        },
    )

    db.commit()
    db.refresh(case)

    return SubmitOut(
        case_id=case_id,
        status=case.status,
        submitted_at=submitted_at,
        mode=getattr(submitter, "name", "unknown"),
    )