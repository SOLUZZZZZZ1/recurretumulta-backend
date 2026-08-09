"""Automatización OPS subordinada a RTM CORE.

El worker nunca extrae, clasifica ni genera. Solo puede presentar un PDF que:
- procede de una Previa Jurídica congelada;
- está registrado en rtm_generated_resources;
- ha sido aprobado expresamente para presentación por OPS.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text

from b2_storage import download_bytes, upload_bytes
from database import get_engine
from dgt_client import DGTNotConfigured, submit_pdf


AUTOMATION_VERSION = "rtm_submission_automation_v1_0"


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


def _has_justificante(conn, case_id: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM documents "
            "WHERE case_id=:id AND kind='justificante_presentacion' "
            "LIMIT 1"
        ),
        {"id": case_id},
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
                "error": str(payload.get("error") or payload.get("reason") or ""),
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

        if _has_justificante(conn, case_id):
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
                detail=f"El expediente no está listo para presentar: {status}",
            )

        pdf = _approved_core_pdf(conn, case_id)
        if not pdf:
            raise HTTPException(
                status_code=409,
                detail="No existe un PDF CORE aprobado para presentación",
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
                "automation_version": AUTOMATION_VERSION,
            },
        )
        return {"case_id": case_id, "already_submitted": False, "pdf": pdf}


def submit_case_fully_automatic(case_id: str) -> Dict[str, Any]:
    """Presenta exclusivamente un recurso CORE previamente aprobado por OPS."""

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
    try:
        pdf_bytes = download_bytes(pdf["bucket"], pdf["key"])
        if not pdf_bytes:
            raise RuntimeError("El PDF aprobado está vacío")
        if pdf["size_bytes"] and len(pdf_bytes) != pdf["size_bytes"]:
            raise RuntimeError("El tamaño del PDF no coincide con el documento registrado")

        response = submit_pdf(
            case_id,
            pdf_bytes,
            metadata={
                "resource_id": pdf["resource_id"],
                "preview_id": pdf["preview_id"],
                "document_id": pdf["document_id"],
                "content_sha256": pdf["content_sha256"],
                "generator_version": pdf["generator_version"],
                "automation_version": AUTOMATION_VERSION,
            },
        )
    except DGTNotConfigured as exc:
        _reset_claim(
            case_id,
            "dgt_not_configured_skip",
            {"error": str(exc), "resource_id": pdf["resource_id"]},
        )
        return {
            "ok": True,
            "case_id": case_id,
            "status": "ready_to_submit",
            "skipped": True,
            "reason": "dgt_not_configured",
        }
    except NotImplementedError as exc:
        _reset_claim(
            case_id,
            "dgt_not_implemented_skip",
            {"error": str(exc), "resource_id": pdf["resource_id"]},
        )
        return {
            "ok": True,
            "case_id": case_id,
            "status": "ready_to_submit",
            "skipped": True,
            "reason": "dgt_not_implemented",
        }
    except Exception as exc:
        _reset_claim(
            case_id,
            "dgt_submit_failed",
            {"error": str(exc), "resource_id": pdf["resource_id"]},
        )
        raise HTTPException(status_code=502, detail=f"Fallo al presentar en DGT: {exc}")

    registro = str(response.get("registro") or "").strip()
    csv = str(response.get("csv") or "").strip() or None
    justificante_pdf = response.get("justificante_pdf") or b""
    if not justificante_pdf or not isinstance(justificante_pdf, (bytes, bytearray)):
        _reset_claim(
            case_id,
            "dgt_submit_without_receipt",
            {"error": "DGT no devolvió justificante PDF", "resource_id": pdf["resource_id"]},
        )
        raise HTTPException(status_code=502, detail="DGT no devolvió justificante_pdf")
    if not registro and not csv:
        _reset_claim(
            case_id,
            "dgt_submit_without_registration",
            {"error": "DGT no devolvió registro ni CSV", "resource_id": pdf["resource_id"]},
        )
        raise HTTPException(status_code=502, detail="DGT no devolvió registro ni CSV")

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE cases
                SET status='submission_receipt_pending', updated_at=NOW()
                WHERE id=:case_id AND status='submitting'
                """
            ),
            {"case_id": case_id},
        )
        _event(
            conn,
            case_id,
            "dgt_submission_accepted_receipt_pending",
            {
                "resource_id": pdf["resource_id"],
                "registro": registro,
                "csv": csv,
            },
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
                {"case_id": case_id, "error": str(exc)},
            )
            _event(
                conn,
                case_id,
                "dgt_receipt_storage_failed",
                {
                    "error": str(exc),
                    "resource_id": pdf["resource_id"],
                    "registro": registro,
                    "csv": csv,
                },
            )
        raise HTTPException(
            status_code=502,
            detail="La presentación fue aceptada, pero no pudo guardarse el justificante",
        )

    with engine.begin() as conn:
        if not _has_justificante(conn, case_id):
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
            text("UPDATE cases SET status='submitted', updated_at=NOW() WHERE id=:case_id"),
            {"case_id": case_id},
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
                "registro": registro,
                "csv": csv,
                "justificante": {"bucket": bucket, "key": key},
                "automation_version": AUTOMATION_VERSION,
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": "submitted",
        "registro": registro,
        "csv": csv,
        "resource_id": pdf["resource_id"],
    }


def tick(limit: int = 25) -> Dict[str, Any]:
    """Procesa casos con recurso CORE aprobado; jamás genera documentos."""

    engine = get_engine()
    with engine.begin() as conn:
        if not _core_schema_ready(conn):
            return {
                "ok": True,
                "picked": 0,
                "processed": 0,
                "failed": 0,
                "skipped": True,
                "reason": "core_schema_not_migrated",
                "automation_version": AUTOMATION_VERSION,
                "results": [],
            }

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
    results: list[dict[str, Any]] = []
    for case_id in picked:
        try:
            result = submit_case_fully_automatic(case_id)
            processed += 1
            results.append({"case_id": case_id, "ok": True, "result": result})
        except Exception as exc:
            failed += 1
            results.append({"case_id": case_id, "ok": False, "error": str(exc)})

    return {
        "ok": True,
        "picked": len(picked),
        "processed": processed,
        "failed": failed,
        "automation_version": AUTOMATION_VERSION,
        "results": results,
    }
