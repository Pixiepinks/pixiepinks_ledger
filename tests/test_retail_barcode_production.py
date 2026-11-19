"""Normal release: mocked Shopify and disposable databases only."""
import copy
from datetime import datetime,timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from alembic import command
from alembic.config import Config
from models import User
from retail_models import *
from settings import settings
import retail_service as service
import retail_barcode_sync as sync
from test_retail_barcode_sync import fixture,preview,approve,run,item,Remote
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate
from test_retail_postgres import pg
from test_retail_p1_pilot import p1


def legacy(r,p,status='QUEUED',sent=False):
    with r.sessions() as db:
        old=RetailBarcodeJob(shop=service.shop(),actor_id=r.uid,actor='admin',token='historical-'+str(db.query(RetailBarcodeJob).count()),
            status='QUEUED',api_version='2026-10',digest='old',expires_at=datetime.utcnow()+timedelta(hours=1))
        db.add(old);db.flush()
        row=RetailBarcodeItem(job_id=old.id,variant_id=p['rows'][0]['snapshot']['id'],idempotency_key='old-'+old.token,
            snapshot=copy.deepcopy(p['rows'][0]['snapshot']),before={},status=status,reason='historical',sent_at=datetime.utcnow() if sent else None)
        db.add(row);db.commit();return old.id,row.id


def test_five_item_preview_is_background_and_batch_verifies_once(fixture):
    r=fixture
    import_nodes(r,[node(i,sku='ONE' if i==1 else 'TWO' if i==2 else 'SKU'+str(i)) for i in range(1,6)])
    allocate(r,choose(r,ids=[3,4,5]))
    response=r.post('barcode-sync/preview',{'selection':choose(r,ids=[1,2,3,4,5])})
    assert response.status_code==200
    p=response.json();assert p['status']=='PREVIEW_PENDING' and p['rows']==[] and not r.remote.calls and not r.remote.writes
    assert approve(r,p).status_code in {403,409}
    for checked in range(1,6):
        assert sync.check_preview_one(r.sessions,r.remote)
        with r.sessions() as db:p=sync.report(db,db.get(RetailBarcodeJob,p['id']))
        assert p['progress']=={'checked':checked,'total':5}
        assert p['status']==('PREVIEW' if checked==5 else 'PREVIEW_PENDING')
    assert approve(r,p).status_code==200
    for _ in range(5):run(r)
    assert len(r.remote.writes)==5 and len({w['code'] for w in r.remote.writes})==5
    assert not sync.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        assert db.get(RetailBarcodeJob,p['id']).status=='COMPLETE'
        assert db.query(RetailBarcodeItem).filter_by(status='VERIFIED').count()==5


def test_old_queue_never_activates_and_requires_fresh_review(fixture):
    r=fixture;p=preview(r);jid,iid=legacy(r,p)
    assert not sync.dispatch_one(r.sessions,r.remote) and not r.remote.writes
    assert approve(r,p).status_code==409
    with r.sessions() as db:
        old=db.get(RetailBarcodeJob,jid)
        with pytest.raises(HTTPException):sync.approve(db,db.get(User,r.uid),jid,old.token,sync.phrase(old,1),r.remote)
    result=r.post('barcode-sync/retire-legacy',{'confirmation':sync.RETIRE_PHRASE})
    assert result.status_code==200 and result.json()['remaining_historical_blockers']==0
    with r.sessions() as db:
        old=db.get(RetailBarcodeJob,jid);old_item=db.get(RetailBarcodeItem,iid)
        assert old.release_version is None and old.status=='LEGACY_RETIRED'
        assert old_item.snapshot==p['rows'][0]['snapshot'] and old_item.status=='BLOCKED'
        assert db.query(RetailBarcodeEvent).filter_by(action='LEGACY_RELEASE_RETIRED').count()==1
    assert approve(r,p).status_code==200;run(r)
    assert len(r.remote.writes)==1


@pytest.mark.parametrize('has_code',[True,False])
def test_legacy_uncertain_recovery_is_read_only_even_with_flags_off(fixture,monkeypatch,has_code):
    r=fixture;p=preview(r);jid,iid=legacy(r,p,status='SENT',sent=True)
    if has_code:
        snap=p['rows'][0]['snapshot'];state=r.remote.inspect(snap);state['codes']=[snap['code']];r.remote.states[snap['variant_id']]=state
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',False)
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',False)
    assert sync.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        result=db.get(RetailBarcodeItem,iid)
        assert result.status==('VERIFIED' if has_code else 'BLOCKED')
        assert result.after['protected_comparison']=='LEGACY_BASELINE_UNAVAILABLE'
        assert db.get(RetailBarcodeJob,jid).release_version is None
        assert sync.historical_blockers(db)==0
    assert not r.remote.writes and not sync.dispatch_one(r.sessions,r.remote)


