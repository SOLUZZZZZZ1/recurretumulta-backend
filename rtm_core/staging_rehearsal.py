"""Explicit opt-in entry for one immutable synthetic radar scenario.

Only an individual OPS supervisor may use the adapter. Fields and file bytes
are checked before ordinary intake runs. A UUID per operator/scenario makes
intake retries recover the same case. No public document policy is relaxed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import text

from database import get_engine
from rtm_core.environment_contract import build_environment_preflight
from rtm_core.ops_case_scope import load_ops_case_scope

VERSION = "rtm_staging_radar_20261005_v1"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "staging_radar_v1"
RADAR_SHA = "4c692028bcab145f9f96169c563b0dab7c899b3e83fd0fa6e16d1e7ab22c1e24"
PRIVATE_HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache",
                   "Vary": "Authorization, Cookie", "X-Content-Type-Options": "nosniff"}
PROFILE = {
    "full_name": "PERSONA DE PRUEBA RTM", "dni_nie": "RTMTEST001",
    "email": "ensayo.radar@example.com", "telefono": "000000000",
    "street": "CALLE FICTICIA RTM", "street_number": "1", "floor": "", "door": "",
    "postal_code": "00000", "city": "CIUDAD FICTICIA", "province": "PROVINCIA FICTICIA",
    "preferred_contact": "email",
    "customer_comment": "Ensayo ficticio RTM. Peticion de identificacion del conductor por radar. No consta importe ni fecha de recepcion; el margen de error no esta verificado.",
}
FORM_FIELDS = {**PROFILE, "department": "traffic", "case_type": "fine",
    "source_module": "rtm_web", "public_service_family": "trafico",
    "domicilio_notif": "CALLE FICTICIA RTM, 1 · 00000 CIUDAD FICTICIA (PROVINCIA FICTICIA)",
    "customer_comment": "Área pública seleccionada: Tráfico\n\n" + PROFILE["customer_comment"],
    "representation_confirmed": "true", "prejudicial_counsel_requested": "false",
    "privacy_accepted": "true"}


def require_profile() -> None:
    if os.getenv("RTM_ENV") != "staging" or os.getenv("RTM_ENABLE_STAGING_REHEARSAL") != "1":
        raise HTTPException(404, "Ensayo no disponible en este entorno")
    required = {"RTM_DOCUMENT_INPUT_POLICY": "synthetic_only", "RTM_STRIPE_MODE": "test",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0", "RTM_ALLOW_REAL_PAYMENTS": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0", "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0"}
    report = build_environment_preflight()
    if any(os.getenv(key) != value for key, value in required.items()) or not report.safe or not report.capabilities.get("b2"):
        raise HTTPException(503, "La configuracion no permite este ensayo aislado")


@dataclass(frozen=True)
class RehearsalGrant:
    operator_id: str

    @property
    def case_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{VERSION}/{self.operator_id}"))

    @property
    def marker(self) -> dict:
        return {"version": VERSION, "operator_id": self.operator_id, "radar_sha256": RADAR_SHA}


def require_supervisor(request: Request) -> RehearsalGrant:
    require_profile()
    scope = load_ops_case_scope(request)
    if not (scope.individual_session and scope.role_code == "rtm.supervisor"
            and "ops.supervise" in scope.permissions and "ops.view" in scope.permissions):
        raise HTTPException(403, "El ensayo requiere una sesion individual de supervisor")
    return RehearsalGrant(str(uuid.UUID(scope.operator_id)))


def trusted_intake_grant(request: Request | None) -> RehearsalGrant | None:
    grant = getattr(getattr(request, "state", None), "rtm_rehearsal_grant", None)
    if grant is None:
        return None
    if not isinstance(grant, RehearsalGrant) or require_supervisor(request) != grant:
        raise HTTPException(403, "Entrada de ensayo no verificable")
    return grant


def fixture(kind: str) -> tuple[str, bytes]:
    if kind not in {"identity_front", "identity_back", "radar"}:
        raise HTTPException(404, "Documento de ensayo no encontrado")
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest[kind]
    path = FIXTURE_ROOT / entry["filename"]
    if path.parent != FIXTURE_ROOT or path.suffix != ".pdf":
        raise HTTPException(503, "Manifiesto de ensayo no verificable")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != entry["size_bytes"] or digest != entry["sha256"] or (kind == "radar" and digest != RADAR_SHA):
        raise HTTPException(503, "Documento de ensayo no verificable")
    return entry["filename"], content


def verify_case_row(row, grant: RehearsalGrant) -> None:
    interested = row["interested_data"]
    expected = {key: FORM_FIELDS[key] for key in ("full_name", "dni_nie", "domicilio_notif", "email", "telefono", "customer_comment")}
    if (str(row["id"]) != grant.case_id or row["test_mode"] is not True
        or row["department"] != "traffic" or row["case_type"] != "fine"
        or not isinstance(interested, dict) or interested.get("staging_rehearsal") != grant.marker
        or any(interested.get(key) != value for key, value in expected.items())
        or row["contact_email"] != PROFILE["email"] or row["contact_name"] != PROFILE["full_name"]):
        raise HTTPException(409, "El expediente no coincide con el ensayo; no se modificara")


def existing_case(grant: RehearsalGrant, conn=None) -> bool:
    if conn is None:
        with get_engine().connect() as connection:
            return existing_case(grant, connection)
    row = conn.execute(text("SELECT id, test_mode, department, case_type, interested_data, contact_email, contact_name FROM cases WHERE id=:id"), {"id": grant.case_id}).mappings().first()
    if row is None:
        return False
    verify_case_row(row, grant)
    return True


class RehearsalAlreadyCreated(Exception):
    """Raised only after the committed row's identity and marker are checked."""


def lock_new_intake(conn, grant: RehearsalGrant) -> None:
    # Serialise the short SQL commit only; uploads remain outside the lock.
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                 {"key": int.from_bytes(hashlib.sha256(grant.case_id.encode()).digest()[:8], "big", signed=True)})
    if existing_case(grant, conn):
        raise RehearsalAlreadyCreated()


def recovered_intake(grant: RehearsalGrant) -> dict:
    from public_case_access import issue_case_access_token
    if not existing_case(grant):
        raise HTTPException(409, "El expediente de ensayo no esta registrado")
    # Intake identity only: never assert a current payment or approval.
    return {"ok": True, "case_id": grant.case_id, "case_access_token": issue_case_access_token(grant.case_id),
            "case_access_token_header": "X-RTM-Case-Token", "test_mode": True,
            "recovered": True, "next_path": "/multas"}


def recover_authority_if_issued(conn, case_id: str):
    from case_authority import verify_active_case_authority, verify_active_authority_document_issue
    from cases import _document_projection
    authorized = conn.execute(text("SELECT authorized FROM cases WHERE id=:id"), {"id": case_id}).scalar_one()
    if not authorized:
        return None
    authority = verify_active_case_authority(conn, case_id)
    issuance = verify_active_authority_document_issue(conn, case_id, authority=authority)
    material = issuance["material"]
    return {"authority_payload": authority, "authority_material": authority["material"],
        "auth_doc": {"issuance": issuance, "document": _document_projection(
            material["document_id"], material["document_sha256"], material["mime"], material["size_bytes"])}}
