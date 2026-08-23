"""Resolución estrecha de secretos para RTM CONNECT C6.

El contrato solo acepta referencias ``env://`` explícitamente permitidas. El
valor resuelto no es serializable y su representación siempre está censurada.
Ni el conector ni el transporte reciben el mapping completo del entorno.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Mapping, Protocol


RTM_CONNECT_C6_SECRET_RESOLVER_VERSION = (
    "rtm_connect_c6_secret_resolver_v1_0"
)

_REFERENCE_RE = re.compile(r"^env://([A-Z][A-Z0-9_]{2,95})$")
_BEARER_RE = re.compile(r"^[A-Za-z0-9._~+/-]+={0,2}$")


class SecretResolutionError(RuntimeError):
    """La referencia no puede resolverse bajo la política C6."""


class ResolvedSecret:
    """Contenedor opaco y no serializable para el borde de transporte.

    No es un ``dataclass`` y no tiene ``__dict__``: ``vars`` y
    ``dataclasses.asdict`` no pueden convertirlo accidentalmente. También se
    bloquea el protocolo pickle. El único acceso deliberado al valor es
    :meth:`reveal_for_transport`.
    """

    __slots__ = ("_reference", "__value", "__sealed")

    def __init__(self, *, reference: str, value: str) -> None:
        normalized_reference = str(reference)
        normalized_value = str(value)
        if (
            not 16 <= len(normalized_value) <= 8192
            or not _BEARER_RE.fullmatch(normalized_value)
        ):
            raise SecretResolutionError("El secreto no cumple el contrato C6")
        object.__setattr__(self, "_reference", normalized_reference)
        object.__setattr__(self, "_ResolvedSecret__value", normalized_value)
        object.__setattr__(self, "_ResolvedSecret__sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_ResolvedSecret__sealed", False):
            raise AttributeError("ResolvedSecret es inmutable")
        object.__setattr__(self, name, value)

    @property
    def reference(self) -> str:
        return self._reference

    def reveal_for_transport(self) -> str:
        """Entrega el valor solo en el borde HTTP inmediato."""

        return self.__value

    def __repr__(self) -> str:
        return f"ResolvedSecret(reference={self.reference!r}, value=<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __copy__(self):
        raise TypeError("ResolvedSecret no es copiable")

    def __deepcopy__(self, memo):
        raise TypeError("ResolvedSecret no es copiable")

    def __reduce_ex__(self, protocol):
        raise TypeError("ResolvedSecret no es serializable")

    def __getstate__(self):
        raise TypeError("ResolvedSecret no es serializable")


class SecretResolver(Protocol):
    def resolve(self, reference: str) -> ResolvedSecret:
        """Resuelve una referencia ya allowlisted."""


class EnvironmentSecretResolver:
    """Resolver fail-closed sobre un mapping y una allowlist exacta."""

    __slots__ = ("_allowed", "_values", "__sealed")

    def __init__(
        self,
        values: Mapping[str, str],
        *,
        allowed_references: tuple[str, ...],
    ) -> None:
        allowed = frozenset(allowed_references)
        object.__setattr__(self, "_allowed", allowed)
        if not self._allowed:
            raise SecretResolutionError("La allowlist de secretos está vacía")
        allowed_names: set[str] = set()
        for reference in self._allowed:
            match = _REFERENCE_RE.fullmatch(str(reference))
            if not match:
                raise SecretResolutionError("Referencia allowlisted no válida")
            allowed_names.add(match.group(1))
        # Nunca retenemos ``os.environ`` ni secretos ajenos al contrato.
        object.__setattr__(
            self,
            "_values",
            MappingProxyType({
                name: str(values[name])
                for name in allowed_names
                if name in values
            }),
        )
        object.__setattr__(
            self,
            "_EnvironmentSecretResolver__sealed",
            True,
        )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_EnvironmentSecretResolver__sealed", False):
            raise AttributeError("EnvironmentSecretResolver es inmutable")
        object.__setattr__(self, name, value)

    def assert_runtime_sealed(self, *, expected_reference: str) -> None:
        expected = str(expected_reference)
        match = _REFERENCE_RE.fullmatch(expected)
        if (
            type(self) is not EnvironmentSecretResolver
            or match is None
            or self._allowed != frozenset({expected})
            or not set(self._values).issubset({match.group(1)})
        ):
            raise SecretResolutionError("Resolver C6 no sellado")

    def resolve(self, reference: str) -> ResolvedSecret:
        normalized = str(reference or "").strip()
        if normalized not in self._allowed:
            raise SecretResolutionError("Referencia de secreto no autorizada")
        match = _REFERENCE_RE.fullmatch(normalized)
        if not match:
            raise SecretResolutionError("Formato de referencia no admitido")
        name = match.group(1)
        value = self._values.get(name)
        if value is None:
            raise SecretResolutionError("Secreto requerido no disponible")
        return ResolvedSecret(reference=normalized, value=str(value))


__all__ = [
    "RTM_CONNECT_C6_SECRET_RESOLVER_VERSION",
    "EnvironmentSecretResolver",
    "ResolvedSecret",
    "SecretResolutionError",
    "SecretResolver",
]
