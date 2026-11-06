import os
import unittest
from unittest.mock import Mock, patch
from rtm_core import embedded_test_worker as worker
from rtm_core.casework_policy import CaseworkConfigurationError, validate_test_boundary
from tests.test_existing_staging_policy import environment


class EmbeddedTestWorkerTest(unittest.TestCase):
    def test_default_never_starts_process(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(worker.subprocess, 'Popen') as popen:
            worker.start()
            self.assertTrue(worker.healthy())
            popen.assert_not_called()

    def test_explicit_exact_existing_identity_is_required(self):
        env = environment()
        for changes in ({}, {'RTM_PROVIDER_TEST_WORKER_MODE':'embedded'},
                        {'RTM_PROVIDER_TEST_EMBEDDED_CHILD':'1'},
                        {'RTM_PROVIDER_TEST_WORKER_MODE':'embedded','RTM_PROVIDER_TEST_EMBEDDED_CHILD':'1',
                         'RENDER_SERVICE_ID':'srv-d5fu32hr0fns73c3oa0g'}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(CaseworkConfigurationError):
                validate_test_boundary({**env, **changes}, worker=True)
        validate_test_boundary({**env, 'RTM_PROVIDER_TEST_WORKER_MODE':'embedded',
                                'RTM_PROVIDER_TEST_EMBEDDED_CHILD':'1'}, worker=True)

    def test_missing_mail_configuration_aborts_before_process(self):
        env = {**environment(), 'RTM_PROVIDER_TEST_WORKER_MODE':'embedded',
               'RTM_CASE_MAIL_WORKER_ENABLED':'1'}
        with patch.dict(os.environ, env, clear=True), patch.object(worker.subprocess, 'Popen') as popen:
            with self.assertRaises(ValueError):
                worker.start()
            popen.assert_not_called()

    def test_child_ownership_shutdown_and_readiness(self):
        env = {'RTM_PROVIDER_TEST_WORKER_MODE':'embedded','RTM_MANAGEMENT_GENERATION_WORKER_ENABLED':'1'}
        child = Mock()
        child.poll.return_value = None
        with patch.dict(os.environ, env, clear=True), patch.object(worker,'child_environment',return_value=env), \
             patch.object(worker.subprocess,'Popen',return_value=child) as popen:
            try:
                worker.start()
                self.assertTrue(worker.healthy())
                with self.assertRaises(RuntimeError):
                    worker.start()
                self.assertEqual(popen.call_count, 1)
                child.poll.return_value = 1
                self.assertFalse(worker.healthy())
                child.poll.return_value = None
            finally:
                worker.stop()
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=12)
            self.assertFalse(worker.healthy())
