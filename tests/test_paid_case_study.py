import copy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine,text
from rtm_core.paid_study import StudyInput, FACT_KEYS, save_assessment,read_history,read_operation


class StudyTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        self.conn=self.engine.connect()
        self.case=str(uuid4()); self.actor=SimpleNamespace(operator_id=str(uuid4()),display_name='OPERADOR SINTETICO')
        self.conn.execute(text('CREATE TABLE cases (id TEXT PRIMARY KEY,payment_status TEXT,product_code TEXT,authorized BOOLEAN)'))
        self.conn.execute(text("INSERT INTO cases VALUES (:id,'paid','REVIEW_BASIC',1)"),{'id':self.case})
        self.conn.execute(text('''CREATE TABLE rtm_study_assessments(case_id TEXT,operation_id TEXT,revision INTEGER,
            author_id TEXT,request_sha256 TEXT,snapshot TEXT,snapshot_sha256 TEXT,
            PRIMARY KEY(case_id,operation_id),UNIQUE(case_id,revision))'''))
        self.rows={str(uuid4()):{'kind':k,'mime':'application/pdf','size_bytes':3} for k in ['original','identity_front','authorization_signed']}
        self.sha=hashlib.sha256(b'pdf').hexdigest()
        self.data={'operation_id':str(uuid4()),'expected_revision':0,'state':'in_progress',
            'facts':{k:{'value':None,'status':'unresolved'} for k in FACT_KEYS},
            'documents':[{'document_id':key,'observed_sha256':self.sha,'read_confirmed':True} for key in self.rows],
            'assessment':'Valoracion sintetica exacta\nLinea 2','missing':'Revisar plazo',
            'options':'Revisar hechos','next_action':'Confirmar fecha documental'}
        self.patches=[patch('rtm_core.paid_study.document_row',side_effect=lambda c,case,doc:self.rows[doc]),
                      patch('rtm_core.paid_study.read_custody',return_value=(b'pdf',self.sha,'not_recorded'))]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in self.patches:p.stop()
        self.conn.close();self.engine.dispose()

    def save(self,data=None):return save_assessment(self.conn,self.case,self.actor,StudyInput.model_validate(data or self.data))

    def test_exact_recovery_idempotency_and_originals(self):
        saved=self.save();self.conn.commit()
        self.assertEqual(self.save(),saved)
        self.assertEqual(read_operation(self.conn,self.case,self.data['operation_id']),saved)
        self.assertEqual(read_history(self.conn,self.case)['revision'],1)
        self.assertEqual(saved['snapshot']['study']['assessment'],self.data['assessment'])
        self.assertEqual(self.conn.execute(text('SELECT payment_status FROM cases')).scalar(),'paid')

    def test_stale_writer_and_changed_operation_rejected(self):
        self.save()
        for mutation in [{'operation_id':str(uuid4())},{'assessment':'Different'}]:
            with self.assertRaises(HTTPException) as ctx:self.save({**self.data,**mutation})
            self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(read_history(self.conn,self.case)['revision'],1)

    def test_new_assessment_keeps_previous_exact(self):
        first=self.save()
        second=self.save({**self.data,'expected_revision':1,'operation_id':str(uuid4()),'assessment':'Correccion sintetica'})
        self.assertEqual(second['snapshot']['previous_sha256'],first['sha256'])
        self.assertEqual(read_operation(self.conn,self.case,self.data['operation_id']),first)

    def test_payment_and_authorization_gates(self):
        for sql in ["UPDATE cases SET payment_status='pending'", "UPDATE cases SET payment_status='paid',authorized=0"]:
            self.conn.execute(text(sql))
            with self.assertRaises(HTTPException):self.save()
        self.assertEqual(read_history(self.conn,self.case)['revision'],0)

    def test_required_documents_and_changed_hash(self):
        for documents in [self.data['documents'][:1],[{**d,'observed_sha256':'0'*64} for d in self.data['documents']]]:
            with self.assertRaises(HTTPException):self.save({**self.data,'documents':documents})

    def test_scope_and_integrity(self):
        self.save()
        with self.assertRaises(HTTPException) as ctx:read_operation(self.conn,str(uuid4()),self.data['operation_id'])
        self.assertEqual(ctx.exception.status_code,404)
        self.conn.execute(text("UPDATE rtm_study_assessments SET snapshot_sha256=:sha"),{'sha':'0'*64})
        with self.assertRaises(HTTPException) as ctx:read_history(self.conn,self.case)
        self.assertEqual(ctx.exception.status_code,503)

    def test_no_actor_injection_or_pretend_completion(self):
        for changes in [{'author':'fake'},{'state':'completed'},{'state':'blocked','missing':''}]:
            with self.assertRaises(ValueError):StudyInput.model_validate({**self.data,**changes})
