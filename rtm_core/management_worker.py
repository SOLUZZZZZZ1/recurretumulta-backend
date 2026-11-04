"""Run the durable generation and mail queues independently in one process."""
import os
import signal
import threading


def main():
    from database import get_engine
    from rtm_core.management_generation import tick as generate_tick
    from rtm_core.case_mail_runtime import tick as mail_tick
    engine=get_engine();stop=threading.Event()
    for name in ('SIGINT','SIGTERM'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),lambda *_:stop.set())
    try:
        while not stop.is_set():
            for flag, tick, code in (
                ('RTM_MANAGEMENT_GENERATION_WORKER_ENABLED',generate_tick,'GENERATION_CYCLE_INCOMPLETE'),
                ('RTM_CASE_MAIL_WORKER_ENABLED',mail_tick,'CASE_MAIL_CYCLE_INCOMPLETE')):
                if os.getenv(flag)!='1':continue
                try:tick(engine)
                except Exception:print(code,flush=True)
            stop.wait(15)
    finally:engine.dispose()


if __name__=='__main__':main()
