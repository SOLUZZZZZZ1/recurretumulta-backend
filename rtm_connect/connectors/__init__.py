"""Conectores internos disponibles en RTM CONNECT C2."""

from rtm_connect.connectors.base import (
    ConnectorAdapter,
    ConnectorDescriptor,
)
from rtm_connect.connectors.synthetic_echo import (
    SYNTHETIC_ECHO_CAPABILITY,
    SYNTHETIC_ECHO_CODE,
    SYNTHETIC_ECHO_CONNECTOR_VERSION,
    SyntheticEchoConnector,
    SyntheticEchoContractError,
    SyntheticEchoScenario,
)


__all__ = [
    "ConnectorAdapter",
    "ConnectorDescriptor",
    "SYNTHETIC_ECHO_CAPABILITY",
    "SYNTHETIC_ECHO_CODE",
    "SYNTHETIC_ECHO_CONNECTOR_VERSION",
    "SyntheticEchoConnector",
    "SyntheticEchoContractError",
    "SyntheticEchoScenario",
]
