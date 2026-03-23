# debug_test_classifier.py
# Endpoint admin para probar clasificación masiva de hechos denunciados.
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from analyze import _score_infraction_families
from generate import resolve_infraction_type, get_hecho_para_recurso


class ClassifierTestCase(BaseModel):
    hecho: str = Field(..., min_length=1)
    familia_esperada: str = Field(..., min_length=1)
    extra_core: Optional[Dict[str, Any]] = None


class ClassifierTestRequest(BaseModel):
    casos: List[ClassifierTestCase]


class ClassifierTestResult(BaseModel):
    hecho: str
    familia_esperada: str
    familia_detectada: str
    correcto: bool
    hecho_para_recurso: str
    scores: Dict[str, int]


class ClassifierTestResponse(BaseModel):
    ok: bool
    total: int
    aciertos: int
    fallos: int
    accuracy: float
    resultados: List[ClassifierTestResult]


def _require_admin_token(x_admin_token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_TOKEN no está configurado en el backend.",
        )
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _build_core_from_case(caso: ClassifierTestCase) -> Dict[str, Any]:
    core: Dict[str, Any] = {
        "hecho_imputado": caso.hecho,
        "hecho_denunciado_literal": caso.hecho,
        "hecho_denunciado_resumido": caso.hecho,
        "raw_text_blob": caso.hecho,
    }
    if caso.extra_core:
        core.update(caso.extra_core)
    return core


@router.post("/test-classifier", response_model=ClassifierTestResponse)
def debug_test_classifier(
    payload: ClassifierTestRequest,
    x_admin_token: str = Header(..., alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)

    resultados: List[ClassifierTestResult] = []
    aciertos = 0

    for caso in payload.casos:
        core = _build_core_from_case(caso)
        familia_detectada = resolve_infraction_type(core)
        hecho_para_recurso = get_hecho_para_recurso(core)
        scores = _score_infraction_families(caso.hecho, core)
        correcto = familia_detectada == caso.familia_esperada

        if correcto:
            aciertos += 1

        resultados.append(
            ClassifierTestResult(
                hecho=caso.hecho,
                familia_esperada=caso.familia_esperada,
                familia_detectada=familia_detectada,
                correcto=correcto,
                hecho_para_recurso=hecho_para_recurso,
                scores=scores,
            )
        )

    total = len(resultados)
    fallos = total - aciertos
    accuracy = round((aciertos / total), 4) if total else 0.0

    return ClassifierTestResponse(
        ok=True,
        total=total,
        aciertos=aciertos,
        fallos=fallos,
        accuracy=accuracy,
        resultados=resultados,
    )
