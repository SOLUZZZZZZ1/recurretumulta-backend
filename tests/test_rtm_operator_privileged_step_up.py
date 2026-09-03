from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

import ops_restaurant_reservations as restaurant_routes
from rtm_core.legacy_ops_session_bridge import LegacyOpsOperatorContext
from rtm_core.operator_admin_router import (
    SupervisorContext,
    require_recent_supervisor_context,
)
from rtm_core.operator_lifecycle_router import (
    LifecycleSupervisorContext,
    require_recent_lifecycle_supervisor_context,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ops/admin/restaurants/create",
            "raw_path": b"/ops/admin/restaurants/create",
            "headers": [
                (b"x-rtm-reauthenticated-at", b"2999-01-01T00:00:00Z")
            ],
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 443),
            "scheme": "https",
            "http_version": "1.1",
        }
    )


class PrivilegedStepUpTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.window = 300

    def _session(self, verified_at):
        return SimpleNamespace(
            login_at=self.now - timedelta(hours=1),
            last_verified_at=verified_at,
        )

    def test_admin_mutation_gate_accepts_only_fresh_persisted_step_up(self):
        config = SimpleNamespace(
            auth=SimpleNamespace(
                reauthentication_max_age_seconds=self.window,
            )
        )
        fresh = SupervisorContext(
            session=self._session(self.now - timedelta(seconds=30)),
            config=config,
        )
        self.assertIs(
            asyncio.run(require_recent_supervisor_context(fresh)),
            fresh,
        )

        for verified_at in (
            None,
            self.now - timedelta(seconds=self.window + 1),
        ):
            with self.subTest(verified_at=verified_at):
                context = SupervisorContext(
                    session=self._session(verified_at),
                    config=config,
                )
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(require_recent_supervisor_context(context))
                self.assertEqual(raised.exception.status_code, 403)

    def test_lifecycle_mutation_gate_accepts_only_fresh_persisted_step_up(self):
        config = SimpleNamespace(
            admin=SimpleNamespace(
                auth=SimpleNamespace(
                    reauthentication_max_age_seconds=self.window,
                )
            )
        )
        fresh = LifecycleSupervisorContext(
            session=self._session(self.now - timedelta(seconds=30)),
            config=config,
        )
        self.assertIs(
            asyncio.run(require_recent_lifecycle_supervisor_context(fresh)),
            fresh,
        )

        for verified_at in (
            None,
            self.now - timedelta(seconds=self.window + 1),
        ):
            with self.subTest(verified_at=verified_at):
                context = LifecycleSupervisorContext(
                    session=self._session(verified_at),
                    config=config,
                )
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        require_recent_lifecycle_supervisor_context(context)
                    )
                self.assertEqual(raised.exception.status_code, 403)

    def test_restaurant_admin_ignores_claimed_header_and_requires_fresh_context(self):
        for verified_at, allowed in (
            (self.now - timedelta(seconds=30), True),
            (self.now - timedelta(seconds=self.window + 1), False),
            (None, False),
        ):
            with self.subTest(verified_at=verified_at):
                request = _request()
                request.state.rtm_operator_context = LegacyOpsOperatorContext(
                    operator_id="11111111-1111-4111-8111-111111111111",
                    session_id="22222222-2222-4222-8222-222222222222",
                    role_code="rtm.supervisor",
                    permissions=("ops.view", "ops.supervise"),
                    actor="operator:11111111-1111-4111-8111-111111111111",
                    login_at=self.now - timedelta(hours=1),
                    last_verified_at=verified_at,
                    reauthentication_max_age_seconds=self.window,
                )
                if allowed:
                    restaurant_routes._need_verified_individual_supervisor(
                        request
                    )
                else:
                    with self.assertRaises(HTTPException) as raised:
                        restaurant_routes._need_verified_individual_supervisor(
                            request
                        )
                    self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
