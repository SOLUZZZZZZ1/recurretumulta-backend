"""Versioned working drafts for the explicit local fine fixture, never final resources."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from case_authority import _signed_envelope, _verify_envelope, verify_signed_case_authority
from pdf_builder import build_pdf
from rtm_core import authority_repository as facts_repository
from rtm_core import local_document_storage as storage
from rtm_core.local_operator_auth import local_operator_auth_requested
from rtm_core.traffic_parking_preparation import build_parking_preparation

VERSION = "rtm_local_traffic_working_draft_v1"
EVENT = "rtm_local_traffic_working_draft_saved"
DOCUMENT_KIND = "rtm_working_draft_pdf"
MAX_PDF_BYTES = 2 * 1024 * 1024
FIELDS = {
    "matricula": "Matrícula", "organismo": "Organismo",
    "expediente_ref": "Referencia del expediente", "fecha_documento": "Fecha del documento",
    "fecha_notificacion": "Fecha de notificación", "sancion_importe_eur": "Sanción (EUR)",
    "hecho_denunciado_literal": "Hecho denunciado",
}
EXTRA_FIELDS = {
    # Documentary subject data stays optional and separate from the fixture's
    # declared identity; it does not identify the driver.
    "document_subject_name": "Nombre de la persona interesada en el documento",
    "document_subject_id": "Identificador de la persona interesada en el documento",
    "radar_modelo_hint": "Modelo de radar indicado",
    "lugar_infraccion": "Lugar de la infracción", "hora_infraccion": "Hora de la infracción",
    "fecha_infraccion": "Fecha de la infracción", "tipo_documento": "Tipo de documento",
    "fase_procedimental": "Fase del procedimiento", "fecha_limite": "Fecha límite documentada",
    "norma_hint": "Norma indicada", "articulo_infringido_num": "Artículo", "apartado_infringido_num": "Apartado",
    "ordenanza_aplicable": "Ordenanza indicada", "senalizacion_estacionamiento": "Señalización del estacionamiento",
    "horario_estacionamiento": "Horario del estacionamiento", "autorizacion_estacionamiento": "Permiso o tique de estacionamiento",
    "tipo_denunciante": "Tipo de denunciante", "prueba_estacionamiento": "Prueba documental del estacionamiento",
    "contradiccion_estacionamiento": "Contradicción documentada", "pago_multa_reducido": "Multa pagada con reducción",
    "fotografia_vehiculo_presente": "Fotografía del vehículo en la documentación", "importe_reducido_eur": "Importe reducido (EUR)",
    "velocidad_medida_kmh": "Velocidad medida (km/h)", "velocidad_limite_kmh": "Velocidad límite (km/h)",
    "puntos_detraccion": "Puntos", "plazo_pago_dias": "Plazo de pago documentado (días)",
}
MARKER = {"synthetic": True, "local_only": True, "fixture": "rtm_local_fine_fixture_v1",
          "payment_simulated": True, "real_charge": False}


class WorkingDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_latest_id: str | None
    grounds: str = Field(default="", max_length=20000)
    request_text: str = Field(default="", max_length=6000)
    pending_notes: str = Field(default="", max_length=6000)
    change_reason: str = Field(min_length=10, max_length=2000)
    draft_acknowledged: bool

    @model_validator(mode="after")
    def valid_review(self):
        if self.draft_acknowledged is not True:
            raise ValueError("Confirma que guardas un borrador pendiente de revisión jurídica")
        if self.expected_latest_id is not None and str(UUID(self.expected_latest_id)) != self.expected_latest_id:
            raise ValueError("Versión anterior no válida")
        if any(any(ord(c) < 32 and c not in "\n\t" for c in value) for value in
               (self.grounds, self.request_text, self.pending_notes, self.change_reason)):
            raise ValueError("El texto contiene caracteres de control")
        return self


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def require_fixture(conn, case_id):
    if not local_operator_auth_requested():
        raise HTTPException(409, "El borrador de trabajo está disponible solo en la prueba local")
    storage.assert_local_document_storage_ready()
    meta = conn.execute(text("""
        SELECT id,test_mode,interested_data,payment_status,authorized,status,department,case_type
        FROM cases WHERE id=:id FOR UPDATE
    """), {"id": case_id}).mappings().one_or_none()
    if not meta: raise HTTPException(404, "Expediente no encontrado")
    interested = meta["interested_data"] or {}
    if (meta["test_mode"] is not True or not isinstance(interested, dict)
            or interested.get("local_test") != MARKER
            or meta["department"] != "traffic" or meta["case_type"] != "fine"):
        raise HTTPException(409, "Este borrador corresponde exclusivamente a la multa ficticia local")
    return dict(meta)


def load_history(conn, case_id):
    history, previous = [], None
    rows = conn.execute(text("""
        SELECT id,payload FROM events WHERE case_id=:case AND type=:kind ORDER BY created_at,id
    """), {"case": case_id, "kind": EVENT})
    for row in rows:
        material = _verify_envelope(row[1], detail="El historial del borrador no es verificable")
        if (material.get("format") != VERSION or material.get("case_id") != case_id
                or material.get("id") != str(row[0]) or material.get("previous_id") != previous
                or material.get("sequence") != len(history) + 1
                or material.get("status") != "working_draft" or material.get("synthetic_only") is not True
                or not str(material.get("actor", "")).startswith("operator:")):
            raise HTTPException(409, "La cadena de versiones del borrador no es verificable")
        previous = str(row[0])
        history.append(material)
    return history


def projection(conn, case_id):
    meta = require_fixture(conn, case_id)
    blockers = []
    if meta["payment_status"] != "paid": blockers.append("Falta el pago simulado del estudio.")
    if not meta["authorized"]: blockers.append("Falta la autorización del cliente.")
    if meta["status"] in facts_repository._NON_MUTABLE_CASE_STATUSES:
        blockers.append("El estado del expediente no permite preparar otro borrador.")
    authority = None
    try: authority = verify_signed_case_authority(conn, case_id)
    except HTTPException as exc:
        if exc.status_code != 409: raise
        blockers.append("Primero revisa y aprueba la autorización firmada en el apartado superior.")
    record = facts_repository.latest_validated_facts(conn, case_id, active_only=True, for_update=True)
    facts = record.facts.facts if record else {}
    source_documents = [dict(row) for row in conn.execute(text("""
        SELECT id,kind,sha256,size_bytes FROM documents
        WHERE case_id=:case AND kind<>:draft ORDER BY id
    """), {"case": case_id, "draft": DOCUMENT_KIND}).mappings()]
    original_ids = {str(row["id"]) for row in source_documents if row["kind"] == "original"}
    reviewed = []
    for key, label in FIELDS.items():
        fact = facts.get(key)
        valid = bool(fact and fact.status.value == "validated" and not fact.conflicts
                     and fact.value not in (None, "", [], {})
                     and any(source.document_id in original_ids and source.source_type == "operator_document_review"
                             and source.page_index is not None and source.evidence for source in fact.sources))
        if not valid: blockers.append(f"Revisa y confirma: {label}.")
        reviewed.append({"key": key, "label": label, "value": fact.value if valid else None, "reviewed": valid})
    if record and record.facts.conflicts: blockers.append("Quedan conflictos en los hechos del expediente.")
    # Additional facts are optional; only documentary, human-reviewed values
    # enter the draft. False is a documented answer, not an empty value.
    for key, label in EXTRA_FIELDS.items():
        fact = facts.get(key)
        if (fact and fact.status.value == "validated" and not fact.conflicts
                and fact.value not in (None, "", [], {})
                and any(source.document_id in original_ids and source.source_type == "operator_document_review"
                        and source.page_index is not None and source.evidence for source in fact.sources)):
            reviewed.append({"key": key, "label": label, "value": fact.value, "reviewed": True})
    interested = meta["interested_data"]
    identity = {key: interested.get(key) for key in ("full_name", "dni_nie", "domicilio_notif")}
    if any(not isinstance(v, str) or not v.strip() for v in identity.values()):
        blockers.append("Faltan datos de identidad en el expediente de prueba.")
    guide = None
    if record and not blockers:
        documentary_facts = {key: fact for key, fact in facts.items()
            if fact.status.value == "validated" and not fact.conflicts
            and any(source.document_id in original_ids and source.source_type == "operator_document_review"
                    and source.page_index is not None and source.evidence for source in fact.sources)}
        guide = build_parking_preparation(record.facts.model_copy(update={"facts": documentary_facts}))
    source_hash = digest({"facts_id": record.id if record else None,
        "facts_sha256": record.payload_sha256 if record else None,
        "authority": authority, "documents": source_documents, "identity": identity,
        "payment_status": meta["payment_status"], "authorized": meta["authorized"], "preparation_guide": guide})
    history = load_history(conn, case_id)
    public = [{**entry, "current_source": entry["source_sha256"] == source_hash and not blockers}
              for entry in reversed(history)]
    return {"version": VERSION, "case_id": case_id, "synthetic_only": True,
        "source_sha256": source_hash, "facts_id": record.id if record else None,
        "facts_sequence": record.sequence if record else None,
        "blockers": blockers, "can_prepare": not blockers, "reviewed_facts": reviewed,
        "identity": identity, "latest_id": history[-1]["id"] if history else None, "history": public,
        "legal_review_pending": True, "final_resource_generated": False, "preparation_guide": guide}


def render_draft(state, body, sequence):
    values = {f["key"]: str(f["value"]) for f in state["reviewed_facts"]}
    identity = state["identity"]
    def display(value): return ("Sí" if value else "No") if isinstance(value, bool) else str(value)
    facts = "\n".join(f'{item["label"]}: {display(item["value"])}' for item in state["reviewed_facts"])
    return (f"BORRADOR DE TRABAJO - SIMULACION LOCAL\n"
        f"VERSION {sequence} - PENDIENTE DE REVISION JURIDICA\n"
        f"Sin firma, aprobacion final ni presentacion administrativa.\n\n"
        f"DESTINATARIO: {values['organismo']}\n"
        f"REFERENCIA: {values['expediente_ref']}\n"
        f"EXPEDIENTE RTM: {state['case_id']}\n\n"
        f"PERSONA FICTICIA: {identity['full_name']}\n"
        f"IDENTIFICADOR DE PRUEBA: {identity['dni_nie']}\n"
        f"DOMICILIO FICTICIO: {identity['domicilio_notif']}\n\n"
        f"I. DATOS CONTRASTADOS DEL EXPEDIENTE\n{facts}\n\n"
        f"II. MOTIVOS PROPUESTOS POR SUPERVISION\n"
        f"{body.grounds or '[PENDIENTE: completar y contrastar los motivos del escrito.]'}\n\n"
        f"III. PETICION PROPUESTA\n"
        f"{body.request_text or '[PENDIENTE: concretar la peticion y el tramite procedente.]'}\n\n"
        f"REVISIONES PENDIENTES\n"
        f"{body.pending_notes or '[PENDIENTE: identificar la documentacion y comprobaciones adicionales.]'}\n"
        f"Revisar individualmente el tramite, la familia juridica, los fundamentos, las pruebas y el plazo.\n"
        f"Este borrador no contiene una validacion automatica de los motivos ni de la peticion.\n")


def save_draft(conn, *, case_id, body, actor, uploaded):
    if not actor.startswith("operator:") or str(UUID(actor[9:])) != actor[9:]:
        raise HTTPException(403, "Identidad individual requerida")
    state = projection(conn, case_id)
    if body.expected_source_sha256 != state["source_sha256"] or body.expected_latest_id != state["latest_id"]:
        raise HTTPException(409, "El expediente o el borrador han cambiado. Recarga antes de guardar")
    if not state["can_prepare"]:
        raise HTTPException(409, "Completa primero la autorización y la revisión de los hechos")
    draft_id, document_id = str(uuid4()), str(uuid4())
    sequence = len(state["history"]) + 1
    content = render_draft(state, body, sequence)
    pdf = build_pdf("BORRADOR - SOLO PRUEBAS LOCALES", content)
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
        raise HTTPException(409, "No se puede preparar el PDF del borrador")
    bucket, key = storage.upload_bytes(case_id, DOCUMENT_KIND, pdf, ".pdf", "application/pdf")
    uploaded.append((bucket, key))
    now = datetime.now(timezone.utc)
    material = {"format": VERSION, "id": draft_id, "case_id": case_id, "sequence": sequence,
        "previous_id": state["latest_id"], "source_sha256": state["source_sha256"],
        "facts_id": state["facts_id"], "facts_sequence": state["facts_sequence"],
        "actor": actor, "created_at": now.isoformat(), "status": "working_draft", "synthetic_only": True,
        "grounds": body.grounds, "request_text": body.request_text, "pending_notes": body.pending_notes,
        "change_reason": body.change_reason, "draft_acknowledged": True,
        "preparation_guide": state["preparation_guide"],
        "content": content, "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "pdf": {"document_id": document_id, "sha256": hashlib.sha256(pdf).hexdigest(), "size_bytes": len(pdf)}}
    conn.execute(text("""
        INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
        VALUES (:id,:case,:kind,:bucket,:key,:sha,'application/pdf',:size,:now)
    """), {"id": document_id, "case": case_id, "kind": DOCUMENT_KIND, "bucket": bucket,
           "key": key, "sha": material["pdf"]["sha256"], "size": len(pdf), "now": now})
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at) VALUES (:id,:case,:kind,CAST(:payload AS JSONB),:now)
    """), {"id": draft_id, "case": case_id, "kind": EVENT,
           "payload": json.dumps(_signed_envelope(material)), "now": now})
    result = projection(conn, case_id)
    if result["latest_id"] != draft_id: raise RuntimeError("No se puede verificar el borrador guardado")
    return result


def read_pdf(conn, case_id, draft_id):
    state = projection(conn, case_id)
    entry = next((item for item in state["history"] if item["id"] == draft_id), None)
    if not entry: raise HTTPException(404, "Borrador no encontrado")
    row = conn.execute(text("""
        SELECT b2_bucket,b2_key,sha256,size_bytes,mime FROM documents
        WHERE id=:id AND case_id=:case AND kind=:kind
    """), {"id": entry["pdf"]["document_id"], "case": case_id, "kind": DOCUMENT_KIND}).first()
    if not row or row[2] != entry["pdf"]["sha256"] or row[3] != entry["pdf"]["size_bytes"] or row[4] != "application/pdf":
        raise HTTPException(409, "El PDF guardado no es verificable")
    data = storage.download_bytes_limited(row[0], row[1], max_bytes=MAX_PDF_BYTES, case_id=case_id)
    if not data.startswith(b"%PDF-") or len(data) != row[3] or hashlib.sha256(data).hexdigest() != row[2]:
        raise HTTPException(409, "El PDF guardado no es verificable")
    return data, entry["pdf"]
