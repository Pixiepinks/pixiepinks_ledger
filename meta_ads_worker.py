"""Daily, bounded read-only synchronization. Can share an existing cron process."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from meta_ads_models import MetaAdsDaily, MetaAdsSync
from meta_ads_client import Client, AdsError, configuration, account_id, configuration_errors, enabled, parse
import meta_ads_diagnostics as diagnostics


def state(db, account):
    row = db.get(MetaAdsSync, account)
    if row is None:
        row = MetaAdsSync(account_id=account, status='NEVER'); db.add(row)
        try: db.commit()
        except IntegrityError: db.rollback()
    return db.get(MetaAdsSync, account)


def preflight():
    errors = configuration_errors()
    if 'INVALID_SYNC_ENABLED' in errors: raise AdsError('INVALID_SYNC_ENABLED')
    if not enabled(): raise AdsError('SYNC_DISABLED')
    return configuration()[0]


def active(row, now=None):
    return bool(row.status == 'RUNNING' and row.started_at and
                row.started_at > (now or datetime.utcnow())-timedelta(minutes=30))


def rejected(db, error):
    code = diagnostics.error_code(error.code)
    evidence = {'stage':'PREFLIGHT','api_requests':0}
    diagnostics.log('preflight_rejected', evidence, code)
    account = account_id()
    if account:
        row = state(db, account)
        if not active(row):
            row.status = 'DISABLED' if code == 'SYNC_DISABLED' else 'CONFIG_ERROR'
            row.error_code = code; row.diagnostics = evidence; row.started_at = datetime.utcnow()
            row.requested_at = None; db.commit()


def request_sync(db):
    try: account = preflight()
    except AdsError as exc:
        rejected(db, exc); raise
    row = state(db, account)
    if active(row):
        diagnostics.log('already_running', {'stage':'LOCK','api_requests':0}, 'RUNNING_LEASE_ACTIVE')
        return 'RUNNING'
    row.requested_at = datetime.utcnow(); row.status = 'QUEUED'; row.error_code = None
    row.diagnostics = {'stage':'PREFLIGHT','api_requests':0}; row.started_at = None; db.commit()
    diagnostics.log('queued', {'stage':'PREFLIGHT','api_requests':0})
    return 'QUEUED'


def tick(sessions, force=False, client_factory=Client):
    # A disabled scheduled integration leaves the database untouched. Manual rejection is recorded.
    if os.getenv('META_ADS_SYNC_ENABLED', '').strip().lower() in {'', 'false'}:
        diagnostics.log('disabled', {'stage':'PREFLIGHT','api_requests':0}, 'SYNC_DISABLED')
        return 'DISABLED'
    lock = None
    try:
        with sessions() as db:
            try: account = preflight()
            except AdsError as exc:
                rejected(db, exc)
                return 'DISABLED' if exc.code == 'SYNC_DISABLED' else 'ERROR'
            state(db, account)
            now = datetime.utcnow()
            if db.get_bind().dialect.name == 'postgresql':
                lock = db.get_bind().connect()
                if not lock.execute(text('SELECT pg_try_advisory_lock(714206272)')).scalar():
                    lock.close(); lock = None
                    diagnostics.log('lock_busy', {'stage':'LOCK','api_requests':0}, 'ADVISORY_LOCK_BUSY')
                    row = db.get(MetaAdsSync, account)
                    if not active(row):
                        row.status = 'BUSY'; row.error_code = 'ADVISORY_LOCK_BUSY'
                        row.diagnostics = {'stage':'LOCK','api_requests':0}; db.commit()
                    return 'BUSY'
            row = db.get(MetaAdsSync, account)
            if not force and not row.requested_at and row.last_success_at and row.last_success_at > now-timedelta(hours=24): return 'IDLE'
            count = db.query(MetaAdsSync).filter(MetaAdsSync.account_id == account,
                or_(MetaAdsSync.status != 'RUNNING', MetaAdsSync.started_at.is_(None),
                    MetaAdsSync.started_at <= now-timedelta(minutes=30))).update(
                    {'status':'RUNNING','started_at':now,'error_code':None,'requested_at':None,
                     'diagnostics':{'stage':'PREFLIGHT','api_requests':0}}, synchronize_session=False)
            db.commit()
            if not count:
                diagnostics.log('lease_busy', {'stage':'LOCK','api_requests':0}, 'RUNNING_LEASE_ACTIVE')
                return 'BUSY'
            client = None; phase = 'PREFLIGHT'
            try:
                client = client_factory(); phase = 'PERMISSIONS'; info = client.verify()
                today = datetime.now(ZoneInfo(info['timezone_name'])).date()
                days = int(os.getenv('META_ADS_LOOKBACK_DAYS', '30').strip())
                start, end = today-timedelta(days=days-1), today
                phase = 'AD_METADATA'
                try: delivery = client.delivery_statuses()
                except AdsError: delivery = {}
                values = []; seen = set()
                for level in ('campaign','adset','ad'):
                    phase = 'INSIGHTS_' + level.upper()
                    for raw in client.insights(level, start, end):
                        phase = 'VALIDATE'
                        value = parse(raw, level, account, info['currency'], start, end, now)
                        value['delivery_status'] = delivery.get((level, value['entity_id']))
                        key = (level, value['entity_id'], value['report_date'])
                        if key in seen: raise AdsError('DUPLICATE_INSIGHT')
                        seen.add(key); values.append(value)
                        if len(values) > 150000: raise AdsError('REPORT_TOO_LARGE')
                        phase = 'INSIGHTS_' + level.upper()
                phase = 'PUBLISH'
                row = db.get(MetaAdsSync, account); db.refresh(row)
                if row.status != 'RUNNING' or row.started_at != now: raise AdsError('SYNC_LEASE_LOST')
                db.query(MetaAdsDaily).filter(MetaAdsDaily.account_id == account,
                    MetaAdsDaily.report_date >= start, MetaAdsDaily.report_date <= end).delete(synchronize_session=False)
                if values: db.bulk_insert_mappings(MetaAdsDaily, values)
                row.currency = info['currency']; row.timezone_name = info['timezone_name']
                row.status = 'SUCCESS'; row.last_success_at = datetime.utcnow(); row.error_code = None
                row.date_from = start; row.date_to = end; row.requested_at = None
                row.diagnostics = {**diagnostics.safe(getattr(client,'diagnostics',{})),
                                   'stage':'COMPLETE','published_rows':len(values)}
                db.commit(); diagnostics.log('completed', row.diagnostics); return 'SUCCESS'
            except Exception as exc:
                evidence = diagnostics.safe(getattr(client,'diagnostics',{}))
                if phase in {'PREFLIGHT','VALIDATE','PUBLISH'} or 'stage' not in evidence: evidence['stage'] = phase
                code = diagnostics.error_code(exc.code if isinstance(exc, AdsError) else None)
                diagnostics.log('failed', evidence, code)
                db.rollback(); row = db.get(MetaAdsSync, account); db.refresh(row)
                if row.started_at == now:
                    row.status = 'ERROR'; row.error_code = code; row.diagnostics = evidence
                    row.requested_at = None; db.commit()
                return 'ERROR'
            finally:
                if client: client.close()
    finally:
        if lock is not None:
            try: lock.execute(text('SELECT pg_advisory_unlock(714206272)'))
            finally: lock.close()


def main():
    from database import SessionLocal
    try:
        result = tick(SessionLocal)
        print('meta_ads status=' + result)
        if result == 'ERROR': raise SystemExit(2)
    except Exception: print('meta_ads status=UNAVAILABLE'); raise SystemExit(2) from None


if __name__ == '__main__': main()
