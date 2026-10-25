"""Explicit individual OPS access to one production pilot case.

This router does not change legacy reader/export authorization and has no
generation, checkout, upload or presentation endpoints.
"""
from dataclasses import dataclass
import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from database import get_engine
from rtm_core.document_viewer import document_row, read_custody, render
from rtm_core.operator_auth_router import load_operator_session_with_device_possession, _runtime_config
from rtm_core.production_casework import load_policy, permits_case
from rtm_core.paid_study import StudyInput, read_history, read_operation, save_assessment
from workspace_read import build_workspace, scalar, MissingCase
from sqlalchemy import text
import hashlib
import json

HEADERS = {'Cache-Control':'private, no-store, max-age=0', 'Pragma':'no-cache',
           'Vary':'Authorization, Cookie, X-RTM-Device', 'X-Content-Type-Options':'nosniff',
           'Referrer-Policy':'no-referrer'}


class CaseworkRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private(request):
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({'detail':{'code':'CASEWORK_REQUEST_INVALID'}}, status_code=422)
            except HTTPException as exc:
                response = JSONResponse({'detail':exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except SQLAlchemyError:
                response = JSONResponse({'detail':{'code':'CASEWORK_STORAGE_UNAVAILABLE'}}, status_code=503)
            except Exception:
                response = JSONResponse({'detail':{'code':'CASEWORK_UNAVAILABLE'}}, status_code=503)
            response.headers.update(HEADERS)
            return response
        return private


router = APIRouter(prefix='/ops/core/cases', tags=['ops-paid-casework'], route_class=CaseworkRoute)


def configured_policy():
    _runtime_config()
    policy = load_policy(os.environ)
    if policy is None:
        raise HTTPException(503, {'code':'CASEWORK_NOT_ENABLED'})
    return policy


def casework_connection():
    configured_policy()  # Never connect just to discover that the feature is off.
    engine = get_engine()
    try:
        with engine.begin() as conn:
            yield conn
    finally:
        engine.dispose()


@dataclass(frozen=True)
class CaseworkContext:
    conn: object
    policy: object
    session: object
    case_id: str


def require_casework(case_id: str, request: Request, conn=Depends(casework_connection)):
    policy = configured_policy()
    try:
        if str(UUID(case_id)) != case_id:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, {'code':'CASEWORK_CASE_ID_INVALID'}) from None
    session = load_operator_session_with_device_possession(
        conn, authorization=request.headers.get('authorization'),
        x_rtm_device=request.headers.get('x-rtm-device'),
        rtm_presenter_device=request.cookies.get('__Host-rtm_presenter_device'), touch=False)
    if not session:
        raise HTTPException(401, {'code':'CASEWORK_INDIVIDUAL_SESSION_REQUIRED'})
    if not permits_case(policy, session, case_id, write=request.method not in {'GET','HEAD'}):
        raise HTTPException(403, {'code':'CASEWORK_SCOPE_DENIED'})
    if request.method not in {'GET','HEAD'} and request.headers.get('origin') != policy.origin:
        raise HTTPException(403, {'code':'CASEWORK_ORIGIN_DENIED'})
    return CaseworkContext(conn, policy, session, case_id)


def document_title(kind):
    return {'identity_front':'Identidad · anverso', 'identity_back':'Identidad · reverso',
            'authorization_signed':'Autorización firmada', 'original':'Documento aportado'
            }.get(kind, 'Documento del expediente')


@router.get('')
def list_permitted_cases(request: Request, conn=Depends(casework_connection)):
    policy = configured_policy()
    # Same individual authorization as the detail; no legacy shared token.
    case_id = policy.case_ids[0]
    context = require_casework(case_id, request, conn)
    workspace = build_workspace(conn, context.case_id)
    return {'ok':True, 'cases':[{'id':case_id, 'case':workspace['case']}]}


@router.get('/{case_id}/work')
def read_casework(context=Depends(require_casework)):
    try:
        workspace = build_workspace(context.conn, context.case_id)
    except MissingCase:
        raise HTTPException(404, {'code':'CASEWORK_CASE_NOT_FOUND'}) from None
    study = read_history(context.conn, context.case_id)
    resources = context.conn.execute(text('''SELECT id, version, is_final, created_at, updated_at
        FROM ops_final_resources WHERE case_id=:case_id ORDER BY version DESC, created_at DESC LIMIT 101'''),
        {'case_id':context.case_id}).mappings().all()
    extractions = context.conn.execute(text('''SELECT id, extracted_json, model, created_at
        FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 10'''),
        {'case_id':context.case_id}).mappings().all()
    suggestions = []
    for row in extractions:
        data = row['extracted_json']
        if isinstance(data,str):
            data = json.loads(data)
        if not isinstance(data,dict):
            raise HTTPException(503, {'code':'EXTRACTION_READ_INVALID'})
        allowed = {k:v for k,v in data.items() if k in {
            'organismo','expediente_ref','notified_at','fecha_notificacion','fecha_hechos',
            'hecho_denunciado_literal','hecho_denunciado_resumido','hecho_imputado','importe',
            'matricula','tipo_sancion'} and isinstance(v,(str,int,float)) and len(str(v))<=12000}
        suggestions.append({'id':str(row['id']), 'created_at':scalar(row['created_at']),
                            'values':allowed,'status':'unreviewed_extraction'})
    return {'ok':True, 'contract':'rtm_paid_casework_v1', 'case_id':context.case_id,
            'workspace':workspace, 'study':study, 'extractions':suggestions,
            'resources':{'status':'available' if resources else 'not_recorded',
                'store':'ops_final_resources', 'truncated':len(resources)>100,
                'items':[{k:scalar(v) for k,v in row.items()} for row in resources[:100]]},
            'capabilities':{'study_write':permits_case(context.policy,context.session,context.case_id,write=True),
                            'resource_generation':False,'client_delivery':False,'payment_creation':False,
                            'document_export':False,'presentation':False}}


@router.get('/{case_id}/resources/{resource_id}')
def read_resource(resource_id: UUID, context=Depends(require_casework)):
    row = context.conn.execute(text('''SELECT id, version, is_final, content
        FROM ops_final_resources WHERE id=:id AND case_id=:case_id'''),
        {'id':str(resource_id),'case_id':context.case_id}).mappings().one_or_none()
    if not row:
        raise HTTPException(404, {'code':'RESOURCE_NOT_FOUND_IN_CASE'})
    if not isinstance(row['content'],str) or len(row['content'].encode('utf-8'))>2000000:
        raise HTTPException(503, {'code':'RESOURCE_CONTENT_UNAVAILABLE'})
    return {'ok':True,'case_id':context.case_id,'id':str(row['id']), 'version':row['version'],
            'is_final':row['is_final'],'content':row['content'],
            'sha256':hashlib.sha256(row['content'].encode('utf-8')).hexdigest(),
            'operator_export_allowed':False}


@router.get('/{case_id}/study/operations/{operation_id}')
def read_study_operation(operation_id: UUID, context=Depends(require_casework)):
    return {'ok':True, **read_operation(context.conn,context.case_id,operation_id)}


@router.post('/{case_id}/study')
def save_study(payload:StudyInput, context=Depends(require_casework)):
    return {'ok':True, **save_assessment(context.conn,context.case_id,context.session,payload)}


@router.get('/{case_id}/documents/{document_id}/view')
def view_document(document_id: str, context=Depends(require_casework)):
    row = document_row(context.conn, context.case_id, document_id)
    data, observed, recorded_state = read_custody(row)
    metadata = render(data, row['mime'])
    return JSONResponse({'ok':True, 'contract':'rtm_ops_document_view_v1',
        'case_id':context.case_id, 'document_id':document_id, 'title':document_title(row['kind']),
        'original_filename_recorded':False, 'mime':row['mime'], **metadata,
        'observed_sha256':observed, 'recorded_hash_state':recorded_state,
        'view_only':True, 'operator_export_allowed':False}, headers={'ETag':'"'+observed+'"'})


@router.get('/{case_id}/documents/{document_id}/pages/{page_number}')
def view_document_page(document_id: str, page_number: int,
                       width: int = Query(1400, ge=400, le=2000),
                       if_match: str | None = Header(None, alias='If-Match'),
                       context=Depends(require_casework)):
    if not if_match:
        raise HTTPException(428, {'code':'DOCUMENT_VIEW_PRECONDITION_REQUIRED'})
    row = document_row(context.conn, context.case_id, document_id)
    data, observed, _ = read_custody(row)
    if not hmac.compare_digest(if_match, '"'+observed+'"'):
        raise HTTPException(412, {'code':'DOCUMENT_CHANGED_REOPEN_VIEWER'})
    png = render(data, row['mime'], page=page_number, width=width)
    return Response(png, media_type='image/png', headers={
        'Content-Disposition':f'inline; filename="page-{page_number}.png"',
        'ETag':'"'+observed+'"'})
