"""Proyecciones sanitizadas del panel supervisor RTM CONNECT C5.

Las consultas se construyen exclusivamente sobre ledgers existentes. No
devuelven cargas de actuacion, documentos, justificantes, material de
autorizacion congelado ni metadatos libres. Todas las funciones de este modulo
son de lectura.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from rtm_connect.manual_schema import MANUAL_TASK_STATUSES
from rtm_connect.schema import ACTION_STATUSES, RISK_CLASSES


RTM_CONNECT_C5_SUPERVISOR_REPOSITORY_VERSION = (
    "rtm_connect_c5_supervisor_repository_v1_0"
)

_ATTENTION_ACTION_STATUSES = (
    "executing",
    "external_accepted",
    "evidence_pending",
    "retryable_failed",
    "unknown",
    "reconciling",
    "manual_review",
    "permanent_failed",
)

_ACTION_SCOPE_SQL = """
    (
        EXISTS (
            SELECT 1
            FROM rtm_connect_connectors scope_current
            WHERE scope_current.id=a.current_connector_id
              AND scope_current.environment='staging'
              AND scope_current.synthetic_only=TRUE
              AND scope_current.credential_ref IS NULL
              AND (
                  (
                      scope_current.code='synthetic.echo'
                      AND scope_current.version='v1.0'
                      AND a.capability='synthetic.echo'
                      AND a.satellite='synthetic'
                  )
                  OR (
                      scope_current.code='manual.handoff'
                      AND scope_current.version='v1.0'
                      AND a.capability='administration.submit_document'
                      AND a.satellite='administration'
                  )
              )
        )
        OR EXISTS (
            SELECT 1
            FROM rtm_connect_attempts scope_visible_attempt
            JOIN rtm_connect_connectors scope_visible_connector
              ON scope_visible_connector.id=scope_visible_attempt.connector_id
            WHERE scope_visible_attempt.action_id=a.id
              AND scope_visible_connector.environment='staging'
              AND scope_visible_connector.synthetic_only=TRUE
              AND scope_visible_connector.credential_ref IS NULL
              AND (
                  (
                      scope_visible_connector.code='synthetic.echo'
                      AND scope_visible_connector.version='v1.0'
                      AND a.capability='synthetic.echo'
                      AND a.satellite='synthetic'
                  )
                  OR (
                      scope_visible_connector.code='manual.handoff'
                      AND scope_visible_connector.version='v1.0'
                      AND a.capability='administration.submit_document'
                      AND a.satellite='administration'
                  )
              )
        )
    )
    AND (
        a.case_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM cases scope_case
            WHERE scope_case.id=a.case_id
              AND COALESCE(scope_case.test_mode, FALSE)=TRUE
        )
    )
    AND (
        a.current_connector_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM rtm_connect_connectors scope_required_current
            WHERE scope_required_current.id=a.current_connector_id
              AND scope_required_current.environment='staging'
              AND scope_required_current.synthetic_only=TRUE
              AND scope_required_current.credential_ref IS NULL
              AND (
                  (
                      scope_required_current.code='synthetic.echo'
                      AND scope_required_current.version='v1.0'
                      AND a.capability='synthetic.echo'
                      AND a.satellite='synthetic'
                  )
                  OR (
                      scope_required_current.code='manual.handoff'
                      AND scope_required_current.version='v1.0'
                      AND a.capability='administration.submit_document'
                      AND a.satellite='administration'
                  )
              )
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM rtm_connect_attempts scope_attempt
        LEFT JOIN rtm_connect_connectors scope_connector
          ON scope_connector.id=scope_attempt.connector_id
        WHERE scope_attempt.action_id=a.id
          AND (
              scope_attempt.connector_id IS NULL
              OR scope_connector.id IS NULL
              OR scope_connector.environment <> 'staging'
              OR NOT scope_connector.synthetic_only
              OR scope_connector.credential_ref IS NOT NULL
              OR NOT (
                  (
                      scope_connector.code='synthetic.echo'
                      AND scope_connector.version='v1.0'
                      AND a.capability='synthetic.echo'
                      AND a.satellite='synthetic'
                  )
                  OR (
                      scope_connector.code='manual.handoff'
                      AND scope_connector.version='v1.0'
                      AND a.capability='administration.submit_document'
                      AND a.satellite='administration'
                  )
              )
          )
    )
"""

_MANUAL_TASK_SCOPE_SQL = f"""
    EXISTS (
        SELECT 1
        FROM rtm_connect_connectors manual_scope_connector
        WHERE manual_scope_connector.id=mt.connector_id
          AND manual_scope_connector.code='manual.handoff'
          AND manual_scope_connector.version='v1.0'
          AND manual_scope_connector.environment='staging'
          AND manual_scope_connector.synthetic_only=TRUE
          AND manual_scope_connector.credential_ref IS NULL
    )
    AND EXISTS (
        SELECT 1
        FROM rtm_connect_actions a
        JOIN rtm_connect_attempts manual_scope_attempt
          ON manual_scope_attempt.id=mt.attempt_id
         AND manual_scope_attempt.action_id=a.id
         AND manual_scope_attempt.connector_id=mt.connector_id
        WHERE a.id=mt.action_id
          AND a.capability='administration.submit_document'
          AND a.satellite='administration'
          AND ({_ACTION_SCOPE_SQL})
    )
