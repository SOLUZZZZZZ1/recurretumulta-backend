"""One explicitly configured new client rehearsal; no OPS privilege in its pass.

The pass is issued by the existing supervisor. Intake still uses the ordinary
transaction, upload checks and authority/payment gates. Changing the run or
scope revokes this entry. No route creates a paid or approved fixture.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import uuid

from fastapi import HTTPException, Request
from public_case_access import require_case_access_token
from rtm_core.casework_policy import load_policy, CaseworkConfigurationError
from rtm_core.staging_rehearsal import RehearsalGrant, FORM_FIELDS, require_profile

VERSION = "rtm_client_rehearsal_20261106_v1"
PREFIX = "/staging/client"
PASS_HEADER = "X-RTM-Client-Pass"
FIXTURES = Path(__file__).parent / "fixtures" / "staging_client_v1"
COMMENT = "Ensayo ficticio nuevo. Solicito el estudio de la notificación RTM-PRUEBA-2026-1106. Adjunto el documento completo para revisión."


@dataclass(frozen=True)
class ClientRehearsalGrant(RehearsalGrant):
    run_id: str
    recipient: str

    @property
    def case_id(self):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{VERSION}/{self.operator_id}/{self.run_id}"))

    @property
    def form_fields(self):
        return {**FORM_FIELDS, "email": self.recipient, "customer_comment": COMMENT}

    @property
    def marker(self):
        return {"version": VERSION, "operator_id": self.operator_id, "run_id": self.run_id,
                "original_sha256": hashlib.sha256(fixture("original")[1]).hexdigest()}


def configured_grant():
    require_profile()
    if (os.getenv("RTM_ENABLE_STAGING_CLIENT_INTAKE") != "1"
            or os.getenv("RTM_PROVIDER_TEST_TARGET") != "existing_staging"):
        raise HTTPException(404, "Entrada de ensayo no habilitada")
    try:
        policy = load_policy(os.environ)
        run_id = str(uuid.UUID(os.environ.get("RTM_STAGING_CLIENT_RUN_ID", "")))
        # This is the owner's already approved controlled mailbox, not a public email input.
        recipient = os.environ.get("RTM_CASE_MAIL_ALLOWED_RECIPIENTS", "").strip().lower()
        if policy is None or len(policy.operator_ids) != 1 or recipient != "soluzziona@gmail.com":
            raise ValueError()
        grant = ClientRehearsalGrant(policy.operator_ids[0], run_id, recipient)
        if policy.case_ids != (grant.case_id,):
            raise ValueError()
        return grant
    except (ValueError, CaseworkConfigurationError):
        raise HTTPException(503, "El ensayo nuevo requiere su ámbito y caducidad configurados") from None


def require_client_pass(request: Request):
    # A state object alone is not authority, nor can a pass be used on OPS paths.
    if not request.url.path.startswith(PREFIX + "/"):
        raise HTTPException(403, "Acceso de cliente fuera de su recorrido")
    grant = configured_grant()
    require_case_access_token(grant.case_id, request.headers.get(PASS_HEADER))
    return grant


def fixture(kind):
    if kind not in {"identity_front", "identity_back", "original"}:
        raise HTTPException(404, "Documento de ensayo no encontrado")
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest[kind]
    path = FIXTURES / entry["filename"]
    if path.parent != FIXTURES or path.suffix != ".pdf":
        raise HTTPException(503, "Documento de ensayo no verificable")
    content = path.read_bytes()
    if len(content) != entry["size_bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
        raise HTTPException(503, "Documento de ensayo no verificable")
    return entry["filename"], content
