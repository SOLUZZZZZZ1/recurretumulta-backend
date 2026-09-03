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
AUTHORITY_DOCUMENT_ISSUE_VERSION = "rtm_authority_document_issue_v1"
AUTHORIZATION_SIGNATURE_CANDIDATE_VERSION = (
    "rtm_authorization_signature_candidate_v1"
)
AUTHORIZATION_SIGNATURE_VIEW_VERSION = "rtm_authorization_signature_view_v1"
SIGNED_AUTHORITY_DOCUMENT_VERSION = "rtm_signed_authority_human_review_v3"
AUTHORIZATION_SIGNATURE_REJECTION_VERSION = (
    "rtm_authorization_signature_rejection_v1"
)
_SECRET_ENV = "RTM_AUTHORITY_SIGNING_SECRET"
_DGT_FINE_DEPARTMENT = "traffic"
_DGT_FINE_CASE_TYPE = "fine"


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


def _canonical_uuid(value: Any, *, detail: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc


def _sha256(value: Any, *, detail: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise HTTPException(status_code=409, detail=detail)
    return digest


def require_dgt_fine_authority_scope(department: Any, case_type: Any) -> None:
    """Keep the DGT-specific legal text out of every other service family."""

    if (
        str(department or "").strip().lower() != _DGT_FINE_DEPARTMENT
        or str(case_type or "").strip().lower() != _DGT_FINE_CASE_TYPE
    ):
        raise HTTPException(
            status_code=409,
            detail="La autorización DGT no corresponde a este tipo de expediente",
        )


def _signed_envelope(material: dict[str, Any]) -> dict[str, Any]:
    canonical = _canonical_json(material)
    return {
        "material": material,
        "material_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_sha256": hmac.new(_secret(), canonical, hashlib.sha256).hexdigest(),
    }


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _verify_envelope(payload: dict[str, Any], *, detail: str) -> dict[str, Any]:
    if set(payload) != {"material", "material_sha256", "signature_sha256"}:
        raise HTTPException(status_code=409, detail=detail)
    material = payload.get("material")
    if not isinstance(material, dict):
        raise HTTPException(status_code=409, detail=detail)
    canonical = _canonical_json(material)
    expected_digest = hashlib.sha256(canonical).hexdigest()
    expected_signature = hmac.new(_secret(), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(
        str(payload.get("material_sha256") or ""), expected_digest
    ) or not hmac.compare_digest(
        str(payload.get("signature_sha256") or ""), expected_signature
    ):
        raise HTTPException(status_code=409, detail=detail)
    return material


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


def build_authority_document_issue_attestation(
    *,
    case_id: str,
    authority_payload: dict[str, Any],
    document_id: str,
    document_sha256: str,
    size_bytes: int,
    document_version: str,
    document_nonce: str,
    issued_at: str,
) -> dict[str, Any]:
    authority_material = authority_payload.get("material")
    if not isinstance(authority_material, dict):
        raise HTTPException(status_code=409, detail="Autoridad activa no verificable")
    canonical_document_id = _canonical_uuid(
        document_id, detail="Documento de autorización inválido"
    )
    canonical_nonce = _canonical_uuid(
        document_nonce, detail="Nonce de autorización inválido"
    )
    digest = _sha256(
        document_sha256, detail="Huella del documento de autorización inválida"
    )
    if int(size_bytes) <= 0:
        raise HTTPException(
            status_code=409, detail="Tamaño del documento de autorización inválido"
        )
    version = str(document_version or "").strip()
    if version != str(authority_material.get("authority_version") or ""):
        raise HTTPException(status_code=409, detail="Versión de autorización inválida")

    material = {
        "format": AUTHORITY_DOCUMENT_ISSUE_VERSION,
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
        "document_version": version,
        "document_nonce": canonical_nonce,
        "issued_at": str(issued_at or ""),
    }
    _as_utc(material["issued_at"])
    return _signed_envelope(material)


def build_authorization_signature_candidate_attestation(
    *,
    case_id: str,
    authority_payload: dict[str, Any],
    issuance_payload: dict[str, Any],
    document_id: str,
    document_sha256: str,
    size_bytes: int,
    uploaded_at: str,
) -> dict[str, Any]:
    authority_material = authority_payload.get("material")
    issuance_material = issuance_payload.get("material")
    if not isinstance(authority_material, dict) or not isinstance(
        issuance_material, dict
    ):
        raise HTTPException(status_code=409, detail="Cadena de autorización incompleta")
    material = {
        "format": AUTHORIZATION_SIGNATURE_CANDIDATE_VERSION,
        "case_id": _canonical_case_id(case_id),
        "authority_id": str(authority_material.get("authority_id") or ""),
        "authority_version": str(authority_material.get("authority_version") or ""),
        "authority_material_sha256": _sha256(
            authority_payload.get("material_sha256"),
            detail="Huella de autoridad inválida",
        ),
        "issued_document_id": _canonical_uuid(
            issuance_material.get("document_id"),
            detail="Documento emitido inválido",
        ),
        "issued_document_sha256": _sha256(
            issuance_material.get("document_sha256"),
            detail="Huella del documento emitido inválida",
        ),
        "issued_document_version": str(
            issuance_material.get("document_version") or ""
        ),
        "document_nonce": _canonical_uuid(
            issuance_material.get("document_nonce"),
            detail="Nonce de autorización inválido",
        ),
        "issuance_attestation_sha256": _sha256(
            issuance_payload.get("material_sha256"),
            detail="Huella de emisión inválida",
        ),
        "candidate_document_id": _canonical_uuid(
            document_id, detail="Documento candidato inválido"
        ),
        "candidate_document_sha256": _sha256(
            document_sha256, detail="Huella del documento candidato inválida"
        ),
        "mime": "application/pdf",
        "size_bytes": int(size_bytes),
        "uploaded_at": str(uploaded_at or ""),
        "review_status": "pending_review",
    }
    if material["size_bytes"] <= 0:
        raise HTTPException(status_code=409, detail="Tamaño del candidato inválido")
    _as_utc(material["uploaded_at"])
    return _signed_envelope(material)


def build_authorization_signature_view_attestation(
    *,
    case_id: str,
    candidate_payload: dict[str, Any],
    reviewer_actor: str,
    operator_session_id: str,
    viewed_at: str,
) -> dict[str, Any]:
    """Sign the fact that an individual reviewer fetched the exact candidate."""

    candidate_material = candidate_payload.get("material")
    actor = str(reviewer_actor or "").strip()
    if not isinstance(candidate_material, dict):
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable")
    if not actor.startswith("operator:") or len(actor) > 80:
        raise HTTPException(status_code=403, detail="Identidad de revisor no válida")
    session_id = _canonical_uuid(
        operator_session_id,
        detail="Sesión individual de revisión no válida",
    )
    material = {
        "format": AUTHORIZATION_SIGNATURE_VIEW_VERSION,
        "case_id": _canonical_case_id(case_id),
        "candidate_document_id": _canonical_uuid(
            candidate_material.get("candidate_document_id"),
            detail="Documento candidato inválido",
        ),
        "candidate_document_sha256": _sha256(
            candidate_material.get("candidate_document_sha256"),
            detail="Huella del candidato inválida",
        ),
        "candidate_attestation_sha256": _sha256(
            candidate_payload.get("material_sha256"),
            detail="Huella de candidato inválida",
        ),
        "reviewer_actor_hmac_sha256": hmac.new(
            _secret(), actor.encode("utf-8"), hashlib.sha256
        ).hexdigest(),
        "operator_session_hmac_sha256": hmac.new(
            _secret(), session_id.encode("ascii"), hashlib.sha256
        ).hexdigest(),
        "viewed_at": str(viewed_at or ""),
    }
    _as_utc(material["viewed_at"])
    return _signed_envelope(material)


def build_reviewed_signed_authority_attestation(
    *,
    case_id: str,
    authority_payload: dict[str, Any],
    issuance_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    reviewer_actor: str,
    operator_session_id: str,
    view_event_id: str,
    view_payload: dict[str, Any],
    reauthentication_event_id: str,
    review_checklist: dict[str, bool],
    reviewed_at: str,
) -> dict[str, Any]:
    authority_material = authority_payload.get("material")
    issuance_material = issuance_payload.get("material")
    candidate_material = candidate_payload.get("material")
    if not all(
        isinstance(value, dict)
        for value in (authority_material, issuance_material, candidate_material)
    ):
        raise HTTPException(status_code=409, detail="Cadena de revisión incompleta")
    actor = str(reviewer_actor or "").strip()
    if not actor.startswith("operator:") or len(actor) > 80:
        raise HTTPException(status_code=403, detail="Identidad de revisor no válida")
    session_id = _canonical_uuid(
        operator_session_id,
        detail="Sesión individual de revisión no válida",
    )
    canonical_view_event_id = _canonical_uuid(
        view_event_id, detail="Evento de visualización no válido"
    )
    canonical_reauthentication_event_id = _canonical_uuid(
        reauthentication_event_id,
        detail="Evento de reautenticación no válido",
    )
    view_material = _verify_envelope(
        view_payload,
        detail="Visualización del candidato no verificable",
    )
    expected_checklist_keys = {
        "reviewed_entire_document",
        "generated_document_matches",
        "identity_matches",
        "signature_present",
    }
    if (
        set(review_checklist) != expected_checklist_keys
        or any(review_checklist.get(key) is not True for key in expected_checklist_keys)
    ):
        raise HTTPException(status_code=422, detail="Checklist de revisión incompleto")
    if (
        view_material.get("format") != AUTHORIZATION_SIGNATURE_VIEW_VERSION
        or str(view_material.get("case_id")) != _canonical_case_id(case_id)
        or str(view_material.get("candidate_document_id"))
        != str(candidate_material.get("candidate_document_id"))
        or not hmac.compare_digest(
            str(view_material.get("candidate_attestation_sha256") or ""),
            str(candidate_payload.get("material_sha256") or ""),
        )
    ):
        raise HTTPException(status_code=409, detail="Visualización del candidato no verificable")
    actor_hmac = hmac.new(
        _secret(), actor.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    session_hmac = hmac.new(
        _secret(), session_id.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if (
        not hmac.compare_digest(
            str(view_material.get("reviewer_actor_hmac_sha256") or ""), actor_hmac
        )
        or not hmac.compare_digest(
            str(view_material.get("operator_session_hmac_sha256") or ""),
            session_hmac,
        )
    ):
        raise HTTPException(status_code=409, detail="Visualización del candidato no verificable")
    material = {
        "format": SIGNED_AUTHORITY_DOCUMENT_VERSION,
        "case_id": _canonical_case_id(case_id),
        "authority_id": str(authority_material.get("authority_id") or ""),
        "authority_version": str(authority_material.get("authority_version") or ""),
        "authority_material_sha256": _sha256(
            authority_payload.get("material_sha256"),
            detail="Huella de autoridad inválida",
        ),
        "issued_document_id": _canonical_uuid(
            issuance_material.get("document_id"),
            detail="Documento emitido inválido",
        ),
        "issued_document_sha256": _sha256(
            issuance_material.get("document_sha256"),
            detail="Huella del documento emitido inválida",
        ),
        "document_nonce": _canonical_uuid(
            issuance_material.get("document_nonce"),
            detail="Nonce de autorización inválido",
        ),
        "issuance_attestation_sha256": _sha256(
            issuance_payload.get("material_sha256"),
            detail="Huella de emisión inválida",
        ),
        "candidate_document_id": _canonical_uuid(
            candidate_material.get("candidate_document_id"),
            detail="Documento candidato inválido",
        ),
        "candidate_document_sha256": _sha256(
            candidate_material.get("candidate_document_sha256"),
            detail="Huella del candidato inválida",
        ),
        "candidate_attestation_sha256": _sha256(
            candidate_payload.get("material_sha256"),
            detail="Huella de candidato inválida",
        ),
        "decision": "approved",
        "verification_method": "human_ops_review",
        "review_id": str(uuid.uuid4()),
        "reviewer_actor_hmac_sha256": actor_hmac,
        "operator_session_hmac_sha256": session_hmac,
        "view_event_id": canonical_view_event_id,
        "view_attestation_sha256": _sha256(
            view_payload.get("material_sha256"),
            detail="Huella de visualización inválida",
        ),
        "reauthentication_event_id": canonical_reauthentication_event_id,
        "review_checklist_version": "rtm_authorization_review_checklist_v1",
        "review_checklist": dict(review_checklist),
        "reviewed_at": str(reviewed_at or ""),
    }
    _as_utc(material["reviewed_at"])
    return _signed_envelope(material)


def build_rejected_authorization_signature_attestation(
    *,
    case_id: str,
    authority_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    reviewer_actor: str,
    reviewed_at: str,
    reason_code: str,
) -> dict[str, Any]:
    actor = str(reviewer_actor or "").strip()
    candidate_material = candidate_payload.get("material")
    if not actor.startswith("operator:") or len(actor) > 80:
        raise HTTPException(status_code=403, detail="Identidad de revisor no válida")
    if not isinstance(candidate_material, dict):
        raise HTTPException(status_code=409, detail="Candidato de firma no verificable")
    reason = str(reason_code or "").strip()
    if reason not in {
        "document_mismatch",
        "identity_mismatch",
        "signature_missing",
        "illegible",
        "suspected_tampering",
    }:
        raise HTTPException(status_code=422, detail="Motivo de rechazo no válido")
    material = {
        "format": AUTHORIZATION_SIGNATURE_REJECTION_VERSION,
        "case_id": _canonical_case_id(case_id),
        "authority_material_sha256": _sha256(
            authority_payload.get("material_sha256"),
            detail="Huella de autoridad inválida",
        ),
        "candidate_document_id": _canonical_uuid(
            candidate_material.get("candidate_document_id"),
            detail="Documento candidato inválido",
        ),
        "candidate_attestation_sha256": _sha256(
            candidate_payload.get("material_sha256"),
            detail="Huella de candidato inválida",
        ),
        "decision": "rejected",
        "reason_code": reason,
        "review_id": str(uuid.uuid4()),
        "reviewer_actor_hmac_sha256": hmac.new(
            _secret(), actor.encode("utf-8"), hashlib.sha256
        ).hexdigest(),
        "reviewed_at": str(reviewed_at or ""),
    }
    _as_utc(material["reviewed_at"])
    return _signed_envelope(material)


def verify_authorization_signature_view_attestation(
    payload: dict[str, Any],
    *,
    case_id: str,
    candidate_payload: dict[str, Any],
    reviewer_actor: str | None = None,
    operator_session_id: str | None = None,
) -> dict[str, Any]:
    detail = "Visualización del candidato no verificable"
    material = _verify_envelope(payload, detail=detail)
    candidate_material = candidate_payload.get("material")
    if not isinstance(candidate_material, dict):
        raise HTTPException(status_code=409, detail=detail)
    try:
        _as_utc(material.get("viewed_at"))
        _sha256(material.get("reviewer_actor_hmac_sha256"), detail=detail)
        _sha256(material.get("operator_session_hmac_sha256"), detail=detail)
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    if (
        material.get("format") != AUTHORIZATION_SIGNATURE_VIEW_VERSION
        or str(material.get("case_id")) != _canonical_case_id(case_id)
        or str(material.get("candidate_document_id"))
        != str(candidate_material.get("candidate_document_id"))
        or not hmac.compare_digest(
            str(material.get("candidate_document_sha256") or ""),
            str(candidate_material.get("candidate_document_sha256") or ""),
        )
        or not hmac.compare_digest(
            str(material.get("candidate_attestation_sha256") or ""),
            str(candidate_payload.get("material_sha256") or ""),
        )
    ):
        raise HTTPException(status_code=409, detail=detail)
    if reviewer_actor is not None:
        expected_actor = hmac.new(
            _secret(), str(reviewer_actor).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(
            str(material.get("reviewer_actor_hmac_sha256") or ""), expected_actor
        ):
            raise HTTPException(status_code=409, detail=detail)
    if operator_session_id is not None:
        canonical_session_id = _canonical_uuid(operator_session_id, detail=detail)
        expected_session = hmac.new(
            _secret(), canonical_session_id.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(
            str(material.get("operator_session_hmac_sha256") or ""),
            expected_session,
        ):
            raise HTTPException(status_code=409, detail=detail)
    return material


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
            "SELECT authorized, COALESCE(interested_data, '{}'::jsonb), authorized_at, "
            "COALESCE(department, ''), COALESCE(case_type, '') "
            "FROM cases WHERE id=:id"
        ),
        {"id": canonical_case_id},
    ).fetchone()
    if not case_row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    require_dgt_fine_authority_scope(case_row[3], case_row[4])
    if not bool(case_row[0]):
        raise HTTPException(status_code=409, detail="El expediente no está autorizado")

    event_row = conn.execute(
        text(
            "SELECT payload, created_at FROM events "
            "WHERE case_id=:id AND type='case_authorized' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
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
                'case_authority_invalidated_by_identity_change',
                'case_authority_invalidated_by_document_change'
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


def _verify_issue_row(
    *,
    canonical_case_id: str,
    authority: dict[str, Any],
    row: Any,
) -> dict[str, Any]:
    detail = "Documento de autorización emitido no verificable"
    if not row:
        raise HTTPException(status_code=409, detail=detail)
    payload = _payload_dict(row[0])
    material = _verify_envelope(payload, detail=detail)
    authority_material = authority["material"]
    try:
        issued_at = _as_utc(material.get("issued_at"))
        event_created_at = _as_utc(row[1])
        document_id = _canonical_uuid(material.get("document_id"), detail=detail)
        stored_document_id = _canonical_uuid(row[2], detail=detail)
        document_sha256 = _sha256(material.get("document_sha256"), detail=detail)
        stored_sha256 = _sha256(row[3], detail=detail)
        size_bytes = int(material.get("size_bytes"))
        stored_size = int(row[5] or 0)
        _canonical_uuid(material.get("document_nonce"), detail=detail)
    except (HTTPException, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    now = datetime.now(timezone.utc)
    if (
        material.get("format") != AUTHORITY_DOCUMENT_ISSUE_VERSION
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
        or not hmac.compare_digest(document_sha256, stored_sha256)
        or str(material.get("mime")) != "application/pdf"
        or str(row[4] or "") != "application/pdf"
        or size_bytes <= 0
        or size_bytes != stored_size
        or str(material.get("document_version") or "")
        != str(authority_material.get("authority_version") or "")
        or issued_at > now + timedelta(minutes=5)
        or abs((event_created_at - issued_at).total_seconds()) > 300
        or issued_at < _as_utc(authority_material.get("accepted_at"))
    ):
        raise HTTPException(status_code=409, detail=detail)
    return payload


def verify_active_authority_document_issue(
    conn,
    case_id: str,
    *,
    authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the latest generated PDF bound to the current authority grant."""

    canonical_case_id = _canonical_case_id(case_id)
    active_authority = authority or verify_active_case_authority(
        conn, canonical_case_id
    )
    row = conn.execute(
        text(
            """
            SELECT e.payload, e.created_at, d.id, d.sha256, d.mime, d.size_bytes
            FROM events e
            JOIN documents d
              ON d.case_id=e.case_id
             AND d.id::text=e.payload->'material'->>'document_id'
            WHERE e.case_id=:id
              AND e.type='authorization_pdf_issued'
            ORDER BY e.created_at DESC, e.id DESC LIMIT 1
            """
        ),
        {"id": canonical_case_id},
    ).fetchone()
    return _verify_issue_row(
        canonical_case_id=canonical_case_id,
        authority=active_authority,
        row=row,
    )


def require_authority_document_binding(
    issuance_payload: dict[str, Any],
    *,
    authority_material_sha256: str,
    generated_document_id: str,
    generated_document_sha256: str,
    generated_document_version: str,
    document_nonce: str,
    issuance_attestation_sha256: str,
) -> None:
    """Compare the exact browser echo with the server-signed issuance."""

    material = issuance_payload.get("material")
    if not isinstance(material, dict):
        raise HTTPException(status_code=409, detail="Vinculación de autorización inválida")
    comparisons = (
        (
            _sha256(authority_material_sha256, detail="Vinculación inválida"),
            str(material.get("authority_material_sha256") or ""),
        ),
        (
            _canonical_uuid(generated_document_id, detail="Vinculación inválida"),
            str(material.get("document_id") or ""),
        ),
        (
            _sha256(generated_document_sha256, detail="Vinculación inválida"),
            str(material.get("document_sha256") or ""),
        ),
        (
            str(generated_document_version or "").strip(),
            str(material.get("document_version") or ""),
        ),
        (
            _canonical_uuid(document_nonce, detail="Vinculación inválida"),
            str(material.get("document_nonce") or ""),
        ),
        (
            _sha256(issuance_attestation_sha256, detail="Vinculación inválida"),
            str(issuance_payload.get("material_sha256") or ""),
        ),
    )
    if any(
        not hmac.compare_digest(supplied, expected)
        for supplied, expected in comparisons
    ):
        raise HTTPException(status_code=409, detail="Autorización obsoleta o manipulada")


def _verify_candidate_row(
    *,
    canonical_case_id: str,
    authority: dict[str, Any],
    issuance: dict[str, Any],
    row: Any,
    allowed_kinds: frozenset[str],
) -> dict[str, Any]:
    detail = "Candidato de firma no verificable"
    if not row:
        raise HTTPException(status_code=409, detail=detail)
    payload = _payload_dict(row[0])
    material = _verify_envelope(payload, detail=detail)
    authority_material = authority["material"]
    issuance_material = issuance["material"]
    try:
        uploaded_at = _as_utc(material.get("uploaded_at"))
        event_created_at = _as_utc(row[1])
        candidate_id = _canonical_uuid(
            material.get("candidate_document_id"), detail=detail
        )
        stored_id = _canonical_uuid(row[2], detail=detail)
        candidate_sha256 = _sha256(
            material.get("candidate_document_sha256"), detail=detail
        )
        stored_sha256 = _sha256(row[3], detail=detail)
        size_bytes = int(material.get("size_bytes"))
        stored_size = int(row[5] or 0)
    except (HTTPException, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    now = datetime.now(timezone.utc)
    if (
        material.get("format") != AUTHORIZATION_SIGNATURE_CANDIDATE_VERSION
        or str(material.get("review_status")) != "pending_review"
        or str(material.get("case_id")) != canonical_case_id
        or str(material.get("authority_id"))
        != str(authority_material.get("authority_id"))
        or str(material.get("authority_version"))
        != str(authority_material.get("authority_version"))
        or not hmac.compare_digest(
            str(material.get("authority_material_sha256") or ""),
            str(authority.get("material_sha256") or ""),
        )
        or str(material.get("issued_document_id"))
        != str(issuance_material.get("document_id"))
        or not hmac.compare_digest(
            str(material.get("issued_document_sha256") or ""),
            str(issuance_material.get("document_sha256") or ""),
        )
        or str(material.get("issued_document_version"))
        != str(issuance_material.get("document_version"))
        or str(material.get("document_nonce"))
        != str(issuance_material.get("document_nonce"))
        or not hmac.compare_digest(
            str(material.get("issuance_attestation_sha256") or ""),
            str(issuance.get("material_sha256") or ""),
        )
        or candidate_id != stored_id
        or not hmac.compare_digest(candidate_sha256, stored_sha256)
        or str(material.get("mime")) != "application/pdf"
        or str(row[4] or "") != "application/pdf"
        or size_bytes <= 0
        or size_bytes != stored_size
        or str(row[6] or "") not in allowed_kinds
        or uploaded_at > now + timedelta(minutes=5)
        or abs((event_created_at - uploaded_at).total_seconds()) > 300
        or uploaded_at < _as_utc(issuance_material.get("issued_at"))
    ):
        raise HTTPException(status_code=409, detail=detail)
    return payload


def verify_authorization_signature_candidate(
    conn,
    case_id: str,
    candidate_document_id: str,
) -> dict[str, Any]:
    """Verify a still-pending candidate against the active authority/PDF."""

    canonical_case_id = _canonical_case_id(case_id)
    canonical_document_id = _canonical_uuid(
        candidate_document_id, detail="Documento candidato inválido"
    )
    authority = verify_active_case_authority(conn, canonical_case_id)
    issuance = verify_active_authority_document_issue(
        conn, canonical_case_id, authority=authority
    )
    row = conn.execute(
        text(
            """
            SELECT e.payload, e.created_at, d.id, d.sha256, d.mime,
                   d.size_bytes, d.kind
            FROM events e
            JOIN documents d
              ON d.case_id=e.case_id
             AND d.id::text=e.payload->'material'->>'candidate_document_id'
            WHERE e.case_id=:id
              AND e.type='authorization_signature_candidate_uploaded'
              AND d.id=CAST(:document_id AS UUID)
              AND NOT EXISTS (
                  SELECT 1 FROM events reviewed
                  WHERE reviewed.case_id=e.case_id
                    AND reviewed.type IN (
                        'authorization_signature_approved',
                        'authorization_signature_rejected'
                    )
                    AND reviewed.payload->'material'->>'candidate_document_id'=
                        d.id::text
              )
            ORDER BY e.created_at DESC, e.id DESC LIMIT 1
            """
        ),
        {"id": canonical_case_id, "document_id": canonical_document_id},
    ).fetchone()
    candidate = _verify_candidate_row(
        canonical_case_id=canonical_case_id,
        authority=authority,
        issuance=issuance,
        row=row,
        allowed_kinds=frozenset({"authorization_signed_candidate"}),
    )
    return {"authority": authority, "issuance": issuance, "candidate": candidate}


def require_authorization_candidate_digest_unused(
    conn,
    case_id: str,
    *,
    authority_payload: dict[str, Any],
    issuance_payload: dict[str, Any],
    candidate_document_sha256: str,
) -> None:
    """Block byte-for-byte replay under the same active authority issuance."""

    digest = _sha256(
        candidate_document_sha256,
        detail="Huella del candidato inválida",
    )
    authority_digest = _sha256(
        authority_payload.get("material_sha256"),
        detail="Huella de autoridad inválida",
    )
    issuance_digest = _sha256(
        issuance_payload.get("material_sha256"),
        detail="Huella de emisión inválida",
    )
    replay = conn.execute(
        text(
            """
            SELECT 1
            FROM events e
            WHERE e.case_id=:case_id
              AND e.type='authorization_signature_candidate_uploaded'
              AND e.payload->'material'->>'authority_material_sha256'=:authority_sha256
              AND e.payload->'material'->>'issuance_attestation_sha256'=:issuance_sha256
              AND e.payload->'material'->>'candidate_document_sha256'=:candidate_sha256
            LIMIT 1
            """
        ),
        {
            "case_id": _canonical_case_id(case_id),
            "authority_sha256": authority_digest,
            "issuance_sha256": issuance_digest,
            "candidate_sha256": digest,
        },
    ).fetchone()
    if replay:
        raise HTTPException(
            status_code=409,
            detail="Este mismo candidato de firma ya fue presentado",
        )


def project_case_authorization_evidence(
    conn,
    case_id: str,
    *,
    authorized: bool,
    document_kinds: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any]:
    """Produce a minimal UI state only after cryptographic verification."""

    result: dict[str, Any] = {
        "authorization_evidence_status": "not_submitted",
        "signed_authority_verified": False,
        "candidate_document_id": None,
        "candidate_attestation_sha256": None,
    }
    if not authorized:
        return result
    kinds = {str(kind or "").strip() for kind in document_kinds}
    if "authorization_signed" in kinds:
        try:
            verify_signed_case_authority(conn, case_id)
            return {
                **result,
                "authorization_evidence_status": "verified",
                "signed_authority_verified": True,
            }
        except HTTPException as exc:
            if exc.status_code != 409:
                raise

    if "authorization_signed_candidate" in kinds:
        candidate_row = conn.execute(
            text(
                """
                SELECT id FROM documents
                WHERE case_id=:case_id
                  AND kind='authorization_signed_candidate'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"case_id": _canonical_case_id(case_id)},
        ).fetchone()
        if candidate_row:
            try:
                chain = verify_authorization_signature_candidate(
                    conn, case_id, str(candidate_row[0])
                )
                return {
                    **result,
                    "authorization_evidence_status": "pending_review",
                    "candidate_document_id": str(candidate_row[0]),
                    "candidate_attestation_sha256": str(
                        chain["candidate"].get("material_sha256") or ""
                    ),
                }
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
    if "authorization_signed_rejected" in kinds:
        result["authorization_evidence_status"] = "rejected"
    return result


def verify_signed_case_authority(conn, case_id: str) -> dict[str, Any]:
    """Verify authority evidence approved by a scoped individual reviewer.

    A public upload is only a candidate.  This verifier intentionally ignores
    all legacy ``authorization_signed_uploaded`` events.
    """

    canonical_case_id = _canonical_case_id(case_id)
    authority = verify_active_case_authority(conn, canonical_case_id)
    issuance = verify_active_authority_document_issue(
        conn, canonical_case_id, authority=authority
    )
    row = conn.execute(
        text(
            """
            SELECT approved.payload, approved.created_at,
                   candidate.payload, candidate.created_at,
                   d.id, d.sha256, d.mime, d.size_bytes, d.kind
            FROM events approved
            JOIN documents d
              ON d.case_id=approved.case_id
             AND d.id::text=approved.payload->'material'->>'candidate_document_id'
            JOIN events candidate
              ON candidate.case_id=approved.case_id
             AND candidate.type='authorization_signature_candidate_uploaded'
             AND candidate.payload->'material'->>'candidate_document_id'=d.id::text
             AND candidate.payload->>'material_sha256'=
                 approved.payload->'material'->>'candidate_attestation_sha256'
            WHERE approved.case_id=:id
              AND approved.type='authorization_signature_approved'
            ORDER BY approved.created_at DESC, approved.id DESC LIMIT 1
            """
        ),
        {"id": canonical_case_id},
    ).fetchone()
    detail = "Revisión firmada de autoridad no verificable"
    if not row:
        raise HTTPException(status_code=409, detail=detail)
    approval = _payload_dict(row[0])
    approval_material = _verify_envelope(approval, detail=detail)
    candidate_row = (row[2], row[3], row[4], row[5], row[6], row[7], row[8])
    candidate = _verify_candidate_row(
        canonical_case_id=canonical_case_id,
        authority=authority,
        issuance=issuance,
        row=candidate_row,
        allowed_kinds=frozenset({"authorization_signed"}),
    )
    authority_material = authority["material"]
    issuance_material = issuance["material"]
    candidate_material = candidate["material"]
    try:
        reviewed_at = _as_utc(approval_material.get("reviewed_at"))
        approval_created_at = _as_utc(row[1])
        _canonical_uuid(approval_material.get("review_id"), detail=detail)
        view_event_id = _canonical_uuid(
            approval_material.get("view_event_id"), detail=detail
        )
        reauthentication_event_id = _canonical_uuid(
            approval_material.get("reauthentication_event_id"), detail=detail
        )
        reviewer_digest = _sha256(
            approval_material.get("reviewer_actor_hmac_sha256"), detail=detail
        )
        session_digest = _sha256(
            approval_material.get("operator_session_hmac_sha256"), detail=detail
        )
        view_attestation_digest = _sha256(
            approval_material.get("view_attestation_sha256"), detail=detail
        )
    except (HTTPException, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    checklist = approval_material.get("review_checklist")
    checklist_keys = {
        "reviewed_entire_document",
        "generated_document_matches",
        "identity_matches",
        "signature_present",
    }
    now = datetime.now(timezone.utc)
    if (
        approval_material.get("format") != SIGNED_AUTHORITY_DOCUMENT_VERSION
        or approval_material.get("decision") != "approved"
        or approval_material.get("verification_method") != "human_ops_review"
        or str(approval_material.get("case_id")) != canonical_case_id
        or str(approval_material.get("authority_id"))
        != str(authority_material.get("authority_id"))
        or str(approval_material.get("authority_version"))
        != str(authority_material.get("authority_version"))
        or not hmac.compare_digest(
            str(approval_material.get("authority_material_sha256") or ""),
            str(authority.get("material_sha256") or ""),
        )
        or str(approval_material.get("issued_document_id"))
        != str(issuance_material.get("document_id"))
        or not hmac.compare_digest(
            str(approval_material.get("issued_document_sha256") or ""),
            str(issuance_material.get("document_sha256") or ""),
        )
        or str(approval_material.get("document_nonce"))
        != str(issuance_material.get("document_nonce"))
        or not hmac.compare_digest(
            str(approval_material.get("issuance_attestation_sha256") or ""),
            str(issuance.get("material_sha256") or ""),
        )
        or str(approval_material.get("candidate_document_id"))
        != str(candidate_material.get("candidate_document_id"))
        or not hmac.compare_digest(
            str(approval_material.get("candidate_document_sha256") or ""),
            str(candidate_material.get("candidate_document_sha256") or ""),
        )
        or not hmac.compare_digest(
            str(approval_material.get("candidate_attestation_sha256") or ""),
            str(candidate.get("material_sha256") or ""),
        )
        or len(reviewer_digest) != 64
        or len(session_digest) != 64
        or len(view_attestation_digest) != 64
        or approval_material.get("review_checklist_version")
        != "rtm_authorization_review_checklist_v1"
        or not isinstance(checklist, dict)
        or set(checklist) != checklist_keys
        or any(checklist.get(key) is not True for key in checklist_keys)
        or reviewed_at > now + timedelta(minutes=5)
        or abs((approval_created_at - reviewed_at).total_seconds()) > 300
        or reviewed_at < _as_utc(candidate_material.get("uploaded_at"))
    ):
        raise HTTPException(status_code=409, detail=detail)

    view_row = conn.execute(
        text(
            """
            SELECT payload, created_at
            FROM events
            WHERE id=CAST(:event_id AS UUID)
              AND case_id=:case_id
              AND type='authorization_signature_candidate_viewed'
            LIMIT 1
            """
        ),
        {"event_id": view_event_id, "case_id": canonical_case_id},
    ).fetchone()
    if not view_row:
        raise HTTPException(status_code=409, detail=detail)
    view_payload = _payload_dict(view_row[0])
    if not hmac.compare_digest(
        str(view_payload.get("material_sha256") or ""),
        view_attestation_digest,
    ):
        raise HTTPException(status_code=409, detail=detail)
    view_material = verify_authorization_signature_view_attestation(
        view_payload,
        case_id=canonical_case_id,
        candidate_payload=candidate,
    )
    try:
        viewed_at = _as_utc(view_material.get("viewed_at"))
        view_created_at = _as_utc(view_row[1])
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    if (
        not hmac.compare_digest(
            str(view_material.get("reviewer_actor_hmac_sha256") or ""),
            reviewer_digest,
        )
        or not hmac.compare_digest(
            str(view_material.get("operator_session_hmac_sha256") or ""),
            session_digest,
        )
        or viewed_at > reviewed_at
        or reviewed_at - viewed_at > timedelta(minutes=15)
        or abs((view_created_at - viewed_at).total_seconds()) > 300
    ):
        raise HTTPException(status_code=409, detail=detail)

    reauthentication_row = conn.execute(
        text(
            """
            SELECT operator_id, session_id, occurred_at
            FROM rtm_operator_access_events
            WHERE id=CAST(:event_id AS UUID)
              AND event_type='auth.reauthenticated'
              AND result='success'
              AND reason_code='password_reverified'
            LIMIT 1
            """
        ),
        {"event_id": reauthentication_event_id},
    ).fetchone()
    if not reauthentication_row:
        raise HTTPException(status_code=409, detail=detail)
    try:
        reauthenticated_at = _as_utc(reauthentication_row[2])
        operator_actor = f"operator:{_canonical_uuid(reauthentication_row[0], detail=detail)}"
        operator_session_id = _canonical_uuid(reauthentication_row[1], detail=detail)
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    expected_reviewer_digest = hmac.new(
        _secret(), operator_actor.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    expected_session_digest = hmac.new(
        _secret(), operator_session_id.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if (
        not hmac.compare_digest(reviewer_digest, expected_reviewer_digest)
        or not hmac.compare_digest(session_digest, expected_session_digest)
        or reauthenticated_at > reviewed_at
        or reviewed_at - reauthenticated_at > timedelta(minutes=5)
    ):
        raise HTTPException(status_code=409, detail=detail)
    return {
        **authority,
        "authority_document_issuance": issuance,
        "signed_document_attestation": approval,
    }


__all__ = [
    "AUTHORITY_ATTESTATION_VERSION",
    "AUTHORITY_DOCUMENT_ISSUE_VERSION",
    "AUTHORITY_VERSION",
    "AUTHORIZATION_SIGNATURE_CANDIDATE_VERSION",
    "AUTHORIZATION_SIGNATURE_VIEW_VERSION",
    "AUTHORIZATION_SIGNATURE_REJECTION_VERSION",
    "SIGNED_AUTHORITY_DOCUMENT_VERSION",
    "build_authority_document_issue_attestation",
    "build_authorization_signature_candidate_attestation",
    "build_authorization_signature_view_attestation",
    "build_case_authority_payload",
    "build_rejected_authorization_signature_attestation",
    "build_reviewed_signed_authority_attestation",
    "project_case_authorization_evidence",
    "require_authorization_candidate_digest_unused",
    "require_dgt_fine_authority_scope",
    "require_authority_document_binding",
    "verify_active_case_authority",
    "verify_active_authority_document_issue",
    "verify_authorization_signature_candidate",
    "verify_authorization_signature_view_attestation",
    "verify_signed_case_authority",
]
