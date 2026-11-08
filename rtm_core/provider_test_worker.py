"""Separate TEST mail process. Initial deployment is idle until enabled."""
import os
import signal
import threading
from rtm_core.casework_policy import validate_test_boundary


def main():
    validate_test_boundary(os.environ,worker=True)
    if any(os.getenv(flag)=='1' for flag in ('RTM_CASE_MAIL_WORKER_ENABLED','RTM_MANAGEMENT_GENERATION_WORKER_ENABLED')):
        from rtm_core.management_worker import main as run_queues
        return run_queues()
    stop=threading.Event()
    for name in ('SIGINT','SIGTERM'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),lambda *_:stop.set())
    print('PROVIDER_TEST_MAIL_DISABLED',flush=True)
    stop.wait()


if __name__ == '__main__':main()
