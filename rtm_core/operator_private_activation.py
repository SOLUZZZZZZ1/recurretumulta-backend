"""Attended administrative activation, never mounted as an HTTP route.

Only new, explicitly approved identities. No password resets, invitations,
MFA bypass, schema creation, grants, service configuration or existing edits.
"""
from datetime import datetime, timezone, timedelta
import json
from uuid import UUID, uuid4
from sqlalchemy import text
from rtm_core.operator_auth_crypto import normalize_operator_email, hash_operator_password, verify_operator_password


class ActivationRejected(ValueError):
    pass


def validate_plan(plan, now=None):
    expected={'version','backend_sha','service','case_id','expires_at','expected_database',
              'expected_sql_role','expected_db_host','participants','mfa_policy'}
    if not isinstance(plan,dict) or set(plan)!=expected or plan['version']!=1:
        raise ActivationRejected('ACTIVATION_PLAN_INVALID')
    if plan['service']!='recurretumulta-backend' or plan['case_id']!='2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee':
        raise ActivationRejected('ACTIVATION_DESTINATION_MISMATCH')
    import re
    if not re.fullmatch('[a-f0-9]{40}',str(plan['backend_sha'])):
        raise ActivationRejected('ACTIVATION_CANDIDATE_REQUIRED')
    if plan['mfa_policy']!='not_required_for_approved_pilot':
        raise ActivationRejected('ACTIVATION_MFA_POLICY_UNSUPPORTED')
    current=now or datetime.now(timezone.utc)
    try:
        expiry=datetime.fromisoformat(plan['expires_at'].replace('Z','+00:00'))
        if expiry.tzinfo is None or not current<expiry<=current+timedelta(days=14):raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ActivationRejected('ACTIVATION_EXPIRY_INVALID') from None
    people=plan['participants']
    if not isinstance(people,list) or not 1<=len(people)<=2:
        raise ActivationRejected('ACTIVATION_PARTICIPANTS_INVALID')
    ids=set();emails=set()
    for person in people:
        if set(person)!={'id','email','display_name','can_save'} or type(person['can_save']) is not bool:
            raise ActivationRejected('ACTIVATION_PARTICIPANT_INVALID')
        try:
            identity=str(UUID(person['id']));email=normalize_operator_email(person['email'])
        except (ValueError,TypeError):raise ActivationRejected('ACTIVATION_PARTICIPANT_INVALID') from None
        if identity!=person['id'] or email!=person['email'] or identity in ids or email in emails:
            raise ActivationRejected('ACTIVATION_PARTICIPANT_DUPLICATE')
        name=person['display_name']
        if not isinstance(name,str) or not 1<=len(name.strip())<=160 or any(ord(c)<32 for c in name):
            raise ActivationRejected('ACTIVATION_DISPLAY_NAME_INVALID')
        ids.add(identity);emails.add(email)
    for key in ('expected_database','expected_sql_role','expected_db_host'):
        if not isinstance(plan[key],str) or not plan[key].strip():raise ActivationRejected('ACTIVATION_SQL_IDENTITY_REQUIRED')
    return plan


def activate_new_operator(conn, plan, operator_id, final_password):
    """Caller owns the transaction and must verify administrative identity first.

    The titular enters a final password privately. Setting must_change_password
    false here records that completed activation; no existing flag is changed.
    """
    validate_plan(plan)
    person=next((p for p in plan['participants'] if p['id']==operator_id),None)
    if not person:raise ActivationRejected('ACTIVATION_IDENTITY_NOT_APPROVED')
    if conn.dialect.name=='postgresql':
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('rtm_private_activation_v1',0))"))
    existing=conn.execute(text('SELECT id FROM rtm_operators WHERE id=:id OR lower(trim(email))=:email'),person).first()
    if existing:raise ActivationRejected('ACTIVATION_EXISTING_IDENTITY_NO_CHANGE')
    code='rtm.supervisor' if person['can_save'] else 'rtm.operator'
    permissions=['ops.view','ops.supervise'] if person['can_save'] else ['ops.view']
    role=conn.execute(text('SELECT id,permissions,active FROM rtm_operator_roles WHERE code=:code'),{'code':code}).mappings().one_or_none()
    if role:
        actual=json.loads(role['permissions']) if isinstance(role['permissions'],str) else role['permissions']
        if set(actual)!=set(permissions) or not role['active']:raise ActivationRejected('ACTIVATION_EXISTING_ROLE_INCOMPATIBLE')
        role_id=str(role['id'])
    else:
        role_id=str(uuid4())
        encoded='CAST(:permissions AS jsonb)' if conn.dialect.name=='postgresql' else ':permissions'
        conn.execute(text('INSERT INTO rtm_operator_roles(id,code,name,permissions,active) VALUES (:id,:code,:name,'+encoded+',true)'),
                     {'id':role_id,'code':code,'name':'Estudio individual autorizado','permissions':json.dumps(permissions)})
    password_hash=hash_operator_password(final_password)
    if not verify_operator_password(password_hash,final_password).valid:raise ActivationRejected('ACTIVATION_PASSWORD_VERIFICATION_FAILED')
    profile=json.dumps({'activation_contract':'rtm_private_activation_v1','case_id':plan['case_id'],
                        'approved_expires_at':plan['expires_at'],'password_source':'titular_final_hidden_input'})
    encoded='CAST(:profile AS jsonb)' if conn.dialect.name=='postgresql' else ':profile'
    conn.execute(text('''INSERT INTO rtm_operators
        (id,email,display_name,password_hash,status,primary_role_id,must_change_password,mfa_required,
         password_changed_at,password_algorithm,password_version,auth_epoch,profile)
        VALUES (:id,:email,:display_name,:password_hash,'active',:role_id,false,false,
                CURRENT_TIMESTAMP,'argon2id',1,1,''' + encoded + ')'),
        {**person,'password_hash':password_hash,'role_id':role_id,'profile':profile})
    metadata=json.dumps({'contract':'rtm_private_activation_v1','case_id':plan['case_id'],
                         'expires_at':plan['expires_at'],'permissions':permissions})
    encoded='CAST(:metadata AS jsonb)' if conn.dialect.name=='postgresql' else ':metadata'
    conn.execute(text('''INSERT INTO rtm_operator_access_events(id,operator_id,event_type,result,metadata)
        VALUES (:event_id,:operator_id,'operator.private_activated','success','''+encoded+')'),
        {'event_id':str(uuid4()),'operator_id':operator_id,'metadata':metadata})
    return {'created':True,'operator_id':operator_id,'role':code,'permissions':permissions}
