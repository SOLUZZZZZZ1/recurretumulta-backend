"""Unit/route tests with explicit database and custody doubles, not live acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4

from fastapi import HTTPException, Request, Response, UploadFile
from io import BytesIO
from pydantic import ValidationError

from rtm_core import case_working_document as draft
from rtm_core import working_document_versions as v
from rtm_core import working_document_versions_router as routes
from rtm_core.ops_case_scope import OpsCaseScope
from rtm_core.upload_security import validate_draft_correction, UploadSecurityError, MAX_DRAFT_TEXT_BYTES
from tests.test_working_document_versions import Rows as BaseRows, MemoryConnection


class Rows(BaseRows):
    def __iter__(self): return iter(self.rows)


class CaseMemory(MemoryConnection):
    def __init__(self, case):
        super().__init__()
        self.case = case
        self.sources = [{"id": str(uuid4()), "kind": "identity_front", "sha256": "b" * 64,
            "size_bytes": 100, "mime": "application/pdf", "created_at": datetime.now(timezone.utc)}]

    def execute(self, statement, values):
        sql = ' '.join(str(statement).split())
        if sql.startswith("SELECT to_jsonb"):
            return Rows([(deepcopy(self.case),)] if values['case_id'] == self.case['id'] else [])
        if sql.startswith("SELECT id::text AS id,kind"):
            return Rows(deepcopy(self.sources))
        if sql.startswith("SELECT id,payload->'envelope'"):
            return Rows((key, row['payload']['envelope']) for key, row in self.events.items()
                if row['case_id'] == values['case_id'] and row['event'] == values['event'])
        if sql.startswith("SELECT id,payload FROM events"):
            return Rows((key, row['payload']) for key, row in self.events.items()
                if row['case_id'] == values['case_id'] and row['event'] == values['event'])
        if sql.startswith("SELECT b2_bucket"):
            row = self.documents.get(values['id'])
            return Rows([(row['bucket'], row['key'], row['sha'], row['size'], row.get('mime', 'application/pdf'))]
                if row and row['case_id'] == values['case_id'] and row['kind'] == values['kind'] else [])
        return super().execute(statement, values)


class CaseVersionsTests(unittest.TestCase):
    def setUp(self):
        self.case = str(uuid4())
        self.grant = draft.CaseDraftGrant(str(uuid4()))
        self.conn = CaseMemory({'id': self.case, 'test_mode': True, 'interested_data': {
            'full_name': 'INTERESADA SINTETICA', 'dni_nie': 'RTMTEST001',
            'local_test': {'synthetic': True, 'local_only': True}},
            'contact_name': 'CONTACTO DIFERENTE', 'contact_email': 'test@example.com',
            'department': 'claims', 'case_type': 'consumer', 'customer_comment': None})
        self.enterContext(patch.dict(os.environ, {'RTM_AUTHORITY_SIGNING_SECRET': 'synthetic-unit-draft-evidence-key-2026'}))
        self.enterContext(patch.object(draft, 'assert_local_document_storage_ready'))
        self.blobs = {}
        def upload(case, kind, data, ext, mime):
            coord = ('synthetic-bucket', f'cases/{case}/{kind}/{uuid4().hex}{ext}')
            self.blobs[coord] = data
            return coord
        self.upload = self.enterContext(patch.object(v.storage, 'upload_bytes', side_effect=upload))
        self.enterContext(patch.object(v.storage, 'download_bytes_limited',
            side_effect=lambda bucket, key, **kw: self.blobs[(bucket, key)]))
        self.initial = draft.load_case_document(self.conn, self.case, self.grant)

    def state(self):
        return v.list_versions(self.conn, case_id=self.case, grant=self.grant)

    def body(self, content='BORRADOR SINTÉTICO\nTexto de prueba.\n', base=None, **override):
        state = self.state()
        source = self.read(base)['snapshot'] if base else self.initial
        return v.SaveCaseDocumentBody.model_validate({
            'operation': 'case_draft', 'expected_source_sha256': source['source_sha256'],
            'expected_latest_id': state['latest_id'], 'expected_selection_revision': state['selection_revision'],
            'based_on_id': base, 'content': content, 'used_document_ids': source['used_document_ids'],
            'change_reason': 'Prueba sintética documentada', 'draft_acknowledged': True, **override})

    def save(self, body=None, correction=None):
        return v.save_case_version(self.conn, case_id=self.case, grant=self.grant,
            body=body or self.body(), uploaded=[], correction=correction)

    def read(self, version):
        return v.read_version(self.conn, case_id=self.case, version_id=version, grant=self.grant)

    def correction(self, version, content='CORRECCIÓN SINTÉTICA\r\nContenido exacto.  \r\n'):
        raw = f'RTM-CASE: {self.case}\r\nRTM-VERSION: {version}\r\n\r\n{content}'.encode()
        result, meta = validate_draft_correction(raw, filename='correccion.txt', declared_mime='text/plain',
            case_id=self.case, based_on_id=version)
        return self.body(content=result, base=version), (raw, meta)

    def test_roles_missing_and_unavailable_are_distinct_and_not_inferred(self):
        roles = self.initial['sources']['roles']
        self.assertNotEqual(roles['interested']['value']['full_name'], roles['contact']['value']['name'])
        self.assertIsNone(roles['interested']['value']['address'])
        self.assertIn('Domicilio: No consta', self.initial['content'])
        for name in ('driver', 'owner', 'representative'):
            self.assertEqual(roles[name]['status'], 'not_available')
            self.assertIsNone(roles[name]['value'])
        self.assertEqual(self.initial['sources']['document_reading'], 'not_performed')
        del self.conn.case['contact_email']
        with self.assertRaises(HTTPException) as caught:
            draft.load_case_document(self.conn, self.case, self.grant)
        self.assertIn('incompleta', caught.exception.detail)

    def test_invalid_stored_field_is_not_reported_as_confirmed_absence(self):
        self.conn.case['contact_email'] = 123
        with self.assertRaises(HTTPException) as caught:
            draft.load_case_document(self.conn, self.case, self.grant)
        self.assertIn('incompatibles', caught.exception.detail)

    def test_append_correction_recovery_exact_custody_and_no_overwrite(self):
        first = self.save()['saved_id']; original = self.read(first)
        body, correction = self.correction(first)
        second = self.save(body, correction)['saved_id']
        restored = self.read(second)
        self.assertEqual(self.read(first), original)
        self.assertEqual(restored['snapshot']['content'], body.content)
        self.assertEqual(restored['entry']['content_sha256'], hashlib.sha256(body.content.encode()).hexdigest())
        self.assertEqual(restored['entry']['correction']['sha256'], hashlib.sha256(correction[0]).hexdigest())
        self.assertEqual(restored['entry']['previous_id'], first)
        self.assertEqual(restored['entry']['based_on_id'], first)
        self.assertEqual(self.state()['selected_id'], second)
        self.assertTrue(restored['custody_verified'])
        self.assertFalse(restored['operator_export_allowed'])
        self.assertEqual(len(self.conn.documents), 3)
        self.assertFalse(any(sql.startswith(('UPDATE', 'DELETE')) for sql in self.conn.sql))

    def test_source_changes_cannot_silently_change_saved_or_derived_version(self):
        first = self.save()['saved_id']; original = self.read(first)
        self.conn.case['interested_data']['full_name'] = 'OTRA IDENTIDAD POSTERIOR'
        self.conn.sources[0]['sha256'] = 'c' * 64
        self.assertEqual(self.read(first), original)
        second = self.save(self.body(content='Corrección sobre fuentes anteriores', base=first))['saved_id']
        self.assertEqual(self.read(second)['snapshot']['sources'], original['snapshot']['sources'])
        with self.assertRaises(HTTPException): self.save(self.body())  # Old live source hash.

    def test_stale_writer_and_selection_aba_are_rejected_before_upload(self):
        stale = self.body(); first = self.save()['saved_id']
        count = self.upload.call_count
        with self.assertRaises(HTTPException) as caught: self.save(stale)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(count, self.upload.call_count)
        stale = self.body(base=first)
        state = self.state()
        v.select_version(self.conn, case_id=self.case, grant=self.grant, body=v.SelectCaseVersionBody(
            version_id=first, expected_latest_id=state['latest_id'], expected_selection_revision=state['selection_revision']))
        with self.assertRaises(HTTPException): self.save(stale)

    def test_view_does_not_select_and_selection_does_not_rewrite(self):
        first = self.save()['saved_id']
        second = self.save(self.body(content='Segunda versión sintética', base=first))['saved_id']
        state = self.state(); count = len(self.conn.events)
        self.read(first)
        self.assertEqual(self.state(), state); self.assertEqual(len(self.conn.events), count)
        v.select_version(self.conn, case_id=self.case, grant=self.grant, body=v.SelectCaseVersionBody(
            version_id=first, expected_latest_id=second, expected_selection_revision=state['selection_revision']))
        self.assertEqual(self.state()['selected_id'], first)
        self.assertEqual(self.state()['latest_id'], second)
        self.assertEqual(len(self.conn.documents), 2)

    def test_cross_case_version_reference_and_pdf_export_denied(self):
        first = self.save()['saved_id']
        with self.assertRaises(HTTPException): self.save(self.body(base=first, used_document_ids=[str(uuid4())]))
        with self.assertRaises(HTTPException): self.save(self.body(based_on_id=str(uuid4())))
        with self.assertRaises(HTTPException) as caught:
            v.read_pdf(self.conn, case_id=self.case, version_id=first, grant=self.grant)
        self.assertEqual(caught.exception.status_code, 403)
        other = str(uuid4()); self.conn.case['id'] = other
        with self.assertRaises(HTTPException) as caught:
            v.read_version(self.conn, case_id=other, version_id=first, grant=self.grant)
        self.assertEqual(caught.exception.status_code, 404)

    def test_snapshot_chain_pdf_and_import_tampering_fail_closed(self):
        first = self.save()['saved_id']
        body, correction = self.correction(first)
        second = self.save(body, correction)['saved_id']
        snapshot = self.conn.events[first]['payload']['snapshot']
        snapshot['content'] += ' altered'
        with self.assertRaises(HTTPException): self.read(first)
        raw_entry = self.read(second)['entry']
        doc = self.conn.documents[raw_entry['correction']['document_id']]
        self.blobs[(doc['bucket'], doc['key'])] += b'altered'
        with self.assertRaises(HTTPException): self.read(second)
        material = self.conn.events[second]['payload']['envelope']['material']
        material['based_on_id'] = str(uuid4())
        with self.assertRaises(HTTPException): self.state()

    def test_invalid_corrections_and_client_actor_are_rejected(self):
        first = self.save()['saved_id']; body, correction = self.correction(first)
        for raw, name, mime in [(correction[0].replace(self.case.encode(), str(uuid4()).encode()), 'a.txt', 'text/plain'),
                (b'\xff\xfe', 'a.txt', 'text/plain'), (correction[0], 'a.html', 'text/html'),
                (b'x'*(MAX_DRAFT_TEXT_BYTES+1), 'a.txt', 'text/plain'),
                (correction[0]+b'\x00', 'a.txt', 'text/plain')]:
            with self.assertRaises(UploadSecurityError):
                validate_draft_correction(raw, filename=name, declared_mime=mime, case_id=self.case, based_on_id=first)
        with self.assertRaises(ValidationError): self.body(created_by='operator:spoofed')
        with self.assertRaises(ValidationError): self.body(draft_acknowledged=False)
        with self.assertRaises(ValidationError): self.body(content='x\x00')

    def test_grant_requires_individual_supervisor_and_profile(self):
        scope = OpsCaseScope(self.grant.operator_id, 'rtm.supervisor', ('ops.view','ops.supervise'), True, True)
        self.assertEqual(draft.local_grant(scope), self.grant)
        with self.assertRaises(HTTPException): draft.local_grant(OpsCaseScope(scope.operator_id, scope.role_code, scope.permissions, True, False))
        with self.assertRaises(HTTPException): draft.local_grant(OpsCaseScope(scope.operator_id, 'rtm.operator', ('ops.view',), False, True))
        with patch.object(draft, 'assert_local_document_storage_ready', side_effect=RuntimeError('closed')):
            with self.assertRaises(HTTPException): draft.local_grant(scope)
        self.conn.case['test_mode'] = False
        with self.assertRaises(HTTPException): self.save()

    def test_route_auth_before_storage_and_existing_compensation(self):
        request = Request({'type':'http','headers':[]})
        with patch.object(routes, '_scope', side_effect=HTTPException(401, 'closed')), patch.object(routes, 'get_engine') as engine:
            with self.assertRaises(HTTPException): routes.post_version(self.case, self.body(), request, Response())
            engine.assert_not_called()
        with patch.object(routes, '_scope', return_value=(object(), self.grant)), \
                patch.object(routes, 'get_engine') as engine, patch.object(routes, 'require_case_in_scope', return_value=self.case), \
                patch.object(v, 'save_version', side_effect=RuntimeError('lost commit')), \
                patch('rtm_core.study_router._cleanup_uncommitted_documents') as cleanup:
            with self.assertRaises(RuntimeError): routes.post_version(self.case, self.body(), request, Response())
            cleanup.assert_called_once()


if __name__ == '__main__': unittest.main()
