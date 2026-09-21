"""Add an explicitly simulated filing to the existing synthetic local fine.

Never changes the real case status, payment, facts, authorization decisions,
resource approvals or Presenter events. No external filing is performed.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, time, timedelta, timezone
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
from case_authority import _signed_envelope
from rtm_core import local_document_storage as storage
from rtm_core.local_operator_auth import assert_local_operator_auth_ready
from rtm_core.post_filing_deadlines import VERSION, EVENT, MADRID, local_post_filing_projection
from scripts import rtm_local_fine_fixture as fine
from scripts.rtm_local_operator_setup import require_local_database
from scripts.rtm_local_test_documents import pdf_bytes


FOLLOWUPS_EVENT = "rtm_local_submission_followups_created"
FOLLOWUPS_ACTOR = "local_fixture:post_filing_v1"


def _ensure_followups(conn, projection):
    """Called only after local/case/evidence checks, under the fixture lock.

    These are operational reminders, never a confirmed statutory deadline.
    Dates come from the retained filing, so reruns cannot move them forward.
    Existing reminders (including resolved ones) are never rewritten.
    """
    clock = projection["calculation"]
    filed_on = date.fromisoformat(clock["filing_date"])
    reference_on = date.fromisoformat(clock["reference_due_on"])
    reminders = [
        ("rtm_local_filing_review", filed_on + timedelta(days=1),
         "PRUEBA LOCAL · Revisar el cómputo tras la presentación",
         "Revisión operativa programada para el día siguiente a la presentación simulada. "
         "Comprobar el justificante, el calendario y las incidencias que afectan al cómputo. "
         "Esta fecha es un recordatorio de trabajo, no un vencimiento legal."),
        ("rtm_local_filing_reference", reference_on - timedelta(days=7),
         "PRUEBA LOCAL · Revisar antes de la fecha de referencia",
         "Aviso operativo siete días naturales antes de la referencia "
         + reference_on.strftime("%d/%m/%Y") + ". "
         "Contrastar resoluciones, notificaciones, suspensiones y calendario. "
         "La referencia sigue pendiente de revisión; este aviso no acredita caducidad ni silencio."),
    ]
    created, items = [], []
    for kind, due_on, title, description in reminders:
        followup_id = str(uuid.uuid5(fine.NAMESPACE, VERSION + ":" + projection["event_id"] + ":" + kind))
        row = conn.execute(text("""
            SELECT case_id,kind,source_event_type,created_by,status,due_at,title
            FROM ops_followups WHERE id=:id FOR UPDATE
        """), {"id": followup_id}).mappings().one_or_none()
        if row is not None:
            if (str(row["case_id"]), row["kind"], row["source_event_type"], row["created_by"]) != (
                    fine.CASE_ID, kind, EVENT, FOLLOWUPS_ACTOR):
                raise RuntimeError("Colision de seguimiento: no se modifica")
        else:
            due_at = datetime.combine(due_on, time(9), tzinfo=MADRID)
            conn.execute(text("""
                INSERT INTO ops_followups(id,case_id,kind,status,title,description,due_at,
                    source_event_type,created_by)
                VALUES (:id,:case,:kind,'pending',:title,:description,:due,:source,:actor)
            """), {"id": followup_id, "case": fine.CASE_ID, "kind": kind, "title": title,
                     "description": description, "due": due_at, "source": EVENT, "actor": FOLLOWUPS_ACTOR})
            created.append(followup_id)
            row = {"status": "pending", "due_at": due_at, "title": title}
        items.append({"id": followup_id, "kind": kind, "status": row["status"],
                      "due_at": row["due_at"].isoformat() if row["due_at"] else None,
                      "title": row["title"]})
    if created:
        fine.event(conn, FOLLOWUPS_EVENT, _signed_envelope({
            "case_id": fine.CASE_ID, "synthetic_only": True,
            "source_event_id": projection["event_id"], "created_ids": created,
            "actor": FOLLOWUPS_ACTOR, "policy": "operational_next_day_and_seven_days_before_reference_v1",
        }), datetime.now(timezone.utc))
    return {"created_count": len(created), "count": len(items), "items": items}


def seed_submission(conn, *, now=None):
    require_local_database(conn)
    storage.assert_local_document_storage_ready()
    conn.execute(text("SELECT pg_advisory_xact_lock(20260920,31001)"))
    case = conn.execute(text("""
        SELECT test_mode,interested_data,expediente_ref,product_code,department,case_type
        FROM cases WHERE id=:id FOR UPDATE
    """), {"id": fine.CASE_ID}).one()
    if (case[0] is not True or not isinstance(case[1], dict)
            or case[1].get("local_test") != fine.IDENTITY["local_test"]
            or tuple(case[2:]) != (fine.REFERENCE, "RTM_LOCAL_SIMULATED_REVIEW", "traffic", "fine")):
        raise RuntimeError("El caso no es el expediente ficticio esperado")
    existing = conn.execute(text("SELECT id FROM events WHERE case_id=:id AND type=:kind"),
                            {"id": fine.CASE_ID, "kind": EVENT}).first()
    if existing:
        result = local_post_filing_projection(conn, fine.CASE_ID)
        if result["status"] != "presented_simulated":
            raise RuntimeError("La simulacion existente necesita revision; no se sobrescribe")
        return {"created": False, **result, "followups": _ensure_followups(conn, result)}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.date().isoformat() < "2026-09-20":
        raise RuntimeError("Fecha de simulacion no valida")
    material = {"format": VERSION, "case_id": fine.CASE_ID, "synthetic_only": True,
        "legal_submission_executed": False, "filing_kind": "traffic_allegations",
        "registration_number": "RTM-SIMULADO-2026-001", "submitted_at": now.isoformat(),
        "procedure_started_on": "2026-09-17", "procedural_events_reviewed": False,
        "calendar": None}
    common = ["Persona ficticia: " + fine.NAME, "Expediente de prueba: " + fine.REFERENCE,
              "Case ID: " + fine.CASE_ID, "No existe presentacion ni registro administrativo real."]
    files = {
        "initiation": ("rtm_local_initiation_notice", pdf_bytes("Inicio de procedimiento ficticio", common + [
            "Fecha de inicio SIMULADA: 17/09/2026.",
            "Este dato se introduce expresamente para probar el computo de caducidad.",
            "No se deduce de la fecha impresa en la multa ni acredita una incoacion real."])),
        "receipt": ("rtm_local_submission_receipt", pdf_bytes("Justificante ficticio de alegaciones", common + [
            "Tipo de escrito SIMULADO: alegaciones a denuncia de trafico.",
            "Registro ficticio: " + material["registration_number"],
            "Presentacion SIMULADA (ISO): " + material["submitted_at"],
            "No acredita firma, aprobacion del recurso ni efectos juridicos."])),
    }
    for prefix, (kind, content) in files.items():
        document_id = str(uuid.uuid5(fine.NAMESPACE, VERSION + ":" + prefix))
        bucket, key = storage.upload_bytes(fine.CASE_ID, kind, content, ".pdf", "application/pdf")
        digest = hashlib.sha256(content).hexdigest()
        conn.execute(text("""
            INSERT INTO documents(id,case_id,kind,b2_bucket,b2_key,sha256,mime,size_bytes,created_at)
            VALUES (:id,:case,:kind,:bucket,:key,:sha,'application/pdf',:size,:now)
        """), {"id": document_id, "case": fine.CASE_ID, "kind": kind, "bucket": bucket,
                 "key": key, "sha": digest, "size": len(content), "now": now})
        material[prefix + "_document_id"] = document_id
        material[prefix + "_sha256"] = digest
    fine.event(conn, EVENT, _signed_envelope(material), now)
    result = local_post_filing_projection(conn, fine.CASE_ID)
    if result["status"] != "presented_simulated":
        raise RuntimeError("La simulacion no supera la verificacion")
    return {"created": True, **result, "followups": _ensure_followups(conn, result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Preparado: presentacion SIMULADA de alegaciones para", fine.REFERENCE)
        print("Inicio del procedimiento ficticio: 17/09/2026. Fecha de presentacion: momento de la prueba.")
        print("No aprueba autorizaciones/recursos ni cambia el pago o el estado real. Usa --apply.")
        return
    launcher = runpy.run_path(str(ROOT.parent / "RTM_LOCAL_OPS_V2.py"))
    trial_url = URL.create("postgresql+psycopg", username="rtm_local_app", password="configuration_check_only",
                           host="127.0.0.1", port=5432, database="rtm_local")
    local_root = launcher["local_document_root"](ROOT.parent)
    dummy_keys = {name: "configuration_check_only" for name in
                  ("hmac_key", "operator_token", "public_case_secret", "authority_signing_secret")}
    assert_local_operator_auth_ready(launcher["local_environment"](
        trial_url.render_as_string(hide_password=False), dummy_keys, local_root))
    print("RTM | PRESENTACION FICTICIA Y CONTADOR DE PLAZOS")
    password = getpass.getpass("Contrasena de PostgreSQL para rtm_local_app: ")
    url = trial_url.set(password=password)
    del password
    environment = launcher["local_environment"](url.render_as_string(hide_password=False),
                                                launcher["local_keys"](), local_root)
    assert_local_operator_auth_ready(environment)
    os.environ.update(environment)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            result = seed_submission(conn)
    finally:
        engine.dispose()
    report = ROOT.parent / "PRUEBAS_RTM" / (fine.REFERENCE + "-submission-simulated.json")
    launcher["atomic_write"](report, json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
    print("PRESENTACION SIMULADA REGISTRADA")
    print("Seguimientos guardados:", result["followups"]["count"],
          "| Nuevos:", result["followups"]["created_count"])
    print("Referencia del computo:", result["calculation"]["reference_due_on"])
    print("Pendiente de calendario y revision juridica. No se declara caducidad ni silencio.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Operacion interrumpida.")
        raise SystemExit(130)
    except Exception as exc:
        print("No se ha completado la simulacion. Tipo:", type(exc).__name__)
        raise SystemExit(1)
