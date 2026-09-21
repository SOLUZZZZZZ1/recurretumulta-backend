"""Local-only generic RTM document issuance; never grants verified authority.

The wording is the existing RTM generic template. Every artifact is visibly a
local test. Signed evidence and document kinds are separate from the DGT chain.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import text

from b2_storage import (
    delete_object, download_bytes_limited, local_document_storage_enabled,
    require_http_document_storage, upload_bytes,
)
from database import get_engine
from pdf_builder import build_pdf
from rtm_core.case_state_policy import lock_case_for_public_material_mutation
from rtm_core.local_operator_auth import (
    assert_local_operator_auth_ready, local_operator_auth_requested,
)

VERSION = "rtm_generic_authorization_v1"
KIND = "rtm_generic_local"
ISSUE_EVENT = "rtm_generic_authorization_issued"
CANDIDATE_EVENT = "rtm_generic_authorization_candidate_received"
PDF_KIND = "rtm_authorization_pdf"
CANDIDATE_KIND = "rtm_authorization_signed_candidate"
MAX_PDF_BYTES = 10 * 1024 * 1024
FAMILIES = frozenset({"bancos", "energia", "telecomunicaciones", "seguros"})
LOCAL_MARK = "PRUEBA LOCAL SIN VALIDEZ"
PRIVATE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer", "Vary": "X-RTM-Case-Token",
    "X-Content-Type-Options": "nosniff",
}


def require_local_generic_profile() -> None:
    if not local_operator_auth_requested():
        raise HTTPException(404, "Not found")
    try:
        assert_local_operator_auth_ready()
        local = local_document_storage_enabled()
    except RuntimeError as exc:
        raise HTTPException(503, "La autorización de pruebas local no está disponible") from exc
    if not local:
        raise HTTPException(503, "La custodia local de documentos no está disponible")
    require_http_document_storage()
    _secret()


def _secret() -> bytes:
    secret = str(os.getenv("RTM_AUTHORITY_SIGNING_SECRET") or "").strip().encode("utf-8")
    if len(secret) < 32:
        raise HTTPException(503, "La firma de evidencias locales no está disponible")
    return secret


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_ascii(left, right: str) -> bool:
    return isinstance(left, str) and left.isascii() and hmac.compare_digest(left, right)


def _seal(material: dict) -> dict:
    material = {**material, "version": VERSION, "authorization_kind": KIND}
    encoded = _canonical(material)
    return {"material": material, "material_sha256": _sha(encoded),
            "hmac_sha256": hmac.new(_secret(), VERSION.encode() + b":" + encoded, hashlib.sha256).hexdigest()}


def _verify(payload, *, event: str, case_id: str) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("material"), dict):
        raise HTTPException(409, "La evidencia de autorización no es verificable")
    material = payload["material"]
    expected = _seal(material)
    if (material.get("version") != VERSION or material.get("authorization_kind") != KIND
            or material.get("event") != event or material.get("case_id") != case_id
            or not _same_ascii(payload.get("material_sha256"), expected["material_sha256"])
            or not _same_ascii(payload.get("hmac_sha256"), expected["hmac_sha256"])):
        raise HTTPException(409, "La evidencia de autorización no es verificable")
    return material


def load_snapshot(conn, case_id: str, *, mutate: bool = False) -> tuple[dict, str]:
    if mutate:
        lock_case_for_public_material_mutation(conn, case_id)
    row = conn.execute(text("""
        SELECT COALESCE(interested_data, '{}'::jsonb) AS interested,
               department, case_type, test_mode, authorized
        FROM cases WHERE id=CAST(:id AS UUID)
    """), {"id": case_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Expediente no encontrado")
    interested = row["interested"]
    if (row["department"] != "claims" or row["case_type"] != "consumer"
            or row["test_mode"] is not True or row["authorized"] is not False
            or not isinstance(interested, dict)
            or interested.get("public_service_family") not in FAMILIES):
        raise HTTPException(409, "Este documento solo corresponde a expedientes locales de consumo sin autoridad verificada")
    if any(not str(interested.get(key) or "").strip()
           for key in ("full_name", "dni_nie", "domicilio_notif", "email")):
        raise HTTPException(409, "Faltan datos para el documento de autorización")
    documents = conn.execute(text("""
        SELECT id, kind, sha256 FROM documents
        WHERE case_id=CAST(:id AS UUID) AND kind IN ('identity_front','identity_back')
        ORDER BY kind, id
    """), {"id": case_id}).mappings().all()
    if len(documents) != 2 or {d["kind"] for d in documents} != {"identity_front", "identity_back"}:
        raise HTTPException(409, "La documentación de identidad no está completa")
    snapshot = {"case_id": case_id, "department": row["department"], "case_type": row["case_type"],
                "interested": interested,
                "identity_documents": [{"id": str(d["id"]), "kind": d["kind"], "sha256": d["sha256"]}
                                       for d in documents]}
    digest = hmac.new(_secret(), b"rtm-generic-snapshot:" + _canonical(snapshot), hashlib.sha256).hexdigest()
    return snapshot, digest


def generic_body(case_id: str, interested: dict) -> str:
    """Existing generic RTM template, with an explicit local-test watermark."""
    name = interested.get("full_name") or ""
    dni = interested.get("dni_nie") or interested.get("dni") or ""
    address = interested.get("domicilio_notif") or interested.get("domicilio") or ""
    email = interested.get("email") or ""
    phone = interested.get("telefono") or ""
    # Exact claims branch from cases._rtm_auth_scope; no new powers or legal text.
    scope = (
        "actuar ante compañías aéreas, aseguradoras, empresas, organismos de consumo "
        "y otras entidades relacionadas con la reclamación."
    )
    return f"""{LOCAL_MARK}

