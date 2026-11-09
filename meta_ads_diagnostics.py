"""Allowlisted operational diagnostics; never provider text, headers or secrets."""
import logging

logger = logging.getLogger('meta_ads')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    logger.addHandler(handler)
HINTS = {
    'SYNC_DISABLED': 'Set META_ADS_SYNC_ENABLED=true on the web service and redeploy that service. Configure the cron service separately.',
    'INVALID_SYNC_ENABLED': 'Use true or false for META_ADS_SYNC_ENABLED, without quotation marks.',
    'MISSING_ACCOUNT_ID': 'Set META_ADS_ACCOUNT_ID on this service.',
    'INVALID_ACCOUNT_ID': 'Use the numeric ad account ID, optionally prefixed act_, without quotation marks.',
    'MISSING_ACCESS_TOKEN': 'Set the separate META_ADS_ACCESS_TOKEN secret on this service. Do not share its value.',
    'INVALID_ACCESS_TOKEN_FORMAT': 'Remove quotation marks or embedded whitespace from the Ads token in Railway. Do not share its value.',
    'INVALID_GRAPH_VERSION': 'Set META_ADS_GRAPH_VERSION to a supported version such as v26.0, without quotation marks.',
    'INVALID_LOOKBACK_DAYS': 'Set META_ADS_LOOKBACK_DAYS to an integer from 1 to 90.',
    'TOKEN_EXPIRED_OR_INVALID': 'Reauthorize the separate Ads token in Meta and update Railway securely.',
    'ADS_PERMISSION_DENIED': 'Check ads_read, Meta app access level and assignment to this ad account.',
    'ADS_READ_REQUIRED': 'The token permissions response did not grant ads_read. Check the separate Ads token and app access.',
    'ACCOUNT_VERIFICATION_FAILED': 'Meta did not return the configured account and a valid currency. Check ad account assignment.',
    'ACCOUNT_TIMEZONE_UNAVAILABLE': 'Meta did not return a supported account timezone.',
    'PROVIDER_REQUEST_FAILED': 'Meta rejected the read request. Use the request stage and numeric provider code to diagnose app/version/field access.',
    'RATE_LIMIT': 'Meta rate limit persisted after bounded retries; retry later.',
    'PROVIDER_TEMPORARY': 'The provider request failed after bounded retries; check connectivity and retry later.',
    'INVALID_RESPONSE': 'Meta returned an unsupported response shape. No refreshed facts were published.',
    'SYNC_INTERRUPTED': 'The worker stopped before publication. Previous valid data was retained; check database and worker availability.',
    'SYNC_LEASE_LOST': 'Another worker reclaimed this run. Its previous cached facts were retained.',
    'SYNC_TIME_LIMIT': 'The bounded sync time was exceeded. Previous valid data was retained.',
    'ADVISORY_LOCK_BUSY': 'Another PostgreSQL worker holds the Ads lock. Wait for it to finish; do not delete reporting data.',
    'RUNNING_LEASE_ACTIVE': 'A sync already started within the last 30 minutes. Wait for completion.',
}
ERROR_CODES = set(HINTS) | {'INVALID_METRIC', 'INVALID_REPORT_DATE', 'CURRENCY_OR_ACCOUNT_MISMATCH',
                          'DUPLICATE_INSIGHT', 'REPORT_TOO_LARGE', 'INVALID_PAGINATION', 'PAGINATION_LIMIT'}

STAGES = {'PREFLIGHT', 'LOCK', 'PERMISSIONS', 'ACCOUNT_VERIFY', 'AD_METADATA', 'INSIGHTS_CAMPAIGN',
          'INSIGHTS_ADSET', 'INSIGHTS_AD', 'VALIDATE', 'PUBLISH', 'COMPLETE'}


def error_code(code):
    return code if isinstance(code, str) and code in ERROR_CODES else 'SYNC_INTERRUPTED'


def hint(code):
    return HINTS.get(code, 'Previous valid data was retained. Review the safe error code and request stage.')


def safe(values):
    """Copy only fixed categories and bounded numeric evidence, even for injected clients."""
    values = values if isinstance(values, dict) else {}
    result = {}
    if isinstance(values.get('stage'), str) and values['stage'] in STAGES: result['stage'] = values['stage']
    for key in ('api_requests', 'http_status', 'provider_code', 'provider_subcode', 'published_rows'):
        value = values.get(key)
        if type(value) is int and 0 <= value <= 2**63-1: result[key] = value
    return result


def log(event, values=None, error=None):
    # Event and error are supplied by our code, never taken from provider messages.
    logger.info('meta_ads event=%s evidence=%s error=%s', event, safe(values), error_code(error) if error else 'NONE')
