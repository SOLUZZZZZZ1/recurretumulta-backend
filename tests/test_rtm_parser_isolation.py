from __future__ import annotations

import ast
import asyncio
import io
import multiprocessing
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

import rtm_core.parser_isolation as parser_isolation
from rtm_core.parser_isolation import (
    ParserIsolationError,
    ParserIsolationTimeout,
    assert_parser_isolation_ready,
    run_parser_isolated,
    run_parser_isolated_async,
)
from rtm_core.upload_security import (
    PDF,
    UploadSecurityError,
    validate_document_bytes,
)


ROOT = Path(__file__).resolve().parents[1]


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class ParserIsolationProcessTests(unittest.TestCase):
    def test_real_pdf_validation_crosses_process_boundary(self) -> None:
        payload = _pdf_bytes()
        result = validate_document_bytes(
            filename="prueba.pdf",
            declared_mime=PDF,
            data=payload,
            max_bytes=2 * 1024 * 1024,
            allowed_mimes={PDF},
        )
        self.assertEqual(result.mime, PDF)
        self.assertEqual(result.size_bytes, len(payload))
        self.assertRegex(result.sha256, r"^[0-9a-f]{64}$")

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(os, "uname")
        and os.uname().sysname.casefold() == "linux",
        "Los límites obligatorios de producción son Linux/POSIX",
    )
    def test_staging_profile_runs_with_mandatory_os_limits(self) -> None:
        with patch.dict(os.environ, {"RTM_ENV": "staging"}):
            configuration = assert_parser_isolation_ready()
            result = validate_document_bytes(
                filename="prueba.pdf",
                declared_mime=PDF,
                data=_pdf_bytes(),
                max_bytes=2 * 1024 * 1024,
                allowed_mimes={PDF},
            )
        self.assertTrue(configuration["os_limits_required"])
        self.assertEqual(configuration["start_method"], "spawn")
        self.assertEqual(configuration["guarantee_scope"], "worker_process_only")
        self.assertNotIn("host_isolated", configuration)
        self.assertNotIn("network_isolated", configuration)
        observed = configuration["observed"]
        self.assertTrue(observed["separate_process"])
        self.assertTrue(observed["environment_cleared"])
        self.assertEqual(
            observed["python_socket_api_replaced"],
            {
                "socket": True,
                "socketpair": True,
                "create_connection": True,
            },
        )
        self.assertTrue(observed["private_umask"])
        self.assertIsInstance(observed["worker_unprivileged_identity"], bool)
        self.assertIs(observed["no_new_privileges"], True)
        self.assertTrue(observed["resource_limits"])
        for soft, hard in observed["resource_limits"].values():
            self.assertEqual(soft, hard)
            self.assertGreaterEqual(soft, 0)
        self.assertEqual(result.mime, PDF)

    @unittest.skipUnless(os.name == "posix", "RLIMIT solo está disponible en POSIX")
    def test_worker_limits_lower_soft_and_hard_without_touching_test_process(self) -> None:
        import resource

        limits = {
            "memory_bytes": 512 * 1024 * 1024,
            "cpu_seconds": 6,
            "require_os_limits": True,
        }
        specifications = parser_isolation._resource_limit_specs(resource, limits)
        inherited = {
            kind: (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
            for _name, kind, _maximum in specifications
        }
        inherited[resource.RLIMIT_CPU] = (2, resource.RLIM_INFINITY)
        inherited[resource.RLIMIT_FSIZE] = (512 * 1024, 512 * 1024)
        writes: list[tuple[int, tuple[int, int]]] = []

        def fake_getrlimit(kind: int) -> tuple[int, int]:
            return inherited[kind]

        def fake_setrlimit(kind: int, pair: tuple[int, int]) -> None:
            inherited[kind] = pair
            writes.append((kind, pair))

        with (
            patch.object(resource, "getrlimit", side_effect=fake_getrlimit),
            patch.object(resource, "setrlimit", side_effect=fake_setrlimit),
        ):
            parser_isolation._install_worker_limits(limits)

        self.assertEqual(len(writes), len(specifications))
        for _kind, (soft, hard) in writes:
            self.assertEqual(soft, hard)
            self.assertNotEqual(soft, resource.RLIM_INFINITY)
        self.assertEqual(inherited[resource.RLIMIT_CORE], (0, 0))
        self.assertEqual(inherited[resource.RLIMIT_CPU], (2, 2))
        self.assertEqual(
            inherited[resource.RLIMIT_FSIZE],
            (512 * 1024, 512 * 1024),
        )

    @unittest.skipUnless(os.name == "posix", "RLIMIT solo está disponible en POSIX")
    def test_worker_limits_fail_if_effective_pair_cannot_be_verified(self) -> None:
        import resource

        limits = {
            "memory_bytes": 512 * 1024 * 1024,
            "cpu_seconds": 6,
            "require_os_limits": True,
        }
        with (
            patch.object(
                resource,
                "getrlimit",
                return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ),
            patch.object(resource, "setrlimit"),
        ):
            with self.assertRaises(RuntimeError):
                parser_isolation._install_worker_limits(limits)

    def test_readiness_rejects_the_old_self_asserted_probe(self) -> None:
        limits = {
            "memory_bytes": 512 * 1024 * 1024,
            "cpu_seconds": 6,
            "require_os_limits": False,
        }
        with self.assertRaises(RuntimeError):
            parser_isolation._validate_readiness_probe(
                {"hardened": True},
                limits=limits,
                parent_pid=os.getpid(),
            )

    def test_wall_timeout_terminates_hung_worker(self) -> None:
        started = time.monotonic()
        with patch.dict(os.environ, {"RTM_PARSER_TEST_HOOKS": "1"}):
            with self.assertRaises(ParserIsolationTimeout):
                run_parser_isolated(
                    "_test_sleep",
                    {"seconds": 5},
                    wall_seconds=0.10,
                )
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertFalse(
            any(
                child.name.startswith("rtm-parser-")
                for child in multiprocessing.active_children()
            )
        )

    def test_worker_crash_fails_closed(self) -> None:
        with patch.dict(os.environ, {"RTM_PARSER_TEST_HOOKS": "1"}):
            with self.assertRaises(ParserIsolationError):
                run_parser_isolated("_test_crash", {})

    def test_memory_pressure_is_contained_and_fails_closed(self) -> None:
        environment = {
            "RTM_PARSER_TEST_HOOKS": "1",
            "RTM_PARSER_MEMORY_MIB": "128",
            "RTM_PARSER_WALL_SECONDS": "5",
        }
        with patch.dict(os.environ, environment):
            with self.assertRaises(ParserIsolationError):
                run_parser_isolated("_test_memory_pressure", {})

    def test_supervision_does_not_block_async_event_loop(self) -> None:
        async def scenario() -> int:
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                for _ in range(20):
                    await asyncio.sleep(0.01)
                    ticks += 1

            with patch.dict(os.environ, {"RTM_PARSER_TEST_HOOKS": "1"}):
                pulse = asyncio.create_task(ticker())
                with self.assertRaises(ParserIsolationTimeout):
                    await run_parser_isolated_async(
                        "_test_sleep",
                        {"seconds": 5},
                        wall_seconds=0.10,
                    )
                await pulse
            return ticks

        self.assertEqual(asyncio.run(scenario()), 20)

    def test_isolation_failure_maps_to_opaque_503(self) -> None:
        with patch(
            "rtm_core.parser_isolation.run_parser_isolated",
            side_effect=ParserIsolationTimeout("sensitive worker detail"),
        ):
            with self.assertRaises(UploadSecurityError) as raised:
                validate_document_bytes(
                    filename="prueba.pdf",
                    declared_mime=PDF,
                    data=_pdf_bytes(),
                    max_bytes=2 * 1024 * 1024,
                    allowed_mimes={PDF},
                )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("sensitive", str(raised.exception))


class AsyncParserCallContractTests(unittest.TestCase):
    def test_async_endpoints_do_not_call_blocking_parsers_directly(self) -> None:
        targets = (
            "analyze.py",
            "analyze_expediente.py",
            "cases.py",
            "ops.py",
            "partner.py",
            "rtm_core/intake_router.py",
            "rtm_presenter_router.py",
            "vehicle_removal_router.py",
        )
        forbidden = {
            "validate_document_bytes",
            "extract_text_from_pdf_bytes",
            "extract_text_from_docx_bytes",
        }
        violations: list[str] = []
        for relative in targets:
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            for function in (
                node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
            ):
                for call in (
                    node for node in ast.walk(function) if isinstance(node, ast.Call)
                ):
                    if isinstance(call.func, ast.Name) and call.func.id in forbidden:
                        violations.append(
                            f"{relative}:{call.lineno}:{function.name}:{call.func.id}"
                        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
