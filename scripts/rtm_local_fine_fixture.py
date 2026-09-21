"""Create one new, idempotent synthetic fine in the dedicated local database.

Payment is a labelled fixture, never a Stripe payment. The authorization is a
pending candidate: this script cannot approve it, create an operator session,
freeze facts, generate a final resource, or submit anything.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from case_authority import (
    AUTHORITY_VERSION, build_case_authority_payload,
    build_authority_document_issue_attestation,
    build_authorization_signature_candidate_attestation,
    verify_active_case_authority, verify_authorization_signature_candidate,
)
from rtm_core.authority_repository import create_validated_facts
from rtm_core.contracts import ValidatedFacts
from rtm_core.local_operator_auth import assert_local_operator_auth_ready
from rtm_core import local_document_storage as storage
from scripts.rtm_local_operator_setup import require_local_database, existing_local_operator
from scripts.rtm_local_test_documents import pdf_bytes

VERSION = "rtm_local_fine_fixture_v1"
NAMESPACE = uuid.UUID("fb9c2e52-05de-4e8d-8969-cd7e50a0ac12")
CASE_ID = str(uuid.uuid5(NAMESPACE, VERSION))
REFERENCE = "RTM-PRUEBA-MULTA-001"
NAME = "PRUEBA LOCAL MULTA - PAGO SIMULADO"
IDENTITY = {
    "full_name": NAME, "dni_nie": "RTMTEST002",
    "domicilio_notif": "Calle de Prueba 1, 08240 Manresa, Barcelona",
    "email": "prueba.multa@example.com", "telefono": "000000000",
    "department": "traffic", "case_type": "fine", "public_service_family": "trafico",
    "local_test": {"synthetic": True, "local_only": True, "fixture": VERSION,
                   "payment_simulated": True, "real_charge": False},
}


def document_id(kind: str) -> str:
    return str(uuid.uuid5(NAMESPACE, VERSION + ":" + kind))


def documents() -> dict[str, bytes]:
    common = ["Escenario ficticio de desarrollo. Sin efectos juridicos ni cobro real.",
              "Persona ficticia: " + NAME, "Identificador de prueba: RTMTEST002",
              "Expediente ficticio: " + REFERENCE, "Case ID: " + CASE_ID]
    authorization = common + [
        "En este escenario se simula una solicitud de estudio y recurso de multa a RTM.",
        "Domicilio ficticio: Calle de Prueba 1, 08240 Manresa, Barcelona.",
        "Email ficticio: prueba.multa@example.com",
        "Esta simulacion no concede representacion sobre ninguna persona real.",
    ]
    return {
        "original": pdf_bytes("Notificacion de multa ficticia", common + [
            "Organismo ficticio: Ayuntamiento de Prueba RTM",
            "Matricula ficticia: RTM-TEST-002", "Fecha del documento: 17/09/2026",
            "Fecha de notificacion: 18/09/2026", "Importe de la sancion ficticia: 200,00 EUR",
            "Hecho ficticio: estacionar en una zona de prueba senalizada.",
            "No es una denuncia ni exige pago o presentacion ante una administracion.",
        ]),
        "identity_front": pdf_bytes("Identificacion ficticia - frontal", common + [
            "No es un DNI, NIE ni pasaporte. No identifica a una persona real."]),
        "identity_back": pdf_bytes("Identificacion ficticia - reverso", common + [
            "Domicilio ficticio: Calle de Prueba 1, 08240 Manresa, Barcelona."]),
        "authorization_pdf": pdf_bytes("Autorizacion ficticia - modelo", authorization + [
            "Casilla de firma para la simulacion: ____________________"]),
        "authorization_signed_candidate": pdf_bytes("Autorizacion ficticia - candidato", authorization + [
            "Casilla de firma para la simulacion: FIRMA FICTICIA RTM TEST",
            "La firma escrita es simulada. Candidato pendiente de revision humana."]),
    }


def event(conn, kind, payload, now):
    conn.execute(text("INSERT INTO events(case_id,type,payload,created_at) "
                      "VALUES (:case,:kind,CAST(:payload AS JSONB),:now)"),
                 {"case": CASE_ID, "kind": kind, "payload": json.dumps(payload), "now": now})


def seed_case(conn, store) -> dict:
    """Requires the caller's transaction and a store that tracks new objects."""
    require_local_database(conn)
    # Serialize this one fixture without locking or rewriting existing cases.
    conn.execute(text("SELECT pg_advisory_xact_lock(20260920, 31001)"))
    old = conn.execute(text("SELECT test_mode,interested_data FROM cases WHERE id=:id FOR UPDATE"),
                       {"id": CASE_ID}).mappings().one_or_none()
    if old is not None:
        metadata = old["interested_data"] or {}
        if old["test_mode"] is not True or metadata.get("local_test") != IDENTITY["local_test"]:
            raise RuntimeError("El identificador existe sin la marca exacta de esta prueba; no se modifica")
        return {"case_id": CASE_ID, "created": False, "reference": REFERENCE}
    now = datetime.now(timezone.utc)
    conn.execute(text("""
        INSERT INTO cases(id,contact_email,contact_name,status,department,case_type,category,
            organismo,expediente_ref,source_module,customer_comment,interested_data,
            test_mode,authorized,authorized_at,payment_status,product_code,paid_at)
        VALUES (:id,:email,:name,'manual_review','traffic','fine','traffic',:authority,:ref,
            'rtm_local_fixture',:comment,CAST(:identity AS JSONB),TRUE,TRUE,:now,'paid',
            'RTM_LOCAL_SIMULATED_REVIEW',:now)
    """), {"id": CASE_ID, "email": IDENTITY["email"], "name": NAME,
             "authority": "Ayuntamiento de Prueba RTM", "ref": REFERENCE,
             "comment": "CASO FICTICIO. Pago del estudio SIMULADO: 10 EUR; cobro real: 0 EUR. Autorizacion pendiente de revision humana.",
             "identity": json.dumps(IDENTITY), "now": now})
    event(conn, "rtm_local_fixture_created", {"version": VERSION, "synthetic": True,
          "local_only": True, "authorization_review": "pending"}, now)
    event(conn, "rtm_local_payment_simulated", {"version": VERSION, "synthetic": True,
          "local_only": True, "payment_status": "paid", "simulated_amount_cents": 1000,
          "real_amount_charged_cents": 0, "currency": "EUR", "provider": "local_fixture",
          "stripe_session_id": None, "stripe_payment_intent": None}, now)
    authority = build_case_authority_payload(case_id=CASE_ID, interested=IDENTITY,
                                             accepted_at=now.isoformat(), request_ip="127.0.0.1")
    event(conn, "case_authorized", authority, now)
    rendered = documents()
    for kind, content in rendered.items():
        folder = "authorization_signature_candidate" if kind == "authorization_signed_candidate" else kind
        bucket, key = store(CASE_ID, folder, content, ".pdf", "application/pdf")
        conn.execute(text("""
            INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
            VALUES (:id,:case,:kind,:bucket,:key,:sha,'application/pdf',:size,:now)
        """), {"id": document_id(kind), "case": CASE_ID, "kind": kind, "bucket": bucket,
                 "key": key, "sha": hashlib.sha256(content).hexdigest(), "size": len(content), "now": now})
    issued = rendered["authorization_pdf"]
    issuance = build_authority_document_issue_attestation(case_id=CASE_ID, authority_payload=authority,
        document_id=document_id("authorization_pdf"), document_sha256=hashlib.sha256(issued).hexdigest(),
        size_bytes=len(issued), document_version=AUTHORITY_VERSION,
        document_nonce=str(uuid.uuid5(NAMESPACE, VERSION + ":nonce")), issued_at=now.isoformat())
    event(conn, "authorization_pdf_issued", issuance, now)
    signed = rendered["authorization_signed_candidate"]
    candidate = build_authorization_signature_candidate_attestation(case_id=CASE_ID,
        authority_payload=authority, issuance_payload=issuance,
        document_id=document_id("authorization_signed_candidate"),
        document_sha256=hashlib.sha256(signed).hexdigest(), size_bytes=len(signed), uploaded_at=now.isoformat())
    event(conn, "authorization_signature_candidate_uploaded", candidate, now)

    # Every field starts pending. This is a fixture, not an AI or human approval.
    names = ["organismo", "expediente_ref", "matricula", "hecho_denunciado_literal",
             "fecha_documento", "fecha_notificacion", "sancion_importe_eur"]
    draft = ValidatedFacts(case_id=CASE_ID, service="traffic", extractor_version=VERSION,
        source_document_ids=[document_id("original")], unresolved=names,
        facts={name: {"status": "unresolved", "value": None, "notes": [
            "Dato pendiente: contrastar con el original ficticio, pagina 1."]} for name in names})
    record = create_validated_facts(conn, case_id=CASE_ID, facts=draft, created_by="fixture:local_fine")
    verify_active_case_authority(conn, CASE_ID)
    verify_authorization_signature_candidate(conn, CASE_ID, document_id("authorization_signed_candidate"))
    return {"case_id": CASE_ID, "created": True, "reference": REFERENCE,
            "facts_id": record.id, "authorization_review": "pending", "real_amount_charged_cents": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Preparado: expediente ficticio", REFERENCE)
        print("Base exclusiva: rtm_local / rtm_local_app / 127.0.0.1:5432")
        print("Pago simulado: 10 EUR. Cobro real: 0 EUR. No se aprueba la autorizacion.")
        print("Usa --apply para crearlo con la contrasena introducida en esta ventana.")
        return
    launcher_path = ROOT.parent / "RTM_LOCAL_OPS_V2.py"
    launcher = runpy.run_path(str(launcher_path))
    # Reject a foreign/deployed environment before requesting a password.
    trial_url = URL.create("postgresql+psycopg", username="rtm_local_app", password="configuration_check_only",
                           host="127.0.0.1", port=5432, database="rtm_local")
    local_root = launcher["local_document_root"](ROOT.parent)
    dummy_keys = {name: "configuration_check_only" for name in
                  ("hmac_key", "operator_token", "public_case_secret", "authority_signing_secret")}
    assert_local_operator_auth_ready(launcher["local_environment"](
        trial_url.render_as_string(hide_password=False), dummy_keys, local_root))
    print("RTM | NUEVO EXPEDIENTE FICTICIO CON PAGO SIMULADO")
    print("No se cobran importes ni se modifican otros expedientes.")
    password = getpass.getpass("Contrasena de PostgreSQL para rtm_local_app: ")
    url = trial_url.set(password=password)
    del password
    environment = launcher["local_environment"](url.render_as_string(hide_password=False),
                                                launcher["local_keys"](), local_root)
    assert_local_operator_auth_ready(environment)
    os.environ.update(environment)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    written = []
    def store(*args):
        coordinate = storage.upload_bytes(*args)
        written.append(coordinate)
        return coordinate
    try:
        with engine.begin() as conn:
            if not existing_local_operator(conn):
                raise RuntimeError("Primero debe existir el supervisor local de pruebas")
            result = seed_case(conn, store)
    except Exception:
        for bucket, key in reversed(written):
            storage.delete_object(bucket, key)
        raise
    finally:
        engine.dispose()
    print("EXPEDIENTE CREADO" if result["created"] else "EL EXPEDIENTE YA EXISTE; SE CONSERVAN SUS CAMBIOS")
    print("Referencia:", REFERENCE)
    print("Pago: SIMULADO. Cobro real: 0 EUR. Revision humana de autorizacion: pendiente.")
    print("Acceso: http://127.0.0.1:5173/ops/review/" + CASE_ID)
    folder = launcher["checked_directory"](ROOT.parent / "PRUEBAS_RTM" / REFERENCE, create=True)
    for kind, content in documents().items():
        path = folder / (kind + ".pdf")
        if not path.exists():
            with path.open("xb") as out: out.write(content)
    print("Documentos ficticios:", folder)
    # Only non-sensitive fixture identifiers are written to the report.
    report = ROOT.parent / "PRUEBAS_RTM" / (REFERENCE + ".json")
    launcher["atomic_write"](report, json.dumps(result, indent=2).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Operacion interrumpida.")
        raise SystemExit(130)
    except Exception as exc:
        # Database errors can include credentials/connection parameters.
        print("No se ha completado la preparacion. Tipo:", type(exc).__name__)
        raise SystemExit(1)
