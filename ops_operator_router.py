from datetime import datetime, timezone
from typing import Optional, Literal, Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Ajusta estos imports a tu proyecto real
from database import get_db
from models import Case, CaseEvent

router = APIRouter(prefix="/ops/cases", tags=["ops-operator"])

def _utcnow():
    return datetime.now(timezone.utc)

def require_operator_token(x_operator_token: Optional[str] = Header(default=None)):
    if not x_operator_token:
        raise HTTPException(status_code=401, detail="Falta X-Operator-Token")
    return x_operator_token

CaseStatus = Literal[
    "pending_review",
    "manual_review",
    "ready_to_submit",
    "submitted",
]

class CaseSummaryOut(BaseModel):
    id: str
    status: str
    familia_detectada: Optional[str] = None
    confianza: Optional[float] = None
    hecho: Optional[str] = None
    updated_at: Optional[datetime] = None

class ApproveBody(BaseModel):
    note: Optional[str] = None

class ManualBody(BaseModel):
    motivo: str = Field(..., min_length=3)

class NoteBody(BaseModel):
    note: str = Field(..., min_length=1)

class OverrideFamilyBody(BaseModel):
    familia: str = Field(..., min_length=1)
    motivo: str = Field(..., min_length=3)

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

def _append_event(db: Session, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    evt = CaseEvent(
        case_id=case_id,
        type=event_type,
        payload=payload or {},
        created_at=_utcnow(),
    )
    db.add(evt)

def _read_classification(case: Case):
    payload = _safe_getattr(case, "analysis_result", "ai_result", "classifier_result", default={}) or {}
    familia = (
        _safe_getattr(case, "familia_detectada", "family_detected", "family")
        or payload.get("familia_detectada")
        or payload.get("family")
        or payload.get("family_detected")
    )
    confianza = (
        _safe_getattr(case, "confianza", "confidence")
        or payload.get("confianza")
        or payload.get("confidence")
    )
    hecho = (
        _safe_getattr(case, "hecho", "facts", "detected_facts")
        or payload.get("hecho")
        or payload.get("facts")
    )
    return familia, confianza, hecho

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

def _set_override_family(case: Case, familia: str):
    if hasattr(case, "manual_family"):
        case.manual_family = familia
        return
    if hasattr(case, "familia_manual"):
        case.familia_manual = familia
        return
    if hasattr(case, "familia_detectada"):
        case.familia_detectada = familia
        return
    raise HTTPException(status_code=500, detail="No existe campo para override de familia en Case")

@router.get("/{case_id}", response_model=CaseSummaryOut)
def get_case_detail(case_id: str, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
    case = _get_case_or_404(db, case_id)
    familia, confianza, hecho = _read_classification(case)

    return CaseSummaryOut(
        id=str(case.id),
        status=_get_status(case),
        familia_detectada=familia,
        confianza=confianza,
        hecho=hecho,
        updated_at=_safe_getattr(case, "updated_at"),
    )

@router.post("/{case_id}/approve", response_model=GenericOk)
def approve_case(case_id: str, body: ApproveBody, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
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

@router.post("/{case_id}/manual", response_model=GenericOk)
def send_to_manual_review(case_id: str, body: ManualBody, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
    case = _get_case_or_404(db, case_id)

    _set_status(case, "manual_review")
    _append_event(
        db,
        case_id,
        "manual_review_required",
        {"motivo": body.motivo, "at": _utcnow().isoformat()},
    )
    db.commit()
    db.refresh(case)

    return GenericOk(case_id=case_id, status=_get_status(case))

@router.post("/{case_id}/note", response_model=GenericOk)
def add_operator_note(case_id: str, body: NoteBody, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
    case = _get_case_or_404(db, case_id)

    _append_event(
        db,
        case_id,
        "operator_note",
        {"note": body.note, "at": _utcnow().isoformat()},
    )
    db.commit()

    return GenericOk(case_id=case_id, status=_get_status(case))

@router.post("/{case_id}/override-family", response_model=GenericOk)
def override_family(case_id: str, body: OverrideFamilyBody, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
    case = _get_case_or_404(db, case_id)

    _set_override_family(case, body.familia)
    _append_event(
        db,
        case_id,
        "operator_override_family",
        {"familia": body.familia, "motivo": body.motivo, "at": _utcnow().isoformat()},
    )
    db.commit()
    db.refresh(case)

    return GenericOk(case_id=case_id, status=_get_status(case))

@router.post("/{case_id}/submit", response_model=SubmitDGTOut)
def submit_to_dgt(case_id: str, body: SubmitDGTBody, db: Session = Depends(get_db), _: str = Depends(require_operator_token)):
    case = _get_case_or_404(db, case_id)
    current_status = _get_status(case)

    if current_status != "ready_to_submit" and not body.force:
        raise HTTPException(
            status_code=400,
            detail="El expediente debe estar en ready_to_submit antes de enviarse a DGT",
        )

    dgt_id = f"DGT-{case_id}-{int(datetime.now().timestamp())}"
    submitted_at = _utcnow()

    _set_status(case, "submitted")

    if hasattr(case, "submitted_at"):
        case.submitted_at = submitted_at
    if hasattr(case, "dgt_submission_id"):
        case.dgt_submission_id = dgt_id
    if hasattr(case, "dgt_id"):
        case.dgt_id = dgt_id

    _append_event(
        db,
        case_id,
        "submitted_to_dgt",
        {
            "document_url": body.document_url,
            "dgt_id": dgt_id,
            "submitted_at": submitted_at.isoformat(),
            "mode": "stub",
        },
    )
    db.commit()
    db.refresh(case)

    return SubmitDGTOut(
        case_id=case_id,
        status=_get_status(case),
        dgt_id=dgt_id,
        submitted_at=submitted_at,
    )
