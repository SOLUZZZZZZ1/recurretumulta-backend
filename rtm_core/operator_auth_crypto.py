"""Criptografía y tokens del acceso individual de operadores RTM.

Esta unidad no expone rutas HTTP ni sustituye el PIN OPS. Centraliza el hash
Argon2id de contraseñas y la generación de secretos opacos de sesión/dispositivo.
Nunca devuelve ni registra contraseñas en claro.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from email_validator import EmailNotValidError, validate_email


OPERATOR_AUTH_CRYPTO_VERSION = "rtm_operator_auth_crypto_v1_0"

# Perfil explícito Argon2id: 19 MiB, 2 iteraciones, un hilo.
ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST_KIB = 19_456
ARGON2_PARALLELISM = 1
ARGON2_HASH_LENGTH = 32
ARGON2_SALT_LENGTH = 16

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
SESSION_TOKEN_BYTES = 48
DEVICE_SECRET_BYTES = 32


class PasswordPolicyError(ValueError):
    """La contraseña no cumple la política mínima de RTM."""


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool = False


_PASSWORD_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LENGTH,
    salt_len=ARGON2_SALT_LENGTH,
    type=Type.ID,
)


def normalize_operator_email(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Email requerido")
    try:
        normalized = validate_email(raw, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("Email de operador no válido") from exc
    return normalized.casefold()


def validate_operator_password(password: str) -> str:
    value = str(password or "")
    if "\x00" in value:
        raise PasswordPolicyError("La contraseña contiene un carácter no permitido")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    if len(value) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"La contraseña no puede superar {MAX_PASSWORD_LENGTH} caracteres"
        )
    if not value.strip():
        raise PasswordPolicyError("La contraseña no puede estar vacía")
    return value


def hash_operator_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(validate_operator_password(password))


def verify_operator_password(
    password_hash: str | None,
    candidate: str,
) -> PasswordVerification:
    encoded = str(password_hash or "").strip()
    if not encoded:
        return PasswordVerification(valid=False)
    try:
        valid = bool(_PASSWORD_HASHER.verify(encoded, str(candidate or "")))
        return PasswordVerification(
            valid=valid,
            needs_rehash=(
                _PASSWORD_HASHER.check_needs_rehash(encoded) if valid else False
            ),
        )
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return PasswordVerification(valid=False)


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def generate_device_secret() -> str:
    return secrets.token_urlsafe(DEVICE_SECRET_BYTES)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def hash_session_token(raw_token: str) -> str:
    token = str(raw_token or "")
    if len(token) < 32:
        raise ValueError("Token de sesión inválido")
    return sha256_hex(token)


def hash_device_secret(raw_secret: str) -> str:
    secret = str(raw_secret or "")
    if len(secret) < 24:
        raise ValueError("Identificador de dispositivo inválido")
    return sha256_hex(secret)


def hmac_identifier(value: str, secret: str) -> str:
    key = str(secret or "").encode("utf-8")
    if len(key) < 32:
        raise ValueError("La clave HMAC debe tener al menos 32 caracteres")
    message = str(value or "").strip().casefold().encode("utf-8")
    if not message:
        raise ValueError("Identificador requerido")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def token_digest_matches(raw_token: str, expected_digest: str) -> bool:
    try:
        actual = hash_session_token(raw_token)
    except ValueError:
        return False
    return hmac.compare_digest(actual, str(expected_digest or ""))


__all__ = [
    "ARGON2_HASH_LENGTH",
    "ARGON2_MEMORY_COST_KIB",
    "ARGON2_PARALLELISM",
    "ARGON2_SALT_LENGTH",
    "ARGON2_TIME_COST",
    "DEVICE_SECRET_BYTES",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "OPERATOR_AUTH_CRYPTO_VERSION",
    "PasswordPolicyError",
    "PasswordVerification",
    "SESSION_TOKEN_BYTES",
    "generate_device_secret",
    "generate_session_token",
    "hash_device_secret",
    "hash_operator_password",
    "hash_session_token",
    "hmac_identifier",
    "normalize_operator_email",
    "sha256_hex",
    "token_digest_matches",
    "validate_operator_password",
    "verify_operator_password",
]