"""

_WEBHOOK_SCOPE_SQL = f"""
    EXISTS (
        SELECT 1
        FROM rtm_connect_connectors webhook_scope_connector
        WHERE webhook_scope_connector.id=w.ingress_connector_id
          AND webhook_scope_connector.code='synthetic.webhook'
          AND webhook_scope_connector.version='v1.0'
          AND webhook_scope_connector.environment='staging'
          AND webhook_scope_connector.synthetic_only=TRUE
          AND webhook_scope_connector.credential_ref IS NULL
    )
    AND (
        (
            w.matched_action_id IS NULL
            AND w.matched_attempt_id IS NULL
        )
        OR EXISTS (
            SELECT 1
            FROM rtm_connect_actions a
            JOIN rtm_connect_attempts webhook_scope_attempt
              ON webhook_scope_attempt.id=w.matched_attempt_id
             AND webhook_scope_attempt.action_id=a.id
            WHERE a.id=w.matched_action_id
              AND ({_ACTION_SCOPE_SQL})
        )
    )
"""

_RECONCILIATION_SCOPE_SQL = f"""
    EXISTS (
        SELECT 1
        FROM rtm_connect_actions a
        JOIN rtm_connect_attempts reconciliation_scope_attempt
          ON reconciliation_scope_attempt.id=r.attempt_id
         AND reconciliation_scope_attempt.action_id=a.id
        JOIN rtm_connect_webhook_inbox w
          ON w.id=r.webhook_inbox_id
         AND w.matched_action_id=a.id
         AND w.matched_attempt_id=reconciliation_scope_attempt.id
        WHERE a.id=r.action_id
          AND ({_ACTION_SCOPE_SQL})
          AND ({_WEBHOOK_SCOPE_SQL})
    )
"""


class ConnectSupervisorScopeError(RuntimeError):
    """Bloquea C5 si el registro deja de ser exclusivamente sintetico."""


def _mapping_list(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _bounded_collection(result, *, limit: int) -> dict[str, Any]:
    rows = _mapping_list(result)
    total = int(rows[0].pop("collection_total")) if rows else 0
    for row in rows[1:]:
        row.pop("collection_total", None)
    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "truncated": total > len(rows),
    }


def _grouped_counts(conn, statement: str) -> dict[str, int]:
    return {
        str(row["key"]): int(row["total"])
        for row in conn.execute(text(statement)).mappings().all()
    }


def assert_synthetic_supervisor_scope(conn) -> None:
    forbidden_connectors = bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                FROM rtm_connect_connectors
                WHERE environment <> 'staging'
                   OR NOT synthetic_only
                   OR credential_ref IS NOT NULL
                   OR (code, version) NOT IN (
                       ('synthetic.echo', 'v1.0'),
                       ('manual.handoff', 'v1.0'),
                       ('synthetic.webhook', 'v1.0')
                   )
                )
                """
            )
        ).scalar_one()
    )
    forbidden_cases = bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                FROM rtm_connect_actions a
                LEFT JOIN cases c ON c.id=a.case_id
                WHERE a.case_id IS NOT NULL
                  AND COALESCE(c.test_mode, FALSE)=FALSE
                )
                """
            )
        ).scalar_one()
    )
    orphaned_attempts = bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_attempts x
                    LEFT JOIN rtm_connect_connectors c ON c.id=x.connector_id
                    WHERE x.connector_id IS NULL OR c.id IS NULL
                )
                """
            )
        ).scalar_one()
    )
    unscoped_actions = bool(
        conn.execute(
            text(
                f"""
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_actions a
                    WHERE NOT ({_ACTION_SCOPE_SQL})
                )
                """
            )
        ).scalar_one()
    )
    invalid_child_links = bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_evidence e
                    LEFT JOIN rtm_connect_attempts x ON x.id=e.attempt_id
                    WHERE e.attempt_id IS NOT NULL
                      AND (x.id IS NULL OR x.action_id <> e.action_id)
                    UNION ALL
                    SELECT 1
                    FROM rtm_connect_transitions t
                    LEFT JOIN rtm_connect_attempts x ON x.id=t.attempt_id
                    WHERE t.attempt_id IS NOT NULL
                      AND (x.id IS NULL OR x.action_id <> t.action_id)
                    UNION ALL
                    SELECT 1
                    FROM rtm_connect_manual_tasks mt
                    LEFT JOIN rtm_connect_attempts x ON x.id=mt.attempt_id
                    WHERE x.id IS NULL
                       OR x.action_id <> mt.action_id
                       OR x.connector_id <> mt.connector_id
                    UNION ALL
                    SELECT 1
                    FROM rtm_connect_webhook_inbox w
                    LEFT JOIN rtm_connect_attempts x
                      ON x.id=w.matched_attempt_id
                    WHERE (w.matched_action_id IS NULL)
                          <> (w.matched_attempt_id IS NULL)
                       OR (
                           w.matched_attempt_id IS NOT NULL
                           AND (
                               x.id IS NULL
                               OR x.action_id <> w.matched_action_id
                           )
                       )
                    UNION ALL
                    SELECT 1
                    FROM rtm_connect_reconciliations r
                    LEFT JOIN rtm_connect_attempts x ON x.id=r.attempt_id
                    LEFT JOIN rtm_connect_webhook_inbox w
                      ON w.id=r.webhook_inbox_id
                    WHERE x.id IS NULL
                       OR x.action_id <> r.action_id
                       OR w.id IS NULL
                       OR w.matched_action_id <> r.action_id
                       OR w.matched_attempt_id <> r.attempt_id
                )
                """
            )
        ).scalar_one()
    )
    if (
        forbidden_connectors
        or forbidden_cases
        or orphaned_attempts
        or unscoped_actions
        or invalid_child_links
    ):
        raise ConnectSupervisorScopeError(
            "C5 solo admite conectores y expedientes sinteticos de staging"
        )


def current_operator_can_supervise(conn, operator_id: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r ON r.id=o.primary_role_id
                    WHERE o.id=CAST(:operator_id AS UUID)
                      AND o.status='active'
                      AND o.password_hash IS NOT NULL
                      AND o.must_change_password=FALSE
                      AND o.mfa_required=FALSE
                      AND (
                          o.locked_until IS NULL OR o.locked_until <= NOW()
                      )
                      AND o.profile @> CAST(:synthetic_profile AS JSONB)
                      AND r.active=TRUE
                      AND r.permissions @> CAST(
                          :supervisor_permissions AS JSONB
                      )
                )
                """
            ),
            {
                "operator_id": operator_id,
                "synthetic_profile": json.dumps(
                    {"synthetic": True, "environment": "staging"},
                    separators=(",", ":"),
                ),
                "supervisor_permissions": json.dumps(
                    ["ops.supervise"],
                    separators=(",", ":"),
                ),
            },
        ).scalar_one()
    )


