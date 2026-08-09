"""Cortafuegos de rutas legacy incompatibles con la autoridad RTM CORE.

Estas rutas se conservan en archivos históricos mientras se migra el panel OPS,
pero quedan registradas antes que los routers legacy. De ese modo ninguna
petición HTTP puede saltarse hechos congelados, familia bloqueada, Previa
Jurídica y Generate controlado.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from rtm_core.security import require_operator_token


router = APIRouter(tags=["rtm-core-legacy-guard"])


def _blocked(*, replacement: str, reason: str) -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "RTM_LEGACY_ROUTE_DISABLED",
            "message": reason,
            "replacement": replacement,
        },
    )


def _blocked_ops(
    token: Optional[str],
    *,
    replacement: str,
    reason: str,
) -> None:
    require_operator_token(token)
    _blocked(replacement=replacement, reason=reason)


@router.post("/generate/dgt")
def block_public_legacy_generate():
    _blocked(
        replacement="POST /ops/core/cases/{case_id}/legal-previews/{preview_id}/generate",
        reason=(
            "Generate directo queda deshabilitado. Solo puede generarse desde "
            "una Previa Jurídica aprobada y congelada."
        ),
    )


@router.post("/ops/cases/{case_id}/reanalyze")
def block_legacy_reanalysis(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE facts pipeline",
        reason=(
            "El reanálisis legacy queda detenido hasta que la extracción escriba "
            "ValidatedFacts sin decidir familia ni estrategia."
        ),
    )


@router.post("/ops/cases/{case_id}/final-resource")
def block_legacy_final_resource_draft(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE LegalPreview lifecycle",
        reason="El borrador final libre no puede sustituir a la Previa Jurídica versionada.",
    )


@router.post("/ops/cases/{case_id}/finalize-resource")
def block_legacy_finalize_resource(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="POST /ops/core/cases/{case_id}/legal-previews/{preview_id}/generate",
        reason="La finalización legacy no conserva la cadena de autoridad RTM CORE.",
    )


@router.post("/ops/cases/{case_id}/send-complete")
def block_legacy_send_complete(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE submission workflow",
        reason="El envío debe partir del recurso CORE aprobado para presentación.",
    )


@router.post("/ops/cases/{case_id}/save-ai-overrides")
def block_legacy_ai_overrides(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="ValidatedFacts / FamilyResolution versioning",
        reason=(
            "Los hechos y la familia no pueden sobrescribirse dentro de "
            "interested_data mediante overrides."
        ),
    )


@router.post("/ops/cases/{case_id}/approve")
def block_legacy_case_approve(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement=(
            "POST /ops/core/cases/{case_id}/generated-resources/"
            "{resource_id}/approve-submission"
        ),
        reason="No puede marcarse ready_to_submit sin un recurso CORE trazable.",
    )


@router.post("/ops/cases/{case_id}/override-family")
def block_legacy_override_family(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="POST /ops/core/cases/{case_id}/family-resolutions",
        reason="Una corrección de familia debe crear una resolución versionada y auditable.",
    )


@router.post("/ops/cases/{case_id}/override-family-and-regenerate")
def block_legacy_override_and_regenerate(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE authority → preview → generation",
        reason="No se permite reclasificar y regenerar en una sola operación.",
    )


@router.post("/ops/cases/{case_id}/rewrite-hecho-and-regenerate")
def block_legacy_rewrite_fact_and_regenerate(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE ValidatedFacts versioning",
        reason="Un hecho corregido debe originar una nueva versión; no puede reescribirse al generar.",
    )


@router.post("/ops/cases/{case_id}/submit")
def block_legacy_stub_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="POST /ops/automation/tick",
        reason="La presentación stub no acredita registro ni conserva justificante real.",
    )


@router.post("/ops/cases/{case_id}/mark-submitted")
def block_mark_submitted_without_receipt(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="POST /ops/cases/{case_id}/register-manual-submission",
        reason="No puede marcarse presentado sin registrar la actuación real y su justificante.",
    )


@router.post("/ops/cases/{case_id}/force-ready-to-submit")
def block_force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement=(
            "POST /ops/core/cases/{case_id}/generated-resources/"
            "{resource_id}/approve-submission"
        ),
        reason="El estado ready_to_submit solo nace de un recurso CORE aprobado.",
    )


@router.post("/ops/cases/{case_id}/lab-force-ready-to-submit")
def block_lab_force_ready_to_submit(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="Entorno de pruebas aislado",
        reason="Las llaves de laboratorio no pueden alterar expedientes del circuito operativo.",
    )


@router.post("/ops/cases/{case_id}/lab-force-authorize")
def block_lab_force_authorize(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="Autorización real del cliente",
        reason="La autorización no puede fabricarse desde OPS o laboratorio.",
    )


@router.post("/ops/cases/{case_id}/lab-force-paid")
def block_lab_force_paid(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="Webhook de Stripe validado",
        reason="El pago solo puede confirmarse mediante el proveedor de cobro.",
    )


@router.post("/ops/cases/{case_id}/force-generate")
def block_force_generate(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _blocked_ops(
        x_operator_token,
        replacement="RTM CORE LegalPreview → Generate",
        reason="No existe una excepción que permita generar fuera de la previa congelada.",
    )
