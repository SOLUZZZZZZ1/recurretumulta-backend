"""Snapshot custody tests. Storage/authority doubles are explicit, not a live OPS run."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from pydantic import ValidationError
from pypdf import PdfReader

from rtm_core import working_document_versions as v
from rtm_core import working_document_versions_router as routes
from rtm_core.staging_rehearsal import FORM_FIELDS, PROFILE, RehearsalGrant
from rtm_core.working_document import compose_working_document


class Rows:
    def __init__(self, rows=()): self.rows = list(rows)
    def first(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows
    def mappings(self): return self


class MemoryConnection:
    def __init__(self): self.events, self.documents, self.sql = {}, {}, []
    def execute(self, statement, values):
        sql = ' '.join(str(statement).split()); self.sql.append(sql)
        if sql.startswith("SELECT id,payload->'envelope'"):
            return Rows((key, row['payload']['envelope']) for key, row in self.events.items() if row['case_id'] == values['case_id'])
        if sql.startswith("SELECT payload->'snapshot'"):
            row = self.events.get(values['id'])
            return Rows([(row['payload']['snapshot'],)] if row and row['case_id'] == values['case_id'] else [])
        if sql.startswith('SELECT b2_bucket'):
            row = self.documents.get(values['id'])
            return Rows([(row['bucket'], row['key'], row['sha'], row['size'], 'application/pdf')]
                        if row and row['case_id'] == values['case_id'] and row['kind'] == values['kind'] else [])
        if sql.startswith('INSERT INTO documents'):
            self.documents[values['id']] = dict(values); return Rows()
        if sql.startswith('INSERT INTO events'):
            self.events[values['id']] = {**values, 'payload': json.loads(values['payload'])}; return Rows()
        raise AssertionError('Unexpected SQL: ' + sql)


def projection(case_id):
    return compose_working_document(case_id=case_id, identity={
        'full_name': PROFILE['full_name'], 'dni_nie': PROFILE['dni_nie'], 'domicilio_notif': FORM_FIELDS['domicilio_notif']},
        wrapper={'extracted': {'traffic_generic_facts': {
            'document_type': 'requerimiento_identificacion_conductor',
            'document_title': 'Petición de datos al titular para identificar al conductor',
            'matricula': 'RTM-TEST-002', 'expediente_ref': 'RTM-RADAR-TEST-001'},
            'raw_text_blob': 'Petición de datos al titular para identificar al conductor'}}, event={})


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.grant = RehearsalGrant(str(uuid4())); self.case = self.grant.case_id
        self.conn, self.blobs = MemoryConnection(), {}
        self.state = projection(self.case)
        self.enterContext(patch.dict(os.environ, {'RTM_AUTHORITY_SIGNING_SECRET': 'synthetic-draft-custody-test-key-2026'}))
        self.enterContext(patch.object(v, '_context', return_value='a'*64))
        self.loader = self.enterContext(patch.object(v, 'load_working_document', side_effect=lambda *_: deepcopy(self.state)))
        def upload(case, kind, data, ext, mime):
            coordinates = ('synthetic-bucket', f'cases/{case}/{kind}/{uuid4().hex}.pdf')
            self.blobs[coordinates] = data; return coordinates
        self.upload = self.enterContext(patch.object(v.storage, 'upload_bytes', side_effect=upload))
        self.download = self.enterContext(patch.object(v.storage, 'download_bytes_limited',
            side_effect=lambda bucket, key, **kw: self.blobs[(bucket, key)]))

    def save(self, latest=None, **changes):
        body = v.SaveWorkingDocumentBody(expected_source_sha256=self.state['source_sha256'], expected_latest_id=latest, **changes)
        return v.save_version(self.conn, case_id=self.case, grant=self.grant, body=body, uploaded=[])

    def read(self, version_id):
        return v.read_version(self.conn, case_id=self.case, version_id=version_id, grant=self.grant)

    def test_save_reopen_text_and_stored_pdf_are_exact_without_reanalysis_or_approval(self):
        result = self.save(); version_id = result['saved_id']; self.loader.reset_mock()
        history = v.list_versions(self.conn, case_id=self.case, grant=self.grant)
        restored = self.read(version_id)
        data, entry = v.read_pdf(self.conn, case_id=self.case, version_id=version_id, grant=self.grant)
        self.assertEqual(history['latest_id'], version_id)
        self.assertEqual(restored['snapshot'], self.state)
        self.assertEqual(hashlib.sha256(data).hexdigest(), entry['pdf']['sha256'])
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
        self.assertIn('RTM-TEST-002', text); self.assertIn('PENDIENTE', text)
        self.assertIn('Persona que conducía: [PENDIENTE', restored['snapshot']['content'])
        self.assertFalse(result['final_resource_generated']); self.assertEqual(entry['status'], 'working_draft')
        self.assertFalse(any(sql.startswith('UPDATE') for sql in self.conn.sql))
        self.assertEqual({row['event'] for row in self.conn.events.values()}, {v.EVENT})
        self.loader.assert_not_called()

    def test_retry_recovers_same_version_without_another_upload(self):
        first = self.save(); again = self.save()
        self.assertTrue(again['reused']); self.assertEqual(first['saved_id'], again['saved_id'])
        self.assertEqual(len(self.conn.events), 1); self.upload.assert_called_once()

    def test_new_source_preserves_previous_text_and_pdf_and_requires_latest_id(self):
        first = self.save(); old = self.read(first['saved_id'])
        self.state['content'] += '\nDato documental actualizado, pendiente de revisión.\n'
        self.state['source_sha256'] = 'b'*64
        with self.assertRaises(HTTPException): self.save()
        second = self.save(first['saved_id'])
        self.assertEqual(second['history'][0]['sequence'], 2)
        self.assertEqual(self.read(first['saved_id']), old)
        self.assertNotEqual(self.read(second['saved_id'])['snapshot']['content'], old['snapshot']['content'])

    def test_stale_source_and_foreign_projection_do_not_upload(self):
        body = v.SaveWorkingDocumentBody(expected_source_sha256='f'*64, expected_latest_id=None)
        with self.assertRaises(HTTPException):
            v.save_version(self.conn, case_id=self.case, grant=self.grant, body=body, uploaded=[])
        self.state['case_id'] = str(uuid4())
        with self.assertRaises(HTTPException): self.save()
        self.upload.assert_not_called()

    def test_foreign_version_or_document_row_never_downloads(self):
        first = self.save()
        with self.assertRaises(HTTPException):
            v.read_pdf(self.conn, case_id=self.case, version_id=str(uuid4()), grant=self.grant)
        doc = next(iter(self.conn.documents.values())); doc['case_id'] = str(uuid4())
        with self.assertRaises(HTTPException):
            v.read_pdf(self.conn, case_id=self.case, version_id=first['saved_id'], grant=self.grant)
        self.download.assert_not_called()

    def test_content_metadata_and_pdf_tampering_are_rejected(self):
        first = self.save(); row = self.conn.events[first['saved_id']]; original = deepcopy(row)
        row['payload']['snapshot']['content'] += ' forged'
        with self.assertRaises(HTTPException): self.read(first['saved_id'])
        self.conn.events[first['saved_id']] = deepcopy(original)
        self.conn.events[first['saved_id']]['payload']['envelope']['material']['case_id'] = str(uuid4())
        with self.assertRaises(HTTPException): self.read(first['saved_id'])
        self.conn.events[first['saved_id']] = original
        coordinate = next(iter(self.blobs)); self.blobs[coordinate] += b'forged'
        with self.assertRaises(HTTPException):
            v.read_pdf(self.conn, case_id=self.case, version_id=first['saved_id'], grant=self.grant)

    def test_browser_cannot_supply_content_actor_or_approval(self):
        for changes in ({'content':'forged'}, {'actor':'forged'}, {'approved':True},
                        {'expected_latest_id':'bad'}, {'expected_source_sha256':'invalid'}):
            body = {'expected_source_sha256':'a'*64, 'expected_latest_id':None, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                v.SaveWorkingDocumentBody.model_validate(body)


class BoundaryTests(unittest.TestCase):
    def test_scope_and_profile_are_checked_before_database_access(self):
        with patch.object(routes, '_scope', side_effect=HTTPException(403, 'denied')), patch.object(routes, 'get_engine') as engine:
            for call in (
                lambda: routes.get_versions('case', Request({'type':'http'}), Response(), 'token'),
                lambda: routes.post_version('case', v.SaveWorkingDocumentBody(expected_source_sha256='a'*64, expected_latest_id=None), Request({'type':'http'}), Response(), 'token'),
                lambda: routes.get_version('case', 'version', Request({'type':'http'}), Response(), 'token'),
                lambda: routes.get_version_pdf('case', 'version', Request({'type':'http'}), 'token')):
                with self.assertRaises(HTTPException): call()
            engine.assert_not_called()

    def test_context_does_not_accept_an_unmarked_or_unpaid_case(self):
        grant = RehearsalGrant(str(uuid4()))
        row = {'id':grant.case_id, 'test_mode':True, 'department':'traffic', 'case_type':'fine',
               'interested_data':{**FORM_FIELDS, 'staging_rehearsal':grant.marker},
               'contact_email':PROFILE['email'], 'contact_name':PROFILE['full_name'],
               'payment_status':'paid', 'authorized':True, 'status':'review'}
        conn=Mock(); conn.execute.return_value.mappings.return_value.first.return_value = row
        with patch.object(v, 'require_profile'), patch.object(v, 'verify_signed_case_authority', return_value={'test':'authority'}) as signed:
            self.assertEqual(len(v._context(conn, grant.case_id, grant)), 64)
            for key, value in [('test_mode',False), ('payment_status','unpaid'), ('authorized',False), ('status','closed')]:
                changed={**row,key:value}; conn.execute.return_value.mappings.return_value.first.return_value=changed
                with self.subTest(key=key), self.assertRaises(HTTPException): v._context(conn,grant.case_id,grant)
            signed.assert_called_once()

    def test_public_presign_excludes_saved_working_drafts(self):
        source=(Path(__file__).parents[1]/'files.py').read_text()
        self.assertIn("AND COALESCE(d.kind, '') <> 'rtm_working_document_pdf'", source)
        self.assertIn("AND COALESCE(d.kind, '') <> 'external_revision'", source)

    def test_failed_save_uses_existing_ambiguous_commit_safe_compensation(self):
        engine=Mock(); engine.begin.return_value.__enter__=Mock(return_value=Mock())
        engine.begin.return_value.__exit__=Mock(side_effect=RuntimeError('lost commit reply'))
        def save(*args,uploaded,**kwargs): uploaded.append(('test-bucket','cases/new'));return {'ok':True}
        from rtm_core import study_router
        with patch.object(routes,'_scope',return_value=(object(),object())), patch.object(routes,'get_engine',return_value=engine), \
             patch.object(routes,'require_case_in_scope',return_value='case'), patch.object(v,'save_version',side_effect=save), \
             patch.object(study_router,'_cleanup_uncommitted_documents') as cleanup:
            with self.assertRaises(RuntimeError):
                routes.post_version('case',v.SaveWorkingDocumentBody(expected_source_sha256='a'*64,expected_latest_id=None),Request({'type':'http'}),Response(),'token')
            cleanup.assert_called_once_with(engine,[('test-bucket','cases/new')])


if __name__ == '__main__': unittest.main()
