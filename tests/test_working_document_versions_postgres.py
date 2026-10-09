"""Real commit/reopen/concurrency in an isolated CI schema, with synthetic B2.

The signed authority verifier is real; only document projection and object
storage are replaced. This is not a live staging/OPS acceptance test.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import re
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Response
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import files
from rtm_core import working_document_versions as v
from rtm_core.staging_rehearsal import FORM_FIELDS, PROFILE, RehearsalGrant
from tests.postgres_authority_fixture import seed_signed_case_authority
from tests.postgres_security_fixtures import apply_operator_security_schema, seed_supervisor_session, operator_http_environment
from tests.test_working_document_versions import projection


@unittest.skipUnless(os.getenv('RTM_CORE_INTEGRATION_DB') == '1' and os.getenv('DATABASE_URL'), 'Requires disposable CI PostgreSQL')
class WorkingVersionsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ['DATABASE_URL'])
        if url.host not in {'localhost','127.0.0.1','postgres'} or not str(url.database).endswith('_test'):
            raise unittest.SkipTest('Only a disposable local test database is permitted')
        cls.schema = 'rtm_draft_versions_test_' + uuid4().hex
        cls.admin = create_engine(url)
        with cls.admin.begin() as conn: conn.execute(text(f'CREATE SCHEMA {cls.schema}'))
        cls.engine = create_engine(url, connect_args={'options':'-csearch_path=' + cls.schema})
        with cls.engine.begin() as conn:
            conn.execute(text('''CREATE TABLE cases (
                id UUID PRIMARY KEY, contact_email TEXT, contact_name TEXT,
                status TEXT, payment_status TEXT, authorized BOOLEAN, authorized_at TIMESTAMPTZ,
                interested_data JSONB, department TEXT, case_type TEXT, category TEXT,
                test_mode BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
            )'''))
            conn.execute(text('''CREATE TABLE documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), case_id UUID REFERENCES cases(id),
                kind TEXT, b2_bucket TEXT, b2_key TEXT, mime TEXT, size_bytes BIGINT, sha256 TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )'''))
            conn.execute(text('''CREATE TABLE events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), case_id UUID REFERENCES cases(id),
                type TEXT, payload JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
            )'''))
            apply_operator_security_schema(conn)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert re.fullmatch('rtm_draft_versions_test_[0-9a-f]{32}', cls.schema)
        with cls.admin.begin() as conn: conn.execute(text(f'DROP SCHEMA {cls.schema} CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {**operator_http_environment(),
            'RTM_AUTHORITY_SIGNING_SECRET':secrets.token_urlsafe(48)}))
        with self.engine.begin() as conn:
            operator = seed_supervisor_session(conn)
            self.grant = RehearsalGrant(operator.operator_id); self.case = self.grant.case_id
            conn.execute(text('''INSERT INTO cases(id,contact_email,contact_name,status,payment_status,authorized,
                interested_data,department,case_type,test_mode)
                VALUES (:id,:email,:name,'review','paid',TRUE,CAST(:data AS JSONB),'traffic','fine',TRUE)'''),
                {'id':self.case,'email':PROFILE['email'],'name':PROFILE['full_name'],
                 'data':json.dumps({**FORM_FIELDS,'staging_rehearsal':self.grant.marker})})
            seed_signed_case_authority(conn, self.case)
        self.state=projection(self.case); self.blobs={}
        self.enterContext(patch.object(v, 'require_profile'))
        self.loader=self.enterContext(patch.object(v,'load_working_document',side_effect=lambda *_:deepcopy(self.state)))
        def upload(case,kind,data,*_):
            coordinates=('ci-synthetic',f'cases/{case}/{kind}/{uuid4().hex}.pdf')
            self.blobs[coordinates]=data; return coordinates
        self.upload=self.enterContext(patch.object(v.storage,'upload_bytes',side_effect=upload))
        self.enterContext(patch.object(v.storage,'download_bytes_limited',side_effect=lambda b,k,**_:self.blobs[(b,k)]))

    def save(self, latest=None):
        with self.engine.begin() as conn:
            return v.save_version(conn,case_id=self.case,grant=self.grant,uploaded=[],
                body=v.SaveWorkingDocumentBody(expected_source_sha256=self.state['source_sha256'],expected_latest_id=latest))

    def test_committed_version_reopens_on_new_connection_without_recomposition(self):
        first=self.save(); self.loader.reset_mock()
        with self.engine.begin() as conn:
            restored=v.read_version(conn,case_id=self.case,version_id=first['saved_id'],grant=self.grant)
            pdf,meta=v.read_pdf(conn,case_id=self.case,version_id=first['saved_id'],grant=self.grant)
            self.assertEqual(restored['snapshot'],self.state)
            self.assertEqual(pdf,self.blobs[next(iter(self.blobs))])
            self.assertEqual(tuple(conn.execute(text('SELECT test_mode,payment_status,authorized,status FROM cases WHERE id=:id'),{'id':self.case}).one()),
                             (True,'paid',True,'review'))
        self.loader.assert_not_called()
        with patch.object(files,'get_engine',return_value=self.engine), \
             patch.object(files,'require_case_access_token',return_value=self.case), patch.object(files,'get_s3_client') as s3:
            with self.assertRaises(HTTPException) as denied:
                files.presign(Response(),case_id=self.case,document_id=meta['pdf']['document_id'],expires=300,x_case_token='synthetic')
            self.assertEqual(denied.exception.status_code,404);s3.assert_not_called()

    def test_concurrent_retries_commit_one_pdf_and_one_version(self):
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:self.save(),range(2)))
        self.assertEqual(len({row['saved_id'] for row in results}),1)
        self.assertEqual(self.upload.call_count,1)
        with self.engine.begin() as conn:
            self.assertEqual(conn.execute(text('SELECT count(*) FROM events WHERE case_id=:case AND type=:kind'),{'case':self.case,'kind':v.EVENT}).scalar_one(),1)
            self.assertEqual(conn.execute(text('SELECT count(*) FROM documents WHERE case_id=:case AND kind=:kind'),{'case':self.case,'kind':v.DOCUMENT_KIND}).scalar_one(),1)

    def test_changed_source_keeps_the_previous_version_and_its_pdf(self):
        first=self.save()
        self.state['content']+='\nNueva propuesta documental, todavía no aprobada.\n';self.state['source_sha256']='d'*64
        second=self.save(first['saved_id'])
        with self.engine.begin() as conn:
            history=v.list_versions(conn,case_id=self.case,grant=self.grant)
            old=v.read_version(conn,case_id=self.case,version_id=first['saved_id'],grant=self.grant)
            self.assertEqual([entry['sequence'] for entry in history['history']],[2,1])
            self.assertNotEqual(old['snapshot']['content'],self.state['content'])
            self.assertEqual(second['history'][0]['previous_id'],first['saved_id'])
