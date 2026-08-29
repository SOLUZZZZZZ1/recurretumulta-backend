#!/usr/bin/env python3
"""Siembra la proyeccion sintetica e inerte de RTM Presenter en staging.

El modo por defecto es un ``dry-run`` de solo lectura. ``--apply`` exige una
confirmacion literal y realiza exclusivamente INSERT idempotentes dentro de
una unica transaccion. La utilidad reutiliza el caso hash-only ya creado por
la fixture A1-S Runtime: no lee ni copia bytes, no resuelve claves de objetos,
no importa clientes B2 y no contacta ningun portal.

El perfil ``synthetic.example`` representa un portal ficticio reservado para
pruebas. No modela DGT, ayuntamientos ni ninguna administracion real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCRIPT_VERSION = "rtm_staging_presenter_synthetic_fixture_v1_1"
APPLY_CONFIRMATION = "STAGING_PRESENTER_SYNTHETIC_FIXTURE_ONLY"
DEFAULT_FIXTURE_KEY = "runtime-a94dcd3-v1"
PROFILE_CODE = "synthetic.example"
PROFILE_VERSION = 2
PROFILE_ORIGIN = "https://synthetic.example"
PRESENTER_MARKER = "RTM_PRESENTER_SYNTHETIC_ONLY"
A1S_MARKER = "RTM_A1S_SYNTHETIC_ONLY"
ASSIGNMENT_ROLE = "responsible"

_NAMESPACE = uuid.UUID("5ad68df0-1793-4b2b-a287-c51257cdff45")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PresenterFixtureError(RuntimeError):
    """La fixture no cumple la frontera sintetica cerrada."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audita o siembra fixtures Presenter sinteticas sobre A1-S staging."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--fixture-key", default=DEFAULT_FIXTURE_KEY)
    parser.add_argument("--compact", action="store_true")
    return parser


def safety_blockers(
    args: argparse.Namespace | None = None,
    values: Mapping[str, str] | None = None,
) -> list[str]:
    """Valida la frontera completa antes de importar o abrir la base."""

    from scripts.rtm_staging_presenter_schema import safety_blockers as schema_blockers

    blockers = list(schema_blockers(values=values))
    if args is not None:
        try:
            from rtm_connect.human_filing_runtime import build_runtime_fixture_plan

            build_runtime_fixture_plan(fixture_key=args.fixture_key)
        except Exception:
            blockers.append("invalid_a1s_fixture_key")
        if args.apply and args.confirmation != APPLY_CONFIRMATION:
            blockers.append("invalid_apply_confirmation")
    return blockers


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise PresenterFixtureError("fixture_metadata_must_be_json_object")


def destination_requirements() -> dict[str, Any]:
    """Contrato de un portal ficticio, con su orden humano explicito."""

    return {
        "contract_version": "rtm.presenter.destination.requirements.v1",
        "synthetic_only": True,
        "representation_modes": ["self"],
        "delivery": {
            "email": {
                "verified": True,
                "recipient": "reclamaciones@synthetic.example",
                "legal_entity_name": "Energía Comercializadora Sintética, S.A.",
                "entity_role": "comercializadora",
                "channel_label": "Correo electrónico oficial verificado",
                "channel_status": "accepted",
                "routing_scope_label": (
                    "Facturación, cobros, contrato, altas, bajas y cambio de compañía"
                ),
                "routing_warning": (
                    "Para cortes, averías, contador o lecturas debe buscarse la "
                    "distribuidora correspondiente"
                ),
                "official_source_label": "Área sintética de atención al cliente",
                "official_source_url": (
                    "https://synthetic.example/atencion/reclamaciones"
                ),
                "recommended_evidence_channel": "correo_certificado_o_burofax",
                "sensitive_attachment_policy": "cifrado_o_enlace_seguro",
                "template_code": "consumer_problem",
                "template_version": 1,
                "subject_template": (
                    "Reclamación contrato [referencia] – "
                    "Expediente RTM [expediente]"
                ),
                "body_template": (
                    "A la atención de [empresa]:\n\n"
                    "Se remite una reclamación completamente sintética relativa "
                    "al contrato [referencia]. La pretensión y los hechos deben "
                    "ser revisados por el operador antes de preparar el envío.\n\n"
                    "Expediente RTM: [expediente]."
                ),
                "matter_codes": [
                    "facturacion_incorrecta",
                    "cobro_indebido",
                    "incumplimiento_contractual",
                    "alta_baja_compania",
                    "cambio_compania",
                ],
            }
        },
        "fields": [
            {
                "step_order": 1,
                "field_code": "main_document",
                "required": True,
                "purposes": ["main_filing"],
                "media_types": ["text/plain"],
                "max_files": 1,
                "max_bytes": 1048576,
            },
            {
                "step_order": 2,
                "field_code": "submission_receipt",
                "required": False,
                "purposes": ["submission_receipt"],
                "media_types": ["application/json"],
                "max_files": 1,
                "max_bytes": 1048576,
            },
        ],
    }


