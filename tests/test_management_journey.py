"""Synthetic contract/ledger tests. Stripe objects here are fixtures, NOT TEST payments."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,text
from rtm_core.management_contract import QuoteInput,ManagementRejected,amounts,digest,canonical
from rtm_core.management_store import save_quote,quote_row,quote_projection,accept_quote,reconcile
from rtm_core.management_payments import record_receipt,reserve,apply_provider_read,obligation,unknown
from rtm_core.management_stripe import obligation_metadata,StripeAdapter,check_session
from rtm_core.management_mail import prepare_quote_message,approve_quote_message
from rtm_core.management_drafts import DraftInput,save as save_draft,version
from rtm_core import management_client_access as client
from tests.test_paid_case_mail import MemoryStorage

TEST_ENGINE=None  # The separate isolated PostgreSQL runner injects its disposable engine.


def sqlite_schema(conn):
    conn.connection.driver_connection.create_function('jsonb_typeof',1,lambda v:'object' if isinstance(json.loads(v),dict) else 'other')
    for sql in [
        'CREATE TABLE cases(id TEXT PRIMARY KEY,payment_status TEXT,product_code TEXT,stripe_session_id TEXT,stripe_payment_intent TEXT,authorized BOOLEAN,contact_email TEXT,interested_data TEXT,expediente_ref TEXT)',
        'CREATE TABLE rtm_operators(id TEXT PRIMARY KEY)',
        'CREATE TABLE rtm_study_assessments(case_id TEXT,operation_id TEXT,revision INTEGER,author_id TEXT,request_sha256 TEXT,snapshot TEXT,snapshot_sha256 TEXT,PRIMARY KEY(case_id,operation_id))',
        'CREATE TABLE ops_final_resources(id TEXT PRIMARY KEY,case_id TEXT,version INTEGER,content TEXT)',
        'CREATE TABLE rtm_invoice_requests(id TEXT PRIMARY KEY,case_id TEXT NOT NULL,stage TEXT NOT NULL,reference_id TEXT NOT NULL,fiscal_data TEXT NOT NULL,recipient_email TEXT NOT NULL,amount_cents INTEGER,currency TEXT NOT NULL,state TEXT NOT NULL,stripe_session_id TEXT,payment_intent_id TEXT,paid_at TEXT,provider_contact_id TEXT,provider_invoice_id TEXT,provider_invoice_number TEXT,provider_invoice_url TEXT,provider_payment_recorded_at TEXT,provider_sent_at TEXT,attempt_started_at TEXT,attempts INTEGER NOT NULL DEFAULT 0,last_error_code TEXT,issued_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(case_id,stage,reference_id))']:
        conn.execute(text(sql))
    for name in ('20261025_case_mail_candidate.sql','20261102_management_journey_candidate.sql','20261102_mail_ingestion_recovery_candidate.sql','20261104_management_generation_candidate.sql'):
        source=(Path(__file__).parents[1]/'migrations'/name).read_text(encoding='utf-8')
        source='\n'.join(line for line in source.splitlines() if not line.strip().startswith('--')).split('CREATE FUNCTION')[0]
        for piece in source.split(';'):
            sql='\n'.join(line for line in piece.splitlines() if not line.strip().startswith('--')).strip()
            if sql.startswith(('CREATE TABLE','CREATE INDEX')):
                sql=sql.replace("'{}'::jsonb","'{}'").replace('DEFAULT NOW()','DEFAULT CURRENT_TIMESTAMP')
                if sql.startswith('CREATE TABLE rtm_management_draft_versions'):
                    sql=sql.replace('author_id UUID NOT NULL','author_id UUID')
                sql=sql.replace("raw_sha256 ~ '^[a-f0-9]{64}$'","raw_sha256 REGEXP '^[a-f0-9]{64}$'")
                conn.execute(text(sql))


class JourneyTest(unittest.TestCase):
    def setUp(self):
        self.engine=TEST_ENGINE or create_engine('sqlite://')
        self.conn=self.engine.connect();self.tx=self.conn.begin()
        if TEST_ENGINE is None:sqlite_schema(self.conn)
        self.case=str(uuid4());self.actor=SimpleNamespace(operator_id=str(uuid4()),display_name='OPERADOR SINTÉTICO')
        self.email='client@example.com';self.mode='test';self.store=MemoryStorage()
        self.policy=SimpleNamespace(mode='test',account_id='acct_fixture',case_id=self.case,origin='https://www.recurretumulta.eu',
            tax_basis_points=2100,tax_label='Impuesto sintético de ensayo',agreed_total_cents=None,
            api_key='sk_test_fixture_not_a_credential',webhook_secret='whsec_fixture_not_a_credential')
        identity={'full_name':'PERSONA SINTÉTICA','dni_nie':'FICTICIO001','domicilio_notif':'DOMICILIO DE ENSAYO'}
        self.conn.execute(text("INSERT INTO cases(id,payment_status,product_code,stripe_session_id,stripe_payment_intent,authorized,contact_email,interested_data,expediente_ref) "
            "VALUES (:id,'paid','REVIEW_BASIC','cs_test_initial','pi_initial',true,:email,:identity,'SINTETICO-001')"),{'id':self.case,'email':self.email,'identity':json.dumps(identity)})
        if self.conn.dialect.name=='postgresql':
            self.conn.execute(text('INSERT INTO rtm_operators(id,email,display_name) VALUES (:id,:email,:name)'),
                {'id':self.actor.operator_id,'email':self.actor.operator_id+'@example.com','name':self.actor.display_name})
        else:self.conn.execute(text('INSERT INTO rtm_operators(id) VALUES (:id)'),{'id':self.actor.operator_id})
        self.conn.execute(text('INSERT INTO rtm_case_mail_participants(id,case_id,email,verified_at,verified_by,verification_reference,active) VALUES (:id,:case,:email,CURRENT_TIMESTAMP,:actor,:ref,true)'),
            {'id':str(uuid4()),'case':self.case,'email':self.email,'actor':self.actor.operator_id,'ref':'SYNTHETIC_FIXTURE'})
        self.studyid=str(uuid4());self.docs=[{'document_id':str(uuid4()),'kind':k,'read_confirmed_by_operator':True,'observed_sha256':hashlib.sha256(b'synthetic').hexdigest()} for k in ('original','identity_front','authorization_signed')]
        facts={k:{'value':'REVISADO SINTETICO '+k,'status':'validated','sources':[{'document_id':self.docs[0]['document_id']}]} for k in ('organismo','expediente_ref','notified_at','facts','deadline')}
        self.study={'case_id':self.case,'operation_id':self.studyid,'revision':1,'sources':self.docs,
            'created_at':datetime.now(timezone.utc).isoformat(),'author':{'id':self.actor.operator_id,'name':self.actor.display_name},
            'study':{'state':'completed','facts':facts,'assessment':'Valoración sintética revisada','missing':''}}
        self.conn.execute(text('INSERT INTO rtm_study_assessments(case_id,operation_id,revision,author_id,request_sha256,snapshot,snapshot_sha256) VALUES (:case,:op,1,:author,:sha,:snapshot,:sha)'),
            {'case':self.case,'op':self.studyid,'author':self.actor.operator_id,'snapshot':canonical(self.study),'sha':digest(self.study)})
        material={'id':str(uuid4()),'case_id':self.case,'stage':'initial_study','mode':'test','account_id':'acct_fixture',
            'session_id':'cs_test_initial','payment_intent_id':'pi_initial','charge_id':'ch_initial'+uuid4().hex,'amount_cents':1000,
            'currency':'eur','provider_status':'succeeded','refunded_cents':0,'disputed':False,'verified_at':datetime.now(timezone.utc).isoformat()}
        self.proof={**material,'evidence_sha256':digest(material)};record_receipt(self.conn,self.proof)

    def tearDown(self):
        self.tx.rollback();self.conn.close()
        if TEST_ENGINE is None:self.engine.dispose()

    def quote(self,total=3900,previous=None):
        body=QuoteInput(operation_id=str(uuid4()),expected_quote_id=previous,study_operation_id=self.studyid,study_sha256=digest(self.study),
            review_confirmed=True,document_type='Alegaciones',title='Actuación sintética',scope='Preparación y revisión de un escrito sintético en una fase.',
            exclusions='Sin firma ni presentación.',client_summary='Explicación sintética autorizada para el cliente.',total_cents=total,
            expires_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
        result=save_quote(self.conn,case_id=self.case,author=self.actor,body=body,receipt=self.proof,policy=self.policy)
        return result['quote'],body

    def communicate(self,quote):
        q=quote['snapshot'];msg=prepare_quote_message(self.conn,case_id=self.case,quote_id=q['id'],expected_sha=quote['sha256'],operation_id=q['id'],
            author=self.actor,storage=self.store,mailbox='expedientes@operativo.example.com',origin=self.policy.origin)
        approve_quote_message(self.conn,case_id=self.case,message_id=msg['id'],original_sha256=msg['message']['raw_sha256'],author=self.actor)
        return msg

    def accepted(self,total=3900):
        quote,_=self.quote(total);self.communicate(quote)
        result=accept_quote(self.conn,case_id=self.case,quote_id=quote['snapshot']['id'],expected_sha=quote['sha256'],operation_id=str(uuid4()),recipient_hash=client.verifier(self.email),confirmed=True)
        return quote,result

    def reserved(self,total=3900):
        q,a=self.accepted(total)
        row=reserve(self.conn,case_id=self.case,quote_id=q['snapshot']['id'],expected_sha=q['sha256'],recipient_hash=client.verifier(self.email),policy=self.policy)
        return q,row

    def provider(self,row,status='paid'):
        metadata=obligation_metadata(row);amount=row['amount_cents'];sid=row['stripe_session_id'] or 'cs_test_'+row['id'].replace('-','')
        pi='pi_'+row['id'].replace('-','')
        result={'id':sid,'livemode':False,'mode':'payment','currency':'eur','amount_total':amount,'metadata':metadata,'client_reference_id':row['id'],
            'status':'complete' if status=='paid' else status,'payment_status':'paid' if status=='paid' else 'unpaid','url':'https://checkout.stripe.com/c/pay/fixture'}
        if status=='paid':result['payment_intent']={'id':pi,'livemode':False,'status':'succeeded','currency':'eur','amount_received':amount,'metadata':metadata,
            'latest_charge':{'id':'ch_'+row['id'].replace('-',''),'livemode':False,'paid':True,'refunded':False,'disputed':False,'amount_refunded':0,'amount':amount,'currency':'eur','payment_intent':pi,'created':int(time.time())}}
        return result

    def settle(self,row,eventid='evt_fixture'):
        return apply_provider_read(self.conn,row,self.provider(row),provider_account_id=self.policy.account_id,event_id=eventid)

    def test_39_and_79_server_cents_inclusive_and_single_credit(self):
        for total,balance in ((3900,2900),(7900,6900)):
            out=amounts(total,self.proof,mode='test',case_id=self.case,account_id='acct_fixture',vat_basis_points=2100,tax_label='fixture')
            self.assertEqual(out['balance_cents'],balance);self.assertEqual(out['net_cents']+out['tax_cents'],total)
        with self.assertRaises(ManagementRejected):amounts(7900,self.proof,mode='test',case_id=self.case,account_id='acct_fixture',vat_basis_points=2100,tax_label='fixture',agreed_total_cents=3900)

    def test_wrong_case_mode_account_refund_currency_and_float_rejected(self):
        for change in ({'case_id':str(uuid4())},{'mode':'live'},{'account_id':'acct_other'},{'refunded_cents':1000},{'currency':'usd'},{'amount_cents':1000.0}):
            proof={**self.proof,**change};proof['evidence_sha256']=digest({k:v for k,v in proof.items() if k!='evidence_sha256'})
            with self.subTest(change=change),self.assertRaises(ManagementRejected):amounts(3900,proof,mode='test',case_id=self.case,account_id='acct_fixture',vat_basis_points=2100,tax_label='fixture')

    def test_quote_requires_completed_exact_study_and_keeps_original(self):
        quote,body=self.quote();same=save_quote(self.conn,case_id=self.case,author=self.actor,body=body,receipt=self.proof,policy=self.policy)
        self.assertEqual(same['quote']['sha256'],quote['sha256'])
        with self.assertRaises(ManagementRejected):self.quote(7900)
        second,_=self.quote(7900,quote['snapshot']['id'])
        self.assertEqual(quote_projection(quote_row(self.conn,self.case,quote['snapshot']['id']))['snapshot'],quote['snapshot'])
        self.assertEqual(second['snapshot']['economics']['balance_cents'],6900)

    def test_preview_not_acceptance_and_no_checkouts_without_it(self):
        quote,_=self.quote();self.communicate(quote)
        with self.assertRaises(ManagementRejected):reserve(self.conn,case_id=self.case,quote_id=quote['snapshot']['id'],expected_sha=quote['sha256'],recipient_hash=client.verifier(self.email),policy=self.policy)
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_obligations')).scalar(),0)

    def test_same_acceptance_and_checkout_after_lost_response(self):
        quote,row=self.reserved()
        unknown(self.conn,row)
        other=reserve(self.conn,case_id=self.case,quote_id=quote['snapshot']['id'],expected_sha=quote['sha256'],recipient_hash=client.verifier(self.email),policy=self.policy)
        self.assertEqual(other['id'],row['id']);self.assertEqual(other['idempotency_key'],row['idempotency_key'])
        self.assertEqual(other['reservation'],row['reservation'])
        self.assertNotIn('case_id',row['reservation']['provider_parameters']['metadata'])
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_obligations')).scalar(),1)

    def test_duplicate_and_out_of_order_notifications_preserve_two_payments(self):
        _,row=self.reserved();self.settle(row)
        self.settle(row)
        apply_provider_read(self.conn,row,self.provider(row,'expired'),provider_account_id='acct_fixture',event_id='evt_older_expired')
        self.settle(row,'evt_late_other_id')
        self.assertEqual(obligation(self.conn,obligation_id=row['id'])['state'],'confirmed')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_receipts')).scalar(),2)
        self.assertEqual(self.conn.execute(text('SELECT sum(amount_cents) FROM rtm_management_receipts')).scalar(),3900)
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_credit_applications')).scalar(),1)
        self.assertEqual(tuple(self.conn.execute(text('SELECT payment_status,stripe_session_id,stripe_payment_intent FROM cases')).one()),('paid','cs_test_initial','pi_initial'))
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_draft_versions')).scalar(),0)

    def test_pending_can_be_confirmed_later_once(self):
        _,row=self.reserved()
        waiting=self.provider(row,'open')
        apply_provider_read(self.conn,row,waiting,provider_account_id='acct_fixture',event_id='evt_open')
        waiting={**waiting,'status':'complete','payment_status':'unpaid'}
        result=apply_provider_read(self.conn,row,waiting,provider_account_id='acct_fixture',event_id='evt_pending')
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_receipts')).scalar(),1)
        self.assertEqual(self.settle(row,'evt_confirmed_later')['state'],'confirmed')
        self.settle(row,'evt_confirmed_later')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_credit_applications')).scalar(),1)

    def test_ops_projection_never_exposes_client_link_even_when_quoted(self):
        from rtm_core.case_mail_repository import conversation,public_message
        client.request_link(self.conn,case_id=self.case,email=self.email,peer='127.0.0.1',policy=self.policy,storage=self.store,mailbox='expedientes@operativo.example.com')
        raw=next(iter(self.store.values.values()));from rtm_core.case_mail import parse_original
        token=parse_original(raw)['body'].split('?enlace=')[1].split()[0]
        self.assertNotIn(token,canonical(conversation(self.conn,self.case)))
        self.assertEqual(conversation(self.conn,self.case,token[:12])['messages'],[])
        stored=self.conn.execute(text('SELECT * FROM rtm_case_messages WHERE case_id=:id'),{'id':self.case}).mappings().one()
        quoted=dict(stored);quoted['relation']='{}'
        self.assertNotIn(token,canonical(public_message(quoted)))
        quoted_payload=json.loads(quoted['payload']) if isinstance(quoted['payload'],str) else dict(quoted['payload'])
        for field in ('subject','original_id','sender'):
            quoted_payload[field]='https://example.com/?enlace='+token
        quoted_payload['attachments']=[{'name':'?enlace='+token}]
        quoted['payload']=quoted_payload
        self.assertNotIn(token,canonical(public_message(quoted)))
        self.assertIn(token,parse_original(raw)['body'])

    def test_tampered_checkout_amount_rejected_before_provider_creation(self):
        _,row=self.reserved();row=copy.deepcopy(row)
        row['reservation']['provider_parameters']['line_items'][0]['price_data']['unit_amount']=1000
        from unittest.mock import Mock
        sdk=Mock();sdk.Account.retrieve.return_value={'id':'acct_fixture'}
        with self.assertRaises(ManagementRejected):StripeAdapter(self.policy,sdk=sdk).create(row)
        sdk.checkout.Session.create.assert_not_called()

    def test_notifications_can_reconcile_with_new_sales_disabled_without_extending_access(self):
        from rtm_core.management_policy import configuration
        source={'RTM_ENABLE_MANAGEMENT_JOURNEY':'0','RTM_ENABLE_MANAGEMENT_RECONCILIATION':'1',
            'RTM_MANAGEMENT_PAYMENT_MODE':'test','RTM_MANAGEMENT_STRIPE_ACCOUNT_ID':'acct_fixture',
            'RTM_MANAGEMENT_STRIPE_TEST_KEY':'sk_test_fixture','RTM_MANAGEMENT_STRIPE_TEST_WEBHOOK_SECRET':'whsec_fixture',
            'RTM_CASEWORK_ALLOWED_CASE_IDS':self.case,'RTM_CASEWORK_PUBLIC_ORIGIN':self.policy.origin,
            'RTM_MANAGEMENT_TAX_BASIS_POINTS':'2100','RTM_MANAGEMENT_TAX_LABEL':'Impuesto sintético',
            'RTM_CASEWORK_EXPIRES_AT':'2020-01-01T00:00:00Z','RTM_MANAGEMENT_DRAFT_READY':'0','RTM_MANAGEMENT_MAIL_VERIFIED':'0'}
        self.assertEqual(configuration(source,for_notification=True,mode='test').case_id,self.case)
        with self.assertRaises(ManagementRejected):configuration(source)
        with self.assertRaises(ManagementRejected):configuration(source,for_notification=True,mode='live')

    def test_mismatched_signed_notice_data_does_not_settle(self):
        _,row=self.reserved()
        for change in ({'amount_total':3900},{'currency':'usd'},{'livemode':True},{'metadata':{}},{'client_reference_id':str(uuid4())}):
            with self.subTest(change=change),self.assertRaises(ManagementRejected):apply_provider_read(self.conn,row,{**self.provider(row),**change},provider_account_id='acct_fixture')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_receipts')).scalar(),1)

    def test_expired_old_quote_and_late_payment_need_review_not_new_charge(self):
        quote,row=self.reserved()
        with self.assertRaises(ManagementRejected):self.quote(7900,quote['snapshot']['id'])
        apply_provider_read(self.conn,row,self.provider(row,'expired'),provider_account_id='acct_fixture')
        self.quote(7900,quote['snapshot']['id'])
        result=self.settle(row)
        self.assertEqual(result['state'],'review_required')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_management_credit_applications')).scalar(),0)

    def test_webhook_signature_verified_before_metadata_and_mode_checked(self):
        import hmac
        adapter=StripeAdapter(self.policy)
        _,row=self.reserved();body={'id':'evt_fixture','type':'checkout.session.completed','livemode':False,'data':{'object':self.provider(row)}}
        raw=json.dumps(body).encode();now=int(time.time())
        signature=hmac.new(self.policy.webhook_secret.encode(),str(now).encode()+b'.'+raw,hashlib.sha256).hexdigest()
        self.assertEqual(adapter.webhook(raw,f't={now},v1={signature}')[0],'evt_fixture')
        with self.assertRaises(ManagementRejected):adapter.webhook(raw,f't={now},v1=wrong')
        with self.assertRaises(ManagementRejected):adapter.webhook(raw+b' ',f't={now},v1={signature}')

    def test_quote_message_exact_private_link_no_automatic_send(self):
        quote,_=self.quote()
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_case_mail_jobs')).scalar(),0)
        msg=self.communicate(quote)
        self.assertEqual(msg['message']['sender'],'expedientes@operativo.example.com')
        self.assertIn('29,00 EUR',msg['message']['body']);self.assertIn('/#/cliente/expediente/'+self.case,msg['message']['body'])
        self.assertNotIn('Valoración sintética revisada',msg['message']['body'])
        approve_quote_message(self.conn,case_id=self.case,message_id=msg['id'],original_sha256=msg['message']['raw_sha256'],author=self.actor)
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_case_mail_jobs')).scalar(),1)

    def test_one_use_private_client_scope_and_delayed_old_logout(self):
        client.request_link(self.conn,case_id=self.case,email=self.email,peer='127.0.0.1',policy=self.policy,storage=self.store,mailbox='expedientes@operativo.example.com')
        raw=next(iter(self.store.values.values()));from rtm_core.case_mail import parse_original
        token=parse_original(raw)['body'].split('?enlace=')[1].split()[0]
        first=client.redeem(self.conn,token=token,previous=None,policy=self.policy)
        grant=client.session(self.conn,token=first,case_id=self.case,policy=self.policy)
        with self.assertRaises(ManagementRejected):client.redeem(self.conn,token=token,previous=None,policy=self.policy)
        with self.assertRaises(ManagementRejected):client.session(self.conn,token=first,case_id=str(uuid4()),policy=self.policy)
        client.request_link(self.conn,case_id=self.case,email=self.email,peer='127.0.0.1',policy=self.policy,storage=self.store,mailbox='expedientes@operativo.example.com')
        raw=list(self.store.values.values())[-1];token=parse_original(raw)['body'].split('?enlace=')[1].split()[0]
        second=client.redeem(self.conn,token=token,previous=first,policy=self.policy)
        client.logout(self.conn,token=second,expected_ref=grant['session_ref'])
        self.assertEqual(client.session(self.conn,token=second,case_id=self.case,policy=self.policy)['recipient_hash'],client.verifier(self.email))

    def draft_input(self,quote,**changes):
        value={'operation_id':str(uuid4()),'expected_version_id':None,'quote_id':quote['snapshot']['id'],
            'study_operation_id':self.studyid,'study_sha256':digest(self.study),'action':'prepare',
            'reviewed':{'document_type':'Alegaciones','primary_strategy':'Estrategia sintética revisada para el ensayo.',
                'legal_arguments':[{'title':'Argumento sintético','body':'Fundamento ficticio explícitamente revisado para la prueba, sin uso jurídico.'}],
                'requested_outcomes':['Petición sintética revisada.'],'identity_and_phase_reviewed':True,'contract_scope_confirmed':True},
            'correction_reason':'Preparación sintética revisada.','draft_only_acknowledged':True}
        return DraftInput.model_validate({**value,**changes})

    def test_draft_requires_balance_and_exact_versions_recover_after_correction(self):
        quote,row=self.reserved();body=self.draft_input(quote)
        with self.assertRaises(ManagementRejected):save_draft(self.conn,case_id=self.case,author=self.actor,body=body,policy=self.policy)
        self.settle(row)
        with patch('rtm_core.management_drafts.document_row',return_value={}),patch('rtm_core.management_drafts.read_custody',return_value=(b'synthetic',self.docs[0]['observed_sha256'],'not_recorded')):
            first=save_draft(self.conn,case_id=self.case,author=self.actor,body=body,policy=self.policy)['version']
            corrected=self.draft_input(quote,action='correct',reviewed=None,expected_version_id=first['snapshot']['id'],expected_version_sha256=first['sha256'],content=first['snapshot']['content']+'\nCorrección sintética exacta.\n')
            second=save_draft(self.conn,case_id=self.case,author=self.actor,body=corrected,policy=self.policy)['version']
            with self.assertRaises(ManagementRejected):save_draft(self.conn,case_id=self.case,author=self.actor,body=self.draft_input(quote,action='correct',content='Texto basado en la versión antigua.'),policy=self.policy)
        self.assertEqual(version(self.conn,self.case,first['snapshot']['id']),first)
        self.assertEqual(version(self.conn,self.case,second['snapshot']['id']),second)
        self.assertEqual(second['snapshot']['previous_sha256'],first['sha256'])
        self.assertFalse(second['snapshot']['presented'])
        recovered=reconcile(self.conn,self.case,str(corrected.operation_id),'draft','operator:'+self.actor.operator_id)
        self.assertEqual(recovered['result']['version'],second)


if __name__=='__main__':unittest.main()
