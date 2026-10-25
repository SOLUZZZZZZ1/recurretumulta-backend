"""Dedicated server worker candidate. Never launched by web imports or a browser."""
import signal
import threading
from rtm_core.case_mail_runtime import configuration,tick

def main():
    configuration()  # Fail before database/storage/network access when disabled.
    from database import get_engine
    engine=get_engine();stop=threading.Event()
    for name in ('SIGINT','SIGTERM'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),lambda *_:stop.set())
    try:
        while not stop.is_set():
            try:tick(engine)
            except Exception:
                # Synchronization details are persisted by the adapter, never raw
                # credentials/provider responses or message bodies in process logs.
                print('CASE_MAIL_CYCLE_INCOMPLETE',flush=True)
            stop.wait(60)
    finally:engine.dispose()

if __name__=='__main__':main()
