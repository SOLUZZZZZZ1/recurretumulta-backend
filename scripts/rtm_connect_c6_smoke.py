#!/usr/bin/env python3
"""Smoke transaccional HTTP-loopback de RTM CONNECT C6."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SMOKE_VERSION = "rtm_connect_c6_smoke_v1_0"
TOKEN = "rtm-c6-loopback-token-not-a-real-secret"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        default=str,
    ))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _SandboxState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.records: dict[str, dict[str, str]] = {}
        self.idempotency_records: dict[str, dict[str, str]] = {}
        self.initial_unknown: set[str] = set()
        self.delayed: set[str] = set()
        self.post_count = 0
        self.effect_count = 0
        self.idempotency_replays = 0
        self.idempotency_conflicts = 0
        self.out_of_band_completions = 0
        self.get_count = 0
        self.client_peers: set[str] = set()


def _handler_type(state: _SandboxState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RTM-C6-Loopback/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _auth_ok(self) -> bool:
            values = self.headers.get_all("Authorization") or []
            return values == [f"Bearer {TOKEN}"]

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "identity")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self) -> None:
            state.client_peers.add(str(self.client_address[0]))
            if self.path != "/v1/probes" or not self._auth_ok():
                self._reply(403, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._reply(400, {"error": "bad_length"})
                return
            if not 1 <= length <= 8192:
                self._reply(413, {"error": "too_large"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._reply(400, {"error": "bad_json"})
                return
            expected_keys = {
                "contract_version", "client_reference",
                "request_sha256", "marker",
            }
            idem = str(self.headers.get("Idempotency-Key") or "")
            request_hash = str(self.headers.get("X-RTM-Request-SHA256") or "")
            if (
                set(body) != expected_keys
                or body.get("request_sha256") != request_hash
                or not idem.startswith("rtmc1:")
            ):
                self._reply(400, {"error": "contract"})
                return
            client = str(body["client_reference"])
            record = {
                "contract_version": str(body["contract_version"]),
                "environment": "sandbox",
                "status": "unknown" if client in state.initial_unknown else "accepted",
                "external_reference": f"c6probe-{client}",
                "client_reference": client,
                "request_sha256": request_hash,
                "idempotency_key": idem,
            }
            fingerprint = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            conflict = False
            with state.lock:
                state.post_count += 1
                claim = state.idempotency_records.get(idem)
                previous = state.records.get(client)
                if claim is not None:
                    if (
                        claim["client_reference"] != client
                        or claim["request_sha256"] != request_hash
                        or claim["fingerprint"] != fingerprint
                    ):
                        conflict = True
                        state.idempotency_conflicts += 1
                    else:
                        state.idempotency_replays += 1
                elif previous is not None:
                    conflict = True
                    state.idempotency_conflicts += 1
                else:
                    state.idempotency_records[idem] = {
                        "client_reference": client,
                        "request_sha256": request_hash,
                        "fingerprint": fingerprint,
                    }
                    state.records[client] = record
                    state.effect_count += 1
            if conflict:
                self._reply(409, {"error": "idempotency_conflict"})
                return
            if client in state.delayed:
                time.sleep(0.35)
            public = {k: v for k, v in state.records[client].items() if k != "idempotency_key"}
            self._reply(200, public)

        def do_GET(self) -> None:
            state.client_peers.add(str(self.client_address[0]))
            prefix = "/v1/probes/by-client-reference/"
            if not self.path.startswith(prefix) or not self._auth_ok():
                self._reply(403, {"error": "forbidden"})
                return
            client = unquote(urlsplit(self.path).path[len(prefix):])
            request_hash = str(self.headers.get("X-RTM-Request-SHA256") or "")
            idem = str(self.headers.get("Idempotency-Key") or "")
            with state.lock:
                record = state.records.get(client)
                state.get_count += 1
                if not record:
                    self._reply(404, {"error": "not_found"})
                    return
                if (
                    record["request_sha256"] != request_hash
                    or record["idempotency_key"] != idem
                ):
                    self._reply(409, {"error": "correlation"})
                    return
                public = {k: v for k, v in record.items() if k != "idempotency_key"}
            self._reply(200, public)

    return Handler


def _grant(action, operator_id: str):
    from rtm_connect.contracts import AuthorizationGrant, ConnectorMode, EvidenceLevel
    from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        ),
        required_evidence_level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
        authorized_connector_modes=(ConnectorMode.API,),
        approved_by_operator_ids=(operator_id,),
        authorized_at=_now(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        legal_effect_authorized=False,
    )


def _action(action_id: str, operator_id: str):
    from rtm_connect.contracts import ConnectActionRequest, RiskClass
    return ConnectActionRequest(
        action_id=action_id,
        capability="sandbox.http.probe",
        satellite="rtm.connect.sandbox",
        target_type="sandbox.probe",
        target_ref="synthetic-probe",
        payload={"synthetic_marker": "RTM_C6_SYNTHETIC_ONLY"},
        requested_by_operator_id=operator_id,
        requested_at=_now(),
        risk_class=RiskClass.R1_LOW_REVERSIBLE,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c6_smoke",
        "version": SMOKE_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "network_used": True,
        "loopback_only": True,
        "external_network_used": False,
        "real_provider_contacted": False,
        "routes_published": False,
        "schema_changes_applied": False,
        "external_effects_executed": False,
        "checks": {},
        "cleanup": {
            "database_rolled_back": False,
            "server_stopped": False,
            "sandbox_memory_cleared": False,
            "error": None,
            "synthetic_actions_remaining": None,
            "synthetic_connectors_remaining": None,
            "synthetic_operators_remaining": None,
            "synthetic_roles_remaining": None,
        },
        "synthetic_ids": {},
        "blockers": [],
    }
    from scripts.rtm_staging_connect_c6_schema import safety_blockers
    report["blockers"].extend(safety_blockers())
    if report["blockers"]:
        _print(report, args.compact)
        return 2

    state = _SandboxState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_type(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    role_id = str(uuid.uuid4())
    operator_id = str(uuid.uuid4())
    action_ids: list[str] = []
    engine = None
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.controlled_sandbox import ControlledSandboxConnector
        from rtm_connect.kernel import authorize_action, create_action
        from rtm_connect.provider_sandbox import (
            ControlledSandboxReplayBlocked,
            execute_controlled_sandbox_probe,
            reconcile_controlled_sandbox_probe,
        )
        from rtm_connect.provider_sandbox_policy import (
            CONTROLLED_SANDBOX_CREDENTIAL_REF,
            ProviderSandboxEndpoint,
            assert_c6_database_identity,
            assert_c6_staging_boundary,
        )
        from rtm_connect.provider_sandbox_transport import (
            ControlledSandboxProbe,
            ControlledSandboxTransport,
            ProviderSandboxAmbiguous,
        )
        from rtm_connect.secret_resolver import EnvironmentSecretResolver

        endpoint = ProviderSandboxEndpoint.loopback_for_smoke(
            f"http://127.0.0.1:{server.server_port}"
        )
        configured_network_targets = {
            str(urlsplit(endpoint.origin).hostname or "")
        }
        resolver = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": TOKEN},
            allowed_references=(CONTROLLED_SANDBOX_CREDENTIAL_REF,),
        )
        transport = ControlledSandboxTransport(
            endpoint=endpoint,
            secret_resolver=resolver,
            timeout_seconds=1.0,
        )
        connector = ControlledSandboxConnector(transport)
        boundary = assert_c6_staging_boundary()

        direct_probe = ControlledSandboxProbe(str(uuid.uuid4()), "a" * 64)
        direct_key = "rtmc1:" + "b" * 64
        first_direct = transport.submit(
            direct_probe,
            idempotency_key=direct_key,
        )
        replay_direct = transport.submit(
            direct_probe,
            idempotency_key=direct_key,
        )
        conflict_probe = ControlledSandboxProbe(str(uuid.uuid4()), "c" * 64)
        direct_conflict = False
        try:
            transport.submit(conflict_probe, idempotency_key=direct_key)
        except ProviderSandboxAmbiguous:
            direct_conflict = True
        report["checks"]["provider_idempotency_reuse_and_conflict"] = (
            first_direct == replay_direct
            and direct_conflict
            and state.post_count == 3
            and state.effect_count == 1
            and state.idempotency_replays == 1
            and state.idempotency_conflicts == 1
        )
        with state.lock:
            state.records.clear()
            state.idempotency_records.clear()
            state.post_count = 0
            state.effect_count = 0
            state.idempotency_replays = 0
            state.idempotency_conflicts = 0
        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            report["connected_database"] = assert_c6_database_identity(
                connection,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            report["checks"]["connected_database_identity_valid"] = True
            suffix = uuid.uuid4().hex[:12]
            connection.execute(text(
                """
                INSERT INTO rtm_operator_roles(
                    id, code, name, permissions, system_role, active,
                    created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :code, :name,
                    '["ops.view", "ops.supervise"]'::jsonb,
                    FALSE, TRUE, NOW(), NOW()
                )
                """
            ), {"id": role_id, "code": f"synthetic.connect.c6.{suffix}", "name": "RTM CONNECT C6 SMOKE"})
            connection.execute(text(
                """
                INSERT INTO rtm_operators(
                    id, email, display_name, password_hash, status,
                    primary_role_id, must_change_password, mfa_required,
                    profile, failed_login_count, password_algorithm,
                    password_version, auth_epoch, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :email, :display_name, NULL, 'active',
                    CAST(:role_id AS UUID), FALSE, FALSE,
                    CAST(:profile AS JSONB), 0, 'argon2id', 1, 1, NOW(), NOW()
                )
                """
            ), {
                "id": operator_id,
                "email": f"rtm-staging-connect-c6-{suffix}@example.com",
                "display_name": "RTM CONNECT C6 SMOKE",
                "role_id": role_id,
                "profile": json.dumps({"synthetic": True, "purpose": "connect_c6_smoke"}),
            })
            report["checks"]["synthetic_operator_inserted"] = True

            success_id = str(uuid.uuid4())
            action_ids.append(success_id)
            success_action = _action(success_id, operator_id)
            success_grant = _grant(success_action, operator_id)
            created = create_action(
                connection,
                action=success_action,
                authority_scope=success_grant.authority_code,
            )
            if created.replayed:
                raise RuntimeError("CORE C6 success action replay inesperado")
            authorize_action(connection, grant=success_grant)
            success = execute_controlled_sandbox_probe(
                connection,
                action=success_action,
                grant=success_grant,
                connector=connector,
                operator_id=operator_id,
            )
            report["synthetic_ids"].update({
                "role_id": role_id,
                "operator_id": operator_id,
                "connector_id": success.connector_id,
                "success_action_id": success_id,
                "success_attempt_id": success.attempt_id,
            })
            report["checks"]["loopback_probe_confirmed_after_exact_e2"] = (
                success.confirmed
                and success.status == "confirmed"
                and success.evidence_level == "E2_external_reference"
                and success.attempts == 1
                and state.post_count == 1
            )
            replay = execute_controlled_sandbox_probe(
                connection,
                action=success_action,
                grant=success_grant,
                connector=connector,
                operator_id=operator_id,
            )
            report["checks"]["confirmed_replay_has_no_network_or_attempt"] = (
                replay.replayed
                and not replay.network_call_performed
                and replay.attempt_id is None
                and replay.attempts == 1
                and state.post_count == 1
            )

            unknown_id = str(uuid.uuid4())
            state.initial_unknown.add(unknown_id)
            action_ids.append(unknown_id)
            unknown_action = _action(unknown_id, operator_id)
            unknown_grant = _grant(unknown_action, operator_id)
            created = create_action(
                connection,
                action=unknown_action,
                authority_scope=unknown_grant.authority_code,
            )
            if created.replayed:
                raise RuntimeError("CORE C6 unknown action replay inesperado")
            authorize_action(connection, grant=unknown_grant)
            unknown = execute_controlled_sandbox_probe(
                connection,
                action=unknown_action,
                grant=unknown_grant,
                connector=connector,
                operator_id=operator_id,
            )
            report["synthetic_ids"].update({
                "unknown_action_id": unknown_id,
                "unknown_attempt_id": unknown.attempt_id,
            })
            report["checks"]["provider_unknown_persisted_without_confirmation"] = (
                unknown.status == "unknown"
                and not unknown.confirmed
                and unknown.evidence_level == "E2_external_reference"
                and state.post_count == 2
            )
            blocked = False
            try:
                execute_controlled_sandbox_probe(
                    connection,
                    action=unknown_action,
                    grant=unknown_grant,
                    connector=connector,
                    operator_id=operator_id,
                )
            except ControlledSandboxReplayBlocked:
                blocked = True
            report["checks"]["unknown_blind_post_retry_blocked"] = (
                blocked and state.post_count == 2
            )
            with state.lock:
                state.records[unknown_id]["status"] = "accepted"
                state.out_of_band_completions += 1
            reconciled = reconcile_controlled_sandbox_probe(
                connection,
                action=unknown_action,
                grant=unknown_grant,
                connector=connector,
                operator_id=operator_id,
            )
            reconciled_attempt = dict(connection.execute(text(
                """
                SELECT status, reconciliation_required,
                       failure_class, error_code
                FROM rtm_connect_attempts
                WHERE id=CAST(:id AS UUID)
                """
            ), {"id": unknown.attempt_id}).mappings().one())
            report["checks"]["unknown_reconciled_by_get_only"] = (
                reconciled.confirmed
                and reconciled.status == "confirmed"
                and reconciled.attempts == 1
                and reconciled.evidence_level == "E2_external_reference"
                and state.post_count == 2
                and state.get_count == 1
                and state.out_of_band_completions == 1
                and reconciled_attempt["status"] == "succeeded"
                and not bool(reconciled_attempt["reconciliation_required"])
                and reconciled_attempt["failure_class"] is None
                and reconciled_attempt["error_code"] is None
            )

            delayed_id = str(uuid.uuid4())
            state.delayed.add(delayed_id)
            action_ids.append(delayed_id)
            delayed_connector = ControlledSandboxConnector(ControlledSandboxTransport(
                endpoint=endpoint,
                secret_resolver=resolver,
                timeout_seconds=0.1,
            ))
            delayed_action = _action(delayed_id, operator_id)
            delayed_grant = _grant(delayed_action, operator_id)
            created = create_action(
                connection,
                action=delayed_action,
                authority_scope=delayed_grant.authority_code,
            )
            if created.replayed:
                raise RuntimeError("CORE C6 delayed action replay inesperado")
            authorize_action(connection, grant=delayed_grant)
            delayed = execute_controlled_sandbox_probe(
                connection,
                action=delayed_action,
                grant=delayed_grant,
                connector=delayed_connector,
                operator_id=operator_id,
            )
            report["checks"]["read_timeout_becomes_unknown_e1"] = (
                delayed.status == "unknown"
                and not delayed.confirmed
                and delayed.evidence_level == "E1_request_recorded"
            )

            transition_values = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT to_status FROM rtm_connect_transitions
                    WHERE action_id=CAST(:id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"id": unknown_id}).fetchall()
            ]
            report["checks"]["unknown_transition_ledger_complete"] = (
                transition_values == [
                    "draft", "authorized", "queued", "executing",
                    "unknown", "reconciling", "confirmed",
                ]
            )
            secret_occurrences = int(connection.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE to_jsonb(rtm_connect_connectors)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE to_jsonb(rtm_connect_actions)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_authorizations
                   WHERE to_jsonb(rtm_connect_authorizations)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_attempts
                   WHERE to_jsonb(rtm_connect_attempts)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_evidence
                   WHERE to_jsonb(rtm_connect_evidence)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_transitions
                   WHERE to_jsonb(rtm_connect_transitions)::text LIKE :needle)
                + (SELECT COUNT(*) FROM rtm_connect_idempotency_claims
                   WHERE to_jsonb(rtm_connect_idempotency_claims)::text LIKE :needle)
                """
            ), {"needle": f"%{TOKEN}%"}).scalar_one())
            report["checks"]["secret_value_absent_from_all_ledgers"] = (
                secret_occurrences == 0
                and TOKEN not in repr(resolver.resolve(CONTROLLED_SANDBOX_CREDENTIAL_REF))
                and TOKEN not in str(resolver.resolve(CONTROLLED_SANDBOX_CREDENTIAL_REF))
            )
            connector_row = dict(connection.execute(text(
                """
                SELECT code, version, mode, synthetic_only, credential_ref,
                       risk_ceiling, supports_idempotency,
                       supports_reconciliation, configuration
                FROM rtm_connect_connectors WHERE id=CAST(:id AS UUID)
                """
            ), {"id": success.connector_id}).mappings().one())
            report["checks"]["single_transactional_connector_exact"] = (
                connector_row["code"] == "controlled.sandbox"
                and connector_row["version"] == "v1.0"
                and connector_row["mode"] == "api"
                and connector_row["risk_ceiling"] == "R1_low_reversible"
                and bool(connector_row["synthetic_only"])
                and bool(connector_row["supports_idempotency"])
                and bool(connector_row["supports_reconciliation"])
                and connector_row["credential_ref"] is None
            )
            configured_loopback_only = bool(configured_network_targets) and all(
                ipaddress.ip_address(target).is_loopback
                for target in configured_network_targets
            )
            observed_local_peers_only = bool(state.client_peers) and all(
                ipaddress.ip_address(peer).is_loopback
                for peer in state.client_peers
            )
            report["external_network_used"] = not configured_loopback_only
            report["real_provider_contacted"] = not configured_loopback_only
            report["external_effects_executed"] = (
                report["external_network_used"]
                or report["real_provider_contacted"]
            )
            report["checks"]["configured_target_is_literal_loopback"] = (
                configured_loopback_only
            )
            report["checks"]["loopback_server_observed_local_peer_only"] = (
                observed_local_peers_only
            )
            report["checks"]["no_external_effects"] = (
                report["external_network_used"] is False
                and report["real_provider_contacted"] is False
                and report["routes_published"] is False
                and report["schema_changes_applied"] is False
                and report["external_effects_executed"] is False
            )
        finally:
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True

        with engine.connect() as verification:
            params = {f"id_{index}": value for index, value in enumerate(action_ids)}
            placeholders = ",".join(
                f"CAST(:id_{index} AS UUID)" for index in range(len(action_ids))
            )
            remaining_actions = int(verification.execute(text(
                f"SELECT COUNT(*) FROM rtm_connect_actions WHERE id IN ({placeholders})"
            ), params).scalar_one())
            remaining_connectors = int(verification.execute(text(
                """
                SELECT COUNT(*) FROM rtm_connect_connectors
                WHERE code='controlled.sandbox' AND version='v1.0'
                """
            )).scalar_one())
            remaining_operators = int(verification.execute(text(
                "SELECT COUNT(*) FROM rtm_operators WHERE id=CAST(:id AS UUID)"
            ), {"id": operator_id}).scalar_one())
            remaining_roles = int(verification.execute(text(
                "SELECT COUNT(*) FROM rtm_operator_roles WHERE id=CAST(:id AS UUID)"
            ), {"id": role_id}).scalar_one())
        report["cleanup"].update({
            "synthetic_actions_remaining": remaining_actions,
            "synthetic_connectors_remaining": remaining_connectors,
            "synthetic_operators_remaining": remaining_operators,
            "synthetic_roles_remaining": remaining_roles,
        })
        report["checks"]["rollback_removed_synthetic_records"] = (
            remaining_actions == 0
            and remaining_connectors == 0
            and remaining_operators == 0
            and remaining_roles == 0
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["cleanup"]["error"] = str(exc)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        report["cleanup"]["server_stopped"] = not thread.is_alive()
        state.records.clear()
        state.idempotency_records.clear()
        state.initial_unknown.clear()
        state.delayed.clear()
        report["cleanup"]["sandbox_memory_cleared"] = (
            not state.records and not state.idempotency_records
        )

    report["checks"]["server_and_memory_cleaned"] = (
        report["cleanup"]["server_stopped"]
        and report["cleanup"]["sandbox_memory_cleared"]
    )
    report["failed_checks"] = sorted(
        key for key, value in report["checks"].items() if not value
    )
    report["tests_ok"] = not report["failed_checks"] and report.get("error") is None
    report["ok"] = bool(
        report["tests_ok"]
        and report["cleanup"]["database_rolled_back"]
        and report["cleanup"]["server_stopped"]
    )
    _print(report, args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
