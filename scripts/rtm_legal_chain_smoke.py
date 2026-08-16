#!/usr/bin/env python3
"""Prueba transaccional de la cadena jurídica completa de RTM CORE en staging.

La prueba usa un único documento sintético incluido en el repositorio y recorre:

    B2 -> extracción -> hechos -> familia -> especialista -> Previa Jurídica
    -> revisión OPS -> aprobación -> congelación -> Generate determinista

La base de datos se mantiene dentro de una transacción que siempre se revierte.
Los objetos sintéticos de B2 se eliminan al finalizar. No se aprueba el recurso
para presentación y no se invoca ningún correo ni sistema administrativo.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


SMOKE_VERSION = "rtm_legal_chain_smoke_v1_0"
SYNTHETIC_MARKER = "DOCUMENTO SINTÉTICO RTM — SOLO PRUEBAS DE STAGING"
FIXTURE_PATH = (
    _REPOSITORY_ROOT / "staging" / "fixtures" / "debt_invoice.txt"
)

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

_REQUIRED_TABLES = (
    "rtm_core_schema_migrations",
    "rtm_document_extractions",
    "rtm_validated_facts",
    "rtm_family_resolutions",
    "rtm_legal_previews",
    "rtm_generated_resources",
)

_EXPECTED_EVENT_TYPES = {
    "rtm_document_extraction_completed",
    "rtm_validated_facts_created",
    "rtm_validated_facts_frozen",
    "rtm_family_resolution_created",
    "rtm_family_resolution_locked",
    "rtm_legal_preview_created",
    "rtm_legal_preview_submitted_for_review",
    "rtm_legal_preview_approved",
    "rtm_legal_preview_frozen",
    "rtm_resource_generated_from_frozen_preview",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida la cadena jurídica completa con un expediente sintético "
            "transaccional de staging."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Imprime el informe JSON en una sola línea.",
    )
    return parser


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _safety_blockers() -> list[str]:
    blockers: list[str] = []
    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    namespace = (os.getenv("RTM_DATA_NAMESPACE") or "").strip().lower()
    policy = (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
    document_policy = (
        os.getenv("RTM_DOCUMENT_INPUT_POLICY") or ""
    ).strip().lower()
    confirmation = (os.getenv("RTM_STAGING_CONFIRM") or "").strip()
    live_allowed = (
        os.getenv("RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION") or ""
    ).strip()

    if environment != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in namespace:
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if policy != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if _flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    if _flag("RTM_ENABLE_B2") is not True:
        blockers.append("RTM_ENABLE_B2_must_be_true")
    if _flag("RTM_ENABLE_DOCUMENT_PROVIDER") is not True:
        blockers.append("RTM_ENABLE_DOCUMENT_PROVIDER_must_be_true")
    if document_policy != "synthetic_only":
        blockers.append("RTM_DOCUMENT_INPUT_POLICY_must_be_synthetic_only")
    if confirmation != "SYNTHETIC_ONLY":
        blockers.append("RTM_STAGING_CONFIRM_must_be_SYNTHETIC_ONLY")
    if live_allowed != "1":
        blockers.append(
            "RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION_must_be_1"
        )
    if _flag("RTM_ENABLE_OUTBOUND_EMAIL") is not False:
        blockers.append("RTM_ENABLE_OUTBOUND_EMAIL_must_be_false")
    if _flag("RTM_ENABLE_EXTERNAL_SUBMISSION") is not False:
        blockers.append("RTM_ENABLE_EXTERNAL_SUBMISSION_must_be_false")
    if _flag("RTM_ENABLE_FINAL_PAYMENTS") is not False:
        blockers.append("RTM_ENABLE_FINAL_PAYMENTS_must_be_false")
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        blockers.append("OPENAI_API_KEY_missing")
    if not (os.getenv("OPENAI_DOCUMENT_MODEL") or "").strip():
        blockers.append("OPENAI_DOCUMENT_MODEL_missing")
    return blockers


def _safe_error(exc: BaseException) -> str:
    value = f"{type(exc).__name__}: {exc}"
    return value[:1800]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fixture_bytes() -> bytes:
    content = FIXTURE_PATH.read_bytes()
    if SYNTHETIC_MARKER.encode("utf-8") not in content:
        raise RuntimeError(
            "El fixture no conserva la marca obligatoria de contenido sintético."
        )
    return content


def _schema_missing(conn) -> list[str]:
    from sqlalchemy import text

    missing: list[str] = []
    for table_name in _REQUIRED_TABLES:
        exists = conn.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one()
        if not exists:
            missing.append(table_name)
    return missing


def _insert_case(conn, *, case_id: str, run_id: str) -> None:
    from sqlalchemy import text

    interested = {
        "full_name": "RTM STAGING SYNTHETIC",
        "dni_nie": "00000000T",
        "dni": "00000000T",
        "domicilio_notif": "STAGING SYNTHETIC 1, 00000 TEST",
        "domicilio": "STAGING SYNTHETIC 1, 00000 TEST",
        "synthetic_only": True,
        "run_id": run_id,
    }
    conn.execute(
        text(
            """
            INSERT INTO cases(
                id, contact_email, contact_name, status, payment_status,
                authorized, authorized_at, interested_data, department,
                case_type, customer_comment, source_module, category,
                organismo, expediente_ref, test_mode, override_deadlines,
                created_at, updated_at
            ) VALUES (
                CAST(:case_id AS UUID), :email, :contact_name,
                'core_review_pending', 'paid', TRUE, NOW(),
                CAST(:interested AS JSONB), 'debt', 'unpaid_invoice',
                :comment, 'rtm_legal_chain_smoke', 'debt',
                'RTM SYNTHETIC COUNTERPARTY', :expediente_ref,
                FALSE, TRUE, NOW(), NOW()
            )
            """
        ),
        {
            "case_id": case_id,
            "email": f"rtm-legal-chain-{run_id[:12]}@example.invalid",
            "contact_name": "RTM STAGING SYNTHETIC",
            "interested": json.dumps(interested, ensure_ascii=False),
            "comment": (
                "Synthetic legal-chain smoke test. No real customer data."
            ),
            "expediente_ref": f"RTM-SYNTH-{run_id[:12]}",
        },
    )


def _insert_source_document(
    conn,
    *,
    case_id: str,
    content: bytes,
    bucket: str,
    key: str,
) -> str:
    from sqlalchemy import text

    document_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO documents(
                id, case_id, kind, b2_bucket, b2_key, sha256, mime,
                size_bytes, created_at
            ) VALUES (
                CAST(:document_id AS UUID), CAST(:case_id AS UUID),
                'original', :bucket, :key, :sha256, 'text/plain',
                :size_bytes, NOW()
            )
            """
        ),
        {
            "document_id": document_id,
            "case_id": case_id,
            "bucket": bucket,
            "key": key,
            "sha256": _sha256(content),
            "size_bytes": len(content),
        },
    )
    return document_id


