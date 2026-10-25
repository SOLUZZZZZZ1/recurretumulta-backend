import unittest
from shutdown_contract import evaluate_shutdown

class ShutdownTest(unittest.TestCase):
    def evidence(self,**changes):
        args=dict(version='0.30.6',started=True,alive_before=True,requested_signal=15,
                  returncode=-15,port_closed=True,log='Application shutdown complete.\nFinished server process [123]',pid=123)
        return evaluate_shutdown(**{**args,**changes})
    def test_controlled_posix_signal_with_all_evidence(self):self.assertTrue(self.evidence()['passed'])
    def test_controlled_zero_with_all_evidence(self):self.assertTrue(self.evidence(returncode=0)['passed'])
    def test_failures_not_confused_with_orderly_signal(self):
        for change in [dict(returncode=-9),dict(returncode=-11),dict(returncode=1),dict(port_closed=False),
                       dict(started=False),dict(alive_before=False),dict(requested_signal=9),dict(timed_out=True),
                       dict(forced=True),dict(log='Finished server process [123]'),dict(pid=321),dict(version='0.48.0')]:
            with self.subTest(change=change):self.assertFalse(self.evidence(**change)['passed'])

if __name__=='__main__':unittest.main()
