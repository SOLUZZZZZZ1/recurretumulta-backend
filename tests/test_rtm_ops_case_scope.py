from __future__ import annotations

import ast
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# El contrato debe poder probarse en el Python mínimo del repositorio.
try:  # pragma: no cover - depende de la imagen que ejecuta la suite
    from fastapi import HTTPException
except ModuleNotFoundError:  # pragma: no cover - ejercitado en CI mínimo
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

    class Request:
        pass

    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.Request = Request
    sys.modules["fastapi"] = fastapi_stub

try:  # pragma: no cover - depende de la imagen que ejecuta la suite
    import sqlalchemy  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - ejercitado en CI mínimo
    sqlalchemy_stub = types.ModuleType("sqlalchemy")
    sqlalchemy_stub.text = lambda value: value
    sys.modules["sqlalchemy"] = sqlalchemy_stub

try:  # pragma: no cover - depende de la imagen que ejecuta la suite
    import database  # noqa: F401
except (ModuleNotFoundError, ImportError):  # pragma: no cover - CI mínimo
    database_stub = types.ModuleType("database")

    def _unexpected_engine():
        raise AssertionError("El test no debe abrir la base real")

    database_stub.get_engine = _unexpected_engine
    sys.modules["database"] = database_stub

from rtm_core import ops_case_scope


ROOT = Path(__file__).resolve().parents[1]
OPERATOR_ID = "11111111-1111-4111-8111-111111111111"
CASE_ID = "22222222-2222-4222-8222-222222222222"


def _request(
    *,
    role_code: str = "rtm.operator",
    permissions: tuple[str, ...] = ("ops.view",),
):
    return SimpleNamespace(
        state=SimpleNamespace(
            rtm_operator_context=SimpleNamespace(
                operator_id=OPERATOR_ID,
                session_id="33333333-3333-4333-8333-333333333333",
                role_code=role_code,
                permissions=permissions,
            )
        )
    )


class _Result:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value

    def fetchone(self):
        return self._scalar_value


class _Connection:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return _Result(self.scalar_value)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _function_source(relative: str, function_name: str) -> str:
    source = _source(relative)
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == function_name
    )
    return ast.get_source_segment(source, node) or ""


def _scope_is_interpolated_in_limited_sql(relative: str, function_name: str) -> bool:
    source = _source(relative)
    tree = ast.parse(source)
    function = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == function_name
    )
    for call in ast.walk(function):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "text"
            and call.args
        ):
            continue
        expression = call.args[0]
        names = {
            node.id for node in ast.walk(expression) if isinstance(node, ast.Name)
        }
        literals = " ".join(
            str(node.value)
            for node in ast.walk(expression)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        if "scope_sql" in names and "LIMIT :limit" in literals:
            return True
    return False


def _calls_in_transaction(relative: str, function_name: str) -> set[str]:
    source = _source(relative)
    tree = ast.parse(source)
    function = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == function_name
    )
    calls: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        contexts = [
            item.context_expr
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
        ]
        if not any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "begin"
            for call in contexts
        ):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name):
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.add(child.func.attr)
    return calls


