"""GET-only official Marketing API client. Never log provider text/URLs/tokens."""
import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
import meta_ads_diagnostics as diagnostics

MESSAGE_ACTION = 'onsite_conversion.messaging_conversation_started_7d'
RETRY_CODES = {1, 2, 4, 17, 32, 613, 80004}


class AdsError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def account_id():
    account = os.getenv('META_ADS_ACCOUNT_ID', '').strip().removeprefix('act_')
    return 'act_' + account if re.fullmatch(r'[0-9]{1,40}', account) else None


def configuration_errors():
    errors = []
    flag = os.getenv('META_ADS_SYNC_ENABLED', '').strip().lower()
    if flag not in {'', 'true', 'false'}: errors.append('INVALID_SYNC_ENABLED')
    raw_account = os.getenv('META_ADS_ACCOUNT_ID', '').strip()
    if not raw_account: errors.append('MISSING_ACCOUNT_ID')
    elif account_id() is None: errors.append('INVALID_ACCOUNT_ID')
    token = os.getenv('META_ADS_ACCESS_TOKEN', '').strip()
    if not token: errors.append('MISSING_ACCESS_TOKEN')
    elif any(ord(c) < 33 or ord(c) > 126 for c in token) or token.startswith(('"', "'")) or token.endswith(('"', "'")):
        errors.append('INVALID_ACCESS_TOKEN_FORMAT')
    if not re.fullmatch(r'v[0-9]{1,3}\.0', os.getenv('META_ADS_GRAPH_VERSION', 'v26.0').strip()):
        errors.append('INVALID_GRAPH_VERSION')
    try:
        days = int(os.getenv('META_ADS_LOOKBACK_DAYS', '30').strip())
        if not 1 <= days <= 90: raise ValueError()
    except ValueError: errors.append('INVALID_LOOKBACK_DAYS')
    return errors


def configuration():
    errors = configuration_errors()
    if errors: raise AdsError(errors[0])
    return account_id(), os.getenv('META_ADS_GRAPH_VERSION', 'v26.0').strip(), os.getenv('META_ADS_ACCESS_TOKEN', '').strip()


def enabled():
    return os.getenv('META_ADS_SYNC_ENABLED', '').strip().lower() == 'true'


def configuration_status():
    errors = configuration_errors()
    version = os.getenv('META_ADS_GRAPH_VERSION', 'v26.0').strip()
    # No raw values for invalid settings; token presence is a boolean, never a length/hash.
    return {'enabled': enabled(), 'configured': not errors, 'errors': errors,
            'account_id': account_id(), 'token_present': bool(os.getenv('META_ADS_ACCESS_TOKEN', '').strip()),
            'graph_version': version if re.fullmatch(r'v[0-9]{1,3}\.0', version) else None}


def number(value, integer=False):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('99999999999999'):
            raise ValueError()
        if integer and result != int(result):
            raise ValueError()
        return int(result) if integer else result
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise AdsError('INVALID_METRIC') from None


