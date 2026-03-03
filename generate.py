import json
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

from generate_dgt import generate_dgt_for_case
from generate_municipal import generate_municipal_for_case

router = APIRouter(tags=["generate"])


class GenerateRequest(BaseModel):
    case_id: str
    interesado: Dict[str, str] = Field(default_factory=dict)
    tipo: Optional[str] = None


def _is_municipal(core: Dict[str, Any]) -> bool:
    authority = str(core.get("authority") or "").lower().strip()
    org = str(core.get("organo") or core.get("organismo") or "").lower()
    return authority.startswith("ayuntamiento") or ("ayuntamiento" in org)


@router.post("/generate/dgt")
def generate(req: GenerateRequest) -> Dict[str, Any]:
    """Mantiene endpoint actual. Decide DGT vs Municipal leyendo extracción del case_id."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
            {"case_id": req.case_id},
        ).fetchone()

        if not row:
            return {"ok": False, "message": "No hay extracción para ese case_id.", "case_id": req.case_id}

        extracted_json = row[0]
        wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)
        core = (wrapper.get("extracted") or {}) if isinstance(wrapper, dict) else {}

        if _is_municipal(core):
            result = generate_municipal_for_case(conn, req.case_id, tipo=req.tipo)
            return {"ok": True, "message": "Recurso municipal generado en DOCX y PDF.", **result}

        result = generate_dgt_for_case(conn, req.case_id, interesado=req.interesado, tipo=req.tipo)
        return {"ok": True, "message": "Recurso generado en DOCX y PDF.", **result}
