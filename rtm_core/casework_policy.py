"""Select the production pilot or an explicitly enabled, isolated TEST target.

The existing staging target requires its exact resource identities and preflight.
Production policy remains unchanged; TEST cannot use production resources.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from rtm_core import production_casework as production
from rtm_core.production_casework import CaseworkConfigurationError, CaseworkPolicy, permits_case, permits_session

TEST_FLAG = 'RTM_ENABLE_PROVIDER_TEST_CASEWORK'
TEST_ORIGIN = 'https://rtm-journey-test-ui.onrender.com'
TEST_API = 'rtm-journey-test-api'
TEST_WORKER = 'rtm-journey-test-mail'
TEST_DATABASE = 'rtm_journey_test'
STAGING_SERVICE = 'srv-d9v1t2qjnfac73a14kug'
STAGING_DATABASE_HOST = 'dpg-d9v2lab7uimc73da0j9g-a'
STAGING_ORIGIN = 'https://recurretumulta-frontendweb3-8-26-git-r-cbbb3a-soluzzzs-projects.vercel.app'
STAGING_BUCKET = 'recurretumulta-rtm-staging'
_OFF = {'', '0', 'false', 'off', 'no'}
_EXISTING_SERVICES = {'srv-d5fu32hr0fns73c3oa0g', 'srv-d9v1t2qjnfac73a14kug'}
_EXISTING_DATABASES = {'dpg-d5ftkbqli9vc7390hni0-a', 'dpg-d9v2lab7uimc73da0j9g-a'}
_LIVE_CASE = '2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee'


def test_requested(source):
    return str(source.get(TEST_FLAG) or '').strip().lower() not in _OFF


def requested(source):
    return production.requested(source) or test_requested(source)


def validate_test_boundary(source, *, worker=False):
    def require(condition, message):
        if not condition:raise CaseworkConfigurationError(message)
    require(not production.requested(source), 'Production and TEST profiles cannot coexist')
    require(source.get('RTM_ENV') == 'staging' and source.get('RTM_ENVIRONMENT_CONFIRMATION') == 'RTM_STAGING_ISOLATED', 'Explicit isolated staging identity required')
    require(source.get('RTM_SIDE_EFFECT_POLICY') == 'isolated' and source.get('RTM_DOCUMENT_INPUT_POLICY') == 'synthetic_only', 'Synthetic isolated inputs required')
    for key in ('RTM_ENABLE_LOCAL_OPERATOR_AUTH', 'RTM_ALLOW_REAL_CUSTOMER_DATA', 'RTM_ENABLE_EXTERNAL_SUBMISSION', 'RTM_ENABLE_FINAL_PAYMENTS', 'RTM_ENABLE_OUTBOUND_EMAIL'):
        require(str(source.get(key) or '').strip().lower() in _OFF, 'TEST forbids '+key)
    target = source.get('RTM_PROVIDER_TEST_TARGET', 'new_stack')
    require(target in {'new_stack', 'existing_staging'}, 'Explicit known TEST target required')
    service_id = source.get('RENDER_SERVICE_ID', '')
    try:
        db = urlsplit(source.get('DATABASE_URL', ''))
        port = db.port
    except ValueError:raise CaseworkConfigurationError('TEST database identity invalid') from None
    expected = source.get('RTM_PROVIDER_TEST_DATABASE_HOST', '')
    if target == 'existing_staging':
        if worker:
            require(source.get('RTM_PROVIDER_TEST_WORKER_MODE') == 'embedded'
                    and source.get('RTM_PROVIDER_TEST_EMBEDDED_CHILD') == '1',
                    'Existing staging worker requires the managed embedded process')
        require(service_id == STAGING_SERVICE and source.get('RENDER_SERVICE_NAME') == 'recurretumulta-backend-1', 'Exact existing staging service required')
        require(expected == STAGING_DATABASE_HOST, 'Exact existing staging database host required')
        require(source.get('B2_BUCKET') == STAGING_BUCKET and source.get('RTM_PRODUCTION_B2_BUCKET') == 'recurretumulta-prod', 'Exact existing staging custody required')
        require(source.get('ALLOWED_ORIGINS') == STAGING_ORIGIN, 'Exact existing staging CORS origin required')
        from rtm_core.environment_contract import build_environment_preflight
        require(build_environment_preflight(source).safe, 'Existing staging environment preflight must pass')
        database, origin = 'rtm_staging', STAGING_ORIGIN
    else:
        require(source.get('RENDER_SERVICE_NAME') == (TEST_WORKER if worker else TEST_API), 'Separate TEST service required')
        require(service_id.startswith('srv-') and service_id not in _EXISTING_SERVICES, 'Existing RTM service cannot host the new-stack profile')
        require(expected.startswith('dpg-') and expected not in _EXISTING_DATABASES and '.' not in expected, 'Separate TEST database host required')
        database, origin = TEST_DATABASE, TEST_ORIGIN
    require(db.scheme in {'postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'} and db.hostname == expected and db.path == '/'+database
            and port in (None, 5432) and not db.query and not db.fragment, 'Exact internal TEST database required')
    require(source.get('FRONTEND_URL') == origin and source.get('RTM_CASEWORK_PUBLIC_ORIGIN') == origin, 'Exact TEST frontend required')
    require(source.get('RTM_STRIPE_MODE') == 'test' and source.get('RTM_MANAGEMENT_PAYMENT_MODE') == 'test', 'TEST payment mode required')
    for key in ('STRIPE_SECRET_KEY', 'RTM_MANAGEMENT_STRIPE_TEST_KEY'):
        require(source.get(key, '').startswith(('sk_test_', 'rk_test_')), 'TEST-only Stripe credentials required')
    require(not source.get('RTM_MANAGEMENT_STRIPE_LIVE_WEBHOOK_SECRET'), 'LIVE reconciliation secret forbidden in TEST')
    bucket = source.get('B2_BUCKET', '')
    require(bucket == source.get('RTM_PROVIDER_TEST_B2_BUCKET') and bool(bucket) and ('staging' in bucket or 'test' in bucket), 'Explicit TEST custody bucket required')
    require(bucket != source.get('RTM_PRODUCTION_B2_BUCKET'), 'Production custody forbidden in TEST')
    return origin


def origin_allowed(source, origin, *, worker=False):
    if origin in production.PUBLIC_CASEWORK_ORIGINS:
        return not test_requested(source)
    if origin not in {TEST_ORIGIN, STAGING_ORIGIN}:return False
    try:expected = validate_test_boundary(source, worker=worker)
    except CaseworkConfigurationError:return False
    return origin == expected


def load_policy(source, *, now=None, service_name='recurretumulta-backend'):
    if not test_requested(source):
        return production.load_policy(source, now=now, service_name=service_name)
    if source.get(TEST_FLAG) != '1':raise CaseworkConfigurationError('Explicit TEST activation required')
    if service_name not in {'recurretumulta-backend', 'rtm-case-mail-pilot'}:
        raise CaseworkConfigurationError('Unknown TEST process role')
    origin = validate_test_boundary(source, worker=service_name == 'rtm-case-mail-pilot')
    cases = production._identifiers(source, 'RTM_CASEWORK_ALLOWED_CASE_IDS', 1)
    if _LIVE_CASE in cases:raise CaseworkConfigurationError('LIVE case forbidden in TEST')
    operators = production._identifiers(source, 'RTM_CASEWORK_ALLOWED_OPERATOR_IDS', 2)
    try:
        expires = datetime.fromisoformat(str(source.get('RTM_CASEWORK_EXPIRES_AT') or '').replace('Z', '+00:00'))
        remaining = (expires - (now or datetime.now(timezone.utc))).total_seconds()
        if expires.tzinfo is None or not 0 < remaining <= 7*24*3600:raise ValueError()
    except (ValueError, TypeError):raise CaseworkConfigurationError('TEST access requires future expiry within seven days') from None
    return CaseworkPolicy(cases, operators, origin, expires)
