from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from submitter_dgt import DGTSubmitter


class LegacyDgtSubmitterRetiredTest(unittest.TestCase):
    def test_enabled_capability_cannot_execute_legacy_signer_or_transport(self):
        submitter = DGTSubmitter()
        with patch("submitter_dgt.require_capability"):
            with self.assertRaises(NotImplementedError):
                submitter.submit("case", b"%PDF-synthetic")
        source = inspect.getsource(__import__("submitter_dgt"))
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests.", source)


if __name__ == "__main__":
    unittest.main()
