# debug_generate_preview.py
import json
import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from database import get_engine
from generate import (
    resolve_infraction_type,
    get_hecho_para_recurso,
    _score_infraction_from_core,
    _select_template,
)

router = APIRouter(prefix="/debug", tags=["debug"])


def _require_admin_token(x_admin_token: str | None):
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _load_core(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT extracted_json
            FROM extractions
            WHERE case_id=:case_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No extraction found")

    wrapper = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    core = wrapper.get("extracted") or wrapper

    if not isinstance(core, dict):
        raise HTTPException(status_code=500, detail="Invalid extraction format")

    return core


@router.post("/generate-preview/{case_id}")
def generate_preview(
    case_id: str,
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
):

    _require_admin_token(x_admin_token)

    engine = get_engine()

    with engine.begin() as conn:
        core = _load_core(conn, case_id)

    familia = resolve_infraction_type(core)

    hecho = get_hecho_para_recurso(core)

    tpl, kind = _select_template(core, familia, "dgt")

    scores = _score_infraction_from_core(core)

    return {
        "ok": True,
        "case_id": case_id,
        "familia_resuelta": familia,
        "hecho_para_recurso": hecho,
        "template_usado": kind,
        "scores": scores,
        "preview_asunto": tpl.get("asunto"),
        "preview_cuerpo": tpl.get("cuerpo"),
    }