class Client:
    def __init__(self, transport=None, sleep=time.sleep):
        self.account, self.version, token = configuration()
        self.http = httpx.Client(transport=transport, timeout=25, follow_redirects=False,
                                 headers={'Authorization': 'Bearer ' + token})
        self.sleep = sleep
        self.deadline = time.monotonic() + 300
        self.diagnostics = {"api_requests": 0}

    def close(self):
        self.http.close()

    def get(self, path, params=None):
        # Only fixed account paths supplied by this module; never follow provider paging URLs.
        level = (params or {}).get('level', '')
        stage = ('PERMISSIONS' if path == 'me/permissions' else 'ACCOUNT_VERIFY' if path == self.account else
                 'AD_METADATA' if path == self.account + '/ads' else 'INSIGHTS_' + level.upper())
        if stage not in diagnostics.STAGES: raise AdsError('INVALID_RESPONSE')
        self.diagnostics['stage'] = stage
        for attempt in range(4):
            if time.monotonic() > self.deadline: raise AdsError('SYNC_TIME_LIMIT')
            self.diagnostics = {'stage': stage, 'api_requests': self.diagnostics['api_requests'] + 1}
            diagnostics.log('api_request', self.diagnostics)
            try:
                response = self.http.get('https://graph.facebook.com/' + self.version + '/' + path, params=params)
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                payload = {}; response = None
            error = payload.get('error', {}) if isinstance(payload, dict) else {}
            if not isinstance(error, dict): raise AdsError('INVALID_RESPONSE')
            code = error.get('code')
            self.diagnostics.update(diagnostics.safe({'http_status': response.status_code if response is not None else None,
                'provider_code': code, 'provider_subcode': error.get('error_subcode')}))
            diagnostics.log('api_response', self.diagnostics)
            if code is not None and type(code) is not int: raise AdsError('INVALID_RESPONSE')
            if code == 190:
                raise AdsError('TOKEN_EXPIRED_OR_INVALID')
            if code in {10, 200, 294} or (response is not None and response.status_code in {401, 403}):
                raise AdsError('ADS_PERMISSION_DENIED')
            transient = response is None or response.status_code == 429 or response.status_code >= 500 or code in RETRY_CODES or error.get('is_transient')
            if transient:
                if attempt == 3:
                    raise AdsError('RATE_LIMIT' if response is not None and response.status_code == 429 or code in {4,17,32,613,80004} else 'PROVIDER_TEMPORARY')
                delay = 2 ** attempt
                if response is not None:
                    try: delay = max(delay, min(60, int(response.headers.get('Retry-After', '0'))))
                    except ValueError: pass
                self.sleep(delay)
                continue
            if response is None or response.status_code != 200 or error or not isinstance(payload, dict):
                raise AdsError('PROVIDER_REQUEST_FAILED')
            return payload
        raise AdsError('PROVIDER_TEMPORARY')

    def verify(self):
        # Explicit granted-scope check independently of the WhatsApp integration.
        permissions = self.get('me/permissions').get('data', [])
        if not isinstance(permissions, list) or any(not isinstance(p, dict) for p in permissions): raise AdsError('INVALID_RESPONSE')
        if not any(p.get('permission') == 'ads_read' and p.get('status') == 'granted' for p in permissions):
            raise AdsError('ADS_READ_REQUIRED')
        account = self.get(self.account, {'fields': 'id,currency,timezone_name,account_status'})
        if account.get('id') != self.account or not re.fullmatch(r'[A-Z]{3}', str(account.get('currency', ''))):
            raise AdsError('ACCOUNT_VERIFICATION_FAILED')
        try: ZoneInfo(account['timezone_name'])
        except (KeyError, ZoneInfoNotFoundError): raise AdsError('ACCOUNT_TIMEZONE_UNAVAILABLE') from None
        return account

    def delivery_statuses(self):
        params = {'fields': 'id,effective_status,campaign{id,effective_status},adset{id,effective_status}', 'limit': 500}
        result = {}; seen = set()
        for _ in range(200):
            page = self.get(self.account + '/ads', params)
            if not isinstance(page.get('data'), list): raise AdsError('INVALID_RESPONSE')
            for ad in page['data']:
                for level, value in [('ad', ad), ('adset', ad.get('adset') or {}), ('campaign', ad.get('campaign') or {})]:
                    if re.fullmatch(r'[0-9]{1,40}', str(value.get('id',''))) and re.fullmatch(r'[A-Z_]{1,64}', str(value.get('effective_status',''))):
                        result[(level, value['id'])] = value['effective_status']
            paging = page.get('paging') or {}
            if not paging.get('next'): return result
            after = (paging.get('cursors') or {}).get('after')
            if not isinstance(after,str) or after in seen or len(after)>4096: raise AdsError('INVALID_PAGINATION')
            seen.add(after); params['after'] = after
        raise AdsError('PAGINATION_LIMIT')

    def insights(self, level, start, end):
        import json
        fields = ['account_id', 'account_currency', 'campaign_id', 'campaign_name', 'date_start', 'date_stop',
                  'spend', 'impressions', 'reach', 'inline_link_clicks', 'actions']
        if level in {'adset', 'ad'}: fields += ['adset_id', 'adset_name']
        if level == 'ad': fields += ['ad_id', 'ad_name']
        params = {'fields': ','.join(fields), 'level': level, 'time_increment': 1, 'limit': 500,
                  'time_range': json.dumps({'since': start.isoformat(), 'until': end.isoformat()}),
                  'action_report_time': 'impression', 'action_attribution_windows': json.dumps(['7d_click', '1d_view'])}
        seen = set()
        for _ in range(2000):
            page = self.get(self.account + '/insights', params)
            if not isinstance(page.get('data'), list): raise AdsError('INVALID_RESPONSE')
            yield from page['data']
            paging = page.get('paging') or {}
            if not paging.get('next'): return
            after = (paging.get('cursors') or {}).get('after')
            if not isinstance(after, str) or len(after) > 4096 or after in seen: raise AdsError('INVALID_PAGINATION')
            seen.add(after); params['after'] = after
        raise AdsError('PAGINATION_LIMIT')


def parse(row, level, account, currency, start, end, now):
    try:
        day = date.fromisoformat(row['date_start']); stop = date.fromisoformat(row['date_stop'])
    except (ValueError, KeyError, TypeError): raise AdsError('INVALID_RESPONSE') from None
    entity = row.get(level + '_id')
    if not isinstance(entity, str) or not re.fullmatch(r'[0-9]{1,40}', entity): raise AdsError('INVALID_RESPONSE')
    if row.get('account_id') != account.removeprefix('act_') or row.get('account_currency') != currency:
        raise AdsError('CURRENCY_OR_ACCOUNT_MISMATCH')
    if not start <= day <= end or stop != day: raise AdsError('INVALID_REPORT_DATE')
    raw_actions = row.get('actions')
    if raw_actions is not None and not isinstance(raw_actions, list): raise AdsError('INVALID_RESPONSE')
    actions = {}
    for action in raw_actions or []:
        key = action.get('action_type')
        if not isinstance(key, str) or len(key) > 160 or key in actions: raise AdsError('INVALID_RESPONSE')
        value = number(action.get('value'))
        actions[key] = str(value) if value is not None else None
    return dict(account_id=account, level=level, entity_id=entity, report_date=day, currency=currency,
                **{key: str(row[key])[:500] if row.get(key) is not None else None for key in
                   ('campaign_id','campaign_name','adset_id','adset_name','ad_id','ad_name')},
                spend=number(row.get('spend')), impressions=number(row.get('impressions'), True),
                reach=number(row.get('reach'), True), link_clicks=number(row.get('inline_link_clicks'), True),
                messaging=number(actions.get(MESSAGE_ACTION)), actions=actions if raw_actions is not None else None,
                source_start=day, source_stop=stop, synced_at=now)
