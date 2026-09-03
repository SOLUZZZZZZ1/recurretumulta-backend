"""Automatización OPS subordinada a RTM CORE.

El worker nunca extrae, clasifica ni genera. Solo puede presentar un PDF que:
- procede de una Previa Jurídica congelada;
- está registrado en rtm_generated_resources;
- ha sido aprobado expresamente para presentación por OPS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text

from b2_storage import (
    delete_object,
    download_bytes_limited,
    get_b2_bucket,
    upload_bytes,
)
from case_authority import verify_signed_case_authority
from database import get_engine
from dgt_client import DGTNotConfigured, submit_pdf
from rtm_core.runtime_capabilities import require_http_capability


AUTOMATION_VERSION = "rtm_submission_automation_v1_1_fail_closed"
MAX_AUTOMATION_PDF_BYTES = 20 * 1024 * 1024
MAX_PROVIDER_REFERENCE_CHARS = 256


def _cleanup_uploaded_receipt(coordinates: tuple[str, str]) -> None:
    try:
        delete_object(*coordinates)
    except Exception:
        pass


@contextmanager
def _b2_backed_transaction(engine, coordinates: tuple[str, str]):
    """Compensa B2 si cualquier sentencia o el commit SQL falla."""

    try:
        with engine.begin() as conn:
            yield conn
    except Exception:
        _cleanup_uploaded_receipt(coordinates)
        raise


def _failure_payload(error_code: str, *, resource_id: str) -> Dict[str, str]:
    """Build an allowlisted diagnostic payload without exception text."""

    safe_code = str(error_code or "automation_failed")[:80]
    safe_resource_id = str(resource_id or "")[:128]
    return {
        "error_code": safe_code,
        "resource_id": safe_resource_id,
    }


def _provider_reference(value: Any, *, field: str) -> Optional[str]:
    """Accept only small, printable identifiers from the external provider."""

    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    candidate = str(value).strip()
    if not candidate:
        return None
    if len(candidate) > MAX_PROVIDER_REFERENCE_CHARS:
        raise ValueError(f"invalid_{field}")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise ValueError(f"invalid_{field}")
    return candidate


def _require_external_submission_capability() -> None:
    state = require_http_capability("external_submission")
    if (
        not state.configured
        or state.reason != "explicitly_enabled"
        or state.environment not in {"staging", "production"}
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "external_submission_unavailable"},
        )


def _event(conn, case_id: str, typ: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO events(case_id, type, payload, created_at) "
            "VALUES (:c,:t,CAST(:p AS JSONB),NOW())"
        ),
        {
            "c": case_id,
            "t": typ,
            "p": json.dumps(payload, ensure_ascii=False),
        },
    )


def _core_schema_ready(conn) -> bool:
    row = conn.execute(
        text("SELECT to_regclass('public.rtm_generated_resources')")
    ).fetchone()
    return bool(row and row[0])


def _verified_submission_evidence(conn, case_id: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1
            FROM events e
            JOIN documents d
              ON d.case_id=e.case_id
             AND d.kind='justificante_presentacion'
             AND d.b2_bucket=e.payload#>>'{justificante,bucket}'
             AND d.b2_key=e.payload#>>'{justificante,key}'
            WHERE e.case_id=:id
              AND e.type='dgt_submitted'
              AND COALESCE(e.payload->>'receipt_sha256', '') <> ''
              AND (
                COALESCE(e.payload->>'registro', '') <> ''
                OR COALESCE(e.payload->>'csv', '') <> ''
              )
            LIMIT 1
            """
        ),
        {"id": case_id},
    ).fetchone()
    return bool(row)


def _receipt_document_exists(conn, case_id: str, bucket: str, key: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM documents "
            "WHERE case_id=:id AND kind='justificante_presentacion' "
            "AND b2_bucket=:bucket AND b2_key=:key LIMIT 1"
        ),
        {"id": case_id, "bucket": bucket, "key": key},
    ).fetchone()
    return bool(row)


