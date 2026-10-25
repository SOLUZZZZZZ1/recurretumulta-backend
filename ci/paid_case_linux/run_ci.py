"""Only on a fresh approved Linux runner. No registry push or deployment."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'linux-results'
OUT.mkdir(exist_ok=False)
# New disposable output directory only, not Windows or existing host permissions.
OUT.chmod(0o777)
name = 'rtm-isolated-' + uuid.uuid4().hex
image = name + ':check'
state = {'passed': False, 'runtime_exit': None, 'container_closed': None,
         'candidate': '976a810c523a98c86abbc26a3c4df7eb6866b4dd',
         'build_network': 'public dependency repositories only', 'runtime_network': 'none'}
created = False

def run(args, stem, timeout):
    with (OUT / (stem + '.stdout.log')).open('wb') as stdout, (OUT / (stem + '.stderr.log')).open('wb') as stderr:
        result = subprocess.run(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=timeout)
        state[stem + '_exit'] = result.returncode
        if result.returncode:
            raise RuntimeError(stem + ' failed; inspect preserved logs')

try:
    if sys.platform != 'linux':
        raise RuntimeError('Linux runner required; do not run in Windows')
    state['runner_os'] = Path('/etc/os-release').read_text()
    run([sys.executable, 'ci/paid_case_linux/export_candidate.py'], 'export', 60)
    run(['docker', 'pull', 'python:3.13.4-slim-bookworm'], 'base_pull', 180)
    state['python_base_digests'] = json.loads(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', 'python:3.13.4-slim-bookworm'], text=True))
    if not state['python_base_digests']:
        raise RuntimeError('Python base digest unavailable')
    run(['docker', 'build', '--progress=plain', '--build-arg', 'PYTHON_IMAGE=' + state['python_base_digests'][0], '-t', image, 'ci-build-context'], 'build', 720)
    state['image_id'] = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    # Resolve the actual base digest used; apt and wheel versions are recorded too.
    run(['docker', 'create', '--name', name, '--network', 'none', '--read-only',
         '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=2g', '--cpus=2', '--pids-limit=256',
         '--tmpfs', '/state:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700',
         '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
         '--mount', 'type=bind,src=' + str(OUT) + ',dst=/results', image], 'create', 30)
    created = True
    run(['docker', 'start', '--attach', name], 'runtime', 300)
    state['runtime_exit'] = int(subprocess.check_output(['docker', 'inspect', '--format', '{{.State.ExitCode}}', name], text=True))
    result = json.loads((OUT / 'RESULTADO.json').read_text())
    if state['runtime_exit'] != 0 or not result['passed'] or not result['pg_closed'] or not result['app_closed']:
        raise RuntimeError('Full app check or cleanup failed')
    state['passed'] = True
except Exception as error:
    state['error'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
finally:
    # Container name is unique to this invocation; never stop a service by image/name pattern.
    probe = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}} {{.State.ExitCode}}', name], capture_output=True, text=True)
    if probe.returncode == 0:
        values = probe.stdout.split()
        state['runtime_exit'] = int(values[1])
        if values[0] == 'true':
            subprocess.run(['docker', 'stop', '--time', '50', name], capture_output=True, timeout=65)
        probe = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name], capture_output=True, text=True)
        state['container_closed'] = probe.returncode == 0 and probe.stdout.strip() == 'false'
        subprocess.run(['docker', 'rm', name], capture_output=True, timeout=30)
    else:
        state['container_closed'] = not created
    state['passed'] = state['passed'] and state['container_closed']
    state['exit_code'] = 0 if state['passed'] else 1
    (OUT / 'PROCESS.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(json.dumps({'passed': state['passed'], 'runtime_exit': state['runtime_exit'], 'container_closed': state['container_closed']}))
sys.exit(state['exit_code'])
