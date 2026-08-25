#!/usr/bin/env python3
"""Smoke transaccional HTTP/PostgreSQL del runtime humano A1-S.

El proceso usa ASGI in-process (sin socket), tres sesiones efimeras de
operadores ya existentes y una fixture exclusivamente sintetica. Todo el DML,
incluidas las sesiones, vive dentro de una transaccion exterior que siempre se
revierte. Una conexion PostgreSQL nueva compara despues el snapshot exacto para
demostrar residuo cero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from unittest.mock import patch


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RTM_CONNECT_A1S_RUNTIME_SMOKE_VERSION = (
    "rtm_connect_a1s_runtime_smoke_v1_0"
)
DEFAULT_RUNTIME_FIXTURE_KEY = "runtime-a94dcd3-v1"
_ROUTE_PREFIX = "/ops/connect/human-filings"
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_SESSION_REQUIRED_COLUMNS = {
    "id",
    "operator_id",
    "token_sha256",
    "status",
    "login_at",
    "last_seen_at",
    "expires_at",
    "absolute_expires_at",
    "auth_epoch",
    "ip_address",
    "user_agent",
    "metadata",
    "device_id",
    "login_access_event_id",
    "ip_source",
    "ip_trusted",
    "country_code",
    "region",
    "city",
    "timezone",
    "risk_flags",
    "created_at",
    "last_verified_at",
}


class A1SRuntimeSmokeError(RuntimeError):
    """El escenario exacto no pudo demostrarse; nunca se degrada a parcial."""


class _TransactionBoundEngine:
    """Adapta cada ``engine.begin`` del router a un SAVEPOINT del smoke."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @contextmanager
    def begin(self) -> Iterator[Any]:
        nested = self._connection.begin_nested()
        try:
            yield self._connection
        except BaseException:
            if nested.is_active:
                nested.rollback()
            raise
        else:
            if nested.is_active:
                nested.commit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-key", default=DEFAULT_RUNTIME_FIXTURE_KEY)
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: Mapping[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            dict(report),
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            default=str,
        )
    )


def _report() -> dict[str, Any]:
    return {
        "ok": False,
        "safe": False,
        "authority": "rtm_connect_a1s_runtime_smoke",
        "version": RTM_CONNECT_A1S_RUNTIME_SMOKE_VERSION,
        "contract_version": "rtm.connect.a1s.human_filing.v1",
        "synthetic_only": True,
        "http_in_process_asgi": False,
        "database_configuration_loaded": False,
        "database_connection_used": False,
        "database_touched": False,
        "database_rolled_back": False,
        "fixture_baseline_restored": False,
        "raw_session_tokens_persisted": False,
        "raw_session_tokens_reported": False,
        "provider_network_used": False,
        "administration_network_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "b2_used": False,
        "b2b_enabled": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "legal_submission_executed": False,
        "routes_published": False,
        "workers_started": False,
        "production_authorized": False,
        "production_safe": False,
        "production_effects_available": False,
        "live_verdict": "no_go",
        "checks": {},
        "blockers": [],
    }


def _fixture_key(raw: str) -> str:
    candidate = str(raw or "").strip().lower()
    if not candidate:
        candidate = f"smoke-{uuid.uuid4().hex[:20]}"
    if (
        len(candidate) < 3
        or len(candidate) > 48
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in candidate)
    ):
        raise A1SRuntimeSmokeError("fixture_key_invalid")
    return candidate


def _safety_blockers(args: argparse.Namespace) -> list[str]:
    blockers: list[str] = []
    for name in (
        "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING",
        "RTM_ENABLE_OPERATOR_AUTH_V1",
    ):
        raw = str(os.environ.get(name) or "").strip().lower()
        if raw not in _FALSE_VALUES:
            blockers.append(f"{name}_must_start_explicitly_false")
    try:
        from rtm_connect.human_filing_policy import assert_a1s_staging_boundary
        from rtm_core.operator_auth_request import (
            load_operator_auth_runtime_config,
        )

        assert_a1s_staging_boundary(os.environ)
        auth_config = load_operator_auth_runtime_config(
            os.environ,
            require_enabled=False,
        )
        if auth_config.environment != "staging":
            raise A1SRuntimeSmokeError("operator_auth_environment_not_staging")
        if len(str(os.environ.get("RTM_OPERATOR_ACCESS_HMAC_KEY") or "")) < 32:
            raise A1SRuntimeSmokeError("operator_auth_hmac_key_not_configured")
        _fixture_key(args.fixture_key)
    except Exception as exc:
        blockers.append(
            f"a1s_runtime_boundary_blocked:{type(exc).__name__}:{exc}"
        )
    return blockers


def _table_columns(conn: Any, table_name: str) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table_name"
            ),
            {"table_name": table_name},
        ).fetchall()
    }


