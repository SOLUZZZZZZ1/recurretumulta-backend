"""Local preparation mode for the existing working-document/version contract.

No authority, facts, payment or case state is promoted by this projection.
Document references are metadata, explicitly not a reading of their contents.
"""
from dataclasses import dataclass
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from rtm_core.local_document_storage import assert_local_document_storage_ready
from rtm_core.ops_case_scope import OpsCaseScope
from rtm_core.working_document import _digest
from rtm_core.workspace_service import _identity_projection, _contact_projection

VERSION = "rtm_case_working_document_v1"


@dataclass(frozen=True)
class CaseDraftGrant:
    operator_id: str


def local_grant(scope: OpsCaseScope) -> CaseDraftGrant:
    if (not scope.individual_session or not scope.scope_all
            or scope.role_code != "rtm.supervisor"
            or not {"ops.view", "ops.supervise"}.issubset(scope.permissions)):
        raise HTTPException(403, "Se requiere una sesión individual de supervisor")
    try:
        operator_id = str(UUID(scope.operator_id))
        assert_local_document_storage_ready()
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(503, "El perfil local y su custodia no están disponibles") from exc
    return CaseDraftGrant(operator_id)


def case_row(conn, case_id):
    # Same case lock as the other version writers; held through event+custody commit.
    row = conn.execute(text("SELECT to_jsonb(c) FROM cases c WHERE c.id=:case_id FOR UPDATE"),
                       {"case_id": case_id}).first()
    if not row:
        raise HTTPException(404, "Expediente no encontrado")
    data = row[0]
    if not isinstance(data, dict) or not {"id", "interested_data", "contact_name", "contact_email",
            "customer_comment", "department", "case_type", "test_mode"}.issubset(data):
        raise HTTPException(409, "La lectura de los datos del expediente está incompleta")
    interested = data["interested_data"]
    marker = interested.get("local_test") if isinstance(interested, dict) else None
    if (data["test_mode"] is not True or not isinstance(marker, dict)
            or marker.get("synthetic") is not True or marker.get("local_only") is not True
            or str(data["id"]) != case_id):
        raise HTTPException(409, "El borrador local requiere un expediente sintético identificado")
    scalar_keys = ("full_name", "name", "dni_nie", "dni", "identity_number", "domicilio_notif",
                   "domicilio", "matricula", "plate", "telefono", "phone", "preferred_contact", "public_service_family")
    invalid = any(interested.get(key) is not None and not isinstance(interested[key], str) for key in scalar_keys)
    invalid |= any(data[key] is not None and not isinstance(data[key], str)
                   for key in ("contact_name", "contact_email", "customer_comment"))
    address = interested.get("address")
    invalid |= address is not None and not isinstance(address, (str, dict))
    if isinstance(address, dict):
        invalid |= any(value is not None and not isinstance(value, str) for value in address.values())
    if invalid:
        raise HTTPException(409, "La lectura contiene datos incompatibles; no se interpretan como ausentes")
    return data


def context(conn, case_id, grant):
    if not isinstance(grant, CaseDraftGrant):
        raise HTTPException(403, "Sesión de borrador no válida")
    try:
        assert_local_document_storage_ready()
    except RuntimeError as exc:
        raise HTTPException(503, "La custodia local no está disponible") from exc
    case_row(conn, case_id)
    # A preparation context is deliberately NOT a representation/authority hash.
    return _digest({"mode": VERSION, "case_id": case_id, "synthetic_only": True,
                    "legal_authority": False})


def load_case_document(conn, case_id, grant):
    context(conn, case_id, grant)
    data = case_row(conn, case_id)
    documents = []
    for row in conn.execute(text("""
        SELECT id::text AS id,kind,sha256,size_bytes,mime,created_at FROM documents
        WHERE case_id=:case_id AND kind NOT IN
          ('rtm_working_document_pdf','rtm_working_draft_pdf','external_revision')
        ORDER BY id LIMIT 101
    """), {"case_id": case_id}).mappings():
        doc = dict(row)
        if (len(documents) >= 100 or not re.fullmatch(r"[a-f0-9]{64}", str(doc["sha256"]))
                or type(doc["size_bytes"]) is not int or doc["size_bytes"] <= 0
                or not isinstance(doc["mime"], str) or not doc["mime"]):
            raise HTTPException(409, "La relación de fuentes documentales no es verificable o supera el límite")
        if doc["created_at"] is not None:
            doc["created_at"] = doc["created_at"].isoformat()
        doc.update(reading_status="not_read", operator_export_allowed=False)
        documents.append(doc)
    identity, contact = _identity_projection(data), _contact_projection(data)
    unavailable = {"status": "not_available", "value": None,
                   "reason": "No hay una identidad revisada para este papel en el contrato de alta."}
    sources = {"case_id": case_id, "data_status": "complete",
        "roles": {
            "interested": {"status": "declared", "value": identity,
                           "source": "cases.interested_data"},
            "contact": {"status": "declared", "value": contact,
                        "source": "cases.contact_name/contact_email; cases.interested_data.telefono/preferred_contact"},
            "driver": dict(unavailable), "owner": dict(unavailable), "representative": dict(unavailable)},
        "customer_comment": data["customer_comment"],
        "public_service_family": data["interested_data"].get("public_service_family"),
        "department": data["department"], "case_type": data["case_type"],
        "documents": documents, "document_reading": "not_performed"}
    def shown(value):
        return value if isinstance(value, str) and value.strip() else "No consta"
    lines = ["BORRADOR DE TRABAJO · PRUEBA LOCAL · PENDIENTE DE REVISIÓN",
        f"Expediente: {case_id}", f"Familia aportada: {shown(sources['public_service_family'])}", "",
        f"Interesado/a (formulario): {shown(identity['full_name'])}",
        f"Identificador: {shown(identity['dni_nie'])}", f"Domicilio: {shown(identity['address'])}",
        f"Contacto (no identifica al interesado): {shown(contact['name'])}",
        f"Correo de contacto: {shown(contact['email'])}",
        "Conductor: identidad no disponible; no se deduce del interesado.",
        "Titular: identidad no disponible; no se deduce del interesado.",
        "Representante: identidad no disponible; no se acredita representación.", "",
        "RELATO APORTADO (sin validación documental)", shown(sources["customer_comment"]), "",
        "REFERENCIAS DOCUMENTALES (metadatos; contenido no leído)",
        *(f"{doc['kind']} · {doc['id']} · SHA-256 {doc['sha256']}" for doc in documents), "",
        "REDACCIÓN DEL OPERADOR", "Pendiente de completar y revisar.", "",
        "Guardar o seleccionar no aprueba, firma, autoriza, paga ni presenta este borrador."]
    return {"ok": True, "case_id": case_id, "version": VERSION, "status": "working_draft",
        "title": "Borrador vinculado al expediente", "document_kind": "case_preparation",
        "content": "\n".join(lines) + "\n", "sources": sources,
        "source_sha256": _digest(sources), "used_document_ids": [d["id"] for d in documents],
        "synthetic_only": True, "final_resource_generated": False, "operator_export_allowed": False}