def _profile_configuration() -> dict[str, Any]:
    return {
        "profile_code": PROFILE_CODE,
        "version_number": PROFILE_VERSION,
        "authority_code": PROFILE_CODE,
        "display_name": "Destino sintético de sede y correspondencia",
        "portal_origin": PROFILE_ORIGIN,
        "requirements": destination_requirements(),
    }


def _expected_runtime_plan(fixture_key: str) -> Any:
    from rtm_connect.human_filing_runtime import build_runtime_fixture_plan

    return build_runtime_fixture_plan(fixture_key=fixture_key)


def _expected_source_documents(runtime_plan: Any) -> tuple[dict[str, Any], ...]:
    return (
        {
            "id": runtime_plan.input_document_id,
            "kind": "rtm_connect_a1s_synthetic_input_fixture",
            "sha256": runtime_plan.input_document_sha256,
            "mime": "text/plain",
            "size_bytes": runtime_plan.input_document_size,
            "purpose": "main_filing",
            "original_filename": "synthetic_filing_input.txt",
        },
        {
            "id": runtime_plan.receipt_document_id,
            "kind": "rtm_connect_a1s_synthetic_receipt_fixture",
            "sha256": runtime_plan.receipt_document_sha256,
            "mime": "application/json",
            "size_bytes": runtime_plan.receipt_document_size,
            "purpose": "submission_receipt",
            "original_filename": "synthetic_submission_receipt.json",
        },
    )


def _validate_marker(
    metadata: Any,
    *,
    marker: str,
    fixture_key: str | None = None,
    test_mode: bool | None = None,
) -> None:
    payload = _json_object(metadata)
    if payload.get("synthetic_marker") != marker:
        raise PresenterFixtureError("synthetic_marker_mismatch")
    if payload.get("synthetic_only") is not True:
        raise PresenterFixtureError("synthetic_only_marker_missing")
    if fixture_key is not None and payload.get("fixture_key") != fixture_key:
        raise PresenterFixtureError("fixture_key_marker_mismatch")
    if test_mode is not None and payload.get("test_mode") is not test_mode:
        raise PresenterFixtureError("test_mode_marker_mismatch")


