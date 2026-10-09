"""Private OPS access to stored staging drafts; never a public download link."""
from typing import Optional
from fastapi import APIRouter, Header, Request, Response
from database import get_engine
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import require_operator_token
from rtm_core.staging_rehearsal import require_supervisor
from rtm_core import working_document_versions as versions

router = APIRouter()
PATH = "/{case_id}/study/working-document/versions"
HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


def _scope(request, token):
    require_operator_token(token)
    grant = require_supervisor(request)
    return load_ops_case_scope(request), grant


@router.get(PATH)
def get_versions(case_id: str, request: Request, response: Response,
                 x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return versions.list_versions(conn, case_id=case_id, grant=grant)


@router.post(PATH)
def post_version(case_id: str, body: versions.SaveWorkingDocumentBody, request: Request, response: Response,
                 x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    engine, uploaded = get_engine(), []
    try:
        with engine.begin() as conn:
            case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
            return versions.save_version(conn, case_id=case_id, grant=grant, body=body, uploaded=uploaded)
    except Exception:
        # Import lazily to reuse the existing lost-COMMIT-safe compensation.
        from rtm_core.study_router import _cleanup_uncommitted_documents
        _cleanup_uncommitted_documents(engine, uploaded)
        raise


@router.get(PATH + "/{version_id}")
def get_version(case_id: str, version_id: str, request: Request, response: Response,
                x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    response.headers.update(HEADERS)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return versions.read_version(conn, case_id=case_id, version_id=version_id, grant=grant)


@router.get(PATH + "/{version_id}/pdf")
def get_version_pdf(case_id: str, version_id: str, request: Request,
                    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    scope, grant = _scope(request, x_operator_token)
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        content, entry = versions.read_pdf(conn, case_id=case_id, version_id=version_id, grant=grant)
    return Response(content, media_type="application/pdf", headers={**HEADERS,
        "Content-Disposition": 'inline; filename="rtm-borrador-guardado.pdf"',
        "X-RTM-Version-ID": entry["id"], "X-RTM-Source-SHA256": entry["source_sha256"],
        "X-RTM-Document-SHA256": entry["pdf"]["sha256"],
        "Access-Control-Expose-Headers": "X-RTM-Version-ID, X-RTM-Source-SHA256, X-RTM-Document-SHA256"})
