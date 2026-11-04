"""Reviewed generation and append-only corrections under a settled contract.

Reuses October's deterministic legal renderer, head preconditions, source
snapshots and content hashes. Reading never generates or rewrites a version.
"""
from datetime import datetime, timezone
import hashlib
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from rtm_core.management_contract import ManagementRejected, canonical, digest, study_requirements
from rtm_core.management_store import (obj,jparam,lock_case,quote_row,quote_projection,
    prior_operation,complete_operation,event)
from rtm_core.management_payments import obligation,receipt
from rtm_core.paid_study import read_history
from rtm_core.document_viewer import document_row,read_custody
from rtm_core.management_renderer import render_legal_preview


class Argument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=3,max_length=200)
    body:str=Field(min_length=20,max_length=12000)
    legal_basis:list[str]=Field(default_factory=list,max_length=20)


class ReviewedDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_type:Literal['Alegaciones','Recurso de reposición','Recurso de alzada']
    primary_strategy:str=Field(min_length=20,max_length=12000)
    legal_arguments:list[Argument]=Field(min_length=1,max_length=20)
    requested_outcomes:list[str]=Field(min_length=1,max_length=20)
    secondary_strategies:list[str]=Field(default_factory=list,max_length=20)
    additional_requests:list[str]=Field(default_factory=list,max_length=20)
    identity_and_phase_reviewed:Literal[True]
    contract_scope_confirmed:Literal[True]


class DraftInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:UUID
    expected_version_id:UUID|None
    expected_version_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    quote_id:UUID
    study_operation_id:UUID
    study_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['prepare','correct','recover_existing']
    reviewed:ReviewedDraft|None=None
    content:str|None=Field(default=None,min_length=20,max_length=200000)
    legacy_resource_id:UUID|None=None
    legacy_content_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    correction_reason:str=Field(min_length=5,max_length=2000)
    draft_only_acknowledged:Literal[True]

    @field_validator('content','correction_reason')
    @classmethod
    def clean(cls,value):
        if value is not None and (not value.strip() or '\0' in value):raise ValueError('Plain text required')
        return value


def version(conn,case_id,version_id=None):
    clause=' AND id=:id' if version_id else ' ORDER BY revision DESC LIMIT 1'
    row=conn.execute(text('SELECT * FROM rtm_management_draft_versions WHERE case_id=:case'+clause),
        {'case':case_id,'id':version_id}).mappings().first()
    if not row:return None
    material=obj(row['snapshot'])
    if (digest(material)!=row['snapshot_sha256'] or material.get('case_id')!=case_id
            or material.get('id')!=str(row['id']) or material.get('revision')!=row['revision']
            or material.get('content_sha256')!=hashlib.sha256(material.get('content','').encode()).hexdigest()
            or material.get('status')!='working_draft' or material.get('presented') is not False):
        raise ManagementRejected('DRAFT_INTEGRITY_UNVERIFIED',503)
    return {'snapshot':material,'sha256':row['snapshot_sha256'],'canonical_snapshot':canonical(material)}


def versions(conn,case_id):
    rows=conn.execute(text('SELECT id,revision,author_id,created_at,snapshot_sha256 FROM rtm_management_draft_versions '
        'WHERE case_id=:case ORDER BY revision DESC LIMIT 101'),{'case':case_id}).mappings().all()
    return {'latest':version(conn,case_id),'status':'available' if rows else 'not_prepared',
        'history':[{k:(v if k=='revision' else str(v)) for k,v in row.items()} for row in rows[:100]],
        'truncated':len(rows)>100,'presented':False}


def requirements(conn,case_id,policy):
    quote=quote_row(conn,case_id)
    if not quote:return ['MANAGEMENT_QUOTE_REQUIRED']
    paid=obligation(conn,case_id=case_id,quote_id=str(quote['id']))
    missing=[]
    if quote['state']!='settled' or not paid or paid['state']!='confirmed':missing.append('MANAGEMENT_BALANCE_NOT_CONFIRMED')
    if not receipt(conn,case_id,policy.mode,'management_balance'):missing.append('MANAGEMENT_PROVIDER_RECEIPT_REQUIRED')
    missing+=study_requirements(read_history(conn,case_id)['latest'])
    return missing


