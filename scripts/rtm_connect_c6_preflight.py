#!/usr/bin/env python3
"""Preflight read-only del proveedor sandbox controlado C6."""

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

PREFLIGHT_VERSION = "rtm_connect_c6_preflight_v1_0"
FROZEN_C5_APP_SHA256 = (
    "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea"
)
_C6_RUNTIME_TOKENS = (
    "provider_sandbox",
    "controlled_sandbox",
    "sandbox.http.probe",
    "rtm_connect_c6",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-enabled", action="store_true")
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
        token in source.lower() for token in _C6_RUNTIME_TOKENS
    )


def _runtime_identity_fragments(
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> tuple[str, ...]:
    """Extrae identidad sin usar repr ni materializar posibles secretos."""

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
        items = tuple(value.items())
        for key, item in items:
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


def _runtime_value_mentions_c6(*values: Any) -> bool:
    fingerprint = " ".join(
        fragment
        for value in values
        for fragment in _runtime_identity_fragments(value)
    ).lower()
    return any(token in fingerprint for token in _C6_RUNTIME_TOKENS)


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


def _runtime_c6_publications(runtime_app: Any) -> tuple[str, ...]:
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
            if _runtime_value_mentions_c6(
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
            if _runtime_value_mentions_c6(
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
        "authority": "rtm_connect_c6_preflight",
        "version": PREFLIGHT_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "synthetic_only": True,
        "schema_changes_required": False,
        "routes_published": False,
        "connector_seeded": False,
        "network_used": False,
        "real_provider_contacted": False,
        "external_effects_executed": False,
        "feature_enabled": False,
        "runtime_available": False,
        "checks": {},
        "blockers": [],
    }
    from scripts.rtm_staging_connect_c6_schema import safety_blockers
    report["blockers"].extend(safety_blockers())
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.controlled_sandbox import (
            ControlledSandboxConnector,
            assert_controlled_sandbox_manifest_frozen,
            controlled_sandbox_manifest,
        )
        from rtm_connect.manifest import architecture_manifest, assert_manifest_frozen
        from rtm_connect.provider_sandbox_policy import (
            CONTROLLED_SANDBOX_CAPABILITY,
            CONTROLLED_SANDBOX_CODE,
            CONTROLLED_SANDBOX_CONNECTOR_VERSION,
            ProviderSandboxRuntimeDisabled,
            assert_c6_database_identity,
            assert_c6_staging_boundary,
            load_c6_runtime_endpoint,
        )
        from scripts.rtm_staging_connect_c6_schema import schema_snapshot
        from app import app as runtime_app

        assert_manifest_frozen()
        assert_controlled_sandbox_manifest_frozen()
        manifest = architecture_manifest()
        c6_manifest = controlled_sandbox_manifest()
        descriptor = ControlledSandboxConnector.descriptor
        report["checks"].update({
            "c0_manifest_frozen": True,
            "c6_manifest_frozen": True,
            "connector_code_exact": descriptor.code == CONTROLLED_SANDBOX_CODE,
            "connector_version_exact": descriptor.version == CONTROLLED_SANDBOX_CONNECTOR_VERSION,
            "connector_capability_exact": descriptor.capabilities == (CONTROLLED_SANDBOX_CAPABILITY,),
            "connector_mode_api": descriptor.mode.value == "api",
            "connector_risk_ceiling_r1": descriptor.risk_ceiling.value == "R1_low_reversible",
            "connector_synthetic_only": descriptor.synthetic_only is True,
            "connector_network_boundary": descriptor.network_used is True,
            "connector_idempotent": descriptor.supports_idempotency is True,
            "connector_reconcilable": descriptor.supports_reconciliation is True,
            "core_authority_tuple_exact": (
                c6_manifest.get("authority_code") == "rtm.core.authorization"
                and c6_manifest.get("authority_version")
                == "rtm_core_authority_v1"
            ),
            "execution_runtime_unpublished": not bool(manifest.get("runtime_published")),
            "manifest_external_effects_disabled": not bool(manifest.get("external_effects_enabled")),
        })
        boundary = assert_c6_staging_boundary()
        try:
            endpoint = load_c6_runtime_endpoint(require_enabled=args.require_enabled)
            report["feature_enabled"] = endpoint is not None
            report["runtime_available"] = False
            report["checks"]["runtime_configuration_valid"] = True
            if endpoint is not None:
                report["blockers"].append(
                    "c6_v1_has_no_real_external_provider_frozen"
                )
        except ProviderSandboxRuntimeDisabled:
            report["checks"]["runtime_configuration_valid"] = False
            report["blockers"].append("RTM_ENABLE_CONNECT_C6_SANDBOX_must_be_true")
        runtime_publications = _runtime_c6_publications(runtime_app)
        report["runtime_publications"] = list(runtime_publications)
        report["routes_published"] = bool(runtime_publications)
        report["checks"]["runtime_route_graph_has_no_c6"] = not bool(
            runtime_publications
        )
        runtime_unwired = _runtime_unwired() and not runtime_publications
        report["checks"]["runtime_not_wired"] = runtime_unwired
        if not runtime_unwired:
            report["blockers"].append("rtm_connect_c6_runtime_unexpectedly_wired")
        dormant_config_absent = not any(
            os.getenv(name)
            for name in (
                "RTM_CONNECT_C6_SANDBOX_TOKEN",
                "RTM_CONNECT_C6_SANDBOX_ORIGIN",
                "RTM_CONNECT_C6_SANDBOX_CREDENTIAL_REF",
            )
        )
        report["checks"]["no_dormant_c6_endpoint_or_secret"] = (
            dormant_config_absent
        )
        if not dormant_config_absent:
            report["blockers"].append(
                "remove_dormant_c6_endpoint_and_secret_configuration"
            )

        with get_engine().connect() as conn:
            report["connected_database"] = assert_c6_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            report["checks"]["connected_database_identity_valid"] = True
            schema = schema_snapshot(conn)
            report["schema"] = schema
            report["checks"]["schema_ready"] = bool(schema["ready"])
            if not schema["ready"]:
                report["blockers"].append("rtm_connect_c6_schema_not_ready")
            migrations = {
                str(row[0])
                for row in conn.execute(text(
                    """
                    SELECT name FROM rtm_management_schema_migrations
                    WHERE name IN (
                        'rtm_connect_c1_schema_v1_0',
                        'rtm_connect_c3_manual_schema_v1_0',
                        'rtm_connect_c4_webhook_schema_v1_0',
                        'rtm_connect_c6_provider_schema_v1_0'
                    )
                    """
                )).fetchall()
            }
            for phase, name in {
                "c1": "rtm_connect_c1_schema_v1_0",
                "c3": "rtm_connect_c3_manual_schema_v1_0",
                "c4": "rtm_connect_c4_webhook_schema_v1_0",
            }.items():
                ready = name in migrations
                report["checks"][f"{phase}_migration_registered"] = ready
                if not ready:
                    report["blockers"].append(f"rtm_connect_{phase}_migration_missing")
            no_c6_migration = "rtm_connect_c6_provider_schema_v1_0" not in migrations
            report["checks"]["c6_migration_absent"] = no_c6_migration
            counts = dict(conn.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_connectors) AS connectors_total,
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE credential_ref IS NOT NULL
                      OR NOT synthetic_only
                      OR environment <> 'staging'
                      OR (code, version) NOT IN (
                          ('synthetic.echo', 'v1.0'),
                          ('manual.handoff', 'v1.0'),
                          ('synthetic.webhook', 'v1.0')
                      ))
                    AS forbidden_connectors,
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE code='controlled.sandbox')
                    AS c6_connectors,
                  (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE capability='sandbox.http.probe') AS c6_actions,
                  (SELECT COUNT(*) FROM rtm_connect_attempts x
                   JOIN rtm_connect_connectors c ON c.id=x.connector_id
                   WHERE c.code='controlled.sandbox') AS c6_attempts
                """
            )).mappings().one())
        for key, value in counts.items():
            report[key] = int(value)
        report["checks"]["no_persistent_c6_connector_or_secret_ref"] = (
            report["c6_connectors"] == 0
            and report["forbidden_connectors"] == 0
        )
        report["checks"]["c5_synthetic_connector_scope_clean"] = (
            report["forbidden_connectors"] == 0
        )
        report["checks"]["no_persistent_c6_residue"] = (
            report["c6_actions"] == 0 and report["c6_attempts"] == 0
        )
        failed = sorted(k for k, value in report["checks"].items() if not value)
        # Feature disabled is the expected default and is not a failed check.
        if not args.require_enabled:
            failed = [k for k in failed if k != "runtime_configuration_valid"]
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
