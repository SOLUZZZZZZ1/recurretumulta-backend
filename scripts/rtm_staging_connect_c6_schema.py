#!/usr/bin/env python3
"""Auditoría read-only de dependencias C6; no existe DDL C6."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_AUDIT_VERSION = "rtm_staging_connect_c6_schema_v1_0"

C6_SMOKE_OPERATOR_COLUMNS: dict[str, set[str]] = {
    "rtm_operator_roles": {
        "id", "code", "name", "permissions", "system_role", "active",
        "created_at", "updated_at",
    },
    "rtm_operators": {
        "id", "email", "display_name", "password_hash", "status",
        "primary_role_id", "must_change_password", "mfa_required",
        "profile", "failed_login_count", "password_algorithm",
        "password_version", "auth_epoch", "created_at", "updated_at",
    },
}

C1_TRIGGER_BINDINGS = {
    "trg_rtm_connect_actions_state_guard": (
        "rtm_connect_actions", "rtm_guard_connect_action_transition", 19,
    ),
    "trg_rtm_connect_transitions_append_only": (
        "rtm_connect_transitions", "rtm_guard_connect_append_only", 27,
    ),
    "trg_rtm_connect_evidence_append_only": (
        "rtm_connect_evidence", "rtm_guard_connect_append_only", 27,
    ),
    "trg_rtm_connect_authorizations_immutable": (
        "rtm_connect_authorizations", "rtm_guard_connect_append_only", 27,
    ),
}

C1_CONSTRAINT_TABLES = {
    "ck_rtm_connect_connector_mode": "rtm_connect_connectors",
    "ck_rtm_connect_connector_status": "rtm_connect_connectors",
    "ck_rtm_connect_connector_risk": "rtm_connect_connectors",
    "ck_rtm_connect_action_status": "rtm_connect_actions",
    "ck_rtm_connect_action_payload_sha256": "rtm_connect_actions",
    "ck_rtm_connect_action_idempotency_key": "rtm_connect_actions",
    "ck_rtm_connect_action_risk": "rtm_connect_actions",
    "ck_rtm_connect_action_document_hashes": "rtm_connect_actions",
    "ck_rtm_connect_authorization_version": "rtm_connect_authorizations",
    "ck_rtm_connect_authorization_frozen": "rtm_connect_authorizations",
    "ck_rtm_connect_authorization_evidence": "rtm_connect_authorizations",
    "ck_rtm_connect_attempt_number": "rtm_connect_attempts",
    "ck_rtm_connect_attempt_status": "rtm_connect_attempts",
    "ck_rtm_connect_evidence_sequence": "rtm_connect_evidence",
    "ck_rtm_connect_evidence_level": "rtm_connect_evidence",
    "ck_rtm_connect_transition_sequence": "rtm_connect_transitions",
    "ck_rtm_connect_idempotency_replay_count": (
        "rtm_connect_idempotency_claims"
    ),
}

_INDEX_DDL_RE = re.compile(
    r"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+"
    r"(?P<name>[a-z0-9_]+)\s+ON\s+(?P<table>[a-z0-9_]+)\s*"
    r"\((?P<columns>.*?)\)\s*"
    r"(?:WHERE\s+(?P<predicate>.*?))?\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _shield_sql_literals(value: str) -> tuple[str, tuple[str, ...]]:
    """Aparta literales SQL para normalizar solo sintaxis, no sus datos."""

    source = str(value)
    chunks: list[str] = []
    literals: list[str] = []
    cursor = 0
    plain_start = 0
    while cursor < len(source):
        delimiter: str | None = None
        if source[cursor] == "'":
            end = cursor + 1
            escape_string = bool(
                cursor > 0
                and source[cursor - 1] in {"e", "E"}
                and (
                    cursor == 1
                    or not (
                        source[cursor - 2].isalnum()
                        or source[cursor - 2] == "_"
                    )
                )
            )
            while end < len(source):
                if escape_string and source[end] == "\\":
                    end = min(end + 2, len(source))
                    continue
                if source[end] != "'":
                    end += 1
                    continue
                if end + 1 < len(source) and source[end + 1] == "'":
                    end += 2
                    continue
                end += 1
                break
            delimiter = source[cursor:end]
        elif source[cursor] == "$":
            tag = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", source[cursor:])
            if tag is not None:
                marker = tag.group(0)
                closing = source.find(marker, cursor + len(marker))
                if closing >= 0:
                    end = closing + len(marker)
                    delimiter = source[cursor:end]
        if delimiter is None:
            cursor += 1
            continue
        chunks.append(source[plain_start:cursor])
        token = f"__rtm_sql_literal_{len(literals):04d}__"
        if token in source:
            raise RuntimeError("Token reservado presente en SQL C1")
        chunks.append(token)
        literals.append(delimiter)
        cursor += len(delimiter)
        plain_start = cursor
    chunks.append(source[plain_start:])
    return "".join(chunks), tuple(literals)


def _restore_sql_literals(value: str, literals: tuple[str, ...]) -> str:
    result = value
    for index, literal in enumerate(literals):
        token = f"__rtm_sql_literal_{index:04d}__"
        if result.count(token) != 1:
            raise RuntimeError("Normalización SQL C1 perdió un literal")
        result = result.replace(token, literal)
    return result


def _normalize_function_body(value: str) -> str:
    protected, literals = _shield_sql_literals(value)
    normalized = " ".join(protected.split()).strip().lower()
    return _restore_sql_literals(normalized, literals)


def _strip_outer_parentheses(value: str) -> str:
    result = str(value).strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        encloses_all = True
        for index, character in enumerate(result):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(result) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        result = result[1:-1].strip()
    return result


def _canonical_sql_fragment(value: str | None) -> str | None:
    if value is None:
        return None
    protected, literals = _shield_sql_literals(value)
    normalized = " ".join(protected.replace('"', "").split()).lower()
    normalized = normalized.removesuffix(";").strip()
    normalized = re.sub(r"::(?:text|character varying)", "", normalized)
    normalized = re.sub(
        r"([a-z_][a-z0-9_]*)\s*=\s*any\s*\(\s*array\[(.*?)\]\s*\)",
        r"\1 in (\2)",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+asc\b", "", normalized)
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r"\(\s+", "(", normalized)
    normalized = re.sub(r"\s+\)", ")", normalized)
    return _restore_sql_literals(
        _strip_outer_parentheses(normalized),
        literals,
    )


def _canonical_catalog_index_key(value: str, option: int) -> str:
    """Combina pg_get_indexdef(columna) con pg_index.indoption.

    PostgreSQL omite ASC/DESC y NULLS FIRST/LAST en la variante por columna
    de pg_get_indexdef. Los bits congelados son DESC=1 y NULLS_FIRST=2.
    """

    normalized = _canonical_sql_fragment(value) or ""
    flags = int(option)
    if flags < 0 or flags & ~3:
        raise RuntimeError("pg_index.indoption C1 no reconocido")
    descending = bool(flags & 1)
    nulls_first = bool(flags & 2)
    if descending:
        normalized += " desc"
        if not nulls_first:
            normalized += " nulls last"
    elif nulls_first:
        normalized += " nulls first"
    return normalized


def _expected_c1_function_hashes() -> dict[str, str]:
    from rtm_connect.schema import connect_c1_ddl

    result: dict[str, str] = {}
    names = {
        "action_state_guard_function": "rtm_guard_connect_action_transition",
        "append_only_function": "rtm_guard_connect_append_only",
    }
    for ddl_name, statement in connect_c1_ddl():
        function_name = names.get(ddl_name)
        if function_name is None:
            continue
        try:
            body = statement.split("RETURNS TRIGGER AS $$", 1)[1]
            body = body.split("$$ LANGUAGE plpgsql", 1)[0]
        except (IndexError, AttributeError) as exc:
            raise RuntimeError("C1 function DDL no parseable") from exc
        normalized = _normalize_function_body(body)
        result[function_name] = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
    if set(result) != set(names.values()):
        raise RuntimeError("C1 function DDL incompleto")
    return result


def _expected_c1_indexes(
) -> dict[str, tuple[str, bool, tuple[str, ...], str | None]]:
    from rtm_connect.schema import CONNECT_C1_REQUIRED_INDEXES, connect_c1_ddl

    result: dict[
        str,
        tuple[str, bool, tuple[str, ...], str | None],
    ] = {}
    for _ddl_name, statement in connect_c1_ddl():
        match = _INDEX_DDL_RE.search(statement)
        if match is None:
            continue
        result[match.group("name")] = (
            match.group("table"),
            bool(match.group("unique")),
            tuple(
                _canonical_sql_fragment(value) or ""
                for value in match.group("columns").split(",")
            ),
            _canonical_sql_fragment(match.group("predicate")),
        )
    if set(result) != set(CONNECT_C1_REQUIRED_INDEXES):
        raise RuntimeError("C1 index DDL incompleto")
    return result


def _expected_c1_constraints() -> dict[str, tuple[str, str]]:
    from rtm_connect.schema import CONNECT_C1_REQUIRED_CONSTRAINTS, connect_c1_ddl

    result: dict[str, tuple[str, str]] = {}
    for _ddl_name, statement in connect_c1_ddl():
        table_match = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z0-9_]+)",
            statement,
            flags=re.IGNORECASE,
        )
        if table_match is None:
            continue
        table_name = table_match.group(1)
        for name in CONNECT_C1_REQUIRED_CONSTRAINTS:
            marker = re.search(
                rf"CONSTRAINT\s+{re.escape(name)}\s+CHECK\s*\(",
                statement,
                flags=re.IGNORECASE,
            )
            if marker is None:
                continue
            opening = statement.find("(", marker.start())
            depth = 0
            closing = None
            for index in range(opening, len(statement)):
                if statement[index] == "(":
                    depth += 1
                elif statement[index] == ")":
                    depth -= 1
                    if depth == 0:
                        closing = index
                        break
            if closing is None:
                raise RuntimeError("C1 constraint DDL no parseable")
            expression = _canonical_sql_fragment(
                statement[opening + 1:closing]
            )
            if expression is None:
                raise RuntimeError("C1 constraint DDL vacío")
            result[name] = (table_name, expression)
    if set(result) != set(CONNECT_C1_REQUIRED_CONSTRAINTS):
        raise RuntimeError("C1 constraint DDL incompleto")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def safety_blockers() -> list[str]:
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        return ["RTM_ENV_must_be_staging"]
    try:
        from rtm_connect.provider_sandbox_policy import assert_c6_staging_boundary
        assert_c6_staging_boundary()
        return []
    except Exception as exc:
        return [f"connect_c6_staging_boundary_blocked:{type(exc).__name__}:{exc}"]


def _column_snapshot(conn, requirements: dict[str, set[str]]) -> dict[str, Any]:
    from sqlalchemy import text

    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in requirements.items():
        present = {
            str(row[0])
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:table_name
                    """
                ),
                {"table_name": table_name},
            ).fetchall()
        }
        missing = sorted(required - present)
        tables[table_name] = {
            "exists": bool(present),
            "required_count": len(required),
            "present_required_count": len(required & present),
            "missing_columns": missing,
        }
        missing_total.extend(
            f"{table_name}.{column}" for column in missing
        )
    return {
        "tables": tables,
        "missing_columns": sorted(missing_total),
        "ready": not missing_total,
    }


