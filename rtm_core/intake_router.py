"""Rutas de entrada comunes subordinadas a RTM CORE.

Sustituyen, por orden de registro, las rutas legacy que analizaban y
clasificaban documentos durante la subida o antes del pago. Aquí solo se
almacena, valida la preparación documental y se expone un estado público sin
PII.
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from b2_storage import delete_object, upload_bytes
from case_authority import (
    project_case_authorization_evidence,
)
from database import get_engine
from public_case_access import require_case_access_token
from rtm_core.authority_repository import (
    invalidate_validated_facts,
    latest_validated_facts,
)
from rtm_core.case_state_policy import lock_case_for_public_material_mutation
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot
from rtm_core.runtime_capabilities import require_http_capability
from rtm_core.upload_security import (
    UploadSecurityError,
    read_upload_limited,
    safe_filename,
    validate_document_bytes,
)


router = APIRouter(prefix="/cases", tags=["rtm-core-intake"])

MAX_APPEND_FILES = 5
_MAX_FILE_BYTES_ABSOLUTE = 8 * 1024 * 1024


def _configured_upload_limit() -> int:
    raw = (os.getenv("RTM_MAX_UPLOAD_BYTES") or str(2 * 1024 * 1024)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("RTM_MAX_UPLOAD_BYTES debe ser un entero") from exc
    if not 64 * 1024 <= value <= _MAX_FILE_BYTES_ABSOLUTE:
        raise RuntimeError("RTM_MAX_UPLOAD_BYTES está fuera del rango seguro")
    return value


MAX_FILE_BYTES = _configured_upload_limit()
_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".docx",
}
_ALLOWED_MIME_PREFIXES = ("image/",)
_ALLOWED_MIME_EXACT = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}


def _append_event(conn, case_id: str, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


def _core_authority_schema_ready(conn) -> bool:
    row = conn.execute(
        text("SELECT to_regclass('public.rtm_validated_facts')")
    ).fetchone()
    return bool(row and row[0])


def _safe_filename(value: str | None) -> str:
    return safe_filename(value)


def _extension(filename: str) -> str:
    if "." not in filename:
        return ".bin"
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    return extension if 2 <= len(extension) <= 10 else ".bin"


def _validate_upload(filename: str, mime: str, data: bytes):
    try:
        return validate_document_bytes(
            filename=filename,
            declared_mime=mime,
            data=data,
            max_bytes=MAX_FILE_BYTES,
        )
    except UploadSecurityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _case_exists(conn, case_id: str) -> None:
    row = conn.execute(
        text("SELECT 1 FROM cases WHERE id=:case_id"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")


def _cleanup_b2_objects(coordinates: list[tuple[str, str]]) -> None:
    """Compensa en orden inverso sin ocultar el fallo que inició la saga."""

    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass


def _existing_original_hashes(case_id: str, hashes: list[str]) -> set[str]:
    """Preflight síncrono para evitar subir duplicados ya registrados."""

    engine = get_engine()
    existing: set[str] = set()
    with engine.begin() as conn:
        # Rechazo temprano para no crear objetos B2 especulativos si el caso ya
        # está congelado. `_commit_appended_documents` repite el lock después
        # de las subidas para cerrar la carrera entre ambas fases.
        lock_case_for_public_material_mutation(conn, case_id)
        for digest in dict.fromkeys(hashes):
            row = conn.execute(
                text(
                    "SELECT 1 FROM documents WHERE case_id=:case_id "
                    "AND kind='original' AND sha256=:sha256 LIMIT 1"
                ),
                {"case_id": case_id, "sha256": digest},
            ).fetchone()
            if row:
                existing.add(digest)
    return existing


def _invalidate_authority_after_new_document(conn, case_id: str) -> list[str]:
    authority_row = conn.execute(
        text(
            """
            UPDATE cases
            SET authorized=FALSE, authorized_at=NULL, updated_at=NOW()
            WHERE id=:case_id AND COALESCE(authorized, FALSE)=TRUE
            RETURNING id
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    stale_row = conn.execute(
        text(
            """
            UPDATE documents
            SET kind = CASE kind
                WHEN 'authorization_signed_candidate'
                    THEN 'authorization_signed_candidate_stale'
                WHEN 'authorization_signed'
                    THEN 'authorization_signed_stale'
                WHEN 'authorization_signed_rejected'
                    THEN 'authorization_signed_rejected_stale'
                ELSE kind
            END
            WHERE case_id=:case_id
              AND kind IN (
                  'authorization_signed_candidate',
                  'authorization_signed',
                  'authorization_signed_rejected'
              )
            RETURNING id
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if authority_row or stale_row:
        _append_event(
            conn,
            case_id,
            "case_authority_invalidated_by_document_change",
            {"reason": "original_document_set_changed"},
        )

    if not _core_authority_schema_ready(conn):
        return []
    active = latest_validated_facts(
        conn,
        case_id,
        active_only=True,
        for_update=True,
    )
    if not active:
        return []
    invalidate_validated_facts(
        conn,
        case_id,
        active.id,
        "system:document-upload",
        "Se añadió nueva documentación al expediente",
    )
    return [active.id]


def _commit_appended_documents(
    case_id: str,
    prepared: list[dict[str, Any]],
    uploaded_coordinates: dict[str, tuple[str, str]],
) -> tuple[list[dict[str, Any]], str, list[str], list[tuple[str, str]]]:
    """Resuelve duplicados y publica todo el lote en una transacción."""

    engine = get_engine()
    uploaded: list[dict[str, Any]] = []
    resolved: dict[str, dict[str, Any]] = {}
    unused_coordinates: list[tuple[str, str]] = []
    with engine.begin() as conn:
        case_state = lock_case_for_public_material_mutation(conn, case_id)

        for item in prepared:
            digest = item["sha256"]
            prior = resolved.get(digest)
            if prior is not None:
                uploaded.append(
                    {
                        **prior,
                        "filename": item["filename"],
                        "reused": True,
                    }
                )
                continue

            duplicate = conn.execute(
                text(
                    """
                    SELECT id, mime, size_bytes
                    FROM documents
                    WHERE case_id=:case_id AND kind='original' AND sha256=:sha256
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"case_id": case_id, "sha256": digest},
            ).fetchone()
            if duplicate:
                coordinate = uploaded_coordinates.get(digest)
                if coordinate is not None:
                    unused_coordinates.append(coordinate)
                projection = {
                    "document_id": str(duplicate[0]),
                    "filename": item["filename"],
                    "mime": duplicate[1] or item["mime"],
                    "size_bytes": int(duplicate[2] or len(item["data"])),
                    "sha256": digest,
                    "reused": True,
                }
            else:
                coordinate = uploaded_coordinates.get(digest)
                if coordinate is None:
                    raise RuntimeError("Documento sin custodia preparada")
                bucket, key = coordinate
                row = conn.execute(
                    text(
                        """
                        INSERT INTO documents(
                            case_id, kind, b2_bucket, b2_key, sha256, mime,
                            size_bytes, created_at
                        ) VALUES (
                            :case_id, 'original', :bucket, :key, :sha256, :mime,
                            :size_bytes, NOW()
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "case_id": case_id,
                        "bucket": bucket,
                        "key": key,
                        "sha256": digest,
                        "mime": item["mime"],
                        "size_bytes": len(item["data"]),
                    },
                ).fetchone()
                if not row:
                    raise RuntimeError("No se registró el documento")
                projection = {
                    "document_id": str(row[0]),
                    "filename": item["filename"],
                    "mime": item["mime"],
                    "size_bytes": len(item["data"]),
                    "sha256": digest,
                    "reused": False,
                }
                _append_event(
                    conn,
                    case_id,
                    "rtm_original_document_stored",
                    {
                        **projection,
                        "analysis_deferred": True,
                    },
                )
            resolved[digest] = projection
            uploaded.append(projection)

        inserted_new = any(not item["reused"] for item in uploaded)
        invalidated_facts_ids = (
            _invalidate_authority_after_new_document(conn, case_id)
            if inserted_new
            else []
        )
        next_status = (
            "manual_review"
            if case_state.payment_status == "paid"
            else "documents_received"
        )
        conn.execute(
            text(
                "UPDATE cases SET status=:status, updated_at=NOW() "
                "WHERE id=:case_id"
            ),
            {"case_id": case_id, "status": next_status},
        )
        _append_event(
            conn,
            case_id,
            "rtm_documents_appended",
            {
                "documents": uploaded,
                "analysis_deferred": True,
                "classification_deferred": True,
                "invalidated_facts_ids": invalidated_facts_ids,
                "status": next_status,
            },
        )

    return uploaded, next_status, invalidated_facts_ids, unused_coordinates


@router.post("/{case_id}/append-documents")
async def append_documents_core(
    case_id: str,
    files: List[UploadFile] = File(...),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Almacena originales; nunca llama a Analyze, scoring o especialistas."""

    case_id = require_case_access_token(case_id, x_case_token)
    require_http_capability("b2")
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Máximo {MAX_APPEND_FILES} documentos por subida",
        )

    prepared: list[dict[str, Any]] = []
    for upload in files:
        filename = _safe_filename(upload.filename)
        mime = (upload.content_type or "application/octet-stream").lower().strip()
        try:
            data = await read_upload_limited(upload, max_bytes=MAX_FILE_BYTES)
        except UploadSecurityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        meta = await run_in_threadpool(_validate_upload, filename, mime, data)
        prepared.append(
            {
                "filename": meta.filename,
                "mime": meta.mime,
                "data": data,
                "extension": meta.extension,
                "sha256": meta.sha256,
            }
        )

    try:
        existing_hashes = await run_in_threadpool(
            _existing_original_hashes,
            case_id,
            [item["sha256"] for item in prepared],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo verificar el expediente de forma segura",
        ) from exc
    uploaded_coordinates: dict[str, tuple[str, str]] = {}
    stored_coordinates: list[tuple[str, str]] = []
    try:
        for item in prepared:
            digest = item["sha256"]
            if digest in existing_hashes or digest in uploaded_coordinates:
                continue
            bucket, key = await run_in_threadpool(
                upload_bytes,
                case_id,
                "original",
                item["data"],
                item["extension"],
                item["mime"],
            )
            uploaded_coordinates[digest] = (bucket, key)
            stored_coordinates.append((bucket, key))
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise HTTPException(
            status_code=502,
            detail="No se pudo custodiar el lote documental",
        ) from exc

    try:
        (
            uploaded,
            next_status,
            invalidated_facts_ids,
            unused_coordinates,
        ) = await run_in_threadpool(
            _commit_appended_documents,
            case_id,
            prepared,
            uploaded_coordinates,
        )
    except HTTPException:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise
    except Exception as exc:
        await run_in_threadpool(_cleanup_b2_objects, stored_coordinates)
        raise HTTPException(
            status_code=503,
            detail="No se pudo registrar el lote documental de forma segura",
        ) from exc

    # Una carrera idempotente puede descubrir durante el lock que otro request
    # ya registró el mismo hash; su PUT especulativo deja de ser necesario.
    if unused_coordinates:
        await run_in_threadpool(_cleanup_b2_objects, unused_coordinates)

    return {
        "ok": True,
        "case_id": case_id,
        "status": next_status,
        "documents": uploaded,
        "traffic_fine_analyzed": False,
        "analysis_deferred": True,
        "classification_deferred": True,
        "invalidated_facts_ids": invalidated_facts_ids,
    }


def _authorization_evidence_state(
    conn,
    case_id: str,
    *,
    authorized: bool,
    document_kinds: tuple[str, ...] | list[str] | set[str],
) -> tuple[bool, bool, bool]:
    """Return verified/pending/rejected only for the active authority chain."""

    evidence = project_case_authorization_evidence(
        conn,
        case_id,
        authorized=authorized,
        document_kinds=document_kinds,
    )
    status = evidence["authorization_evidence_status"]
    return status == "verified", status == "pending_review", status == "rejected"


@router.post("/{case_id}/review")
def review_case_core(
    case_id: str,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Comprueba preparación documental; no ejecuta inteligencia jurídica."""

    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        case_state = lock_case_for_public_material_mutation(conn, case_id)
        snapshot = load_case_review_snapshot(conn, case_id)
        signed_authority_verified, _, _ = _authorization_evidence_state(
            conn,
            case_id,
            authorized=snapshot.authorized,
            document_kinds=snapshot.document_kinds,
        )
        readiness = build_case_review_readiness(snapshot)
        readiness_payload = readiness.model_dump(mode="json")
        if readiness.ready and not signed_authority_verified:
            readiness_payload["ready"] = False
            readiness_payload["blocking_issues"].append(
                {
                    "code": "authorization_signature_review",
                    "message": "La firma requiere revisión humana verificable",
                    "area": "authorization",
                    "blocking": True,
                }
            )

        if snapshot.payment_status == "paid":
            next_status = "manual_review"
        elif readiness_payload["ready"]:
            next_status = "ready_for_review_payment"
        else:
            next_status = "documents_pending"

        updated = conn.execute(
            text(
                "UPDATE cases SET status=:status, updated_at=NOW() "
                "WHERE id=:case_id AND COALESCE(status,'')=:expected_status "
                "RETURNING id"
            ),
            {
                "case_id": case_id,
                "status": next_status,
                "expected_status": case_state.status,
            },
        ).fetchone()
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="El expediente cambió durante la revisión",
            )
        _append_event(
            conn,
            case_id,
            "rtm_review_readiness_evaluated",
            {
                "status": next_status,
                "readiness": readiness_payload,
                "legal_analysis_executed": False,
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": next_status,
        "readiness": readiness_payload,
        "legal_analysis_executed": False,
    }


@router.get("/{case_id}/public-status")
def public_status_core(
    case_id: str,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Proyección pública mínima: nunca devuelve PII, OCR ni extracción."""

    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        snapshot = load_case_review_snapshot(conn, case_id)
        (
            signed_authority_verified,
            pending_signature_candidate,
            authorization_rejected,
        ) = _authorization_evidence_state(
            conn,
            case_id,
            authorized=snapshot.authorized,
            document_kinds=snapshot.document_kinds,
        )
    readiness = build_case_review_readiness(snapshot)

    kinds = set(snapshot.document_kinds)
    progress = {
        "data_received": not any(
            issue.area == "data" and issue.blocking
            for issue in readiness.blocking_issues
        ),
        "identity_received": (
            "identity_front" in kinds and "identity_back" in kinds
        ),
        "authorization_received": (
            snapshot.authorized and signed_authority_verified
        ),
        "authorization_candidate_received": pending_signature_candidate,
        "signed_authority_verified": signed_authority_verified,
        "main_document_received": "original" in kinds,
        "ready_for_review_payment": readiness.ready and signed_authority_verified,
        "review_paid": snapshot.payment_status == "paid",
    }

    if snapshot.payment_status == "paid":
        message = "Pago confirmado. El expediente está en revisión RTM."
    elif readiness.ready and signed_authority_verified:
        message = "El expediente está completo para contratar el estudio inicial."
    elif not progress["main_document_received"]:
        message = "Falta aportar el documento principal del asunto."
    elif pending_signature_candidate:
        message = "La autorización firmada está pendiente de revisión humana."
    elif authorization_rejected:
        message = "La autorización firmada fue rechazada y debe volver a aportarse."
    elif not progress["authorization_received"]:
        message = "Falta completar y verificar la autorización firmada."
    else:
        message = "El expediente está pendiente de completar documentación."

    return {
        "ok": True,
        "case_id": case_id,
        "status": snapshot.status,
        "payment_status": snapshot.payment_status,
        "authorized": snapshot.authorized,
        "signed_authority_verified": signed_authority_verified,
        "authorization_evidence_status": (
            "verified"
            if signed_authority_verified
            else "pending_review"
            if pending_signature_candidate
            else "rejected"
            if authorization_rejected
            else "not_submitted"
        ),
        "department": readiness.quote.department,
        "case_type": snapshot.case_type,
        "message": message,
        "progress": progress,
        "review_quote": {
            "billing_code": readiness.quote.billing_code,
            "amount_cents": readiness.quote.amount_cents,
            "currency": readiness.quote.currency,
        },
        "privacy_projection": "rtm_public_status_v1_0",
    }
