"""Explicit paid-study pilot boundary. No credentials, writes or service startup.

This adds a production profile; it never reclassifies production as local or
staging. Existing profiles keep their original guards. Disabled by default.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

FLAG = 'RTM_ENABLE_PRODUCTION_CASEWORK'
_OFF = {'', '0', 'false', 'off', 'no'}


class CaseworkConfigurationError(ValueError):
    """Sanitized configuration failure; never includes values or credentials."""


def requested(source: Mapping[str, str]) -> bool:
    return str(source.get(FLAG) or '').strip().lower() not in _OFF


def _identifiers(source, name, maximum):
    raw = str(source.get(name) or '').strip().split(',')
    try:
        values = tuple(str(UUID(value.strip())) for value in raw)
    except (ValueError, AttributeError):
        raise CaseworkConfigurationError(name + ' requires explicit UUIDs') from None
    if not 1 <= len(values) <= maximum or len(set(values)) != len(values):
        raise CaseworkConfigurationError(name + ' has an invalid scope')
    return values


@dataclass(frozen=True)
class CaseworkPolicy:
    case_ids: tuple[str, ...]
    operator_ids: tuple[str, ...]
    origin: str
    expires_at: datetime


def load_policy(source: Mapping[str, str], *, now=None) -> CaseworkPolicy | None:
    if not requested(source):
        return None
    if str(source.get(FLAG) or '').strip() != '1':
        raise CaseworkConfigurationError('Explicit production activation required')
    if source.get('RTM_ENV') != 'production':
        raise CaseworkConfigurationError('Production identity required')
    if str(source.get('RTM_ENABLE_LOCAL_OPERATOR_AUTH') or '').strip().lower() not in _OFF:
        raise CaseworkConfigurationError('Local and production profiles cannot coexist')
    if source.get('RENDER_SERVICE_NAME') != 'recurretumulta-backend':
        raise CaseworkConfigurationError('Public backend identity mismatch')
    origin = source.get('RTM_CASEWORK_PUBLIC_ORIGIN')
    if origin != 'https://www.recurretumulta.eu':
        raise CaseworkConfigurationError('Public frontend identity mismatch')
    try:
        expiry = datetime.fromisoformat(str(source.get('RTM_CASEWORK_EXPIRES_AT') or '').replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
            raise ValueError()
    except (ValueError, TypeError):
        raise CaseworkConfigurationError('A valid future pilot expiry is required') from None
    return CaseworkPolicy(
        case_ids=_identifiers(source, 'RTM_CASEWORK_ALLOWED_CASE_IDS', 1),
        operator_ids=_identifiers(source, 'RTM_CASEWORK_ALLOWED_OPERATOR_IDS', 2),
        origin=origin, expires_at=expiry,
    )


def permits_session(policy, session) -> bool:
    return bool(policy and session
                and str(session.operator_id) in policy.operator_ids
                and not session.must_change_password and not session.mfa_required
                and session.role_code in {'rtm.operator', 'rtm.supervisor'}
                and 'ops.view' in session.permissions)


def permits_case(policy, session, case_id, *, write=False) -> bool:
    return bool(permits_session(policy, session) and case_id in policy.case_ids
                and (not write or 'ops.supervise' in session.permissions))