def validate_source_context(
    *,
    fixture_key: str,
    authority: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rechaza cualquier desviacion del caso A1-S sintetico exacto."""

    runtime_plan = _expected_runtime_plan(fixture_key)
    exact_ids = {
        "case_id": runtime_plan.case_id,
        "tenant_id": runtime_plan.tenant_id,
        "binding_id": runtime_plan.case_binding_id,
        "creator_membership_id": runtime_plan.requester_membership_id,
        "verifier_membership_id": runtime_plan.verifier_membership_id,
    }
    for key, expected in exact_ids.items():
        if str(authority.get(key) or "") != str(expected):
            raise PresenterFixtureError(f"a1s_{key}_mismatch")
    if authority.get("case_test_mode") is not True:
        raise PresenterFixtureError("presenter_case_must_be_test_mode")
    for key in (
        "tenant_synthetic_only",
        "binding_synthetic_only",
        "creator_membership_synthetic_only",
        "verifier_membership_synthetic_only",
        "creator_operator_synthetic",
        "verifier_operator_synthetic",
    ):
        if authority.get(key) is not True:
            raise PresenterFixtureError(f"{key}_required")
    for key in (
        "creator_operator_ready",
        "verifier_operator_ready",
    ):
        if authority.get(key) is not True:
            raise PresenterFixtureError(f"{key}_required")
    for key in (
        "tenant_status",
        "binding_status",
        "creator_membership_status",
        "verifier_membership_status",
        "creator_operator_status",
        "verifier_operator_status",
    ):
        if str(authority.get(key) or "") != "active":
            raise PresenterFixtureError(f"{key}_must_be_active")
    if authority.get("binding_revoked_at") is not None:
        raise PresenterFixtureError("a1s_binding_must_not_be_revoked")
    if authority.get("creator_membership_revoked_at") is not None:
        raise PresenterFixtureError("a1s_creator_membership_must_not_be_revoked")
    if authority.get("verifier_membership_revoked_at") is not None:
        raise PresenterFixtureError("a1s_verifier_membership_must_not_be_revoked")
    if str(authority.get("creator_membership_role") or "") != "supervisor":
        raise PresenterFixtureError("a1s_creator_role_mismatch")
    if str(authority.get("verifier_membership_role") or "") != "verifier":
        raise PresenterFixtureError("a1s_verifier_role_mismatch")
    if str(authority.get("creator_operator_environment") or "") != "staging":
        raise PresenterFixtureError("creator_operator_not_staging")
    if str(authority.get("verifier_operator_environment") or "") != "staging":
        raise PresenterFixtureError("verifier_operator_not_staging")
    creator_id = str(authority.get("creator_operator_id") or "")
    verifier_id = str(authority.get("verifier_operator_id") or "")
    try:
        creator_id = str(uuid.UUID(creator_id))
        verifier_id = str(uuid.UUID(verifier_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PresenterFixtureError("synthetic_operator_id_invalid") from exc
    if creator_id == verifier_id:
        raise PresenterFixtureError("profile_requires_distinct_creator_verifier")

    _validate_marker(
        authority.get("tenant_metadata"),
        marker=A1S_MARKER,
        fixture_key=runtime_plan.fixture_key,
    )
    _validate_marker(
        authority.get("binding_metadata"),
        marker=A1S_MARKER,
        fixture_key=runtime_plan.fixture_key,
        test_mode=True,
    )
    _validate_marker(
        authority.get("creator_membership_metadata"),
        marker=A1S_MARKER,
        fixture_key=runtime_plan.fixture_key,
    )
    _validate_marker(
        authority.get("verifier_membership_metadata"),
        marker=A1S_MARKER,
        fixture_key=runtime_plan.fixture_key,
    )

    expected_documents = _expected_source_documents(runtime_plan)
    if len(documents) != len(expected_documents):
        raise PresenterFixtureError("case_contains_non_fixture_documents")
    by_id = {str(row.get("id") or ""): row for row in documents}
    if len(by_id) != len(documents):
        raise PresenterFixtureError("duplicate_source_document_identity")
    for expected in expected_documents:
        row = by_id.get(str(expected["id"]))
        if row is None:
            raise PresenterFixtureError("synthetic_source_document_missing")
        for key in ("kind", "sha256", "mime"):
            if str(row.get(key) or "") != str(expected[key]):
                raise PresenterFixtureError(
                    f"synthetic_source_document_{key}_mismatch"
                )
        if int(row.get("size_bytes") or 0) != int(expected["size_bytes"]):
            raise PresenterFixtureError("synthetic_source_document_size_mismatch")
        if not _SHA256_RE.fullmatch(str(row.get("sha256") or "")):
            raise PresenterFixtureError("synthetic_source_document_hash_invalid")
        if row.get("b2_bucket") is not None or row.get("b2_key") is not None:
            raise PresenterFixtureError("synthetic_source_document_has_storage_ref")

    return {
        "runtime_plan": runtime_plan,
        "case_id": str(runtime_plan.case_id),
        "tenant_id": str(runtime_plan.tenant_id),
        "creator_operator_id": creator_id,
        "verifier_operator_id": verifier_id,
        "documents": expected_documents,
    }


def load_source_context(conn: Any, *, fixture_key: str) -> dict[str, Any]:
    """Carga solo identidad/metadata; nunca selecciona contenido documental."""

    from sqlalchemy import text

    runtime_plan = _expected_runtime_plan(fixture_key)
    authority = conn.execute(
        text(
            """
            SELECT c.id AS case_id, COALESCE(c.test_mode,FALSE) AS case_test_mode,
                   t.id AS tenant_id, t.status AS tenant_status,
                   t.synthetic_only AS tenant_synthetic_only,
                   t.metadata AS tenant_metadata,
                   b.id AS binding_id, b.status AS binding_status,
                   b.synthetic_only AS binding_synthetic_only,
                   b.revoked_at AS binding_revoked_at,
                   b.metadata AS binding_metadata,
                   cm.id AS creator_membership_id,
                   cm.role AS creator_membership_role,
                   cm.status AS creator_membership_status,
                   cm.synthetic_only AS creator_membership_synthetic_only,
                   cm.revoked_at AS creator_membership_revoked_at,
                   cm.metadata AS creator_membership_metadata,
                   vm.id AS verifier_membership_id,
                   vm.role AS verifier_membership_role,
                   vm.status AS verifier_membership_status,
                   vm.synthetic_only AS verifier_membership_synthetic_only,
                   vm.revoked_at AS verifier_membership_revoked_at,
                   vm.metadata AS verifier_membership_metadata,
                   co.id AS creator_operator_id,
                   co.status AS creator_operator_status,
                   COALESCE((co.profile->>'synthetic')::boolean,FALSE)
                     AS creator_operator_synthetic,
                   COALESCE(co.profile->>'environment','')
                     AS creator_operator_environment,
                   (co.must_change_password=FALSE
                     AND co.mfa_required=FALSE
                     AND (co.locked_until IS NULL OR co.locked_until<=NOW()))
                     AS creator_operator_ready,
                   vo.id AS verifier_operator_id,
                   vo.status AS verifier_operator_status,
                   COALESCE((vo.profile->>'synthetic')::boolean,FALSE)
                     AS verifier_operator_synthetic,
                   COALESCE(vo.profile->>'environment','')
                     AS verifier_operator_environment,
                   (vo.must_change_password=FALSE
                     AND vo.mfa_required=FALSE
                     AND (vo.locked_until IS NULL OR vo.locked_until<=NOW()))
                     AS verifier_operator_ready
            FROM cases c
            JOIN rtm_connect_a1s_case_bindings b ON b.case_id=c.id
            JOIN rtm_connect_a1s_tenants t ON t.id=b.tenant_id
            JOIN rtm_connect_a1s_memberships cm
              ON cm.id=CAST(:creator_membership_id AS UUID)
             AND cm.tenant_id=t.id
            JOIN rtm_connect_a1s_memberships vm
              ON vm.id=CAST(:verifier_membership_id AS UUID)
             AND vm.tenant_id=t.id
            JOIN rtm_operators co ON co.id=cm.operator_id
            JOIN rtm_operators vo ON vo.id=vm.operator_id
            WHERE c.id=CAST(:case_id AS UUID)
              AND t.id=CAST(:tenant_id AS UUID)
              AND b.id=CAST(:binding_id AS UUID)
              AND b.binding_code=:binding_code
              AND b.case_snapshot_sha256=:case_snapshot_sha256
            LIMIT 2
            """
        ),
        {
            "case_id": runtime_plan.case_id,
            "tenant_id": runtime_plan.tenant_id,
            "binding_id": runtime_plan.case_binding_id,
            "binding_code": runtime_plan.case_binding_code,
            "case_snapshot_sha256": runtime_plan.case_snapshot_sha256,
            "creator_membership_id": runtime_plan.requester_membership_id,
            "verifier_membership_id": runtime_plan.verifier_membership_id,
        },
    ).mappings().all()
    if len(authority) != 1:
        raise PresenterFixtureError("a1s_synthetic_case_authority_not_unique")
    documents = conn.execute(
        text(
            """
            SELECT id, kind, sha256, mime, size_bytes, b2_bucket, b2_key
            FROM documents
            WHERE case_id=CAST(:case_id AS UUID)
            ORDER BY id
            """
        ),
        {"case_id": runtime_plan.case_id},
    ).mappings().all()
    return validate_source_context(
        fixture_key=runtime_plan.fixture_key,
        authority=authority[0],
        documents=documents,
    )


def build_seed_plan(source: Mapping[str, Any]) -> dict[str, Any]:
    """Construye solo filas de referencia; no incluye una clave ``bytes``."""

    runtime_plan = source["runtime_plan"]
    fixture_key = str(runtime_plan.fixture_key)
    document_rows: list[dict[str, Any]] = []
    for item in source["documents"]:
        source_id = str(item["id"])
        document_rows.append(
            {
                "id": _stable_uuid(f"{fixture_key}:document-version:{source_id}:v1"),
                "case_id": str(source["case_id"]),
                "logical_document_id": _stable_uuid(
                    f"{fixture_key}:logical-document:{source_id}"
                ),
                "version_number": 1,
                "supersedes_version_id": None,
                "source_document_id": source_id,
                "sha256": str(item["sha256"]),
                "purpose": str(item["purpose"]),
                "state": "active",
                "scan_status": "clean",
                "original_filename": str(item["original_filename"]),
                "detected_mime": str(item["mime"]),
                "size_bytes": int(item["size_bytes"]),
                "source_kind": "legacy_backfill",
                "created_by_operator_id": str(source["creator_operator_id"]),
                "metadata": {
                    "synthetic_marker": PRESENTER_MARKER,
                    "synthetic_only": True,
                    "source_synthetic_marker": A1S_MARKER,
                    "fixture_key": fixture_key,
                    "projection": "source_document_reference_only",
                    "bytes_copied": False,
                    "scan_evidence": "synthetic_fixture_contract_only",
                    "network_used": False,
                    "b2_used": False,
                    "real_data_used": False,
                },
            }
        )

    configuration = _profile_configuration()
    profile = {
        **configuration,
        "id": _stable_uuid(
            f"destination-profile:{PROFILE_CODE}:v{PROFILE_VERSION}"
        ),
        "status": "active",
        "profile_sha256": _canonical_sha256(configuration),
        "created_by_operator_id": str(source["creator_operator_id"]),
        "verified_by_operator_id": str(source["verifier_operator_id"]),
        "metadata": {
            "synthetic_marker": PRESENTER_MARKER,
            "synthetic_only": True,
            "fixture_key": fixture_key,
            "reserved_example_domain": True,
            "network_used": False,
            "b2_used": False,
            "real_data_used": False,
            "external_effects_executed": False,
        },
    }
    if profile["created_by_operator_id"] == profile["verified_by_operator_id"]:
        raise PresenterFixtureError("profile_requires_distinct_creator_verifier")
    assignment = {
        "id": _stable_uuid(f"{fixture_key}:presenter-work-assignment:v1"),
        "case_id": str(source["case_id"]),
        "operator_id": str(source["creator_operator_id"]),
        "assignment_role": ASSIGNMENT_ROLE,
        "status": "active",
        "assigned_by": str(source["creator_operator_id"]),
        "metadata": {
            "synthetic_marker": PRESENTER_MARKER,
            "source_synthetic_marker": A1S_MARKER,
            "synthetic_only": True,
            "fixture_key": fixture_key,
            "accepted_for": "rtm_presenter_synthetic_demo",
            "real_data_used": False,
        },
    }
    return {
        "fixture_key": fixture_key,
        "case_id": str(source["case_id"]),
        "document_versions": tuple(document_rows),
        "destination_profile": profile,
        "work_assignment": assignment,
    }


_DOCUMENT_COMPARE_KEYS = (
    "id",
    "case_id",
    "logical_document_id",
    "version_number",
    "supersedes_version_id",
    "source_document_id",
    "sha256",
    "purpose",
    "state",
    "scan_status",
    "original_filename",
    "detected_mime",
    "size_bytes",
    "source_kind",
    "created_by_operator_id",
)
_PROFILE_COMPARE_KEYS = (
    "id",
    "profile_code",
    "version_number",
    "status",
    "authority_code",
    "display_name",
    "portal_origin",
    "profile_sha256",
    "created_by_operator_id",
    "verified_by_operator_id",
)
_ASSIGNMENT_COMPARE_KEYS = (
    "id",
    "case_id",
    "operator_id",
    "assignment_role",
    "status",
    "assigned_by",
)


def _same_scalar(actual: Any, expected: Any) -> bool:
    if isinstance(expected, int):
        try:
            return int(actual) == expected
        except (TypeError, ValueError):
            return False
    if expected is None:
        return actual is None
    return str(actual) == str(expected)


def reconcile_fixture_state(
    *,
    plan: Mapping[str, Any],
    document_rows: Sequence[Mapping[str, Any]],
    profile_rows: Sequence[Mapping[str, Any]],
    assignment_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Distingue ausencia idempotente de una colision append-only."""

    expected_documents = {
        str(row["source_document_id"]): row
        for row in plan["document_versions"]
    }
    actual_documents = {
        str(row.get("source_document_id") or ""): row for row in document_rows
    }
    if len(actual_documents) != len(document_rows):
        raise PresenterFixtureError("presenter_document_projection_not_unique")
    unexpected = sorted(set(actual_documents) - set(expected_documents))
    if unexpected:
        raise PresenterFixtureError("presenter_case_projection_collision")
    missing_document_ids: list[str] = []
    for source_id, expected in expected_documents.items():
        actual = actual_documents.get(source_id)
        if actual is None:
            missing_document_ids.append(source_id)
            continue
        if any(
            not _same_scalar(actual.get(key), expected.get(key))
            for key in _DOCUMENT_COMPARE_KEYS
        ):
            raise PresenterFixtureError("presenter_document_projection_collision")
        if _json_object(actual.get("metadata")) != expected["metadata"]:
            raise PresenterFixtureError("presenter_document_metadata_collision")

    if len(profile_rows) > 1:
        raise PresenterFixtureError("synthetic_example_profile_not_unique")
    expected_profile = plan["destination_profile"]
    profile_missing = not profile_rows
    if profile_rows:
        actual_profile = profile_rows[0]
        if any(
            not _same_scalar(actual_profile.get(key), expected_profile.get(key))
            for key in _PROFILE_COMPARE_KEYS
        ):
            raise PresenterFixtureError("synthetic_example_profile_collision")
        if _json_object(actual_profile.get("requirements")) != expected_profile[
            "requirements"
        ]:
            raise PresenterFixtureError("synthetic_example_requirements_collision")
        if _json_object(actual_profile.get("metadata")) != expected_profile[
            "metadata"
        ]:
            raise PresenterFixtureError("synthetic_example_metadata_collision")

    if len(assignment_rows) > 1:
        raise PresenterFixtureError("presenter_work_assignment_not_unique")
    expected_assignment = plan["work_assignment"]
    assignment_missing = not assignment_rows
    if assignment_rows:
        actual_assignment = assignment_rows[0]
        if any(
            not _same_scalar(
                actual_assignment.get(key), expected_assignment.get(key)
            )
            for key in _ASSIGNMENT_COMPARE_KEYS
        ):
            raise PresenterFixtureError("presenter_work_assignment_collision")
        if actual_assignment.get("attention_item_id") is not None:
            raise PresenterFixtureError("presenter_assignment_must_be_case_scoped")
        if actual_assignment.get("accepted_at") is None:
            raise PresenterFixtureError("presenter_assignment_must_be_accepted")
        if actual_assignment.get("released_at") is not None:
            raise PresenterFixtureError("presenter_assignment_must_be_active")
        if _json_object(actual_assignment.get("metadata")) != expected_assignment[
            "metadata"
        ]:
            raise PresenterFixtureError("presenter_assignment_metadata_collision")

    return {
        "ready": (
            not missing_document_ids
            and not profile_missing
            and not assignment_missing
        ),
        "missing_source_document_ids": sorted(missing_document_ids),
        "profile_missing": profile_missing,
        "assignment_missing": assignment_missing,
        "would_insert_document_versions": len(missing_document_ids),
        "would_insert_destination_profiles": int(profile_missing),
        "would_insert_work_assignments": int(assignment_missing),
    }


def load_fixture_state(conn: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    from sqlalchemy import bindparam, text

    source_ids = [
        str(row["source_document_id"]) for row in plan["document_versions"]
    ]
    statement = text(
        """
        SELECT id, case_id, logical_document_id, version_number,
               supersedes_version_id, source_document_id, sha256, purpose,
               state, scan_status, original_filename, detected_mime,
               size_bytes, source_kind, created_by_operator_id, metadata
        FROM rtm_presenter_document_versions
        WHERE case_id=CAST(:case_id AS UUID)
          AND source_document_id IN :source_ids
        ORDER BY source_document_id
        """
    ).bindparams(bindparam("source_ids", expanding=True))
    document_rows = conn.execute(
        statement,
        {"case_id": plan["case_id"], "source_ids": source_ids},
    ).mappings().all()
    profile_rows = conn.execute(
        text(
            """
            SELECT id, profile_code, version_number, status, authority_code,
                   display_name, portal_origin, requirements, profile_sha256,
                   created_by_operator_id, verified_by_operator_id, metadata
            FROM rtm_presenter_destination_profiles
            WHERE profile_code=:profile_code
            ORDER BY version_number
            """
        ),
        {"profile_code": PROFILE_CODE},
    ).mappings().all()
    assignment_rows = conn.execute(
        text(
            """
            SELECT id, case_id, attention_item_id, operator_id,
                   assignment_role, status, assigned_by, accepted_at,
                   released_at, metadata
            FROM rtm_work_assignments
            WHERE case_id=CAST(:case_id AS UUID)
              AND attention_item_id IS NULL
              AND assignment_role=:assignment_role
              AND status='active'
            ORDER BY assigned_at
            """
        ),
        {
            "case_id": plan["case_id"],
            "assignment_role": ASSIGNMENT_ROLE,
        },
    ).mappings().all()
    return reconcile_fixture_state(
        plan=plan,
        document_rows=document_rows,
        profile_rows=profile_rows,
        assignment_rows=assignment_rows,
    )


def assert_work_assignment_schema(conn: Any) -> None:
    """Presenter exige asignacion real; membership A1-S sola no basta."""

    from sqlalchemy import text

    required = {
        "id",
        "case_id",
        "attention_item_id",
        "operator_id",
        "assignment_role",
        "status",
        "assigned_by",
        "assigned_at",
        "accepted_at",
        "released_at",
        "metadata",
        "created_at",
        "updated_at",
    }
    columns = {
        str(row["column_name"])
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' "
                "AND table_name='rtm_work_assignments'"
            )
        ).mappings().all()
    }
    if not required.issubset(columns):
        raise PresenterFixtureError(
            "presenter_work_assignment_schema_not_ready"
        )
    indexes = {
        str(row["indexname"])
        for row in conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' "
                "AND tablename='rtm_work_assignments'"
            )
        ).mappings().all()
    }
    if "uq_rtm_assignment_case_role" not in indexes:
        raise PresenterFixtureError(
            "presenter_work_assignment_uniqueness_not_ready"
        )