def _fixture_snapshot(conn: Any, plan: Any) -> dict[str, int]:
    """Cuenta solamente el grafo determinista y el conector compartido."""

    from sqlalchemy import text
    from rtm_connect.human_filing_contracts import HUMAN_FILING_CODE

    row = conn.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM rtm_connect_a1s_tenants
               WHERE id=CAST(:tenant_id AS UUID)) AS tenants,
              (SELECT COUNT(*) FROM rtm_connect_a1s_memberships
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS memberships,
              (SELECT COUNT(*) FROM cases
               WHERE id=CAST(:case_id AS UUID)) AS cases,
              (SELECT COUNT(*) FROM documents
               WHERE case_id=CAST(:case_id AS UUID)) AS documents,
              (SELECT COUNT(*) FROM rtm_connect_a1s_case_bindings
               WHERE id=CAST(:binding_id AS UUID)) AS case_bindings,
              (SELECT COUNT(*) FROM rtm_connect_a1s_representation_evidence
               WHERE id=CAST(:representation_id AS UUID)) AS representations,
              (SELECT COUNT(*) FROM rtm_connect_actions
               WHERE id=CAST(:action_id AS UUID)) AS actions,
              (SELECT COUNT(*) FROM rtm_connect_authorizations
               WHERE action_id=CAST(:action_id AS UUID)) AS authorizations,
              (SELECT COUNT(*) FROM rtm_connect_attempts
               WHERE action_id=CAST(:action_id AS UUID)) AS attempts,
              (SELECT COUNT(*) FROM rtm_connect_evidence
               WHERE action_id=CAST(:action_id AS UUID)) AS evidence,
              (SELECT COUNT(*) FROM rtm_connect_transitions
               WHERE action_id=CAST(:action_id AS UUID)) AS core_transitions,
              (SELECT COUNT(*) FROM rtm_connect_idempotency_claims
               WHERE action_id=CAST(:action_id AS UUID)) AS core_idempotency,
              (SELECT COUNT(*) FROM rtm_connect_a1s_human_tasks
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS tasks,
              (SELECT COUNT(*) FROM rtm_connect_a1s_approvals
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS approvals,
              (SELECT COUNT(*) FROM rtm_connect_a1s_artifacts
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS artifacts,
              (SELECT COUNT(*) FROM rtm_connect_a1s_events
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS events,
              (SELECT COUNT(*) FROM rtm_connect_a1s_idempotency
               WHERE tenant_id=CAST(:tenant_id AS UUID)) AS idempotency,
              (SELECT COUNT(*) FROM rtm_connect_connectors
               WHERE code=:connector_code) AS shared_connectors
            """
        ),
        {
            "tenant_id": plan.tenant_id,
            "case_id": plan.case_id,
            "binding_id": plan.case_binding_id,
            "representation_id": plan.representation_id,
            "action_id": plan.action_id,
            "connector_code": HUMAN_FILING_CODE,
        },
    ).mappings().one()
    return {str(name): int(value) for name, value in row.items()}


def _operator_auth_epochs(conn: Any, operator_ids: tuple[str, ...]) -> dict[str, int]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT id, auth_epoch, locked_until
            FROM rtm_operators
            WHERE id IN (
              CAST(:one AS UUID), CAST(:two AS UUID), CAST(:three AS UUID)
            )
            """
        ),
        {"one": operator_ids[0], "two": operator_ids[1], "three": operator_ids[2]},
    ).mappings().all()
    if len(rows) != 3:
        raise A1SRuntimeSmokeError("three_synthetic_operators_required")
    now = datetime.now(timezone.utc)
    result: dict[str, int] = {}
    for row in rows:
        locked_until = row["locked_until"]
        if locked_until is not None and locked_until > now:
            raise A1SRuntimeSmokeError("synthetic_operator_locked")
        result[str(row["id"])] = int(row["auth_epoch"])
    return result


def _runtime_operators_from_fixture(conn: Any, plan: Any) -> Any:
    """Deriva solo UUIDs desde la cohorte persistente ya provisionada."""

    from sqlalchemy import text
    from rtm_connect.human_filing_runtime import RuntimeOperators

    rows = conn.execute(
        text(
            """
            SELECT id, operator_id, role
            FROM rtm_connect_a1s_memberships
            WHERE tenant_id=CAST(:tenant_id AS UUID)
              AND id IN (
                CAST(:requester AS UUID), CAST(:releaser AS UUID),
                CAST(:verifier AS UUID)
              )
            """
        ),
        {
            "tenant_id": plan.tenant_id,
            "requester": plan.requester_membership_id,
            "releaser": plan.releaser_membership_id,
            "verifier": plan.verifier_membership_id,
        },
    ).mappings().all()
    by_id = {str(row["id"]): row for row in rows}
    expected = (
        (plan.requester_membership_id, "supervisor"),
        (plan.releaser_membership_id, "releaser"),
        (plan.verifier_membership_id, "verifier"),
    )
    if len(by_id) != 3 or any(
        membership_id not in by_id
        or str(by_id[membership_id]["role"]) != role
        for membership_id, role in expected
    ):
        raise A1SRuntimeSmokeError("persistent_runtime_fixture_not_provisioned")
    return RuntimeOperators(
        requester_executor_id=str(
            by_id[plan.requester_membership_id]["operator_id"]
        ),
        releaser_id=str(by_id[plan.releaser_membership_id]["operator_id"]),
        verifier_id=str(by_id[plan.verifier_membership_id]["operator_id"]),
    )


def _remaining_sessions(conn: Any, session_ids: list[str]) -> int:
    from sqlalchemy import text

    return sum(
        int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM rtm_operator_sessions "
                    "WHERE id=CAST(:session_id AS UUID)"
                ),
                {"session_id": session_id},
            ).scalar_one()
        )
        for session_id in session_ids
    )


