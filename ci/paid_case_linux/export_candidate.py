"""Prepare a build context from exact Git blobs; never copy a working tree/env."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

CANDIDATE = '976a810c523a98c86abbc26a3c4df7eb6866b4dd'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEST = ROOT / 'ci-build-context'
if DEST.exists():
    raise SystemExit('Build context exists; use a fresh runner')
manifest = json.loads((HERE / 'SOURCE_LINUX.json').read_text())
assert manifest['commit'] == CANDIDATE
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
parent = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], cwd=ROOT, text=True).strip()
assert parent == CANDIDATE, 'Verification commit must be a direct child of the candidate'
changed = subprocess.check_output(['git', 'diff', '--name-only', CANDIDATE, head], cwd=ROOT, text=True).splitlines()
assert changed and all(p.startswith('ci/paid_case_linux/') or p == '.github/workflows/rtm-paid-case-linux.yml' for p in changed)
for item in manifest['files']:
    name = item['path']
    assert not Path(name).is_absolute() and '..' not in Path(name).parts
    raw = subprocess.check_output(['git', 'show', CANDIDATE + ':' + name], cwd=ROOT)
    assert hashlib.sha256(raw).hexdigest() == item['sha256_git_bytes'], name
    target = DEST / 'source' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
shutil.copytree(HERE, DEST / 'harness')
shutil.copyfile(HERE / 'Dockerfile', DEST / 'Dockerfile')
print(json.dumps({'candidate': CANDIDATE, 'verification_commit': head,
                  'verified_source_files': len(manifest['files']), 'scope': 'full app.py and all tracked runtime Python modules'}))
