from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from schemas_regenerate import (
    OverrideFamilyRegenerateIn,
    OverrideFamilyRegenerateOut,
)
from service_regenerate import regenerate_case_with_forced_family

router = APIRouter(prefix="/ops/cases", tags=["ops"])


def get_db():
    raise NotImplementedError


def require_operator():
    return {"role": "operator", "name": "operator"}


def get_case_or_404(db, case_id: str):
    case = None
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return case


@router.post(
    "/{case_id}/override-family-and-regenerate",
    response_model=OverrideFamilyRegenerateOut,
)
def override_family_and_regenerate(
    case_id: str,
    body: OverrideFamilyRegenerateIn,
    db = Depends(get_db),
    operator = Depends(require_operator),
):
    case = get_case_or_404(db, case_id)

    try:
        result = regenerate_case_with_forced_family(
            db=db,
            case=case,
            forced_family=body.family,
            reason=body.reason,
            actor=operator.get("name", "operator"),
            regenerate_pdf=body.regenerate_pdf,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error regenerando recurso: {e}")

    return OverrideFamilyRegenerateOut(
        ok=True,
        case_id=result.case_id,
        family_ai_original=result.family_ai_original,
        family_corrected=result.family_corrected,
        status=result.status,
        message="Familia corregida y recurso regenerado correctamente",
        document_ids=result.document_ids,
    )