#!/usr/bin/env python3
"""Preflight read-only de RTM CONNECT C5 Supervisor Panel."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PREFLIGHT_VERSION = "rtm_connect_c5_preflight_v1_0"


def _router_contract() -> dict[str, bool]:
    app_tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    router_tree = ast.parse(
        (ROOT / "rtm_connect" / "supervisor_router.py").read_text(
            encoding="utf-8"
        )
    )
    app_calls = [node for node in ast.walk(app_tree) if isinstance(node, ast.Call)]
    include_names = {
        node.args[0].id
        for node in app_calls
        if isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    route_methods: list[str] = []
    for node in ast.walk(router_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
            ):
                route_methods.append(decorator.func.attr.lower())
    router_assignment = next(
        (
            node.value
            for node in router_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "router"
                for target in node.targets
            )
        ),
        None,
    )
    openapi_hidden = bool(
        isinstance(router_assignment, ast.Call)
        and any(
            keyword.arg == "include_in_schema"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in router_assignment.keywords
        )
    )
    imported_modules = {
        node.module
        for node in ast.walk(app_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden_runtime_modules = {
        "rtm_connect.execution",
        "rtm_connect.webhooks",
        "rtm_connect.reconciliation",
        "rtm_connect.manual_handoff",
    }
    return {
        "projection_wired": "connect_supervisor_router" in include_names,
        "get_only": len(route_methods) == 7 and set(route_methods) == {"get"},
        "openapi_hidden": openapi_hidden,
        "execution_runtime_not_wired": not bool(
            imported_modules & forbidden_runtime_modules
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-enabled", action="store_true")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c5_preflight",
        "version": PREFLIGHT_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "read_only": True,
        "synthetic_only": True,
        "schema_changes_required": False,
        "execution_runtime_published": False,
        "supervisor_projection_wired": False,
        "network_used": False,
        "external_effects_executed": False,
        "checks": {},
        "blockers": [],
    }
    try:
        from scripts.rtm_staging_connect_c5_schema import safety_blockers

        report["blockers"].extend(safety_blockers())
        if report["blockers"]:
            report["safe"] = False
            _print(report, args.compact)
            return 2

        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.manifest import (
            architecture_manifest,
            assert_manifest_frozen,
        )
        from rtm_connect.supervisor_policy import (
            ConnectSupervisorRoutesDisabled,
            ConnectSupervisorRuntimeMisconfigured,
            assert_connect_supervisor_database_identity,
            assert_connect_supervisor_staging_boundary,
            load_connect_supervisor_runtime_config,
        )
        from rtm_connect.supervisor_repository import (
            ConnectSupervisorScopeError,
            assert_synthetic_supervisor_scope,
        )
        from scripts.rtm_staging_connect_c5_schema import (
            schema_snapshot,
        )

        assert_manifest_frozen()
        report["checks"]["c0_manifest_frozen"] = True
        manifest = architecture_manifest()
        report["execution_runtime_published"] = bool(
            manifest.get("runtime_published")
        )
        report["checks"]["execution_runtime_unpublished"] = not bool(
            manifest.get("runtime_published")
        )
        report["checks"]["manifest_external_effects_disabled"] = not bool(
            manifest.get("external_effects_enabled")
        )
        boundary = assert_connect_supervisor_staging_boundary()
        report["checks"]["central_staging_boundary_ready"] = True

        try:
            config = load_connect_supervisor_runtime_config(
                require_enabled=args.require_enabled
            )
            report["feature_enabled"] = config.enabled
            report["runtime_available"] = config.available
            report["checks"]["runtime_configuration_valid"] = True
        except ConnectSupervisorRoutesDisabled:
            report["feature_enabled"] = False
            report["runtime_available"] = False
            report["checks"]["runtime_configuration_valid"] = False
            report["blockers"].append(
                "RTM_ENABLE_CONNECT_SUPERVISOR_V1_must_be_true"
            )
        except ConnectSupervisorRuntimeMisconfigured as exc:
            report["feature_enabled"] = False
            report["runtime_available"] = False
            report["checks"]["runtime_configuration_valid"] = False
            report["blockers"].append(
                f"connect_supervisor_runtime_misconfigured:{exc}"
            )

        router_source = (
            ROOT / "rtm_connect" / "supervisor_router.py"
        ).read_text(encoding="utf-8")
        route_contract = _router_contract()
        report["supervisor_projection_wired"] = route_contract[
            "projection_wired"
        ]
        report["checks"]["supervisor_projection_wired"] = route_contract[
            "projection_wired"
        ]
        report["checks"]["execution_runtime_not_wired"] = route_contract[
            "execution_runtime_not_wired"
        ]
        report["checks"]["supervisor_routes_get_only"] = route_contract[
            "get_only"
        ]
        report["checks"]["supervisor_openapi_hidden"] = route_contract[
            "openapi_hidden"
        ]
        report["checks"]["raw_material_redacted"] = all(
            marker not in router_source
            for marker in (
                "target_ref",
                "document_hashes",
                "receipt_storage_ref",
                "reason_detail AS",
            )
        )

        engine = get_engine()
        with engine.connect() as conn:
            report["connected_database"] = (
                assert_connect_supervisor_database_identity(
                    conn,
                    expected_database_name=boundary.database_name,
                )
            )
            report["checks"]["connected_database_identity_valid"] = True
            schema = schema_snapshot(conn)
            report["schema"] = schema
            report["checks"]["schema_ready"] = schema["ready"]
            connector_scope = dict(
                conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) FILTER (
                                WHERE environment <> 'staging'
                                   OR NOT synthetic_only
                                   OR credential_ref IS NOT NULL
                                   OR (code, version) NOT IN (
                                       ('synthetic.echo', 'v1.0'),
                                       ('manual.handoff', 'v1.0'),
                                       ('synthetic.webhook', 'v1.0')
                                   )
                            ) AS forbidden
                        FROM rtm_connect_connectors
                        """
                    )
                ).mappings().one()
            )
            report["connectors_total"] = int(connector_scope["total"])
            report["forbidden_connectors"] = int(
                connector_scope["forbidden"]
            )
            report["checks"]["synthetic_connector_scope_clean"] = (
                int(connector_scope["forbidden"]) == 0
            )
            forbidden_case_actions = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_connect_actions a
                        LEFT JOIN cases c ON c.id=a.case_id
                        WHERE a.case_id IS NOT NULL
                          AND COALESCE(c.test_mode, FALSE)=FALSE
                        """
                    )
                ).scalar_one()
            )
            report["forbidden_case_actions"] = forbidden_case_actions
            report["checks"]["synthetic_case_scope_clean"] = (
                forbidden_case_actions == 0
            )
            orphaned_attempts = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_connect_attempts x
                        LEFT JOIN rtm_connect_connectors c
                          ON c.id=x.connector_id
                        WHERE x.connector_id IS NULL OR c.id IS NULL
                        """
                    )
                ).scalar_one()
            )
            report["orphaned_attempts"] = orphaned_attempts
            report["checks"]["connector_relations_intact"] = (
                orphaned_attempts == 0
            )
            try:
                assert_synthetic_supervisor_scope(conn)
                report["checks"]["complete_supervisor_scope_clean"] = True
            except ConnectSupervisorScopeError:
                report["checks"]["complete_supervisor_scope_clean"] = False
            active_supervisors = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_operators o
                        JOIN rtm_operator_roles r ON r.id=o.primary_role_id
                        WHERE o.status='active'
                          AND o.must_change_password=FALSE
                          AND o.mfa_required=FALSE
                          AND o.password_hash IS NOT NULL
                          AND o.profile @> CAST(
                              :synthetic_profile AS JSONB
                          )
                          AND (
                              o.locked_until IS NULL
                              OR o.locked_until <= NOW()
                          )
                          AND r.active=TRUE
                          AND r.permissions @> CAST(
                              :supervisor_permissions AS JSONB
                          )
                        """
                    ),
                    {
                        "synthetic_profile": json.dumps(
                            {
                                "synthetic": True,
                                "environment": "staging",
                            },
                            separators=(",", ":"),
                        ),
                        "supervisor_permissions": json.dumps(
                            ["ops.supervise"],
                            separators=(",", ":"),
                        ),
                    },
                ).scalar_one()
            )
            report["active_supervisors"] = active_supervisors
            report["checks"]["active_supervisor_present"] = (
                active_supervisors >= 1
            )
            non_synthetic_operators = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_operators
                        WHERE NOT (
                            profile @> CAST(:synthetic_profile AS JSONB)
                        )
                        """
                    ),
                    {
                        "synthetic_profile": json.dumps(
                            {
                                "synthetic": True,
                                "environment": "staging",
                            },
                            separators=(",", ":"),
                        )
                    },
                ).scalar_one()
            )
            report["non_synthetic_operators"] = non_synthetic_operators
            report["checks"]["synthetic_operator_scope_clean"] = (
                non_synthetic_operators == 0
            )

        for key, value in report["checks"].items():
            if not bool(value):
                report["blockers"].append(f"failed_check:{key}")
        report["failed_checks"] = sorted(
            key for key, value in report["checks"].items()
            if not bool(value)
        )
        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
    _print(report, args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
