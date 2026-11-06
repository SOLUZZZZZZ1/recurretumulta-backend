"""Linux-only, secret-free startup acceptance of the integrated application.

Uses fixture Render identities and a local CI database. This is not a remote
Render deployment nor external provider acceptance. Parser limits are real.
"""
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch


def main():
    if os.name != 'posix':
        raise RuntimeError('Linux startup verification requires POSIX; no skipped success')
    root=Path(__file__).resolve().parents[1]
    sys.path[:0]=[str(root),str(root/'tests')]
    from tests.test_existing_staging_policy import environment
    if os.environ.get('RTM_CI_LOCAL_POSTGRES')!='1':
        raise RuntimeError('Explicit local fixture database required')
    from sqlalchemy.engine import URL
    local_db=URL.create('postgresql+psycopg',username='postgres',password='synthetic-fixture-only',
        host='127.0.0.1',port=5432,database='rtm_staging_fixture').render_as_string(hide_password=False)
    profile=environment()
    profile.update(OPENAI_API_KEY='synthetic-no-provider',AWS_EC2_METADATA_DISABLED='true',
                   RTM_EXPECTED_BRANCH='rtm-staging-integrated-20261105',
                   RENDER_GIT_BRANCH='rtm-staging-integrated-20261105')
    original_connect=socket.socket.connect
    def local_connect(sock,address):
        if isinstance(address,tuple) and address[0] not in {'localhost','127.0.0.1','::1'}:
            raise RuntimeError('External network forbidden in startup fixture')
        return original_connect(sock,address)
    keep={k:os.environ[k] for k in ('PATH','LANG','TMPDIR') if k in os.environ}
    with patch.dict(os.environ,{**keep,**profile},clear=True),patch.object(socket.socket,'connect',local_connect):
        import database
        with patch.object(database,'get_database_url',return_value=local_db):
            from app import app
            from fastapi.testclient import TestClient
            with TestClient(app,base_url='https://recurretumulta-backend-1.onrender.com') as client:
                # Entering the context executes unmodified environment,
                # document limits and POSIX parser readiness startup hooks.
                ready=client.get('/health/ready')
                assert ready.status_code==200,ready.status_code
                status=client.get('/ops/auth/status')
                assert status.status_code==200,status.status_code
                assert status.json()['configuration_valid'] is True
                assert client.get('/docs').status_code==404
                assert client.post('/cases/intake-draft',content=b'{}').status_code==409
                assert client.get('/health/live',headers={'Host':'untrusted.example'}).status_code==400
            database.get_engine().dispose()
    print(json.dumps({'passed':True,'startup_hooks':'unmodified','parser_os_limits':'real POSIX',
        'readiness':'HTTP 200 with local PostgreSQL','docs_disabled':True,
        'synthetic_input_guard':True,'host_guard':True,'external_network':'blocked',
        'scope':'Linux CI + synthetic identity/local database; NOT remote Render/provider acceptance'}))


if __name__=='__main__':main()
