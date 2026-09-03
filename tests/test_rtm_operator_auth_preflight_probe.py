from __future__ import annotations

import unittest

from scripts.rtm_operator_auth_routes_preflight import (
    _execute_case_scope_probe,
)


class _Context:
    def __init__(self, value):
        self.value = value
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self.value

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self.exited = True


class _Result:
    def __init__(self):
        self.fetched = False

    def fetchone(self):
        self.fetched = True
        return None


class _Connection:
    def __init__(self):
        self.transaction = _Context(None)
        self.calls = []

    def begin(self):
        return self.transaction

    def execute(self, statement, parameters=None):
        result = _Result()
        self.calls.append((str(statement), dict(parameters or {}), result))
        return result


class _Engine:
    def __init__(self):
        self.connection = _Connection()
        self.context = _Context(self.connection)

    def connect(self):
        return self.context


class OperatorAuthPreflightScopeProbeTest(unittest.TestCase):
    def test_probe_executes_real_filter_inside_read_only_transaction(self):
        engine = _Engine()
        scope_sql = "(:rtm_ops_scope_all = TRUE OR FALSE)"

        _execute_case_scope_probe(engine, lambda value: value, scope_sql)

        self.assertTrue(engine.context.entered)
        self.assertTrue(engine.context.exited)
        self.assertTrue(engine.connection.transaction.entered)
        self.assertTrue(engine.connection.transaction.exited)
        self.assertEqual(len(engine.connection.calls), 2)
        self.assertEqual(
            engine.connection.calls[0][0],
            "SET TRANSACTION READ ONLY",
        )
        query, parameters, result = engine.connection.calls[1]
        self.assertIn(scope_sql, query)
        self.assertIn("FROM cases c", query)
        self.assertFalse(parameters["rtm_ops_scope_all"])
        self.assertTrue(result.fetched)

    def test_probe_propagates_sql_failure_to_preflight_blocker_path(self):
        class BrokenConnection(_Connection):
            def execute(self, statement, parameters=None):
                if "SELECT c.id" in str(statement):
                    raise RuntimeError("scope SQL incompatible")
                return super().execute(statement, parameters)

        engine = _Engine()
        engine.connection = BrokenConnection()
        engine.context = _Context(engine.connection)

        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            _execute_case_scope_probe(
                engine,
                lambda value: value,
                "(:rtm_ops_scope_all = TRUE OR FALSE)",
            )


if __name__ == "__main__":
    unittest.main()
