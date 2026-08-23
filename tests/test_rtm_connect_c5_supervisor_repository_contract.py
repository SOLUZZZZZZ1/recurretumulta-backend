from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT / "rtm_connect" / "supervisor_repository.py"


class _Result:
    _COUNTS = {
        "total": 0,
        "active": 0,
        "synthetic": 0,
        "real": 0,
        "open": 0,
        "overdue": 0,
        "unassigned": 0,
        "received": 0,
        "verified": 0,
        "matched": 0,
        "dead_lettered": 0,
        "resolved": 0,
    }

    def mappings(self):
        return self

    def all(self):
        return []

    def one(self):
        return dict(self._COUNTS)

    def first(self):
        return None

    def scalar_one(self):
        return 0


class _RecordingConnection:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        return _Result()


class _FirstRowResult(_Result):
    def first(self):
        return {"id": "00000000-0000-0000-0000-000000000001"}


class _DetailRecordingConnection(_RecordingConnection):
    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        if len(self.calls) == 1:
            return _FirstRowResult()
        return _Result()


def _load_repository_module():
    previous = sys.modules.get("sqlalchemy")
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.text = lambda statement: statement
    sys.modules["sqlalchemy"] = sqlalchemy
    module_name = "_rtm_connect_c5_supervisor_repository_under_test"
    spec = importlib.util.spec_from_file_location(module_name, REPOSITORY)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar supervisor_repository.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("sqlalchemy", None)
        else:
            sys.modules["sqlalchemy"] = previous
    return module


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


