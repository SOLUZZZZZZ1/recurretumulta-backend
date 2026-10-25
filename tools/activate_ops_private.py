"""Prepared for a future authorized owner console; never invoked by app.py/CI.

Usage (after authorization): python -B tools/activate_ops_private.py --apply PLAN.json UUID
PLAN contains approved identities and destination metadata, never secrets.
The administrative URL must be supplied through protected server configuration
RTM_ACTIVATION_DATABASE_URL, not CLI/history, and removed after the intervention.
"""
import getpass
import json
import logging
import os
from pathlib import Path
import sys
import warnings

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from rtm_core.operator_private_activation import validate_plan,activate_new_operator,ActivationRejected


def main():
    engine=None
    phase='guard'
    try:
        if len(sys.argv)!=4 or sys.argv[1]!='--apply' or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ActivationRejected('ACTIVATION_ATTENDED_CONSOLE_REQUIRED')
        # Refuse getpass fallback that would echo a password on an unusable TTY.
        warnings.filterwarnings('error',category=getpass.GetPassWarning)
        plan=validate_plan(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
        person=next((p for p in plan['participants'] if p['id']==sys.argv[3]),None)
        if not person:raise ActivationRejected('ACTIVATION_IDENTITY_NOT_APPROVED')
        if os.getenv('RTM_ENV')!='production' or os.getenv('RENDER_SERVICE_NAME')!=plan['service'] or os.getenv('RENDER_GIT_COMMIT')!=plan['backend_sha']:
            raise ActivationRejected('ACTIVATION_RUNTIME_IDENTITY_MISMATCH')
        if os.getenv('RTM_ENABLE_PRODUCTION_CASEWORK','0')!='0':raise ActivationRejected('ACTIVATION_PROFILE_MUST_REMAIN_OFF')
        raw=os.getenv('RTM_ACTIVATION_DATABASE_URL','')
        url=make_url(raw)
        if url.get_backend_name()!='postgresql' or url.host!=plan['expected_db_host'] or url.database!=plan['expected_database']:
            raise ActivationRejected('ACTIVATION_CONNECTION_DESTINATION_MISMATCH')
        logging.getLogger('sqlalchemy').disabled=True
        engine=create_engine(url,echo=False,hide_parameters=True,connect_args={'connect_timeout':5})
        phase='sql_identity'
        with engine.connect() as conn:
            identity=conn.execute(text('SELECT current_database(),current_user,session_user')).one()
            if tuple(identity)!=(plan['expected_database'],plan['expected_sql_role'],plan['expected_sql_role']):
                raise ActivationRejected('ACTIVATION_SQL_IDENTITY_MISMATCH')
            existing=conn.execute(text('SELECT id FROM rtm_operators WHERE id=:id OR lower(trim(email))=:email'),person).first()
            if existing:
                raise ActivationRejected('ACTIVATION_EXISTING_IDENTITY_NO_CHANGE')
        print('Activación privada de una cuenta NUEVA. Titular:',person['display_name'],person['email'])
        print('El titular introduce y conserva su contraseña final; no se registra ni se comunica al agente.')
        if input('Escriba ACTIVAR_CUENTA_NUEVA para confirmar su identidad: ')!='ACTIVAR_CUENTA_NUEVA':
            raise ActivationRejected('ACTIVATION_CANCELLED')
        phase='hidden_password'
        password=getpass.getpass('Contraseña final (oculta): ')
        confirmation=getpass.getpass('Confirmación (oculta): ')
        if password!=confirmation:raise ActivationRejected('ACTIVATION_CONFIRMATION_MISMATCH')
        phase='transaction'
        with engine.begin() as conn:
            result=activate_new_operator(conn,plan,person['id'],password)
        del password,confirmation
        phase='new_read'
        with engine.connect() as conn:
            row=conn.execute(text('SELECT status,must_change_password,mfa_required FROM rtm_operators WHERE id=:id'),{'id':person['id']}).one()
            if tuple(row)!=('active',False,False):raise ActivationRejected('ACTIVATION_READ_NOT_CONFIRMED')
        print(json.dumps({**result,'new_read_confirmed':True,'login_verified':False}))
        print('Debe comprobarse después el inicio individual normal; no se ha creado ninguna sesión por este auxiliar.')
        return 0
    except Exception as error:
        code=str(error) if isinstance(error,ActivationRejected) else 'ACTIVATION_FAILED'
        sqlstate=getattr(getattr(error,'orig',None),'sqlstate',None)
        print(json.dumps({'ok':False,'phase':phase,'code':code,'type':type(error).__name__,'sqlstate':sqlstate,
                          'instruction':'Detenerse; no repetir si la transacción pudo confirmarse. Reconciliar primero por el UUID aprobado.'}))
        return 1
    finally:
        if engine:engine.dispose()

if __name__=='__main__':
    raise SystemExit(main())
