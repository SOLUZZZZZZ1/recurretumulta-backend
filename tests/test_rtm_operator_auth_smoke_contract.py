from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "rtm_operator_auth_smoke.py"


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class OperatorAuthSmokeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SMOKE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(SMOKE))

    def test_synthetic_device_is_inserted_before_sessions(self):
        self.assertIn("INSERT INTO rtm_operator_devices", self.source)
        self.assertIn(":device_key_sha256, 'known'", self.source)
        self.assertIn('"metadata": json.dumps({"synthetic": True})', self.source)
        self.assertIn('"device_key_sha256": device_digest', self.source)
        self.assertNotIn('"device_key_sha256": device_secret', self.source)
        self.assertIn('report["checks"]["device_inserted"] = True', self.source)
        self.assertIn('"device_stores_sha256_only"', self.source)

        insert_line = next(
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "INSERT INTO rtm_operator_devices" in node.value
        )
        session_lines = [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and _call_name(node) == "create_operator_session"
        ]
        self.assertEqual(len(session_lines), 2)
        self.assertLess(insert_line, min(session_lines))

    def test_every_synthetic_session_is_device_bound(self):
        session_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and _call_name(node) == "create_operator_session"
        ]
        self.assertEqual(len(session_calls), 2)
        for call in session_calls:
            with self.subTest(line=call.lineno):
                device_keywords = [
                    keyword
                    for keyword in call.keywords
                    if keyword.arg == "device_id"
                ]
                self.assertEqual(len(device_keywords), 1)
                self.assertIsInstance(device_keywords[0].value, ast.Name)
                self.assertEqual(device_keywords[0].value.id, "device_id")

    def test_epoch_check_starts_from_a_loadable_second_session(self):
        calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
        ]
        second_session_create = next(
            node
            for node in calls
            if _call_name(node) == "create_operator_session"
            and any(
                keyword.arg == "raw_token"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "second_token"
                for keyword in node.keywords
            )
        )
        epoch_increment = next(
            node
            for node in calls
            if _call_name(node) == "increment_operator_auth_epoch"
        )
        second_token_loads = [
            node
            for node in calls
            if _call_name(node) == "load_active_operator_session"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == "second_token"
        ]

        self.assertTrue(
            any(
                second_session_create.lineno < node.lineno < epoch_increment.lineno
                for node in second_token_loads
            )
        )
        self.assertTrue(
            any(node.lineno > epoch_increment.lineno for node in second_token_loads)
        )
        self.assertIn('"second_session_active_before_epoch_change"', self.source)
        self.assertIn("session.device_id == device_id", self.source)
        self.assertIn("second_session.device_id == device_id", self.source)
        self.assertIn('"rtm_operator_auth_smoke_v1_1"', self.source)


if __name__ == "__main__":
    unittest.main()