def _approved_core_pdf(conn, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            """
            SELECT
                gr.id AS resource_id,
                gr.legal_preview_id,
                gr.generator_version,
                gr.preview_payload_sha256,
                gr.content_sha256,
                gr.approved_by,
                gr.approved_at,
                d.id AS document_id,
                d.kind,
                d.b2_bucket,
                d.b2_key,
                d.sha256 AS pdf_sha256,
                d.mime,
                d.size_bytes,
                d.created_at
            FROM rtm_generated_resources gr
            JOIN rtm_legal_previews lp
              ON lp.id = gr.legal_preview_id
             AND lp.case_id = gr.case_id
            JOIN documents d
              ON d.id = gr.pdf_document_id
             AND d.case_id = gr.case_id
            WHERE gr.case_id = :case_id
              AND gr.status = 'final_ready'
              AND gr.invalidated_at IS NULL
              AND gr.approved_at IS NOT NULL
              AND lp.status = 'frozen'
              AND lp.invalidated_at IS NULL
              AND gr.preview_payload_sha256 = lp.payload_sha256
              AND d.mime = 'application/pdf'
              AND d.kind = 'rtm_generated_pdf'
            ORDER BY gr.sequence DESC
            LIMIT 1
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        return None
    mapping = row._mapping
    pdf_sha256 = str(mapping["pdf_sha256"] or "").strip().lower()
    if len(pdf_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in pdf_sha256
    ):
        # Los recursos legacy sin una huella de los bytes PDF no son aptos para
        # una presentación externa. No se reutiliza la huella del texto.
        return None
    return {
        "resource_id": str(mapping["resource_id"]),
        "preview_id": str(mapping["legal_preview_id"]),
        "generator_version": str(mapping["generator_version"]),
        "preview_payload_sha256": str(mapping["preview_payload_sha256"]),
        "content_sha256": str(mapping["content_sha256"]),
        "approved_by": str(mapping["approved_by"] or ""),
        "approved_at": str(mapping["approved_at"] or ""),
        "document_id": str(mapping["document_id"]),
        "kind": str(mapping["kind"]),
        "bucket": str(mapping["b2_bucket"]),
        "key": str(mapping["b2_key"]),
        "pdf_sha256": pdf_sha256,
        "mime": str(mapping["mime"]),
        "size_bytes": int(mapping["size_bytes"] or 0),
        "created_at": str(mapping["created_at"]),
    }


def _ensure_submission_row(conn, case_id: str, resource_id: str) -> None:
    row = conn.execute(
        text(
            """
            SELECT id FROM submissions
            WHERE case_id=:case_id AND channel='DGT_CORE'
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if row:
        conn.execute(
            text(
                """
                UPDATE submissions
                SET status='processing',
                    context_intensity=:resource_id,
                    last_error=NULL,
                    updated_at=NOW()
                WHERE id=:id
                """
            ),
            {"id": row[0], "resource_id": resource_id},
        )
        return

    conn.execute(
        text(
            """
            INSERT INTO submissions(
                case_id, channel, status, dry_run, context_intensity,
                created_at, updated_at
            ) VALUES (
                :case_id, 'DGT_CORE', 'processing', FALSE, :resource_id,
                NOW(), NOW()
            )
            """
        ),
        {"case_id": case_id, "resource_id": resource_id},
    )


def _reset_claim(case_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE cases
                SET status='ready_to_submit', updated_at=NOW()
                WHERE id=:case_id AND status='submitting'
                """
            ),
            {"case_id": case_id},
        )
        conn.execute(
            text(
                """
                UPDATE submissions
                SET status='blocked', last_error=:error, updated_at=NOW()
                WHERE case_id=:case_id AND channel='DGT_CORE'
                """
            ),
            {
                "case_id": case_id,
                "error": str(payload.get("error_code") or "automation_failed")[:80],
            },
        )
        _event(conn, case_id, event_type, payload)


def _block_unavailable_submission(
    case_id: str,
    *,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """Make a non-operational provider visible and stop automatic retries."""

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE cases
                SET status='submission_blocked', updated_at=NOW()
                WHERE id=:case_id AND status='submitting'
                """
            ),
            {"case_id": case_id},
        )
        conn.execute(
            text(
                """
                UPDATE submissions
                SET status='blocked', last_error=:error, updated_at=NOW()
                WHERE case_id=:case_id AND channel='DGT_CORE'
                """
            ),
            {
                "case_id": case_id,
                "error": str(payload.get("error_code") or "provider_unavailable")[:80],
            },
        )
        _event(conn, case_id, event_type, payload)


