#!/usr/bin/env python3
"""Preflight read-only del handoff juridico asistido RTM CONNECT C7."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREFLIGHT_VERSION = "rtm_connect_c7_preflight_v1_0"
FROZEN_C5_APP_SHA256 = (
    "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea"
)
_C7_RUNTIME_TOKENS = (
    "/ops/connect/assisted",
    "administration.submit.legal.assisted",
    "assisted_legal",
    "assisted.legal",
    "connect_c7",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: dict[str, Any], compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def _runtime_unwired() -> bool:
    source = (ROOT / "app.py").read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest == FROZEN_C5_APP_SHA256 and not any(
        token in source.lower() for token in _C7_RUNTIME_TOKENS
    )


def _runtime_identity_fragments(
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> tuple[str, ...]:
    """Extrae solo identidad estructural, sin ``repr`` ni secretos."""

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
    return tuple(fragment for fragment in fragments if fragment)


def _runtime_value_mentions_c7(*values: Any) -> bool:
    fingerprint = " ".join(
        fragment
        for value in values
        for fragment in _runtime_identity_fragments(value)
    ).lower()
    return any(token in fingerprint for token in _C7_RUNTIME_TOKENS)


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


def _runtime_c7_publications(runtime_app: Any) -> tuple[str, ...]:
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
            if _runtime_value_mentions_c7(
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
            if _runtime_value_mentions_c7(
                middleware_class,
                tuple(getattr(middleware, "args", ()) or ()),
                dict(getattr(middleware, "kwargs", {}) or {}),
            ):
                publications.add(
                    f"middleware:{getattr(middleware_class, '__qualname__', '')}"
                )

    inspect_surface(runtime_app)
    return tuple(sorted(publications))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c7_preflight",
        "version": PREFLIGHT_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "read_only": True,
        "synthetic_only": True,
        "schema_changes_required": False,
        "routes_published": False,
        "connector_seeded": False,
        "network_used": False,
        "real_administration_contacted": False,
        "external_effects_executed": False,
        "feature_enabled": False,
        "runtime_available": False,
        "checks": {},
        "blockers": [],
    }

    try:
        from scripts.rtm_staging_connect_c7_schema import safety_blockers

        report["blockers"].extend(safety_blockers())
    except Exception as exc:
        report["blockers"].append(
            f"connect_c7_boundary_error:{type(exc).__name__}:{exc}"
        )
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2

    try:
        from sqlalchemy import text

        from app import app as runtime_app
        from database import get_engine
        from rtm_connect.assisted_legal_policy import (
            ASSISTED_LEGAL_CAPABILITY,
            ASSISTED_LEGAL_CODE,
            ASSISTED_LEGAL_CONNECTOR_VERSION,
            AssistedLegalRuntimeDisabled,
            assert_c7_database_identity,
            assert_c7_staging_boundary,
            load_c7_runtime_configuration,
        )
        from rtm_connect.connectors.assisted_legal import (
            AssistedLegalConnector,
            assert_assisted_legal_manifest_frozen,
            assisted_legal_manifest,
        )
        from rtm_connect.manifest import (
            architecture_manifest,
            assert_manifest_frozen,
        )
        from scripts.rtm_staging_connect_c7_schema import schema_snapshot

        assert_manifest_frozen()
        assert_assisted_legal_manifest_frozen()
        architecture = architecture_manifest()
        connector_manifest = assisted_legal_manifest()
        descriptor = AssistedLegalConnector.descriptor
        app_runtime_frozen = _runtime_unwired()
        report["checks"].update(
            {
                "c0_manifest_frozen": True,
                "c7_manifest_frozen": True,
                "connector_code_exact": (
                    descriptor.code == ASSISTED_LEGAL_CODE
                ),
                "connector_version_exact": (
                    descriptor.version == ASSISTED_LEGAL_CONNECTOR_VERSION
                ),
                "connector_capability_exact": (
                    descriptor.capabilities
                    == (ASSISTED_LEGAL_CAPABILITY,)
                ),
                "connector_mode_assisted": (
                    descriptor.mode.value == "assisted"
                ),
                "connector_risk_ceiling_r4": (
                    descriptor.risk_ceiling.value
                    == "R4_critical_regulated"
                ),
                "connector_synthetic_only": (
                    descriptor.synthetic_only is True
                ),
                "connector_network_free": (
                    descriptor.network_used is False
                ),
                "connector_idempotent": (
                    descriptor.supports_idempotency is True
                ),
                "connector_supports_reconciliation": (
                    descriptor.supports_reconciliation is True
                    and connector_manifest.get("supports_reconciliation")
                    is True
                ),
                "reconciliation_is_manual_without_resubmission": (
                    "reconciliation_is_manual_observation_without_resubmission"
                    in tuple(connector_manifest.get("invariants") or ())
                ),
                "human_final_submit_required": (
                    connector_manifest.get("human_final_submit_required")
                    is True
                ),
                "r4_dual_control_declared": (
                    connector_manifest.get("risk_ceiling")
                    == "R4_critical_regulated"
                    and connector_manifest.get("required_evidence")
                    == "E4_receipt_verified"
                ),
                "execution_runtime_unpublished": not bool(
                    architecture.get("runtime_published")
                ),
                "app_runtime_frozen_from_c5": app_runtime_frozen,
                "manifest_external_effects_disabled": not bool(
                    architecture.get("external_effects_enabled")
                ),
            }
        )

        boundary = assert_c7_staging_boundary(os.environ)
        try:
            load_c7_runtime_configuration(os.environ)
            report["checks"]["feature_closed_by_default"] = True
        except AssistedLegalRuntimeDisabled:
            report["checks"]["feature_closed_by_default"] = False
            report["blockers"].append(
                "RTM_ENABLE_CONNECT_C7_ASSISTED_must_remain_false"
            )

        publications = _runtime_c7_publications(runtime_app)
        report["runtime_publications"] = list(publications)
        report["routes_published"] = bool(publications)
        report["checks"]["runtime_route_graph_has_no_c7"] = not bool(
            publications
        )
        report["checks"]["runtime_not_wired"] = bool(
            app_runtime_frozen and not publications
        )
        if publications or not app_runtime_frozen:
            report["blockers"].append(
                "rtm_connect_c7_runtime_unexpectedly_wired"
            )

        dormant_config = tuple(
            name
            for name in (
                "RTM_CONNECT_C7_ENDPOINT",
                "RTM_CONNECT_C7_ORIGIN",
                "RTM_CONNECT_C7_TOKEN",
                "RTM_CONNECT_C7_CREDENTIAL_REF",
            )
            if os.getenv(name)
        )
        report["dormant_configuration"] = list(dormant_config)
        report["checks"]["no_dormant_c7_endpoint_or_secret"] = (
            not dormant_config
        )
        if dormant_config:
            report["blockers"].append(
                "remove_dormant_c7_endpoint_or_secret_configuration"
            )

        with get_engine().connect() as conn:
            report["connected_database"] = assert_c7_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            report["checks"]["connected_database_identity_valid"] = True
            schema = schema_snapshot(conn)
            report["schema"] = schema
            report["checks"]["schema_ready"] = bool(schema["ready"])
            if not schema["ready"]:
                report["blockers"].append(
                    "rtm_connect_c7_schema_not_ready"
                )

            migrations = {
                str(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT name
                        FROM rtm_management_schema_migrations
                        WHERE name IN (
                            'rtm_connect_c1_schema_v1_0',
                            'rtm_connect_c3_manual_schema_v1_0',
                            'rtm_connect_c4_webhook_schema_v1_0',
                            'rtm_connect_c7_assisted_schema_v1_0'
                        )
                        """
                    )
                ).fetchall()
            }
            for phase, name in {
                "c1": "rtm_connect_c1_schema_v1_0",
                "c3": "rtm_connect_c3_manual_schema_v1_0",
                "c4": "rtm_connect_c4_webhook_schema_v1_0",
                "c7": "rtm_connect_c7_assisted_schema_v1_0",
            }.items():
                ready = name in migrations
                report["checks"][f"{phase}_migration_registered"] = ready
                if not ready:
                    report["blockers"].append(
                        f"rtm_connect_{phase}_migration_missing"
                    )

            counts = dict(
                conn.execute(
                    text(
                        """
                        SELECT
                          (SELECT COUNT(*)
                           FROM rtm_connect_connectors)
                            AS connectors_total,
                          (SELECT COUNT(*)
                           FROM rtm_connect_connectors
                           WHERE credential_ref IS NOT NULL
                              OR NOT synthetic_only
                              OR environment <> 'staging'
                              OR (code, version) NOT IN (
                                  ('synthetic.echo', 'v1.0'),
                                  ('manual.handoff', 'v1.0'),
                                  ('synthetic.webhook', 'v1.0')
                              ))
                            AS forbidden_connectors,
                          (SELECT COUNT(*)
                           FROM rtm_connect_connectors
                           WHERE code='assisted.legal')
                            AS c7_connectors,
                          (SELECT COUNT(*)
                           FROM rtm_connect_actions
                           WHERE capability=:c7_capability)
                            AS c7_actions,
                          (SELECT COUNT(*)
                           FROM rtm_connect_attempts x
                           JOIN rtm_connect_connectors c
                             ON c.id=x.connector_id
                           WHERE c.code='assisted.legal')
                            AS c7_attempts,
                          (SELECT COUNT(*)
                           FROM rtm_connect_assisted_tasks)
                            AS c7_tasks,
                          (SELECT COUNT(*)
                           FROM rtm_connect_assisted_events)
                            AS c7_events,
                          (SELECT COUNT(*)
                           FROM rtm_connect_assisted_tasks ast
                           JOIN rtm_connect_manual_tasks mt
                             ON mt.action_id=ast.action_id
                             OR mt.attempt_id=ast.attempt_id)
                            AS manual_assisted_conflicts,
                          (SELECT COUNT(*)
                           FROM rtm_connect_assisted_tasks ast
                           LEFT JOIN rtm_connect_attempts x
                             ON x.id=ast.attempt_id
                           LEFT JOIN rtm_connect_connectors c
                             ON c.id=ast.connector_id
                           LEFT JOIN rtm_connect_authorizations au
                             ON au.id=ast.authorization_id
                           WHERE x.id IS NULL
                              OR x.action_id <> ast.action_id
                              OR x.connector_id <> ast.connector_id
                              OR c.id IS NULL
                              OR c.code <> 'assisted.legal'
                              OR c.version <> 'v1.0'
                              OR au.id IS NULL
                              OR au.action_id <> ast.action_id
                              OR au.authorization_version
                                 <> ast.authorization_version)
                            AS invalid_c7_scope,
                          (SELECT COUNT(*)
                            FROM rtm_connect_assisted_events ev
                            LEFT JOIN rtm_connect_assisted_tasks ast
                              ON ast.id=ev.task_id
                            WHERE ast.id IS NULL
                               OR ev.action_id <> ast.action_id
                               OR ev.attempt_id <> ast.attempt_id)
                            AS invalid_c7_event_scope
                        """
                    ),
                    {"c7_capability": ASSISTED_LEGAL_CAPABILITY},
                ).mappings().one()
            )

        for key, value in counts.items():
            report[key] = int(value)
        report["checks"]["c5_synthetic_connector_scope_clean"] = (
            report["forbidden_connectors"] == 0
        )
        report["checks"]["assisted_connector_not_persistently_seeded"] = (
            report["c7_connectors"] == 0
        )
        report["checks"]["no_persistent_c7_residue"] = all(
            report[key] == 0
            for key in (
                "c7_actions",
                "c7_attempts",
                "c7_tasks",
                "c7_events",
            )
        )
        report["checks"]["manual_assisted_exclusive"] = (
            report["manual_assisted_conflicts"] == 0
        )
        report["checks"]["assisted_relations_intact"] = (
            report["invalid_c7_scope"] == 0
        )
        report["checks"]["assisted_event_relations_intact"] = (
            report["invalid_c7_event_scope"] == 0
        )

        failed = sorted(
            key
            for key, value in report["checks"].items()
            if not value
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
