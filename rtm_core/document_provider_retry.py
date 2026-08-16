"""Reintentos acotados para el proveedor documental OpenAI Responses.

La política solo reintenta respuestas HTTP 429. No reintenta errores de
credenciales, permisos, modelo, esquema ni contenido. El tiempo indicado por el
proveedor se respeta con un pequeño margen y nunca se imprimen secretos.
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable, Optional

from fastapi import HTTPException

from rtm_core.document_extraction import (
    OpenAIResponsesDocumentProvider,
    ProviderDocumentResult,
    SourceDocument,
)


DOCUMENT_PROVIDER_RETRY_VERSION = "rtm_document_provider_retry_v1_0"


def _positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _rate_limit_delay(exc: HTTPException, attempt: int) -> Optional[float]:
    detail = exc.detail
    if not isinstance(detail, dict) or detail.get("status_code") != 429:
        return None

    provider_detail = str(detail.get("provider_detail") or "")
    match = re.search(
        r"try\s+again\s+in\s+([0-9]+(?:\.[0-9]+)?)s",
        provider_detail,
        flags=re.IGNORECASE,
    )
    if match:
        return float(match.group(1))

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
        self.max_attempts = (
            int(max_attempts)
            if max_attempts is not None
            else _positive_int("OPENAI_DOCUMENT_MAX_ATTEMPTS", 3)
        )
        self.retry_margin_seconds = (
            float(retry_margin_seconds)
            if retry_margin_seconds is not None
            else _positive_float(
                "OPENAI_DOCUMENT_RETRY_MARGIN_SECONDS",
                0.75,
            )
        )
        self._sleeper = sleeper

    def extract_document(
        self,
        *,
        service: str,
        document: SourceDocument,
        content: bytes,
    ) -> tuple[ProviderDocumentResult, str, list[str]]:
        retry_warnings: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
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
                wait_seconds = max(
                    0.1,
                    delay + self.retry_margin_seconds,
                )
                retry_warnings.append(
                    f"provider_rate_limit_retry:{attempt}"
                )
                self._sleeper(wait_seconds)

        raise RuntimeError("Bucle de reintentos documental agotado.")