def _idempotency_key(fixture_key: str, operation: str) -> str:
    digest = hashlib.sha256(f"{fixture_key}:{operation}".encode("utf-8")).hexdigest()
    return f"rtma1s:{digest}"


def _expect(response: Any, *, label: str, status_code: int) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise A1SRuntimeSmokeError(f"{label}:non_json_response") from exc
    if response.status_code != status_code:
        error = payload.get("error") if isinstance(payload, dict) else None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise A1SRuntimeSmokeError(
            f"{label}:http_{response.status_code}:{error or detail or 'unexpected'}"
        )
    if response.headers.get("cache-control") != "no-store, max-age=0":
        raise A1SRuntimeSmokeError(f"{label}:missing_no_store")
    return payload


def _task_success(
    response: Any,
    *,
    label: str,
    status_code: int = 200,
    expected_status: str,
) -> tuple[dict[str, Any], str]:
    payload = _expect(response, label=label, status_code=status_code)
    if payload.get("ok") is not True or not isinstance(payload.get("task"), dict):
        raise A1SRuntimeSmokeError(f"{label}:success_envelope_invalid")
    task = dict(payload["task"])
    if task.get("status") != expected_status:
        raise A1SRuntimeSmokeError(
            f"{label}:status_{task.get('status')}_expected_{expected_status}"
        )
    etag = str(response.headers.get("etag") or "")
    expected_etag = f'W/"{task["task_id"]}:{task["status_version"]}"'
    if etag != expected_etag:
        raise A1SRuntimeSmokeError(f"{label}:etag_mismatch")
    return task, etag


