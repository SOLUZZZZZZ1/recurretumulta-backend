import os
from fastapi import APIRouter, HTTPException, Header
from sqlalchemy import text
from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-override"])


def _require_operator(x_operator_token: str | None):
    """
    Protege el endpoint con OPERATOR_TOKEN
    """
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=500,
            detail