def current_supervisor_device_id(
    conn,
    *,
    session_id: str,
    operator_id: str,
) -> str | None:
    value = conn.execute(
        text(
            """
            SELECT device_id
            FROM rtm_operator_sessions
            WHERE id=CAST(:session_id AS UUID)
              AND operator_id=CAST(:operator_id AS UUID)
              AND status='active'
            LIMIT 1
            """
        ),
        {"session_id": session_id, "operator_id": operator_id},
    ).scalar_one_or_none()
    return str(value) if value else None


def _action_filter_sql(
    *,
    status: str | None,
    risk_class: str | None,
    capability: str | None,
    case_id: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = [f"({_ACTION_SCOPE_SQL})"]
    parameters: dict[str, Any] = {}
    if status:
        clauses.append("a.status=:status")
        parameters["status"] = status
    if risk_class:
        clauses.append("a.risk_class=:risk_class")
        parameters["risk_class"] = risk_class
    if capability:
        clauses.append("a.capability=:capability")
        parameters["capability"] = capability
    if case_id:
        clauses.append("a.case_id=CAST(:case_id AS UUID)")
        parameters["case_id"] = case_id
    rendered = " AND ".join(clauses)
    return (f"WHERE {rendered}" if rendered else ""), parameters


def count_actions(
    conn,
    *,
    status: str | None = None,
    risk_class: str | None = None,
    capability: str | None = None,
    case_id: str | None = None,
) -> int:
    where_sql, parameters = _action_filter_sql(
        status=status,
        risk_class=risk_class,
        capability=capability,
        case_id=case_id,
    )
    return int(
        conn.execute(
            text(f"SELECT COUNT(*) FROM rtm_connect_actions a {where_sql}"),
            parameters,
        ).scalar_one()
    )


def list_action_summaries(
    conn,
    *,
    status: str | None = None,
    risk_class: str | None = None,
    capability: str | None = None,
    case_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where_sql, parameters = _action_filter_sql(
        status=status,
        risk_class=risk_class,
        capability=capability,
        case_id=case_id,
    )
    parameters.update({"limit": limit, "offset": offset})
    return _mapping_list(
        conn.execute(
            text(
                f"""
                SELECT
                    a.id,
                    a.case_id,
                    a.capability,
                    a.satellite,
                    a.target_type,
                    a.risk_class,
                    a.requires_dual_control,
                    a.requested_by_operator_id,
                    a.requested_at,
                    a.status,
                    a.status_version,
                    a.current_connector_id,
                    c.code AS connector_code,
                    c.version AS connector_version,
                    c.mode AS connector_mode,
                    c.synthetic_only AS connector_synthetic_only,
                    (a.external_reference IS NOT NULL)
                        AS external_reference_present,
                    a.confirmed_at,
                    a.unknown_since,
                    a.cancelled_at,
                    a.created_at,
                    a.updated_at
                FROM rtm_connect_actions a
                LEFT JOIN rtm_connect_connectors c
                  ON c.id=a.current_connector_id
                 AND c.environment='staging'
                 AND c.synthetic_only=TRUE
                 AND c.credential_ref IS NULL
                {where_sql}
                ORDER BY a.updated_at DESC, a.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )
    )


def overview_snapshot(conn) -> dict[str, Any]:
    actions_by_status = _grouped_counts(
        conn,
        f"""
        SELECT a.status AS key, COUNT(*) AS total
        FROM rtm_connect_actions a
        WHERE {_ACTION_SCOPE_SQL}
        GROUP BY a.status
        """,
    )
    actions_by_risk = _grouped_counts(
        conn,
        f"""
        SELECT a.risk_class AS key, COUNT(*) AS total
        FROM rtm_connect_actions a
        WHERE {_ACTION_SCOPE_SQL}
        GROUP BY a.risk_class
        """,
    )
    connectors = dict(
        conn.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status='active') AS active,
                    COUNT(*) FILTER (WHERE synthetic_only) AS synthetic,
                    COUNT(*) FILTER (WHERE NOT synthetic_only) AS real
                FROM rtm_connect_connectors
                WHERE environment='staging'
                  AND synthetic_only=TRUE
                  AND credential_ref IS NULL
                """
            )
        ).mappings().one()
    )
    manual_tasks = dict(
        conn.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE mt.status <> 'completed') AS open,
                    COUNT(*) FILTER (
                        WHERE mt.status <> 'completed' AND mt.due_at < NOW()
                    ) AS overdue,
                    COUNT(*) FILTER (
                        WHERE mt.status <> 'completed'
                          AND mt.assignee_operator_id IS NULL
                    ) AS unassigned
                FROM rtm_connect_manual_tasks mt
                WHERE {_MANUAL_TASK_SCOPE_SQL}
                """
            )
        ).mappings().one()
    )
    webhooks = dict(
        conn.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE w.status='received') AS received,
                    COUNT(*) FILTER (WHERE w.status='verified') AS verified,
                    COUNT(*) FILTER (WHERE w.status='matched') AS matched,
                    COUNT(*) FILTER (
                        WHERE w.status='dead_lettered'
                    ) AS dead_lettered
                FROM rtm_connect_webhook_inbox w
                WHERE {_WEBHOOK_SCOPE_SQL}
                """
            )
        ).mappings().one()
    )
    reconciliations = dict(
        conn.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE r.status='started') AS open,
                    COUNT(*) FILTER (WHERE r.status='resolved') AS resolved
                FROM rtm_connect_reconciliations r
                WHERE {_RECONCILIATION_SCOPE_SQL}
                """
            )
        ).mappings().one()
    )
    attention_actions = _count_attention_actions(conn)
    dead_letters = int(webhooks.get("dead_lettered") or 0)
    return {
        "generated_at": datetime.now(timezone.utc),
        "actions": {
            "total": sum(actions_by_status.values()),
            "by_status": actions_by_status,
            "by_risk": actions_by_risk,
        },
        "connectors": {
            key: int(value or 0) for key, value in connectors.items()
        },
        "manual_tasks": {
            key: int(value or 0) for key, value in manual_tasks.items()
        },
        "webhooks": {
            key: int(value or 0) for key, value in webhooks.items()
        },
        "reconciliations": {
            key: int(value or 0)
            for key, value in reconciliations.items()
        },
        "attention": {
            "action_items": attention_actions,
            "dead_letter_items": dead_letters,
            "total": attention_actions + dead_letters,
        },
        "raw_operational_material_exposed": False,
    }


def _attention_action_predicate() -> str:
    statuses = ", ".join(f"'{value}'" for value in _ATTENTION_ACTION_STATUSES)
    return f"""
        (
            ({_ACTION_SCOPE_SQL})
            AND (
                a.status IN ({statuses})
                OR EXISTS (
                    SELECT 1
                    FROM rtm_connect_manual_tasks mt
                    WHERE mt.action_id=a.id
                      AND mt.status <> 'completed'
                      AND ({_MANUAL_TASK_SCOPE_SQL})
                )
                OR EXISTS (
                    SELECT 1
                    FROM rtm_connect_reconciliations r
                    WHERE r.action_id=a.id
                      AND r.status='started'
                      AND ({_RECONCILIATION_SCOPE_SQL})
                )
            )
        )
    """


def _count_attention_actions(conn) -> int:
    return int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM rtm_connect_actions a
                WHERE {_attention_action_predicate()}
                """
            )
        ).scalar_one()
    )


