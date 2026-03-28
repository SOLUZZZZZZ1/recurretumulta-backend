from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field


FamilyValue = Literal[
    "semaforo",
    "vehiculo",
    "velocidad",
    "estacionamiento",
    "documentacion",
    "cinturon",
    "movil",
    "alcohol_drogas",
    "itv",
    "otras",
]


class OverrideFamilyRegenerateIn(BaseModel):
    family: FamilyValue = Field(..., description="Familia correcta fijada por operador")
    reason: Optional[str] = Field(default=None, description="Motivo opcional del override")
    regenerate_pdf: bool = Field(default=True, description="Si true, regenera también PDF/DOCX")


class OverrideFamilyRegenerateOut(BaseModel):
    ok: bool
    case_id: str
    family_ai_original: Optional[str] = None
    family_corrected: str
    status: str
    message: str
    document_ids: list[str] = []