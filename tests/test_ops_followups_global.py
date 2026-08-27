from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import ops


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.statement = ""
        self.params = {}

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _Result(self.rows)


class _Begin:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        return False


class _Engine:
    def __init__(self, rows):
        self.connection = _Connection(rows)

    def begin(self):
        return _Begin(self.connection)


class GlobalFollowupsTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.row = (
            "followup-1",
            "case-1",
            "seguimiento_manual",
            "pending",
            "Comprobar respuesta administrativa",
            "Revisar si llegó contestación.",
            self.now - timedelta(days=1),
            None,
            None,
            self.now - timedelta(days=2),
            self.now - timedelta(days=1),
            "manual_review",
            "paid",
            "cliente@example.test",
            "Cliente sintético",
            "traffic",
            "fine",
            "traffic",
            "DGT",
            "EXP-SINT-1",
            {"matricula": "0000-TEST"},
            None,
        )

    def test_global_followups_include_case_context_and_urgency(self):
        engine = _Engine([self.row])
        with patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-test"}), patch(
            "ops.get_engine", return_value=engine
        ):
            result = ops.list_all_followups(
                x_operator_token="operator-test",
                status="all",
                limit=500,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["case_id"], "case-1")
        self.assertEqual(item["contact_name"], "Cliente sintético")
        self.assertEqual(item["expediente_ref"], "EXP-SINT-1")
        self.assertEqual(item["matricula"], "0000-TEST")
        self.assertTrue(item["overdue"])
        self.assertLess(item["days_left"], 0)
        self.assertIn("JOIN cases", engine.connection.statement)
        self.assertNotIn("followup_status", engine.connection.params)

    def test_status_filter_is_parameterized(self):
        engine = _Engine([self.row])
        with patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-test"}), patch(
            "ops.get_engine", return_value=engine
        ):
            ops.list_all_followups(
                x_operator_token="operator-test",
                status="pending",
                limit=100,
            )

        self.assertEqual(engine.connection.params["followup_status"], "pending")
        self.assertIn("f.status = :followup_status", engine.connection.statement)

    def test_invalid_status_is_rejected_before_database_access(self):
        with patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-test"}):
            with self.assertRaises(HTTPException) as ctx:
                ops.list_all_followups(
                    x_operator_token="operator-test",
                    status="unknown",
                    limit=100,
                )

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
