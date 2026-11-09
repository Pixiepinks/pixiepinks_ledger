"""GET-only official Marketing API client. Never log provider text/URLs/tokens."""
import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx

MESSAGE_ACTION = 'onsite_conversion.messaging_conversation_started_7d'
RETRY_CODES = {1, 2, 4, 17, 32, 613, 80004}


class AdsError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def account_id():
    account = os.getenv('META_ADS_ACCOUNT_ID', '').removeprefix('act_')
    return 'act_' + account if re.fullmatch(r'[0-9]{1,40}', account) else None


def configuration():
    account = account_id()
    version = os.getenv('META_ADS_GRAPH_VERSION', 'v26.0')
    token = os.getenv('META_ADS_ACCESS_TOKEN', '')
    if not account or not re.fullmatch(r'v[0-9]{1,3}\.0', version) or not token:
        raise AdsError('NOT_CONFIGURED')
    return account, version, token


def enabled():
    return os.getenv('META_ADS_SYNC_ENABLED', '').lower() == 'true'


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

    def close(self):
        self.http.close()

    def get(self, path, params=None):
        # Only fixed account paths supplied by this module; never follow provider paging URLs.
        for attempt in range(4):
            if time.monotonic() > self.deadline: raise AdsError('SYNC_TIME_LIMIT')
            try:
                response = self.http.get('https://graph.facebook.com/' + self.version + '/' + path, params=params)
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                payload = {}; response = None
            error = payload.get('error', {}) if isinstance(payload, dict) else {}
            code = error.get('code')
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