def _c1_trigger_integrity(conn) -> dict[str, Any]:
    from sqlalchemy import text

    rows = {
        str(row["trigger_name"]): row
        for row in conn.execute(
            text(
                """
                SELECT t.tgname AS trigger_name,
                       c.relname AS table_name,
                       n.nspname AS table_schema,
                       p.proname AS function_name,
                       pn.nspname AS function_schema,
                       t.tgenabled AS enabled_mode,
                       t.tgtype AS trigger_type,
                       t.tgqual IS NULL AS unconditional,
                       ARRAY(
                           SELECT a.attname
                           FROM unnest(t.tgattr::smallint[]) WITH ORDINALITY
                                AS u(attnum, position)
                           JOIN pg_attribute a
                             ON a.attrelid=t.tgrelid
                            AND a.attnum=u.attnum
                           ORDER BY u.position
                       ) AS trigger_columns
                FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_proc p ON p.oid=t.tgfoid
                JOIN pg_namespace pn ON pn.oid=p.pronamespace
                WHERE NOT t.tgisinternal
                  AND n.nspname='public'
                  AND t.tgname IN (
                    'trg_rtm_connect_actions_state_guard',
                    'trg_rtm_connect_transitions_append_only',
                    'trg_rtm_connect_evidence_append_only',
                    'trg_rtm_connect_authorizations_immutable'
                  )
                """
            )
        ).mappings().all()
    }
    invalid: list[str] = []
    details: dict[str, Any] = {}
    for name, expected in C1_TRIGGER_BINDINGS.items():
        row = rows.get(name)
        actual = None if row is None else (
            str(row["table_name"]),
            str(row["function_name"]),
            int(row["trigger_type"]),
        )
        enabled = bool(
            row is not None and str(row["enabled_mode"]) in {"O", "A"}
        )
        schemas_exact = bool(
            row is not None
            and str(row["table_schema"]) == "public"
            and str(row["function_schema"]) == "public"
        )
        expected_columns = (
            ("status",)
            if name == "trg_rtm_connect_actions_state_guard" else ()
        )
        actual_columns = (
            tuple(str(value) for value in row["trigger_columns"])
            if row is not None else None
        )
        unconditional = bool(row is not None and row["unconditional"])
        valid = (
            actual == expected
            and enabled
            and schemas_exact
            and actual_columns == expected_columns
            and unconditional
        )
        details[name] = {
            "expected_table": expected[0],
            "expected_function": expected[1],
            "expected_type": expected[2],
            "actual": actual,
            "enabled": enabled,
            "schemas_exact": schemas_exact,
            "expected_columns": expected_columns,
            "actual_columns": actual_columns,
            "unconditional": unconditional,
            "valid": valid,
        }
        if not valid:
            invalid.append(name)
    return {
        "triggers": details,
        "invalid_triggers": sorted(invalid),
        "ready": not invalid,
    }