class OpsCaseScopePolicyTest(unittest.TestCase):
    def test_operator_scope_is_a1s_membership_and_assignment_bound(self):
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            scope = ops_case_scope.load_ops_case_scope(_request())

        self.assertFalse(scope.scope_all)
        self.assertEqual(scope.role_code, "rtm.operator")
        self.assertEqual(scope.operator_id, OPERATOR_ID)

    def test_only_exact_supervisor_with_permission_sees_all(self):
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            supervisor = ops_case_scope.load_ops_case_scope(
                _request(
                    role_code="rtm.supervisor",
                    permissions=("ops.view", "ops.supervise"),
                )
            )
            with self.assertRaises(HTTPException) as missing_permission:
                ops_case_scope.load_ops_case_scope(
                    _request(role_code="rtm.supervisor", permissions=("ops.view",))
                )

        self.assertTrue(supervisor.scope_all)
        self.assertEqual(missing_permission.exception.status_code, 403)

    def test_signer_with_ops_view_does_not_open_general_ops(self):
        with (
            patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True),
            self.assertRaises(HTTPException) as denied,
        ):
            ops_case_scope.load_ops_case_scope(
                _request(
                    role_code="rtm.signer",
                    permissions=("ops.view", "presenter.signing.queue"),
                )
            )
        self.assertEqual(denied.exception.status_code, 403)

    def test_role_match_is_exact(self):
        with (
            patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True),
            self.assertRaises(HTTPException) as denied,
        ):
            ops_case_scope.load_ops_case_scope(
                _request(
                    role_code="RTM.SUPERVISOR",
                    permissions=("ops.view", "ops.supervise"),
                )
            )
        self.assertEqual(denied.exception.status_code, 403)

    def test_staging_without_trusted_context_fails_closed(self):
        request = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True),
            self.assertRaises(HTTPException) as denied,
        ):
            ops_case_scope.load_ops_case_scope(request)
        self.assertEqual(denied.exception.status_code, 401)

    def test_invalid_operator_identity_fails_closed(self):
        request = _request()
        request.state.rtm_operator_context.operator_id = "not-a-uuid"
        with (
            patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True),
            self.assertRaises(HTTPException) as denied,
        ):
            ops_case_scope.load_ops_case_scope(request)
        self.assertEqual(denied.exception.status_code, 401)

    def test_exact_case_denial_is_indistinguishable_404(self):
        scope = ops_case_scope.OpsCaseScope(
            operator_id=OPERATOR_ID,
            role_code="rtm.operator",
            permissions=("ops.view",),
            scope_all=False,
            individual_session=True,
        )
        connection = _Connection(scalar_value=None)

        with self.assertRaises(HTTPException) as denied:
            ops_case_scope.require_case_in_scope(
                connection,
                scope=scope,
                case_id=CASE_ID,
            )

        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(len(connection.calls), 1)
        sql, params = connection.calls[0]
        normalized = " ".join(sql.lower().split())
        for fragment in (
            "coalesce(c.test_mode, false) = true",
            "rtm_ops_binding.status = 'active'",
            "rtm_ops_binding.synthetic_only = true",
            "rtm_ops_binding.revoked_at is null",
            "rtm_ops_tenant.status = 'active'",
            "rtm_ops_tenant.synthetic_only = true",
            "rtm_ops_membership.status = 'active'",
            "rtm_ops_membership.synthetic_only = true",
            "rtm_ops_membership.revoked_at is null",
            "rtm_ops_assignment.attention_item_id is null",
            "rtm_ops_assignment.status = 'active'",
            "rtm_ops_assignment.accepted_at is not null",
            "rtm_ops_assignment.released_at is null",
            "'responsible', 'reviewer', 'supervisor'",
            "rtm_ops_assignment.metadata @>",
            "rtm_presenter_synthetic_only",
        ):
            self.assertIn(fragment, normalized)
        self.assertIn(
            "for update of c, rtm_ops_binding, rtm_ops_tenant, "
            "rtm_ops_membership, rtm_ops_assignment",
            normalized,
        )
        self.assertNotIn("select exists", normalized)
        self.assertEqual(params["rtm_ops_operator_id"], OPERATOR_ID)
        self.assertNotIn("rtm_ops_scope_all", params)

    def _operator_scope_sql_pair(self):
        scope = ops_case_scope.OpsCaseScope(
            operator_id=OPERATOR_ID,
            role_code="rtm.operator",
            permissions=("ops.view",),
            scope_all=False,
            individual_session=True,
        )
        list_clause, _ = ops_case_scope.ops_case_scope_filter(scope)
        connection = _Connection(scalar_value=(CASE_ID,))
        ops_case_scope.require_case_in_scope(
            connection,
            scope=scope,
            case_id=CASE_ID,
        )
        exact_sql, _ = connection.calls[0]
        return tuple(
            " ".join(sql.lower().split()) for sql in (list_clause, exact_sql)
        )

    def test_revoked_a1s_binding_removes_case_from_lists_and_exact_reads(self):
        for sql in self._operator_scope_sql_pair():
            self.assertIn("rtm_connect_a1s_case_bindings", sql)
            self.assertIn("rtm_ops_binding.status = 'active'", sql)
            self.assertIn("rtm_ops_binding.synthetic_only = true", sql)
            self.assertIn("rtm_ops_binding.revoked_at is null", sql)
            self.assertIn('"test_mode":true', sql)

    def test_suspended_a1s_tenant_removes_case_from_lists_and_exact_reads(self):
        for sql in self._operator_scope_sql_pair():
            self.assertIn("rtm_connect_a1s_tenants", sql)
            self.assertIn("rtm_ops_tenant.status = 'active'", sql)
            self.assertIn("rtm_ops_tenant.synthetic_only = true", sql)
            self.assertIn("rtm_a1s_synthetic_only", sql)

    def test_revoked_a1s_membership_removes_case_from_lists_and_exact_reads(self):
        for sql in self._operator_scope_sql_pair():
            self.assertIn("rtm_connect_a1s_memberships", sql)
            self.assertIn("rtm_ops_membership.status = 'active'", sql)
            self.assertIn("rtm_ops_membership.synthetic_only = true", sql)
            self.assertIn("rtm_ops_membership.revoked_at is null", sql)
            self.assertIn(
                "rtm_ops_membership.operator_id = "
                "cast(:rtm_ops_operator_id as uuid)",
                sql,
            )

    def test_unmarked_assignment_never_grants_presenter_scope(self):
        for sql in self._operator_scope_sql_pair():
            self.assertIn("rtm_ops_assignment.metadata @>", sql)
            self.assertIn(
                '"synthetic_marker":"rtm_presenter_synthetic_only"',
                sql,
            )
            self.assertIn('"synthetic_only":true', sql)

    def test_supervisor_exact_case_locks_case_without_assignment_join(self):
        scope = ops_case_scope.OpsCaseScope(
            operator_id=OPERATOR_ID,
            role_code="rtm.supervisor",
            permissions=("ops.view", "ops.supervise"),
            scope_all=True,
            individual_session=True,
        )
        connection = _Connection(scalar_value=(CASE_ID,))

        result = ops_case_scope.require_case_in_scope(
            connection,
            scope=scope,
            case_id=CASE_ID,
        )

        self.assertEqual(result, CASE_ID)
        sql, params = connection.calls[0]
        normalized = " ".join(sql.lower().split())
        self.assertIn("for update of c", normalized)
        self.assertNotIn("rtm_work_assignments", normalized)
        self.assertEqual(params, {"rtm_ops_case_id": CASE_ID})

    def test_invalid_case_uuid_returns_404_without_database_query(self):
        scope = ops_case_scope.OpsCaseScope(
            operator_id=OPERATOR_ID,
            role_code="rtm.operator",
            permissions=("ops.view",),
            scope_all=False,
            individual_session=True,
        )
        connection = _Connection(scalar_value=True)
        with self.assertRaises(HTTPException) as denied:
            ops_case_scope.require_case_in_scope(
                connection,
                scope=scope,
                case_id="not-a-uuid",
            )
        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(connection.calls, [])

    def test_legacy_filter_has_no_management_schema_reference_or_params(self):
        with patch.dict(os.environ, {"RTM_ENV": "production"}, clear=True):
            scope = ops_case_scope.load_ops_case_scope(
                SimpleNamespace(state=SimpleNamespace())
            )
        clause, params = ops_case_scope.ops_case_scope_filter(scope)

        self.assertFalse(scope.individual_session)
        self.assertEqual(clause, "TRUE")
        self.assertEqual(params, {})
        for table in (
            "rtm_work_assignments",
            "rtm_connect_a1s_case_bindings",
            "rtm_connect_a1s_tenants",
            "rtm_connect_a1s_memberships",
        ):
            self.assertNotIn(table, clause)

    def test_misidentified_staging_never_receives_legacy_global_scope(self):
        environments = (
            {
                "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            },
            {
                "RTM_ENV": "production",
                "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            },
            {
                "RTM_ENV": "stagin",
                "RTM_DATA_NAMESPACE": "rtm_staging",
            },
            {
                "RTM_ENV": "production",
                "RENDER_SERVICE_NAME": "recurretumulta-rtm-staging",
            },
        )
        for environment in environments:
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
                self.assertRaises(HTTPException) as denied,
            ):
                ops_case_scope.load_ops_case_scope(
                    SimpleNamespace(state=SimpleNamespace())
                )
            self.assertEqual(denied.exception.status_code, 503)

    def test_staging_filter_keeps_assignment_contract_and_params(self):
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            scope = ops_case_scope.load_ops_case_scope(_request())
        clause, params = ops_case_scope.ops_case_scope_filter(scope)

        self.assertIn("rtm_work_assignments", clause)
        self.assertIn("rtm_connect_a1s_case_bindings", clause)
        self.assertIn("rtm_connect_a1s_tenants", clause)
        self.assertIn("rtm_connect_a1s_memberships", clause)
        self.assertEqual(params["rtm_ops_operator_id"], OPERATOR_ID)
        self.assertFalse(params["rtm_ops_scope_all"])

    def test_legacy_exact_scope_preserves_identifier_without_database(self):
        legacy_scope = ops_case_scope.OpsCaseScope(
            operator_id=OPERATOR_ID,
            role_code="legacy.operator",
            permissions=("ops.view",),
            scope_all=True,
            individual_session=False,
        )
        connection = _Connection(scalar_value=True)

        result = ops_case_scope.require_case_in_scope(
            connection,
            scope=legacy_scope,
            case_id="legacy-id-not-normalized",
        )

        self.assertEqual(result, "legacy-id-not-normalized")
        self.assertEqual(connection.calls, [])

    def test_legacy_dependency_never_opens_database(self):
        request = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.dict(os.environ, {"RTM_ENV": "production"}, clear=True),
            patch.object(ops_case_scope, "get_engine") as get_engine,
        ):
            scope = ops_case_scope.require_current_case_scope(
                request,
                "legacy-id-not-normalized",
            )

        self.assertFalse(scope.individual_session)
        get_engine.assert_not_called()


