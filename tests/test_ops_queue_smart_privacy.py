from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ops_queue_smart as smart_queue


CASE_ID = "22222222-2222-4222-8222-222222222222"
OPERATOR_TOKEN = "server-only-operator-token"


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(
        self,
        interested_canary,
        ai_payload=None,
        *,
        case_type="fine",
        vehicle_consent=False,
    ):
        self.interested_canary = interested_canary
        self.case_type = case_type
        self.vehicle_consent = vehicle_consent
        self.ai_payload = ai_payload or {
            "tipo_infraccion": "traffic",
            "classifier_result": {
                "family": "traffic",
                "confidence": 0.95,
            },
        }
        self.case_sql = ""
        self.case_queries = 0

    def execute(self, statement, parameters=None):
        del parameters
        sql = " ".join(str(statement).split())
        normalized = sql.casefold()
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)

        if "from cases c" in normalized or "from cases where" in normalized:
            self.case_queries += 1
            self.case_sql = sql
            common = (
                CASE_ID,
                "generated",
                "paid",
                True,
                "operador@example.invalid",
                "EXP-SYNTHETIC-QUEUE-1",
                now + timedelta(days=5),
                now - timedelta(days=2),
                now,
            )
            row = (
                *common,
                self.interested_canary
                if "as interested_data" in normalized
                else now,
                self.case_type,
                self.vehicle_consent,
            )
            return _Result([row])

        if "from events" in normalized:
            return _Result(
                [
                    (
                        "ai_expediente_result",
                        self.ai_payload,
                        now,
                    )
                ]
            )

        if "from documents" in normalized:
            return _Result(
                [
                    ("doc-pdf", "generated_pdf", "application/pdf", 512, now),
                    (
                        "doc-docx",
                        "generated_docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        768,
                        now,
                    ),
                ]
            )

        raise AssertionError(f"SQL inesperado: {sql}")


class _PagingConnection:
    def __init__(self):
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        self.rows = []
        for number in range(101):
            case_id = f"00000000-0000-4000-8000-{101 - number:012d}"
            updated_at = now - timedelta(minutes=number)
            self.rows.append(
                (
                    case_id,
                    "ready_to_submit" if number == 100 else "generated",
                    "paid",
                    True,
                    f"operator-{number}@example.invalid",
                    f"EXP-SYNTHETIC-PAGE-{number:03d}",
                    now + timedelta(days=5),
                    now - timedelta(days=2),
                    updated_at,
                    updated_at,
                    "fine",
                    False,
                )
            )
        self.target_case_id = self.rows[-1][0]
        self.case_queries = []

    def execute(self, statement, parameters=None):
        parameters = dict(parameters or {})
        sql = " ".join(str(statement).split())
        normalized = sql.casefold()
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)

        if "from cases c" in normalized or "from cases where" in normalized:
            self.case_queries.append((sql, parameters))
            candidates = self.rows
            cursor_updated_at = parameters.get("cursor_updated_at")
            cursor_case_id = parameters.get("cursor_case_id")
            if cursor_updated_at is not None and cursor_case_id is not None:
                candidates = [
                    row
                    for row in candidates
                    if row[9] < cursor_updated_at
                    or (row[9] == cursor_updated_at and row[0] < cursor_case_id)
                ]
            return _Result(candidates[: int(parameters["limit"])])

        if "from events" in normalized:
            confidence = 0.95 if parameters["case_id"] == self.target_case_id else 0.20
            return _Result(
                [
                    (
                        "ai_expediente_result",
                        {
                            "tipo_infraccion": "traffic",
                            "classifier_result": {
                                "family": "traffic",
                                "confidence": confidence,
                            },
                        },
                        now,
                    )
                ]
            )

        if "from documents" in normalized:
            return _Result(
                [
                    ("doc-pdf", "generated_pdf", "application/pdf", 512, now),
                    (
                        "doc-docx",
                        "generated_docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        768,
                        now,
                    ),
                ]
            )

        raise AssertionError(f"SQL inesperado: {sql}")


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