def _execute_http_flow(
    *,
    connection: Any,
    plan: Any,
    operators: Any,
    tokens: Mapping[str, str],
    checks: dict[str, Any],
    scenario: str,
) -> tuple[str, dict[str, Any]]:
    from fastapi.testclient import TestClient
    from app import app as asgi_app
    from rtm_connect import human_filing_router
    from rtm_connect.human_filing_policy import HUMAN_FILING_FEATURE_FLAG
    from rtm_connect.human_filing_service import (
        HUMAN_RECEIPT_VERIFICATION_GATE,
        HUMAN_RELEASE_GATE,
        HUMAN_REVIEW_GATE,
        HUMAN_VERIFICATION_PREAPPROVAL_GATE,
    )

    bound_engine = _TransactionBoundEngine(connection)
    tenant_query = {"tenant_id": plan.tenant_id}

    def auth(operator_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[operator_id]}"}

    def command_headers(operator_id: str, operation: str, etag: str) -> dict[str, str]:
        return {
            **auth(operator_id),
            "Idempotency-Key": _idempotency_key(plan.fixture_key, operation),
            "If-Match": etag,
        }

    status_sequence: list[str] = []
    with patch.object(human_filing_router, "get_engine", return_value=bound_engine):
        with patch.dict(
            os.environ,
            {
                HUMAN_FILING_FEATURE_FLAG: "false",
                "RTM_ENABLE_OPERATOR_AUTH_V1": "false",
            },
            clear=False,
        ):
            with TestClient(asgi_app, raise_server_exceptions=False) as client:
                disabled = _expect(
                    client.get(f"{_ROUTE_PREFIX}/tenants"),
                    label="default_off_before",
                    status_code=404,
                )
                checks["feature_default_off_returns_404"] = (
                    disabled.get("ok") is False
                )

                os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "true"
                os.environ[HUMAN_FILING_FEATURE_FLAG] = "true"
                missing_session = _expect(
                    client.get(f"{_ROUTE_PREFIX}/tenants"),
                    label="individual_session_required",
                    status_code=401,
                )
                checks["individual_bearer_session_required"] = bool(
                    isinstance(missing_session.get("detail"), dict)
                    and missing_session["detail"].get("code")
                    == "human_filing.session_required"
                )

                requester = operators.requester_executor_id
                releaser = operators.releaser_id
                verifier = operators.verifier_id

                tenants = _expect(
                    client.get(
                        f"{_ROUTE_PREFIX}/tenants",
                        headers=auth(requester),
                    ),
                    label="tenant_bootstrap",
                    status_code=200,
                )
                checks["tenant_bootstrap_scoped"] = (
                    tenants.get("ok") is True
                    and sum(
                        str(item.get("tenant_id")) == plan.tenant_id
                        for item in (tenants.get("items") or [])
                    )
                    == 1
                )
                context = _expect(
                    client.get(
                        f"{_ROUTE_PREFIX}/context",
                        params=tenant_query,
                        headers=auth(requester),
                    ),
                    label="tenant_context",
                    status_code=200,
                )
                checks["three_distinct_tenant_participants"] = (
                    context.get("ok") is True
                    and len(context.get("participants") or []) == 3
                )
                options = _expect(
                    client.get(
                        f"{_ROUTE_PREFIX}/preparation-options",
                        params=tenant_query,
                        headers=auth(requester),
                    ),
                    label="preparation_options",
                    status_code=200,
                )
                checks["single_preparation_candidate"] = (
                    options.get("ok") is True
                    and len(options.get("options") or []) == 1
                    and str(
                        (options["options"][0].get("action") or {}).get("id")
                    )
                    == plan.action_id
                )

                prepare_body = {
                    "tenant_id": plan.tenant_id,
                    "case_binding_id": plan.case_binding_id,
                    "representation_evidence_id": plan.representation_id,
                    "action_id": plan.action_id,
                    "authorization_id": plan.authorization_id,
                    "due_at": plan.due_at,
                }
                prepare_headers = {
                    **auth(requester),
                    "Idempotency-Key": _idempotency_key(
                        plan.fixture_key, "prepare"
                    ),
                }
                task, etag = _task_success(
                    client.post(
                        _ROUTE_PREFIX,
                        json=prepare_body,
                        headers=prepare_headers,
                    ),
                    label="prepare",
                    status_code=201,
                    expected_status="prepared",
                )
                status_sequence.append(str(task["status"]))
                task_id = str(task["task_id"])
                package_sha256 = str(task["package_sha256"])

                replay, replay_etag = _task_success(
                    client.post(
                        _ROUTE_PREFIX,
                        json=prepare_body,
                        headers=prepare_headers,
                    ),
                    label="prepare_replay",
                    expected_status="prepared",
                )
                checks["prepare_idempotency_replayed"] = (
                    replay.get("replayed") is True
                    and replay["task_id"] == task_id
                    and replay_etag == etag
                )

                def transition(
                    suffix: str,
                    *,
                    operator_id: str,
                    operation: str,
                    expected_status: str,
                    body: Mapping[str, Any] | None = None,
                ) -> None:
                    nonlocal task, etag
                    response = client.post(
                        f"{_ROUTE_PREFIX}/{task_id}{suffix}",
                        params=tenant_query,
                        json=dict(body) if body is not None else None,
                        headers=command_headers(operator_id, operation, etag),
                    )
                    task, etag = _task_success(
                        response,
                        label=operation,
                        expected_status=expected_status,
                    )
                    status_sequence.append(str(task["status"]))

                transition(
                    "/assignments",
                    operator_id=requester,
                    operation="assign",
                    expected_status="assigned",
                    body={"assignee_operator_id": requester},
                )
                transition(
                    "/reviews/start",
                    operator_id=requester,
                    operation="review-start",
                    expected_status="reviewing",
                )
                transition(
                    "/reviews/attest",
                    operator_id=requester,
                    operation="review-attest",
                    expected_status="ready_for_release",
                    body={
                        "package_sha256": package_sha256,
                        "attestation": HUMAN_REVIEW_GATE,
                    },
                )
                transition(
                    "/verification-preapprovals",
                    operator_id=verifier,
                    operation="verification-preapproval",
                    expected_status="ready_for_release",
                    body={
                        "package_sha256": package_sha256,
                        "attestation": HUMAN_VERIFICATION_PREAPPROVAL_GATE,
                    },
                )
                transition(
                    "/releases",
                    operator_id=releaser,
                    operation="release",
                    expected_status="released",
                    body={
                        "package_sha256": package_sha256,
                        "attestation": HUMAN_RELEASE_GATE,
                    },
                )
                transition(
                    "/executions/start",
                    operator_id=requester,
                    operation="execution-start",
                    expected_status="in_progress",
                )
                witnessed_at = datetime.now(timezone.utc).isoformat()
                if scenario == "completed":
                    external_reference = (
                        "a1s-synthetic-"
                        + hashlib.sha256(
                            (
                                f"{plan.fixture_key}:external-reference"
                            ).encode("utf-8")
                        ).hexdigest()[:24]
                    )
                    transition(
                        "/outcomes",
                        operator_id=requester,
                        operation="outcome-submitted",
                        expected_status="awaiting_receipt",
                        body={
                            "outcome": "submitted",
                            "external_reference": external_reference,
                            "witnessed_at": witnessed_at,
                        },
                    )

                    receipt_options = _expect(
                        client.get(
                            f"{_ROUTE_PREFIX}/{task_id}/receipt-options",
                            params=tenant_query,
                            headers=auth(requester),
                        ),
                        label="receipt_options",
                        status_code=200,
                    )
                    eligible_receipts = receipt_options.get("options") or []
                    checks["single_hash_only_receipt_fixture"] = (
                        len(eligible_receipts) == 1
                        and str(eligible_receipts[0].get("document_id"))
                        == plan.receipt_document_id
                        and str(
                            eligible_receipts[0].get("document_sha256")
                        )
                        == plan.receipt_document_sha256
                    )
                    transition(
                        "/receipts",
                        operator_id=requester,
                        operation="receipt-submit",
                        expected_status="receipt_submitted",
                        body={
                            "document_id": plan.receipt_document_id,
                            "document_sha256": plan.receipt_document_sha256,
                            "external_reference": external_reference,
                            "witnessed_at": witnessed_at,
                        },
                    )
                    transition(
                        "/verifications",
                        operator_id=verifier,
                        operation="receipt-verify",
                        expected_status="completed",
                        body={
                            "observed_receipt_sha256": (
                                plan.receipt_document_sha256
                            ),
                            "observed_external_reference": (
                                external_reference
                            ),
                            "observed_package_sha256": package_sha256,
                            "attestation": HUMAN_RECEIPT_VERIFICATION_GATE,
                        },
                    )
                    expected_terminal = "completed"
                    checks["full_http_state_machine_completed"] = (
                        status_sequence
                        == [
                            "prepared",
                            "assigned",
                            "reviewing",
                            "ready_for_release",
                            "ready_for_release",
                            "released",
                            "in_progress",
                            "awaiting_receipt",
                            "receipt_submitted",
                            "completed",
                        ]
                    )
                elif scenario == "unknown_manual_review":
                    transition(
                        "/outcomes",
                        operator_id=requester,
                        operation="outcome-unknown",
                        expected_status="outcome_unknown",
                        body={
                            "outcome": "unknown",
                            "witnessed_at": witnessed_at,
                        },
                    )
                    transition(
                        "/reconciliations/start",
                        operator_id=verifier,
                        operation="reconciliation-start-one",
                        expected_status="reconciling",
                    )
                    transition(
                        "/reconciliations/resolve",
                        operator_id=verifier,
                        operation="reconciliation-remains-unknown",
                        expected_status="outcome_unknown",
                        body={"resolution": "remains_unknown"},
                    )
                    transition(
                        "/reconciliations/start",
                        operator_id=verifier,
                        operation="reconciliation-start-two",
                        expected_status="reconciling",
                    )
                    transition(
                        "/reconciliations/resolve",
                        operator_id=verifier,
                        operation="reconciliation-manual-review",
                        expected_status="manual_review",
                        body={"resolution": "manual_review"},
                    )
                    expected_terminal = "manual_review"
                    checks["full_http_unknown_reconciliation_branch"] = (
                        status_sequence
                        == [
                            "prepared",
                            "assigned",
                            "reviewing",
                            "ready_for_release",
                            "ready_for_release",
                            "released",
                            "in_progress",
                            "outcome_unknown",
                            "reconciling",
                            "outcome_unknown",
                            "reconciling",
                            "manual_review",
                        ]
                    )
                else:
                    raise A1SRuntimeSmokeError(
                        f"runtime_scenario_not_admitted:{scenario}"
                    )

                detail = _expect(
                    client.get(
                        f"{_ROUTE_PREFIX}/{task_id}",
                        params=tenant_query,
                        headers=auth(requester),
                    ),
                    label=f"{scenario}_detail",
                    status_code=200,
                )
                listing = _expect(
                    client.get(
                        _ROUTE_PREFIX,
                        params={**tenant_query, "status": expected_terminal},
                        headers=auth(requester),
                    ),
                    label=f"{scenario}_list",
                    status_code=200,
                )
                checks[f"{scenario}_visible_through_read_api"] = (
                    (detail.get("task") or {}).get("status")
                    == expected_terminal
                    and len(listing.get("items") or []) == 1
                    and listing["items"][0].get("task_id") == task_id
                )
                checks["package_and_receipt_hashes_disjoint"] = (
                    plan.receipt_document_sha256
                    not in {plan.input_document_sha256}
                    and plan.receipt_document_sha256
                    != plan.input_document_sha256
                )

                os.environ[HUMAN_FILING_FEATURE_FLAG] = "false"
                os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "false"
                disabled_after = _expect(
                    client.get(f"{_ROUTE_PREFIX}/tenants"),
                    label="default_off_after",
                    status_code=404,
                )
                checks["feature_closes_again_without_restart"] = (
                    disabled_after.get("ok") is False
                )
    return task_id, task


