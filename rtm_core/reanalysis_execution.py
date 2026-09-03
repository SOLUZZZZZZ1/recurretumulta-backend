"""Ejecución controlada de Reanalysis bajo la política RTM CORE.

Este adaptador conserva el extractor validado ``traffic_fine_reanalysis_v1_18``
pero sustituye su selector previo de lectura profunda. El selector legacy no es
una autoridad jurídica y no puede usar familia heredada, OCR crudo ni etiquetas
impresas del formulario para lanzar Velocidad.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Mapping, Optional

from fastapi import HTTPException
from sqlalchemy import text

import reanalysis as legacy_reanalysis
from case_authority import verify_signed_case_authority
from database import get_engine
from rtm_core.extraction_policy import (
    EXTRACTION_POLICY_VERSION,
    ExtractionRouteDecision,
    last_extraction_route_decision,
    select_deep_extraction_route,
)
from rtm_core.ops_case_scope import OpsCaseScope, require_case_in_scope
from rtm_core.runtime_capabilities import require_http_capability


REANALYSIS_EXECUTION_VERSION = "rtm_safe_reanalysis_execution_v1_0"
_TERMINAL_CASE_STATUSES = {
    "submitted",
    "closed",
    "archived",
    "resolved",
    "estimado",
    "desestimado",
    "presentado_manual_ayuntamiento",
    "presentado_auto_dgt",
    "presentado_auto_registro",
}
_INSTALL_LOCK = threading.RLock()
_ORIGINAL_SELECTOR = getattr(legacy_reanalysis, "_resolved_traffic_family", None)
_INSTALL_MARKER = "_rtm_safe_extraction_policy_version"


def install_safe_extraction_policy() -> dict[str, Any]:
    """Instala de forma idempotente el selector conservador en Reanalysis."""

    with _INSTALL_LOCK:
        current_version = getattr(legacy_reanalysis, _INSTALL_MARKER, None)
        if (
            current_version != EXTRACTION_POLICY_VERSION
            or getattr(legacy_reanalysis, "_resolved_traffic_family", None)
            is not select_deep_extraction_route
        ):
            legacy_reanalysis._resolved_traffic_family = select_deep_extraction_route
            setattr(legacy_reanalysis, _INSTALL_MARKER, EXTRACTION_POLICY_VERSION)

    return extraction_policy_status()


def extraction_policy_status() -> dict[str, Any]:
    current = getattr(legacy_reanalysis, "_resolved_traffic_family", None)
    original_name = getattr(_ORIGINAL_SELECTOR, "__name__", None)
    original_module = getattr(_ORIGINAL_SELECTOR, "__module__", None)
    return {
        "ok": True,
        "execution_version": REANALYSIS_EXECUTION_VERSION,
        "policy_version": EXTRACTION_POLICY_VERSION,
        "installed": current is select_deep_extraction_route,
        "installed_marker": getattr(legacy_reanalysis, _INSTALL_MARKER, None),
        "current_selector": getattr(current, "__name__", None),
        "current_selector_module": getattr(current, "__module__", None),
        "legacy_selector_preserved_as": (
            f"{original_module}.{original_name}"
            if original_module and original_name
            else None
        ),
    }


def _case_guard(case_id: str, *, scope: OpsCaseScope) -> Mapping[str, Any]:
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        row = conn.execute(
            text(
                """
                SELECT c.id,
                       COALESCE(c.payment_status, '') AS payment_status,
                       COALESCE(c.authorized, FALSE) AS authorized,
                       COALESCE(c.status, '') AS status,
                       COALESCE(c.department, '') AS department,
                       COALESCE(c.case_type, '') AS case_type,
                       COALESCE(NULLIF(to_jsonb(c)->>'test_mode', '')::boolean, FALSE)
                           AS test_mode,
                       to_regclass('public.rtm_validated_facts') AS facts_table
                FROM cases c
                WHERE c.id=:case_id
                FOR UPDATE
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        meta = row._mapping
        if str(meta["payment_status"]) != "paid":
            raise HTTPException(
                status_code=402,
                detail="El estudio debe estar pagado antes del Reanalysis CORE",
            )
        if not bool(meta["authorized"]):
            raise HTTPException(status_code=409, detail="Falta autorización del cliente")
        if bool(meta["test_mode"]):
            raise HTTPException(
                status_code=409,
                detail="El Reanalysis CORE operativo no admite test_mode",
            )
        if str(meta["status"]) in _TERMINAL_CASE_STATUSES:
            raise HTTPException(status_code=409, detail="El expediente está finalizado")
        # ``reanalysis_in_progress`` is a durable single-flight claim.  A
        # second request must not be allowed to CAS the value to itself after
        # waiting for the row lock, otherwise two provider runs can publish
        # against the same case.
        if str(meta["status"]) == "reanalysis_in_progress":
            raise HTTPException(
                status_code=409,
                detail="Ya existe un reanálisis en curso para el expediente",
            )
        if str(meta["department"]).strip().lower() != "traffic":
            raise HTTPException(
                status_code=409,
                detail="La ejecución segura actual solo admite Tráfico",
            )
        if str(meta["case_type"]).strip().lower() not in {
            "fine",
            "multa",
            "multas",
            "sanction",
            "sancion",
            "sanción",
        }:
            raise HTTPException(
                status_code=409,
                detail="La ejecución segura actual solo admite multas de tráfico",
            )
        if not meta["facts_table"]:
            raise HTTPException(
                status_code=409,
                detail="La migración de autoridad RTM CORE todavía no está aplicada",
            )

        active_facts = conn.execute(
            text(
                """
                SELECT id, frozen
                FROM rtm_validated_facts
                WHERE case_id=:case_id AND invalidated_at IS NULL
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"case_id": case_id},
        ).fetchone()
        if active_facts:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Ya existen hechos activos. Deben invalidarse expresamente "
                        "antes de ejecutar un nuevo Reanalysis."
                    ),
                    "validated_facts_id": str(active_facts[0]),
                    "frozen": bool(active_facts[1]),
                },
            )

        originals = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM documents
                WHERE case_id=:case_id AND kind='original'
                  AND b2_bucket IS NOT NULL AND b2_key IS NOT NULL
                """
            ),
            {"case_id": case_id},
        ).scalar_one()
        if int(originals or 0) < 1:
            raise HTTPException(
                status_code=409,
                detail="El expediente no contiene documentos originales analizables",
            )
        authority = verify_signed_case_authority(conn, case_id)
        claimed = conn.execute(
            text(
                """
                UPDATE cases
                SET status='reanalysis_in_progress', updated_at=NOW()
                WHERE id=:case_id AND status=:prior_status
                RETURNING id
                """
            ),
            {"case_id": case_id, "prior_status": str(meta["status"])},
        ).fetchone()
        if not claimed:
            raise HTTPException(
                status_code=409,
                detail="El expediente cambió antes de iniciar Reanalysis",
            )

        return {
            **dict(meta),
            "prior_status": str(meta["status"]),
            "authority_material_sha256": str(
                authority.get("material_sha256") or ""
            ),
        }