def test_protected_change_before_or_after_send_never_claims_verified(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    r.remote.protected=lambda snap:{'changed':True}
    run(r);assert not r.remote.writes and item(r).reason=='PROTECTED_ATTRIBUTES_CHANGED'


def test_completed_real_p1_cannot_enter_standard_batch(p1):
    r=p1
    with r.sessions() as db:
        user=db.get(User,r.uid);selection=service.selection(db,user,{'ids':[r.rid]})['token']
        result=sync.preview(db,user,selection);db.commit();jid=result['id']
    assert sync.check_preview_one(r.sessions,r.remote)
    with r.sessions() as db:
        result=sync.report(db,db.get(RetailBarcodeJob,jid))
        assert result['rows'][0]['reason']=='COMPLETED_P1_PILOT_CANNOT_REPLAY'
    assert not r.state.writes


def test_cancel_pending_preview_never_calls_shopify(fixture):
    r=fixture;p=r.post('barcode-sync/preview',{'selection':choose(r,ids=[1])}).json()
    assert r.post(f"barcode-sync/{p['id']}/cancel",{}).status_code==200
    assert not sync.check_preview_one(r.sessions,r.remote) and not r.remote.calls


def test_abandoned_background_claim_resumes_reads_without_duplicate_items(fixture):
    r=fixture;p=r.post('barcode-sync/preview',{'selection':choose(r,ids=[1])}).json()
    with r.sessions() as db:
        job=db.get(RetailBarcodeJob,p['id']);job.status='PREVIEW_CHECKING';job.preview_claim='dead';job.preview_lease=datetime.utcnow()-timedelta(minutes=1);db.commit()
    assert sync.check_preview_one(r.sessions,r.remote)
    assert not sync.check_preview_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.query(RetailBarcodeItem).filter_by(job_id=p['id']).count()==1
    assert not r.remote.writes


def test_pg_migration_never_grandfathers_or_allows_retagging_old_jobs(pg):
    engine,sessions,uid=pg
    with engine.begin() as db:
        for column in ['release_version','preview_selection','preview_capability','preview_claim','preview_lease']:
            db.execute(text('ALTER TABLE retail_barcode_jobs DROP COLUMN '+column))
        db.execute(text("INSERT INTO retail_barcode_jobs (shop,actor_id,actor,token,status,api_version,digest,created_at,expires_at) VALUES ('retail-pg-fixture',:uid,'admin','old','QUEUED','2026-10','old',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),{'uid':uid})
    cfg=Config('alembic.ini');command.stamp(cfg,'20261118_43');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        old=db.query(RetailBarcodeJob).one();assert old.release_version is None and old.status=='QUEUED'
        with pytest.raises(Exception):
            old.release_version=sync.RELEASE;db.commit()
        db.rollback()
        assert db.query(RetailBarcodeJob).one().release_version is None


def test_general_protected_reader_exhausts_inventory_and_images(p1):
    from retail_barcode_protection import read_protected
    from retail_barcode_p1_remote import IDENTITY
    r=p1
    names=['available','on_hand','committed','incoming','reserved','damaged','safety_stock','quality_control']
    r.state.levels=[{'id':str(i),'location':{'id':str(i)},'quantities':[{'name':n,'quantity':1} for n in names]} for i in range(102)]
    r.state.images=[{'id':str(i),'url':'https://cdn.shopify.com/image'} for i in range(103)]
    result=read_protected(r.remote,IDENTITY)
    assert len(result['inventoryItem']['inventoryLevels'])==102 and len(result['product']['images'])==103
    assert r.state.protected_calls==6 and not r.state.writes


def test_untracked_and_empty_sku_protected_values_are_preserved(p1):
    from retail_barcode_protection import read_protected
    from retail_barcode_p1_remote import IDENTITY
    r=p1;r.state.attributes['sku']=None;r.state.attributes['inventoryQuantity']=None;r.state.attributes['inventoryItem']['tracked']=False
    result=read_protected(r.remote,{**IDENTITY,'sku':''})
    assert result['sku'] is None and result['inventoryQuantity'] is None and not r.state.writes


def test_retired_verified_reservation_is_audited_and_released_without_send(fixture):
    r=fixture;p=preview(r);jid,iid=legacy(r,p,status='VERIFIED',sent=True)
    with r.sessions() as db:
        db.get(RetailBarcodeJob,jid).status='COMPLETE'
        db.add(RetailBarcodeReservation(shop=service.shop(),variant_id=p['rows'][0]['snapshot']['id'],value=p['rows'][0]['snapshot']['code'],item_id=iid));db.commit()
        assert sync.historical_blockers(db)>0
    records=r.client.get('/crm/retail/api/barcode-sync/reservations').json()
    assert records['total']==1 and records['rows'][0]['item_id']==iid
    result=r.post('barcode-sync/retire-legacy',{'confirmation':sync.RETIRE_PHRASE})
    assert result.status_code==200 and result.json()['remaining_historical_blockers']==0
    with r.sessions() as db:
        assert db.get(RetailBarcodeJob,jid).status=='COMPLETE'
        assert db.query(RetailBarcodeEvent).filter_by(action='LEGACY_RESERVATION_RELEASED').count()==1
    assert not r.remote.writes


def test_preflight_conflict_without_dispatch_cannot_be_rearmed(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    snap=p['rows'][0]['snapshot']
    r.remote.states[snap['variant_id']]={'codes':['SUPPLIER'],'complete':True,'variant_id':snap['variant_id'],'product_id':snap['product_id'],'status':'ACTIVE'}
    run(r);assert item(r).status=='CONFLICT' and item(r).sent_at is None
    assert r.post(f"barcode-sync/{p['id']}/reconcile",{}).status_code==409
    assert item(r).sent_at is None and not r.remote.writes


def test_delegated_staff_cannot_approve_production_writes(fixture):
    r=fixture;p=preview(r)
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='STAFF';user.section_access=[sync.PERMISSION];db.commit()
        with pytest.raises(HTTPException) as error:
            sync.approve(db,user,p['id'],p['approval_token'],p['confirmation'],r.remote)
        assert error.value.status_code==403
    assert not r.remote.writes


def test_pre_release_selection_requires_new_product_master_selection(fixture):
    r=fixture;token=choose(r,ids=[1])
    with r.sessions() as db:
        selection=db.get(RetailPreview,token);selection.payload={'ids':[1]};db.commit()
    response=r.post('barcode-sync/preview',{'selection':token})
    assert response.status_code==409 and 'Select products again' in response.json()['detail']
    assert not r.remote.calls and not r.remote.writes
