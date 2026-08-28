#!/usr/bin/env python3
"""Prueba sintética del flujo documental real de RTM en staging.

Ejercita los endpoints que utiliza el backend para:

1. crear un expediente sintético y guardar identidad en B2;
2. generar y conservar la autorización PDF;
3. añadir un documento original;
4. subir la autorización firmada;
5. subir el justificante de presentación;
6. comprobar que PostgreSQL conserva cada referencia B2;
7. validar B2 mediante SDK, una URL pública limitada al expediente y el bloqueo
   de descarga directa desde OPS;
8. eliminar objetos y registros de prueba al terminar.

No utiliza datos de clientes, no activa pagos, correo ni presentaciones externas y
se niega a ejecutarse fuera de ``RTM_ENV=staging``. Tampoco imprime secretos ni
URLs prefirmadas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import app  # noqa: E402
from b2_storage import download_bytes, get_s3_client  # noqa: E402
from database import get_engine  # noqa: E402
from pdf_builder import build_pdf  # noqa: E402


_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida el flujo documental real de RTM con datos sintéticos.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Conserva el expediente y los objetos sintéticos para inspección manual.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Timeout de la descarga prefirmada en segundos (por defecto, 20).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Imprime el informe JSON en una sola línea.",
    )
    return parser


def _flag(name: str) -> Optional[bool]:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _read_url(url: str, *, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": "RTM-Document-Flow-Smoke/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _response_payload(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:1000]}
    if not response.is_success:
        raise RuntimeError(
            f"HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False, default=str)}"
        )
    if not isinstance(payload, dict):
        raise RuntimeError("La respuesta HTTP no contiene un objeto JSON.")
    return payload


def _document_rows(case_id: str) -> list[dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT CAST(id AS TEXT), COALESCE(kind,''), COALESCE(b2_bucket,''),
                       COALESCE(b2_key,''), COALESCE(mime,''),
                       COALESCE(size_bytes,0), created_at
                FROM documents
                WHERE case_id=:case_id
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"case_id": case_id},
        ).fetchall()
    return [
        {
            "id": str(row[0]),
            "kind": str(row[1] or ""),
            "bucket": str(row[2] or ""),
            "key": str(row[3] or ""),
            "mime": str(row[4] or ""),
            "size_bytes": int(row[5] or 0),
            "created_at": str(row[6]),
        }
        for row in rows
    ]


def _mark_synthetic_case(case_id: str) -> None:
    """Marca el expediente como test cuando las columnas legacy están disponibles."""
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE cases
                    SET test_mode=TRUE,
                        override_deadlines=TRUE,
                        source_module='rtm_document_flow_smoke',
                        updated_at=NOW()
                    WHERE id=:case_id
                    """
                ),
                {"case_id": case_id},
            )
    except Exception:
        # La seguridad principal no depende de estas columnas: los datos ya son
        # inequívocamente sintéticos y staging prohíbe datos reales.
        pass


