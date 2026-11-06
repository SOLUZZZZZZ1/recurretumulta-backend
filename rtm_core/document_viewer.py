"""Bounded custody reads and isolated rendering for a case-scoped OPS viewer."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

MAX_BYTES = 12 * 1024 * 1024
MAX_OUTPUT = 18 * 1024 * 1024
RENDER_SLOT = threading.BoundedSemaphore(1)
SUPPORTED = {'application/pdf', 'image/jpeg', 'image/png', 'image/webp'}


def document_row(conn, case_id, document_id):
    try:
        if str(UUID(case_id)) != case_id or str(UUID(document_id)) != document_id:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, {'code': 'DOCUMENT_ID_INVALID'}) from None
    row = conn.execute(text('''SELECT id, case_id, kind, mime, size_bytes,
        sha256, b2_bucket, b2_key, created_at FROM documents
        WHERE case_id=:case_id AND id=:document_id'''),
        {'case_id': case_id, 'document_id': document_id}).mappings().one_or_none()
    if not row:
        raise HTTPException(404, {'code': 'DOCUMENT_NOT_FOUND_IN_CASE'})
    if row['mime'] not in SUPPORTED:
        raise HTTPException(415, {'code': 'DOCUMENT_VIEW_FORMAT_UNSUPPORTED'})
    return row


def read_custody(row):
    from b2_storage import get_b2_bucket, get_s3_client
    body = None
    try:
        bucket, key = row['b2_bucket'], row['b2_key']
        if bucket != get_b2_bucket() or not isinstance(key, str):
            raise ValueError()
        if not key.startswith('cases/' + str(row['case_id']) + '/') or any(
                part in {'.', '..', ''} for part in key.split('/')):
            raise ValueError()
        size = row['size_bytes']
        if type(size) is not int or not 0 < size <= MAX_BYTES:
            raise ValueError()
        response = get_s3_client().get_object(Bucket=bucket, Key=key)
        body = response['Body']
        if response.get('ContentLength') != size:
            raise ValueError()
        data = body.read(MAX_BYTES + 1)
        if len(data) != size:
            raise ValueError()
        observed = hashlib.sha256(data).hexdigest()
        recorded = row['sha256']
        if recorded is not None and (not isinstance(recorded, str)
                or not hmac.compare_digest(recorded.lower(), observed)):
            raise ValueError()
        return data, observed, 'verified' if recorded else 'not_recorded'
    except Exception:
        # Do not leak storage coordinates, signed URLs or provider error bodies.
        raise HTTPException(503, {'code': 'DOCUMENT_CUSTODY_UNVERIFIED'}) from None
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def render(data, mime, *, page=None, width=1400):
    if not 0 < len(data) <= MAX_BYTES or mime not in SUPPORTED:
        raise HTTPException(422, {'code': 'DOCUMENT_RENDER_INPUT_INVALID'})
    if type(width) is not int or not 400 <= width <= 2000 or (
            page is not None and (type(page) is not int or not 1 <= page <= 200)):
        raise HTTPException(422, {'code': 'DOCUMENT_PAGE_INVALID'})
    if not RENDER_SLOT.acquire(blocking=False):
        raise HTTPException(429, {'code': 'DOCUMENT_RENDER_BUSY'}, headers={'Retry-After': '2'})
    try:
        root = str(Path(__file__).resolve().parents[1])
        # Credentials stay in the API process. The renderer receives paths and
        # OS runtime settings only; bytes travel on private stdin, never arguments.
        env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LANG') if k in os.environ}
        env['PYTHONPATH'] = os.pathsep.join([root] + [p for p in sys.path if p and os.path.isdir(p)])
        args = [sys.executable, '-B', '-m', 'rtm_core.document_render_worker',
                'inspect' if page is None else 'page', mime]
        if page is not None:
            args += [str(page), str(width)]
        result = subprocess.run(args, input=data, capture_output=True, timeout=10,
                                cwd=root, env=env, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT:
            raise ValueError()
        if page is None:
            meta = json.loads(result.stdout)
            if set(meta) != {'pages', 'width', 'height'} or not 1 <= meta['pages'] <= 200:
                raise ValueError()
            return meta
        if not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError()
        return result.stdout
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise HTTPException(422, {'code': 'DOCUMENT_RENDER_UNAVAILABLE'}) from None
    finally:
        RENDER_SLOT.release()
