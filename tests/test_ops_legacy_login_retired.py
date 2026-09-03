from __future__ import annotations

import inspect
import unittest

from fastapi import HTTPException

import ops


class LegacyOpsLoginRetiredTest(unittest.TestCase):
    def test_legacy_login_never_returns_global_token(self):
        with self.assertRaises(HTTPException) as caught:
            ops.ops_login("any-pin")
        self.assertEqual(caught.exception.status_code, 410)
        source = inspect.getsource(ops.ops_login)
        self.assertNotIn("OPERATOR_TOKEN", source)
        self.assertNotIn("OPERATOR_PIN", source)


if __name__ == "__main__":
    unittest.main()
