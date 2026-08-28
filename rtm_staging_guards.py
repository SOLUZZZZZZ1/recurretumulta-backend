"""Guardas estrictas para mutaciones sintéticas de laboratorio en staging."""

from __future__ import annotations

import os

from fastapi import HTTPException


_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def require_isolated_synthetic_staging() -> None:
    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    namespace = (os.getenv("RTM_DATA_NAMESPACE") or "").strip().lower()
    policy = (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
    allow_real = (os.getenv("RTM_ALLOW_REAL_CUSTOMER_DATA") or "").strip().lower()
    blockers: list[str] = []
    if environment != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in namespace:
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if policy != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if allow_real not in _FALSE_VALUES:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    if blockers:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "La mutación sintética solo está disponible en staging aislado.",
                "blockers": blockers,
            },
        )


__all__ = ["require_isolated_synthetic_staging"]
