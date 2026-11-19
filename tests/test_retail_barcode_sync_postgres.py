"""Local PostgreSQL migration, durable fencing and concurrent worker validation."""
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi import HTTPException
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from models import User,Item
from retail_models import *
import retail_service as service
import retail_sync as imports
import retail_barcode_sync as sync
from test_retail_postgres import pg
from test_retail_phase1 import node,Reader
from test_retail_barcode_sync import Remote


def setup(pg,monkeypatch):
    engine,sessions,uid=pg
    from settings import settings
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',True);monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
    with sessions() as db:
        job=imports.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';rid=job.id;db.commit()
    imports.run_sync(sessions,rid,Reader([([node(1,sku='ONE'),node(2,sku='TWO')],False)]))
    with sessions() as db:
        user=db.get(User,uid);token=service.selection(db,user,{'ids':[1]})['token'];db.commit()
        p=service.allocate_preview(db,user,token,'barcode');service.allocate_commit(db,user,p['token'],True);db.commit()
    remote=Remote()
    with sessions() as db:
        result=sync.preview(db,db.get(User,uid),token,remote);db.commit()
    for _ in range(5):
        if not sync.check_preview_one(sessions,remote):break
    with sessions() as db:result=sync.report(db,db.get(RetailBarcodeJob,result['id']))
    return sessions,uid,remote,result


def approve(sessions,uid,remote,p):
    with sessions() as db:
        job=db.get(RetailBarcodeJob,p['id'])
        if job.status in {'PREVIEW_PENDING','PREVIEW_CHECKING'}:
            db.rollback()
            for _ in range(5):
                if not sync.check_preview_one(sessions,remote):break
            db.expire_all();p=sync.report(db,db.get(RetailBarcodeJob,p['id']))
        result=sync.approve(db,db.get(User,uid),p['id'],p['approval_token'],p['confirmation'],remote);db.commit();return result


def test_pg_two_workers_dispatch_once_and_repeat_approval(pg,monkeypatch):
    sessions,uid,remote,p=setup(pg,monkeypatch);approve(sessions,uid,remote,p)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:sync.dispatch_one(sessions,remote),range(2)))
    assert sum(results)==1 and len(remote.writes)==1
    assert approve(sessions,uid,remote,p)['status']=='COMPLETE'
    with sessions() as db:assert db.query(RetailBarcodeItem).one().status=='VERIFIED'


def test_pg_import_and_allocation_invalidate_review_and_preserve_local(pg,monkeypatch):
    sessions,uid,remote,p=setup(pg,monkeypatch)
    with sessions() as db:
        user=db.get(User,uid);row=db.get(RetailVariant,1);row.sku=None;row.version+=1
        token=service.selection(db,user,{'ids':[1]})['token'];p=sync.preview(db,user,token,remote);db.commit()
        allocation=service.allocate_preview(db,user,token,'sku',category='OTH');db.commit()
    approve(sessions,uid,remote,p)
    with sessions() as db:service.allocate_commit(db,db.get(User,uid),allocation['token'],True);db.commit()
    assert sync.dispatch_one(sessions,remote) and not remote.writes
    with sessions() as db:
        assert db.query(RetailBarcodeItem).filter_by(job_id=p['id']).one().reason=='LOCAL_SNAPSHOT_CHANGED'
        assert db.get(RetailVariant,1).local_barcode=='P1' and db.get(RetailVariant,1).local_sku


def test_pg_additive_migration_repeatability_immutable_events_and_snapshots(pg,monkeypatch):
    engine,sessions,uid=pg
    for model in [RetailBarcodeEvent,RetailBarcodeReservation,RetailBarcodeItem,RetailBarcodeJob]:model.__table__.drop(engine)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(cfg,'20261117_42');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    sessions,uid,remote,p=setup(pg,monkeypatch)
    with sessions() as db:
        assert db.get(User,uid) and db.query(Item).one().sku=='UNCHANGED'
        with pytest.raises(Exception):db.execute(text('DELETE FROM retail_barcode_events'));db.commit()
        db.rollback()
        with pytest.raises(Exception):db.execute(text("UPDATE retail_barcode_items SET snapshot='{}'"));db.commit()
        db.rollback()
    approve(sessions,uid,remote,p);assert sync.dispatch_one(sessions,remote)
    command.downgrade(cfg,'20261117_42')
    with sessions() as db:assert db.query(RetailBarcodeItem).one().status=='VERIFIED' and db.query(RetailBarcodeEvent).count()>0


def test_pg_two_approvals_same_variant_reserve_only_once(pg,monkeypatch):
    sessions,uid,remote,p=setup(pg,monkeypatch)
    with sessions() as db:
        user=db.get(User,uid);token=service.selection(db,user,{'ids':[1]})['token'];other=sync.preview(db,user,token,remote);db.commit()
    def submit(batch):
        try:approve(sessions,uid,remote,batch);return 200
        except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(submit,[p,other]))
    assert sorted(results)==[200,409]
    with sessions() as db:assert db.query(RetailBarcodeReservation).count()==1


def test_pg_import_waits_for_dispatch_boundary(pg,monkeypatch):
    from threading import Event
    sessions,uid,remote,p=setup(pg,monkeypatch);approve(sessions,uid,remote,p)
    entered=Event();release=Event();imported=Event();original=remote.write_empty
    def write(snap,protected_before=None,not_after=None):
        entered.set();assert release.wait(10);original(snap)
    remote.write_empty=write
    def publish():
        with sessions() as db:
            job=imports.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';jid=job.id;db.commit()
        imports.run_sync(sessions,jid,Reader([([node(1,sku='ONE',barcode='P1'),node(2,sku='TWO')],False)]));imported.set()
    with ThreadPoolExecutor(2) as pool:
        dispatch=pool.submit(sync.dispatch_one,sessions,remote);assert entered.wait(10)
        waiting=pool.submit(publish)
        assert not imported.wait(.1)
        release.set();assert dispatch.result(timeout=15);waiting.result(timeout=15)
    with sessions() as db:
        assert db.query(RetailBarcodeItem).one().status=='VERIFIED'
        assert db.get(RetailVariant,1).barcode=='P1' and db.get(RetailVariant,1).local_barcode=='P1'
    assert len(remote.writes)==1


def test_pg_approver_revocation_waits_for_authorized_dispatch_boundary(pg,monkeypatch):
    from threading import Event
    sessions,uid,remote,p=setup(pg,monkeypatch);approve(sessions,uid,remote,p)
    entered=Event();release=Event();revoking=Event();revoked=Event();original=remote.write_empty
    def write(snap,protected_before=None,not_after=None):
        entered.set();assert release.wait(10);original(snap)
    remote.write_empty=write
    def revoke():
        with sessions() as db:
            revoking.set();db.get(User,uid).is_active=False;db.commit();revoked.set()
    with ThreadPoolExecutor(2) as pool:
        dispatch=pool.submit(sync.dispatch_one,sessions,remote);assert entered.wait(10)
        pending=pool.submit(revoke);assert revoking.wait(10)
        assert not revoked.wait(.1)
        release.set();assert dispatch.result(timeout=15);pending.result(timeout=15)
    with sessions() as db:
        assert not db.get(User,uid).is_active
        assert db.query(RetailBarcodeItem).one().status=='VERIFIED'
    assert len(remote.writes)==1
