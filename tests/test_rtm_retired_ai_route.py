import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from starlette.requests import Request

import app as backend_app
from rtm_core.legacy_ops_session_bridge import (
    legacy_ops_individual_session_bridge,
)


ROOT = Path(__file__).resolve().parents[1]


def _request(path: str = "/ai/expediente/run") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


class RetiredLegacyAIRouteContractTest(unittest.TestCase):
    def test_legacy_ai_router_is_not_imported_or_mounted(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("from ai_router", source)
        self.assertNotIn("include_router(ai_router)", source)
        self.assertNotIn(
            "/ai/expediente/run",
            {getattr(route, "path", None) for route in backend_app.app.routes},
        )

    def test_fail_closed_guard_returns_410_without_reaching_application(self):
        downstream = AsyncMock()

        response = asyncio.run(
            legacy_ops_individual_session_bridge(
                _request(),
                downstream,
            )
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.headers.get("cache-control"), "no-store, max-age=0")
        downstream.assert_not_awaited()

    def test_guard_also_blocks_descendants_to_prevent_path_bypass(self):
        downstream = AsyncMock()

        response = asyncio.run(
            legacy_ops_individual_session_bridge(
                _request("/ai/expediente/run/anything"),
                downstream,
            )
        )

        self.assertEqual(response.status_code, 410)
        downstream.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