class OpsCaseScopeWiringContractTest(unittest.TestCase):
    def test_required_lists_apply_scope_before_limit(self):
        targets = (
            ("ops.py", "queue"),
            ("ops.py", "list_all_followups"),
            ("ops.py", "list_due_followups"),
            ("ops_queue_smart.py", "queue_smart"),
            ("ops_vehicle_removal_router.py", "list_vehicle_removals"),
        )
        for relative, function_name in targets:
            with self.subTest(relative=relative, function=function_name):
                source = _function_source(relative, function_name)
                self.assertIn("ops_case_scope_filter", source)
                self.assertIn("scope_sql", source)
                self.assertNotIn("OPS_CASE_SCOPE_SQL", source)
                self.assertIn("LIMIT :limit", source)
                self.assertLess(
                    source.index("scope_sql"),
                    source.rindex("LIMIT :limit"),
                )
                self.assertTrue(
                    _scope_is_interpolated_in_limited_sql(relative, function_name),
                    "El scope debe formar parte de la expresión SQL, no del literal",
                )

    def test_presented_case_lists_are_also_assignment_scoped(self):
        for function_name in ("list_presented_cases_safe", "list_presented_cases"):
            with self.subTest(function=function_name):
                source = _function_source("ops.py", function_name)
                self.assertIn("ops_case_scope_filter", source)
                self.assertIn("scope_sql", source)
                self.assertNotIn("OPS_CASE_SCOPE_SQL", source)
                self.assertLess(
                    source.index("scope_sql"),
                    source.rindex("LIMIT :limit"),
                )
                self.assertTrue(
                    _scope_is_interpolated_in_limited_sql("ops.py", function_name)
                )

    def test_lists_keep_legacy_token_check_before_scope_and_database(self):
        targets = (
            ("ops.py", "queue", "_require_operator"),
            ("ops.py", "list_all_followups", "_require_operator"),
            ("ops.py", "list_due_followups", "_require_operator"),
            ("ops.py", "list_presented_cases_safe", "_require_operator"),
            ("ops.py", "list_presented_cases", "_require_operator"),
            ("ops_queue_smart.py", "queue_smart", "_require_operator"),
            (
                "ops_vehicle_removal_router.py",
                "list_vehicle_removals",
                "_require_operator",
            ),
        )
        for relative, function_name, auth_call in targets:
            with self.subTest(relative=relative, function=function_name):
                source = _function_source(relative, function_name)
                self.assertLess(source.index(auth_call), source.index("load_ops_case_scope"))
                self.assertLess(source.index("load_ops_case_scope"), source.index("get_engine"))

    def test_every_legacy_ops_case_route_has_exact_scope_dependency(self):
        source = _source("ops.py")
        tree = ast.parse(source)
        routes: list[tuple[str, str]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                path_arg = decorator.args[0]
                if not isinstance(path_arg, ast.Constant) or not isinstance(path_arg.value, str):
                    continue
                if "{case_id}" in path_arg.value:
                    routes.append((path_arg.value, ast.unparse(decorator)))

        self.assertTrue(routes)
        for path, decorator in routes:
            with self.subTest(path=path):
                self.assertIn("Depends(require_current_case_scope)", decorator)

    def test_operator_router_installs_exact_case_dependency(self):
        self.assertIn(
            "Depends(require_current_case_scope)",
            _source("ops_operator_router.py"),
        )

    def test_workspace_and_payment_reads_scope_inside_read_transaction(self):
        relative = os.path.join("rtm_core", "workspace_router.py")
        for function_name in ("get_case_workspace", "get_case_payment_status"):
            with self.subTest(function=function_name):
                calls = _calls_in_transaction(relative, function_name)
                self.assertIn("load_ops_case_scope", calls)
                self.assertIn("require_case_in_scope", calls)

        payment_source = _function_source(relative, "get_case_payment_status")
        self.assertIn("SELECT COALESCE(payment_status, '')", payment_source)
        for forbidden in (
            "stripe_session_id",
            "stripe_payment_intent",
            "x_case_token",
            "require_case_or_operator_access",
            "contact_email",
            "interested_data",
            "authorized",
            "b2_bucket",
            "b2_key",
        ):
            self.assertNotIn(forbidden, payment_source)

    def test_every_exact_allowlisted_read_rechecks_scope_in_read_transaction(self):
        targets = (
            ("ops.py", "list_documents", "_require_operator"),
            ("ops.py", "list_events", "_require_operator"),
            ("ops.py", "list_case_followups", "_require_operator"),
            ("ops_operator_router.py", "get_case_detail", "require_operator_token"),
            ("ops_operator_router.py", "get_ai_overrides", "require_operator_token"),
            (
                "ops_vehicle_removal_router.py",
                "get_vehicle_removal",
                "_require_operator",
            ),
            (
                os.path.join("rtm_core", "workspace_router.py"),
                "get_case_workspace",
                "require_operator_token",
            ),
            (
                os.path.join("rtm_core", "workspace_router.py"),
                "get_case_payment_status",
                "require_operator_token",
            ),
        )
        for relative, function_name, auth_call in targets:
            with self.subTest(relative=relative, function=function_name):
                calls = _calls_in_transaction(relative, function_name)
                self.assertIn("load_ops_case_scope", calls)
                self.assertIn("require_case_in_scope", calls)
                source = _function_source(relative, function_name)
                self.assertLess(source.index(auth_call), source.index("get_engine"))

    def test_permitted_mutations_recheck_scope_inside_write_transaction(self):
        targets = (
            ("ops.py", "upload_justificante"),
            ("ops.py", "register_manual_submission"),
            ("ops.py", "create_case_followup"),
            ("ops.py", "resolve_case_followup"),
            ("ops.py", "restore_real_case"),
            ("ops.py", "rebuild_followups"),
            ("ops_operator_router.py", "send_to_manual_review"),
            ("ops_operator_router.py", "add_operator_note"),
            ("ops_vehicle_removal_router.py", "mark_vehicle_paid"),
            ("ops_vehicle_removal_router.py", "assign_vehicle_removal"),
            ("ops_vehicle_removal_router.py", "complete_vehicle_removal"),
            ("ops_vehicle_removal_router.py", "add_vehicle_removal_note"),
        )
        for relative, function_name in targets:
            with self.subTest(relative=relative, function=function_name):
                calls = _calls_in_transaction(relative, function_name)
                self.assertIn("load_ops_case_scope", calls)
                self.assertIn("require_case_in_scope", calls)

    def test_direct_document_download_remains_uniformly_blocked(self):
        source = _function_source("ops.py", "download_document")
        self.assertIn("status_code=403", source)
        self.assertNotIn("get_engine", source)
        self.assertNotIn("download_bytes", source)

    def test_non_atomic_legacy_mutators_are_shadowed_by_410_guards(self):
        guard = _source(os.path.join("rtm_core", "legacy_guard_router.py"))
        for path in (
            "/ops/cases/{case_id}/reanalyze",
            "/ops/cases/{case_id}/final-resource",
            "/ops/cases/{case_id}/finalize-resource",
            "/ops/cases/{case_id}/send-complete",
            "/ops/cases/{case_id}/save-ai-overrides",
            "/ops/cases/{case_id}/approve",
            "/ops/cases/{case_id}/override-family",
            "/ops/cases/{case_id}/override-family-and-regenerate",
            "/ops/cases/{case_id}/rewrite-hecho-and-regenerate",
            "/ops/cases/{case_id}/submit",
            "/ops/cases/{case_id}/mark-submitted",
            "/ops/cases/{case_id}/force-ready-to-submit",
            "/ops/cases/{case_id}/lab-force-ready-to-submit",
            "/ops/cases/{case_id}/lab-force-authorize",
            "/ops/cases/{case_id}/lab-force-paid",
        ):
            with self.subTest(path=path):
                self.assertIn(f'@router.post("{path}")', guard)


if __name__ == "__main__":
    unittest.main()
