import json
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from dgt_templates import (
    build_dgt_alegaciones_text,
    build_dgt_reposicion_text,
)

router = APIRouter(tags=["generate"])

# ==========================
# CONFIG
# ==========================
RTM_DGT_GENERATION_MODE = (
    os.getenv("RTM_DGT_GENERATION_MODE") or "AI_FIRST"
).strip().upper()


# ==========================
# HELPERS
# ==========================
def _load_interested_data_from_cases(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text("SELECT COALESCE(interest
