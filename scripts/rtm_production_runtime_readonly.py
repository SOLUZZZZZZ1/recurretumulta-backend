"""Production metadata only. Standard library; no app, DB, HTTP or mutations."""
from datetime import datetime, timezone
import json
import os
import re
from urllib.parse import urlsplit

SERVICE = 'srv-d5fu32hr0fns73c3oa0g'
DB_HOST = 'dpg-d5ftkbqli9vc7390hni0-a'
FLAGS = (
    'RTM_ENABLE_PRODUCTION_CASEWORK', 'RTM_ENABLE_MANAGEMENT_JOURNEY',
    'RTM_ENABLE_MANAGEMENT_RECONCILIATION', 'RTM_ENABLE_CASE_MAIL',
    'RTM_CASE_MAIL_WORKER_ENABLED', 'RTM_MANAGEMENT_GENERATION_WORKER_ENABLED',
    'RTM_MANAGEMENT_DRAFT_READY', 'RTM_MANAGEMENT_MAIL_VERIFIED',
    'RTM_CASE_MAIL_DEDICATED_CONFIRMED',
)
CONFIGURED = (
    'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET',
    'STRIPE_PRICE_ID_REVIEW_BASIC', 'RTM_MANAGEMENT_STRIPE_ACCOUNT_ID',
    'RTM_MANAGEMENT_STRIPE_LIVE_WEBHOOK_SECRET',
    'RTM_CASE_MAIL_PASSWORD', 'RTM_CASE_MAIL_SMTP_HOST', 'RTM_CASE_MAIL_IMAP_HOST',
    'RTM_CASE_MAIL_ALLOWED_HOSTS', 'RTM_CASE_MAIL_ALLOWED_RECIPIENTS',
    'RTM_CASE_MAIL_AUTHSERV_ID', 'RTM_CASE_MAIL_DELIVERY_SENDERS',
    'OPENAI_API_KEY', 'RTM_MANAGEMENT_GENERATION_MODEL', 'OPENAI_MODEL',
    'RTM_MANAGEMENT_TAX_BASIS_POINTS', 'RTM_MANAGEMENT_TAX_LABEL',
    'RTM_CASEWORK_ALLOWED_CASE_IDS', 'RTM_CASEWORK_ALLOWED_OPERATOR_IDS',
)


def inspect(source=None):
    source = os.environ if source is None else source
    out = {'checked_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
           'scope': 'Configuration booleans only; no app import, DB, network or mutation',
           'activation_ready': False}
    exact = source.get('RENDER_SERVICE_ID') == SERVICE and source.get('RENDER_SERVICE_NAME') == 'recurretumulta-backend'
    out['exact_production_service'] = exact
    if not exact:
        out.update(ok=False, error_code='EXACT_PRODUCTION_SERVICE_REQUIRED')
        return out
    out['production_environment'] = source.get('RTM_ENV') == 'production'
    try:
        db = urlsplit(source.get('DATABASE_URL', ''))
        out['expected_database'] = (db.scheme in {'postgres', 'postgresql', 'postgresql+psycopg', 'postgresql+psycopg2'}
            and (db.hostname == DB_HOST or (db.hostname or '').startswith(DB_HOST + '.'))
            and db.path == '/recurretumulta' and db.port in (None, 5432) and not db.query and not db.fragment)
    except ValueError:
        out['expected_database'] = False
    sha = source.get('RENDER_GIT_COMMIT', '')
    out['running_commit'] = sha if re.fullmatch('[0-9a-f]{40}', sha) else 'unavailable'
    out['flags_enabled'] = {key: source.get(key) == '1' for key in FLAGS}
    out['configured'] = {key: bool(source.get(key)) for key in CONFIGURED}
    out['stripe_key_is_live'] = source.get('STRIPE_SECRET_KEY', '').startswith(('sk_live_', 'rk_live_'))
    out['management_mode_is_live'] = source.get('RTM_MANAGEMENT_PAYMENT_MODE') == 'live'
    out['live_webhook_prefix_valid'] = source.get('RTM_MANAGEMENT_STRIPE_LIVE_WEBHOOK_SECRET', '').startswith('whsec_')
    out['management_account_matches_known_business'] = source.get('RTM_MANAGEMENT_STRIPE_ACCOUNT_ID') == 'acct_1SLgSlGv82bS4KLY'
    out['production_bucket_matches'] = source.get('B2_BUCKET') == 'recurretumulta-prod'
    out['origin_matches'] = {key: source.get(key) == 'https://www.recurretumulta.eu'
                             for key in ('FRONTEND_URL', 'RTM_CASEWORK_PUBLIC_ORIGIN')}
    try:
        expiry = datetime.fromisoformat(source.get('RTM_CASEWORK_EXPIRES_AT', '').replace('Z', '+00:00'))
        out['scope_not_expired'] = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        out['scope_not_expired'] = False
    out['existing_supervisor_in_scope'] = 'dca14cf0-bc54-409e-93cf-53098511b503' in source.get('RTM_CASEWORK_ALLOWED_OPERATOR_IDS', '').split(',')
    out['ok'] = True
    out['limit'] = 'Does not verify Stripe account, delivery, webhook processing, generation, current case or browser journey.'
    return out


if __name__ == '__main__':
    print('RTM_PRODUCTION_RUNTIME_READONLY ' + json.dumps(inspect(), sort_keys=True))