def _execute_runtime(args: argparse.Namespace, report: dict[str, Any]) -> None:
    from sqlalchemy import text

    from database import get_engine
    from rtm_connect.human_filing_policy import (
        assert_a1s_database_identity,
        assert_a1s_staging_boundary,
    )
    from rtm_connect.human_filing_runtime import (
        audit_runtime_fixture,
        build_runtime_fixture_plan,
        provision_runtime_fixture,
    )
    from rtm_core.operator_auth_crypto import (
        generate_session_token,
        hash_session_token,
    )
    from rtm_core.operator_auth_repository import create_operator_session
    from scripts.rtm_staging_connect_a1s_schema import schema_snapshot

    boundary = assert_a1s_staging_boundary(os.environ)
    report["database_configuration_loaded"] = True
    fixture_key = _fixture_key(args.fixture_key)
    unknown_fixture_key = "unknown-" + hashlib.sha256(
        fixture_key.encode("utf-8")
    ).hexdigest()[:24]
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)
    plan = build_runtime_fixture_plan(fixture_key=fixture_key)
    unknown_plan = build_runtime_fixture_plan(
        fixture_key=unknown_fixture_key,
    )
    report["fixtures"] = {
        "completed": {
            "fixture_key": plan.fixture_key,
            "tenant_id": plan.tenant_id,
            "case_id": plan.case_id,
            "action_id": plan.action_id,
            "authorization_id": plan.authorization_id,
            "synthetic_only": True,
        },
        "unknown_manual_review": {
            "fixture_key": unknown_plan.fixture_key,
            "tenant_id": unknown_plan.tenant_id,
            "case_id": unknown_plan.case_id,
            "action_id": unknown_plan.action_id,
            "authorization_id": unknown_plan.authorization_id,
            "synthetic_only": True,
        },
    }

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session_ids: list[str] = []
    task_ids: dict[str, str] = {}
    baselines: dict[str, dict[str, int]] | None = None
    runtime_failure: Exception | None = None
    try:
        report["database_connection_used"] = True
        report["database_touched"] = True
        assert_a1s_database_identity(
            connection,
            expected_database_name=boundary.database_name,
            expected_database_role=boundary.database_role,
        )
        schema = schema_snapshot(connection)
        if not schema["ready"]:
            raise A1SRuntimeSmokeError("a1s_schema_not_ready")
        session_columns = _table_columns(connection, "rtm_operator_sessions")
        missing_session_columns = sorted(
            _SESSION_REQUIRED_COLUMNS - session_columns
        )
        if missing_session_columns:
            raise A1SRuntimeSmokeError(
                "operator_session_schema_missing:"
                + ",".join(missing_session_columns)
            )
        report["checks"]["postgresql_schema_ready"] = True

        operators = _runtime_operators_from_fixture(connection, plan)
        persistent_audit = audit_runtime_fixture(
            connection,
            fixture_key=plan.fixture_key,
            operators=operators,
        )
        if not persistent_audit.get("ready"):
            raise A1SRuntimeSmokeError(
                "persistent_runtime_fixture_not_ready"
            )

        # Los dos baselines se capturan antes del primer INSERT del smoke.
        baselines = {
            "completed": _fixture_snapshot(connection, plan),
            "unknown_manual_review": _fixture_snapshot(
                connection, unknown_plan
            ),
        }
        provisioned = provision_runtime_fixture(
            connection,
            operators=operators,
            fixture_key=unknown_plan.fixture_key,
        )
        unknown_audit = audit_runtime_fixture(
            connection,
            fixture_key=unknown_plan.fixture_key,
            operators=operators,
        )
        if not provisioned.get("ready") or not unknown_audit.get("ready"):
            raise A1SRuntimeSmokeError(
                "synthetic_fixture_audit_failed:unknown_manual_review"
            )
        report["checks"]["persistent_fixture_read_only_audited"] = True
        report["checks"]["unknown_fixture_transactionally_provisioned"] = True
        report["fixture_audit_checks"] = {
            "completed": persistent_audit.get("checks"),
            "unknown_manual_review": unknown_audit.get("checks"),
        }

        operator_ids = (
            operators.requester_executor_id,
            operators.releaser_id,
            operators.verifier_id,
        )
        auth_epochs = _operator_auth_epochs(connection, operator_ids)
        tokens = {operator_id: generate_session_token() for operator_id in operator_ids}
        if len(set(tokens.values())) != 3:
            raise A1SRuntimeSmokeError("session_token_collision")
        for operator_id in operator_ids:
            session_ids.append(
                create_operator_session(
                    connection,
                    operator_id=operator_id,
                    raw_token=tokens[operator_id],
                    auth_epoch=auth_epochs[operator_id],
                    now=fixed_now,
                    user_agent="rtm-connect-a1s-runtime-smoke/asgi",
                    metadata_json=json.dumps(
                        {
                            "runtime_smoke": RTM_CONNECT_A1S_RUNTIME_SMOKE_VERSION,
                            "synthetic_only": True,
                        },
                        sort_keys=True,
                    ),
                )
            )
        stored_hashes = {
            str(row["id"]): str(row["token_sha256"])
            for row in connection.execute(
                text(
                    "SELECT id, token_sha256 FROM rtm_operator_sessions "
                    "WHERE id IN (CAST(:one AS UUID), CAST(:two AS UUID), "
                    "CAST(:three AS UUID))"
                ),
                {
                    "one": session_ids[0],
                    "two": session_ids[1],
                    "three": session_ids[2],
                },
            ).mappings().all()
        }
        report["checks"]["sessions_store_only_sha256"] = (
            len(stored_hashes) == 3
            and all(
                stored_hashes[session_id] == hash_session_token(tokens[operator_id])
                and stored_hashes[session_id] != tokens[operator_id]
                for session_id, operator_id in zip(session_ids, operator_ids)
            )
        )

        egress_attempts: list[str] = []

        def block_egress(name: str):
            def blocked(*_args: Any, **_kwargs: Any) -> None:
                egress_attempts.append(name)
                raise A1SRuntimeSmokeError(
                    f"external_socket_attempt_blocked:{name}"
                )

            return blocked

        socket_targets = (
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.socket.sendto",
            "socket.create_connection",
            "socket.getaddrinfo",
        )
        with ExitStack() as egress_guard:
            for target in socket_targets:
                egress_guard.enter_context(
                    patch(target, side_effect=block_egress(target))
                )
            try:
                task_id, completed = _execute_http_flow(
                    connection=connection,
                    plan=plan,
                    operators=operators,
                    tokens=tokens,
                    checks=report["checks"],
                    scenario="completed",
                )
                unknown_task_id, unknown_terminal = _execute_http_flow(
                    connection=connection,
                    plan=unknown_plan,
                    operators=operators,
                    tokens=tokens,
                    checks=report["checks"],
                    scenario="unknown_manual_review",
                )
            finally:
                report["checks"]["zero_external_socket_attempts"] = (
                    len(egress_attempts) == 0
                )
        task_ids.update(
            {
                "completed": task_id,
                "unknown_manual_review": unknown_task_id,
            }
        )
        report["task_ids"] = dict(task_ids)
        report["checks"]["temporary_runtime_flags_restored"] = (
            str(
                os.environ.get("RTM_ENABLE_CONNECT_A1S_HUMAN_FILING") or ""
            ).strip().lower()
            in _FALSE_VALUES
            and str(
                os.environ.get("RTM_ENABLE_OPERATOR_AUTH_V1") or ""
            ).strip().lower()
            in _FALSE_VALUES
        )
        report["http_in_process_asgi"] = True
        task_row = connection.execute(
            text(
                "SELECT status, version FROM rtm_connect_a1s_human_tasks "
                "WHERE id=CAST(:task_id AS UUID)"
            ),
            {"task_id": task_id},
        ).mappings().one()
        action_status = connection.execute(
            text(
                "SELECT status FROM rtm_connect_actions "
                "WHERE id=CAST(:action_id AS UUID)"
            ),
            {"action_id": plan.action_id},
        ).scalar_one()
        evidence_row = connection.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (
                    WHERE e.evidence_level='E3_receipt_captured'
                  ) AS e3_total,
                  COUNT(*) FILTER (
                    WHERE e.evidence_level='E4_receipt_verified'
                  ) AS e4_total,
                  COUNT(*) FILTER (
                    WHERE e.evidence_level='E4_receipt_verified'
                      AND e.verified_by_operator_id=
                          CAST(:verifier_id AS UUID)
                      AND e.request_sha256=a.payload_sha256
                      AND e.receipt_sha256=:receipt_sha256
                      AND e.external_reference=:external_reference
                      AND e.receipt_storage_ref=:storage_ref
                      AND e.verification_method=
                          'a1s_fixture_hash_gate_v1'
                      AND e.verified_at IS NOT NULL
                      AND EXISTS (
                        SELECT 1
                        FROM rtm_connect_a1s_approvals approval
                        JOIN rtm_connect_a1s_human_tasks task
                          ON task.id=approval.task_id
                         AND task.tenant_id=approval.tenant_id
                        WHERE task.action_id=a.id
                          AND approval.approval_type=
                              'verification_preapproval'
                          AND approval.operator_id=
                              e.verified_by_operator_id
                          AND approval.principal_id=
                              task.verified_by_principal_id
                      )
                  ) AS exact_e4,
                  COUNT(DISTINCT e.id) AS distinct_evidence
                FROM rtm_connect_evidence e
                JOIN rtm_connect_actions a ON a.id=e.action_id
                WHERE e.action_id=CAST(:action_id AS UUID)
                """
            ),
            {
                "action_id": plan.action_id,
                "verifier_id": operators.verifier_id,
                "receipt_sha256": plan.receipt_document_sha256,
                "external_reference": completed["external_reference"],
                "storage_ref": (
                    f"fixture://documents/{plan.receipt_document_id}"
                ),
            },
        ).mappings().one()
        approval_row = connection.execute(
            text(
                "SELECT COUNT(*) AS total, COUNT(DISTINCT principal_id) AS principals "
                "FROM rtm_connect_a1s_approvals "
                "WHERE task_id=CAST(:task_id AS UUID)"
            ),
            {"task_id": task_id},
        ).mappings().one()
        report["checks"]["postgresql_final_state_completed"] = (
            str(task_row["status"]) == "completed"
            and int(task_row["version"]) == int(completed["status_version"])
            and str(action_status) == "confirmed"
            and int(evidence_row["e3_total"]) == 1
            and int(evidence_row["e4_total"]) == 1
            and int(evidence_row["distinct_evidence"]) == 2
        )
        report["checks"]["e4_exactly_bound_to_preapproved_verifier"] = (
            int(evidence_row["exact_e4"]) == 1
            and completed.get("verified_by_operator_id")
            == operators.verifier_id
        )
        report["checks"]["two_preoperation_principals_distinct"] = (
            int(approval_row["total"]) == 2
            and int(approval_row["principals"]) == 2
        )
        unknown_approval_row = connection.execute(
            text(
                "SELECT COUNT(*) AS total, "
                "COUNT(DISTINCT principal_id) AS principals "
                "FROM rtm_connect_a1s_approvals "
                "WHERE task_id=CAST(:task_id AS UUID)"
            ),
            {"task_id": unknown_task_id},
        ).mappings().one()
        report["checks"]["unknown_preoperation_principals_distinct"] = (
            int(unknown_approval_row["total"]) == 2
            and int(unknown_approval_row["principals"]) == 2
        )
        unknown_row = connection.execute(
            text(
                "SELECT status, version FROM rtm_connect_a1s_human_tasks "
                "WHERE id=CAST(:task_id AS UUID)"
            ),
            {"task_id": unknown_task_id},
        ).mappings().one()
        unknown_action = connection.execute(
            text(
                "SELECT status FROM rtm_connect_actions "
                "WHERE id=CAST(:action_id AS UUID)"
            ),
            {"action_id": unknown_plan.action_id},
        ).scalar_one()
        unknown_attempt_row = connection.execute(
            text(
                """
                SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (
                    WHERE attempt_number=1
                      AND status='failed'
                      AND retryable=FALSE
                      AND reconciliation_required=FALSE
                      AND result_metadata @>
                          CAST(:safe_attempt_metadata AS JSONB)
                  ) AS exact_safe_attempt
                FROM rtm_connect_attempts
                WHERE action_id=CAST(:action_id AS UUID)
                """
            ),
            {
                "action_id": unknown_plan.action_id,
                "safe_attempt_metadata": json.dumps(
                    {
                        "blind_retry_allowed": False,
                        "network_used": False,
                        "legal_submission_executed": False,
                    },
                    sort_keys=True,
                ),
            },
        ).mappings().one()
        unknown_event_row = connection.execute(
            text(
                """
                SELECT
                  COUNT(*) AS relevant_events,
                  COUNT(*) FILTER (
                    WHERE payload @>
                        CAST(:blind_retry_metadata AS JSONB)
                  ) AS blind_retry_blocked_events
                FROM rtm_connect_a1s_events
                WHERE task_id=CAST(:task_id AS UUID)
                  AND event_type IN (
                    'human_filing.outcome_unknown',
                    'human_filing.reconciling',
                    'human_filing.manual_review'
                  )
                """
            ),
            {
                "task_id": unknown_task_id,
                "blind_retry_metadata": json.dumps(
                    {"blind_retry_allowed": False}, sort_keys=True
                ),
            },
        ).mappings().one()
        report["checks"]["unknown_branch_closes_manual_review"] = (
            str(unknown_row["status"]) == "manual_review"
            and int(unknown_row["version"])
            == int(unknown_terminal["status_version"])
            and str(unknown_action) == "manual_review"
        )
        report["checks"]["unknown_branch_never_blind_retries"] = (
            int(unknown_attempt_row["total"]) == 1
            and int(unknown_attempt_row["exact_safe_attempt"]) == 1
            and int(unknown_event_row["relevant_events"]) == 5
            and int(unknown_event_row["blind_retry_blocked_events"]) == 5
        )
        in_transaction = _fixture_snapshot(connection, plan)
        unknown_in_transaction = _fixture_snapshot(connection, unknown_plan)
        report["checks"]["transaction_contains_complete_fixture_graph"] = (
            in_transaction["tenants"] == 1
            and in_transaction["memberships"] == 3
            and in_transaction["cases"] == 1
            and in_transaction["documents"] == 2
            and in_transaction["actions"] == 1
            and in_transaction["authorizations"] == 1
            and in_transaction["attempts"] == 1
            and in_transaction["tasks"] == 1
            and in_transaction["approvals"] == 2
            and in_transaction["evidence"] >= 2
            and in_transaction["core_transitions"] >= 7
            and in_transaction["core_idempotency"] == 1
            and unknown_in_transaction["tenants"] == 1
            and unknown_in_transaction["memberships"] == 3
            and unknown_in_transaction["cases"] == 1
            and unknown_in_transaction["documents"] == 2
            and unknown_in_transaction["actions"] == 1
            and unknown_in_transaction["authorizations"] == 1
            and unknown_in_transaction["attempts"] == 1
            and unknown_in_transaction["tasks"] == 1
            and unknown_in_transaction["approvals"] == 2
            and unknown_in_transaction["core_transitions"] >= 9
            and unknown_in_transaction["core_idempotency"] == 1
        )
    except Exception as exc:
        runtime_failure = exc
    finally:
        try:
            if transaction.is_active:
                transaction.rollback()
            elif connection.in_transaction():
                connection.rollback()
        finally:
            connection.close()
            report["database_rolled_back"] = True

    if baselines is not None:
        with engine.connect() as verification:
            assert_a1s_database_identity(
                verification,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            after = {
                "completed": _fixture_snapshot(verification, plan),
                "unknown_manual_review": _fixture_snapshot(
                    verification, unknown_plan
                ),
            }
            sessions_remaining = _remaining_sessions(
                verification, session_ids
            )
        report["fixture_baseline_restored"] = after == baselines
        report["checks"][
            "fresh_connection_observes_baseline_restored_and_ephemeral_zero_residue"
        ] = (
            after == baselines and sessions_remaining == 0
        )
        report["cleanup"] = {
            "fixture_snapshots_equal_to_baselines": after == baselines,
            "ephemeral_sessions_remaining": sessions_remaining,
            "database_rolled_back": report["database_rolled_back"],
        }
    else:
        report["cleanup"] = {
            "fixture_snapshots_equal_to_baselines": False,
            "ephemeral_sessions_remaining": None,
            "database_rolled_back": report["database_rolled_back"],
        }
    if runtime_failure is not None:
        raise runtime_failure
    if baselines is None:
        raise A1SRuntimeSmokeError("baseline_snapshots_missing")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _report()
    report["blockers"] = _safety_blockers(args)
    if report["blockers"]:
        _print(report, compact=args.compact)
        return 2
    try:
        _execute_runtime(args, report)
        checks_ok = bool(report["checks"]) and all(
            bool(value) for value in report["checks"].values()
        )
        report["ok"] = bool(
            checks_ok
            and report["database_rolled_back"]
            and report["fixture_baseline_restored"]
        )
        report["safe"] = report["ok"]
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["blockers"].append(
            f"runtime_smoke_blocked:{type(exc).__name__}:{str(exc)[:400]}"
        )
        report["ok"] = False
        report["safe"] = False
        code = 1
    _print(report, compact=args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
