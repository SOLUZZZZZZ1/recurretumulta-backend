from __future__ import annotations

import asyncio
import json
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.requests import Request
from starlette.responses import JSONResponse
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as backend_app
import ops_restaurant_reservations as restaurant_routes
from rtm_core import legacy_ops_session_bridge as bridge


OPERATOR_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
BEARER = "b" * 48
DEVICE = "d" * 32
LEGACY_TOKEN = "legacy-secret-that-never-leaves-the-server"
SHARED_ADMIN_TOKEN = "shared-admin-secret"
CASE_ID = "33333333-3333-4333-8333-333333333333"


def _request(
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (name.casefold().encode("ascii"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "headers": raw_headers,
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "scheme": "https",
            "http_version": "1.1",
        }
    )


class _Engine:
    def __init__(self, connection: object):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


class _RestaurantResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _RestaurantConnection:
    def __init__(self):
        self.statements: list[tuple[str, dict | None]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append((sql, parameters))
        if "SELECT id FROM restaurants" in sql:
            return _RestaurantResult(("rest_007",))
        return _RestaurantResult()


def _session(
    *permissions: str,
    must_change_password: bool = False,
    mfa_required: bool = False,
    role_code: str = "rtm.operator",
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        operator_id=OPERATOR_ID,
        session_id=SESSION_ID,
        role_code=role_code,
        permissions=permissions,
        must_change_password=must_change_password,
        mfa_required=mfa_required,
        login_at=now - timedelta(hours=1),
        last_verified_at=now - timedelta(seconds=30),
    )


def _response_body(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


class LegacyOpsPathAuditTest(unittest.TestCase):
    def test_legacy_surfaces_are_covered(self):
        for path in (
            "/ops",
            "/ops/queue",
            "/ops/queue-smart",
            "/ops/cases/case-id",
            "/ops/core/cases/case-id/workspace",
            "/ops/automation/tick",
            "/ops/vehicle-removal",
            "/ai/expediente/run",
        ):
            with self.subTest(path=path):
                self.assertTrue(bridge.is_legacy_ops_path(path))

    def test_surfaces_with_own_controls_are_not_intercepted(self):
        for path in (
            "/ops/auth/login",
            "/ops/admin/operators",
            f"/ops/admin/operators/{OPERATOR_ID}",
            f"/ops/admin/operators/{OPERATOR_ID}/sessions",
            f"/ops/admin/operators/{OPERATOR_ID}/credentials/rotate",
            f"/ops/admin/sessions/{SESSION_ID}/revoke",
            "/ops/admin/lifecycle/status",
            "/ops/presenter/cases/case-id/documents",
            "/ops/connect/supervisor/overview",
            "/ops/connect/human-filings",
            "/ops/restaurant-reservations",
            "/ops/restaurants/change-pin",
        ):
            with self.subTest(path=path):
                self.assertFalse(bridge.is_legacy_ops_path(path))

    def test_prefix_matching_is_segment_safe(self):
        self.assertTrue(bridge.is_legacy_ops_path("/ops/authentic-report"))
        self.assertFalse(bridge.is_legacy_ops_path("/ops/auth/me/"))
        self.assertFalse(bridge.is_legacy_ops_path("/not-ops/queue"))

    def test_unknown_admin_routes_and_restaurant_creation_use_bridge(self):
        for path in (
            "/ops/admin/restaurants/create",
            "/ops/admin/future-control",
            "/ops/admin/status-extra",
            "/ops/admin/operators/not-a-uuid",
            f"/ops/admin/operators/{OPERATOR_ID}/future-control",
            f"/ops/admin/sessions/{SESSION_ID}/revoke/again",
        ):
            with self.subTest(path=path):
                self.assertTrue(bridge.is_legacy_ops_path(path))

    def test_only_automation_requires_supervisor(self):
        self.assertTrue(
            bridge.legacy_ops_requires_supervisor("/ops/automation/tick")
        )
        self.assertFalse(bridge.legacy_ops_requires_supervisor("/ops/queue"))

    def test_vehicle_mark_paid_matcher_is_method_and_segment_safe(self):
        self.assertTrue(
            bridge.is_retired_vehicle_mark_paid(
                f"/ops/vehicle-removal/{CASE_ID}/mark-paid",
                "POST",
            )
        )
        self.assertTrue(
            bridge.is_retired_vehicle_mark_paid(
                f"/ops/vehicle-removal/{CASE_ID}/mark-paid/",
                "post",
            )
        )
        for case_segment in (
            CASE_ID.replace("-", "").upper(),
            "NOT-A-UUID",
        ):
            with self.subTest(case_segment=case_segment):
                self.assertTrue(
                    bridge.is_retired_vehicle_mark_paid(
                        f"/ops/vehicle-removal/{case_segment}/mark-paid",
                        "POST",
                    )
                )
        for path, method in (
            (f"/ops/vehicle-removal/{CASE_ID}/mark-paid", "GET"),
            (f"/ops/vehicle-removal/{CASE_ID}/mark-paid/again", "POST"),
            (f"/ops/vehicle-removal-extra/{CASE_ID}/mark-paid", "POST"),
        ):
            with self.subTest(path=path, method=method):
                self.assertFalse(
                    bridge.is_retired_vehicle_mark_paid(path, method)
                )

    def test_operator_read_allowlist_is_uuid_and_segment_safe(self):
        for path in (
            "/ops/queue",
            "/ops/queue-smart/",
            "/ops/followups",
            "/ops/followups/due",
            f"/ops/cases/{CASE_ID}",
            f"/ops/cases/{CASE_ID}/documents",
            f"/ops/cases/{CASE_ID}/events",
            f"/ops/cases/{CASE_ID}/followups",
            f"/ops/cases/{CASE_ID}/ai-overrides",
            f"/ops/core/cases/{CASE_ID}/workspace",
            f"/ops/core/cases/{CASE_ID}/payment-status",
            "/ops/vehicle-removal",
            f"/ops/vehicle-removal/{CASE_ID}",
        ):
            with self.subTest(path=path):
                self.assertTrue(bridge.is_scoped_operator_read_path(path))

        for path in (
            "/ops",
            "/ops/core/version",
            "/ops/cases/presented",
            "/ops/presented-cases",
            "/ops/cases/not-a-uuid",
            f"/ops/cases/{CASE_ID}/validated-facts",
            f"/ops/cases/{CASE_ID}/documents/extra",
            f"/ops/core/cases/{CASE_ID}/workspace/extra",
            f"/ops/core/cases/{CASE_ID}/payment-status/extra",
            "/ops/core/cases/reanalysis/policy-status",
            "/ops/vehicle-removal/not-a-uuid",
            f"/ops/vehicle-removal-extra/{CASE_ID}",
        ):
            with self.subTest(path=path):
                self.assertFalse(bridge.is_scoped_operator_read_path(path))


class LegacyOpsSessionBridgeTest(unittest.TestCase):
    def _execute(
        self,
        path: str,
        *,
        session=None,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        environment: str | None = "staging",
        extra_environment: dict[str, str] | None = None,
        load_side_effect: Exception | None = None,
    ):
        request = _request(path, method=method, headers=headers)
        forwarded: dict[str, object] = {}

        async def call_next(current: Request):
            forwarded["called"] = True
            forwarded["operator_token"] = current.headers.get(
                "X-Operator-Token"
            )
            forwarded["operator_actor"] = current.headers.get(
                "X-Operator-Actor"
            )
            forwarded["request"] = current
            return JSONResponse({"ok": True})

        connection = object()
        loader = Mock(return_value=session)
        if load_side_effect is not None:
            loader.side_effect = load_side_effect
        environment_patch = {"OPERATOR_TOKEN": LEGACY_TOKEN}
        if environment is not None:
            environment_patch["RTM_ENV"] = environment
        environment_patch.update(extra_environment or {})
        with (
            patch.dict(os.environ, environment_patch, clear=True),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                return_value=SimpleNamespace(enabled=True),
            ) as config,
            patch.object(bridge, "get_engine", return_value=_Engine(connection)),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                loader,
            ),
        ):
            response = asyncio.run(
                bridge.legacy_ops_individual_session_bridge(
                    request,
                    call_next,
                )
            )
        return response, forwarded, loader, config, connection

    def test_valid_session_is_adapted_and_exposes_trusted_audit_context(self):
        response, forwarded, loader, config, connection = self._execute(
            f"/ops/core/cases/{CASE_ID}/workspace",
            session=_session("ops.view", "presenter.documents.read"),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
                "X-Operator-Actor": "spoofed-client-identity",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(forwarded["called"])
        self.assertEqual(forwarded["operator_token"], LEGACY_TOKEN)
        self.assertEqual(
            forwarded["operator_actor"],
            f"operator:{OPERATOR_ID}",
        )
        request = forwarded["request"]
        self.assertEqual(request.state.rtm_operator_id, OPERATOR_ID)
        self.assertEqual(request.state.rtm_operator_session_id, SESSION_ID)
        self.assertEqual(
            request.state.rtm_operator_permissions,
            ("ops.view", "presenter.documents.read"),
        )
        self.assertNotIn(BEARER, repr(request.state.rtm_operator_context))
        self.assertNotIn(DEVICE, repr(request.state.rtm_operator_context))
        config.assert_called_once_with(require_enabled=True)
        loader.assert_called_once_with(
            connection,
            authorization=f"Bearer {BEARER}",
            x_rtm_device=DEVICE,
            rtm_presenter_device=None,
            touch=True,
        )

    def test_device_cookie_is_accepted_without_echoing_it(self):
        response, forwarded, loader, _, connection = self._execute(
            "/ops/queue",
            session=_session("ops.view"),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "Cookie": f"__Host-rtm_presenter_device={DEVICE}",
            },
        )

        self.assertEqual(response.status_code, 200)
        loader.assert_called_once_with(
            connection,
            authorization=f"Bearer {BEARER}",
            x_rtm_device=None,
            rtm_presenter_device=DEVICE,
            touch=True,
        )
        self.assertEqual(forwarded["operator_token"], LEGACY_TOKEN)

    def test_shared_token_from_client_is_rejected_before_database_access(self):
        response, forwarded, loader, config, _ = self._execute(
            "/ops/queue",
            session=_session("ops.view"),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
                "X-Operator-Token": LEGACY_TOKEN,
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            _response_body(response),
            {"detail": "Autenticación individual requerida"},
        )
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_invalid_session_and_storage_failure_fail_closed(self):
        invalid, invalid_forwarded, _, _, _ = self._execute(
            "/ops/queue",
            session=None,
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )
        failed, failed_forwarded, _, _, _ = self._execute(
            "/ops/queue",
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
            load_side_effect=RuntimeError("database unavailable"),
        )

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(failed.status_code, 503)
        self.assertFalse(invalid_forwarded)
        self.assertFalse(failed_forwarded)

    def test_permission_and_temporary_password_gates_are_fail_closed(self):
        no_permission, no_permission_forwarded, _, _, _ = self._execute(
            "/ops/queue",
            session=_session("presenter.documents.read"),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )
        temporary, temporary_forwarded, _, _, _ = self._execute(
            "/ops/queue",
            session=_session("ops.view", must_change_password=True),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )

        self.assertEqual(no_permission.status_code, 403)
        self.assertEqual(temporary.status_code, 409)
        self.assertFalse(no_permission_forwarded)
        self.assertFalse(temporary_forwarded)

    def test_signer_role_cannot_enter_general_ops_with_ops_view(self):
        signer = _session("ops.view")
        signer.role_code = "rtm.signer"
        response, forwarded, _, _, _ = self._execute(
            "/ops/queue",
            session=signer,
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            _response_body(response),
            {"detail": "Rol de operador OPS requerido"},
        )
        self.assertFalse(forwarded)

    def test_mfa_required_session_is_blocked_until_mfa_exists(self):
        response, forwarded, _, _, _ = self._execute(
            "/ops/queue",
            session=_session("ops.view", mfa_required=True),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(forwarded)

    def test_automation_requires_supervisor_permission(self):
        denied, denied_forwarded, _, _, _ = self._execute(
            "/ops/automation/tick",
            session=_session("ops.view"),
            method="POST",
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )
        allowed, allowed_forwarded, _, _, _ = self._execute(
            "/ops/automation/tick",
            session=_session(
                "ops.view",
                "ops.supervise",
                role_code="rtm.supervisor",
            ),
            method="POST",
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )

        self.assertEqual(denied.status_code, 403)
        self.assertFalse(denied_forwarded)
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed_forwarded["called"])

    def test_legacy_mutations_require_exact_supervisor_and_permission(self):
        path = f"/ops/cases/{CASE_ID}/save-ai-overrides"
        variants = (
            _session("ops.view"),
            _session("ops.view", "ops.supervise"),
            _session("ops.view", role_code="rtm.supervisor"),
        )
        for session in variants:
            with self.subTest(
                role=session.role_code,
                permissions=session.permissions,
            ):
                response, forwarded, _, _, _ = self._execute(
                    path,
                    session=session,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {BEARER}",
                        "X-RTM-Device": DEVICE,
                    },
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    _response_body(response),
                    {
                        "detail": (
                            "Operación reservada a supervisión durante "
                            "la migración"
                        )
                    },
                )
                self.assertFalse(forwarded)

        allowed, forwarded, _, _, _ = self._execute(
            path,
            session=_session(
                "ops.view",
                "ops.supervise",
                role_code="rtm.supervisor",
            ),
            method="POST",
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(forwarded["called"])

    def test_operator_reads_are_limited_to_transactionally_scoped_surfaces(self):
        headers = {
            "Authorization": f"Bearer {BEARER}",
            "X-RTM-Device": DEVICE,
        }
        allowed, allowed_forwarded, _, _, _ = self._execute(
            f"/ops/cases/{CASE_ID}/events",
            session=_session("ops.view"),
            headers=headers,
        )
        denied, denied_forwarded, _, _, _ = self._execute(
            f"/ops/core/cases/{CASE_ID}/validated-facts",
            session=_session("ops.view"),
            headers=headers,
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed_forwarded["called"])
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            _response_body(denied),
            {"detail": "Superficie OPS aún no migrada"},
        )
        self.assertFalse(denied_forwarded)

    def test_supervisor_with_permission_bypasses_read_allowlist(self):
        response, forwarded, _, _, _ = self._execute(
            f"/ops/core/cases/{CASE_ID}/validated-facts",
            session=_session(
                "ops.view",
                "ops.supervise",
                role_code="rtm.supervisor",
            ),
            headers={
                "Authorization": f"Bearer {BEARER}",
                "X-RTM-Device": DEVICE,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(forwarded["called"])

    def test_legacy_ai_run_is_retired_before_it_can_mutate_a_case(self):
        response, forwarded, loader, _, _ = self._execute(
            "/ai/expediente/run",
            method="POST",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            _response_body(response),
            {"detail": "Análisis legacy retirado; utilice el flujo RTM CORE"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()

    def test_vehicle_mark_paid_is_retired_before_authentication(self):
        response, forwarded, loader, config, _ = self._execute(
            f"/ops/vehicle-removal/{CASE_ID}/mark-paid",
            method="POST",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            _response_body(response),
            {"detail": "Marcado manual de pago retirado"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_noncanonical_vehicle_mark_paid_is_also_retired_before_router(self):
        response, forwarded, loader, config, _ = self._execute(
            f"/ops/vehicle-removal/{CASE_ID.replace('-', '').upper()}/mark-paid",
            method="POST",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            _response_body(response),
            {"detail": "Marcado manual de pago retirado"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_vehicle_mark_paid_production_is_fail_closed(self):
        response, forwarded, loader, config, _ = self._execute(
            f"/ops/vehicle-removal/{CASE_ID}/mark-paid",
            method="POST",
            environment="production",
            headers={"X-Operator-Token": "existing-production-token"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_legacy_pin_login_is_retired_in_staging(self):
        response, forwarded, loader, config, _ = self._execute(
            "/ops/login",
            method="POST",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            _response_body(response),
            {"detail": "Acceso individual requerido"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_own_control_surfaces_and_options_are_untouched(self):
        for path, method in (
            ("/ops/auth/login", "POST"),
            ("/ops/admin/operators", "GET"),
            ("/ops/presenter/signature-queue", "GET"),
            ("/ops/connect/supervisor/overview", "GET"),
            ("/ops/restaurant-reservations", "GET"),
            ("/ops/queue", "OPTIONS"),
        ):
            with self.subTest(path=path, method=method):
                response, forwarded, loader, config, _ = self._execute(
                    path,
                    method=method,
                    headers={"X-Operator-Token": "client-value"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    forwarded["operator_token"],
                    "client-value",
                )
                loader.assert_not_called()
                config.assert_not_called()

    def test_production_never_preserves_shared_token_contract(self):
        response, forwarded, loader, config, _ = self._execute(
            "/ops/queue",
            environment="production",
            headers={"X-Operator-Token": "existing-production-token"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_legacy_ai_is_retired_even_in_non_staging_legacy_mode(self):
        response, forwarded, loader, config, _ = self._execute(
            "/ai/expediente/run",
            method="POST",
            environment="production",
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            _response_body(response),
            {"detail": "Análisis legacy retirado; utilice el flujo RTM CORE"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_emergency_switch_blocks_non_staging_legacy_surface(self):
        response, forwarded, loader, config, _ = self._execute(
            "/ops/queue",
            environment="production",
            headers={"X-Operator-Token": "existing-production-token"},
            extra_environment={"RTM_BLOCK_LEGACY_OPERATOR_AUTH": "1"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            _response_body(response),
            {"detail": "Autenticación individual no disponible"},
        )
        self.assertFalse(forwarded)
        loader.assert_not_called()
        config.assert_not_called()

    def test_feature_requested_outside_staging_fails_closed(self):
        environments = (
            (None, {"RTM_ENABLE_OPERATOR_AUTH_V1": "1"}),
            ("production", {"RTM_ENABLE_OPERATOR_AUTH_V1": "1"}),
            ("production", {"RTM_ENABLE_OPERATOR_AUTH_V1": "invalid"}),
            ("stagin", {"RTM_DATA_NAMESPACE": "rtm_staging"}),
            (
                "production",
                {"RENDER_SERVICE_NAME": "recurretumulta-rtm-staging"},
            ),
        )
        for environment, extra_environment in environments:
            with self.subTest(
                environment=environment,
                extra_environment=extra_environment,
            ):
                response, forwarded, loader, config, _ = self._execute(
                    "/ops/queue",
                    environment=environment,
                    extra_environment=extra_environment,
                    headers={"X-Operator-Token": "legacy-client-token"},
                )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    _response_body(response),
                    {"detail": "Autenticación individual no disponible"},
                )
                self.assertFalse(forwarded)
                loader.assert_not_called()
                config.assert_not_called()


class RestaurantAdminIndividualSessionTest(unittest.TestCase):
    _BODY = {"display_name": "Restaurante Seguro", "pin": "24682468"}

    def _bridge_patches(self, *, environment: str, session):
        loader = Mock(return_value=session)
        config = Mock(return_value=SimpleNamespace(enabled=True))
        environment_values = {
            "RTM_ENV": environment,
            "ADMIN_TOKEN": SHARED_ADMIN_TOKEN,
            "OPERATOR_TOKEN": LEGACY_TOKEN,
        }
        return loader, config, environment_values

    def test_staging_shared_admin_token_alone_is_rejected(self):
        loader, config, environment = self._bridge_patches(
            environment="staging",
            session=None,
        )
        restaurant_engine = Mock()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(backend_app, "assert_environment_ready"),
            patch.object(backend_app, "extraction_limits"),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                config,
            ),
            patch.object(bridge, "get_engine", return_value=_Engine(object())),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                loader,
            ),
            patch.object(
                restaurant_routes,
                "get_engine",
                restaurant_engine,
            ),
            TestClient(backend_app.app) as client,
        ):
            response = client.post(
                "/ops/admin/restaurants/create",
                headers={"X-Admin-Token": SHARED_ADMIN_TOKEN},
                json=self._BODY,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Sesión no válida"})
        config.assert_called_once_with(require_enabled=True)
        loader.assert_called_once()
        restaurant_engine.assert_not_called()

    def test_staging_operator_cannot_create_restaurant(self):
        loader, config, environment = self._bridge_patches(
            environment="staging",
            session=_session("ops.view"),
        )
        restaurant_engine = Mock()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(backend_app, "assert_environment_ready"),
            patch.object(backend_app, "extraction_limits"),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                config,
            ),
            patch.object(bridge, "get_engine", return_value=_Engine(object())),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                loader,
            ),
            patch.object(
                restaurant_routes,
                "get_engine",
                restaurant_engine,
            ),
            TestClient(backend_app.app) as client,
        ):
            response = client.post(
                "/ops/admin/restaurants/create",
                headers={
                    "Authorization": f"Bearer {BEARER}",
                    "X-RTM-Device": DEVICE,
                },
                json=self._BODY,
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {
                "detail": (
                    "Operación reservada a supervisión durante la migración"
                )
            },
        )
        restaurant_engine.assert_not_called()

    def test_staging_verified_supervisor_ignores_shared_admin_token(self):
        loader, config, environment = self._bridge_patches(
            environment="staging",
            session=_session(
                "ops.view",
                "ops.supervise",
                role_code="rtm.supervisor",
            ),
        )
        restaurant_connection = _RestaurantConnection()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(backend_app, "assert_environment_ready"),
            patch.object(backend_app, "extraction_limits"),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                config,
            ),
            patch.object(bridge, "get_engine", return_value=_Engine(object())),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                loader,
            ),
            patch.object(
                restaurant_routes,
                "get_engine",
                return_value=_Engine(restaurant_connection),
            ),
            patch.object(restaurant_routes, "_need_admin") as shared_gate,
            TestClient(backend_app.app) as client,
        ):
            response = client.post(
                "/ops/admin/restaurants/create",
                headers={
                    "Authorization": f"Bearer {BEARER}",
                    "X-RTM-Device": DEVICE,
                    "X-Admin-Token": "ignored-client-value",
                },
                json=self._BODY,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "rest_008")
        shared_gate.assert_not_called()
        self.assertEqual(len(restaurant_connection.statements), 2)

    def test_production_rejects_shared_admin_token_contract(self):
        loader, config, environment = self._bridge_patches(
            environment="production",
            session=None,
        )
        restaurant_connection = _RestaurantConnection()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(backend_app, "assert_environment_ready"),
            patch.object(backend_app, "extraction_limits"),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                config,
            ),
            patch.object(bridge, "get_engine", return_value=_Engine(object())),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                loader,
            ),
            patch.object(
                restaurant_routes,
                "get_engine",
                return_value=_Engine(restaurant_connection),
            ),
            TestClient(backend_app.app) as client,
        ):
            accepted = client.post(
                "/ops/admin/restaurants/create",
                headers={"X-Admin-Token": SHARED_ADMIN_TOKEN},
                json=self._BODY,
            )
            rejected = client.post(
                "/ops/admin/restaurants/create",
                headers={"X-Admin-Token": "wrong-token"},
                json=self._BODY,
            )

        self.assertEqual(accepted.status_code, 503)
        self.assertEqual(rejected.status_code, 503)
        self.assertFalse(restaurant_connection.statements)
        loader.assert_not_called()
        config.assert_not_called()

    def test_handler_itself_rejects_shared_token_in_production(self):
        request = _request("/ops/admin/restaurants/create", method="POST")
        with (
            patch.dict(
                os.environ,
                {
                    "RTM_ENV": "production",
                    "ADMIN_TOKEN": SHARED_ADMIN_TOKEN,
                },
                clear=True,
            ),
            patch.object(restaurant_routes, "_need_admin") as shared_gate,
            patch.object(
                restaurant_routes,
                "_need_verified_individual_supervisor",
            ) as individual_gate,
        ):
            with self.assertRaises(HTTPException) as raised:
                restaurant_routes._need_restaurant_admin(
                    request,
                    SHARED_ADMIN_TOKEN,
                )

        self.assertEqual(raised.exception.status_code, 503)
        shared_gate.assert_not_called()
        individual_gate.assert_not_called()

    def test_handler_keeps_shared_token_only_in_explicit_local_mode(self):
        request = _request("/ops/admin/restaurants/create", method="POST")
        with (
            patch.dict(
                os.environ,
                {
                    "RTM_ENV": "development",
                    "ADMIN_TOKEN": SHARED_ADMIN_TOKEN,
                },
                clear=True,
            ),
            patch.object(restaurant_routes, "_need_admin") as shared_gate,
            patch.object(
                restaurant_routes,
                "_need_verified_individual_supervisor",
            ) as individual_gate,
        ):
            restaurant_routes._need_restaurant_admin(
                request,
                SHARED_ADMIN_TOKEN,
            )

        shared_gate.assert_called_once_with(SHARED_ADMIN_TOKEN)
        individual_gate.assert_not_called()


class LegacyOpsBridgeWiringContractTest(unittest.TestCase):
    def test_app_installs_bridge_without_router_level_presenter_dependency(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "app.py"), encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn(
            "legacy_ops_individual_session_bridge",
            source,
        )
        self.assertIn(
            'app.middleware("http")(legacy_ops_individual_session_bridge)',
            source,
        )
        self.assertLess(
            source.index('app.middleware("http")(legacy_ops_individual_session_bridge)'),
            source.index("app.include_router(rtm_core_legacy_guard_router)"),
        )
        self.assertNotIn(
            "dependencies=[Depends(require_operator_device_possession)]",
            source,
        )

    def test_real_app_accepts_individual_session_and_rejects_shared_header(self):
        session = _session(
            "ops.view",
            "ops.supervise",
            role_code="rtm.supervisor",
        )
        environment = {
            "RTM_ENV": "staging",
            "OPERATOR_TOKEN": LEGACY_TOKEN,
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(backend_app, "assert_environment_ready"),
            patch.object(backend_app, "extraction_limits"),
            patch.object(
                bridge,
                "load_operator_auth_runtime_config",
                return_value=SimpleNamespace(enabled=True),
            ),
            patch.object(bridge, "get_engine", return_value=_Engine(object())),
            patch.object(
                bridge,
                "load_operator_session_with_device_possession",
                return_value=session,
            ),
            TestClient(backend_app.app) as client,
        ):
            individual = client.get(
                "/ops/core/version",
                headers={
                    "Authorization": f"Bearer {BEARER}",
                    "X-RTM-Device": DEVICE,
                },
            )
            shared = client.get(
                "/ops/core/version",
                headers={
                    "Origin": "https://frontend.example.test",
                    "X-Operator-Token": LEGACY_TOKEN,
                },
            )
            retired_ai = client.post(
                "/ai/expediente/run",
                json={"case_id": "33333333-3333-4333-8333-333333333333"},
            )

        self.assertEqual(individual.status_code, 200)
        self.assertEqual(shared.status_code, 401)
        self.assertEqual(
            shared.json(),
            {"detail": "Autenticación individual requerida"},
        )
        # Sin ALLOWED_ORIGINS explícito, CORS falla cerrado incluso en una
        # denegación generada por el bridge.
        self.assertIsNone(
            shared.headers.get("access-control-allow-origin"),
        )
        self.assertEqual(retired_ai.status_code, 410)
        self.assertEqual(
            retired_ai.json(),
            {"detail": "Análisis legacy retirado; utilice el flujo RTM CORE"},
        )


if __name__ == "__main__":
    unittest.main()