def _c1_object_integrity(conn) -> dict[str, Any]:
    """Congela cuerpo de guards y propiedades críticas de índices/checks."""

    from sqlalchemy import text

    expected_functions = _expected_c1_function_hashes()
    function_rows = {
        str(row["function_name"]): row
        for row in conn.execute(
            text(
                """
                SELECT p.proname AS function_name,
                       n.nspname AS function_schema,
                       l.lanname AS language_name,
                       p.pronargs, p.provolatile, p.prosecdef,
                       p.prorettype='trigger'::regtype AS returns_trigger,
                       p.prosrc AS function_body
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace
                JOIN pg_language l ON l.oid=p.prolang
                WHERE n.nspname='public'
                  AND p.proname IN (
                    'rtm_guard_connect_action_transition',
                    'rtm_guard_connect_append_only'
                  )
                """
            )
        ).mappings().all()
    }
    functions: dict[str, Any] = {}
    invalid_functions: list[str] = []
    for name, expected_hash in expected_functions.items():
        row = function_rows.get(name)
        actual_hash = None
        if row is not None:
            actual_hash = hashlib.sha256(
                _normalize_function_body(row["function_body"]).encode("utf-8")
            ).hexdigest()
        valid = bool(
            row is not None
            and str(row["function_schema"]) == "public"
            and str(row["language_name"]) == "plpgsql"
            and int(row["pronargs"]) == 0
            and str(row["provolatile"]) == "v"
            and not bool(row["prosecdef"])
            and bool(row["returns_trigger"])
            and actual_hash == expected_hash
        )
        functions[name] = {
            "expected_body_sha256": expected_hash,
            "actual_body_sha256": actual_hash,
            "valid": valid,
        }
        if not valid:
            invalid_functions.append(name)

    expected_indexes = _expected_c1_indexes()
    index_rows = {
        str(row["index_name"]): row
        for row in conn.execute(
            text(
                """
                SELECT i.relname AS index_name,
                       ni.nspname AS index_schema,
                       t.relname AS table_name,
                       nt.nspname AS table_schema,
                       x.indisunique, x.indisvalid, x.indisready,
                       ARRAY(
                           SELECT pg_get_indexdef(x.indexrelid, key_number, TRUE)
                           FROM generate_series(1, x.indnkeyatts) key_number
                           ORDER BY key_number
                       ) AS key_definitions,
                       ARRAY(
                           SELECT x.indoption[key_number - 1]::integer
                           FROM generate_series(1, x.indnkeyatts) key_number
                           ORDER BY key_number
                       ) AS key_options,
                       pg_get_expr(x.indpred, x.indrelid, TRUE) AS predicate
                FROM pg_index x
                JOIN pg_class i ON i.oid=x.indexrelid
                JOIN pg_namespace ni ON ni.oid=i.relnamespace
                JOIN pg_class t ON t.oid=x.indrelid
                JOIN pg_namespace nt ON nt.oid=t.relnamespace
                WHERE ni.nspname='public' AND nt.nspname='public'
                """
            )
        ).mappings().all()
    }
    indexes: dict[str, Any] = {}
    invalid_indexes: list[str] = []
    for name, (
        table_name,
        unique,
        expected_keys,
        expected_predicate,
    ) in expected_indexes.items():
        row = index_rows.get(name)
        key_definitions = (
            tuple(row["key_definitions"] or ())
            if row is not None else ()
        )
        key_options = (
            tuple(row["key_options"] or ())
            if row is not None else ()
        )
        actual_keys = None
        if row is not None and len(key_definitions) == len(key_options):
            actual_keys = tuple(
                _canonical_catalog_index_key(value, option)
                for value, option in zip(key_definitions, key_options)
            )
        actual_predicate = (
            _canonical_sql_fragment(row["predicate"])
            if row is not None else None
        )
        valid = bool(
            row is not None
            and str(row["index_schema"]) == "public"
            and str(row["table_schema"]) == "public"
            and str(row["table_name"]) == table_name
            and bool(row["indisunique"]) is unique
            and bool(row["indisvalid"])
            and bool(row["indisready"])
            and actual_keys == expected_keys
            and actual_predicate == expected_predicate
        )
        indexes[name] = {
            "expected_table": table_name,
            "expected_unique": unique,
            "expected_keys": expected_keys,
            "actual_keys": actual_keys,
            "actual_key_options": key_options if row is not None else None,
            "expected_predicate": expected_predicate,
            "actual_predicate": actual_predicate,
            "valid": valid,
        }
        if not valid:
            invalid_indexes.append(name)

    constraint_rows = {
        str(row["constraint_name"]): row
        for row in conn.execute(
            text(
                """
                SELECT c.conname AS constraint_name,
                       t.relname AS table_name,
                       n.nspname AS table_schema,
                       c.contype, c.convalidated,
                       pg_get_constraintdef(c.oid, TRUE) AS definition
                FROM pg_constraint c
                JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public'
                """
            )
        ).mappings().all()
    }
    constraints: dict[str, Any] = {}
    invalid_constraints: list[str] = []
    expected_constraints = _expected_c1_constraints()
    for name, (table_name, expected_expression) in expected_constraints.items():
        row = constraint_rows.get(name)
        actual_definition = (
            str(row["definition"])
            if row is not None else ""
        )
        if actual_definition.lower().startswith("check"):
            actual_definition = actual_definition[5:].strip()
        actual_expression = _canonical_sql_fragment(actual_definition)
        expected_hash = hashlib.sha256(
            expected_expression.encode("utf-8")
        ).hexdigest()
        actual_hash = (
            hashlib.sha256(actual_expression.encode("utf-8")).hexdigest()
            if actual_expression is not None else None
        )
        valid = bool(
            row is not None
            and str(row["table_schema"]) == "public"
            and str(row["table_name"]) == table_name
            and str(row["contype"]) == "c"
            and bool(row["convalidated"])
            and actual_expression == expected_expression
        )
        constraints[name] = {
            "expected_table": table_name,
            "expected_definition_sha256": expected_hash,
            "actual_definition_sha256": actual_hash,
            "valid": valid,
        }
        if not valid:
            invalid_constraints.append(name)
    ready = not (
        invalid_functions or invalid_indexes or invalid_constraints
    )
    return {
        "functions": functions,
        "indexes": indexes,
        "constraints": constraints,
        "invalid_functions": sorted(invalid_functions),
        "invalid_indexes": sorted(invalid_indexes),
        "invalid_constraints": sorted(invalid_constraints),
        "ready": ready,
    }


