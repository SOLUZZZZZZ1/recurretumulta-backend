import io
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from rtm_core.document_viewer import render, read_custody, document_row

CASE = '00000000-0000-4000-8000-000000000001'
DOC = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'


def image_bytes():
    data = io.BytesIO()
    with Image.new('RGB', (160, 240), 'navy') as image:
        image.save(data, 'PNG')
    return data.getvalue()


class PaidCaseDocumentTests(unittest.TestCase):
    def test_retrieval_is_case_scoped(self):
        db = create_engine('sqlite://')
        try:
            with db.begin() as conn:
                conn.execute(text('CREATE TABLE documents(id TEXT,case_id TEXT,kind TEXT,mime TEXT,size_bytes INT,sha256 TEXT,b2_bucket TEXT,b2_key TEXT,created_at TEXT)'))
                conn.execute(text('INSERT INTO documents(id,case_id,mime) VALUES (:doc,:case,:mime)'), {'doc':DOC,'case':CASE,'mime':'image/png'})
                self.assertEqual(document_row(conn, CASE, DOC)['id'], DOC)
                with self.assertRaises(HTTPException) as failed:
                    document_row(conn, OTHER, DOC)
                self.assertEqual(failed.exception.status_code, 404)
        finally:
            db.dispose()

    def test_custody_hash_and_unknown_recorded_hash_are_distinct(self):
        data = image_bytes()
        record = {'case_id':CASE,'b2_bucket':'synthetic-test','b2_key':f'cases/{CASE}/original/sample.png',
                  'size_bytes':len(data),'sha256':None}
        body = io.BytesIO(data)
        storage = Mock();storage.get_object.return_value={'Body':body,'ContentLength':len(data)}
        with patch('b2_storage.get_b2_bucket',return_value='synthetic-test'), patch('b2_storage.get_s3_client',return_value=storage):
            actual, observed, state = read_custody(record)
            self.assertEqual(actual, data)
            self.assertEqual(len(observed), 64)
            self.assertEqual(state, 'not_recorded')
            self.assertTrue(body.closed)

    def test_cross_case_key_and_hash_mismatch_do_not_return_content(self):
        data = image_bytes()
        base = {'case_id':CASE,'b2_bucket':'synthetic-test','b2_key':f'cases/{CASE}/original/sample.png',
                'size_bytes':len(data),'sha256':None}
        for changes in [{'b2_key':f'cases/{OTHER}/original/sample.png'}, {'sha256':'0'*64}, {'size_bytes':len(data)+1}]:
            storage = Mock();storage.get_object.return_value={'Body':io.BytesIO(data),'ContentLength':len(data)}
            with patch('b2_storage.get_b2_bucket',return_value='synthetic-test'), patch('b2_storage.get_s3_client',return_value=storage), self.assertRaises(HTTPException) as failure:
                read_custody({**base, **changes})
            self.assertEqual(failure.exception.detail, {'code':'DOCUMENT_CUSTODY_UNVERIFIED'})

    def test_image_inspection_and_reencoded_pixels(self):
        data = image_bytes()
        self.assertEqual(render(data,'image/png'), {'pages':1,'width':160,'height':240})
        image = render(data,'image/png',page=1)
        with Image.open(io.BytesIO(image)) as decoded:
            self.assertEqual(decoded.size, (160,240))
            self.assertEqual(decoded.getpixel((0,0)), (0,0,128))

    def test_pdf_pages_are_rendered_distinctly(self):
        from reportlab.pdfgen.canvas import Canvas
        raw = io.BytesIO(); pdf = Canvas(raw, pagesize=(240,300))
        pdf.drawString(25,250,'SYNTHETIC PAGE ONE');pdf.showPage()
        pdf.drawString(25,250,'SYNTHETIC PAGE TWO');pdf.showPage();pdf.save()
        data = raw.getvalue()
        from rtm_core.document_render_worker import inspect_or_render
        self.assertEqual(inspect_or_render(data,'application/pdf')['pages'], 2)
        self.assertEqual(render(data,'application/pdf')['pages'], 2)
        first = render(data,'application/pdf',page=1,width=600)
        second = render(data,'application/pdf',page=2,width=600)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(b'\x89PNG'))
        with self.assertRaises(HTTPException):
            render(data,'application/pdf',page=3)

    def test_invalid_or_mislabeled_content_is_rejected(self):
        for data, mime in [(b'not a pdf','application/pdf'), (image_bytes(),'image/jpeg')]:
            with self.assertRaises(HTTPException) as failed:
                render(data,mime)
            self.assertEqual(failed.exception.detail, {'code':'DOCUMENT_RENDER_UNAVAILABLE'})


if __name__ == '__main__':
    unittest.main()
