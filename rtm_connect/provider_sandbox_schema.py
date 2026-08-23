"""Dependencias persistentes reutilizadas por C6; cero DDL nuevo."""

from __future__ import annotations

from rtm_connect.schema import CONNECT_C1_REQUIRED_COLUMNS


RTM_CONNECT_C6_PROVIDER_SCHEMA_VERSION = (
    "rtm_connect_c6_provider_schema_v1_0"
)
CONNECT_C6_SCHEMA_CHANGES_REQUIRED = False

CONNECT_C6_REQUIRED_COLUMNS: dict[str, set[str]] = {
    table: set(columns)
    for table, columns in CONNECT_C1_REQUIRED_COLUMNS.items()
}


def connect_c6_provider_ddl() -> list[tuple[str, str]]:
    return []


__all__ = [
    "RTM_CONNECT_C6_PROVIDER_SCHEMA_VERSION",
    "CONNECT_C6_REQUIRED_COLUMNS",
    "CONNECT_C6_SCHEMA_CHANGES_REQUIRED",
    "connect_c6_provider_ddl",
]
