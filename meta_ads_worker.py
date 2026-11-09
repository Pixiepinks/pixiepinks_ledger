"""Daily, bounded read-only synchronization. Can share an existing cron process."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from meta_ads_models import MetaAdsDaily, MetaAdsSync
from meta_ads_client import Client, AdsError, configuration, enabled, parse


def state(db, account):
    row = db.get(MetaAdsSync, account)
    if row is None:
        row = MetaAdsSync(account_id=account, status='NEVER'); db.add(row)
        try: db.commit()
        except IntegrityError: db.rollback()
    return db.get(MetaAdsSync, account)


def request_sync(db):
    if not enabled(): raise AdsError('SYNC_DISABLED')
    account, _, _ = configuration()
    row = state(db, account)
    if row.status == 'RUNNING' and row.started_at and row.started_at > datetime.utcnow()-timedelta(minutes=30):
        return 'RUNNING'
    row.requested_at = datetime.utcnow(); db.commit()
    return 'QUEUED'


def tick(sessions, force=False, client_factory=Client):
    if not enabled(): return 'DISABLED'
    account, _, _ = configuration()
    lock = None
    try:
        with sessions() as db:
            state(db, account)
            now = datetime.utcnow()
            if db.get_bind().dialect.name == 'postgresql':
                lock = db.get_bind().connect()
                if not lock.execute(text('SELECT pg_try_advisory_lock(714206272)')).scalar():
                    lock.close(); lock = None; return 'BUSY'
            row = db.get(MetaAdsSync, account)
            if not force and not row.requested_at and row.last_success_at and row.last_success_at > now-timedelta(hours=24): return 'IDLE'
            count = db.query(MetaAdsSync).filter(MetaAdsSync.account_id == account,
                or_(MetaAdsSync.status != 'RUNNING', MetaAdsSync.started_at < now-timedelta(minutes=30))).update(
                    {'status':'RUNNING','started_at':now,'error_code':None,'requested_at':None}, synchronize_session=False)
            db.commit()
            if not count: return 'BUSY'
            client = None
            try:
                client = client_factory(); info = client.verify()
                today = datetime.now(ZoneInfo(info['timezone_name'])).date()
                days = max(1, min(90, int(os.getenv('META_ADS_LOOKBACK_DAYS', '30'))))
                start, end = today-timedelta(days=days-1), today
                try: delivery = client.delivery_statuses()
                except AdsError: delivery = {}
                values = []; seen = set()
                for level in ('campaign','adset','ad'):
                    for raw in client.insights(level, start, end):
                        value = parse(raw, level, account, info['currency'], start, end, now)
                        value['delivery_status'] = delivery.get((level, value['entity_id']))
                        key = (level, value['entity_id'], value['report_date'])
                        if key in seen: raise AdsError('DUPLICATE_INSIGHT')
                        seen.add(key); values.append(value)
                        if len(values) > 150000: raise AdsError('REPORT_TOO_LARGE')
                # Fence a reclaimed lease before touching the last valid facts.
                row = db.get(MetaAdsSync, account); db.refresh(row)
                if row.status != 'RUNNING' or row.started_at != now: raise AdsError('SYNC_LEASE_LOST')
                db.query(MetaAdsDaily).filter(MetaAdsDaily.account_id == account,
                    MetaAdsDaily.report_date >= start, MetaAdsDaily.report_date <= end).delete(synchronize_session=False)
                if values: db.bulk_insert_mappings(MetaAdsDaily, values)
                row.currency = info['currency']; row.timezone_name = info['timezone_name']
                row.status = 'SUCCESS'; row.last_success_at = datetime.utcnow(); row.error_code = None
                row.date_from = start; row.date_to = end; row.requested_at = None
                db.commit(); return 'SUCCESS'
            except Exception as exc:
                db.rollback(); row = db.get(MetaAdsSync, account); db.refresh(row)
                if row.started_at == now:
                    row.status = 'ERROR'; row.error_code = exc.code if isinstance(exc, AdsError) else 'SYNC_INTERRUPTED'
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
