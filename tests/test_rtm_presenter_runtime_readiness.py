from __future__ import annotations

import unittest
from typing import Any

from rtm_presenter_schema import (
    PRESENTER_REQUIRED_COLUMN_TYPES,
    PRESENTER_REQUIRED_CONSTRAINTS,
    PRESENTER_REQUIRED_CONSTRAINT_TABLES,
    PRESENTER_REQUIRED_FUNCTIONS,
    PRESENTER_REQUIRED_INDEXES,
    PRESENTER_REQUIRED_INDEX_TABLES,
    PRESENTER_REQUIRED_TRIGGERS,
    PRESENTER_REQUIRED_TRIGGER_BINDINGS,
    RTM_PRESENTER_SCHEMA_VERSION,
)
from rtm_presenter_service import SqlPresenterRepository
from scripts.rtm_staging_presenter_schema import (
    PRESENTER_SCHEMA_SCRIPT_VERSION,
    schema_contract,
)


def _migration_metadata() -> dict[str, Any]:
    return {
        "source": PRESENTER_SCHEMA_SCRIPT_VERSION,
        "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
        "schema_contract_sha256": schema_contract()["sha256"],
        "scope": "staging_isolated_synthetic_schema_only",
        "synthetic_only": True,
        "real_data_allowed": False,
        "profiles_seeded": False,
        "documents_seeded": False,
        "cases_seeded": False,
        "operators_seeded": False,
        "b2_used": False,
        "external_effects": False,
        "destructive": False,
    }


class _Result:
    def __init__(self, *, rows=(), scalar=None) -> None:
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


class _ReadyCatalogConnection:
    def __init__(self) -> None:
        self.indexes = set(PRESENTER_REQUIRED_INDEXES)
        self.triggers = set(PRESENTER_REQUIRED_TRIGGERS)
        self.constraints = set(PRESENTER_REQUIRED_CONSTRAINTS)
        self.metadata = _migration_metadata()
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        del parameters
        sql = str(statement)
        self.statements.append(sql)
        normalized = " ".join(sql.split()).lower()
        if "from information_schema.columns" in normalized:
            return _Result(
                rows=(
                    {
                        "table_name": table,
                        "column_name": column,
                        "type_name": type_name,
                    }
                    for table, columns
                    in PRESENTER_REQUIRED_COLUMN_TYPES.items()
                    for column, type_name in columns.items()
                )
            )
        if "from pg_index" in normalized:
            return _Result(
                rows=(
                    {
                        "object_name": name,
                        "table_name": PRESENTER_REQUIRED_INDEX_TABLES[name],
                        "is_unique": name.startswith("uq_"),
                    }
                    for name in self.indexes
                )
            )
        if "from pg_trigger" in normalized:
            return _Result(
                rows=(
                    {
                        "object_name": name,
                        "table_name": (
                            PRESENTER_REQUIRED_TRIGGER_BINDINGS[name][0]
                        ),
                        "function_name": (
                            PRESENTER_REQUIRED_TRIGGER_BINDINGS[name][1]
                        ),
                    }
                    for name in self.triggers
                )
            )
        if "from pg_constraint" in normalized:
            return _Result(
                rows=(
                    {
                        "object_name": name,
                        "table_name": (
                            PRESENTER_REQUIRED_CONSTRAINT_TABLES[name]
                        ),
                        "constraint_type": "c",
                    }
                    for name in self.constraints
                )
            )
        if "from pg_proc function_state" in normalized:
            return _Result(
                rows=(
                    {
                        "object_name": name,
                        "return_type": "trigger",
                        "language_name": "plpgsql",
                        "argument_count": 0,
                    }
                    for name in PRESENTER_REQUIRED_FUNCTIONS
                )
            )
        if "from rtm_management_schema_migrations" in normalized:
            return _Result(scalar=self.metadata)
        raise AssertionError(f"Consulta de readiness inesperada: {sql}")


class RTMPresenterRuntimeReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = SqlPresenterRepository()
        self.connection = _ReadyCatalogConnection()

    def test_complete_migrated_contract_is_ready_using_selects_only(self):
        self.assertTrue(
            self.repository.presenter_schema_ready(self.connection)
        )
        self.assertTrue(self.connection.statements)
        for statement in self.connection.statements:
            self.assertTrue(statement.lstrip().upper().startswith("SELECT"))
            self.assertNotIn("CREATE ", statement.upper())
            self.assertNotIn("ALTER ", statement.upper())

    def test_missing_catalog_object_fails_closed(self):
        self.connection.triggers.remove(next(iter(PRESENTER_REQUIRED_TRIGGERS)))
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_wrong_column_type_fails_closed(self):
        original_execute = self.connection.execute

        def execute_with_wrong_type(statement, parameters=None):
            result = original_execute(statement, parameters)
            if "from information_schema.columns" in " ".join(
                str(statement).split()
            ).lower():
                rows = result.all()
                rows[0] = {**rows[0], "type_name": "text"}
                return _Result(rows=rows)
            return result

        self.connection.execute = execute_with_wrong_type
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_trigger_on_wrong_table_or_function_fails_closed(self):
        original_execute = self.connection.execute

        def execute_with_wrong_binding(statement, parameters=None):
            result = original_execute(statement, parameters)
            if "from pg_trigger" in " ".join(str(statement).split()).lower():
                rows = result.all()
                rows[0] = {**rows[0], "function_name": "other_guard"}
                return _Result(rows=rows)
            return result

        self.connection.execute = execute_with_wrong_binding
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_index_on_wrong_table_fails_closed(self):
        original_execute = self.connection.execute

        def execute_with_wrong_binding(statement, parameters=None):
            result = original_execute(statement, parameters)
            if "from pg_index" in " ".join(str(statement).split()).lower():
                rows = result.all()
                rows[0] = {**rows[0], "table_name": "other_table"}
                return _Result(rows=rows)
            return result

        self.connection.execute = execute_with_wrong_binding
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_constraint_on_wrong_table_or_type_fails_closed(self):
        original_execute = self.connection.execute

        def execute_with_wrong_binding(statement, parameters=None):
            result = original_execute(statement, parameters)
            if "from pg_constraint" in " ".join(
                str(statement).split()
            ).lower():
                rows = result.all()
                rows[0] = {**rows[0], "constraint_type": "u"}
                return _Result(rows=rows)
            return result

        self.connection.execute = execute_with_wrong_binding
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_wrong_function_contract_fails_closed(self):
        original_execute = self.connection.execute

        def execute_with_wrong_function(statement, parameters=None):
            result = original_execute(statement, parameters)
            if "from pg_proc function_state" in " ".join(
                str(statement).split()
            ).lower():
                rows = result.all()
                rows[0] = {**rows[0], "return_type": "void"}
                return _Result(rows=rows)
            return result

        self.connection.execute = execute_with_wrong_function
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_migration_contract_mismatch_fails_closed(self):
        self.connection.metadata = {
            **self.connection.metadata,
            "schema_contract_sha256": "0" * 64,
        }
        self.assertFalse(
            self.repository.presenter_schema_ready(self.connection)
        )

    def test_catalog_error_fails_closed(self):
        class _BrokenConnection:
            def execute(self, statement, parameters=None):
                del statement, parameters
                raise RuntimeError("catalog unavailable")

        self.assertFalse(
            self.repository.presenter_schema_ready(_BrokenConnection())
        )


if __name__ == "__main__":
    unittest.main()
