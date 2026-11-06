"""Optional, managed queue child of the exact existing staging web service.

No new Render resource, no startup DDL, and disabled by default. The published
startup checks run first. The child has the same TEST identity and narrow case
scope; queue validation still runs before every provider operation.
"""
import os
from pathlib import Path
import subprocess
import sys
import threading

from rtm_core.casework_policy import CaseworkConfigurationError, validate_test_boundary

_lock = threading.Lock()
_process = None
_FLAGS = ('RTM_CASE_MAIL_WORKER_ENABLED', 'RTM_MANAGEMENT_GENERATION_WORKER_ENABLED')


def requested(source=None):
    source = os.environ if source is None else source
    return source.get('RTM_PROVIDER_TEST_WORKER_MODE') == 'embedded' and any(
        source.get(flag) == '1' for flag in _FLAGS)


def child_environment(source):
    if source.get('RTM_PROVIDER_TEST_TARGET') != 'existing_staging' or not requested(source):
        raise CaseworkConfigurationError('Embedded queues require explicit existing staging activation')
    if source.get('RTM_PROVIDER_TEST_EMBEDDED_CHILD'):
        raise CaseworkConfigurationError('Embedded queue recursion forbidden')
    child = dict(source)
    child['RTM_PROVIDER_TEST_EMBEDDED_CHILD'] = '1'
    validate_test_boundary(child, worker=True)
    if child.get('RTM_CASE_MAIL_WORKER_ENABLED') == '1':
        from rtm_core.case_mail_runtime import configuration
        configuration(child)
    if child.get('RTM_MANAGEMENT_GENERATION_WORKER_ENABLED') == '1':
        from rtm_core.management_policy import configuration
        configuration(child, for_worker=True)
    return child


def start():
    global _process
    if not requested():
        return
    child = child_environment(os.environ)
    with _lock:
        if _process is not None:
            raise RuntimeError('Embedded queues already started')
        _process = subprocess.Popen(
            [sys.executable, '-B', '-m', 'rtm_core.provider_test_worker'],
            cwd=str(Path(__file__).resolve().parents[1]), env=child,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )


def healthy():
    if not requested():
        return True
    with _lock:
        return _process is not None and _process.poll() is None


def stop():
    global _process
    with _lock:
        child, _process = _process, None
    if child is None or child.poll() is not None:
        return
    # Termination targets only the Popen child owned by this application.
    child.terminate()
    try:
        child.wait(timeout=12)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)
