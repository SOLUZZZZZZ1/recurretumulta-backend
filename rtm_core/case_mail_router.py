"""Individual, case-scoped conversation API; no inline SMTP or public attachments."""
import os
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Header
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import text
from fastapi.responses import Response
from rtm_core.casework_router import CaseworkRoute,require_casework
from rtm_core.case_mail import MailRejected,extract_attachment
from rtm_core.case_mail_repository import conversation,prepare,queue,message_for_case,public_message,obj
from rtm_core.case_mail_runtime import B2MailStorage
from rtm_core.document_viewer import render

router=APIRouter(prefix='/ops/core/cases',tags=['case-communications'],route_class=CaseworkRoute)

def enabled(context=Depends(require_casework)):
    if os.environ.get('RTM_ENABLE_CASE_MAIL')!='1':
        raise HTTPException(503,{'code':'CASE_MAIL_NOT_ENABLED'})
    return context

class Compose(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:UUID
    recipient:str=Field(min_length=3,max_length=254)
    subject:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1,max_length=50000)
    reply_id:UUID|None=None

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approve_exact_message:Literal[True]
    original_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')

@router.get('/{case_id}/communications')
def read_conversation(q:str=Query('',max_length=200),context=Depends(enabled)):
    return conversation(context.conn,context.case_id,q)

@router.get('/{case_id}/communications/{message_id}')
def read_message(message_id:UUID,context=Depends(enabled)):
    return {'ok':True,**public_message(message_for_case(context.conn,context.case_id,str(message_id)))}

@router.post('/{case_id}/communications')
def prepare_message(payload:Compose,context=Depends(enabled)):
    try:
        result=prepare(context.conn,case_id=context.case_id,session=context.session,
            operation_id=str(payload.operation_id),recipient=payload.recipient,
            subject=payload.subject,body=payload.body,reply_id=str(payload.reply_id) if payload.reply_id else None,
            storage=B2MailStorage())
    except MailRejected as exc:
        raise HTTPException(422,{'code':str(exc)}) from None
    return {'ok':True,**result}

@router.post('/{case_id}/communications/{message_id}/queue')
def queue_message(message_id:UUID,payload:Approval,context=Depends(enabled)):
    row=message_for_case(context.conn,context.case_id,str(message_id))
    if obj(row['payload'])['raw_sha256']!=payload.original_sha256:
        raise HTTPException(409,{'code':'MAIL_APPROVAL_CONTENT_MISMATCH'})
    return {'ok':True,**queue(context.conn,context.case_id,str(message_id),context.session)}

def checked_attachment(message_id,part,context):
    row=message_for_case(context.conn,context.case_id,str(message_id))
    attachment=next((a for a in obj(row['payload'])['attachments'] if a['part']==part),None)
    if not attachment:raise HTTPException(404,{'code':'MAIL_ATTACHMENT_NOT_FOUND'})
    check=context.conn.execute(text('''SELECT state FROM rtm_case_mail_attachment_checks
        WHERE message_id=:id AND part=:part AND sha256=:sha'''),
        {'id':str(message_id),'part':part,'sha':attachment['sha256']}).scalar()
    if check!='clean':raise HTTPException(409,{'code':'MAIL_ATTACHMENT_QUARANTINED'})
    original=context.conn.execute(text('SELECT custody_key,sha256 FROM rtm_case_mail_originals WHERE message_id=:id AND sha256=:sha'),
        {'id':str(message_id),'sha':obj(row['payload'])['raw_sha256']}).mappings().first()
    if not original:raise HTTPException(503,{'code':'MAIL_ORIGINAL_UNAVAILABLE'})
    raw=B2MailStorage().get(original['custody_key'],original['sha256'])
    data,meta=extract_attachment(raw,part,attachment['sha256'])
    return data,meta

@router.get('/{case_id}/communications/{message_id}/attachments/{part}/view')
def view_attachment(message_id:UUID,part:int,context=Depends(enabled)):
    data,meta=checked_attachment(message_id,part,context)
    return {'ok':True,'contract':'rtm_ops_document_view_v1','case_id':context.case_id,
        'document_id':str(message_id)+':'+str(part),'title':meta['name'],
        'observed_sha256':meta['sha256'],'recorded_hash_state':'verified',
        'view_only':True,'operator_export_allowed':False,**render(data,meta['mime'])}

@router.get('/{case_id}/communications/{message_id}/attachments/{part}/pages/{page}')
def read_attachment_page(message_id:UUID,part:int,page:int,
        if_match:str|None=Header(None,alias='If-Match'),context=Depends(enabled)):
    data,meta=checked_attachment(message_id,part,context)
    if if_match!='"'+meta['sha256']+'"':raise HTTPException(412,{'code':'MAIL_ATTACHMENT_HASH_MISMATCH'})
    return Response(render(data,meta['mime'],page=page),media_type='image/png',
        headers={'Content-Disposition':f'inline; filename="attachment-page-{page}.png"'})
