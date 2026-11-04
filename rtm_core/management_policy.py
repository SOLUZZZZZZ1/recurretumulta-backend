"""An explicit extension of the existing per-case, time-limited OPS profile."""
from dataclasses import dataclass
import os
from rtm_core.production_casework import load_policy
from rtm_core.trusted_origins import PUBLIC_CASEWORK_ORIGINS
from rtm_core.management_contract import ManagementRejected


@dataclass(frozen=True, repr=False)
class ManagementPolicy:
    mode: str
    account_id: str
    api_key: str
    webhook_secret: str
    case_id: str
    origin: str
    tax_basis_points: int
    tax_label: str
    agreed_total_cents: int | None


def configuration(source=None, *, for_notification=False, mode=None):
    source = os.environ if source is None else source
    enabled=source.get('RTM_ENABLE_MANAGEMENT_JOURNEY')=='1'
    reconciliation=source.get('RTM_ENABLE_MANAGEMENT_RECONCILIATION')=='1'
    if not enabled and not (for_notification and reconciliation):
        raise ManagementRejected('MANAGEMENT_JOURNEY_NOT_ENABLED', 503)
    selected = source.get('RTM_MANAGEMENT_PAYMENT_MODE')
    if selected not in {'test', 'live'} or (mode is not None and mode != selected):
        raise ManagementRejected('MANAGEMENT_PAYMENT_MODE_MISMATCH', 503)
    # Notifications can reconcile an existing obligation after OPS access expires;
    # they never create a checkout, extend access, generate or submit a resource.
    if not for_notification:
        try:
            existing = load_policy(source)
        except ValueError:
            raise ManagementRejected('MANAGEMENT_ACCESS_NOT_ACTIVE', 403) from None
        if not existing:
            raise ManagementRejected('MANAGEMENT_ACCESS_NOT_ACTIVE', 403)
        case_id, origin = existing.case_ids[0], existing.origin
    else:
        from uuid import UUID
        try:
            case_id = str(UUID(source.get('RTM_CASEWORK_ALLOWED_CASE_IDS', '')))
        except ValueError:
            raise ManagementRejected('MANAGEMENT_CASE_SCOPE_INVALID', 503) from None
        origin = source.get('RTM_CASEWORK_PUBLIC_ORIGIN')
    if origin not in PUBLIC_CASEWORK_ORIGINS:
        raise ManagementRejected('MANAGEMENT_ORIGIN_INVALID', 503)
    account = source.get('RTM_MANAGEMENT_STRIPE_ACCOUNT_ID', '')
    key = source.get('RTM_MANAGEMENT_STRIPE_TEST_KEY', '') if selected == 'test' else source.get('STRIPE_SECRET_KEY', '')
    if not account.startswith('acct_') or not key.startswith(('sk_' + selected + '_', 'rk_' + selected + '_')):
        raise ManagementRejected('MANAGEMENT_STRIPE_CONFIGURATION_REQUIRED', 503)
    secret = source.get('RTM_MANAGEMENT_STRIPE_' + selected.upper() + '_WEBHOOK_SECRET', '')
    if not secret.startswith('whsec_'):
        raise ManagementRejected('MANAGEMENT_WEBHOOK_CONFIGURATION_REQUIRED', 503)
    if not for_notification and (source.get('RTM_MANAGEMENT_DRAFT_READY') != '1' or source.get('RTM_MANAGEMENT_MAIL_VERIFIED') != '1'):
        raise ManagementRejected('CONNECTED_DELIVERY_NOT_READY', 503)
    try:
        tax = int(source['RTM_MANAGEMENT_TAX_BASIS_POINTS'])
        label = source['RTM_MANAGEMENT_TAX_LABEL'].strip()
        agreed = int(source['RTM_MANAGEMENT_AGREED_TOTAL_CENTS']) if source.get('RTM_MANAGEMENT_AGREED_TOTAL_CENTS') else None
        if not 0 <= tax <= 10000 or not label or (agreed is not None and agreed < 3900):
            raise ValueError()
    except (ValueError, KeyError):
        raise ManagementRejected('MANAGEMENT_TAX_OR_TOTAL_UNVERIFIED', 503) from None
    # The agreed production case is never eligible for synthetic TEST obligations.
    if case_id == '2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee' and (selected != 'live' or agreed != 3900):
        raise ManagementRejected('EXISTING_LIVE_CASE_CONTRACT_MISMATCH', 503)
    return ManagementPolicy(selected, account, key, secret, case_id, origin, tax, label, agreed)
