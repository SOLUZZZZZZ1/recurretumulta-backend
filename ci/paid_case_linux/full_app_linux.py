"""Full candidate app.py, real HTTP and disposable PG18, offline Linux only.

Not a repetition of logout regression, B2 integration, or production acceptance.
No router substitution, monkeypatch, inherited credentials or business fixtures.
"""
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from shutdown_contract import evaluate_shutdown

ROOT = Path('/opt/rtm/source')
OUT = Path('/results')
STATE = Path('/state')
PG = Path('/usr/lib/postgresql/18/bin')
DATA = STATE / 'pgdata'
record = {'candidate': '976a810c523a98c86abbc26a3c4df7eb6866b4dd',
          'scope': 'Linux installation and full app.py startup; empty disposable PostgreSQL and synthetic renderer input',
          'checks': [], 'passed': False, 'modes': {}}
secret_values = []
app_process = None
pg_started_identity = None
phase = 'prerequisites'

def safe(value):
    text = str(value)
    for secret in sorted(secret_values, key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[REDACTED_EPHEMERAL]')
    return text

def check(name, condition=True):
    if not condition:
        raise AssertionError(name)
    record['checks'].append(name)

def port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]

def process_start_ticks(pid):
    return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19]

def get(http_port, path):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{http_port}{path}', timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)

def pg_control(operation):
    with (STATE / f'pg_ctl_{operation}.stdout').open('wb') as stdout, (STATE / f'pg_ctl_{operation}.stderr').open('wb') as stderr:
        args = [str(PG / 'pg_ctl'), '-D', str(DATA), '-w', '-t', '30']
        args += ['-l', str(STATE / 'postgres.log'), 'start'] if operation == 'start' else ['-m', 'fast', 'stop']
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=40)
        return result.returncode