def _reset_failed_claim(case_id: str, prior_status: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE cases
                SET status=:prior_status, updated_at=NOW()
                WHERE id=:case_id AND status='reanalysis_in_progress'
                """
            ),
            {"case_id": case_id, "prior_status": prior_status},
        )


def _append_policy_event(
    case_id: str,
    *,
    actor: str,
    decision: Optional[ExtractionRouteDecision],
) -> None:
    payload = {
        "execution_version": REANALYSIS_EXECUTION_VERSION,
        "policy_version": EXTRACTION_POLICY_VERSION,
        "actor": actor,
        "decision": decision.as_dict() if decision else None,
    }
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'rtm_extraction_route_decided',
                        CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )


def run_safe_traffic_reanalysis(
    case_id: str,
    *,
    actor: str,
    scope: OpsCaseScope,
) -> dict[str, Any]:
    """Ejecuta Reanalysis; no promueve ni congela hechos automáticamente."""

    require_http_capability("document_provider")
    claim = _case_guard(case_id, scope=scope)
    installation = install_safe_extraction_policy()
    try:
        result = legacy_reanalysis.reanalyze_traffic_fine_case(case_id)
    except BaseException:
        _reset_failed_claim(case_id, str(claim["prior_status"]))
        raise
    decision = last_extraction_route_decision()
    _append_policy_event(case_id, actor=actor, decision=decision)

    return {
        "ok": True,
        "case_id": case_id,
        "execution_version": REANALYSIS_EXECUTION_VERSION,
        "policy": installation,
        "route_decision": decision.as_dict() if decision else None,
        "reanalysis": result,
        "persisted_authority": False,
        "next_action": (
            "OPS debe revisar /reanalysis/facts-preview y crear el borrador "
            "ValidatedFacts de forma explícita."
        ),
    }
