"""Recoverable device logout for the explicit production casework profile.

The device cookie remains an HttpOnly possession factor, not a login session.
Only session rows are closed. Immutable audit records deduplicate operations so
a delayed retry cannot close sessions admitted after that operation completed.
"""
from dataclasses import replace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from rtm_core.operator_access_runtime_repository import record_operator_access_event
from rtm_core.operator_auth_crypto import hash_device_secret
from rtm_core.operator_auth_request import normalize_device_token

CONTRACT = 'rtm_device_logout_v1'
EVENT = 'auth.device_logout'


def lock_device(conn, digest):
    if conn.dialect.name != 'postgresql':
        raise HTTPException(503, {'code': 'DEVICE_LOGOUT_STORE_UNSUPPORTED'})
    key = int.from_bytes(bytes.fromhex(digest[:16]), 'big', signed=True)
    conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def close_device_sessions(conn, *, device_cookie, operation_id, context, config):
    if not config.production_casework:
        raise HTTPException(404, {'code': 'DEVICE_LOGOUT_NOT_ENABLED'})
    operation_id = str(UUID(str(operation_id)))
    device = normalize_device_token(device_cookie)
    if device is None:
        raise HTTPException(401, {'code': 'DEVICE_LOGOUT_POSSESSION_REQUIRED'})
    digest = hash_device_secret(device)
    lock_device(conn, digest)
    prior = conn.execute(text('''SELECT id FROM rtm_operator_access_events
        WHERE event_type=:event AND device_key_sha256=:digest AND request_id=:operation'''),
        {'event': EVENT, 'digest': digest, 'operation': operation_id}).first()
    if prior:
        return {'ok': True, 'contract': CONTRACT, 'operation_id': operation_id, 'status': 'closed'}
    allowed = {f'actor{i}': value for i, value in enumerate(config.allowed_operator_ids)}
    if not allowed:
        raise HTTPException(403, {'code': 'DEVICE_LOGOUT_SCOPE_REQUIRED'})
    params = ','.join(f'CAST(:{name} AS UUID)' for name in allowed)
    device_query = f'''SELECT id FROM rtm_operator_devices WHERE device_key_sha256=:digest
        AND operator_id IN ({params})'''
    values = {**allowed, 'digest': digest}
    if not conn.execute(text(device_query), values).first():
        raise HTTPException(401, {'code': 'DEVICE_LOGOUT_POSSESSION_REQUIRED'})
    conn.execute(text(f'''UPDATE rtm_operator_sessions SET status='closed',
        logout_at=NOW(), close_reason='operator_device_logout'
        WHERE status='active' AND device_id IN ({device_query})'''), values)
    record_operator_access_event(conn, context=replace(context, request_id=operation_id),
        event_type=EVENT, result='success', auth_method='device_possession',
        retention_days=config.evidence_retention_days, device_key_sha256=digest,
        reason_code='explicit_browser_logout')
    return {'ok': True, 'contract': CONTRACT, 'operation_id': operation_id, 'status': 'closed'}
