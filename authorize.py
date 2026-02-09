from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import text
import json

from database import get_engine

router = APIRouter(prefix="/cases", tags=["authorization"])

@router.post("/{case_id}/authorize")
def authorize_case(case_id: str, request: Request, payload: dict):
    engine = get_engine()
    ip = request.client.host if request.client else None

    version = payload.get("version", "v1")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT contact_name FROM cases WHERE id=:id"),
            {"id": case_id}
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Expedient no trobat")

        text_authorization = (
            f"Autorització DGT homologada. Versió {version}."
        )

        conn.execute(
            text(
                "UPDATE cases SET authorized=true, authorized_at=NOW(), authorized_ip=:ip, authorized_text=:txt WHERE id=:id"
            ),
            {"id": case_id, "ip": ip, "txt": text_authorization}
        )

        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) VALUES (:id, 'client_authorized', CAST(:p AS JSONB), NOW())"
            ),
            {"id": case_id, "p": json.dumps({"version": version})}
        )

    return {"ok": True}