def _action_attention_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    status = str(row.get("resource_status") or "")
    if status in _ATTENTION_ACTION_STATUSES:
        reasons.append(status)
    if bool(row.get("manual_overdue")):
        reasons.append("manual_task_overdue")
    elif bool(row.get("manual_open")):
        reasons.append("manual_task_open")
    if bool(row.get("reconciliation_open")):
        reasons.append("reconciliation_open")
    return list(dict.fromkeys(reasons))


def _action_attention_priority(
    row: dict[str, Any], reasons: list[str]
) -> str:
    if (
        str(row.get("risk_class")) == "R4_critical_regulated"
        or "manual_task_overdue" in reasons
    ):
        return "urgent"
    if any(
        value in reasons
        for value in ("unknown", "manual_review", "retryable_failed")
    ):
        return "high"
    return "normal"


def list_attention_items(
    conn,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    rows = _mapping_list(
        conn.execute(
            text(
                f"""
                WITH candidates AS (
                    SELECT
                        'action'::TEXT AS resource_type,
                        a.id AS resource_id,
                        a.case_id,
                        a.capability,
                        a.status AS resource_status,
                        a.risk_class,
                        a.requires_dual_control,
                        a.current_connector_id AS connector_id,
                        c.code AS connector_code,
                        c.version AS connector_version,
                        c.synthetic_only AS connector_synthetic_only,
                        NULL::TEXT AS event_type,
                        NULL::TEXT AS dead_letter_reason_code,
                        EXISTS (
                            SELECT 1
                            FROM rtm_connect_manual_tasks mt
                            WHERE mt.action_id=a.id
                              AND mt.status <> 'completed'
                              AND ({_MANUAL_TASK_SCOPE_SQL})
                        ) AS manual_open,
                        EXISTS (
                            SELECT 1
                            FROM rtm_connect_manual_tasks mt
                            WHERE mt.action_id=a.id
                              AND mt.status <> 'completed'
                              AND mt.due_at < NOW()
                              AND ({_MANUAL_TASK_SCOPE_SQL})
                        ) AS manual_overdue,
                        EXISTS (
                            SELECT 1
                            FROM rtm_connect_reconciliations r
                            WHERE r.action_id=a.id
                              AND r.status='started'
                              AND ({_RECONCILIATION_SCOPE_SQL})
                        ) AS reconciliation_open,
                        CASE
                            WHEN a.risk_class='R4_critical_regulated'
                              OR EXISTS (
                                  SELECT 1
                                  FROM rtm_connect_manual_tasks mt
                                  WHERE mt.action_id=a.id
                                    AND mt.status <> 'completed'
                                    AND mt.due_at < NOW()
                                    AND ({_MANUAL_TASK_SCOPE_SQL})
                              )
                            THEN 0
                            WHEN a.status IN (
                                'unknown', 'manual_review',
                                'retryable_failed'
                            ) THEN 1
                            ELSE 2
                        END AS priority_rank,
                        a.updated_at AS last_activity_at
                    FROM rtm_connect_actions a
                    LEFT JOIN rtm_connect_connectors c
                      ON c.id=a.current_connector_id
                     AND c.environment='staging'
                     AND c.synthetic_only=TRUE
                     AND c.credential_ref IS NULL
                     AND (c.code, c.version) IN (
                         ('synthetic.echo', 'v1.0'),
                         ('manual.handoff', 'v1.0')
                     )
                    WHERE {_attention_action_predicate()}

                    UNION ALL

                    SELECT
                        'webhook_dead_letter'::TEXT AS resource_type,
                        w.id AS resource_id,
                        NULL::UUID AS case_id,
                        NULL::TEXT AS capability,
                        w.status AS resource_status,
                        NULL::TEXT AS risk_class,
                        FALSE AS requires_dual_control,
                        w.ingress_connector_id AS connector_id,
                        c.code AS connector_code,
                        c.version AS connector_version,
                        c.synthetic_only AS connector_synthetic_only,
                        w.event_type,
                        w.dead_letter_reason_code,
                        FALSE AS manual_open,
                        FALSE AS manual_overdue,
                        FALSE AS reconciliation_open,
                        0 AS priority_rank,
                        w.updated_at AS last_activity_at
                    FROM rtm_connect_webhook_inbox w
                    JOIN rtm_connect_connectors c
                      ON c.id=w.ingress_connector_id
                    WHERE w.status='dead_lettered'
                      AND ({_WEBHOOK_SCOPE_SQL})
                )
                SELECT
                    resource_type,
                    resource_id,
                    case_id,
                    capability,
                    resource_status,
                    risk_class,
                    requires_dual_control,
                    connector_id,
                    connector_code,
                    connector_version,
                    connector_synthetic_only,
                    event_type,
                    dead_letter_reason_code,
                    manual_open,
                    manual_overdue,
                    reconciliation_open,
                    priority_rank,
                    last_activity_at
                FROM candidates
                ORDER BY
                    priority_rank ASC,
                    last_activity_at DESC,
                    resource_id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.get("resource_type") == "webhook_dead_letter":
            row["attention_reasons"] = ["webhook_dead_lettered"]
            row["priority"] = "urgent"
        else:
            reasons = _action_attention_reasons(row)
            row["attention_reasons"] = reasons
            row["priority"] = _action_attention_priority(row, reasons)
        row.pop("manual_open", None)
        row.pop("manual_overdue", None)
        row.pop("reconciliation_open", None)
        row.pop("priority_rank", None)
        items.append(row)
    total = _count_attention_actions(conn) + count_dead_letters(conn)
    return items, total


def _manual_filter_sql(
    *,
    status: str | None,
    assignee_operator_id: str | None,
    overdue_only: bool,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = [
        f"({_MANUAL_TASK_SCOPE_SQL})",
    ]
    parameters: dict[str, Any] = {}
    if status:
        clauses.append("mt.status=:status")
        parameters["status"] = status
    if assignee_operator_id:
        clauses.append(
            "mt.assignee_operator_id=CAST(:assignee_operator_id AS UUID)"
        )
        parameters["assignee_operator_id"] = assignee_operator_id
    if overdue_only:
        clauses.extend(("mt.status <> 'completed'", "mt.due_at < NOW()"))
    rendered = " AND ".join(clauses)
    return (f"WHERE {rendered}" if rendered else ""), parameters


def count_manual_tasks(
    conn,
    *,
    status: str | None = None,
    assignee_operator_id: str | None = None,
    overdue_only: bool = False,
) -> int:
    where_sql, parameters = _manual_filter_sql(
        status=status,
        assignee_operator_id=assignee_operator_id,
        overdue_only=overdue_only,
    )
    return int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM rtm_connect_manual_tasks mt {where_sql}
                """
            ),
            parameters,
        ).scalar_one()
    )


def list_manual_task_summaries(
    conn,
    *,
    status: str | None = None,
    assignee_operator_id: str | None = None,
    overdue_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where_sql, parameters = _manual_filter_sql(
        status=status,
        assignee_operator_id=assignee_operator_id,
        overdue_only=overdue_only,
    )
    parameters.update({"limit": limit, "offset": offset})
    return _mapping_list(
        conn.execute(
            text(
                f"""
                SELECT
                    mt.id,
                    mt.action_id,
                    mt.attempt_id,
                    mt.connector_id,
                    mt.task_code,
                    mt.status,
                    mt.assignee_operator_id,
                    mt.assigned_by_operator_id,
                    mt.assigned_at,
                    mt.due_at,
                    (mt.status <> 'completed' AND mt.due_at < NOW())
                        AS overdue,
                    mt.started_at,
                    mt.receipt_submitted_at,
                    mt.verified_at,
                    mt.verified_by_operator_id,
                    mt.completed_at,
                    mt.version,
                    a.case_id,
                    a.capability,
                    a.risk_class,
                    a.status AS action_status,
                    c.code AS connector_code,
                    c.version AS connector_version,
                    c.synthetic_only AS connector_synthetic_only,
                    mt.created_at,
                    mt.updated_at
                FROM rtm_connect_manual_tasks mt
                JOIN rtm_connect_actions a ON a.id=mt.action_id
                JOIN rtm_connect_connectors c
                  ON c.id=mt.connector_id
                {where_sql}
                ORDER BY
                    (mt.status <> 'completed' AND mt.due_at < NOW()) DESC,
                    mt.due_at ASC,
                    mt.id ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )
    )


def count_dead_letters(conn) -> int:
    return int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*) FROM rtm_connect_webhook_inbox w
                WHERE w.status='dead_lettered'
                  AND ({_WEBHOOK_SCOPE_SQL})
                """
            )
        ).scalar_one()
    )


def list_dead_letter_summaries(
    conn,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _mapping_list(
        conn.execute(
            text(
                f"""
                SELECT
                    w.id,
                    w.ingress_connector_id,
                    c.code AS connector_code,
                    c.version AS connector_version,
                    c.synthetic_only AS connector_synthetic_only,
                    w.event_type,
                    w.reported_outcome,
                    w.matched_action_id,
                    w.matched_attempt_id,
                    w.status,
                    w.occurred_at,
                    w.received_at,
                    w.processed_at,
                    w.dead_letter_reason_code,
                    w.replay_count,
                    w.last_seen_at,
                    w.created_at,
                    w.updated_at
                FROM rtm_connect_webhook_inbox w
                JOIN rtm_connect_connectors c
                  ON c.id=w.ingress_connector_id
                WHERE w.status='dead_lettered'
                  AND ({_WEBHOOK_SCOPE_SQL})
                ORDER BY w.processed_at DESC, w.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
    )


def get_action_supervisor_detail(
    conn,
    action_id: str,
    *,
    history_limit: int = 100,
) -> dict[str, Any] | None:
    action = conn.execute(
        text(
            f"""
            SELECT
                a.id,
                a.case_id,
                a.capability,
                a.satellite,
                a.target_type,
                a.risk_class,
                a.requires_dual_control,
                a.requested_by_operator_id,
                a.requested_at,
                a.status,
                a.status_version,
                a.current_connector_id,
                c.code AS connector_code,
                c.version AS connector_version,
                c.mode AS connector_mode,
                c.status AS connector_status,
                c.synthetic_only AS connector_synthetic_only,
                c.supports_reconciliation,
                (a.external_reference IS NOT NULL)
                    AS external_reference_present,
                a.confirmed_at,
                a.unknown_since,
                a.cancelled_at,
                a.created_at,
                a.updated_at
            FROM rtm_connect_actions a
            LEFT JOIN rtm_connect_connectors c
              ON c.id=a.current_connector_id
             AND c.environment='staging'
             AND c.synthetic_only=TRUE
             AND c.credential_ref IS NULL
            WHERE a.id=CAST(:action_id AS UUID)
              AND {_ACTION_SCOPE_SQL}
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not action:
        return None

    authorization = conn.execute(
        text(
            """
            SELECT
                id,
                authorization_version,
                decision,
                required_evidence_level,
                authorized_at,
                expires_at,
                revoked_at,
                legal_effect_authorized,
                frozen,
                created_at
            FROM rtm_connect_authorizations
            WHERE action_id=CAST(:action_id AS UUID)
            ORDER BY authorization_version DESC
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    query_parameters = {
        "action_id": action_id,
        "history_limit": history_limit,
    }
    attempts = _bounded_collection(
        conn.execute(
            text(
                """
                WITH recent_attempts AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        x.id,
                        x.connector_id,
                        c.code AS connector_code,
                        c.version AS connector_version,
                        c.mode AS connector_mode,
                        c.synthetic_only AS connector_synthetic_only,
                        x.attempt_number,
                        x.status,
                        x.started_at,
                        x.finished_at,
                        (x.external_reference IS NOT NULL)
                            AS external_reference_present,
                        x.retryable,
                        x.reconciliation_required,
                        x.created_at,
                        x.updated_at
                    FROM rtm_connect_attempts x
                    JOIN rtm_connect_connectors c
                      ON c.id=x.connector_id
                     AND c.environment='staging'
                     AND c.synthetic_only=TRUE
                     AND c.credential_ref IS NULL
                    WHERE x.action_id=CAST(:action_id AS UUID)
                    ORDER BY x.attempt_number DESC, x.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    connector_id,
                    connector_code,
                    connector_version,
                    connector_mode,
                    connector_synthetic_only,
                    attempt_number,
                    status,
                    started_at,
                    finished_at,
                    external_reference_present,
                    retryable,
                    reconciliation_required,
                    created_at,
                    updated_at
                FROM recent_attempts
                ORDER BY attempt_number ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    evidence = _bounded_collection(
        conn.execute(
            text(
                """
                WITH recent_evidence AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        e.id,
                        e.attempt_id,
                        e.sequence_number,
                        e.evidence_level,
                        e.verified_at,
                        e.verified_by_operator_id,
                        e.created_at
                    FROM rtm_connect_evidence e
                    WHERE e.action_id=CAST(:action_id AS UUID)
                      AND (
                          e.attempt_id IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM rtm_connect_attempts scoped_attempt
                              WHERE scoped_attempt.id=e.attempt_id
                                AND scoped_attempt.action_id=e.action_id
                          )
                      )
                    ORDER BY e.sequence_number DESC, e.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    attempt_id,
                    sequence_number,
                    evidence_level,
                    verified_at,
                    verified_by_operator_id,
                    created_at
                FROM recent_evidence
                ORDER BY sequence_number ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    transitions = _bounded_collection(
        conn.execute(
            text(
                """
                WITH recent_transitions AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        t.id,
                        t.attempt_id,
                        t.sequence_number,
                        t.from_status,
                        t.to_status,
                        t.actor_type,
                        t.operator_id,
                        t.created_at
                    FROM rtm_connect_transitions t
                    WHERE t.action_id=CAST(:action_id AS UUID)
                      AND (
                          t.attempt_id IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM rtm_connect_attempts scoped_attempt
                              WHERE scoped_attempt.id=t.attempt_id
                                AND scoped_attempt.action_id=t.action_id
                          )
                      )
                    ORDER BY t.sequence_number DESC, t.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    attempt_id,
                    sequence_number,
                    from_status,
                    to_status,
                    actor_type,
                    operator_id,
                    created_at
                FROM recent_transitions
                ORDER BY sequence_number ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    manual_tasks = _bounded_collection(
        conn.execute(
            text(
                f"""
                WITH recent_manual_tasks AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        mt.id,
                        mt.attempt_id,
                        mt.connector_id,
                        mt.task_code,
                        mt.status,
                        mt.assignee_operator_id,
                        mt.assigned_by_operator_id,
                        mt.assigned_at,
                        mt.due_at,
                        (
                            mt.status <> 'completed'
                            AND mt.due_at < NOW()
                        ) AS overdue,
                        mt.started_at,
                        mt.receipt_submitted_at,
                        mt.verified_at,
                        mt.verified_by_operator_id,
                        mt.completed_at,
                        mt.version,
                        mt.created_at,
                        mt.updated_at
                    FROM rtm_connect_manual_tasks mt
                    WHERE mt.action_id=CAST(:action_id AS UUID)
                      AND ({_MANUAL_TASK_SCOPE_SQL})
                    ORDER BY mt.created_at DESC, mt.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    attempt_id,
                    connector_id,
                    task_code,
                    status,
                    assignee_operator_id,
                    assigned_by_operator_id,
                    assigned_at,
                    due_at,
                    (status <> 'completed' AND due_at < NOW()) AS overdue,
                    started_at,
                    receipt_submitted_at,
                    verified_at,
                    verified_by_operator_id,
                    completed_at,
                    version,
                    created_at,
                    updated_at
                FROM recent_manual_tasks
                ORDER BY created_at ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    reconciliations = _bounded_collection(
        conn.execute(
            text(
                f"""
                WITH recent_reconciliations AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        r.id,
                        r.attempt_id,
                        r.webhook_inbox_id,
                        r.reconciliation_number,
                        r.status,
                        r.resolution,
                        r.evidence_id,
                        r.started_at,
                        r.resolved_at,
                        r.resolved_by_operator_id,
                        r.created_at,
                        r.updated_at
                    FROM rtm_connect_reconciliations r
                    WHERE r.action_id=CAST(:action_id AS UUID)
                      AND ({_RECONCILIATION_SCOPE_SQL})
                    ORDER BY r.reconciliation_number DESC, r.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    attempt_id,
                    webhook_inbox_id,
                    reconciliation_number,
                    status,
                    resolution,
                    evidence_id,
                    started_at,
                    resolved_at,
                    resolved_by_operator_id,
                    created_at,
                    updated_at
                FROM recent_reconciliations
                ORDER BY reconciliation_number ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    webhooks = _bounded_collection(
        conn.execute(
            text(
                f"""
                WITH recent_webhooks AS (
                    SELECT
                        COUNT(*) OVER() AS collection_total,
                        w.id,
                        w.ingress_connector_id,
                        w.event_type,
                        w.reported_outcome,
                        w.matched_attempt_id,
                        w.status,
                        w.occurred_at,
                        w.received_at,
                        w.matched_at,
                        w.processed_at,
                        w.dead_letter_reason_code,
                        w.replay_count,
                        w.last_seen_at,
                        w.created_at,
                        w.updated_at
                    FROM rtm_connect_webhook_inbox w
                    WHERE w.matched_action_id=CAST(:action_id AS UUID)
                      AND ({_WEBHOOK_SCOPE_SQL})
                    ORDER BY w.received_at DESC, w.id DESC
                    LIMIT :history_limit
                )
                SELECT
                    collection_total,
                    id,
                    ingress_connector_id,
                    event_type,
                    reported_outcome,
                    matched_attempt_id,
                    status,
                    occurred_at,
                    received_at,
                    matched_at,
                    processed_at,
                    dead_letter_reason_code,
                    replay_count,
                    last_seen_at,
                    created_at,
                    updated_at
                FROM recent_webhooks
                ORDER BY received_at ASC, id ASC
                """
            ),
            query_parameters,
        ),
        limit=history_limit,
    )
    return {
        "action": dict(action),
        "authorization": dict(authorization) if authorization else None,
        "attempts": attempts,
        "evidence": evidence,
        "transitions": transitions,
        "manual_tasks": manual_tasks,
        "reconciliations": reconciliations,
        "webhooks": webhooks,
        "redaction": {
            "target_reference_exposed": False,
            "raw_action_material_exposed": False,
            "raw_authorization_material_exposed": False,
            "raw_evidence_material_exposed": False,
            "raw_webhook_material_exposed": False,
            "free_form_metadata_exposed": False,
        },
    }


__all__ = [
    "RTM_CONNECT_C5_SUPERVISOR_REPOSITORY_VERSION",
    "ACTION_STATUSES",
    "ConnectSupervisorScopeError",
    "MANUAL_TASK_STATUSES",
    "RISK_CLASSES",
    "assert_synthetic_supervisor_scope",
    "count_actions",
    "count_dead_letters",
    "count_manual_tasks",
    "current_operator_can_supervise",
    "current_supervisor_device_id",
    "get_action_supervisor_detail",
    "list_action_summaries",
    "list_attention_items",
    "list_dead_letter_summaries",
    "list_manual_task_summaries",
    "overview_snapshot",
]