def _hold_claim_for_reconciliation(
    case_id: str,
    *,
    case_status: str,
    submission_status: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """Bloquea reintentos cuando una llamada externa pudo producir efectos."""

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE cases
                SET status=:status, updated_at=NOW()
                WHERE id=:case_id AND status='submitting'
                """
            ),
            {"case_id": case_id, "status": case_status},
        )
        conn.execute(
            text(
                """
                UPDATE submissions
                SET status=:status, last_error=:error, updated_at=NOW()
                WHERE case_id=:case_id AND channel='DGT_CORE'
                """
            ),
            {
                "case_id": case_id,
                "status": submission_status,
                "error": str(payload.get("error_code") or "automation_failed")[:80],
            },
        )
        _event(conn, case_id, event_type, payload)


def _claim_case(case_id: str) -> Dict[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        if not _core_schema_ready(conn):
            raise HTTPException(status_code=503, detail="RTM CORE schema no aplicado")

        row = conn.execute(
            text(
                """
                SELECT id, status, payment_status, authorized,
                       COALESCE(test_mode, FALSE) AS test_mode
                FROM cases
                WHERE id=:case_id
                FOR UPDATE
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        mapping = row._mapping
        status = str(mapping["status"] or "")
        if str(mapping["payment_status"] or "") != "paid":
            raise HTTPException(status_code=402, detail="Pago requerido")
        if not bool(mapping["authorized"]):
            raise HTTPException(status_code=409, detail="Falta autorización")
        if bool(mapping["test_mode"]):
            raise HTTPException(status_code=409, detail="No se presenta test_mode")
        authority = verify_signed_case_authority(conn, case_id)

        if _verified_submission_evidence(conn, case_id):
            conn.execute(
                text("UPDATE cases SET status='submitted', updated_at=NOW() WHERE id=:case_id"),
                {"case_id": case_id},
            )
            return {
                "case_id": case_id,
                "already_submitted": True,
                "status": "submitted",
            }

        if status != "ready_to_submit":
            raise HTTPException(
                status_code=409,
                detail={"code": "case_not_ready_for_submission"},
            )

        pdf = _approved_core_pdf(conn, case_id)
        if not pdf:
            raise HTTPException(
                status_code=409,
                detail="No existe un PDF CORE aprobado para presentación",
            )
        expected_key_prefix = f"cases/{case_id}/"
        if (
            pdf["bucket"] != get_b2_bucket()
            or not pdf["key"].startswith(expected_key_prefix)
            or ".." in pdf["key"].split("/")
            or "\\" in pdf["key"]
        ):
            raise HTTPException(
                status_code=409,
                detail="El PDF aprobado está fuera de la custodia del expediente",
            )

        signed_attestation_sha256 = str(
            authority.get("signed_document_attestation", {}).get(
                "material_sha256"
            )
            or ""
        )

        claimed = conn.execute(
            text(
                """
                UPDATE cases
                SET status='submitting', updated_at=NOW()
                WHERE id=:case_id AND status='ready_to_submit'
                RETURNING id
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        if not claimed:
            raise HTTPException(status_code=409, detail="El expediente ya está siendo procesado")

        _ensure_submission_row(conn, case_id, pdf["resource_id"])
        _event(
            conn,
            case_id,
            "rtm_submission_claimed",
            {
                "resource_id": pdf["resource_id"],
                "preview_id": pdf["preview_id"],
                "pdf_document_id": pdf["document_id"],
                "content_sha256": pdf["content_sha256"],
                "rendered_content_sha256": pdf["content_sha256"],
                "pdf_sha256": pdf["pdf_sha256"],
                "authority_material_sha256": authority["material_sha256"],
                "signed_document_attestation_sha256": signed_attestation_sha256,
                "automation_version": AUTOMATION_VERSION,
            },
        )
        return {
            "case_id": case_id,
            "already_submitted": False,
            "pdf": pdf,
            "authority_material_sha256": authority["material_sha256"],
            "signed_document_attestation_sha256": signed_attestation_sha256,
        }


def _revalidate_claim(case_id: str, claim: Dict[str, Any]) -> None:
    """Recheck every claimed immutable identifier immediately before egress."""

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, payment_status, COALESCE(authorized, FALSE)
                FROM cases
                WHERE id=:case_id
                FOR UPDATE
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        if (
            not row
            or str(row[0] or "") != "submitting"
            or str(row[1] or "") != "paid"
            or not bool(row[2])
        ):
            raise HTTPException(status_code=409, detail="La reclamación de envío caducó")
        authority = verify_signed_case_authority(conn, case_id)
        current_pdf = _approved_core_pdf(conn, case_id)
        expected_pdf = claim["pdf"]
        if not current_pdf:
            raise HTTPException(status_code=409, detail="El PDF aprobado dejó de ser válido")
        authority_digest = str(authority.get("material_sha256") or "")
        signed_digest = str(
            authority.get("signed_document_attestation", {}).get(
                "material_sha256"
            )
            or ""
        )
        if (
            not hmac.compare_digest(
                authority_digest,
                str(claim.get("authority_material_sha256") or ""),
            )
            or not hmac.compare_digest(
                signed_digest,
                str(claim.get("signed_document_attestation_sha256") or ""),
            )
            or any(
                str(current_pdf.get(key) or "")
                != str(expected_pdf.get(key) or "")
                for key in (
                    "resource_id",
                    "preview_id",
                    "document_id",
                    "content_sha256",
                    "pdf_sha256",
                    "bucket",
                    "key",
                    "size_bytes",
                )
            )
        ):
            raise HTTPException(status_code=409, detail="La evidencia de envío cambió")


def submit_case_fully_automatic(case_id: str) -> Dict[str, Any]:
    """Presenta exclusivamente un recurso CORE previamente aprobado por OPS."""

    _require_external_submission_capability()
    claim = _claim_case(case_id)
    if claim.get("already_submitted"):
        return {
            "ok": True,
            "case_id": case_id,
            "status": "submitted",
            "skipped": True,
            "reason": "receipt_already_exists",
        }

    pdf = claim["pdf"]
    external_call_started = False
    try:
        pdf_bytes = download_bytes_limited(
            pdf["bucket"],
            pdf["key"],
            max_bytes=MAX_AUTOMATION_PDF_BYTES,
            case_id=case_id,
        )
        if not pdf_bytes:
            raise RuntimeError("El PDF aprobado está vacío")
        if not isinstance(pdf_bytes, (bytes, bytearray)):
            raise RuntimeError("El documento aprobado no contiene bytes")
        pdf_bytes = bytes(pdf_bytes)
        if len(pdf_bytes) > MAX_AUTOMATION_PDF_BYTES:
            raise RuntimeError("El PDF aprobado supera el tamaño máximo")
        if not pdf_bytes.startswith(b"%PDF-"):
            raise RuntimeError("El documento aprobado no es un PDF válido")
        if pdf["size_bytes"] and len(pdf_bytes) != pdf["size_bytes"]:
            raise RuntimeError("El tamaño del PDF no coincide con el documento registrado")
        actual_pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        if not hmac.compare_digest(actual_pdf_sha256, pdf["pdf_sha256"]):
            raise RuntimeError("El hash del PDF no coincide con el recurso CORE aprobado")

        _revalidate_claim(case_id, claim)

        external_call_started = True
        response = submit_pdf(
            case_id,
            pdf_bytes,
            metadata={
                "resource_id": pdf["resource_id"],
                "preview_id": pdf["preview_id"],
                "document_id": pdf["document_id"],
                "rendered_content_sha256": pdf["content_sha256"],
                "pdf_sha256": actual_pdf_sha256,
                "generator_version": pdf["generator_version"],
                "automation_version": AUTOMATION_VERSION,
            },
        )
    except DGTNotConfigured as exc:
        _block_unavailable_submission(
            case_id,
            event_type="dgt_submission_unavailable",
            payload=_failure_payload(
                "dgt_not_configured",
                resource_id=pdf["resource_id"],
            ),
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "external_submission_unavailable"},
        ) from exc
    except NotImplementedError as exc:
        _block_unavailable_submission(
            case_id,
            event_type="dgt_submission_unavailable",
            payload=_failure_payload(
                "dgt_not_implemented",
                resource_id=pdf["resource_id"],
            ),
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "external_submission_unavailable"},
        ) from exc
    except Exception as exc:
        error_code = (
            "external_submission_outcome_unknown"
            if external_call_started
            else "document_preflight_failed"
        )
        payload = _failure_payload(error_code, resource_id=pdf["resource_id"])
        if external_call_started:
            _hold_claim_for_reconciliation(
                case_id,
                case_status="submission_outcome_unknown",
                submission_status="reconciliation_required",
                event_type="dgt_submission_outcome_unknown",
                payload=payload,
            )
        else:
            _reset_claim(case_id, "dgt_submit_failed_preflight", payload)
        raise HTTPException(
            status_code=502,
            detail={"code": "external_submission_failed"},
        ) from exc

    if not isinstance(response, dict):
        _hold_claim_for_reconciliation(
            case_id,
            case_status="submission_outcome_unknown",
            submission_status="reconciliation_required",
            event_type="dgt_submission_response_invalid",
            payload=_failure_payload(
                "invalid_provider_response",
                resource_id=pdf["resource_id"],
            ),
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_external_submission_response"},
        )

    try:
        registro = _provider_reference(response.get("registro"), field="registro")
        csv = _provider_reference(response.get("csv"), field="csv")
    except ValueError as exc:
        _hold_claim_for_reconciliation(
            case_id,
            case_status="submission_outcome_unknown",
            submission_status="reconciliation_required",
            event_type="dgt_submission_response_invalid",
            payload=_failure_payload(
                "invalid_provider_reference",
                resource_id=pdf["resource_id"],
            ),
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_external_submission_response"},
        ) from exc
    registro = registro or ""
    justificante_pdf = response.get("justificante_pdf") or b""
    if not justificante_pdf or not isinstance(justificante_pdf, (bytes, bytearray)):
        has_registration = bool(registro or csv)
        _hold_claim_for_reconciliation(
            case_id,
            case_status=(
                "submission_receipt_pending"
                if has_registration
                else "submission_outcome_unknown"
            ),
            submission_status=("receipt_pending" if has_registration else "reconciliation_required"),
            event_type=(
                "dgt_submission_accepted_receipt_pending"
                if has_registration
                else "dgt_submit_without_receipt"
            ),
            payload={
                "error_code": "provider_receipt_missing",
                "diagnostic": "dgt_submit_without_receipt",
                "resource_id": pdf["resource_id"],
                "registro": registro,
                "csv": csv,
                "authority_material_sha256": claim["authority_material_sha256"],
            },
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "external_submission_receipt_unavailable"},
        )
    justificante_pdf = bytes(justificante_pdf)
    if len(justificante_pdf) > MAX_AUTOMATION_PDF_BYTES:
        has_registration = bool(registro or csv)
        _hold_claim_for_reconciliation(
            case_id,
            case_status=("submission_receipt_pending" if has_registration else "submission_outcome_unknown"),
            submission_status=("receipt_pending" if has_registration else "reconciliation_required"),
            event_type=(
                "dgt_submission_accepted_receipt_pending"
                if has_registration
                else "dgt_submit_invalid_receipt"
            ),
            payload={
                "error_code": "provider_receipt_too_large",
                "diagnostic": "dgt_submit_invalid_receipt",
                "resource_id": pdf["resource_id"],
                "registro": registro,
                "csv": csv,
                "authority_material_sha256": claim["authority_material_sha256"],
            },
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_external_submission_receipt"},
        )
    if not justificante_pdf.startswith(b"%PDF-"):
        has_registration = bool(registro or csv)
        _hold_claim_for_reconciliation(
            case_id,
            case_status=("submission_receipt_pending" if has_registration else "submission_outcome_unknown"),
            submission_status=("receipt_pending" if has_registration else "reconciliation_required"),
            event_type=(
                "dgt_submission_accepted_receipt_pending"
                if has_registration
                else "dgt_submit_invalid_receipt"
            ),
            payload={
                "error_code": "provider_receipt_invalid",
                "diagnostic": "dgt_submit_invalid_receipt",
                "resource_id": pdf["resource_id"],
                "registro": registro,
                "csv": csv,
                "authority_material_sha256": claim["authority_material_sha256"],
            },
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_external_submission_receipt"},
        )
    receipt_sha256 = hashlib.sha256(justificante_pdf).hexdigest()
    if not registro and not csv:
        _hold_claim_for_reconciliation(
            case_id,
            case_status="submission_outcome_unknown",
            submission_status="reconciliation_required",
            event_type="dgt_submit_without_registration",
            payload={
                "error_code": "provider_reference_missing",
                "resource_id": pdf["resource_id"],
                "receipt_sha256": receipt_sha256,
            },
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_external_submission_response"},
        )

    engine = get_engine()
    with engine.begin() as conn:
        pending_transition = conn.execute(
            text(
                """
                UPDATE cases
                SET status='submission_receipt_pending', updated_at=NOW()
                WHERE id=:case_id AND status='submitting'
                RETURNING id
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        _event(
            conn,
            case_id,
            "dgt_submission_accepted_receipt_pending",
            {
                "resource_id": pdf["resource_id"],
                "registro": registro,
                "csv": csv,
                "receipt_sha256": receipt_sha256,
                "authority_material_sha256": claim["authority_material_sha256"],
                "state_transition_applied": bool(pending_transition),
            },
        )
        if not pending_transition:
            conn.execute(
                text(
                    "UPDATE submissions SET status='reconciliation_required', "
                    "last_error='accepted_response_state_conflict', updated_at=NOW() "
                    "WHERE case_id=:case_id AND channel='DGT_CORE'"
                ),
                {"case_id": case_id},
            )

    if not pending_transition:
        raise HTTPException(
            status_code=409,
            detail="DGT aceptó la presentación, pero el estado exige reconciliación",
        )

    try:
        bucket, key = upload_bytes(
            case_id,
            "justificantes",
            bytes(justificante_pdf),
            ".pdf",
            "application/pdf",
        )
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE submissions
                    SET status='receipt_pending', last_error=:error, updated_at=NOW()
                    WHERE case_id=:case_id AND channel='DGT_CORE'
                    """
                ),
                {"case_id": case_id, "error": "receipt_storage_failed"},
            )
            _event(
                conn,
                case_id,
                "dgt_receipt_storage_failed",
                {
                    "error_code": "receipt_storage_failed",
                    "resource_id": pdf["resource_id"],
                    "registro": registro,
                    "csv": csv,
                    "receipt_sha256": receipt_sha256,
                    "authority_material_sha256": claim["authority_material_sha256"],
                },
            )
        raise HTTPException(
            status_code=502,
            detail={"code": "external_submission_receipt_storage_failed"},
        ) from exc

    with _b2_backed_transaction(engine, (bucket, key)) as conn:
        submitted_transition = conn.execute(
            text(
                "UPDATE cases SET status='submitted', updated_at=NOW() "
                "WHERE id=:case_id AND status='submission_receipt_pending' "
                "RETURNING status"
            ),
            {"case_id": case_id},
        ).fetchone()
        if not submitted_transition:
            current_status_row = conn.execute(
                text("SELECT status FROM cases WHERE id=:case_id"),
                {"case_id": case_id},
            ).fetchone()
            current_status = (
                str(current_status_row[0] or "") if current_status_row else ""
            )
            if current_status == "submitted" and _verified_submission_evidence(
                conn, case_id
            ):
                # Este reintento no necesita el objeto recién creado: la
                # evidencia autoritativa ya estaba persistida.
                _cleanup_uploaded_receipt((bucket, key))
                return {
                    "ok": True,
                    "case_id": case_id,
                    "status": "submitted",
                    "registro": registro,
                    "csv": csv,
                    "resource_id": pdf["resource_id"],
                    "receipt_sha256": receipt_sha256,
                    "replayed": True,
                }
            raise HTTPException(
                status_code=409,
                detail="El justificante exige reconciliación con el estado actual",
            )

        if not _receipt_document_exists(conn, case_id, bucket, key):
            conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at
                    ) VALUES (
                        :case_id, 'justificante_presentacion', :bucket, :key,
                        'application/pdf', :size, NOW()
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "bucket": bucket,
                    "key": key,
                    "size": len(justificante_pdf),
                },
            )

        conn.execute(
            text(
                """
                UPDATE submissions
                SET status='submitted', notification_id=:registro,
                    last_error=NULL, updated_at=NOW()
                WHERE case_id=:case_id AND channel='DGT_CORE'
                """
            ),
            {"case_id": case_id, "registro": registro or csv},
        )
        _event(
            conn,
            case_id,
            "dgt_submitted",
            {
                "resource_id": pdf["resource_id"],
                "preview_id": pdf["preview_id"],
                "pdf_document_id": pdf["document_id"],
                "rendered_content_sha256": pdf["content_sha256"],
                "pdf_sha256": actual_pdf_sha256,
                "registro": registro,
                "csv": csv,
                "justificante": {"bucket": bucket, "key": key},
                "receipt_sha256": receipt_sha256,
                "authority_material_sha256": claim["authority_material_sha256"],
                "state_transition_applied": bool(submitted_transition),
                "automation_version": AUTOMATION_VERSION,
            },
        )
        current_status_row = conn.execute(
            text("SELECT status FROM cases WHERE id=:case_id"),
            {"case_id": case_id},
        ).fetchone()
        current_status = str(current_status_row[0] or "") if current_status_row else ""

    return {
        "ok": True,
        "case_id": case_id,
        "status": current_status,
        "registro": registro,
        "csv": csv,
        "resource_id": pdf["resource_id"],
        "receipt_sha256": receipt_sha256,
    }


def tick(limit: int = 25) -> Dict[str, Any]:
    """Procesa casos con recurso CORE aprobado; jamás genera documentos."""

    _require_external_submission_capability()
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_automation_limit"},
        )
    engine = get_engine()
    with engine.begin() as conn:
        if not _core_schema_ready(conn):
            raise HTTPException(
                status_code=503,
                detail={"code": "core_schema_unavailable"},
            )

        rows = conn.execute(
            text(
                """
                SELECT DISTINCT c.id
                FROM cases c
                JOIN rtm_generated_resources gr
                  ON gr.case_id = c.id
                 AND gr.status = 'final_ready'
                 AND gr.invalidated_at IS NULL
                 AND gr.approved_at IS NOT NULL
                JOIN rtm_legal_previews lp
                  ON lp.id = gr.legal_preview_id
                 AND lp.status = 'frozen'
                 AND lp.invalidated_at IS NULL
                 AND lp.payload_sha256 = gr.preview_payload_sha256
                WHERE c.status='ready_to_submit'
                  AND c.payment_status='paid'
                  AND c.authorized=TRUE
                  AND COALESCE(c.test_mode,FALSE)=FALSE
                ORDER BY c.id
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()
        picked: List[str] = [str(row[0]) for row in rows]

    processed = 0
    failed = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    for case_id in picked:
        try:
            result = submit_case_fully_automatic(case_id)
            if result.get("ok") is not True:
                raise RuntimeError("automation_result_not_successful")
            if result.get("skipped"):
                skipped += 1
            else:
                processed += 1
            results.append({"case_id": case_id, "ok": True, "result": result})
        except Exception:
            failed += 1
            results.append(
                {
                    "case_id": case_id,
                    "ok": False,
                    "error_code": "automation_case_failed",
                }
            )

    return {
        "ok": failed == 0,
        "picked": len(picked),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "automation_version": AUTOMATION_VERSION,
        "results": results,
    }
