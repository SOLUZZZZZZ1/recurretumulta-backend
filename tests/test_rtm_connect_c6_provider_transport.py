from __future__ import annotations

import io
import json
import threading
import time
import traceback
import unittest
import uuid
from email.message import Message
from unittest import mock

from rtm_connect.provider_sandbox_policy import (
    CONTROLLED_SANDBOX_CREDENTIAL_REF,
    ProviderSandboxEndpoint,
)
from rtm_connect.provider_sandbox_transport import (
    MAX_RESPONSE_BYTES,
    ControlledSandboxProbe,
    ControlledSandboxTransport,
    ProviderSandboxAmbiguous,
    ProviderSandboxContractError,
)
from rtm_connect.secret_resolver import EnvironmentSecretResolver, ResolvedSecret


TOKEN = "transport-test-token-value"


class FakeSocket:
    def __init__(self) -> None:
        self.aborted = threading.Event()
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def shutdown(self, _how: int) -> None:
        self.aborted.set()

    def close(self) -> None:
        self.aborted.set()


class FakeResponse:
    def __init__(
        self,
        raw: bytes,
        *,
        status: int = 200,
        content_type: str | None = "application/json",
        encoding: str | None = "identity",
        content_length: str | None = None,
        transfer_encoding: str | None = None,
    ) -> None:
        self.status = status
        self.headers = Message()
        if content_type is not None:
            self.headers.add_header("Content-Type", content_type)
        if encoding is not None:
            self.headers.add_header("Content-Encoding", encoding)
        if content_length is None:
            content_length = str(len(raw))
        if content_length != "ABSENT":
            self.headers.add_header("Content-Length", content_length)
        if transfer_encoding is not None:
            self.headers.add_header("Transfer-Encoding", transfer_encoding)
        self._stream = io.BytesIO(raw)
        self.closed = False

    def read(self, amount: int) -> bytes:
        return self._stream.read(amount)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        connect_error: Exception | None = None,
        request_error: Exception | None = None,
        response_error: Exception | None = None,
    ) -> None:
        self.response = response
        self.connect_error = connect_error
        self.request_error = request_error
        self.response_error = response_error
        self.sock = FakeSocket()
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, path, body, dict(headers)))
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self) -> FakeResponse:
        if self.response_error is not None:
            raise self.response_error
        if self.response is None:
            raise OSError("missing fake response")
        return self.response

    def close(self) -> None:
        self.closed = True
        self.sock.close()


class BlockingConnection(FakeConnection):
    def getresponse(self) -> FakeResponse:
        self.sock.aborted.wait(1.0)
        raise OSError("watchdog closed fake socket")


def resolver() -> EnvironmentSecretResolver:
    return EnvironmentSecretResolver(
        {"RTM_CONNECT_C6_SANDBOX_TOKEN": TOKEN},
        allowed_references=(CONTROLLED_SANDBOX_CREDENTIAL_REF,),
    )


def endpoint() -> ProviderSandboxEndpoint:
    return ProviderSandboxEndpoint.loopback_for_smoke(
        "http://127.0.0.1:54321"
    )


def response_for(probe: ControlledSandboxProbe, **changes) -> bytes:
    payload = {
        "contract_version": "rtm.c6.controlled_sandbox.probe.v1",
        "environment": "sandbox",
        "status": "accepted",
        "external_reference": probe.expected_external_reference,
        "client_reference": probe.client_reference,
        "request_sha256": probe.request_sha256,
    }
    payload.update(changes)
    return json.dumps(payload, separators=(",", ":")).encode()


def transport(*, timeout_seconds: float = 3.0) -> ControlledSandboxTransport:
    return ControlledSandboxTransport(
        endpoint=endpoint(),
        secret_resolver=resolver(),
        timeout_seconds=timeout_seconds,
    )


def submit_with(
    connection: FakeConnection,
    probe: ControlledSandboxProbe,
    *,
    timeout_seconds: float = 3.0,
):
    client = transport(timeout_seconds=timeout_seconds)
    with mock.patch(
        "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection",
        return_value=connection,
    ):
        return client.submit(probe, idempotency_key="rtmc1:" + "b" * 64)