def _document_rows(conn, case_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT CAST(id AS TEXT), COALESCE(kind,''),
                   COALESCE(b2_bucket,''), COALESCE(b2_key,''),
                   COALESCE(mime,''), COALESCE(size_bytes,0)
            FROM documents
            WHERE case_id=CAST(:case_id AS UUID)
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
        }
        for row in rows
    ]


def _find_document(
    documents: list[dict[str, Any]],
    kind: str,
) -> Optional[dict[str, Any]]:
    candidates = [
        document for document in documents
        if document["kind"] == kind
    ]
    return candidates[-1] if candidates else None


def _generate_is_blocked(
    conn,
    *,
    case_id: str,
    preview_id: str,
) -> bool:
    from fastapi import HTTPException

    from rtm_core.generation_gateway import generate_from_frozen_preview

    try:
        generate_from_frozen_preview(
            conn,
            case_id=case_id,
            preview_id=preview_id,
            generated_by="staging-smoke:premature-generate",
        )
    except HTTPException as exc:
        detail = json.dumps(exc.detail, ensure_ascii=False, default=str)
        return exc.status_code == 409 and "congelad" in detail.lower()
    return False


def _authority_links(
    conn,
    *,
    case_id: str,
) -> Optional[dict[str, str]]:
    from sqlalchemy import text

    row = conn.execute(
        text(
            """
            SELECT CAST(vf.id AS TEXT),
                   CAST(vf.source_extraction_id AS TEXT),
                   CAST(fr.id AS TEXT),
                   CAST(fr.validated_facts_id AS TEXT),
                   CAST(lp.id AS TEXT),
                   CAST(lp.validated_facts_id AS TEXT),
                   CAST(lp.family_resolution_id AS TEXT),
                   CAST(gr.id AS TEXT),
                   CAST(gr.legal_preview_id AS TEXT),
                   CAST(gr.docx_document_id AS TEXT),
                   CAST(gr.pdf_document_id AS TEXT)
            FROM rtm_validated_facts vf
            JOIN rtm_family_resolutions fr
              ON fr.validated_facts_id=vf.id
            JOIN rtm_legal_previews lp
              ON lp.validated_facts_id=vf.id
             AND lp.family_resolution_id=fr.id
            JOIN rtm_generated_resources gr
              ON gr.legal_preview_id=lp.id
            WHERE vf.case_id=CAST(:case_id AS UUID)
            ORDER BY gr.sequence DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        return None
    keys = (
        "facts_id",
        "source_extraction_id",
        "family_id",
        "family_facts_id",
        "preview_id",
        "preview_facts_id",
        "preview_family_id",
        "resource_id",
        "resource_preview_id",
        "docx_document_id",
        "pdf_document_id",
    )
    return {
        key: str(value) if value is not None else ""
        for key, value in zip(keys, row)
    }


def _event_types(conn, *, case_id: str) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT DISTINCT type
            FROM events
            WHERE case_id=CAST(:case_id AS UUID)
            """
        ),
        {"case_id": case_id},
    ).fetchall()
    return {str(row[0]) for row in rows}


