"""Exact quote-to-message adapter. Saving an assessment never calls this module."""
from sqlalchemy import text
from fastapi import HTTPException
from rtm_core.management_contract import ManagementRejected,timestamp
from datetime import datetime,timezone
from rtm_core.management_store import obj,lock_case,quote_row,quote_projection,event
from rtm_core.case_mail_repository import prepare,queue,message_for_case


def validate_relation(conn,case_id,relation):
    if set(relation)!={'type','quote_id','quote_sha256','study_operation_id','study_sha256'} or relation['type']!='management_quote':
        raise HTTPException(409,{'code':'MAIL_VERSION_ADAPTER_REQUIRED'})
    row=quote_row(conn,case_id,relation['quote_id'])
    if not row:raise ManagementRejected('QUOTE_NOT_FOUND',404)
    quote=quote_projection(row);snapshot=quote['snapshot']
    if (relation['quote_sha256']!=quote['sha256'] or relation['study_operation_id']!=snapshot['study_operation_id']
            or relation['study_sha256']!=snapshot['study_sha256']):
        raise ManagementRejected('MAIL_QUOTE_VERSION_MISMATCH')


def verified_recipient(conn,case_id):
    # The current persisted case contact AND an independently verified participant
    # must agree. No From/Reply-To or operator-entered address changes this binding.
    value=conn.execute(text('SELECT contact_email FROM cases WHERE id=:case'),{'case':case_id}).scalar()
    address=str(value or '').strip().lower()
    row=conn.execute(text('SELECT email FROM rtm_case_mail_participants WHERE case_id=:case AND email=:email AND active=true'),
        {'case':case_id,'email':address}).scalar()
    if not row:raise ManagementRejected('CLIENT_RECIPIENT_NOT_VERIFIED')
    return row


def prepare_quote_message(conn,*,case_id,quote_id,expected_sha,operation_id,author,storage,mailbox,origin):
    lock_case(conn,case_id)
    row=quote_row(conn,case_id)
    if not row or str(row['id'])!=quote_id or row['state']!='prepared':raise ManagementRejected('QUOTE_NOT_READY_TO_COMMUNICATE')
    quote=quote_projection(row)
    if quote['sha256']!=expected_sha:raise ManagementRejected('QUOTE_VERSION_CHANGED')
    if timestamp(quote['snapshot']['expires_at'])<=datetime.now(timezone.utc):raise ManagementRejected('COMMERCIAL_OFFER_EXPIRED')
    snapshot=quote['snapshot'];amount=snapshot['economics']
    euro=lambda cents:f'{cents//100},{cents%100:02d} EUR'
    subject='Tu presupuesto está preparado · RTM · versión '+str(snapshot['revision'])
    body=('Hemos preparado una propuesta para continuar con tu expediente.\n\n'
        'Total, impuestos incluidos: '+euro(amount['total_cents'])+'\n'
        'Estudio inicial abonado y aplicado: '+euro(amount['study_credit_cents'])+'\n'
        'Saldo de gestión: '+euro(amount['balance_cents'])+'\n\n'
        'Consulta la valoración autorizada, el alcance, las exclusiones, los impuestos y la vigencia '
        'en tu acceso privado. Tú decides si aceptas esta versión. Abrir este correo no supone aceptación.\n\n'
        +origin+'/#/cliente/expediente/'+case_id+'\n\n'
        'La vigencia de la oferta no es el plazo administrativo de la multa. '
        'Esta comunicación no firma ni presenta ningún escrito.\n\nRTM · Resuelve tus movidas')
    relation={'type':'management_quote','quote_id':quote_id,'quote_sha256':expected_sha,
        'study_operation_id':snapshot['study_operation_id'],'study_sha256':snapshot['study_sha256']}
    return prepare(conn,case_id=case_id,session=author,operation_id=operation_id,
        recipient=verified_recipient(conn,case_id),subject=subject,body=body,reply_id=None,
        storage=storage,relation=relation,mailbox=mailbox)


def approve_quote_message(conn,*,case_id,message_id,original_sha256,author):
    lock_case(conn,case_id)
    message=message_for_case(conn,case_id,message_id);relation=obj(message['relation'])
    validate_relation(conn,case_id,relation)
    current=quote_row(conn,case_id)
    if str(current['id'])!=relation['quote_id'] or current['state'] not in {'prepared','communicated','accepted','settled'}:
        raise ManagementRejected('QUOTE_SUPERSEDED_DO_NOT_SEND')
    if current['state']!='settled' and timestamp(quote_projection(current)['snapshot']['expires_at'])<=datetime.now(timezone.utc):
        raise ManagementRejected('COMMERCIAL_OFFER_EXPIRED')
    if obj(message['payload'])['raw_sha256']!=original_sha256:
        raise ManagementRejected('MAIL_APPROVAL_CONTENT_MISMATCH')
    if obj(message['payload'])['recipients']!=[verified_recipient(conn,case_id)]:
        raise ManagementRejected('CLIENT_RECIPIENT_CHANGED')
    result=queue(conn,case_id,message_id,author)
    if current['state']=='prepared':
        conn.execute(text("UPDATE rtm_management_quotes SET state='communicated' WHERE id=:id"),{'id':relation['quote_id']})
        event(conn,case_id,'quote.authorized_for_client','operator:'+author.operator_id,relation['quote_id'],
            {'message_id':message_id,'original_sha256':original_sha256,'delivery_confirmed':False})
    return result