class SmartQueuePrivacyTest(unittest.TestCase):
    def test_response_never_loads_or_returns_full_interested_data(self):
        interested_canary = {
            "dni_nie": "QUEUE-DNI-00000000T",
            "domicilio_notif": "QUEUE-DOMICILIO-CALLE-123",
            "b2_bucket": "queue-private-bucket",
            "b2_key": "cases/private/queue-canary.pdf",
            "nested": {
                "secret": "QUEUE-NESTED-SECRET",
                "storage_key": "queue/nested/private.bin",
            },
        }
        connection = _Connection(interested_canary)

        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=False,
            ),
            patch.object(
                smart_queue,
                "get_engine",
                return_value=_Engine(connection),
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=False,
                    individual_session=True,
                ),
            ),
            patch.object(
                smart_queue,
                "ops_case_scope_filter",
                return_value=("TRUE", {}),
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=100,
                only_action=None,
            )

        normalized_sql = connection.case_sql.casefold()
        self.assertNotIn("as interested_data", normalized_sql)
        self.assertIn(
            "vehicle_removal_preparation_consent",
            normalized_sql,
        )
        self.assertEqual(response["count"], 1)
        self.assertNotIn("interested_data", response["items"][0])

        rendered = json.dumps(response, default=str, ensure_ascii=False)
        for canary in (
            "QUEUE-DNI-00000000T",
            "QUEUE-DOMICILIO-CALLE-123",
            "queue-private-bucket",
            "cases/private/queue-canary.pdf",
            "QUEUE-NESTED-SECRET",
            "queue/nested/private.bin",
        ):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, rendered)

    def test_individual_projection_uses_only_safe_bounded_scalar_labels(self):
        connection = _Connection(
            {},
            {
                "tipo_infraccion": {"unsafe": "not-a-label"},
                "familia": "  Tráfico   sancionador  ",
                "classifier_result": {
                    "family": "fallback-family",
                    "confidence": 0.95,
                },
                "admisibilidad": {
                    "panel": {
                        "admissibility_panel": {
                            "admisibilidad": "ADMISSIBLE"
                        }
                    }
                },
            },
        )

        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}),
            patch.object(
                smart_queue, "get_engine", return_value=_Engine(connection)
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=False, individual_session=True
                ),
            ),
            patch.object(
                smart_queue,
                "ops_case_scope_filter",
                return_value=("TRUE", {}),
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=100,
                only_action=None,
            )

        self.assertEqual(response["items"][0]["familia"], "Tráfico sancionador")
        self.assertEqual(response["items"][0]["admisibilidad"], "ADMISSIBLE")

    def test_individual_projection_rejects_locators_signed_urls_and_tokens(self):
        private_values = (
            "https://bucket.s3.amazonaws.com/private.pdf",
            "s3://private-bucket/private.pdf",
            "Bearer header.payload.signature",
            "sk_synthetic_placeholder_never_real",
            "eyJheader.payload.signature",
        )
        for private_value in private_values:
            connection = _Connection(
                {},
                {
                    "tipo_infraccion": private_value,
                    "classifier_result": {
                        "family": private_value,
                        "confidence": 0.95,
                    },
                    "admisibilidad": private_value,
                },
            )
            with (
                self.subTest(private_value=private_value),
                patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}),
                patch.object(
                    smart_queue,
                    "get_engine",
                    return_value=_Engine(connection),
                ),
                patch.object(
                    smart_queue,
                    "load_ops_case_scope",
                    return_value=SimpleNamespace(
                        scope_all=False, individual_session=True
                    ),
                ),
                patch.object(
                    smart_queue,
                    "ops_case_scope_filter",
                    return_value=("TRUE", {}),
                ),
            ):
                response = smart_queue.queue_smart(
                    request=SimpleNamespace(state=SimpleNamespace()),
                    x_operator_token=OPERATOR_TOKEN,
                    limit=100,
                    only_action=None,
                )
            self.assertEqual(response["items"][0]["familia"], "")
            self.assertEqual(response["items"][0]["admisibilidad"], "")

    def test_only_action_scans_beyond_first_page_until_limit_matches(self):
        connection = _PagingConnection()

        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=False,
            ),
            patch.object(
                smart_queue,
                "get_engine",
                return_value=_Engine(connection),
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=False,
                    individual_session=True,
                ),
            ),
            patch.object(
                smart_queue,
                "ops_case_scope_filter",
                return_value=("c.test_mode = TRUE", {}),
            ),
            patch.object(
                smart_queue,
                "project_case_authorization_evidence",
                return_value={
                    "authorization_evidence_status": "verified",
                    "signed_authority_verified": True,
                },
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=1,
                only_action="PRESENTAR",
            )

        self.assertEqual(response["count"], 1)
        self.assertEqual(response["items"][0]["case_id"], connection.target_case_id)
        self.assertEqual(response["items"][0]["next_action"], "PRESENTAR")
        self.assertEqual(len(connection.case_queries), 2)

        first_sql, first_params = connection.case_queries[0]
        second_sql, second_params = connection.case_queries[1]
        for sql, parameters in connection.case_queries:
            normalized = sql.casefold()
            self.assertLess(
                normalized.index("c.test_mode = true"),
                normalized.index("limit :limit"),
            )
            self.assertIn("c.id desc", normalized)
            self.assertEqual(parameters["limit"], 100)

        self.assertNotIn("cursor_updated_at", first_params)
        self.assertIn(":cursor_updated_at", second_sql)
        self.assertEqual(second_params["cursor_updated_at"], connection.rows[99][9])
        self.assertEqual(second_params["cursor_case_id"], connection.rows[99][0])

    def test_vehicle_consent_never_becomes_generic_authority(self):
        connection = _Connection(
            {},
            case_type="vehicle_removal",
            vehicle_consent=True,
        )
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}),
            patch.object(
                smart_queue,
                "get_engine",
                return_value=_Engine(connection),
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=False,
                    individual_session=True,
                ),
            ),
            patch.object(
                smart_queue,
                "ops_case_scope_filter",
                return_value=("TRUE", {}),
            ),
            patch.object(
                smart_queue,
                "project_case_authorization_evidence",
            ) as authority_projection,
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=10,
                only_action=None,
            )

        item = response["items"][0]
        self.assertEqual(item["case_type"], "vehicle_removal")
        self.assertTrue(item["vehicle_preparation_consent"])
        self.assertFalse(item["authorized"])
        self.assertFalse(item["signed_authority_verified"])
        self.assertEqual(
            item["authorization_evidence_status"],
            "not_applicable",
        )
        self.assertEqual(item["next_action"], "FALTA_AUTORIZACION")
        authority_projection.assert_not_called()

    def test_legacy_only_action_preserves_one_bounded_case_query(self):
        connection = _PagingConnection()

        with (
            patch.dict(
                os.environ,
                {"OPERATOR_TOKEN": OPERATOR_TOKEN},
                clear=False,
            ),
            patch.object(
                smart_queue,
                "get_engine",
                return_value=_Engine(connection),
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=True,
                    individual_session=False,
                ),
            ),
            patch.object(
                smart_queue,
                "ops_case_scope_filter",
                return_value=("TRUE", {}),
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=1,
                only_action="PRESENTAR",
            )

        self.assertEqual(response["count"], 0)
        self.assertEqual(len(connection.case_queries), 1)
        self.assertNotIn("cursor_updated_at", connection.case_queries[0][1])

    def test_legacy_query_and_interested_data_contract_remain_exact(self):
        interested = {"raw": {"legacy": True}}
        connection = _Connection(interested)
        scope_filter = Mock(
            side_effect=AssertionError("legacy must not build individual scope SQL")
        )

        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}),
            patch.object(
                smart_queue, "get_engine", return_value=_Engine(connection)
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=True, individual_session=False
                ),
            ),
            patch.object(
                smart_queue, "ops_case_scope_filter", scope_filter
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=17,
                only_action=None,
            )

        normalized = " ".join(connection.case_sql.casefold().split())
        self.assertIn("from cases where", normalized)
        self.assertNotIn("from cases c", normalized)
        self.assertIn("coalesce(interested_data, '{}'::jsonb)", normalized)
        self.assertIn("order by updated_at desc limit :limit", normalized)
        self.assertNotIn("queue_sort_at", normalized)
        self.assertEqual(response["items"][0]["interested_data"], interested)
        scope_filter.assert_not_called()

    def test_legacy_family_and_admissibility_values_remain_raw(self):
        raw_family = {"legacy_family": ["traffic"]}
        raw_admissibility = {"legacy_panel": {"status": "RAW"}}
        connection = _Connection(
            {"legacy": True},
            {
                "familia": raw_family,
                "admisibilidad": raw_admissibility,
                "classifier_result": {"confidence": 0.95},
            },
        )

        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}),
            patch.object(
                smart_queue, "get_engine", return_value=_Engine(connection)
            ),
            patch.object(
                smart_queue,
                "load_ops_case_scope",
                return_value=SimpleNamespace(
                    scope_all=True, individual_session=False
                ),
            ),
        ):
            response = smart_queue.queue_smart(
                request=SimpleNamespace(state=SimpleNamespace()),
                x_operator_token=OPERATOR_TOKEN,
                limit=100,
                only_action=None,
            )

        self.assertIs(response["items"][0]["familia"], raw_family)
        self.assertIs(
            response["items"][0]["admisibilidad"], raw_admissibility
        )


if __name__ == "__main__":
    unittest.main()