class ConnectC5SupervisorRepositoryContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = _load_repository_module()
        cls.source = REPOSITORY.read_text(encoding="utf-8")

    def test_repository_imports_canonical_state_constants(self):
        self.assertIn(
            "from rtm_connect.schema import ACTION_STATUSES, RISK_CLASSES",
            self.source,
        )
        self.assertIn(
            "from rtm_connect.manual_schema import MANUAL_TASK_STATUSES",
            self.source,
        )

    def test_repository_contains_no_connect_mutation_sql(self):
        for forbidden in (
            r"\bINSERT\s+INTO\s+RTM_CONNECT(?:_|\b)",
            r"\bUPDATE\s+RTM_CONNECT(?:_|\b)",
            r"\bDELETE\s+FROM\s+RTM_CONNECT(?:_|\b)",
            r"\bTRUNCATE\b",
            r"\bDROP\s+TABLE\b",
            r"\bFOR\s+UPDATE\b",
            r"\bSELECT\s+\*",
        ):
            self.assertIsNone(
                re.search(forbidden, self.source, flags=re.IGNORECASE),
                forbidden,
            )

    def test_executed_repository_statements_are_select_only(self):
        conn = _RecordingConnection()
        self.repository.assert_synthetic_supervisor_scope(conn)
        self.repository.current_operator_can_supervise(
            conn,
            "00000000-0000-0000-0000-000000000001",
        )
        self.repository.count_actions(conn)
        self.repository.list_action_summaries(conn)
        self.repository.overview_snapshot(conn)
        self.repository.list_attention_items(conn)
        self.repository.count_manual_tasks(conn)
        self.repository.list_manual_task_summaries(conn)
        self.repository.count_dead_letters(conn)
        self.repository.list_dead_letter_summaries(conn)
        self.repository.get_action_supervisor_detail(
            conn,
            "00000000-0000-0000-0000-000000000001",
        )
        self.assertTrue(conn.calls)
        for statement, _ in conn.calls:
            self.assertRegex(statement.lstrip().upper(), r"^(SELECT|WITH)\b")

    def test_operator_json_predicates_are_bound_for_sqlalchemy(self):
        conn = _RecordingConnection()
        self.repository.current_operator_can_supervise(
            conn,
            "00000000-0000-0000-0000-000000000001",
        )
        statement, parameters = conn.calls[-1]
        sql = _normalized(statement)
        self.assertIn("cast(:synthetic_profile as jsonb)", sql)
        self.assertIn("cast( :supervisor_permissions as jsonb )", sql)
        self.assertNotIn(":true", statement)
        self.assertEqual(
            json.loads(str(parameters["synthetic_profile"])),
            {"synthetic": True, "environment": "staging"},
        )
        self.assertEqual(
            json.loads(str(parameters["supervisor_permissions"])),
            ["ops.supervise"],
        )

    def test_every_operational_query_enforces_connector_scope(self):
        conn = _RecordingConnection()
        self.repository.count_actions(conn)
        self.repository.list_action_summaries(conn)
        self.repository.overview_snapshot(conn)
        self.repository.list_attention_items(conn)
        self.repository.count_manual_tasks(conn)
        self.repository.list_manual_task_summaries(conn)
        self.repository.count_dead_letters(conn)
        self.repository.list_dead_letter_summaries(conn)
        self.repository.get_action_supervisor_detail(
            conn,
            "00000000-0000-0000-0000-000000000001",
        )
        operational_tables = (
            "rtm_connect_actions",
            "rtm_connect_manual_tasks",
            "rtm_connect_webhook_inbox",
            "rtm_connect_reconciliations",
        )
        checked = 0
        for statement, _ in conn.calls:
            sql = _normalized(statement)
            if not any(table in sql for table in operational_tables):
                continue
            checked += 1
            self.assertIn("rtm_connect_connectors", sql, statement)
            self.assertIn("environment", sql, statement)
            self.assertIn("synthetic_only", sql, statement)
            self.assertIn("credential_ref", sql, statement)
        self.assertGreaterEqual(checked, 8)

    def test_action_filters_are_bound_and_pagination_is_stable(self):
        conn = _RecordingConnection()
        case_id = "00000000-0000-0000-0000-000000000002"
        self.repository.list_action_summaries(
            conn,
            status="unknown",
            risk_class="R4_critical_regulated",
            capability="synthetic.capability",
            case_id=case_id,
            limit=25,
            offset=50,
        )
        statement, parameters = conn.calls[-1]
        sql = _normalized(statement)
        for placeholder in (
            ":status",
            ":risk_class",
            ":capability",
            ":case_id",
            ":limit",
            ":offset",
        ):
            self.assertIn(placeholder, sql)
        self.assertNotIn("synthetic.capability", statement)
        self.assertEqual(parameters["case_id"], case_id)
        self.assertEqual(parameters["limit"], 25)
        self.assertEqual(parameters["offset"], 50)
        self.assertIn("order by a.updated_at desc, a.id desc", sql)
        self.assertIn("limit :limit offset :offset", sql)

    def test_manual_filters_and_order_are_bounded(self):
        conn = _RecordingConnection()
        assignee = "00000000-0000-0000-0000-000000000003"
        self.repository.list_manual_task_summaries(
            conn,
            status="assigned",
            assignee_operator_id=assignee,
            overdue_only=True,
            limit=20,
            offset=40,
        )
        statement, parameters = conn.calls[-1]
        sql = _normalized(statement)
        self.assertIn(":status", sql)
        self.assertIn(":assignee_operator_id", sql)
        self.assertIn("due_at < now()", sql)
        self.assertIn("limit :limit offset :offset", sql)
        self.assertEqual(parameters["assignee_operator_id"], assignee)
        self.assertEqual(parameters["limit"], 20)
        self.assertEqual(parameters["offset"], 40)

    def test_action_detail_histories_are_bounded_and_report_truncation(self):
        self.assertIn("history_limit", self.source)
        self.assertIn("COUNT(*) OVER() AS collection_total", self.source)
        self.assertIn("LIMIT :history_limit", self.source)
        self.assertIn('"truncated": total > len(rows)', self.source)

    def test_child_ledgers_are_bound_to_the_same_action_and_attempt(self):
        for required in (
            "x.action_id <> e.action_id",
            "x.action_id <> t.action_id",
            "x.action_id <> mt.action_id",
            "x.connector_id <> mt.connector_id",
            "x.action_id <> w.matched_action_id",
            "x.action_id <> r.action_id",
            "w.matched_action_id <> r.action_id",
            "w.matched_attempt_id <> r.attempt_id",
        ):
            self.assertIn(required, self.source)

    def test_manual_scope_is_exact_and_relationally_coherent(self):
        sql = _normalized(self.repository._MANUAL_TASK_SCOPE_SQL)
        for required in (
            "manual_scope_connector.code='manual.handoff'",
            "manual_scope_connector.version='v1.0'",
            "manual_scope_connector.environment='staging'",
            "manual_scope_connector.synthetic_only=true",
            "manual_scope_connector.credential_ref is null",
            "manual_scope_attempt.id=mt.attempt_id",
            "manual_scope_attempt.action_id=a.id",
            "manual_scope_attempt.connector_id=mt.connector_id",
            "a.id=mt.action_id",
            "a.capability='administration.submit_document'",
            "a.satellite='administration'",
        ):
            self.assertIn(required, sql)

        conn = _RecordingConnection()
        self.repository.overview_snapshot(conn)
        self.repository.list_attention_items(conn)
        self.repository.list_manual_task_summaries(conn)
        manual_queries = [
            _normalized(statement)
            for statement, _ in conn.calls
            if "rtm_connect_manual_tasks" in statement
        ]
        self.assertGreaterEqual(len(manual_queries), 3)
        for statement in manual_queries:
            self.assertIn("manual.handoff", statement)
            self.assertIn("manual_scope_attempt.id=mt.attempt_id", statement)

    def test_webhook_scope_is_exact_and_rejects_partial_matches(self):
        sql = _normalized(self.repository._WEBHOOK_SCOPE_SQL)
        for required in (
            "webhook_scope_connector.code='synthetic.webhook'",
            "webhook_scope_connector.version='v1.0'",
            "webhook_scope_connector.environment='staging'",
            "webhook_scope_connector.synthetic_only=true",
            "webhook_scope_connector.credential_ref is null",
            "w.matched_action_id is null",
            "w.matched_attempt_id is null",
            "webhook_scope_attempt.id=w.matched_attempt_id",
            "webhook_scope_attempt.action_id=a.id",
            "a.id=w.matched_action_id",
        ):
            self.assertIn(required, sql)

        conn = _RecordingConnection()
        self.repository.overview_snapshot(conn)
        self.repository.list_attention_items(conn)
        self.repository.count_dead_letters(conn)
        self.repository.list_dead_letter_summaries(conn)
        webhook_queries = [
            _normalized(statement)
            for statement, _ in conn.calls
            if "rtm_connect_webhook_inbox" in statement
        ]
        self.assertGreaterEqual(len(webhook_queries), 4)
        for statement in webhook_queries:
            self.assertIn("synthetic.webhook", statement)
            self.assertIn(
                "webhook_scope_attempt.id=w.matched_attempt_id",
                statement,
            )

    def test_action_histories_keep_latest_n_then_render_ascending(self):
        conn = _DetailRecordingConnection()
        detail = self.repository.get_action_supervisor_detail(
            conn,
            "00000000-0000-0000-0000-000000000001",
            history_limit=2,
        )
        self.assertIsNotNone(detail)
        history_calls = conn.calls[2:]
        self.assertEqual(len(history_calls), 6)
        expected_orders = (
            ("x.attempt_number desc, x.id desc", "attempt_number asc, id asc"),
            ("e.sequence_number desc, e.id desc", "sequence_number asc, id asc"),
            ("t.sequence_number desc, t.id desc", "sequence_number asc, id asc"),
            ("mt.created_at desc, mt.id desc", "created_at asc, id asc"),
            (
                "r.reconciliation_number desc, r.id desc",
                "reconciliation_number asc, id asc",
            ),
            ("w.received_at desc, w.id desc", "received_at asc, id asc"),
        )
        for (statement, parameters), (descending, ascending) in zip(
            history_calls,
            expected_orders,
        ):
            sql = _normalized(statement)
            self.assertIn(descending, sql)
            self.assertIn("limit :history_limit", sql)
            self.assertIn(ascending, sql)
            self.assertLess(sql.index(descending), sql.index("limit :history_limit"))
            self.assertLess(sql.index("limit :history_limit"), sql.rindex(ascending))
            self.assertEqual(parameters["history_limit"], 2)

    def test_projection_omits_unbounded_and_untrusted_text(self):
        for forbidden in (
            "failure_class",
            "error_code",
            "verification_method",
            "reason_code",
            "resolution_code",
            "claimed_action_id",
            "claimed_attempt_id",
            "authority_version",
            "authorized_connector_modes",
            "approved_by_operator_ids",
        ):
            self.assertIsNone(
                re.search(rf"\b{re.escape(forbidden)}\b", self.source),
                forbidden,
            )

    def test_projection_does_not_select_raw_operational_material(self):
        for forbidden in (
            "target_ref",
            "document_hashes",
            "request_metadata",
            "result_metadata",
            "package_manifest",
            "instructions",
            "receipt_storage_ref",
            "reason_detail",
        ):
            self.assertIsNone(
                re.search(rf"\b{re.escape(forbidden)}\b", self.source),
                forbidden,
            )
        self.assertIsNone(
            re.search(r"\b(payload|metadata)\b\s*(,|as|from)", self.source)
        )
        self.assertIn("external_reference_present", self.source)

    def test_dead_letter_attention_does_not_relabel_claims_as_case_data(self):
        self.assertNotIn("claimed_action_id AS case_id", self.source)
        self.assertNotIn("event_type AS capability", self.source)
        self.assertIn("permanent_failed", self.source)


if __name__ == "__main__":
    unittest.main()
