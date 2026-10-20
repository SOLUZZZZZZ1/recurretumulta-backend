"""Production OPS workspace route, without business writes."""
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import get_engine
from ops_auth import require_operator
from workspace_read import build_workspace, MissingCase, MissingSchema, InvalidWorkspace, VERSION

PRIVATE = {"Cache-Control": "private, no-store", "Pragma": "no-cache",
           "Vary": "X-Operator-Token, X-RTM-Workspace-Contract", "X-Content-Type-Options": "nosniff"}


class PrivateReadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private(request):
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": {"code": "WORKSPACE_REQUEST_INVALID"}}, status_code=422)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            except Exception:
                # Never serialize SQL parameters, operational URLs or private data.
                response = JSONResponse({"detail": {"code": "WORKSPACE_READ_FAILED"}}, status_code=503)
            response.headers.update(PRIVATE)
            return response
        return private


router = APIRouter(prefix="/ops/core/cases", tags=["ops-workspace-read"],
                   route_class=PrivateReadRoute)


@router.get("/{case_id}/workspace")
def get_workspace(case_id: str, x_operator_token: str | None = Header(None, alias="X-Operator-Token"),
                  contract: str | None = Header(None, alias="X-RTM-Workspace-Contract")):
    require_operator(x_operator_token)  # The same production OPS authorization, before DB access.
    if contract != VERSION:
        # Old published UI must not mistake the partial reader for a full legal workspace.
        raise HTTPException(406, {"code": "WORKSPACE_CONTRACT_REQUIRED"})
    try:
        canonical = str(UUID(case_id))
    except (ValueError, TypeError):
        raise HTTPException(422, {"code": "WORKSPACE_INVALID_CASE_ID"}) from None
    engine = get_engine()
    try:
        with engine.connect() as conn:
            with conn.begin():
                if conn.dialect.name == "postgresql":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                payload = build_workspace(conn, canonical)
        return JSONResponse(jsonable_encoder(payload))
    except MissingCase:
        raise HTTPException(404, {"code": "WORKSPACE_CASE_NOT_FOUND"}) from None
    except MissingSchema:
        raise HTTPException(503, {"code": "WORKSPACE_SCHEMA_UNAVAILABLE"}) from None
    except InvalidWorkspace:
        raise HTTPException(500, {"code": "WORKSPACE_INTEGRITY_INVALID"}) from None
    except SQLAlchemyError as exc:
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        code = code or getattr(getattr(exc, "orig", None), "pgcode", None)
        reason = "WORKSPACE_STORAGE_PERMISSION_DENIED" if code == "42501" else "WORKSPACE_READ_FAILED"
        raise HTTPException(503, {"code": reason}) from None
    finally:
        engine.dispose()
