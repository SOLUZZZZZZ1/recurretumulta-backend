"""Rutas de entrada comunes subordinadas a RTM CORE.

Sustituyen, por orden de registro, las rutas legacy que analizaban y
clasificaban documentos durante la subida o antes del pago. Aquí solo se
almacena, valida la preparación documental y se expone un estado público sin
PII.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, List, Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from sqlalchemy import text

from b2_storage import upload_bytes
from database import get_engine
from public_case_access import require_case_access_token
from rtm_core.authority_repository import (
    invalidate_validated_facts,
    latest_validated_facts,
)
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot


router = APIRouter(prefix="/cases", tags=["rtm-core-intake"])

MAX_APPEND_FILES = 5
MAX_FILE_BYTES = int(os.getenv("RTM_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))
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
    filename = (value or "documento").replace("/", "_").replace("\\", "_")
    filename = "".join(ch for ch in filename if ch.isprintable()).strip()
    return (filename or "documento")[:140]


def _extension(filename: str) -> str:
    if "." not in filename:
        return ".bin"
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    return extension if 2 <= len(extension) <= 10 else ".bin"


def _validate_upload(filename: str, mime: str, data: bytes) -> str:
    if not data:
        raise HTTPException(status_code=400, detail=f"El archivo {filename} está vacío")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "message": f"El archivo {filename} supera el límite permitido",
                "max_bytes": MAX_FILE_BYTES,
                "size_bytes": len(data),
            },
        )
    extension = _extension(filename)
    normalized_mime = (mime or "application/octet-stream").lower().strip()
    if extension not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Formato no permitido para {filename}: {extension}",
        )
    if not (
        normalized_mime in _ALLOWED_MIME_EXACT
        or normalized_mime.startswith(_ALLOWED_MIME_PREFIXES)
    ):
        raise HTTPException(
            status_code=415,
            detail=f"Tipo MIME no permitido para {filename}: {normalized_mime}",
        )
    return extension


def _case_exists(conn, case_id: str) -> None:
    row = conn.execute(
        text("SELECT 1 FROM cases WHERE id=:case_id"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")


def _invalidate_authority_after_new_document(conn, case_id: str) -> list[str]:
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


@router.post("/{case_id}/append-documents")
async def append_documents_core(
    case_id: str,
    files: List[UploadFile] = File(...),
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Almacena originales; nunca llama a Analyze, scoring o especialistas."""

    case_id = require_case_access_token(case_id, x_case_token)
    if not files:
        raise HTTPException(status_code=400, detail="No se han recibido archivos")
    if len(files) > MAX_APPEND_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Máximo {MAX_APPEND_FILES} documentos por subida",
        )

    engine = get_engine()
    prepared: list[dict[str, Any]] = []
    for upload in files:
        filename = _safe_filename(upload.filename)
        mime = (upload.content_type or "application/octet-stream").lower().strip()
        data = await upload.read()
        extension = _validate_upload(filename, mime, data)
        prepared.append(
            {
                "filename": filename,
                "mime": mime,
                "data": data,
                "extension": extension,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    uploaded: list[dict[str, Any]] = []
    with engine.begin() as conn:
        _case_exists(conn, case_id)

    # B2 es externo a la transacción SQL; cada resultado queda registrado de
    # inmediato y con huella para que una repetición sea idempotente.
    for item in prepared:
        with engine.begin() as conn:
            duplicate = conn.execute(
                text(
                    """
                    SELECT id, b2_bucket, b2_key, mime, size_bytes
                    FROM documents
                    WHERE case_id=:case_id AND kind='original' AND sha256=:sha256
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"case_id": case_id, "sha256": item["sha256"]},
            ).fetchone()
        if duplicate:
            uploaded.append(
                {
                    "document_id": str(duplicate[0]),
                    "filename": item["filename"],
                    "mime": duplicate[3] or item["mime"],
                    "size_bytes": int(duplicate[4] or len(item["data"])),
                    "sha256": item["sha256"],
                    "reused": True,
                }
            )
            continue

        bucket, key = upload_bytes(
            case_id,
            "original",
            item["data"],
            item["extension"],
            item["mime"],
        )
        with engine.begin() as conn:
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
                    "sha256": item["sha256"],
                    "mime": item["mime"],
                    "size_bytes": len(item["data"]),
                },
            ).fetchone()
            document_id = str(row[0])
            _append_event(
                conn,
                case_id,
                "rtm_original_document_stored",
                {
                    "document_id": document_id,
                    "filename": item["filename"],
                    "mime": item["mime"],
                    "size_bytes": len(item["data"]),
                    "sha256": item["sha256"],
                    "analysis_deferred": True,
                },
            )
        uploaded.append(
            {
                "document_id": document_id,
                "filename": item["filename"],
                "mime": item["mime"],
                "size_bytes": len(item["data"]),
                "sha256": item["sha256"],
                "reused": False,
            }
        )

    with engine.begin() as conn:
        invalidated_facts_ids = (
            _invalidate_authority_after_new_document(conn, case_id)
            if any(not item["reused"] for item in uploaded)
            else []
        )
        payment_row = conn.execute(
            text("SELECT COALESCE(payment_status,'') FROM cases WHERE id=:case_id"),
            {"case_id": case_id},
        ).fetchone()
        next_status = (
            "manual_review"
            if payment_row and str(payment_row[0]) == "paid"
            else "documents_received"
        )
        conn.execute(
            text("UPDATE cases SET status=:status, updated_at=NOW() WHERE id=:case_id"),
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


@router.post("/{case_id}/review")
def review_case_core(
    case_id: str,
    x_case_token: Optional[str] = Header(default=None, alias="X-RTM-Case-Token"),
):
    """Comprueba preparación documental; no ejecuta inteligencia jurídica."""

    case_id = require_case_access_token(case_id, x_case_token)
    engine = get_engine()
    with engine.begin() as conn:
        snapshot = load_case_review_snapshot(conn, case_id)
    readiness = build_case_review_readiness(snapshot)

    if snapshot.payment_status == "paid":
        next_status = "manual_review"
    elif readiness.ready:
        next_status = "ready_for_review_payment"
    else:
        next_status = "documents_pending"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cases SET status=:status, updated_at=NOW() WHERE id=:case_id"),
            {"case_id": case_id, "status": next_status},
        )
        _append_event(
            conn,
            case_id,
            "rtm_review_readiness_evaluated",
            {
                "status": next_status,
                "readiness": readiness.model_dump(mode="json"),
                "legal_analysis_executed": False,
            },
        )

    return {
        "ok": True,
        "case_id": case_id,
        "status": next_status,
        "readiness": readiness.model_dump(mode="json"),
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
            snapshot.authorized and "authorization_signed" in kinds
        ),
        "main_document_received": "original" in kinds,
        "ready_for_review_payment": readiness.ready,
        "review_paid": snapshot.payment_status == "paid",
    }

    if snapshot.payment_status == "paid":
        message = "Pago confirmado. El expediente está en revisión RTM."
    elif readiness.ready:
        message = "El expediente está completo para contratar el estudio inicial."
    elif not progress["main_document_received"]:
        message = "Falta aportar el documento principal del asunto."
    elif not progress["authorization_received"]:
        message = "Falta completar la autorización firmada."
    else:
        message = "El expediente está pendiente de completar documentación."

    return {
        "ok": True,
        "case_id": case_id,
        "status": snapshot.status,
        "payment_status": snapshot.payment_status,
        "authorized": snapshot.authorized,
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
