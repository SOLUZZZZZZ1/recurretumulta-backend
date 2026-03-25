from datetime import datetime, timezone
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# AJUSTA ESTOS IMPORTS A TU PROYECTO
from database import get_db
from models import Case, CaseEvent

router = APIRouter(prefix="/ops/cases", tags=["ops-operator"])


def _utcnow():
    return datetime.now(timezone.utc)


def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    if not x_operator_token:
        raise HTTPException(status_code=401, detail="Falta X-Operator-Token")
    return x_operator_token


class ApproveBody(BaseModel):
    note: Optional[str] = None


class SubmitDGTBody(BaseModel):
    document_url: Optional[str] = None
    force: bool = False


class GenericOk(BaseModel):
    ok: bool = True
    case_id: str
    status: str


class SubmitDGTOut(BaseModel):
    ok: bool = True
    case_id: str
    status: str
    dgt_id: str
    submitted_at: datetime
    mode: str


def _get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return case


def _safe_getattr(obj: Any, *names: str, default=None):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _set_status(case: Case, status: str):
    if hasattr(case, "status"):
        case.status = status
    elif hasattr(case, "estado"):
        case.estado = status
    else:
        raise HTTPException(status_code=500, detail="El modelo Case no tiene campo status/estado")

    if hasattr(case, "updated_at"):
        case.updated_at = _utcnow()


def _get_status(case: Case) -> str:
    return _safe_getattr(case, "status", "estado", default="pending_review")


def _append_event(db: Session, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    evt = CaseEvent(
        case_id=case_id,
        type=event_type,
        payload=payload or {},
        created_at=_utcnow(),
    )
    db.add(evt)


def _store_dgt_result(case: Case, dgt_id: str, submitted_at: datetime):
    if hasattr(case, "dgt_submission_id"):
        case.dgt_submission_id = dgt_id
    if hasattr(case, "dgt_id"):
        case.dgt_id = dgt_id
    if hasattr(case, "submitted_at"):
        case.submitted_at = submitted_at


def _fake_dgt_submit(case_id: str, document_url: Optional[str]) -> Dict[str, Any]:
    """
    STUB SERIO.
    Sustituye esta función mañana por la integración real con DGT/homologación.
    """
    return {
        "ok": True,
        "dgt_id": f"DGT-{case_id}-{int(datetime.now().timestamp())}",
        "submitted_at": _utcnow(),
        "mode": "stub",
        "document_url": document_url,
    }


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

    return GenericOk(case_id=case_id, status=_get_status(case))


@router.post("/{case_id}/submit", response_model=SubmitDGTOut)
def submit_to_dgt(
    case_id: str,
    body: SubmitDGTBody,
    db: Session = Depends(get_db),
    _: str = Depends(require_operator_token),
):
    case = _get_case_or_404(db, case_id)
    current_status = _get_status(case)

    if current_status != "ready_to_submit" and not body.force:
        raise HTTPException(
            status_code=400,
            detail="El expediente debe estar en ready_to_submit antes de enviarse a DGT",
        )

    # 1) Sustituir por llamada real homologada cuando la tengas
    result = _fake_dgt_submit(case_id=case_id, document_url=body.document_url)

    if not result.get("ok"):
        _set_status(case, "error_submission")
        _append_event(
            db,
            case_id,
            "dgt_submit_error",
            {
                "detail": result.get("detail", "Error desconocido"),
                "at": _utcnow().isoformat(),
            },
        )
        db.commit()
        raise HTTPException(status_code=502, detail=result.get("detail", "Error enviando a DGT"))

    dgt_id = result["dgt_id"]
    submitted_at = result["submitted_at"]
    mode = result.get("mode", "unknown")

    # 2) Guardar estado final
    _set_status(case, "submitted")
    _store_dgt_result(case, dgt_id=dgt_id, submitted_at=submitted_at)

    # 3) Trazabilidad
    _append_event(
        db,
        case_id,
        "submitted_to_dgt",
        {
            "dgt_id": dgt_id,
            "submitted_at": submitted_at.isoformat(),
            "mode": mode,
            "document_url": body.document_url,
        },
    )

    db.commit()
    db.refresh(case)

    return SubmitDGTOut(
        case_id=case_id,
        status=_get_status(case),
        dgt_id=dgt_id,
        submitted_at=submitted_at,
        mode=mode,
    )