AUTORIZACIÓN DE REPRESENTACIÓN RTM

Expediente RTM: {case_id}
Departamento: claims
Tipo de expediente: consumer

DATOS DEL INTERESADO

Nombre y apellidos: {name}
DNI/NIE/Pasaporte: {dni}
Domicilio: {address}
Email: {email}
Teléfono: {phone}

AUTORIZACIÓN

Yo, {name}, con documento identificativo {dni}, autorizo expresamente a
LA TALAMANQUINA, S.L. (RTM / RecurreTuMulta), con NIF B75440115, para {scope}

Esta autorización queda limitada exclusivamente a las actuaciones necesarias para la gestión
del expediente RTM {case_id} y no comprende facultades ajenas a dicho asunto.

El interesado declara que los datos y documentos aportados son veraces y que dispone de
legitimación suficiente para solicitar la gestión.

Firma del interesado:



____________________________________

Nombre: {name}
DNI/NIE/Pasaporte: {dni}
Fecha: _____________________________

{LOCAL_MARK}
"""


def _latest_issue(conn, case_id):
    row = conn.execute(text("""
        SELECT payload FROM events WHERE case_id=CAST(:id AS UUID) AND type=:type
        ORDER BY created_at DESC, id DESC LIMIT 1
    """), {"id": case_id, "type": ISSUE_EVENT}).fetchone()
    return row[0] if row else None


def _append_event(conn, case_id, event, payload):
    conn.execute(text("""
        INSERT INTO events(case_id,type,payload,created_at)
        VALUES (CAST(:id AS UUID),:type,CAST(:payload AS JSONB),NOW())
    """), {"id": case_id, "type": event, "payload": json.dumps(payload, ensure_ascii=False)})


def _document(conn, case_id, material, kind):
    row = conn.execute(text("""
        SELECT id, b2_bucket, b2_key, sha256, mime, size_bytes FROM documents
        WHERE case_id=CAST(:id AS UUID) AND id=CAST(:document_id AS UUID) AND kind=:kind
    """), {"id": case_id, "document_id": material["document_id"], "kind": kind}).mappings().one_or_none()
    if not row or any(row[key] != material[key] for key in ("b2_bucket", "b2_key", "sha256", "mime", "size_bytes")):
        raise HTTPException(409, "El documento emitido no coincide con su evidencia")
    return row


def verified_issue(conn, case_id, snapshot_digest):
    payload = _latest_issue(conn, case_id)
    if not payload:
        raise HTTPException(409, "Primero debe emitir el documento de autorización")
    material = _verify(payload, event=ISSUE_EVENT, case_id=case_id)
    if not _same_ascii(material.get("snapshot_sha256"), snapshot_digest):
        raise HTTPException(409, "Los datos del expediente han cambiado; emita una nueva autorización")
    _document(conn, case_id, material, PDF_KIND)
    return payload


def has_pending_local_candidate(conn, case_id: str) -> bool:
    """Project receipt of the current local candidate, never verified authority."""
    require_local_generic_profile()
    try:
        _, digest = load_snapshot(conn, case_id)
        issue = verified_issue(conn, case_id, digest)
        row = conn.execute(text("""
            SELECT payload FROM events WHERE case_id=CAST(:id AS UUID) AND type=:type
            ORDER BY created_at DESC, id DESC LIMIT 1
        """), {"id": case_id, "type": CANDIDATE_EVENT}).fetchone()
        if not row:
            return False
        candidate = _verify(row[0], event=CANDIDATE_EVENT, case_id=case_id)
        expected = {
            "snapshot_sha256": digest,
            "issuance_attestation_sha256": issue["material_sha256"],
            "generated_document_id": issue["material"]["document_id"],
            "document_nonce": issue["material"]["nonce"],
            "evidence_status": "pending_review",
        }
        if any(not _same_ascii(candidate.get(key), value) for key, value in expected.items()):
            return False
        _document(conn, case_id, candidate, CANDIDATE_KIND)
        return True
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            return False
        raise


def binding_for(payload):
    m = payload["material"]
    return {"case_id": m["case_id"], "authorization_kind": KIND,
            "generated_document_id": m["document_id"], "generated_document_sha256": m["sha256"],
            "generated_document_version": VERSION, "document_nonce": m["nonce"],
            "issuance_attestation_sha256": payload["material_sha256"]}


def require_binding(payload, supplied: dict):
    expected = binding_for(payload)
    fields = ("generated_document_id", "generated_document_sha256", "generated_document_version",
              "document_nonce", "issuance_attestation_sha256")
    if any(not _same_ascii(supplied.get(k), expected[k]) for k in fields):
        raise HTTPException(409, "El archivo no está vinculado a la autorización vigente")


def _insert_document(conn, case_id, kind, bucket, key, data):
    document_id = str(uuid.uuid4())
    digest = _sha(data)
    conn.execute(text("""
        INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
        VALUES (CAST(:document_id AS UUID),CAST(:id AS UUID),:kind,:bucket,:key,:sha256,
                'application/pdf',:size,NOW())
    """), {"document_id": document_id, "id": case_id, "kind": kind, "bucket": bucket,
           "key": key, "sha256": digest, "size": len(data)})
    return {"document_id": document_id, "b2_bucket": bucket, "b2_key": key,
            "sha256": digest, "mime": "application/pdf", "size_bytes": len(data)}


def _cleanup(coordinate):
    if coordinate:
        try:
            delete_object(*coordinate)
        except Exception:
            pass


def issue_generic_authorization(case_id: str) -> dict:
    require_local_generic_profile()
    coordinate = None
    try:
        with get_engine().begin() as conn:
            snapshot, digest = load_snapshot(conn, case_id, mutate=True)
            previous = _latest_issue(conn, case_id)
            if previous:
                material = _verify(previous, event=ISSUE_EVENT, case_id=case_id)
                if material.get("snapshot_sha256") == digest:
                    _document(conn, case_id, material, PDF_KIND)
                    return issue_envelope(previous)
            data = build_pdf("AUTORIZACIÓN DE REPRESENTACIÓN RTM", generic_body(case_id, snapshot["interested"]))
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(413, "Documento generado demasiado grande")
            coordinate = upload_bytes(case_id, "rtm_authorization", data, ".pdf", "application/pdf")
            document = _insert_document(conn, case_id, PDF_KIND, *coordinate, data)
            payload = _seal({"case_id": case_id, "event": ISSUE_EVENT, "snapshot_sha256": digest,
                             "nonce": str(uuid.uuid4()), "issued_at": datetime.now(timezone.utc).isoformat(),
                             **document})
            _append_event(conn, case_id, ISSUE_EVENT, payload)
    except Exception:
        _cleanup(coordinate)
        raise
    return issue_envelope(payload)


def issue_envelope(payload):
    return {"ok": True, "case_id": payload["material"]["case_id"], "authorized": False,
            "signed_authority_verified": False, "authorization_kind": KIND,
            "authorization_evidence_status": "document_issued",
            "authorization_document_binding": binding_for(payload)}


def read_generic_authorization(case_id: str) -> bytes:
    require_local_generic_profile()
    with get_engine().begin() as conn:
        _, digest = load_snapshot(conn, case_id)
        payload = verified_issue(conn, case_id, digest)
        m = payload["material"]
        data = download_bytes_limited(m["b2_bucket"], m["b2_key"], max_bytes=MAX_PDF_BYTES, case_id=case_id)
        if len(data) != m["size_bytes"] or not hmac.compare_digest(_sha(data), m["sha256"]):
            raise HTTPException(409, "El PDF no coincide con el documento emitido")
        return data


def store_generic_candidate(case_id: str, data: bytes, binding: dict) -> dict:
    require_local_generic_profile()
    coordinate = None
    try:
        with get_engine().begin() as conn:
            _, digest = load_snapshot(conn, case_id, mutate=True)
            issue = verified_issue(conn, case_id, digest)
            require_binding(issue, binding)
            candidate_digest = _sha(data)
            if hmac.compare_digest(candidate_digest, issue["material"]["sha256"]):
                raise HTTPException(409, "El PDF es el documento emitido sin cambios; aporte un candidato distinto")
            prior = conn.execute(text("""
                SELECT 1 FROM documents WHERE case_id=CAST(:id AS UUID)
                  AND kind=:kind AND sha256=:sha256 LIMIT 1
            """), {"id": case_id, "kind": CANDIDATE_KIND, "sha256": candidate_digest}).fetchone()
            if prior:
                raise HTTPException(409, "Este candidato ya está registrado")
            coordinate = upload_bytes(case_id, "rtm_authorization_candidate", data, ".pdf", "application/pdf")
            document = _insert_document(conn, case_id, CANDIDATE_KIND, *coordinate, data)
            payload = _seal({"case_id": case_id, "event": CANDIDATE_EVENT, "snapshot_sha256": digest,
                             "issuance_attestation_sha256": issue["material_sha256"],
                             "generated_document_id": issue["material"]["document_id"],
                             "document_nonce": issue["material"]["nonce"],
                             "received_at": datetime.now(timezone.utc).isoformat(),
                             "evidence_status": "pending_review", **document})
            _append_event(conn, case_id, CANDIDATE_EVENT, payload)
            # No UPDATE of cases.authorized/payment_status and no DGT evidence.
    except Exception:
        _cleanup(coordinate)
        raise
    return {"ok": True, "case_id": case_id, "authorized": False, "signed_authority_verified": False,
            "authorization_kind": KIND, "authorization_evidence_status": "pending_review",
            "document_id": document["document_id"], "document_sha256": document["sha256"]}
