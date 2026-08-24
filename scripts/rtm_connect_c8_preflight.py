#!/usr/bin/env python3
"""Preflight read-only del plano inerte de admision RTM CONNECT C8."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREFLIGHT_VERSION = "rtm_connect_c8_preflight_v1_0"
FROZEN_C5_APP_SHA256 = (
    "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea"
)
_C8_RUNTIME_TOKENS = (
    "rtm_connect.production_control",
    "rtm_connect.production_policy",
    "rtm_connect_c8",
    "connect/production",
    "production.dispatch.dry_run",
)
_DORMANT_LIVE_NAMES = (
    "RTM_ENABLE_CONNECT_C8_PRODUCTION",
    "RTM_ENABLE_CONNECT_C8_LIVE_ACTIVATION",
    "RTM_CONNECT_C8_PROVIDER_ENDPOINT",
    "RTM_CONNECT_C8_PROVIDER_ORIGIN",
    "RTM_CONNECT_C8_PROVIDER_TOKEN",
    "RTM_CONNECT_C8_PROVIDER_SECRET",
    "RTM_CONNECT_C8_CREDENTIAL_REF",
    "RTM_CONNECT_C8_EGRESS_ORIGIN",
    "RTM_CONNECT_C8_WORKER_ENABLED",
)


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


def _runtime_unwired() -> bool:
    source = (ROOT / "app.py").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest == FROZEN_C5_APP_SHA256 and not any(
        token in source.lower() for token in _C8_RUNTIME_TOKENS
    )


def _runtime_identity_fragments(
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> tuple[str, ...]:
    """Extrae identidad sin ``repr`` ni serializar posibles secretos."""

    if depth > 4 or value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (bytes, bytearray, memoryview, int, float, bool)):
        return ()
    active = seen if seen is not None else set()
    identity = id(value)
    if identity in active:
        return ()
    active.add(identity)
    fragments = [
        str(getattr(value, "__module__", "")),
        str(getattr(value, "__qualname__", "")),
        str(getattr(type(value), "__module__", "")),
        str(getattr(type(value), "__qualname__", "")),
    ]
    if isinstance(value, Mapping):
        for key, item in tuple(value.items()):
            fragments.extend(_runtime_identity_fragments(
                key, depth=depth + 1, seen=active,
            ))
            fragments.extend(_runtime_identity_fragments(
                item, depth=depth + 1, seen=active,
            ))
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in tuple(value):
            fragments.extend(_runtime_identity_fragments(
                item, depth=depth + 1, seen=active,
            ))
    return tuple(item for item in fragments if item)


def _runtime_value_mentions_c8(*values: Any) -> bool:
    fingerprint = " ".join(
        fragment
        for value in values
        for fragment in _runtime_identity_fragments(value)
    ).lower()
    return any(token in fingerprint for token in _C8_RUNTIME_TOKENS)


def _dependency_calls(dependant: Any) -> tuple[Any, ...]:
    calls: list[Any] = []
    pending = [dependant] if dependant is not None else []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        call = getattr(current, "call", None)
        if call is not None:
            calls.append(call)
        pending.extend(tuple(getattr(current, "dependencies", ()) or ()))
    return tuple(calls)


def _runtime_c8_publications(runtime_app: Any) -> tuple[str, ...]:
    """Inspecciona rutas, subapps, dependencias y middleware efectivos."""

    publications: set[str] = set()
    visited_surfaces: set[int] = set()

    def inspect_surface(surface: Any, prefix: str = "") -> None:
        if surface is None or id(surface) in visited_surfaces:
            return
        visited_surfaces.add(id(surface))
        for route in tuple(getattr(surface, "routes", ()) or ()):
            endpoint = getattr(route, "endpoint", None)
            route_app = getattr(route, "app", None)
            path = f"{prefix}{getattr(route, 'path', '')}"
            name = str(getattr(route, "name", ""))
            if _runtime_value_mentions_c8(
                path,
                name,
                tuple(getattr(route, "tags", ()) or ()),
                endpoint,
                route_app,
                _dependency_calls(getattr(route, "dependant", None)),
            ):
                publications.add(f"route:{path}:{name}")
            if route_app is not None and hasattr(route_app, "routes"):
                inspect_surface(route_app, path)
            elif hasattr(route, "routes"):
                inspect_surface(route, path)
        for middleware in tuple(
            getattr(surface, "user_middleware", ()) or ()
        ):
            middleware_class = getattr(middleware, "cls", None)
            if _runtime_value_mentions_c8(
                middleware_class,
                tuple(getattr(middleware, "args", ()) or ()),
                dict(getattr(middleware, "kwargs", {}) or {}),
            ):
                publications.add(
                    "middleware:"
                    f"{getattr(middleware_class, '__qualname__', '')}"
                )

    inspect_surface(runtime_app)
    return tuple(sorted(publications))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c8_preflight",
        "version": PREFLIGHT_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "synthetic_only": True,
        "schema_changes_required": False,
        "routes_published": False,
        "connector_seeded": False,
        "network_used": False,
        "real_provider_contacted": False,
        "secret_resolution_performed": False,
        "external_effects_executed": False,
        "production_effects_available": False,
        "live_activation_available": False,
        "feature_enabled": False,
        "runtime_available": False,
        "runtime_publications": [],
        "checks": {},
        "blockers": [],
    }
    from scripts.rtm_staging_connect_c8_schema import safety_blockers

    report["blockers"].extend(safety_blockers())
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2

    try:
        from sqlalchemy import text
        from app import app as runtime_app
        from database import get_engine
        from rtm_connect.manifest import (
            architecture_manifest,
            assert_manifest_frozen,
        )
        from rtm_connect.production_policy import (
            assert_c8_database_identity,
            assert_c8_staging_boundary,
            load_c8_runtime_configuration,
        )
        from scripts.rtm_staging_connect_c8_schema import schema_snapshot

        assert_manifest_frozen()
        manifest = architecture_manifest()
        report["checks"].update({
            "c0_manifest_frozen": True,
            "c0_runtime_unpublished": not bool(
                manifest.get("runtime_published")
            ),
            "c0_external_effects_disabled": not bool(
                manifest.get("external_effects_enabled")
            ),
            "production_effects_unavailable": True,
            "live_activation_unavailable": True,
            "simulation_only_contract": True,
        })
        boundary = assert_c8_staging_boundary(os.environ)
        load_c8_runtime_configuration(os.environ)
        report["checks"]["feature_closed_by_default"] = True

        runtime_publications = _runtime_c8_publications(runtime_app)
        report["runtime_publications"] = list(runtime_publications)
        report["routes_published"] = bool(runtime_publications)
        report["checks"]["runtime_route_graph_has_no_c8"] = not bool(
            runtime_publications
        )
        runtime_unwired = _runtime_unwired() and not runtime_publications
        report["checks"]["app_runtime_frozen_from_c5"] = _runtime_unwired()
        report["checks"]["runtime_not_wired"] = runtime_unwired
        if not runtime_unwired:
            report["blockers"].append(
                "rtm_connect_c8_runtime_unexpectedly_wired"
            )

        dormant_absent = not any(
            str(os.getenv(name) or "").strip()
            for name in _DORMANT_LIVE_NAMES
        )
        report["checks"]["no_dormant_c8_endpoint_or_secret"] = (
            dormant_absent
        )
        if not dormant_absent:
            report["blockers"].append(
                "remove_dormant_c8_live_configuration"
            )

        with get_engine().connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            report["connected_database"] = assert_c8_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            report["checks"]["connected_database_identity_valid"] = True
            schema = schema_snapshot(conn)
            report["schema"] = schema
            report["checks"]["schema_ready"] = bool(schema["ready"])
            if not schema["ready"]:
                report["blockers"].append("rtm_connect_c8_schema_not_ready")

            migration_names = {
                str(row[0])
                for row in conn.execute(text(
                    """
                    SELECT name FROM rtm_management_schema_migrations
                    WHERE name IN (
                        'rtm_connect_c1_schema_v1_0',
                        'rtm_connect_c3_manual_schema_v1_0',
                        'rtm_connect_c4_webhook_schema_v1_0',
                        'rtm_connect_c7_assisted_schema_v1_0',
                        'rtm_connect_c8_production_schema_v1_0'
                    )
                    """
                )).fetchall()
            }
            for phase, name in {
                "c1": "rtm_connect_c1_schema_v1_0",
                "c3": "rtm_connect_c3_manual_schema_v1_0",
                "c4": "rtm_connect_c4_webhook_schema_v1_0",
                "c7": "rtm_connect_c7_assisted_schema_v1_0",
                "c8": "rtm_connect_c8_production_schema_v1_0",
            }.items():
                ready = name in migration_names
                report["checks"][f"{phase}_migration_registered"] = ready
                if not ready:
                    report["blockers"].append(
                        f"rtm_connect_{phase}_migration_missing"
                    )

            counts = dict(conn.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_connectors)
                    AS connectors_total,
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE credential_ref IS NOT NULL
                      OR NOT synthetic_only
                      OR environment <> 'staging'
                      OR (code, version) NOT IN (
                          ('synthetic.echo', 'v1.0'),
                          ('manual.handoff', 'v1.0'),
                          ('synthetic.webhook', 'v1.0')
                      )) AS forbidden_connectors,
                  (SELECT COUNT(*)
                   FROM rtm_connect_production_releases)
                    AS c8_releases,
                  (SELECT COUNT(*)
                   FROM rtm_connect_production_release_events)
                    AS c8_release_events,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_outbox)
                    AS c8_outbox,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_events)
                    AS c8_dispatch_events,
                  (SELECT COUNT(*)
                   FROM rtm_connect_production_releases
                   WHERE NOT simulation_only
                      OR external_effects_allowed
                      OR live_activation_allowed
                      OR NOT human_activation_required
                      OR provider_pack_present)
                    AS invalid_release_scope,
                  (SELECT COUNT(*)
                   FROM rtm_connect_dispatch_outbox o
                   JOIN rtm_connect_actions a ON a.id=o.action_id
                   JOIN rtm_connect_authorizations z
                     ON z.id=o.authorization_id
                   JOIN rtm_connect_production_releases r
                     ON r.id=o.release_id
                   WHERE NOT o.dry_run_only
                      OR o.network_allowed
                      OR o.provider_contacted
                      OR o.external_effects_allowed
                      OR a.capability
                           <> 'connect.production.admission.simulate'
                      OR a.satellite
                           <> 'rtm.connect.production.admission'
                      OR a.target_type
                           <> 'production.admission.candidate'
                      OR a.target_ref <> 'synthetic-c8-admission'
                      OR a.risk_class <> 'R4_critical_regulated'
                      OR NOT a.requires_dual_control
                      OR a.case_id IS NOT NULL
                      OR a.correlation_id IS NOT NULL
                      OR jsonb_array_length(a.document_hashes) <> 0
                      OR a.requested_by_operator_id
                           <> r.requested_by_operator_id
                      OR a.payload <> jsonb_build_object(
                           'contract_version',
                           'rtm.connect.c8.admission.v1',
                           'candidate_sha256', r.release_binding_sha256,
                           'synthetic_marker', 'RTM_C8_SYNTHETIC_ONLY',
                           'simulation_only', TRUE,
                           'external_effects_allowed', FALSE,
                           'live_activation_allowed', FALSE,
                           'human_activation_required', TRUE
                         )
                      OR a.payload_sha256 <> o.payload_sha256
                      OR z.action_id <> a.id
                      OR z.authorization_version <> o.authorization_version
                      OR z.payload_sha256 <> a.payload_sha256
                      OR z.idempotency_key <> a.idempotency_key
                      OR z.authority_code <> 'rtm.core.authorization'
                      OR z.authority_version <> 'rtm_core_authority_v1'
                      OR z.decision <> 'approved_frozen'
                      OR NOT z.frozen
                      OR z.revoked_at IS NOT NULL
                      OR z.required_evidence_level <> 'E4_receipt_verified'
                      OR z.authorized_connector_modes
                           <> jsonb_build_array('assisted')
                      OR jsonb_array_length(z.approved_by_operator_ids) <> 2
                      OR NOT z.approved_by_operator_ids @>
                           jsonb_build_array(
                             CAST(r.security_approved_by_operator_id AS TEXT),
                             CAST(r.operations_approved_by_operator_id AS TEXT)
                           )
                      OR z.legal_effect_authorized
                      OR r.status <> 'simulated_active'
                      OR r.security_approved_by_operator_id IS NULL
                      OR r.operations_approved_by_operator_id IS NULL
                      OR NOT r.simulation_only
                      OR r.external_effects_allowed
                      OR r.live_activation_allowed
                      OR NOT r.human_activation_required
                      OR r.provider_pack_present
                      OR r.manifest_sha256 <> o.release_manifest_sha256
                      OR r.release_binding_sha256 <> o.release_binding_sha256)
                    AS invalid_outbox_scope
                """
            )).mappings().one())

        for key, value in counts.items():
            report[key] = int(value)
        report["checks"]["no_forbidden_connectors"] = (
            report["forbidden_connectors"] == 0
        )
        report["checks"]["no_invalid_c8_scope"] = (
            report["invalid_release_scope"] == 0
            and report["invalid_outbox_scope"] == 0
        )
        report["checks"]["no_persistent_c8_residue"] = all(
            report[key] == 0
            for key in (
                "c8_releases", "c8_release_events",
                "c8_outbox", "c8_dispatch_events",
            )
        )
        report["checks"]["no_network_or_secret_resolution"] = True
        report["checks"]["no_external_effects"] = True

        failed = sorted(
            key for key, value in report["checks"].items() if not value
        )
        report["failed_checks"] = failed
        report["safe"] = not report["blockers"] and not failed
        report["ok"] = bool(report["safe"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
    _print(report, args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
