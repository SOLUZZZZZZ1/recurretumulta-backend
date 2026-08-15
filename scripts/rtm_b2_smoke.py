#!/usr/bin/env python3
"""Prueba real, sintética y aislada de Backblaze B2 para RTM staging.

Comprueba, usando las mismas funciones del backend:

1. subida de un objeto sintético;
2. descarga por SDK y verificación SHA-256;
3. descarga mediante URL prefirmada;
4. rechazo del mismo objeto sin firma;
5. opcionalmente, caducidad efectiva de una URL prefirmada corta;
6. borrado de limpieza cuando la credencial lo permite.

No utiliza expedientes ni datos reales y se niega a ejecutarse fuera de staging.
No imprime credenciales ni la URL prefirmada.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from b2_storage import (  # noqa: E402
    download_bytes,
    get_s3_client,
    presign_get_url,
    upload_bytes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida subida, descarga, privacidad y limpieza de B2 en RTM staging.",
    )
    parser.add_argument(
        "--verify-expiry",
        action="store_true",
        help="Comprueba también que una URL prefirmada corta deja de funcionar.",
    )
    parser.add_argument(
        "--expiry-seconds",
        type=int,
        default=5,
        help="Caducidad usada por --verify-expiry (1-60 segundos; por defecto, 5).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Conserva el objeto sintético para inspeccionarlo manualmente en Backblaze.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Timeout HTTP en segundos (por defecto, 20).",
    )
    return parser


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_url(url: str, *, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": "RTM-B2-Smoke/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _unsigned_url(presigned_url: str) -> str:
    parts = urlsplit(presigned_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _unsigned_access_is_blocked(url: str, *, timeout: int) -> tuple[bool, int | None]:
    try:
        _read_url(url, timeout=timeout)
    except HTTPError as exc:
        # Algunos almacenes privados ocultan la existencia con 404.
        return exc.code in {401, 403, 404}, exc.code
    return False, 200


def _expired_url_is_blocked(url: str, *, timeout: int) -> tuple[bool, int | None]:
    try:
        _read_url(url, timeout=timeout)
    except HTTPError as exc:
        return exc.code in {400, 401, 403, 404}, exc.code
    return False, 200


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    if environment != "staging":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "safety_guard",
                    "message": "La prueba B2 solo puede ejecutarse con RTM_ENV=staging.",
                    "environment": environment or "unset",
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    if not 1 <= args.expiry_seconds <= 60:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "invalid_expiry_seconds",
                    "message": "--expiry-seconds debe estar entre 1 y 60.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    if args.timeout < 1:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "invalid_timeout",
                    "message": "--timeout debe ser mayor que cero.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    run_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = (
        "RTM synthetic B2 smoke test\n"
        f"run_id={run_id}\n"
        f"created_at={timestamp}\n"
    ).encode("utf-8")
    expected_sha256 = _sha256(payload)

    bucket: str | None = None
    key: str | None = None
    cleanup_deleted = False
    cleanup_error: str | None = None
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_b2_smoke",
        "version": "rtm_b2_smoke_v1_0",
        "environment": environment,
        "synthetic_only": True,
        "payload_bytes": len(payload),
        "sha256": expected_sha256,
        "verify_expiry_requested": bool(args.verify_expiry),
        "keep_requested": bool(args.keep),
    }

    exit_code = 1

    try:
        bucket, key = upload_bytes(
            case_id="__rtm_b2_smoke__",
            kind_folder="diagnostics",
            content=payload,
            ext=".txt",
            mime="text/plain; charset=utf-8",
        )
        report["bucket"] = bucket
        report["key"] = key
        report["upload_ok"] = True

        downloaded = download_bytes(bucket, key)
        sdk_roundtrip_ok = _sha256(downloaded) == expected_sha256
        report["sdk_download_ok"] = sdk_roundtrip_ok

        signed_url = presign_get_url(
            bucket,
            key,
            expires_seconds=60,
            filename="rtm-b2-smoke.txt",
        )
        signed_download = _read_url(signed_url, timeout=args.timeout)
        signed_roundtrip_ok = _sha256(signed_download) == expected_sha256
        report["presigned_download_ok"] = signed_roundtrip_ok

        unsigned_blocked, unsigned_status = _unsigned_access_is_blocked(
            _unsigned_url(signed_url),
            timeout=args.timeout,
        )
        report["unsigned_access_blocked"] = unsigned_blocked
        report["unsigned_http_status"] = unsigned_status

        expiry_ok = True
        if args.verify_expiry:
            short_url = presign_get_url(
                bucket,
                key,
                expires_seconds=args.expiry_seconds,
                filename="rtm-b2-smoke-expiring.txt",
            )
            immediate = _read_url(short_url, timeout=args.timeout)
            immediate_ok = _sha256(immediate) == expected_sha256
            report["expiry_immediate_download_ok"] = immediate_ok

            time.sleep(args.expiry_seconds + 2)
            expiry_blocked, expiry_status = _expired_url_is_blocked(
                short_url,
                timeout=args.timeout,
            )
            report["expired_url_blocked"] = expiry_blocked
            report["expired_http_status"] = expiry_status
            report["expiry_wait_seconds"] = args.expiry_seconds + 2
            expiry_ok = immediate_ok and expiry_blocked

        tests_ok = (
            sdk_roundtrip_ok
            and signed_roundtrip_ok
            and unsigned_blocked
            and expiry_ok
        )
        report["tests_ok"] = tests_ok
        report["ok"] = tests_ok
        exit_code = 0 if tests_ok else 1

    except Exception as exc:
        report["error"] = _safe_error(exc)
        report["ok"] = False
        exit_code = 1

    finally:
        if bucket and key and not args.keep:
            try:
                get_s3_client().delete_object(Bucket=bucket, Key=key)
                cleanup_deleted = True
            except Exception as exc:
                cleanup_error = _safe_error(exc)

        report["cleanup"] = {
            "kept": bool(args.keep and bucket and key),
            "deleted": cleanup_deleted,
            "error": cleanup_error,
        }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
