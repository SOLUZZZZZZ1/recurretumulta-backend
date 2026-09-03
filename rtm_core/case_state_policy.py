"""Política única para mutaciones públicas del material de un expediente.

El token público identifica al titular del expediente, pero no autoriza a
reescribir una cadena que ya está congelada, en presentación o terminada.  Los
routers públicos deben adquirir el mismo lock de fila y aplicar esta política
antes de cambiar identidad, autoridad, documentos o estado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text


TERMINAL_CASE_STATUSES = frozenset(
    {
        "submitted",
        "closed",
        "archived",
        "archived_test",
        "resolved",
        "cancelled",
        "estimado",
        "desestimado",
        "presentado_manual_ayuntamiento",
        "presentado_auto_dgt",
        "presentado_auto_registro",
    }
)

PROTECTED_PROCESSING_CASE_STATUSES = frozenset(
    {
        "submitting",
        "reanalysis_in_progress",
        "document_extraction_in_progress",
        "submission_receipt_pending",
        "submission_outcome_unknown",
    }
)

# En estas fases existe ya un artefacto o una intención de servicio que no puede
# quedar desligada de la identidad/documentación sobre la que se creó.
FROZEN_PUBLIC_WORKFLOW_CASE_STATUSES = frozenset(
    {
        "final_ready",
        "manual_review",
        "ready_to_submit",
        "payment_reconciliation_required",
        "vehicle_removal_pending_payment",
        "vehicle_removal_paid",
        "vehicle_removal_assigned",
        "vehicle_removal_completed",
    }
)

PUBLIC_MATERIAL_MUTATION_BLOCKED_STATUSES = frozenset(
    TERMINAL_CASE_STATUSES
    | PROTECTED_PROCESSING_CASE_STATUSES
    | FROZEN_PUBLIC_WORKFLOW_CASE_STATUSES
)

# Un checkout abierto queda ligado al material y a la autoridad verificados al
# crearlo. Permitir una mutación mientras Stripe todavía puede cobrar dejaría
# una sesión cobrable cuyo webhook tendría que rechazar después del cargo.
PUBLIC_MATERIAL_MUTATION_BLOCKED_PAYMENT_STATUSES = frozenset(
    {
        "creating",
        "pending",
        "paid",
        "manual_review",
        "failed",
        "disputed",
        "refunded",
    }
)


@dataclass(frozen=True)
class LockedPublicCaseState:
    payment_status: str
    status: str


def normalize_case_status(value: Any) -> str:
    return str(value or "").strip().lower()


def public_material_mutation_is_blocked(status: Any) -> bool:
    normalized = normalize_case_status(status)
    return (
        normalized in PUBLIC_MATERIAL_MUTATION_BLOCKED_STATUSES
        or normalized.startswith("presentado")
    )


def require_public_material_mutation_status(status: Any) -> str:
    normalized = normalize_case_status(status)
    if public_material_mutation_is_blocked(normalized):
        raise HTTPException(
            status_code=409,
            detail="El expediente ya no admite cambios públicos de su material",
        )
    return normalized


def require_public_material_mutation_payment_status(payment_status: Any) -> str:
    normalized = normalize_case_status(payment_status)
    if normalized in PUBLIC_MATERIAL_MUTATION_BLOCKED_PAYMENT_STATUSES:
        detail = (
            "El expediente tiene un pago en curso y no admite cambios públicos"
            if normalized in {"creating", "pending"}
            else "El estado de pago no admite cambios públicos del expediente"
        )
        raise HTTPException(
            status_code=409,
            detail=detail,
        )
    return normalized


def _row_value(row: Any, index: int, key: str) -> Any:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return ""


def lock_case_for_material_mutation(
    conn: Any,
    case_id: str,
) -> LockedPublicCaseState:
    """Bloquea el expediente y rechaza estados públicos no mutables."""

    row = conn.execute(
        text(
            "SELECT COALESCE(payment_status,'') AS payment_status, "
            "COALESCE(status,'') AS status FROM cases "
            "WHERE id=:case_id FOR UPDATE"
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    payment_status = require_public_material_mutation_payment_status(
        _row_value(row, 0, "payment_status")
    )
    status = require_public_material_mutation_status(
        _row_value(row, 1, "status")
    )
    return LockedPublicCaseState(
        payment_status=payment_status,
        status=status,
    )


def lock_case_for_public_material_mutation(
    conn: Any,
    case_id: str,
) -> LockedPublicCaseState:
    """Alias explícito para los entrypoints públicos."""

    return lock_case_for_material_mutation(conn, case_id)


__all__ = [
    "FROZEN_PUBLIC_WORKFLOW_CASE_STATUSES",
    "LockedPublicCaseState",
    "PROTECTED_PROCESSING_CASE_STATUSES",
    "PUBLIC_MATERIAL_MUTATION_BLOCKED_PAYMENT_STATUSES",
    "PUBLIC_MATERIAL_MUTATION_BLOCKED_STATUSES",
    "TERMINAL_CASE_STATUSES",
    "lock_case_for_material_mutation",
    "lock_case_for_public_material_mutation",
    "normalize_case_status",
    "public_material_mutation_is_blocked",
    "require_public_material_mutation_payment_status",
    "require_public_material_mutation_status",
]