def _delete_synthetic_case(case_id: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    object_errors: list[str] = []
    deleted_objects = 0
    seen: set[tuple[str, str]] = set()

    for document in documents:
        bucket = str(document.get("bucket") or "")
        key = str(document.get("key") or "")
        identity = (bucket, key)
        if not bucket or not key or identity in seen:
            continue
        seen.add(identity)
        try:
            get_s3_client().delete_object(Bucket=bucket, Key=key)
            deleted_objects += 1
        except Exception as exc:
            object_errors.append(_safe_error(exc))

    database_deleted = False
    database_error: Optional[str] = None
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM events WHERE case_id=:case_id"), {"case_id": case_id})
            conn.execute(text("DELETE FROM documents WHERE case_id=:case_id"), {"case_id": case_id})
            conn.execute(text("DELETE FROM cases WHERE id=:case_id"), {"case_id": case_id})
        database_deleted = True
    except Exception as exc:
        database_error = _safe_error(exc)
        try:
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE cases
                        SET status='archived_test', test_mode=TRUE, updated_at=NOW()
                        WHERE id=:case_id
                        """
                    ),
                    {"case_id": case_id},
                )
        except Exception:
            pass

    return {
        "objects_deleted": deleted_objects,
        "object_errors": object_errors,
        "database_deleted": database_deleted,
        "database_error": database_error,
    }


def _safety_error(message: str, **extra: Any) -> int:
    payload = {"ok": False, "error": "safety_guard", "message": message, **extra}
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    if environment != "staging":
        return _safety_error(
            "La prueba documental solo puede ejecutarse con RTM_ENV=staging.",
            environment=environment or "unset",
        )
    if _flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        return _safety_error(
            "RTM_ALLOW_REAL_CUSTOMER_DATA debe estar explícitamente desactivada.",
        )
    if _flag("RTM_ENABLE_B2") is not True:
        return _safety_error("RTM_ENABLE_B2 debe estar explícitamente activada.")
    if args.timeout < 1:
        return _safety_error("--timeout debe ser mayor que cero.")

    run_id = uuid.uuid4().hex
    email = f"rtm-smoke-{run_id[:12]}@example.com"

    identity_front = (
        "RTM SYNTHETIC IDENTITY FRONT\n"
        f"run_id={run_id}\n"
        "NO REAL CUSTOMER DATA\n"
    ).encode("utf-8")
    identity_back = (
        "RTM SYNTHETIC IDENTITY BACK\n"
        f"run_id={run_id}\n"
        "NO REAL CUSTOMER DATA\n"
    ).encode("utf-8")
    original_pdf = build_pdf(
        "RTM SYNTHETIC SOURCE DOCUMENT",
        f"Synthetic document flow test. Run ID: {run_id}. No real customer data.",
    )
    signed_pdf = build_pdf(
        "RTM SYNTHETIC SIGNED AUTHORIZATION",
        f"Synthetic signed authorization. Run ID: {run_id}.",
    )
    receipt_pdf = build_pdf(
        "RTM SYNTHETIC SUBMISSION RECEIPT",
        f"Synthetic receipt. Run ID: {run_id}.",
    )

    expected_bytes = {
        "identity_front": identity_front,
        "identity_back": identity_back,
        "original": original_pdf,
        "authorization_signed": signed_pdf,
        "submission_receipt": receipt_pdf,
    }
    expected_kinds = set(expected_bytes) | {"authorization_pdf"}

    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_document_flow_smoke",
        "version": "rtm_document_flow_smoke_v1_1",
        "environment": environment,
        "synthetic_only": True,
        "run_id": run_id,
        "keep_requested": bool(args.keep),
        "checks": {},
    }

    case_id: Optional[str] = None
    documents: list[dict[str, Any]] = []
    exit_code = 1

    try:
        with TestClient(app) as client:
            intake = _response_payload(
                client.post(
                    "/cases/intake-draft",
                    data={
                        "department": "other",
                        "case_type": "synthetic_document_flow",
                        "source_module": "rtm_document_flow_smoke",
                        "full_name": "RTM STAGING SYNTHETIC",
                        "dni_nie": "00000000T",
                        "domicilio_notif": "STAGING SYNTHETIC 1, 00000 TEST",
                        "street": "STAGING SYNTHETIC",
                        "street_number": "1",
                        "floor": "",
                        "door": "",
                        "postal_code": "00000",
                        "city": "TEST",
                        "province": "STAGING",
                        "email": email,
                        "telefono": "000000000",
                        "preferred_contact": "email",
                        "customer_comment": "Synthetic document flow smoke test.",
                        "representation_confirmed": "true",
                        "privacy_accepted": "true",
                    },
                    files={
                        "dni_front": (
                            "identity-front.txt",
                            identity_front,
                            "text/plain; charset=utf-8",
                        ),
                        "dni_back": (
                            "identity-back.txt",
                            identity_back,
                            "text/plain; charset=utf-8",
                        ),
                    },
                )
            )
            case_id = str(intake.get("case_id") or "")
            if not case_id:
                raise RuntimeError("intake-draft no devolvió case_id.")
            case_access_token = str(intake.get("case_access_token") or "")
            if not case_access_token:
                raise RuntimeError("intake-draft no devolvió la capacidad del expediente.")
            case_headers = {"X-RTM-Case-Token": case_access_token}
            report["case_id"] = case_id
            report["checks"]["intake_draft"] = True
            _mark_synthetic_case(case_id)

            authorization = _response_payload(
                client.post(
                    f"/cases/{case_id}/authorize",
                    headers=case_headers,
                )
            )
            report["checks"]["authorization_generated"] = bool(
                authorization.get("authorized")
            )

            appended = _response_payload(
                client.post(
                    f"/cases/{case_id}/append-documents",
                    headers=case_headers,
                    files=[
                        (
                            "files",
                            (
                                "synthetic-source.pdf",
                                original_pdf,
                                "application/pdf",
                            ),
                        )
                    ],
                )
            )
            report["checks"]["original_appended"] = bool(appended.get("ok"))

            signed = _response_payload(
                client.post(
                    f"/cases/{case_id}/authorization-signed",
                    headers=case_headers,
                    files={
                        "file": (
                            "synthetic-authorization-signed.pdf",
                            signed_pdf,
                            "application/pdf",
                        )
                    },
                )
            )
            report["checks"]["authorization_signed_uploaded"] = bool(
                signed.get("authorized")
            )

            receipt = _response_payload(
                client.post(
                    f"/cases/{case_id}/upload-receipt",
                    headers=case_headers,
                    files={
                        "file": (
                            "synthetic-submission-receipt.pdf",
                            receipt_pdf,
                            "application/pdf",
                        )
                    },
                )
            )
            report["checks"]["receipt_uploaded"] = bool(receipt.get("ok"))

            public_status = _response_payload(
                client.get(
                    f"/cases/{case_id}/public-status",
                    headers=case_headers,
                )
            )
            report["checks"]["public_status_submitted"] = (
                public_status.get("status") == "submitted"
                and bool(public_status.get("authorized"))
            )

            documents = _document_rows(case_id)
            actual_kinds = {str(item.get("kind") or "") for item in documents}
            report["document_count"] = len(documents)
            report["document_kinds"] = sorted(actual_kinds)
            report["checks"]["expected_kinds_present"] = expected_kinds.issubset(
                actual_kinds
            )

            roundtrip: dict[str, bool] = {}
            for kind, expected in expected_bytes.items():
                candidates = [item for item in documents if item["kind"] == kind]
                if not candidates:
                    roundtrip[kind] = False
                    continue
                downloaded = download_bytes(
                    candidates[-1]["bucket"], candidates[-1]["key"]
                )
                roundtrip[kind] = _sha256(downloaded) == _sha256(expected)

            auth_candidates = [
                item for item in documents if item["kind"] == "authorization_pdf"
            ]
            auth_pdf_ok = False
            if auth_candidates:
                auth_bytes = download_bytes(
                    auth_candidates[-1]["bucket"], auth_candidates[-1]["key"]
                )
                auth_pdf_ok = bool(auth_bytes.startswith(b"%PDF-") and len(auth_bytes) > 500)
            roundtrip["authorization_pdf"] = auth_pdf_ok
            report["b2_roundtrip"] = roundtrip
            report["checks"]["all_b2_roundtrips"] = all(roundtrip.values())

            original_doc = next(
                (item for item in reversed(documents) if item["kind"] == "original"),
                None,
            )
            if not original_doc:
                raise RuntimeError("No existe documento original para validar descargas.")

            presign = _response_payload(
                client.get(
                    "/files/presign",
                    params={
                        "case_id": case_id,
                        "document_id": original_doc["id"],
                        "expires": 60,
                    },
                    headers=case_headers,
                )
            )
            signed_url = str(presign.get("url") or "")
            if not signed_url:
                raise RuntimeError("files/presign no devolvió URL.")
            presigned_bytes = _read_url(signed_url, timeout=args.timeout)
            report["checks"]["presigned_original_download"] = (
                _sha256(presigned_bytes) == _sha256(original_pdf)
            )

            operator_token = (os.getenv("OPERATOR_TOKEN") or "").strip()
            if not operator_token:
                raise RuntimeError("OPERATOR_TOKEN no está configurado.")

            ops_listing = _response_payload(
                client.get(
                    f"/ops/cases/{case_id}/documents",
                    headers={"X-Operator-Token": operator_token},
                )
            )
            listed_ids = {
                str(item.get("id") or "")
                for item in (ops_listing.get("documents") or [])
                if isinstance(item, dict)
            }
            report["checks"]["ops_lists_original"] = original_doc["id"] in listed_ids

            ops_download = client.get(
                f"/ops/documents/{original_doc['id']}/download",
                headers={"X-Operator-Token": operator_token},
            )
            report["checks"]["ops_original_download_blocked"] = (
                ops_download.status_code == 403
            )

        tests_ok = all(bool(value) for value in report["checks"].values())
        report["tests_ok"] = tests_ok
        report["ok"] = tests_ok
        exit_code = 0 if tests_ok else 1

    except Exception as exc:
        report["error"] = _safe_error(exc)
        report["tests_ok"] = False
        report["ok"] = False
        exit_code = 1

    finally:
        if case_id:
            try:
                documents = _document_rows(case_id)
            except Exception:
                pass

            if args.keep:
                report["cleanup"] = {
                    "kept": True,
                    "case_id": case_id,
                    "document_count": len(documents),
                }
            else:
                cleanup = _delete_synthetic_case(case_id, documents)
                report["cleanup"] = {"kept": False, **cleanup}
                if cleanup["object_errors"] or not cleanup["database_deleted"]:
                    report["ok"] = False
                    report["tests_ok"] = False
                    exit_code = 1
        else:
            report["cleanup"] = {"kept": False, "nothing_created": True}

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
