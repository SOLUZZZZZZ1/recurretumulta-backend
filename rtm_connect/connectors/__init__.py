"""Conectores internos sintéticos disponibles en RTM CONNECT."""

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
from rtm_connect.connectors.synthetic_webhook import (
    SYNTHETIC_WEBHOOK_CAPABILITY,
    SYNTHETIC_WEBHOOK_CODE,
    SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
    SyntheticWebhookConnector,
    SyntheticWebhookContractError,
    SyntheticWebhookDelivery,
    SyntheticWebhookIntegrityError,
    SyntheticWebhookOutcome,
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
    "SYNTHETIC_WEBHOOK_CAPABILITY",
    "SYNTHETIC_WEBHOOK_CODE",
    "SYNTHETIC_WEBHOOK_CONNECTOR_VERSION",
    "SyntheticWebhookConnector",
    "SyntheticWebhookContractError",
    "SyntheticWebhookDelivery",
    "SyntheticWebhookIntegrityError",
    "SyntheticWebhookOutcome",
]
