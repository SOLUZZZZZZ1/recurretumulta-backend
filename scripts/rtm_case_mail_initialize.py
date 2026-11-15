"""Initialize reviewed mailbox cursors once; never send or import messages."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.rtm_case_mail_preflight import check


class BaselineRejected(ValueError):
    pass


def parse_baseline(value):
    try:
        result={}
        for entry in value.split(','):
            folder,validity,last_uid=entry.rsplit(':',2)
            if not folder or folder in result:raise ValueError()
            validity,last_uid=int(validity),int(last_uid)
            if not 0<validity<=2**32-1 or not 0<=last_uid<=2**32-1:raise ValueError()
            result[folder]=(validity,last_uid)
        if not 1<=len(result)<=3:raise ValueError()
        return result
    except (ValueError,TypeError):
        raise BaselineRejected('MAIL_BASELINE_FORMAT_INVALID') from None


def validate(report,reviewed):
    if not report.get('transport_ok') or not report.get('configuration_valid'):
        raise BaselineRejected('MAIL_TRANSPORT_PREFLIGHT_REQUIRED')
    flags=report['runtime']['flags_enabled']
    if flags['RTM_ENABLE_CASE_MAIL'] or flags['RTM_CASE_MAIL_WORKER_ENABLED']:
        raise BaselineRejected('MAIL_MUST_REMAIN_DISABLED_DURING_INITIALIZATION')
    observed={r['folder']:(r['uidvalidity'],r['last_uid']) for r in report['baseline_candidates']}
    if set(reviewed)!=set(report['folders']) or set(reviewed)!=set(observed):
        raise BaselineRejected('MAIL_BASELINE_FOLDERS_MISMATCH')
    for folder,(validity,last_uid) in reviewed.items():
        if observed[folder][0]!=validity or observed[folder][1]<last_uid:
            raise BaselineRejected('MAIL_BASELINE_REMOTE_CHANGED')


def initialize(engine,report,reviewed):
    from sqlalchemy import text
    validate(report,reviewed)
    mailbox=report['mailbox'];created=0
    with engine.begin() as conn:
        if conn.dialect.name=='postgresql':
            conn.execute(text("SET LOCAL lock_timeout='5s'"))
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            if conn.execute(text('SELECT current_database()')).scalar()!='recurretumulta':
                raise BaselineRejected('MAIL_BASELINE_DATABASE_MISMATCH')
            conn.execute(text('LOCK TABLE rtm_case_mail_sync IN SHARE ROW EXCLUSIVE MODE'))
        rows=conn.execute(text('SELECT folder,uidvalidity,last_uid FROM rtm_case_mail_sync WHERE mailbox=:mailbox'),{'mailbox':mailbox}).mappings().all()
        existing={r['folder']:(r['uidvalidity'],r['last_uid']) for r in rows}
        if existing:
            # Initialization is all-or-nothing. A retry never resets, advances,
            # repairs or silently adds to an existing mailbox configuration.
            if set(existing)!=set(reviewed) or any(existing[f][0]!=v or existing[f][1]<u for f,(v,u) in reviewed.items()):
                raise BaselineRejected('MAIL_BASELINE_EXISTS_REVIEW_REQUIRED')
            return {'ok':True,'created':0,'already_initialized':True,'cursors_preserved':True}
        for folder,(validity,last_uid) in reviewed.items():
            conn.execute(text('INSERT INTO rtm_case_mail_sync(mailbox,folder,uidvalidity,last_uid) VALUES (:mailbox,:folder,:validity,:last_uid)'),
                {'mailbox':mailbox,'folder':folder,'validity':validity,'last_uid':last_uid})
            created+=1
    return {'ok':True,'created':created,'already_initialized':False,'mailbox':mailbox,
            'baseline':[{'folder':f,'uidvalidity':v,'last_uid':u} for f,(v,u) in reviewed.items()]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True,help='Reviewed folder:uidvalidity:last_uid entries, comma separated.')
    parser.add_argument('--apply',action='store_true',help='Create absent cursors transactionally. Never changes an existing cursor.')
    args=parser.parse_args();engine=None
    result={'ok':False,'sends_mail':False,'imports_messages':False}
    try:
        reviewed=parse_baseline(args.baseline)
        report=check(connect=True)
        validate(report,reviewed)
        if args.apply:
            from database import get_engine
            engine=get_engine()
            result.update(initialize(engine,report,reviewed))
        else:
            result.update(ok=True,applied=False,reviewed_baseline=reviewed)
    except BaselineRejected as exc:
        result['error_code']=str(exc)
    except Exception:
        result['error_code']='MAIL_BASELINE_INITIALIZATION_FAILED'
    finally:
        if engine is not None:engine.dispose()
    print('RTM_MAIL_BASELINE '+json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
