from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from submitters.base import SubmitterNotReady
from submitters.registro import RegistroSubmitter


class RegistroSubmitterRetiredTest(unittest.TestCase):
    def test_enabled_capability_still_cannot_reach_configurable_destination(self):
        submitter = RegistroSubmitter()
        with patch("submitters.registro.require_capability"):
            with self.assertRaises(SubmitterNotReady):
                submitter.submit(case_id="case", pdf_bytes=b"%PDF-synthetic")
        source = inspect.getsource(RegistroSubmitter.submit)
        self.assertNotIn("REG_PROVIDER_URL", source)
        self.assertNotIn("REG_PROVIDER_TOKEN", source)
        self.assertNotIn("requests.", source)


if __name__ == "__main__":
    unittest.main()