def insert_fixture(conn: Any, *, plan: Mapping[str, Any]) -> int:
    """Inserta solo filas ausentes; el llamador posee la transaccion."""

    from sqlalchemy import text

    before = load_fixture_state(conn, plan=plan)
    inserted = 0
    missing = set(before["missing_source_document_ids"])
    document_sql = text(
        """
        INSERT INTO rtm_presenter_document_versions(
            id, case_id, logical_document_id, version_number,
            supersedes_version_id, source_document_id, sha256, purpose,
            state, scan_status, original_filename, detected_mime, size_bytes,
            source_kind, created_by_operator_id, metadata
        ) VALUES (
            CAST(:id AS UUID), CAST(:case_id AS UUID),
            CAST(:logical_document_id AS UUID), :version_number,
            CAST(:supersedes_version_id AS UUID),
            CAST(:source_document_id AS UUID), :sha256, :purpose, :state,
            :scan_status, :original_filename, :detected_mime, :size_bytes,
            :source_kind, CAST(:created_by_operator_id AS UUID),
            CAST(:metadata AS JSONB)
        ) ON CONFLICT DO NOTHING
        """
    )
    for row in plan["document_versions"]:
        if str(row["source_document_id"]) not in missing:
            continue
        values = dict(row)
        values["metadata"] = _canonical_json(values["metadata"])
        result = conn.execute(document_sql, values)
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if before["profile_missing"]:
        profile = dict(plan["destination_profile"])
        profile["requirements"] = _canonical_json(profile["requirements"])
        profile["metadata"] = _canonical_json(profile["metadata"])
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_destination_profiles(
                    id, profile_code, version_number, status, authority_code,
                    display_name, portal_origin, requirements, profile_sha256,
                    created_by_operator_id, verified_by_operator_id,
                    verified_at, created_at, metadata
                ) VALUES (
                    CAST(:id AS UUID), :profile_code, :version_number, :status,
                    :authority_code, :display_name, :portal_origin,
                    CAST(:requirements AS JSONB), :profile_sha256,
                    CAST(:created_by_operator_id AS UUID),
                    CAST(:verified_by_operator_id AS UUID), NOW(), NOW(),
                    CAST(:metadata AS JSONB)
                ) ON CONFLICT DO NOTHING
                """
            ),
            profile,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    if before["assignment_missing"]:
        assignment = dict(plan["work_assignment"])
        assignment["metadata"] = _canonical_json(assignment["metadata"])
        result = conn.execute(
            text(
                """
                INSERT INTO rtm_work_assignments(
                    id, case_id, attention_item_id, operator_id,
                    assignment_role, status, assigned_by, assigned_at,
                    accepted_at, metadata, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID), NULL,
                    CAST(:operator_id AS UUID), :assignment_role, :status,
                    CAST(:assigned_by AS UUID), NOW(), NOW(),
                    CAST(:metadata AS JSONB), NOW(), NOW()
                ) ON CONFLICT DO NOTHING
                """
            ),
            assignment,
        )
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))

    after = load_fixture_state(conn, plan=plan)
    if not after["ready"]:
        raise PresenterFixtureError("presenter_fixture_not_ready_after_insert")
    return inserted


def audit_fixture(conn: Any, *, fixture_key: str) -> dict[str, Any]:
    source = load_source_context(conn, fixture_key=fixture_key)
    plan = build_seed_plan(source)
    state = load_fixture_state(conn, plan=plan)
    return {
        "ready": bool(state["ready"]),
        "ready_to_apply": True,
        "fixture_key": plan["fixture_key"],
        "case_id": plan["case_id"],
        "source_document_ids": [
            row["source_document_id"] for row in plan["document_versions"]
        ],
        "document_version_ids": [
            row["id"] for row in plan["document_versions"]
        ],
        "destination_profile": {
            "id": plan["destination_profile"]["id"],
            "profile_code": PROFILE_CODE,
            "version_number": PROFILE_VERSION,
            "portal_origin": PROFILE_ORIGIN,
            "profile_sha256": plan["destination_profile"]["profile_sha256"],
            "field_step_order": [
                field["step_order"]
                for field in plan["destination_profile"]["requirements"]["fields"]
            ],
            "distinct_creator_verifier": (
                plan["destination_profile"]["created_by_operator_id"]
                != plan["destination_profile"]["verified_by_operator_id"]
            ),
        },
        "would_insert_document_versions": state[
            "would_insert_document_versions"
        ],
        "would_insert_destination_profiles": state[
            "would_insert_destination_profiles"
        ],
        "would_insert_work_assignments": state[
            "would_insert_work_assignments"
        ],
        "active_accepted_work_assignment": not state["assignment_missing"],
        "bytes_read": False,
        "bytes_copied": False,
        "b2_used": False,
        "network_used": False,
        "real_data_used": False,
        "external_effects_executed": False,
    }


def _print(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            default=str,
        )
    )


def _base_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_presenter_synthetic_fixture",
        "version": SCRIPT_VERSION,
        "environment": str(os.getenv("RTM_ENV") or "").strip().lower()
        or "unset",
        "fixture_key": args.fixture_key,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "read_only": not bool(args.apply),
        "transactional": True,
        "idempotent_insert_only": True,
        "synthetic_only": True,
        "profile_code": PROFILE_CODE,
        "reserved_example_domain": True,
        "cases_created": False,
        "documents_created": False,
        "operators_created": False,
        "source_bytes_read": False,
        "source_bytes_copied": False,
        "b2_used": False,
        "network_used": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "production_authorized": False,
        "database_configuration_loaded": False,
        "database_connection_used": False,
        "database_identity_verified": False,
        "database_mutated": False,
        "transaction_committed": False,
        "blockers": [],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _base_report(args)
    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        _print(report, compact=args.compact)
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine
        from scripts.rtm_staging_presenter_schema import (
            _database_identity_from_url,
            assert_database_identity,
            schema_snapshot,
        )

        expected_database, expected_role = _database_identity_from_url(os.environ)
        report["database_configuration_loaded"] = True
        engine = get_engine()

        if args.apply:
            with engine.begin() as conn:
                report["database_connection_used"] = True
                report["connected_database"] = assert_database_identity(
                    conn,
                    expected_database_name=expected_database,
                    expected_database_role=expected_role,
                )
                report["database_identity_verified"] = True
                snapshot = schema_snapshot(conn)
                report["presenter_schema_ready"] = bool(snapshot["ready"])
                if not snapshot["ready"]:
                    raise PresenterFixtureError("presenter_schema_not_ready")
                assert_work_assignment_schema(conn)
                before = audit_fixture(conn, fixture_key=args.fixture_key)
                report["before"] = before
                source = load_source_context(conn, fixture_key=args.fixture_key)
                plan = build_seed_plan(source)
                inserted = insert_fixture(conn, plan=plan)
                report["inserted_rows"] = inserted
                report["database_mutated"] = inserted > 0
                inside = audit_fixture(conn, fixture_key=args.fixture_key)
                if not inside["ready"]:
                    raise PresenterFixtureError(
                        "presenter_fixture_not_ready_before_commit"
                    )
            report["transaction_committed"] = True
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    assert_database_identity(
                        conn,
                        expected_database_name=expected_database,
                        expected_database_role=expected_role,
                    )
                    audit = audit_fixture(conn, fixture_key=args.fixture_key)
                finally:
                    transaction.rollback()
        else:
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    report["database_connection_used"] = True
                    report["connected_database"] = assert_database_identity(
                        conn,
                        expected_database_name=expected_database,
                        expected_database_role=expected_role,
                    )
                    report["database_identity_verified"] = True
                    snapshot = schema_snapshot(conn)
                    report["presenter_schema_ready"] = bool(snapshot["ready"])
                    if not snapshot["ready"]:
                        raise PresenterFixtureError("presenter_schema_not_ready")
                    assert_work_assignment_schema(conn)
                    audit = audit_fixture(conn, fixture_key=args.fixture_key)
                finally:
                    transaction.rollback()

        report["audit"] = audit
        report["fixture_ready"] = bool(audit["ready"])
        report["ready_to_apply"] = bool(audit["ready_to_apply"])
        report["safe"] = True
        report["ok"] = True
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["blockers"].append("presenter_synthetic_fixture_operation_failed")
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
