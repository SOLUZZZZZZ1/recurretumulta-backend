"""Contrato interno de conectores RTM CONNECT C2.

Esta interfaz no publica rutas ni conoce proveedores. Un adaptador recibe una
acción ya autorizable por CORE y devuelve un resultado normalizado sin secretos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rtm_connect.contracts import (
    ConnectActionRequest,
    ConnectExecutionResult,
    ConnectorMode,
    RiskClass,
)


RTM_CONNECT_C2_CONNECTOR_BASE_VERSION = (
    "rtm_connect_c2_connector_base_v1_0"
)


@dataclass(frozen=True)
class ConnectorDescriptor:
    code: str
    version: str
    mode: ConnectorMode
    capabilities: tuple[str, ...]
    risk_ceiling: RiskClass
    supports_idempotency: bool
    supports_reconciliation: bool
    synthetic_only: bool
    network_used: bool
    manifest_sha256: str


class ConnectorAdapter(Protocol):
    descriptor: ConnectorDescriptor

    def execute(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        scenario: str,
    ) -> ConnectExecutionResult:
        """Ejecuta una actuación ya autorizada por el Kernel."""

    def reconcile(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        external_reference: str,
    ) -> ConnectExecutionResult:
        """Reconcilia un resultado previamente clasificado como unknown."""


__all__ = [
    "RTM_CONNECT_C2_CONNECTOR_BASE_VERSION",
    "ConnectorAdapter",
    "ConnectorDescriptor",
]
