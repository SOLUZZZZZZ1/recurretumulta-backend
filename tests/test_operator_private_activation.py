from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
import unittest
from uuid import uuid4
from sqlalchemy import create_engine,text
from rtm_core.operator_private_activation import validate_plan,activate_new_operator,ActivationRejected
from rtm_core.operator_auth_crypto import verify_operator_password

class PrivateActivationTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://');self.conn=self.engine.connect()
        self.conn.execute(text('CREATE TABLE rtm_operator_roles(id TEXT PRIMARY KEY,code TEXT UNIQUE,name TEXT,permissions TEXT,active BOOLEAN)'))
        self.conn.execute(text('''CREATE TABLE rtm_operators(id TEXT PRIMARY KEY,email TEXT UNIQUE,display_name TEXT,password_hash TEXT,status TEXT,
            primary_role_id TEXT,must_change_password BOOLEAN,mfa_required BOOLEAN,password_changed_at TEXT,
            password_algorithm TEXT,password_version INTEGER,auth_epoch INTEGER,profile TEXT)'''))
        self.conn.execute(text('CREATE TABLE rtm_operator_access_events(id TEXT PRIMARY KEY,operator_id TEXT,event_type TEXT,result TEXT,metadata TEXT)'))
        self.person={'id':str(uuid4()),'email':'operator@example.com','display_name':'OPERADOR SINTETICO','can_save':True}
        self.plan={'version':1,'backend_sha':'a'*40,'service':'recurretumulta-backend',
            'case_id':'2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee','expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            'expected_database':'disposable','expected_sql_role':'disposable_owner','expected_db_host':'127.0.0.1',
            'participants':[self.person],'mfa_policy':'not_required_for_approved_pilot'}
        self.password='Isolated final password only!'
    def tearDown(self):self.conn.close();self.engine.dispose()
    def activate(self):return activate_new_operator(self.conn,self.plan,self.person['id'],self.password)
    def test_final_hidden_input_contract_hashes_and_audits_only_new_identity(self):
        result=self.activate();self.conn.commit()
        self.assertTrue(result['created'])
        row=self.conn.execute(text('SELECT * FROM rtm_operators')).mappings().one()
        self.assertNotEqual(row['password_hash'],self.password)
        self.assertTrue(verify_operator_password(row['password_hash'],self.password).valid)
        self.assertEqual((row['status'],row['must_change_password'],row['mfa_required']),('active',0,0))
        audit=self.conn.execute(text('SELECT metadata FROM rtm_operator_access_events')).scalar()
        self.assertNotIn(self.password,audit);self.assertNotIn(row['password_hash'],audit)
    def test_existing_identity_never_reset(self):
        self.activate();before=self.conn.execute(text('SELECT password_hash FROM rtm_operators')).scalar()
        with self.assertRaises(ActivationRejected):self.activate()
        self.assertEqual(before,self.conn.execute(text('SELECT password_hash FROM rtm_operators')).scalar())
    def test_existing_incompatible_role_not_modified(self):
        self.conn.execute(text("INSERT INTO rtm_operator_roles VALUES ('x','rtm.supervisor','Old','[\"ops.admin\"]',1)"))
        with self.assertRaises(ActivationRejected):self.activate()
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_operators')).scalar(),0)
    def test_read_only_participant_has_no_study_write_permission(self):
        self.person['can_save']=False
        result=self.activate();self.assertEqual(result['permissions'],['ops.view'])
    def test_mfa_requirement_is_not_silently_disabled(self):
        self.plan['mfa_policy']='required'
        with self.assertRaises(ActivationRejected):self.activate()
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_operators')).scalar(),0)
    def test_extra_identity_and_credential_fields_rejected(self):
        for change in [{'password':'forbidden'},{'token':'forbidden'},{'global_scope':True}]:
            with self.assertRaises(ActivationRejected):validate_plan({**self.plan,**change})
    def test_wrong_destination_expiry_or_unknown_person_rejected(self):
        for changes in [{'service':'recurretumulta-backend-1'},{'case_id':str(uuid4())},{'expires_at':'2020-01-01T00:00:00+00:00'}]:
            with self.assertRaises(ActivationRejected):validate_plan({**self.plan,**changes})
        with self.assertRaises(ActivationRejected):activate_new_operator(self.conn,self.plan,str(uuid4()),self.password)
    def test_weak_password_rolls_back_new_role_and_account(self):
        self.conn.commit()
        with self.assertRaises(ValueError):
            with self.conn.begin():activate_new_operator(self.conn,self.plan,self.person['id'],'weak')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_operator_roles')).scalar(),0)
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM rtm_operators')).scalar(),0)