def schema_snapshot(conn) -> dict[str, Any]:
    from rtm_connect.provider_sandbox_schema import (
        CONNECT_C6_SCHEMA_CHANGES_REQUIRED,
        connect_c6_provider_ddl,
    )
    from scripts.rtm_staging_connect_c1_schema import (
        schema_snapshot as c1_schema_snapshot,
    )
    from scripts.rtm_staging_connect_c3_schema import (
        schema_snapshot as c3_schema_snapshot,
    )
    from scripts.rtm_staging_connect_c4_schema import (
        schema_snapshot as c4_schema_snapshot,
    )
    c1 = c1_schema_snapshot(conn)
    c3 = c3_schema_snapshot(conn)
    c4 = c4_schema_snapshot(conn)
    operator_dependencies = _column_snapshot(
        conn,
        C6_SMOKE_OPERATOR_COLUMNS,
    )
    c1_trigger_integrity = _c1_trigger_integrity(conn)
    c1_object_integrity = _c1_object_integrity(conn)
    ddl = connect_c6_provider_ddl()
    ready = (
        bool(c1["ready"])
        and bool(c3["ready"])
        and bool(c4["ready"])
        and bool(operator_dependencies["ready"])
        and bool(c1_trigger_integrity["ready"])
        and bool(c1_object_integrity["ready"])
        and not ddl
        and not CONNECT_C6_SCHEMA_CHANGES_REQUIRED
    )
    return {
        "c1": c1,
        "c3": c3,
        "c4": c4,
        "operator_dependencies": operator_dependencies,
        "c1_trigger_integrity": c1_trigger_integrity,
        "c1_object_integrity": c1_object_integrity,
        "schema_changes_required": CONNECT_C6_SCHEMA_CHANGES_REQUIRED,
        "ddl_statement_count": len(ddl),
        "ready": ready,
    }


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        default=str,
    ))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_connect_c6_schema",
        "version": SCHEMA_AUDIT_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "apply_available": False,
        "schema_changes_required": False,
        "applied": [],
        "destructive": False,
        "routes_modified": False,
        "connectors_seeded": False,
        "external_effects_executed": False,
        "blockers": [],
    }
    report["blockers"].extend(safety_blockers())
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.provider_sandbox_schema import (
            RTM_CONNECT_C6_PROVIDER_SCHEMA_VERSION,
        )
        from rtm_connect.provider_sandbox_policy import (
            assert_c6_database_identity,
            assert_c6_staging_boundary,
        )
        boundary = assert_c6_staging_boundary()
        with get_engine().connect() as conn:
            report["connected_database"] = assert_c6_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            before = schema_snapshot(conn)
            after = schema_snapshot(conn)
            migration_count = int(conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_management_schema_migrations
                    WHERE name=:name
                    """
                ),
                {"name": RTM_CONNECT_C6_PROVIDER_SCHEMA_VERSION},
            ).scalar_one())
        report["before"] = before
        report["after"] = after
        report["c6_migration_registered"] = migration_count != 0
        report["unchanged"] = before == after
        if not after["ready"]:
            report["blockers"].append("connect_c6_dependencies_not_ready")
        if migration_count:
            report["blockers"].append("unexpected_connect_c6_migration_registered")
        report["safe"] = not report["blockers"] and report["unchanged"]
        report["ok"] = bool(report["safe"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
    _print(report, args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
