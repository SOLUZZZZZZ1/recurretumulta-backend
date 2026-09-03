"""Reintentos acotados para el proveedor documental OpenAI Responses.

La política solo reintenta respuestas HTTP 429. No reintenta errores de
credenciales, permisos, modelo, esquema ni contenido. El tiempo indicado por el
proveedor se respeta con un pequeño margen y nunca se imprimen secretos.
"""

from __future__ import annotations

import math
import os
import time
from typing import Callable, Optional

from fastapi import HTTPException

from rtm_core.ai_security import require_model_call_budget
from rtm_core.document_extraction import (
    OpenAIResponsesDocumentProvider,
    ProviderDocumentResult,
    SourceDocument,
)


DOCUMENT_PROVIDER_RETRY_VERSION = "rtm_document_provider_retry_v1_0"
MAX_DOCUMENT_PROVIDER_ATTEMPTS = 3
MAX_DOCUMENT_PROVIDER_RETRY_MARGIN_SECONDS = 5.0


def _bounded_attempts_from_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Configuración inválida para {name}") from exc
    if not 1 <= value <= MAX_DOCUMENT_PROVIDER_ATTEMPTS:
        raise RuntimeError(
            f"{name} debe estar entre 1 y {MAX_DOCUMENT_PROVIDER_ATTEMPTS}"
        )
    return value


def _bounded_margin_from_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Configuración inválida para {name}") from exc
    if (
        not math.isfinite(value)
        or value < 0.0
        or value > MAX_DOCUMENT_PROVIDER_RETRY_MARGIN_SECONDS
    ):
        raise RuntimeError(
            f"{name} debe estar entre 0 y "
            f"{MAX_DOCUMENT_PROVIDER_RETRY_MARGIN_SECONDS}"
        )
    return value


def _rate_limit_delay(exc: HTTPException, attempt: int) -> Optional[float]:
    detail = exc.detail
    if (
        not isinstance(detail, dict)
        or detail.get("code") != "document_provider_rate_limited"
    ):
        return None

    try:
        retry_after = float(detail.get("retry_after_seconds") or 0)
    except (TypeError, ValueError):
        retry_after = 0.0
    if 0.0 < retry_after <= 60.0:
        return retry_after

    return min(2.0 ** max(0, attempt - 1), 8.0)


class RetryingOpenAIResponsesDocumentProvider(
    OpenAIResponsesDocumentProvider
):
    """Proveedor compatible que añade reintentos transparentes para 429."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_attempts: Optional[int] = None,
        retry_margin_seconds: Optional[float] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        if max_attempts is not None and (
            isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
        ):
            raise ValueError("max_attempts debe ser un entero")
        requested_attempts = (
            max_attempts
            if max_attempts is not None
            else _bounded_attempts_from_env(
                "OPENAI_DOCUMENT_MAX_ATTEMPTS",
                MAX_DOCUMENT_PROVIDER_ATTEMPTS,
            )
        )
        if not 1 <= requested_attempts <= MAX_DOCUMENT_PROVIDER_ATTEMPTS:
            raise ValueError(
                "max_attempts fuera del rango seguro "
                f"1..{MAX_DOCUMENT_PROVIDER_ATTEMPTS}"
            )
        self.max_attempts = requested_attempts

        requested_margin = (
            float(retry_margin_seconds)
            if retry_margin_seconds is not None
            else _bounded_margin_from_env(
                "OPENAI_DOCUMENT_RETRY_MARGIN_SECONDS",
                0.75,
            )
        )
        if (
            not math.isfinite(requested_margin)
            or requested_margin < 0.0
            or requested_margin > MAX_DOCUMENT_PROVIDER_RETRY_MARGIN_SECONDS
        ):
            raise ValueError(
                "retry_margin_seconds fuera del rango seguro 0.."
                f"{MAX_DOCUMENT_PROVIDER_RETRY_MARGIN_SECONDS}"
            )
        self.retry_margin_seconds = requested_margin
        self._sleeper = sleeper

    def extract_document(
        self,
        *,
        service: str,
        document: SourceDocument,
        content: bytes,
    ) -> tuple[ProviderDocumentResult, str, list[str]]:
        # Este adaptador nunca puede convertir la ausencia de presupuesto en
        # llamadas ilimitadas. Los entrypoints live deben instalar uno antes.
        require_model_call_budget()
        retry_warnings: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            require_model_call_budget()
            try:
                result, mode, warnings = super().extract_document(
                    service=service,
                    document=document,
                    content=content,
                )
                return (
                    result,
                    mode,
                    [*warnings, *retry_warnings],
                )
            except HTTPException as exc:
                delay = _rate_limit_delay(exc, attempt)
                if delay is None or attempt >= self.max_attempts:
                    raise
                # No duerme si el siguiente intento ya estaría bloqueado por
                # el presupuesto exterior.
                require_model_call_budget()
                wait_seconds = max(
                    0.1,
                    delay + self.retry_margin_seconds,
                )
                retry_warnings.append(
                    f"provider_rate_limit_retry:{attempt}"
                )
                self._sleeper(wait_seconds)

        raise RuntimeError("Bucle de reintentos documental agotado.")