def save(conn,*,case_id,author,body,policy):
    lock_case(conn,case_id)
    request=body.model_dump(mode='json');op=str(body.operation_id);actor='operator:'+author.operator_id
    existing=prior_operation(conn,case_id,op,'draft',actor,request)
    if existing is not None:return existing
    missing=requirements(conn,case_id,policy)
    if missing:raise ManagementRejected(missing[0])
    quote=quote_projection(quote_row(conn,case_id))
    if quote['snapshot']['id']!=str(body.quote_id) or quote['snapshot']['mode']!=policy.mode:
        raise ManagementRejected('DRAFT_CONTRACT_VERSION_CHANGED')
    current=version(conn,case_id)
    if ((current['snapshot']['id'] if current else None)!=(str(body.expected_version_id) if body.expected_version_id else None)
            or (current['sha256'] if current else None)!=body.expected_version_sha256):
        raise ManagementRejected('DRAFT_VERSION_CONFLICT_REVIEW_BOTH')
    study=read_history(conn,case_id)['latest']
    if study['snapshot']['operation_id']!=str(body.study_operation_id) or study['sha256']!=body.study_sha256:
        raise ManagementRejected('DRAFT_REVIEWED_STUDY_CHANGED')
    case=conn.execute(text('SELECT id,authorized,interested_data,expediente_ref FROM cases WHERE id=:case'),{'case':case_id}).mappings().one()
    if case['authorized'] is not True and case['authorized']!=1:raise ManagementRejected('AUTHORIZATION_REVIEW_REQUIRED')
    # Re-read original bytes; a historical missing hash is not backfilled.
    for source in study['snapshot']['sources']:
        data,observed,_=read_custody(document_row(conn,case_id,source['document_id']))
        if observed!=source['observed_sha256']:raise ManagementRejected('DRAFT_SOURCE_CHANGED_REVIEW_REQUIRED')
    legacy=conn.execute(text('SELECT id,version,content FROM ops_final_resources WHERE case_id=:case ORDER BY version DESC LIMIT 1'),{'case':case_id}).mappings().first()
    reviewed=None;legacy_ref=None
    if body.action=='prepare':
        if current or legacy:raise ManagementRejected('EXISTING_RESOURCE_MUST_BE_RECOVERED')
        if not body.reviewed or body.content is not None:raise ManagementRejected('REVIEWED_LEGAL_INPUT_REQUIRED',422)
        from types import SimpleNamespace
        facts=study['snapshot']['study']['facts']
        review=body.reviewed.model_dump()
        reviewed=review
        preview=SimpleNamespace(**{k:v for k,v in review.items() if k not in {'legal_arguments','identity_and_phase_reviewed','contract_scope_confirmed'}},
            legal_arguments=body.reviewed.legal_arguments,destination=facts['organismo']['value'],
            subject=facts['expediente_ref']['value'],problem_summary='',
            validated_facts_summary=[facts['facts']['value']],family='traffic')
        case_meta=dict(case);case_meta['interested_data']=obj(case['interested_data'])
        content=render_legal_preview(preview,case_meta)
    elif body.action=='recover_existing':
        if current or not legacy or str(legacy['id'])!=str(body.legacy_resource_id):raise ManagementRejected('LEGACY_RESOURCE_VERSION_CHANGED')
        content=legacy['content']
        if not isinstance(content,str) or hashlib.sha256(content.encode()).hexdigest()!=body.legacy_content_sha256:
            raise ManagementRejected('LEGACY_RESOURCE_CONTENT_CHANGED')
        if body.content is not None:raise ManagementRejected('RECOVERY_MUST_PRESERVE_CONTENT',422)
        legacy_ref={'id':str(legacy['id']),'version':legacy['version'],'sha256':body.legacy_content_sha256}
    else:
        if not current or body.content is None:raise ManagementRejected('DRAFT_BASE_REQUIRED',422)
        content=body.content
        reviewed=current['snapshot']['reviewed_legal_inputs']
        legacy_ref=current['snapshot'].get('legacy_source')
    snapshot={'contract':'rtm_management_working_draft_v1','id':op,'case_id':case_id,
        'revision':current['snapshot']['revision']+1 if current else 1,
        'previous_id':current['snapshot']['id'] if current else None,
        'previous_sha256':current['sha256'] if current else None,
        'quote_id':str(body.quote_id),'quote_sha256':quote['sha256'],
        'study_operation_id':str(body.study_operation_id),'study_sha256':body.study_sha256,
        'sources':study['snapshot']['sources'],'reviewed_legal_inputs':reviewed,'legacy_source':legacy_ref,
        'content':content,'content_sha256':hashlib.sha256(content.encode()).hexdigest(),
        'author':{'id':author.operator_id,'name':author.display_name},'created_at':datetime.now(timezone.utc).isoformat(),
        'correction_reason':body.correction_reason,'status':'working_draft','signed':False,'approved':False,'presented':False}
    sha=digest(snapshot)
    conn.execute(text('INSERT INTO rtm_management_draft_versions '
        '(id,case_id,operation_id,revision,previous_id,based_on_id,quote_id,author_id,snapshot,snapshot_sha256) '
        'VALUES (:id,:case,:op,:revision,:previous,:previous,:quote,:author,'+jparam(conn,'snapshot')+',:sha)'),
        {'id':op,'case':case_id,'op':op,'revision':snapshot['revision'],'previous':snapshot['previous_id'],
         'quote':str(body.quote_id),'author':author.operator_id,'snapshot':canonical(snapshot),'sha':sha})
    event(conn,case_id,'draft.saved',actor,op,{'sha256':sha,'revision':snapshot['revision'],'presented':False})
    return complete_operation(conn,case_id,op,'draft',actor,request,{'version':version(conn,case_id,op)})
