"""Repair only the original synthetic fine's incorrect candidate storage folder.

Preserves bytes, document identity, attestations and the old immutable object.
Does not create a human view/review event, approve a signature or alter payment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from case_authority import verify_authorization_signature_candidate
from ops_operator_router import _download_verified_candidate_pdf
from rtm_core import local_document_storage as storage
from rtm_core.local_operator_auth import assert_local_operator_auth_ready
from rtm_core.upload_security import PDF, validate_document_bytes
from scripts import rtm_local_fine_fixture as fixture
from scripts.rtm_local_operator_setup import require_local_database

MAX_BYTES = 10 * 1024 * 1024
FOLDER = "authorization_signature_candidate"
REPAIR_EVENT = "rtm_local_candidate_storage_repaired"


def repair_candidate(conn) -> dict:
    """Caller owns the transaction. A failed repair never deletes custody data."""
    require_local_database(conn)
    storage.assert_local_document_storage_ready()
    conn.execute(text("SELECT pg_advisory_xact_lock(20260920,31001)"))
    case = conn.execute(text("""
        SELECT test_mode,interested_data,expediente_ref,product_code,department,case_type
        FROM cases WHERE id=:id FOR UPDATE
    """), {"id": fixture.CASE_ID}).one()
    if (case[0] is not True or not isinstance(case[1], dict)
            or case[1].get("local_test") != fixture.IDENTITY["local_test"]
            or tuple(case[2:]) != (fixture.REFERENCE, "RTM_LOCAL_SIMULATED_REVIEW", "traffic", "fine")):
        raise RuntimeError("El expediente no es la prueba local prevista; no se cambia nada")
    candidate_id = fixture.document_id("authorization_signed_candidate")
    row = conn.execute(text("""
        SELECT b2_bucket,b2_key,sha256,mime,size_bytes FROM documents
        WHERE id=:document AND case_id=:case AND kind='authorization_signed_candidate'
        FOR UPDATE
    """), {"document": candidate_id, "case": fixture.CASE_ID}).one()
    chain = verify_authorization_signature_candidate(conn, fixture.CASE_ID, candidate_id)
    bucket, old_key = storage.validate_local_object_coordinate(row[0], row[1], case_id=fixture.CASE_ID)
    original_folder = old_key.split("/")[2]
    if original_folder not in {"authorization_signed_candidate", FOLDER}:
        raise RuntimeError("La ubicacion no corresponde al fallo conocido")
    content = storage.download_bytes_limited(bucket, old_key, max_bytes=MAX_BYTES, case_id=fixture.CASE_ID)
    digest = hashlib.sha256(content).hexdigest()
    signed_digest = chain["candidate"]["material"]["candidate_document_sha256"]
    if (row[3] != PDF or len(content) != row[4]
            or not hmac.compare_digest(digest, row[2])
            or not hmac.compare_digest(digest, signed_digest)):
        raise RuntimeError("El PDF no coincide con su huella; no se repara")
    validate_document_bytes(filename="candidate.pdf", declared_mime=PDF, data=content,
                            max_bytes=MAX_BYTES, allowed_mimes={PDF})
    result = {"case_id": fixture.CASE_ID, "candidate_document_id": candidate_id,
              "sha256": digest, "size_bytes": len(content), "changed": False,
              "authorization_review": "pending", "pdf_download_verified": False,
              "payment_changed": False, "old_object_preserved": True}
    if original_folder != FOLDER:
        # Keep the old object even if the transaction fails. No signed bytes change.
        new_bucket, new_key = storage.upload_bytes(fixture.CASE_ID, FOLDER, content, ".pdf", PDF)
        verified = storage.download_bytes_limited(new_bucket, new_key, max_bytes=MAX_BYTES, case_id=fixture.CASE_ID)
        if verified != content:
            raise RuntimeError("La copia no conserva los bytes originales")
        updated = conn.execute(text("""
            UPDATE documents SET b2_key=:new_key
            WHERE id=:document AND case_id=:case AND kind='authorization_signed_candidate'
              AND b2_bucket=:bucket AND b2_key=:old_key AND sha256=:sha AND size_bytes=:size
        """), {"new_key": new_key, "document": candidate_id, "case": fixture.CASE_ID,
                 "bucket": new_bucket, "old_key": old_key, "sha": digest, "size": len(content)})
        if updated.rowcount != 1:
            raise RuntimeError("El documento cambio durante la reparacion")
        # Audit the relocation without exposing storage coordinates in OPS events.
        fixture.event(conn, REPAIR_EVENT, {
            "version": "rtm_local_candidate_storage_repair_v1", "fixture": fixture.VERSION,
            "synthetic": True, "local_only": True, "candidate_document_id": candidate_id,
            "sha256": digest, "size_bytes": len(content),
            "old_storage_key_sha256": hashlib.sha256(old_key.encode()).hexdigest(),
            "new_storage_key_sha256": hashlib.sha256(new_key.encode()).hexdigest(),
            "authorization_approved": False, "payment_changed": False,
        }, datetime.now(timezone.utc))
        result["changed"] = True
    # Use exactly the download/integrity path used by the protected OPS viewer.
    downloaded = _download_verified_candidate_pdf(conn, case_id=fixture.CASE_ID,
        candidate_document_id=candidate_id, candidate_payload=chain["candidate"])
    if downloaded != content:
        raise RuntimeError("La descarga OPS no devuelve el PDF exacto")
    result["pdf_download_verified"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Preparado: reparar la ubicacion del candidato de", fixture.REFERENCE)
        print("Conserva el PDF y deja su revision humana pendiente. Usa --apply.")
        return
    launcher = runpy.run_path(str(ROOT.parent / "RTM_LOCAL_OPS_V2.py"))
    trial_url = URL.create("postgresql+psycopg", username="rtm_local_app", password="configuration_check_only",
                           host="127.0.0.1", port=5432, database="rtm_local")
    local_root = launcher["local_document_root"](ROOT.parent)
    dummy_keys = {name: "configuration_check_only" for name in
                  ("hmac_key", "operator_token", "public_case_secret", "authority_signing_secret")}
    assert_local_operator_auth_ready(launcher["local_environment"](
        trial_url.render_as_string(hide_password=False), dummy_keys, local_root))
    print("RTM | REPARAR UBICACION DEL PDF FICTICIO, SIN APROBARLO")
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
            result = repair_candidate(conn)
    finally:
        engine.dispose()
    # No passwords, keys, storage coordinates or operator assertions in this receipt.
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    report = ROOT.parent / "PRUEBAS_RTM" / (fixture.REFERENCE + "-candidate-repair.json")
    launcher["atomic_write"](report, json.dumps(result, indent=2).encode("utf-8"))
    print("REPARACION COMPLETADA" if result["changed"] else "EL PDF YA ESTABA REPARADO")
    print("Descarga e integridad comprobadas. Autorizacion pendiente de revision humana.")
    print("Vuelve a RTM, pulsa Recargar y abre el PDF exacto.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Operacion interrumpida.")
        raise SystemExit(130)
    except Exception as exc:
        print("No se ha completado la reparacion. Tipo:", type(exc).__name__)
        raise SystemExit(1)
