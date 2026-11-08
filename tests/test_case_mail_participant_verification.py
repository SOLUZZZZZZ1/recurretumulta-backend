import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, text

from rtm_core.case_mail_router import ParticipantVerification, verify_participant


class ParticipantVerificationTest(TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        self.case=str(uuid4());self.operator=str(uuid4());self.email='pilot@example.com'
        with self.engine.begin() as conn:
            conn.execute(text('CREATE TABLE cases(id TEXT PRIMARY KEY,contact_email TEXT,payment_status TEXT,paid_at TEXT,authorized BOOLEAN)'))
            conn.execute(text('CREATE TABLE rtm_operators(id TEXT PRIMARY KEY)'))
            conn.execute(text('''CREATE TABLE rtm_case_mail_participants(
                id TEXT PRIMARY KEY,case_id TEXT NOT NULL,email TEXT NOT NULL,verified_at TEXT NOT NULL,
                verified_by TEXT NOT NULL,verification_reference TEXT NOT NULL,active BOOLEAN NOT NULL,
                UNIQUE(case_id,email))'''))
            conn.execute(text('INSERT INTO cases VALUES (:id,:email,\'paid\',CURRENT_TIMESTAMP,1)'),
                {'id':self.case,'email':self.email})
            conn.execute(text('INSERT INTO rtm_operators VALUES (:id)'),{'id':self.operator})
        self.context=SimpleNamespace(conn=None,case_id=self.case,session=SimpleNamespace(operator_id=self.operator))
        self.env=patch.dict(os.environ,{'RTM_CASE_MAIL_DEDICATED_CONFIRMED':'1',
            'RTM_CASE_MAIL_ALLOWED_RECIPIENTS':self.email})
        self.env.start()

    def tearDown(self):
        self.env.stop();self.engine.dispose()

    def invoke(self,email=None,evidence='Customer confirmed mailbox and case communications by phone.'):
        with self.engine.begin() as conn:
            self.context.conn=conn
            return verify_participant(ParticipantVerification(email=email or self.email,
                independently_verified=True,verification_reference=evidence),self.context)

    def test_verification_is_case_bound_audited_and_idempotent(self):
        first=self.invoke();second=self.invoke()
        self.assertFalse(first['already_verified']);self.assertTrue(second['already_verified'])
        with self.engine.begin() as conn:
            row=conn.execute(text('SELECT case_id,email,verified_by,verification_reference,active FROM rtm_case_mail_participants')).one()
        self.assertEqual(tuple(row),(self.case,self.email,self.operator,
            'Customer confirmed mailbox and case communications by phone.',True))

    def test_rejects_non_case_recipient_and_unpaid_case(self):
        with self.assertRaises(HTTPException) as exc:self.invoke('other@example.com')
        self.assertEqual(exc.exception.detail['code'],'MAIL_RECIPIENT_OUTSIDE_CONTROLLED_PILOT')
        with self.engine.begin() as conn:conn.execute(text("UPDATE cases SET payment_status='pending' WHERE id=:id"),{'id':self.case})
        with self.assertRaises(HTTPException) as exc:self.invoke()
        self.assertEqual(exc.exception.detail['code'],'MAIL_PARTICIPANT_CASE_PREREQUISITES_NOT_MET')

    def test_does_not_reactivate_inactive_participant(self):
        with self.engine.begin() as conn:
            conn.execute(text('''INSERT INTO rtm_case_mail_participants
                VALUES (:id,:case,:email,CURRENT_TIMESTAMP,:operator,'prior record',0)'''),
                {'id':str(uuid4()),'case':self.case,'email':self.email,'operator':self.operator})
        with self.assertRaises(HTTPException) as exc:self.invoke()
        self.assertEqual(exc.exception.detail['code'],'MAIL_PARTICIPANT_INACTIVE_REVIEW_REQUIRED')