def _list_case_keys(
    client,
    *,
    bucket: str,
    case_id: str,
) -> list[str]:
    prefix = f"cases/{case_id}/"
    keys: list[str] = []
    continuation: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
        }
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key:
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        continuation = str(response.get("NextContinuationToken") or "")
        if not continuation:
            break
    return keys


def _cleanup_b2(
    *,
    case_id: str,
    known_objects: set[tuple[str, str]],
) -> dict[str, Any]:
    from b2_storage import get_b2_bucket, get_s3_client

    errors: list[str] = []
    deleted = 0
    remaining = -1
    bucket = get_b2_bucket()
    try:
        client = get_s3_client()
        objects = {
            (object_bucket, key)
            for object_bucket, key in known_objects
            if object_bucket and key
        }
        try:
            objects.update(
                (bucket, key)
                for key in _list_case_keys(
                    client,
                    bucket=bucket,
                    case_id=case_id,
                )
            )
        except Exception as exc:
            errors.append(f"list_before_delete:{_safe_error(exc)}")

        for object_bucket, key in sorted(objects):
            try:
                client.delete_object(Bucket=object_bucket, Key=key)
                deleted += 1
            except Exception as exc:
                errors.append(f"delete_object:{_safe_error(exc)}")

        try:
            remaining = len(
                _list_case_keys(
                    client,
                    bucket=bucket,
                    case_id=case_id,
                )
            )
        except Exception as exc:
            errors.append(f"list_after_delete:{_safe_error(exc)}")
    except Exception as exc:
        errors.append(f"b2_cleanup:{_safe_error(exc)}")

    return {
        "objects_deleted": deleted,
        "object_errors": errors,
        "objects_remaining": remaining,
    }