def interrupted(signum, frame):
    raise RuntimeError('Interrupted; cleanup required')

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
try:
    OUT.mkdir(exist_ok=True)
    STATE.mkdir(exist_ok=True)
    check('Linux x86_64 Python 3.13.4', sys.platform == 'linux' and platform.machine() == 'x86_64' and sys.version_info[:3] == (3, 13, 4))
    check('Non-root runtime', os.getuid() == 10001)
    check('Offline namespace: loopback is the only interface', sorted(os.listdir('/sys/class/net')) == ['lo'])
    record.update(python=sys.version, os_release=Path('/etc/os-release').read_text(), platform=platform.platform(), libc=platform.libc_ver())
    record['packages'] = {d.metadata['Name']: d.version for d in metadata.distributions()}
    record['system_packages'] = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines()
    install_report = json.loads(Path('/opt/rtm/install-report.json').read_text())
    record['installed_wheels'] = [{'name': x['metadata']['name'], 'version': x['metadata']['version'],
                                  'sha256': x.get('download_info', {}).get('archive_info', {}).get('hashes', {}).get('sha256')}
                                 for x in install_report['install']]
    checked = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True)
    record['pip_check'] = {'exit': checked.returncode, 'stdout': checked.stdout, 'stderr': checked.stderr}
    check('Installed dependency graph is consistent', checked.returncode == 0)
    import psycopg
    import psycopg.pq
    import argon2
    import pypdfium2
    import PIL
    import uvloop
    import httptools
    import boto3
    import stripe
    import openai
    record['libpq_version'] = psycopg.pq.version()
    check('Linux native dependencies import')
    generated = secrets.token_urlsafe(40)
    secret_values.append(generated)
    hasher = argon2.PasswordHasher()
    check('Argon2 native operation', hasher.verify(hasher.hash(generated), generated))
    phase = 'disposable_postgresql'
    # Password passed via anonymous memory descriptor, never an argument or file.
    descriptor = os.memfd_create('isolated-initdb')
    try:
        os.write(descriptor, (generated + '\n').encode())
        os.lseek(descriptor, 0, 0)
        with (STATE / 'initdb.log').open('wb') as log:
            result = subprocess.run([str(PG / 'initdb'), '-D', str(DATA), '-U', 'isolated_admin',
                                     '--auth-local=scram-sha-256', '--auth-host=scram-sha-256',
                                     '--pwfile=/proc/self/fd/' + str(descriptor)],
                                    pass_fds=(descriptor,), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=40)
        check('Disposable initdb with SCRAM', result.returncode == 0)
    finally:
        os.close(descriptor)
    pg_port = port()
    with (DATA / 'postgresql.conf').open('a') as config:
        config.write(f"\nlisten_addresses='127.0.0.1'\nport={pg_port}\nunix_socket_directories='/state'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
    code = pg_control('start')
    # Cleanup below inspects postmaster.pid even if this call failed/timed out.
    check('PG18 starts', code == 0)
    pid_lines = (DATA / 'postmaster.pid').read_text().splitlines()
    pid = int(pid_lines[0])
    pg_started_identity = (pid, process_start_ticks(pid))
    check('Postmaster data and port match', pid_lines[1] == str(DATA) and int(pid_lines[3]) == pg_port)
    with psycopg.connect(host='127.0.0.1', port=pg_port, user='isolated_admin', password=generated, dbname='postgres', autocommit=True) as conn:
        conn.execute('CREATE DATABASE isolated_app')
        version = conn.execute('SHOW server_version').fetchone()[0]
        record['postgres_version'] = version
        check('PostgreSQL 18 matches production major version', version.startswith('18.'))
    phase = 'candidate_schema_on_disposable_pg18'
    migration = ROOT / 'migrations/20261024_paid_casework_candidate.sql'
    record['migration_sha256'] = hashlib.sha256(migration.read_bytes()).hexdigest()
    expected_tables = {'rtm_operator_roles','rtm_operators','rtm_operator_sessions',
                       'rtm_operator_devices','rtm_operator_access_events',
                       'rtm_operator_access_evidence','rtm_study_assessments'}
    with psycopg.connect(host='127.0.0.1',port=pg_port,user='isolated_admin',password=generated,dbname='isolated_app',autocommit=True) as conn:
        # Existing-case fixture provides only the FK target. No personal data,
        # accounts, payment or document rows are created by this schema check.
        conn.execute('CREATE TABLE cases(id uuid PRIMARY KEY)')
        conn.execute(migration.read_text(),prepare=False)
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        check('Exact unchanged migration creates seven additive tables on PG18',tables==expected_tables|{'cases'})
        check('Schema-only trial creates no operators or assessments',
              conn.execute('SELECT (SELECT count(*) FROM rtm_operators),(SELECT count(*) FROM rtm_study_assessments)').fetchone()==(0,0))
        try:
            conn.execute(migration.read_text(),prepare=False)
        except psycopg.errors.RaiseException:
            conn.execute('ROLLBACK')
            check('Existing-schema guard refuses repeat without changing objects',
                  {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}==tables)
        else:
            raise AssertionError('Repeated migration unexpectedly accepted')
    phase = 'native_document_renderer'
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for label in ('SYNTHETIC DOCUMENT - NO VALIDITY', 'SYNTHETIC SECOND PAGE'):
        pdf.drawString(40, 700, label)
        pdf.showPage()
    pdf.save()
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'rtm_core/document_render_worker.py'), 'page', 'application/pdf', '2', '1400'], input=buffer.getvalue(), capture_output=True, timeout=20)
    record['renderer'] = {'exit': result.returncode, 'stderr': safe(result.stderr.decode(errors='replace'))}
    check('Candidate renderer renders page 2 with Linux resource limits', result.returncode == 0 and result.stdout.startswith(b'\x89PNG\r\n\x1a\n'))
    record['renderer']['png_sha256'] = hashlib.sha256(result.stdout).hexdigest()
    for mode in ('disabled', 'limited-profile'):
        phase = 'full_app_' + mode
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/home/runner', 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
               'DATABASE_URL': f'postgresql+psycopg://isolated_admin:{generated}@127.0.0.1:{pg_port}/isolated_app',
               'RTM_ENV': 'production', 'RTM_ENABLE_PRODUCTION_CASEWORK': '0',
               'RTM_ENABLE_CASE_MAIL': '0', 'RTM_CASE_MAIL_WORKER_ENABLED': '0',
               'AWS_EC2_METADATA_DISABLED': 'true', 'ALLOWED_ORIGINS': 'https://www.recurretumulta.eu',
               'OPENAI_API_KEY': 'isolated-placeholder-not-a-credential', 'OPENAI_BASE_URL': 'http://127.0.0.1:9/v1'}
        # Legacy eager OpenAI client constructor needs a nonempty string, but no
        # endpoint is called. The namespace has no external network regardless.
        if mode == 'limited-profile':
            hmac_key = secrets.token_urlsafe(40)
            secret_values.append(hmac_key)
            env.update(RTM_ENABLE_PRODUCTION_CASEWORK='1', RENDER_SERVICE_NAME='recurretumulta-backend',
                       RTM_CASEWORK_PUBLIC_ORIGIN='https://www.recurretumulta.eu',
                       RTM_CASEWORK_ALLOWED_CASE_IDS='11111111-1111-4111-8111-111111111111',
                       RTM_CASEWORK_ALLOWED_OPERATOR_IDS='22222222-2222-4222-8222-222222222222',
                       RTM_CASEWORK_EXPIRES_AT=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                       RTM_OPERATOR_ACCESS_HMAC_KEY=hmac_key)
        http_port = port()
        with (STATE / (mode + '.stdout')).open('wb') as stdout, (STATE / (mode + '.stderr')).open('wb') as stderr:
            app_process = subprocess.Popen([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(http_port), '--no-access-log'], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            deadline = time.monotonic() + 90
            schema = None
            while time.monotonic() < deadline and app_process.poll() is None:
                try:
                    status, schema = get(http_port, '/openapi.json')
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(.25)
            check('Actual uvicorn app:app starts: ' + mode, schema is not None and status == 200)
            required = ['/health', '/ops/auth/login', '/ops/auth/device-logout', '/ops/core/cases/{case_id}/work', '/ops/core/cases/{case_id}/study', '/ops/core/cases/{case_id}/documents/{document_id}/view']
            check('Legacy and new routes coexist: ' + mode, all(p in schema['paths'] for p in required))
            status, health = get(http_port, '/health')
            check('Actual app queries disposable PG: ' + mode, status == 200 and health.get('ok') is True)
            status, auth = get(http_port, '/ops/auth/status')
            record['modes'][mode] = {'paths': len(schema['paths']), 'health': health, 'auth_status': {'http': status, 'body': auth}}
            if mode == 'limited-profile':
                check('Recoverable logout contract announced', status == 200 and auth.get('device_logout_contract') == 'rtm_device_logout_v1')
            # No login or case is fabricated; DB is intentionally empty.
            alive=app_process.poll() is None
            check('Uvicorn alive immediately before controlled SIGTERM: '+mode,alive)
            app_process.terminate()
            app_process.wait(timeout=20)
            with socket.socket() as probe:
                probe.settimeout(1)
                port_closed=probe.connect_ex(('127.0.0.1',http_port))!=0
            lifecycle=(STATE/(mode+'.stderr')).read_text(errors='replace')
            stopped=evaluate_shutdown(version=metadata.version('uvicorn'),started=True,alive_before=alive,
                requested_signal=signal.SIGTERM,returncode=app_process.returncode,port_closed=port_closed,
                log=lifecycle,pid=app_process.pid)
            record['modes'][mode]['shutdown']=stopped
            record['modes'][mode]['exit'] = app_process.returncode
            check('Full app stopped with orderly lifespan, signal and port evidence: ' + mode, stopped['passed'])
            app_process = None
    record['passed'] = True
except Exception as error:
    record['error'] = {'phase': phase, 'type': type(error).__name__, 'message': safe(error), 'traceback': safe(traceback.format_exc())}
finally:
    if app_process is not None and app_process.poll() is None:
        app_process.terminate()
        try:
            app_process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app_process.kill()
            app_process.wait(timeout=10)
    record['app_closed'] = app_process is None or app_process.poll() is not None
    try:
        pidfile = DATA / 'postmaster.pid'
        if pidfile.exists():
            lines = pidfile.read_text().splitlines()
            current_pid = int(lines[0])
            if lines[1] != str(DATA) or int(lines[3]) != pg_port:
                raise RuntimeError('Cleanup identity mismatch; no signal sent')
            executable = Path(f'/proc/{current_pid}/exe').resolve()
            command = Path(f'/proc/{current_pid}/cmdline').read_bytes().split(b'\0')
            if executable != PG / 'postgres' or str(DATA).encode() not in command:
                raise RuntimeError('Cleanup process mismatch; no signal sent')
            pg_started_identity = (current_pid, process_start_ticks(current_pid))
            record['pg_stop_exit'] = pg_control('stop')
        closed_process = pg_started_identity is None
        if pg_started_identity:
            pid, ticks = pg_started_identity
            # Docker --init reaps adopted postgres children. Observe the exact
            # process until it disappears; do not call a lingering zombie clean.
            deadline = time.monotonic() + 5
            while True:
                try:
                    same_process = process_start_ticks(pid) == ticks
                    proc_state = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0]
                except FileNotFoundError:
                    same_process, proc_state = False, 'absent'
                closed_process = not same_process
                record['pg_process_after_stop'] = {'pid':pid,'same_process':same_process,'state':proc_state}
                if closed_process or time.monotonic() >= deadline:
                    break
                time.sleep(.05)
        with socket.socket() as probe:
            probe.settimeout(1)
            closed_port = 'pg_port' not in globals() or probe.connect_ex(('127.0.0.1', pg_port)) != 0
        record['pg_pidfile_absent'] = not pidfile.exists()
        record['pg_exact_process_exited'] = closed_process
        record['pg_port_closed'] = closed_port
        record['pg_closed'] = record['pg_pidfile_absent'] and closed_process and closed_port
    except Exception as error:
        record['pg_closed'] = False
        record['cleanup_error'] = {'message': safe(error), 'traceback': safe(traceback.format_exc())}
    record['passed'] = record['passed'] and record['app_closed'] and record['pg_closed']
    record['at'] = datetime.now(timezone.utc).isoformat()
    # Only bounded diagnostic logs from this disposable fixture leave /state.
    for name in ('initdb.log', 'postgres.log', 'pg_ctl_start.stdout', 'pg_ctl_start.stderr', 'pg_ctl_stop.stdout', 'pg_ctl_stop.stderr', 'disabled.stdout', 'disabled.stderr', 'limited-profile.stdout', 'limited-profile.stderr'):
        path = STATE / name
        if path.exists():
            (OUT / name).write_text(safe(path.read_text(errors='replace')), encoding='utf-8')
    record['exit_code'] = 0 if record['passed'] else 1
    (OUT / 'RESULTADO.json').write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'passed': record['passed'], 'phase': phase, 'checks': len(record['checks']), 'exit': record['exit_code']}))
sys.exit(record['exit_code'])