class ConnectC6ProviderTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = ControlledSandboxProbe(str(uuid.uuid4()), "a" * 64)

    def test_post_body_is_stable_and_has_no_attempt_or_timestamp(self):
        connection = FakeConnection(FakeResponse(response_for(self.probe)))
        result = submit_with(connection, self.probe)
        method, path, raw_body, headers = connection.requests[0]
        body = json.loads(raw_body)
        self.assertEqual(result.external_reference, self.probe.expected_external_reference)
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1/probes")
        self.assertEqual(
            set(body),
            {"contract_version", "client_reference", "request_sha256", "marker"},
        )
        self.assertNotIn("attempt_id", body)
        self.assertNotIn("timestamp", body)
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["Idempotency-Key"], "rtmc1:" + "b" * 64)
        self.assertTrue(connection.closed)

    def test_get_reconciliation_uses_fixed_path_and_no_body(self):
        connection = FakeConnection(FakeResponse(response_for(self.probe)))
        client = transport()
        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection",
            return_value=connection,
        ):
            client.reconcile(self.probe, idempotency_key="rtmc1:" + "b" * 64)
        method, path, body, _headers = connection.requests[0]
        self.assertEqual(method, "GET")
        self.assertIsNone(body)
        self.assertEqual(
            path,
            f"/v1/probes/by-client-reference/{self.probe.action_id}",
        )

    def test_redirect_status_is_ambiguous_and_not_followed(self):
        connection = FakeConnection(
            FakeResponse(response_for(self.probe), status=302)
        )
        with self.assertRaises(ProviderSandboxAmbiguous) as captured:
            submit_with(connection, self.probe)
        self.assertTrue(captured.exception.network_call_performed)
        self.assertEqual(len(connection.requests), 1)

    def test_raw_protocol_error_cannot_leak_token_through_traceback_chain(self):
        connection = FakeConnection(
            response_error=RuntimeError(f"reflected-{TOKEN}")
        )
        try:
            submit_with(connection, self.probe)
        except ProviderSandboxAmbiguous as exc:
            rendered = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            self.assertNotIn(TOKEN, rendered)
            self.assertIsNone(exc.__cause__)
            self.assertIsNone(exc.__context__)
        else:
            self.fail("El error crudo debía normalizarse como ambiguo")

    def test_total_deadline_blocks_drip_response(self):
        class DripResponse(FakeResponse):
            def read1(self, amount: int) -> bytes:
                time.sleep(0.03)
                return self._stream.read(1)

        connection = FakeConnection(DripResponse(response_for(self.probe)))
        started = time.monotonic()
        with self.assertRaises(ProviderSandboxAmbiguous) as captured:
            submit_with(connection, self.probe, timeout_seconds=0.1)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(captured.exception.network_call_performed)

    def test_total_deadline_covers_status_and_leaves_no_watchdog(self):
        connection = BlockingConnection()
        before = {thread.ident for thread in threading.enumerate()}
        started = time.monotonic()
        with self.assertRaises(ProviderSandboxAmbiguous) as captured:
            submit_with(connection, self.probe, timeout_seconds=0.1)
        elapsed = time.monotonic() - started
        after = {thread.ident for thread in threading.enumerate()}
        self.assertLess(elapsed, 0.3)
        self.assertTrue(captured.exception.network_call_performed)
        self.assertEqual(after, before)

    def test_connect_failure_is_pre_send_and_not_ambiguous(self):
        connection = FakeConnection(connect_error=OSError("no listener"))
        with self.assertRaises(ProviderSandboxContractError) as captured:
            submit_with(connection, self.probe)
        self.assertFalse(captured.exception.network_call_performed)
        self.assertEqual(connection.requests, [])

    def test_oversize_encoded_chunked_and_wrong_content_type_are_blocked(self):
        cases = (
            FakeResponse(
                b"x",
                content_length=str(MAX_RESPONSE_BYTES + 1),
            ),
            FakeResponse(response_for(self.probe), encoding="gzip"),
            FakeResponse(response_for(self.probe), content_type="text/plain"),
            FakeResponse(response_for(self.probe), transfer_encoding="chunked"),
            FakeResponse(response_for(self.probe), content_length="ABSENT"),
        )
        for response in cases:
            with self.subTest(headers=str(response.headers)):
                with self.assertRaises(ProviderSandboxContractError):
                    submit_with(FakeConnection(response), self.probe)

    def test_duplicate_security_relevant_headers_are_blocked(self):
        response = FakeResponse(response_for(self.probe))
        response.headers.add_header("Content-Length", "1")
        with self.assertRaises(ProviderSandboxContractError):
            submit_with(FakeConnection(response), self.probe)

    def test_mismatch_extra_field_duplicate_key_and_nan_are_blocked(self):
        malformed = (
            response_for(self.probe, request_sha256="c" * 64),
            response_for(self.probe, extra="x"),
            b'{"contract_version":"x","contract_version":"y"}',
            b'{"value":NaN}',
        )
        for raw in malformed:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(ProviderSandboxContractError):
                    submit_with(FakeConnection(FakeResponse(raw)), self.probe)

    def test_transport_has_no_proxy_redirect_https_or_public_client_injection(self):
        import inspect
        import rtm_connect.provider_sandbox_transport as module

        source = inspect.getsource(module)
        self.assertIn("http.client.HTTPConnection", source)
        self.assertNotIn("HTTPSConnection", source)
        self.assertNotIn("urllib.request", source)
        self.assertNotIn("ProxyHandler", source)
        with self.assertRaises(TypeError):
            ControlledSandboxTransport(
                endpoint=endpoint(),
                secret_resolver=resolver(),
                connection=FakeConnection(),
            )

    def test_fake_or_mutated_endpoint_is_blocked_before_connection(self):
        class FakeEndpoint:
            origin = "http://127.0.0.1:54321"
            credential_ref = CONTROLLED_SANDBOX_CREDENTIAL_REF
            loopback_test_only = True

        with self.assertRaises(TypeError):
            ControlledSandboxTransport(
                endpoint=FakeEndpoint(),
                secret_resolver=resolver(),
            )
        item = endpoint()
        object.__setattr__(item, "origin", "http://203.0.113.10:54321")
        client = ControlledSandboxTransport(
            endpoint=item,
            secret_resolver=resolver(),
        )
        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection"
        ) as constructor:
            with self.assertRaises(ProviderSandboxContractError):
                client.submit(self.probe, idempotency_key="rtmc1:" + "b" * 64)
        constructor.assert_not_called()

    def test_resolver_must_return_exact_frozen_reference(self):
        class WrongResolver:
            def resolve(self, _reference):
                return ResolvedSecret(
                    reference="env://ANOTHER_SANDBOX_TOKEN",
                    value=TOKEN,
                )

        with self.assertRaises(TypeError):
            ControlledSandboxTransport(
                endpoint=endpoint(),
                secret_resolver=WrongResolver(),
            )
        client = transport()
        def wrong_reference(_self, _reference):
            return ResolvedSecret(
                reference="env://ANOTHER_SANDBOX_TOKEN",
                value=TOKEN,
            )

        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection"
        ) as constructor, mock.patch.object(
            EnvironmentSecretResolver,
            "resolve",
            wrong_reference,
        ):
            with self.assertRaises(ProviderSandboxContractError):
                client.submit(self.probe, idempotency_key="rtmc1:" + "b" * 64)
        constructor.assert_not_called()

    def test_resolver_with_broader_allowlist_is_not_runtime_sealed(self):
        broad = EnvironmentSecretResolver(
            {
                "RTM_CONNECT_C6_SANDBOX_TOKEN": TOKEN,
                "ANOTHER_SANDBOX_TOKEN": TOKEN,
            },
            allowed_references=(
                CONTROLLED_SANDBOX_CREDENTIAL_REF,
                "env://ANOTHER_SANDBOX_TOKEN",
            ),
        )
        client = ControlledSandboxTransport(
            endpoint=endpoint(),
            secret_resolver=broad,
        )
        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection"
        ) as constructor:
            with self.assertRaises(ProviderSandboxContractError):
                client.submit(self.probe, idempotency_key="rtmc1:" + "b" * 64)
        constructor.assert_not_called()

    def test_slow_resolver_expires_before_secret_enters_headers(self):
        client = transport(timeout_seconds=0.1)
        original = EnvironmentSecretResolver.resolve

        def slow_resolve(instance, reference):
            time.sleep(0.12)
            return original(instance, reference)

        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection"
        ) as constructor, mock.patch.object(
            EnvironmentSecretResolver,
            "resolve",
            slow_resolve,
        ):
            with self.assertRaises(ProviderSandboxContractError) as captured:
                client.submit(self.probe, idempotency_key="rtmc1:" + "b" * 64)
        self.assertNotIn(TOKEN, "".join(traceback.format_exception(captured.exception)))
        constructor.assert_not_called()

    def test_transport_configuration_is_immutable(self):
        client = transport()
        with self.assertRaises(AttributeError):
            client._endpoint = endpoint()

    def test_invalid_idempotency_key_is_blocked_before_connection(self):
        connection = FakeConnection(FakeResponse(response_for(self.probe)))
        client = transport()
        with mock.patch(
            "rtm_connect.provider_sandbox_transport.http.client.HTTPConnection",
            return_value=connection,
        ) as constructor:
            with self.assertRaises(ProviderSandboxContractError) as captured:
                client.submit(self.probe, idempotency_key="not-frozen")
        self.assertFalse(captured.exception.network_call_performed)
        constructor.assert_not_called()

    def test_idempotency_key_is_not_normalized_by_transport(self):
        client = transport()
        for value in (
            " RTMC1:" + "B" * 64 + " ",
            "rtmc1:" + "B" * 64,
        ):
            with self.subTest(value=value[:10]):
                with self.assertRaises(ProviderSandboxContractError):
                    client.submit(self.probe, idempotency_key=value)


if __name__ == "__main__":
    unittest.main()
