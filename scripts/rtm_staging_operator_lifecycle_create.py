#!/usr/bin/env python3
"""Alta interactiva y auditada de un operador sintético en staging.

Este script no es el bootstrap histórico de operadores. Reutiliza las rutas
oficiales de login, administración, ciclo de vida y logout mediante ASGI local,
por lo que conserva autorización, Argon2id, auditoría y transacciones reales sin
enviar credenciales por la red pública.

Ninguna contraseña se acepta por argv o variables de entorno. La contraseña del
supervisor se lee con ``getpass`` y la temporal se genera o introduce únicamente
en un TTY. El informe JSON usa lista blanca y nunca contiene contraseñas, bearer,
cookies, identificadores opacos de dispositivo ni cuerpos HTTP sin sanear.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


AUTHORITY = "rtm_staging_operator_lifecycle_create"
VERSION = "rtm_staging_operator_lifecycle_create_v1_0"
CONFIRMATION = "STAGING_SYNTHETIC_OPERATOR_LIFECYCLE_CREATE_ONLY"
DEFAULT_SUPERVISOR_EMAIL = "rtm-staging-supervisor@example.com"
DEFAULT_OPERATOR_EMAIL = "rtm-staging-operador-02@example.com"
DEFAULT_OPERATOR_DISPLAY_NAME = "RTM STAGING OPERADOR 02"
ROLE_CODE = "rtm.operator"
_DEVICE_COOKIE = "__Host-rtm_presenter_device"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}


class ControlledOperationError(RuntimeError):
    """Error de código cerrado que nunca incorpora material sensible."""

    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        supervisor_session_closed: bool | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.supervisor_session_closed = supervisor_session_closed


@dataclass
class IssuedSecret:
    value: str = field(repr=False)
    generated: bool = False


@dataclass
class OperationOutcome:
    status: str
    operator: dict[str, Any]
    audit_event_id: str | None
    issued_secret: IssuedSecret | None = field(default=None, repr=False)
    supervisor_session_closed: bool = False

    @property
    def created(self) -> bool:
        return self.status == "created"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Alta controlada de un operador sintético mediante las rutas "
            "oficiales de staging. Sin --apply solo valida configuración."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--generate-password", action="store_true")
    parser.add_argument(
        "--supervisor-email",
        default=DEFAULT_SUPERVISOR_EMAIL,
    )
    parser.add_argument("--email", default=DEFAULT_OPERATOR_EMAIL)
    parser.add_argument(
        "--display-name",
        default=DEFAULT_OPERATOR_DISPLAY_NAME,
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def _strict_flag(
    environ: Mapping[str, str],
    name: str,
) -> bool | None:
    raw = str(environ.get(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _safety_blockers(environ: Mapping[str, str]) -> list[str]:
    blockers: list[str] = []
    if str(environ.get("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in str(
        environ.get("RTM_DATA_NAMESPACE") or ""
    ).strip().lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if (
        str(environ.get("RTM_ENVIRONMENT_CONFIRMATION") or "").strip()
        != "RTM_STAGING_ISOLATED"
    ):
        blockers.append("RTM_ENVIRONMENT_CONFIRMATION_must_confirm_staging")
    if (
        str(environ.get("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
        != "isolated"
    ):
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")

    real_data = _strict_flag(environ, "RTM_ALLOW_REAL_CUSTOMER_DATA")
    if real_data is None:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_invalid")
    elif real_data:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")

    for name in (
        "RTM_ENABLE_OPERATOR_AUTH_V1",
        "RTM_ENABLE_OPERATOR_ADMIN_V1",
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1",
    ):
        enabled = _strict_flag(environ, name)
        if enabled is None:
            blockers.append(f"{name}_invalid")
        elif not enabled:
            blockers.append(f"{name}_must_be_true")
    return blockers


def _print(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def _base_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "safe": False,
        "authority": AUTHORITY,
        "version": VERSION,
        "environment": (
            str(os.getenv("RTM_ENV") or "").strip().lower() or "unset"
        ),
        "apply_requested": bool(args.apply),
        "synthetic_only": True,
        "role_code": ROLE_CODE,
        "transport": "in_process_asgi",
        "public_network_used": False,
        "official_routes": [
            "/ops/auth/login",
            "/ops/auth/reauthenticate",
            "/ops/admin/operators",
            "/ops/auth/logout",
        ],
        "credentials_in_argv": False,
        "credentials_in_environment": False,
        "credentials_in_json": False,
        "temporary_password_returned": False,
        "public_registration_available": False,
        "blockers": [],
    }


def _interactive_tty_ready() -> bool:
    return bool(sys.stdin.isatty() and sys.stderr.isatty())


def _prompt_literal(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    value = sys.stdin.readline()
    if value == "":
        raise ControlledOperationError("interactive_input_closed")
    return value.rstrip("\r\n")


def _prompt_supervisor_password() -> str:
    value = getpass.getpass(
        "Contraseña del supervisor (entrada oculta): ",
        stream=sys.stderr,
    )
    if not value:
        raise ControlledOperationError("supervisor_password_required")
    return value


def _password_factory(*, generate: bool) -> Callable[[], IssuedSecret]:
    def issue() -> IssuedSecret:
        from rtm_core.operator_auth_crypto import validate_operator_password
        from rtm_core.operator_provisioning import generate_temporary_password

        if generate:
            return IssuedSecret(
                value=generate_temporary_password(),
                generated=True,
            )
        first = getpass.getpass(
            "Contraseña temporal del nuevo operador (entrada oculta): ",
            stream=sys.stderr,
        )
        second = getpass.getpass(
            "Repite la contraseña temporal (entrada oculta): ",
            stream=sys.stderr,
        )
        if first != second:
            raise ControlledOperationError(
                "temporary_password_confirmation_mismatch"
            )
        try:
            validated = validate_operator_password(first)
        except ValueError as exc:
            raise ControlledOperationError(
                "temporary_password_policy_rejected"
            ) from exc
        return IssuedSecret(value=validated, generated=False)

    return issue


def _json_object(response: Any, *, code: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise ControlledOperationError(
            f"{code}_invalid_json",
            http_status=int(response.status_code),
        ) from exc
    if not isinstance(payload, dict):
        raise ControlledOperationError(
            f"{code}_invalid_payload",
            http_status=int(response.status_code),
        )
    return payload


def _uuid_text(value: Any, *, code: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ControlledOperationError(code) from exc


def _operator_whitelist(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControlledOperationError("operator_response_invalid")
    operator_id = value.get("operator_id", value.get("id"))
    return {
        "operator_id": _uuid_text(
            operator_id,
            code="operator_id_invalid",
        ),
        "email": str(value.get("email") or "").strip().casefold(),
        "display_name": str(value.get("display_name") or "").strip(),
        "status": str(value.get("status") or "").strip(),
        "role_code": str(value.get("role_code") or "").strip(),
        "must_change_password": bool(value.get("must_change_password")),
    }


def _validate_exact_existing(
    value: Any,
    *,
    email: str,
    display_name: str,
) -> dict[str, Any]:
    operator = _operator_whitelist(value)
    profile = value.get("profile") if isinstance(value, dict) else None
    exact = (
        operator["email"] == email
        and operator["display_name"] == display_name
        and operator["status"] == "active"
        and operator["role_code"] == ROLE_CODE
        and isinstance(profile, dict)
        and profile.get("synthetic") is True
        and profile.get("environment") == "staging"
        and profile.get("purpose") == "controlled_operator_lifecycle"
    )
    if not exact:
        raise ControlledOperationError(
            "operator_identity_collision",
            http_status=409,
        )
    return operator


async def _find_existing_operator(
    client: Any,
    *,
    headers: dict[str, str],
    email: str,
    display_name: str,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = await client.get(
            "/ops/admin/operators",
            params={"limit": 100, "offset": offset},
            headers=headers,
        )
        if response.status_code != 200:
            raise ControlledOperationError(
                "operator_inventory_rejected",
                http_status=int(response.status_code),
            )
        body = _json_object(response, code="operator_inventory")
        items = body.get("items")
        pagination = body.get("pagination")
        if not isinstance(items, list) or not isinstance(pagination, dict):
            raise ControlledOperationError("operator_inventory_invalid")
        matches.extend(
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("email") or "").strip().casefold() == email
        )
        try:
            total = int(pagination.get("total"))
        except (TypeError, ValueError) as exc:
            raise ControlledOperationError(
                "operator_inventory_pagination_invalid"
            ) from exc
        offset += len(items)
        if offset >= total:
            break
        if not items or offset > 10_000:
            raise ControlledOperationError(
                "operator_inventory_exceeds_safe_pagination"
            )

    if len(matches) > 1:
        raise ControlledOperationError("duplicate_operator_identity")
    if not matches:
        return None
    operator_id = _uuid_text(
        matches[0].get("id", matches[0].get("operator_id")),
        code="existing_operator_id_invalid",
    )
    response = await client.get(
        f"/ops/admin/operators/{operator_id}",
        headers=headers,
    )
    if response.status_code != 200:
        raise ControlledOperationError(
            "existing_operator_detail_rejected",
            http_status=int(response.status_code),
        )
    body = _json_object(response, code="existing_operator_detail")
    return _validate_exact_existing(
        body.get("operator"),
        email=email,
        display_name=display_name,
    )


async def _run_official_routes(
    app: Any,
    *,
    supervisor_email: str,
    supervisor_password: str,
    target_email: str,
    target_display_name: str,
    issue_secret: Callable[[], IssuedSecret],
) -> OperationOutcome:
    """Ejecuta rutas reales in-process; no usa URL ni transporte externos."""

    import httpx

    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )
    common_headers = {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        "User-Agent": f"RTM staging operator lifecycle CLI/{VERSION}",
    }
    token = ""
    device_secret = ""
    operation_error: ControlledOperationError | None = None
    outcome: OperationOutcome | None = None
    session_closed: bool | None = None

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://rtm-staging.internal",
        headers=common_headers,
        follow_redirects=False,
        timeout=30.0,
    ) as client:
        auth_status = await client.get("/ops/auth/status")
        auth_body = _json_object(auth_status, code="auth_status")
        if (
            auth_status.status_code != 200
            or auth_body.get("individual_login_enabled") is not True
        ):
            raise ControlledOperationError(
                "operator_auth_not_available",
                http_status=int(auth_status.status_code),
            )

        lifecycle_status = await client.get(
            "/ops/admin/lifecycle/status"
        )
        lifecycle_body = _json_object(
            lifecycle_status,
            code="lifecycle_status",
        )
        if (
            lifecycle_status.status_code != 200
            or lifecycle_body.get("operator_lifecycle_enabled") is not True
            or lifecycle_body.get("synthetic_only") is not True
            or lifecycle_body.get("passwords_returned") is not False
        ):
            raise ControlledOperationError(
                "operator_lifecycle_not_available",
                http_status=int(lifecycle_status.status_code),
            )

        login = await client.post(
            "/ops/auth/login",
            json={
                "email": supervisor_email,
                "password": supervisor_password,
            },
        )
        if login.status_code != 200:
            raise ControlledOperationError(
                "supervisor_login_rejected",
                http_status=int(login.status_code),
            )
        login_body = _json_object(login, code="supervisor_login")
        token = str(login_body.get("token") or "")
        operator = login_body.get("operator")
        authorization_headers = {
            "Authorization": f"Bearer {token}",
        }
        try:
            if len(token) < 32 or not isinstance(operator, dict):
                raise ControlledOperationError(
                    "supervisor_login_invalid"
                )
            try:
                device_secret = str(
                    client.cookies.get(_DEVICE_COOKIE) or ""
                )
            except Exception as exc:
                raise ControlledOperationError(
                    "supervisor_device_cookie_invalid"
                ) from exc
            supervisor_permissions = {
                str(item) for item in operator.get("permissions", [])
            }
            if len(device_secret) < 24:
                raise ControlledOperationError(
                    "supervisor_device_not_issued"
                )
            authorization_headers["X-RTM-Device"] = device_secret
            if "ops.supervise" not in supervisor_permissions:
                raise ControlledOperationError(
                    "supervisor_permission_missing",
                    http_status=403,
                )
            if bool(operator.get("must_change_password")):
                raise ControlledOperationError(
                    "supervisor_password_change_required",
                    http_status=409,
                )

            reauthenticated = await client.post(
                "/ops/auth/reauthenticate",
                json={"password": supervisor_password},
                headers=authorization_headers,
            )
            if (
                reauthenticated.status_code != 200
                or _json_object(
                    reauthenticated,
                    code="supervisor_reauthentication",
                ).get("status")
                != "reauthenticated"
            ):
                raise ControlledOperationError(
                    "supervisor_reauthentication_rejected",
                    http_status=int(reauthenticated.status_code),
                )

            existing = await _find_existing_operator(
                client,
                headers=authorization_headers,
                email=target_email,
                display_name=target_display_name,
            )
            if existing is not None:
                outcome = OperationOutcome(
                    status="already_exists",
                    operator=existing,
                    audit_event_id=None,
                )
            else:
                issued = issue_secret()
                create_payload = {
                    "email": target_email,
                    "display_name": target_display_name,
                    "temporary_password": issued.value,
                }
                created = await client.post(
                    "/ops/admin/operators",
                    json=create_payload,
                    headers=authorization_headers,
                )
                create_payload.clear()
                if created.status_code != 201:
                    raise ControlledOperationError(
                        "operator_create_rejected",
                        http_status=int(created.status_code),
                    )
                created_body = _json_object(
                    created,
                    code="operator_create",
                )
                created_operator = _operator_whitelist(
                    created_body.get("operator")
                )
                audit_event_id = _uuid_text(
                    created_body.get("audit_event_id"),
                    code="operator_create_audit_id_invalid",
                )
                if (
                    created_body.get("ok") is not True
                    or created_body.get("temporary_password_returned")
                    is not False
                    or created_operator["email"] != target_email
                    or created_operator["display_name"]
                    != target_display_name
                    or created_operator["status"] != "active"
                    or created_operator["role_code"] != ROLE_CODE
                    or created_operator["must_change_password"] is not True
                ):
                    raise ControlledOperationError(
                        "operator_create_response_contract_invalid"
                    )
                outcome = OperationOutcome(
                    status="created",
                    operator=created_operator,
                    audit_event_id=audit_event_id,
                    issued_secret=issued,
                )
                created_body.clear()
        except ControlledOperationError as exc:
            operation_error = exc
        except Exception:
            operation_error = ControlledOperationError(
                "official_route_operation_failed"
            )
        finally:
            try:
                logout = await client.post(
                    "/ops/auth/logout",
                    headers=authorization_headers,
                )
                session_closed = logout.status_code == 200
            except Exception:
                session_closed = False
            client.cookies.clear()
            token = ""
            device_secret = ""
            login_body.clear()

    if operation_error is not None:
        operation_error.supervisor_session_closed = bool(session_closed)
        raise operation_error
    if outcome is None:
        raise ControlledOperationError(
            "operator_operation_missing_outcome",
            supervisor_session_closed=bool(session_closed),
        )
    outcome.supervisor_session_closed = bool(session_closed)
    return outcome


def _reveal_generated_secret_once(
    secret: str,
    *,
    stream: TextIO | None = None,
) -> None:
    target = stream if stream is not None else sys.stderr
    if not target.isatty():
        raise ControlledOperationError(
            "temporary_password_requires_tty"
        )
    target.write(
        "\nNO COPIES ESTE BLOQUE EN EL CHAT, TICKETS NI LOGS.\n"
        "RTM_STAGING_TEMPORARY_PASSWORD_BEGIN\n"
        f"{secret}\n"
        "RTM_STAGING_TEMPORARY_PASSWORD_END\n"
        "Entrégala por un canal privado; deberá cambiarse en el primer acceso.\n"
    )
    target.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _base_report(args)
    blockers = _safety_blockers(os.environ)
    if blockers:
        report["blockers"] = blockers
        _print(report, compact=args.compact)
        return 2

    try:
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_core.operator_lifecycle_policy import (
            load_operator_lifecycle_runtime_config,
        )
        from rtm_core.operator_provisioning import (
            normalize_synthetic_operator_email,
        )
        from rtm_core.operator_auth_crypto import normalize_operator_email

        assert_environment_ready()
        config = load_operator_lifecycle_runtime_config(
            require_enabled=True
        )
        if not config.available:
            raise ControlledOperationError(
                "operator_lifecycle_runtime_not_available"
            )
        target_email = normalize_synthetic_operator_email(args.email)
        supervisor_email = normalize_operator_email(args.supervisor_email)
        target_display_name = " ".join(
            str(args.display_name or "").split()
        ).strip()
        if not 3 <= len(target_display_name) <= 160:
            raise ControlledOperationError(
                "operator_display_name_invalid"
            )
    except ControlledOperationError as exc:
        report["blockers"] = [exc.code]
        _print(report, compact=args.compact)
        return 2
    except Exception as exc:
        report["blockers"] = [
            f"runtime_preflight_failed:{type(exc).__name__}"
        ]
        _print(report, compact=args.compact)
        return 2

    report.update(
        {
            "supervisor_email": supervisor_email,
            "email": target_email,
            "display_name": target_display_name,
            "configuration_valid": True,
        }
    )
    if not args.apply:
        report.update(
            {
                "ok": True,
                "safe": True,
                "status": "dry_run",
                "ready_for_interactive_apply": True,
                "database_accessed": False,
                "operator_created": False,
                "password_issued": False,
                "confirmation_required": CONFIRMATION,
            }
        )
        _print(report, compact=args.compact)
        return 0

    if not _interactive_tty_ready():
        report["blockers"] = ["interactive_tty_required"]
        _print(report, compact=args.compact)
        return 2

    try:
        sys.stderr.write(
            "\nAlta sintética controlada en Render staging:\n"
            f"  supervisor: {supervisor_email}\n"
            f"  nuevo operador: {target_email}\n"
            f"  nombre: {target_display_name}\n"
            f"  rol fijo: {ROLE_CODE}\n"
        )
        supplied_confirmation = _prompt_literal(
            f"Escribe exactamente {CONFIRMATION}: "
        )
        if supplied_confirmation != CONFIRMATION:
            report["blockers"] = [
                "invalid_lifecycle_create_confirmation"
            ]
            _print(report, compact=args.compact)
            return 2
        supervisor_password = _prompt_supervisor_password()

        from app import app as rtm_app

        outcome = asyncio.run(
            _run_official_routes(
                rtm_app,
                supervisor_email=supervisor_email,
                supervisor_password=supervisor_password,
                target_email=target_email,
                target_display_name=target_display_name,
                issue_secret=_password_factory(
                    generate=bool(args.generate_password)
                ),
            )
        )
        supervisor_password = ""
    except ControlledOperationError as exc:
        report.update(
            {
                "error": exc.code,
                "http_status": exc.http_status,
                "supervisor_session_closed": (
                    exc.supervisor_session_closed
                ),
            }
        )
        _print(report, compact=args.compact)
        return 1
    except (EOFError, KeyboardInterrupt):
        report["error"] = "interactive_operation_cancelled"
        _print(report, compact=args.compact)
        return 1
    except Exception as exc:
        report["error"] = f"unexpected_error:{type(exc).__name__}"
        _print(report, compact=args.compact)
        return 1

    report.update(
        {
            "status": outcome.status,
            "operator_created": outcome.created,
            "password_issued": outcome.issued_secret is not None,
            "password_generated": bool(
                outcome.issued_secret and outcome.issued_secret.generated
            ),
            "operator": outcome.operator,
            "audit_event_id": outcome.audit_event_id,
            "supervisor_session_closed": (
                outcome.supervisor_session_closed
            ),
            "temporary_password_delivery": (
                "tty_once_after_report"
                if outcome.issued_secret
                and outcome.issued_secret.generated
                else "entered_privately_or_not_issued"
            ),
        }
    )
    report["ok"] = bool(outcome.supervisor_session_closed)
    report["safe"] = bool(outcome.supervisor_session_closed)
    if not outcome.supervisor_session_closed:
        report["blockers"] = ["supervisor_session_logout_failed"]

    _print(report, compact=args.compact)
    if outcome.issued_secret and outcome.issued_secret.generated:
        try:
            _reveal_generated_secret_once(outcome.issued_secret.value)
        finally:
            outcome.issued_secret.value = ""
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
