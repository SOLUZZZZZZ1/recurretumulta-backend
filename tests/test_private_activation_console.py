"""Console guard test: no terminal, no database, no environment credentials."""
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch
import unittest

class ActivationConsoleTest(unittest.TestCase):
    def test_unattended_invocation_never_connects_or_prompts(self):
        path=Path(__file__).resolve().parents[1]/'tools/activate_ops_private.py'
        spec=importlib.util.spec_from_file_location('activation_console_test_module',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module.sys,'argv',['activate_ops_private.py','--apply','not-read.json','not-read-id']),\
             patch.object(module.sys,'stdin',io.StringIO()),patch.object(module.sys,'stdout',io.StringIO()) as output,\
             patch.object(module,'create_engine') as connect,patch.object(module.getpass,'getpass') as password:
            self.assertEqual(module.main(),1)
            connect.assert_not_called();password.assert_not_called()
            self.assertIn('ACTIVATION_ATTENDED_CONSOLE_REQUIRED',output.getvalue())
