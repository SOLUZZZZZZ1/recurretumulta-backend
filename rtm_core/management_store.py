"""Case-locked operations and append-only quote history; no provider I/O."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import text

from rtm_core.management_contract import (ManagementRejected, canonical, digest, offer,
                                         verify_quote, timestamp)
from rtm_core.paid_study import read_history


def obj(value):
    import json
    return json.loads(value) if isinstance(value, str) else value


def jparam(conn, name):
    return 'CAST(:' + name + ' AS jsonb)' if conn.dialect.name == 'postgresql' else ':' + name


def lock_case(conn, case_id):
    suffix = ' FOR UPDATE' if conn.dialect.name == 'postgresql' else ''
    if not conn.execute(text('SELECT id FROM cases WHERE id=:id' + suffix), {'id': case_id}).first():
        raise ManagementRejected('CASE_NOT_AVAILABLE', 404)


def event(conn, case_id, kind, actor, reference, evidence):
    conn.execute(text('INSERT INTO rtm_management_events(id,case_id,event_type,actor,reference_id,evidence) '
                     'VALUES (:id,:case_id,:kind,:actor,:reference,' + jparam(conn, 'evidence') + ')'),
        {'id': str(uuid4()), 'case_id': case_id, 'kind': kind, 'actor': actor,
         'reference': reference, 'evidence': canonical(evidence)})


def prior_operation(conn, case_id, operation_id, kind, actor, request):
    row = conn.execute(text('SELECT * FROM rtm_management_operations WHERE case_id=:case AND operation_id=:op'),
                       {'case': case_id, 'op': operation_id}).mappings().one_or_none()
    if not row:
        return None
    if row['state'] == 'cancelled':
        raise ManagementRejected('MANAGEMENT_OPERATION_CANCELLED')
    if row['kind'] != kind or row['actor'] != actor or row['request_sha256'] != digest(request):
        raise ManagementRejected('MANAGEMENT_OPERATION_CONFLICT')
    if row['state'] != 'committed':
        raise ManagementRejected('MANAGEMENT_OPERATION_RECONCILIATION_REQUIRED')
    return obj(row['result'])


def complete_operation(conn, case_id, operation_id, kind, actor, request, result):
    conn.execute(text('INSERT INTO rtm_management_operations '
        '(case_id,operation_id,kind,actor,request_sha256,state,result) '
        "VALUES (:case,:op,:kind,:actor,:sha,'committed'," + jparam(conn, 'result') + ')'),
        {'case': case_id, 'op': operation_id, 'kind': kind, 'actor': actor,
         'sha': digest(request), 'result': canonical(result)})
    return result


def reconcile(conn, case_id, operation_id, kind, actor):
    # Same lock as writers: absence becomes a terminal fence against delayed POSTs.
    lock_case(conn, case_id)
    row = conn.execute(text('SELECT * FROM rtm_management_operations WHERE case_id=:case AND operation_id=:op'),
                       {'case': case_id, 'op': operation_id}).mappings().one_or_none()
    if row:
        if row['actor'] != actor or row['kind'] != kind:
            raise ManagementRejected('MANAGEMENT_OPERATION_CONFLICT')
        return {'status': row['state'], 'result': obj(row['result'])}
    conn.execute(text('INSERT INTO rtm_management_operations '
        '(case_id,operation_id,kind,actor,request_sha256,state,result) '
        "VALUES (:case,:op,:kind,:actor,:sha,'cancelled'," + jparam(conn, 'result') + ')'),
        {'case': case_id, 'op': operation_id, 'kind': kind, 'actor': actor, 'sha': '0'*64, 'result': '{}'})
    return {'status': 'cancelled', 'result': {}}


def quote_row(conn, case_id, quote_id=None):
    suffix = ' AND id=:id' if quote_id else ' ORDER BY revision DESC LIMIT 1'
    return conn.execute(text('SELECT * FROM rtm_management_quotes WHERE case_id=:case' + suffix),
                        {'case': case_id, 'id': quote_id}).mappings().first()


def quote_projection(row):
    if not row:
        return None
    result = {'snapshot': obj(row['snapshot']), 'sha256': row['snapshot_sha256']}
    verify_quote(result, case_id=str(row['case_id']))
    result.update(state=row['state'], canonical_snapshot=canonical(result['snapshot']))
    return result


def quotes(conn, case_id):
    rows = conn.execute(text('SELECT * FROM rtm_management_quotes WHERE case_id=:case ORDER BY revision DESC LIMIT 101'),
                        {'case': case_id}).mappings().all()
    return {'latest': quote_projection(rows[0]) if rows else None,
            'history': [quote_projection(row) for row in rows[:100]], 'truncated': len(rows)>100}


def save_quote(conn, *, case_id, author, body, receipt, policy):
    lock_case(conn, case_id)
    request = body.model_dump()
    actor = 'operator:' + author.operator_id
    previous = prior_operation(conn, case_id, body.operation_id, 'quote', actor, request)
    if previous is not None:
        return previous
    latest = quote_row(conn, case_id)
    if (str(latest['id']) if latest else None) != body.expected_quote_id:
        raise ManagementRejected('QUOTE_REVISION_CONFLICT')
    if latest:
        pending = conn.execute(text('SELECT state FROM rtm_management_obligations WHERE quote_id=:id'),
                               {'id': str(latest['id'])}).scalar()
        if pending and pending != 'expired':
            raise ManagementRejected('PREVIOUS_CHECKOUT_MUST_BE_RECONCILED')
        if latest['state'] == 'settled':
            raise ManagementRejected('PAID_CONTRACT_CANNOT_BE_REPLACED')
    study = read_history(conn, case_id)['latest']
    prepared = offer(body, case_id=case_id, author=author, study=study, receipt=receipt,
                     mode=policy.mode, account_id=policy.account_id, revision=latest['revision']+1 if latest else 1,
                     tax_basis_points=policy.tax_basis_points, tax_label=policy.tax_label,
                     agreed_total_cents=policy.agreed_total_cents)
    if latest:
        conn.execute(text("UPDATE rtm_management_quotes SET state='superseded' WHERE id=:id"), {'id': str(latest['id'])})
    conn.execute(text('INSERT INTO rtm_management_quotes '
        '(id,case_id,revision,previous_id,study_operation_id,author_id,snapshot,snapshot_sha256,state) '
        'VALUES (:id,:case,:revision,:previous,:study,:author,' + jparam(conn, 'snapshot') + ",:sha,'prepared')"),
        {'id': body.operation_id, 'case': case_id, 'revision': prepared['snapshot']['revision'],
         'previous': body.expected_quote_id, 'study': body.study_operation_id, 'author': author.operator_id,
         'snapshot': canonical(prepared['snapshot']), 'sha': prepared['sha256']})
    event(conn, case_id, 'quote.prepared', actor, body.operation_id, {'sha256': prepared['sha256']})
    return complete_operation(conn, case_id, body.operation_id, 'quote', actor, request,
                              {'quote': quote_projection(quote_row(conn, case_id, body.operation_id))})


def current_client_quote(conn, case_id, quote_id, expected_sha=None, *, permit_expired=False):
    row = quote_row(conn, case_id)
    if not row or str(row['id']) != quote_id or row['state'] not in {'communicated', 'accepted', 'settled'}:
        raise ManagementRejected('QUOTE_NOT_AVAILABLE_TO_CLIENT')
    value = quote_projection(row)
    if expected_sha is not None and value['sha256'] != expected_sha:
        raise ManagementRejected('QUOTE_VERSION_CHANGED')
    if not permit_expired and timestamp(value['snapshot']['expires_at']) <= datetime.now(timezone.utc):
        raise ManagementRejected('COMMERCIAL_OFFER_EXPIRED')
    return value


def accept_quote(conn, *, case_id, quote_id, expected_sha, operation_id, recipient_hash, confirmed):
    lock_case(conn, case_id)
    actor = 'client:' + recipient_hash
    request = {'quote_id': quote_id, 'sha256': expected_sha, 'confirmed': confirmed}
    prior = prior_operation(conn, case_id, operation_id, 'acceptance', actor, request)
    if prior is not None:
        return prior
    if confirmed is not True:
        raise ManagementRejected('EXPLICIT_ACCEPTANCE_REQUIRED', 422)
    quote = current_client_quote(conn, case_id, quote_id, expected_sha)
    existing = conn.execute(text('SELECT * FROM rtm_management_acceptances WHERE quote_id=:id'), {'id': quote_id}).mappings().one_or_none()
    if existing:
        if existing['recipient_hash'] != recipient_hash:
            raise ManagementRejected('ACCEPTANCE_IDENTITY_MISMATCH')
        result = {'acceptance': obj(existing['snapshot']), 'sha256': existing['snapshot_sha256']}
    else:
        material = {'contract': 'rtm_management_acceptance_v1', 'case_id': case_id, 'quote_id': quote_id,
            'quote_sha256': expected_sha, 'recipient_hash': recipient_hash, 'operation_id': operation_id,
            'mode': quote['snapshot']['mode'], 'accepted_at': datetime.now(timezone.utc).isoformat(),
            'explicit_acceptance': True, 'submission_authorized': False}
        result = {'acceptance': material, 'sha256': digest(material)}
        conn.execute(text('INSERT INTO rtm_management_acceptances '
            '(quote_id,case_id,operation_id,recipient_hash,snapshot,snapshot_sha256) '
            'VALUES (:quote,:case,:op,:recipient,' + jparam(conn, 'snapshot') + ',:sha)'),
            {'quote': quote_id, 'case': case_id, 'op': operation_id, 'recipient': recipient_hash,
             'snapshot': canonical(material), 'sha': result['sha256']})
        conn.execute(text("UPDATE rtm_management_quotes SET state='accepted' WHERE id=:id"), {'id': quote_id})
        event(conn, case_id, 'quote.accepted', actor, quote_id, {'quote_sha256': expected_sha})
    return complete_operation(conn, case_id, operation_id, 'acceptance', actor, request, result)
