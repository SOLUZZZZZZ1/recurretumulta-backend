"""Shared production OPS token check. No data or service side effects."""
import os
import secrets
from fastapi import HTTPException


def require_operator(token):
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPS_AUTH_UNAVAILABLE")
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized operator")
