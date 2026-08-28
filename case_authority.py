"""Cadena criptográfica activa para la representación de un expediente RTM."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text


AUTHORITY_VERSION = "v1_dgt_homologado"
AUTHORITY_ATTESTATION_VERSION = "rtm_case_authority_attestation_v1"
SIGNED_AUTHORITY_DOCUMENT_VERSION = "rtm_signed_authority_document_attestation_v1"
_SECRET_ENV = "RTM_AUTHORITY_SIGNING_SECRET"


def _secret() -> bytes:
    value = (os.getenv(_SECRET_ENV) or "").strip().encode("utf-8")
    if len(value) < 32:
        raise HTTPException(
            status_code=503,
            detail=f"{_SECRET_ENV} debe tener al menos 32 bytes",
        )
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_case_id(case_id: str) -> str:
    try:
        return str(uuid.UUID(str(case_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail="Identificador de autoridad inválido") from exc


def _identity(interested: dict[str, Any]) -> dict[str, str]:
    return {
        "full_name": str(interested.get("full_name") or "").strip(),
        "dni_nie": str(interested.get("dni_nie") or "").strip().upper(),
        "domicilio_notif": str(interested.get("domicilio_notif") or "").strip(),
        "email": str(interested.get("email") or "").strip().lower(),
    }


def _identity_hmac(interested: dict[str, Any]) -> str:
    return hmac.new(_secret(), _canonical_json(_identity(interested)), hashlib.sha256).hexdigest()


def build_case_authority_payload(
    *,
    case_id: str,
    interested: dict[str, Any],
    accepted_at: str,
    request_ip: str,
) -> dict[str, Any]:
    canonical_case_id = _canonical_case_id(case_id)
    material = {
        "format": AUTHORITY_ATTESTATION_VERSION,
        "authority_id": str(uuid.uuid4()),
        "case_id": canonical_case_id,
        "authority_version": AUTHORITY_VERSION,
        "accepted_at": accepted_at,
        "identity_hmac_sha256": _identity_hmac(interested),
        "request_ip_hmac_sha256": hmac.new(
            _secret(), str(request_ip or "").encode("utf-8"), hashlib.sha256
        ).hexdigest(),
        "consent": True,
        "representation_confirmed": True,
    }
    canonical = _canonical_json(material)
    return {
        "material": material,
        "material_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_sha256": hmac.new(_secret(), canonical, hashlib.sha256).hexdigest(),
    }


def build_signed_authority_document_attestation(
    *,
    case_id: str,
    authority_payload: dict[str, Any],
    document_id: str,
    document_sha256: str,
    size_bytes: int,
    storage_bucket: str,
    storage_key: str,
    uploaded_at: str,
) -> dict[str, Any]:
    authority_material = authority_payload.get("material")
    if not isinstance(authority_material, dict):
        raise HTTPException(status_code=409, detail="Autoridad activa no verificable")
    try:
        canonical_document_id = str(uuid.UUID(str(document_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail="Documento firmado inválido") from exc
    digest = str(document_sha256 or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise HTTPException(status_code=409, detail="Huella del documento firmado inválida")
    if int(size_bytes) <= 0:
        raise HTTPException(status_code=409, detail="Tamaño del documento firmado inválido")

    material = {
        "format": SIGNED_AUTHORITY_DOCUMENT_VERSION,
        "case_id": _canonical_case_id(case_id),
        "authority_id": str(authority_material.get("authority_id") or ""),
        "authority_version": str(authority_material.get("authority_version") or ""),
        "authority_material_sha256": str(
            authority_payload.get("material_sha256") or ""
        ),
        "document_id": canonical_document_id,
        "document_sha256": digest,
        "mime": "application/pdf",
        "size_bytes": int(size_bytes),
        "storage_bucket": str(storage_bucket or ""),
        "storage_key": str(storage_key or ""),
        "uploaded_at": str(uploaded_at or ""),
    }
    if not material["storage_bucket"] or not material["storage_key"]:
        raise HTTPException(status_code=409, detail="Custodia del documento firmado inválida")
    canonical = _canonical_json(material)
    return {
        "material": material,
        "material_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_sha256": hmac.new(_secret(), canonical, hashlib.sha256).hexdigest(),
    }


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="Fecha de autoridad inválida") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def verify_active_case_authority(conn, case_id: str) -> dict[str, Any]:
    canonical_case_id = _canonical_case_id(case_id)
    case_row = conn.execute(
        text(
            "SELECT authorized, COALESCE(interested_data, '{}'::jsonb), authorized_at "
            "FROM cases WHERE id=:id"
        ),
        {"id": canonical_case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    if not bool(case_row[0]):
        raise HTTPException(status_code=409, detail="El expediente no está autorizado")

    event_row = conn.execute(
        text(
            "SELECT payload, created_at FROM events "
            "WHERE case_id=:id AND type='case_authorized' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"id": canonical_case_id},
    ).fetchone()
    raw_payload = event_row[0] if event_row else None
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            raw_payload = None
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    material = payload.get("material") if isinstance(payload.get("material"), dict) else {}
    canonical = _canonical_json(material)
    expected_digest = hashlib.sha256(canonical).hexdigest()
    expected_signature = hmac.new(_secret(), canonical, hashlib.sha256).hexdigest()
    interested = case_row[1] if isinstance(case_row[1], dict) else {}

    try:
        authority_id = str(uuid.UUID(str(material.get("authority_id"))))
    except (TypeError, ValueError, AttributeError):
        authority_id = ""
    accepted_at = _as_utc(material.get("accepted_at"))
    authorized_at = _as_utc(case_row[2])
    now = datetime.now(timezone.utc)

    invalidated = conn.execute(
        text(
            """
            SELECT 1 FROM events
            WHERE case_id=:id
              AND type IN (
                'case_authority_revoked',
                'case_authority_invalidated_by_identity_change'
              )
              AND created_at >= :authorized_event_at
            LIMIT 1
            """
        ),
        {
            "id": canonical_case_id,
            "authorized_event_at": event_row[1] if event_row else now,
        },
    ).fetchone()

    if (
        not event_row
        or invalidated
        or not authority_id
        or material.get("format") != AUTHORITY_ATTESTATION_VERSION
        or str(material.get("case_id")) != canonical_case_id
        or material.get("authority_version") != AUTHORITY_VERSION
        or material.get("consent") is not True
        or material.get("representation_confirmed") is not True
        or accepted_at > now + timedelta(minutes=5)
        or abs((authorized_at - accepted_at).total_seconds()) > 5
        or not hmac.compare_digest(
            str(material.get("identity_hmac_sha256") or ""),
            _identity_hmac(interested),
        )
        or not hmac.compare_digest(str(payload.get("material_sha256") or ""), expected_digest)
        or not hmac.compare_digest(str(payload.get("signature_sha256") or ""), expected_signature)
    ):
        raise HTTPException(status_code=409, detail="Cadena de autorización no verificable")
    return payload


def verify_signed_case_authority(conn, case_id: str) -> dict[str, Any]:
    """Verify the active authority and its exact signed PDF evidence."""

    canonical_case_id = _canonical_case_id(case_id)
    authority = verify_active_case_authority(conn, canonical_case_id)
    row = conn.execute(
        text(
            """
            SELECT e.payload, e.created_at, d.id, d.mime, d.size_bytes,
                   d.b2_bucket, d.b2_key
            FROM events e
            JOIN documents d
              ON d.case_id=e.case_id
             AND d.id::text=e.payload->'material'->>'document_id'
            WHERE e.case_id=:id
              AND e.type='authorization_signed_uploaded'
            ORDER BY e.created_at DESC LIMIT 1
            """
        ),
        {"id": canonical_case_id},
    ).fetchone()
    raw_payload = row[0] if row else None
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            raw_payload = None
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    material = payload.get("material") if isinstance(payload.get("material"), dict) else {}
    canonical = _canonical_json(material)
    expected_digest = hashlib.sha256(canonical).hexdigest()
    expected_signature = hmac.new(_secret(), canonical, hashlib.sha256).hexdigest()
    authority_material = authority["material"]
    now = datetime.now(timezone.utc)

    try:
        uploaded_at = _as_utc(material.get("uploaded_at"))
        event_created_at = _as_utc(row[1] if row else None)
        document_id = str(uuid.UUID(str(material.get("document_id"))))
        stored_document_id = str(uuid.UUID(str(row[2] if row else "")))
        size_bytes = int(material.get("size_bytes"))
    except (HTTPException, TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=409, detail="Documento de autoridad no verificable")

    document_sha256 = str(material.get("document_sha256") or "").lower()
    if (
        not row
        or material.get("format") != SIGNED_AUTHORITY_DOCUMENT_VERSION
        or str(material.get("case_id")) != canonical_case_id
        or str(material.get("authority_id"))
        != str(authority_material.get("authority_id"))
        or str(material.get("authority_version"))
        != str(authority_material.get("authority_version"))
        or not hmac.compare_digest(
            str(material.get("authority_material_sha256") or ""),
            str(authority.get("material_sha256") or ""),
        )
        or document_id != stored_document_id
        or len(document_sha256) != 64
        or any(char not in "0123456789abcdef" for char in document_sha256)
        or str(material.get("mime")) != "application/pdf"
        or str(row[3] or "") != "application/pdf"
        or size_bytes <= 0
        or size_bytes != int(row[4] or 0)
        or str(material.get("storage_bucket") or "") != str(row[5] or "")
        or str(material.get("storage_key") or "") != str(row[6] or "")
        or uploaded_at > now + timedelta(minutes=5)
        or abs((event_created_at - uploaded_at).total_seconds()) > 300
        or uploaded_at < _as_utc(authority_material.get("accepted_at"))
        or not hmac.compare_digest(
            str(payload.get("material_sha256") or ""), expected_digest
        )
        or not hmac.compare_digest(
            str(payload.get("signature_sha256") or ""), expected_signature
        )
    ):
        raise HTTPException(status_code=409, detail="Documento de autoridad no verificable")
    return {**authority, "signed_document_attestation": payload}


__all__ = [
    "AUTHORITY_ATTESTATION_VERSION",
    "AUTHORITY_VERSION",
    "SIGNED_AUTHORITY_DOCUMENT_VERSION",
    "build_case_authority_payload",
    "build_signed_authority_document_attestation",
    "verify_active_case_authority",
    "verify_signed_case_authority",
]