def _case_absent(engine, case_id: str) -> tuple[bool, Optional[str]]:
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            count = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM cases
                        WHERE id=CAST(:case_id AS UUID)
                        """
                    ),
                    {"case_id": case_id},
                ).scalar_one()
                or 0
            )
        return count == 0, None
    except Exception as exc:
        return False, _safe_error(exc)


def _print_report(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environment = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
    run_id = uuid.uuid4().hex
    case_id = str(uuid.uuid4())
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_legal_chain_smoke",
        "version": SMOKE_VERSION,
        "environment": environment,
        "synthetic_only": True,
        "transactional": True,
        "run_id": run_id,
        "case_id": case_id,
        "model": (
            os.getenv("OPENAI_DOCUMENT_MODEL") or ""
        ).strip(),
        "checks": {},
    }

    blockers = _safety_blockers()
    if blockers:
        report["blockers"] = blockers
        report["safe"] = False
        _print_report(report, compact=args.compact)
        return 2

    engine = None
    connection = None
    transaction = None
    database_rolled_back = False
    known_objects: set[tuple[str, str]] = set()
    tests_ok = False

    try:
        from sqlalchemy import text

        from b2_storage import download_bytes, upload_bytes
        from database import get_engine
        from rtm_core.authority_repository import (
            create_family_resolution,
            create_validated_facts,
            freeze_validated_facts,
            lock_family_resolution,
        )
        from rtm_core.document_extraction import (
            extract_service_documents,
        )
        from rtm_core.document_extraction_repository import (
            persist_document_extraction,
            prepare_document_extraction,
        )
        from rtm_core.document_normalization import (
            normalize_document_packet,
        )
        from rtm_core.document_provider_retry import (
            RetryingOpenAIResponsesDocumentProvider,
        )
        from rtm_core.family_dispatch import resolve_family
        from rtm_core.generation_gateway import (
            generate_from_frozen_preview,
        )
        from rtm_core.preview_repository import (
            approve_preview,
            create_preview,
            freeze_preview,
            submit_for_review,
        )
        from rtm_core.specialist_dispatch import build_legal_preview

        fixture = _fixture_bytes()
        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()

        missing_schema = _schema_missing(connection)
        if missing_schema:
            raise RuntimeError(
                "Falta esquema RTM CORE: " + ", ".join(missing_schema)
            )
        report["checks"]["core_schema_ready"] = True

        _insert_case(connection, case_id=case_id, run_id=run_id)
        report["checks"]["synthetic_case_created"] = True
        report["checks"]["gateway_test_mode_false_transactional"] = True

        source_bucket, source_key = upload_bytes(
            case_id,
            "synthetic_legal_chain_source",
            fixture,
            ".txt",
            "text/plain",
        )
        known_objects.add((source_bucket, source_key))
        source_document_id = _insert_source_document(
            connection,
            case_id=case_id,
            content=fixture,
            bucket=source_bucket,
            key=source_key,
        )
        report["source_document_id"] = source_document_id
        report["checks"]["source_uploaded_to_b2"] = (
            download_bytes(source_bucket, source_key) == fixture
        )

        service, documents = prepare_document_extraction(
            connection,
            case_id=case_id,
            requested_document_ids=[source_document_id],
        )
        provider = RetryingOpenAIResponsesDocumentProvider()
        extraction_result = extract_service_documents(
            case_id=case_id,
            service=service,
            documents=documents,
            provider=provider,
            byte_loader=download_bytes,
        )
        extraction_record = persist_document_extraction(
            connection,
            case_id=case_id,
            result=extraction_result,
            created_by="staging-smoke:document-extraction",
        )
        report["extraction_id"] = extraction_record.id
        report["extraction"] = {
            "provider_version": extraction_record.provider_version,
            "model": extraction_record.model,
            "extractor_version": extraction_record.extractor_version,
            "observation_count": len(extraction_record.packet.observations),
            "declared_unresolved_count": len(
                extraction_record.packet.declared_unresolved
            ),
        }
        report["checks"]["extraction_persisted"] = bool(
            extraction_record.status == "completed"
            and extraction_record.packet.source_document_ids
            == [source_document_id]
        )

        normalized = normalize_document_packet(extraction_record.packet)
        report["normalization"] = {
            "accepted_fields": list(normalized.accepted_fields),
            "unresolved_fields": list(normalized.unresolved_fields),
            "conflicted_fields": list(normalized.conflicted_fields),
        }
        facts_record = create_validated_facts(
            connection,
            case_id=case_id,
            facts=normalized.facts,
            created_by="staging-smoke:document-normalization",
        )
        connection.execute(
            text(
                """
                UPDATE rtm_validated_facts
                SET source_extraction_id=CAST(:extraction_id AS UUID),
                    updated_at=NOW()
                WHERE id=CAST(:facts_id AS UUID)
                """
            ),
            {
                "extraction_id": extraction_record.id,
                "facts_id": facts_record.id,
            },
        )
        facts_record = freeze_validated_facts(
            connection,
            case_id,
            facts_record.id,
            "ops:staging-legal-chain-smoke",
        )
        report["facts_id"] = facts_record.id
        report["checks"]["facts_created_and_frozen"] = bool(
            facts_record.frozen
            and source_document_id
            in facts_record.facts.source_document_ids
        )

        resolution = resolve_family(facts_record.facts)
        report["family_resolution"] = {
            "status": resolution.status.value,
            "family": resolution.family,
            "specialist": resolution.specialist,
            "confidence": resolution.confidence,
        }
        report["checks"]["family_resolved_exactly"] = bool(
            resolution.status.value == "resolved"
            and resolution.family == "factura_impagada"
            and resolution.specialist == "debt.unpaid_invoice"
            and resolution.confidence >= 0.90
        )
        family_record = create_family_resolution(
            connection,
            case_id=case_id,
            resolution=resolution,
            created_by="staging-smoke:family-dispatch",
            validated_facts_id=facts_record.id,
        )
        family_record = lock_family_resolution(
            connection,
            case_id,
            family_record.id,
            "ops:staging-legal-chain-smoke",
        )
        report["family_resolution_id"] = family_record.id
        report["checks"]["family_locked"] = bool(
            family_record.locked
            and family_record.validated_facts_id == facts_record.id
        )

        preview = build_legal_preview(facts_record, family_record)
        blocking_items = [
            item.code
            for item in preview.missing_items
            if item.severity.value == "blocking"
        ]
        report["legal_preview"] = {
            "family": preview.family,
            "specialist": preview.specialist,
            "document_type": preview.document_type,
            "argument_count": len(preview.legal_arguments),
            "blocking_items": blocking_items,
        }
        report["checks"]["specialist_built_preview"] = bool(
            preview.family == "factura_impagada"
            and preview.specialist == "debt.unpaid_invoice"
            and preview.legal_arguments
            and not blocking_items
        )

        preview_record = create_preview(
            connection,
            case_id=case_id,
            preview=preview,
            created_by="staging-smoke:debt.unpaid_invoice",
        )
        report["preview_id"] = preview_record.id
        report["checks"]["preview_created_as_draft"] = (
            preview_record.status.value == "draft"
        )
        report["checks"]["generate_blocked_before_freeze"] = (
            _generate_is_blocked(
                connection,
                case_id=case_id,
                preview_id=preview_record.id,
            )
        )

        preview_record = submit_for_review(
            connection,
            case_id,
            preview_record.id,
            "ops:staging-legal-chain-smoke",
        )
        report["checks"]["preview_submitted_for_review"] = (
            preview_record.status.value == "ops_review"
        )
        preview_record = approve_preview(
            connection,
            case_id,
            preview_record.id,
            "ops:staging-legal-chain-smoke",
        )
        report["checks"]["preview_approved"] = bool(
            preview_record.status.value == "approved"
            and preview_record.approved_by
        )
        preview_record = freeze_preview(
            connection,
            case_id,
            preview_record.id,
            "ops:staging-legal-chain-smoke",
        )
        report["checks"]["preview_frozen"] = bool(
            preview_record.status.value == "frozen"
            and preview_record.frozen_at
        )

        resource = generate_from_frozen_preview(
            connection,
            case_id=case_id,
            preview_id=preview_record.id,
            generated_by="staging-smoke:deterministic-generate",
        )
        same_resource = generate_from_frozen_preview(
            connection,
            case_id=case_id,
            preview_id=preview_record.id,
            generated_by="staging-smoke:deterministic-generate-retry",
        )
        report["resource_id"] = resource.id
        report["generated_resource"] = {
            "status": resource.status,
            "generator_version": resource.generator_version,
            "content_sha256": resource.content_sha256,
            "approved_for_submission": bool(resource.approved_at),
        }
        report["checks"]["generation_completed"] = bool(
            resource.status == "final_ready"
            and resource.preview_payload_sha256
            == preview_record.payload_sha256
        )
        report["checks"]["generation_idempotent"] = (
            same_resource.id == resource.id
        )

        document_rows = _document_rows(connection, case_id)
        for document in document_rows:
            if document["bucket"] and document["key"]:
                known_objects.add(
                    (document["bucket"], document["key"])
                )

        docx_document = _find_document(
            document_rows,
            "rtm_generated_docx",
        )
        pdf_document = _find_document(
            document_rows,
            "rtm_generated_pdf",
        )
        docx_bytes = (
            download_bytes(
                docx_document["bucket"],
                docx_document["key"],
            )
            if docx_document
            else b""
        )
        pdf_bytes = (
            download_bytes(
                pdf_document["bucket"],
                pdf_document["key"],
            )
            if pdf_document
            else b""
        )
        report["checks"]["generated_docx_valid"] = bool(
            docx_document
            and zipfile.is_zipfile(io.BytesIO(docx_bytes))
        )
        report["checks"]["generated_pdf_valid"] = bool(
            pdf_document
            and pdf_bytes.startswith(b"%PDF-")
            and len(pdf_bytes) > 500
        )

        links = _authority_links(connection, case_id=case_id)
        report["checks"]["authority_links_intact"] = bool(
            links
            and links["source_extraction_id"]
            == extraction_record.id
            and links["family_facts_id"] == facts_record.id
            and links["preview_facts_id"] == facts_record.id
            and links["preview_family_id"] == family_record.id
            and links["resource_preview_id"] == preview_record.id
            and links["docx_document_id"] == resource.docx_document_id
            and links["pdf_document_id"] == resource.pdf_document_id
        )

        event_types = _event_types(connection, case_id=case_id)
        report["checks"]["authority_events_complete"] = (
            _EXPECTED_EVENT_TYPES.issubset(event_types)
        )

        case_status = str(
            connection.execute(
                text(
                    """
                    SELECT status
                    FROM cases
                    WHERE id=CAST(:case_id AS UUID)
                    """
                ),
                {"case_id": case_id},
            ).scalar_one()
        )
        report["checks"]["case_stops_at_final_ready"] = (
            case_status == "final_ready"
            and resource.approved_by is None
            and resource.approved_at is None
        )
        report["checks"]["external_effects_remain_disabled"] = bool(
            _flag("RTM_ENABLE_EXTERNAL_SUBMISSION") is False
            and _flag("RTM_ENABLE_OUTBOUND_EMAIL") is False
            and _flag("RTM_ENABLE_FINAL_PAYMENTS") is False
        )

        tests_ok = all(
            bool(value)
            for value in report["checks"].values()
        )
        report["tests_ok"] = tests_ok
    except Exception as exc:
        report["error"] = _safe_error(exc)
        report["tests_ok"] = False
    finally:
        if transaction is not None:
            try:
                if transaction.is_active:
                    transaction.rollback()
                database_rolled_back = True
            except Exception as exc:
                report["rollback_error"] = _safe_error(exc)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

        case_absent = False
        case_absent_error: Optional[str] = None
        if engine is not None:
            case_absent, case_absent_error = _case_absent(
                engine,
                case_id,
            )

        b2_cleanup = _cleanup_b2(
            case_id=case_id,
            known_objects=known_objects,
        )
        cleanup = {
            "database_rolled_back": database_rolled_back,
            "case_absent_after_rollback": case_absent,
            "database_check_error": case_absent_error,
            **b2_cleanup,
        }
        cleanup["ok"] = bool(
            database_rolled_back
            and case_absent
            and not case_absent_error
            and not cleanup["object_errors"]
            and cleanup["objects_remaining"] == 0
        )
        report["cleanup"] = cleanup
        report["ok"] = bool(tests_ok and cleanup["ok"])

    _print_report(report, compact=args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
