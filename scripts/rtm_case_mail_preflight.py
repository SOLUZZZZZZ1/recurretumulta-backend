"""Read-only mail login/folder check; never sends, fetches bodies or writes DB."""
import argparse
import imaplib
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.rtm_production_runtime_readonly import inspect


def check(source=None,*,connect=False,smtp_ssl_factory=None,smtp_factory=None,imap_factory=None):
    source=dict(os.environ if source is None else source)
    runtime=inspect(source)
    out={'read_only':True,'running_commit':runtime.get('running_commit'),'runtime':runtime,
         'transport_ok':False,'network_requested':connect,'sends_mail':False,'writes_database':False}
    required=('exact_production_service','production_environment','expected_database',
              'production_bucket_matches','scope_not_expired','existing_supervisor_in_scope')
    if not all(runtime.get(k) for k in required) or not all(runtime.get('origin_matches',{}).values()):
        out['error_code']='MAIL_RUNTIME_REVIEW_REQUIRED';return out
    try:
        from rtm_core.case_mail_runtime import configuration
        # Validate the would-be worker configuration without changing process
        # flags. This only creates a dataclass; it cannot start a queue.
        config=configuration({**source,'RTM_ENABLE_CASE_MAIL':'1','RTM_CASE_MAIL_WORKER_ENABLED':'1'})
    except Exception:
        out['error_code']='MAIL_CONFIGURATION_INVALID';return out
    out.update(configuration_valid=True,case_id=config.case_id,mailbox=config.mailbox,
               allowed_recipients=list(config.allowed_recipients),folders=list(config.folders),
               delivery_senders=list(config.delivery_senders),delivery_arc_domains=list(config.delivery_arc_domains),
               scope_expires_at=source.get('RTM_CASEWORK_EXPIRES_AT'))
    if not connect:return out
    smtp=None
    try:
        context=ssl.create_default_context()
        if config.smtp_tls=='starttls':
            smtp=(smtp_factory or smtplib.SMTP)(config.smtp_host,config.smtp_port,timeout=20)
            if smtp.ehlo()[0]!=250:raise ValueError()
            if smtp.starttls(context=context)[0]!=220:raise ValueError()
        else:
            smtp=(smtp_ssl_factory or smtplib.SMTP_SSL)(config.smtp_host,config.smtp_port,context=context,timeout=20)
        if smtp.ehlo()[0]!=250:raise ValueError()
        if smtp.login(config.username,config.password)[0] not in (235,503):raise ValueError()
        if smtp.noop()[0]!=250:raise ValueError()
        out['smtp_login_ok']=True
    except Exception:
        out.update(smtp_login_ok=False,smtp_error_code='SMTP_LOGIN_CHECK_FAILED')
    finally:
        if smtp is not None:
            try:smtp.quit()
            except Exception:
                try:smtp.close()
                except Exception:pass
    connection=None;baselines=[]
    try:
        connection=(imap_factory or imaplib.IMAP4_SSL)(config.imap_host,993,ssl_context=ssl.create_default_context(),timeout=20)
        if connection.login(config.username,config.password)[0]!='OK':raise ValueError()
        for folder in config.folders:
            if connection.select('"'+folder+'"',readonly=True)[0]!='OK':raise ValueError()
            validity=int(connection.response('UIDVALIDITY')[1][0])
            next_uid=int(connection.response('UIDNEXT')[1][0])
            if validity<1 or next_uid<1:raise ValueError()
            baselines.append({'folder':folder,'uidvalidity':validity,'last_uid':next_uid-1})
        out.update(imap_login_ok=True,baseline_candidates=baselines)
    except Exception:
        out.update(imap_login_ok=False,imap_error_code='IMAP_LOGIN_OR_FOLDER_CHECK_FAILED')
    finally:
        if connection is not None:
            try:connection.logout()
            except Exception:pass
    out['transport_ok']=bool(out['smtp_login_ok'] and out['imap_login_ok'])
    out['limit']='Login and read-only folder metadata only; delivery and synchronization remain unverified.'
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect',action='store_true',help='Check TLS logins and read-only folder metadata; no email is sent.')
    args=parser.parse_args()
    print('RTM_CASE_MAIL_PREFLIGHT '+json.dumps(check(connect=args.connect),sort_keys=True))
