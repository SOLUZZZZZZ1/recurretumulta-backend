"""Human initial-study assessments, not generated resources or client deliveries.

Reuses October's fact/source contract. Append-only snapshots and operation IDs
protect retries; the existing case row serializes writers without updating it.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from rtm_core.contracts import ValidatedFact, FactStatus
from rtm_core.document_viewer import document_row, read_custody

CONTRACT = 'rtm_paid_initial_study_v1'
FACT_KEYS = {'organismo', 'expediente_ref', 'notified_at', 'facts', 'deadline',
             'interested', 'contact', 'driver', 'owner', 'representative'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class ReviewedDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID
    observed_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    read_confirmed: Literal[True]


class StudyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    expected_revision: int = Field(ge=0, le=100000)
    state: Literal['in_progress', 'blocked', 'completed']
    facts: dict[str, ValidatedFact]
    documents: list[ReviewedDocument] = Field(min_length=1, max_length=20)
    assessment: str = Field(min_length=1, max_length=12000)
    missing: str = Field(max_length=4000)
    options: str = Field(max_length=8000)
    next_action: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_content(self):
        if set(self.facts) != FACT_KEYS:
            raise ValueError('Explicit reviewed/unresolved facts required')
        ids = {str(doc.document_id) for doc in self.documents}
        if len(ids) != len(self.documents):
            raise ValueError('Duplicate document')
        for fact in self.facts.values():
            if fact.value is not None and (not isinstance(fact.value, str) or not fact.value.strip()):
                raise ValueError('Fact must be text or unresolved')
            if len(canonical(fact.model_dump(mode='json'))) > 16000:
                raise ValueError('Fact too large')
            if any(s.document_id not in ids or s.source_type != 'document'
                   or s.extraction_method != 'human_review' for s in fact.sources):
                raise ValueError('Source not reviewed in this operation')
        if not self.assessment.strip() or not self.next_action.strip():
            raise ValueError('Assessment and next action required')
        if self.state == 'blocked' and not self.missing.strip():
            raise ValueError('Explain the blocking requirement')
        if self.state == 'completed' and any(self.facts[k].status != FactStatus.VALIDATED
                for k in ('organismo', 'expediente_ref', 'notified_at', 'facts', 'deadline')):
            raise ValueError('Core facts and reviewed deadline required to complete study')
        if len(canonical(self.model_dump(mode='json'))) > 100000:
            raise ValueError('Study too large')
        return self


def checked(row, case_id):
    if row is None:
        raise HTTPException(404, {'code':'STUDY_OPERATION_NOT_FOUND'})
    payload = row['snapshot']
    if isinstance(payload, str):
        payload = json.loads(payload)
    if (not isinstance(payload, dict) or payload.get('case_id') != case_id
            or payload.get('operation_id') != str(row['operation_id'])
            or payload.get('revision') != row['revision']
            or not hmac.compare_digest(digest(payload), row['snapshot_sha256'])):
        raise HTTPException(503, {'code':'STUDY_INTEGRITY_UNVERIFIED'})
    return {'snapshot': payload, 'sha256': row['snapshot_sha256'], 'canonical_snapshot':canonical(payload)}


def read_operation(conn, case_id, operation_id):
    return checked(conn.execute(text('''SELECT operation_id, revision, snapshot, snapshot_sha256
        FROM rtm_study_assessments WHERE case_id=:case_id AND operation_id=:operation_id'''),
        {'case_id':case_id, 'operation_id':str(operation_id)}).mappings().one_or_none(), case_id)


def read_history(conn, case_id):
    rows = conn.execute(text('''SELECT operation_id, revision, snapshot, snapshot_sha256
        FROM rtm_study_assessments WHERE case_id=:case_id ORDER BY revision DESC LIMIT 101'''),
        {'case_id':case_id}).mappings().all()
    values = [checked(row, case_id) for row in rows[:100]]
    return {'status':'available' if rows else 'not_started',
            'revision':rows[0]['revision'] if rows else 0,
            'latest':values[0] if rows else None,
            'history':[{'operation_id':v['snapshot']['operation_id'],
                        'revision':v['snapshot']['revision'], 'created_at':v['snapshot']['created_at'],
                        'author':v['snapshot']['author'], 'state':v['snapshot']['study']['state'],
                        'sha256':v['sha256']} for v in values], 'truncated':len(rows)>100}


def cancelled_operation(conn, case_id, operation_id, author_id):
    """Immutable no-content receipt; protected by the same case lock as saves."""
    rows = conn.execute(text('''SELECT operator_id, metadata FROM rtm_operator_access_events
        WHERE event_type='study.operation_cancelled' AND request_id=:operation_id'''),
        {'operation_id':str(operation_id)}).mappings().all()
    for row in rows:
        meta = row['metadata']
        if isinstance(meta,str):
            meta = json.loads(meta)
        if meta.get('case_id') == case_id:
            if str(row['operator_id']) != author_id:
                raise HTTPException(409, {'code':'STUDY_OPERATION_MISMATCH'})
            return True
    return False


def reconcile_operation(conn, case_id, session, operation_id):
    """Return the committed operation or fence it against a delayed first POST.

    A 404 GET alone cannot prove no write is still in flight. This explicit
    reconciliation serializes with save_assessment and preserves a terminal
    receipt in the existing append-only audit table. No business data changes.
    """
    suffix = ' FOR UPDATE' if conn.dialect.name == 'postgresql' else ''
    case = conn.execute(text('SELECT id FROM cases WHERE id=:case_id' + suffix),
                        {'case_id':case_id}).first()
    if not case:
        raise HTTPException(404, {'code':'CASEWORK_CASE_NOT_FOUND'})
    prior = conn.execute(text('''SELECT operation_id, revision, snapshot, snapshot_sha256, author_id
        FROM rtm_study_assessments WHERE case_id=:case_id AND operation_id=:operation_id'''),
        {'case_id':case_id,'operation_id':str(operation_id)}).mappings().one_or_none()
    if prior:
        if str(prior['author_id']) != session.operator_id:
            raise HTTPException(409, {'code':'STUDY_OPERATION_MISMATCH'})
        return {'status':'committed', **checked(prior,case_id)}
    if not cancelled_operation(conn,case_id,operation_id,session.operator_id):
        metadata = canonical({'case_id':case_id,'operation_id':str(operation_id),
                              'contract':'rtm_study_reconciliation_v1'})
        param = 'CAST(:metadata AS jsonb)' if conn.dialect.name == 'postgresql' else ':metadata'
        conn.execute(text('''INSERT INTO rtm_operator_access_events
            (id,operator_id,event_type,result,request_id,metadata)
            VALUES (:id,:author,'study.operation_cancelled','noop',:operation_id,''' + param + ')'),
            {'id':str(uuid4()),'author':session.operator_id,
             'operation_id':str(operation_id),'metadata':metadata})
    return {'status':'cancelled','case_id':case_id,'operation_id':str(operation_id),
            'contract':'rtm_study_reconciliation_v1'}


def save_assessment(conn, case_id, session, submitted):
    request_data = submitted.model_dump(mode='json')
    request_hash = digest(request_data)
    suffix = ' FOR UPDATE' if conn.dialect.name == 'postgresql' else ''
    case = conn.execute(text('''SELECT id, payment_status, product_code, authorized
        FROM cases WHERE id=:case_id''' + suffix), {'case_id':case_id}).mappings().one_or_none()
    if not case:
        raise HTTPException(404, {'code':'CASEWORK_CASE_NOT_FOUND'})
    prior = conn.execute(text('''SELECT operation_id, revision, snapshot, snapshot_sha256,
        request_sha256, author_id FROM rtm_study_assessments
        WHERE case_id=:case_id AND operation_id=:operation_id'''),
        {'case_id':case_id,'operation_id':str(submitted.operation_id)}).mappings().one_or_none()
    if prior:
        if str(prior['author_id']) != session.operator_id or not hmac.compare_digest(prior['request_sha256'],request_hash):
            raise HTTPException(409, {'code':'STUDY_OPERATION_MISMATCH'})
        return checked(prior,case_id)
    if cancelled_operation(conn,case_id,submitted.operation_id,session.operator_id):
        raise HTTPException(409, {'code':'STUDY_OPERATION_CANCELLED'})
    if case['payment_status'] != 'paid' or case['product_code'] != 'REVIEW_BASIC':
        raise HTTPException(409, {'code':'STUDY_PAYMENT_NOT_CONFIRMED'})
    if case['authorized'] not in (True,1):
        raise HTTPException(409, {'code':'STUDY_AUTHORIZATION_NOT_CONFIRMED'})
    previous = read_history(conn,case_id)
    if previous['revision'] != submitted.expected_revision:
        raise HTTPException(409, {'code':'STUDY_REVISION_CONFLICT'})
    sources = []
    for doc in submitted.documents:
        row = document_row(conn, case_id, str(doc.document_id))
        _, observed, recorded_state = read_custody(row)
        if not hmac.compare_digest(observed, doc.observed_sha256):
            raise HTTPException(409, {'code':'STUDY_SOURCE_CHANGED'})
        sources.append({'document_id':str(doc.document_id), 'kind':row['kind'],
                        'mime':row['mime'],'size_bytes':row['size_bytes'],
                        'observed_sha256':observed, 'recorded_hash_state':recorded_state,
                        'read_confirmed_by_operator':True})
    kinds = {s['kind'] for s in sources}
    if not {'original','authorization_signed'}.issubset(kinds) or not kinds.intersection({'identity_front','identity_back'}):
        raise HTTPException(409, {'code':'STUDY_REQUIRED_DOCUMENTS_NOT_REVIEWED'})
    snapshot = {'contract':CONTRACT, 'case_id':case_id,
        'operation_id':str(submitted.operation_id), 'revision':previous['revision']+1,
        'previous_operation_id':previous['latest']['snapshot']['operation_id'] if previous['latest'] else None,
        'previous_sha256':previous['latest']['sha256'] if previous['latest'] else None,
        'created_at':datetime.now(timezone.utc).isoformat(),
        'author':{'id':session.operator_id,'name':session.display_name},
        'payment':{'status':case['payment_status'],'product':case['product_code'],
                   'meaning':'initial_study_only'},
        'sources':sources, 'study':{k:v for k,v in request_data.items()
                                  if k not in {'operation_id','expected_revision','documents'}}}
    sha = digest(snapshot)
    snapshot_param = 'CAST(:snapshot AS jsonb)' if conn.dialect.name == 'postgresql' else ':snapshot'
    conn.execute(text('''INSERT INTO rtm_study_assessments
        (case_id, operation_id, revision, author_id, request_sha256, snapshot, snapshot_sha256)
        VALUES (:case_id, :operation_id, :revision, :author_id, :request_sha256, '''
        + snapshot_param + ''', :snapshot_sha256)'''),
        {'case_id':case_id, 'operation_id':str(submitted.operation_id), 'revision':snapshot['revision'],
         'author_id':session.operator_id, 'request_sha256':request_hash,
         'snapshot':canonical(snapshot), 'snapshot_sha256':sha})
    # The client must perform a NEW GET after the transaction has committed.
    return {'snapshot':snapshot, 'sha256':sha, 'canonical_snapshot':canonical(snapshot)}
