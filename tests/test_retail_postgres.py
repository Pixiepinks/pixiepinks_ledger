"""Disposable PostgreSQL migration and multi-session identifier tests."""
import os
from uuid import uuid4
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from pathlib import Path
from models import Base,User,Item
from retail_models import *
import retail_service as service
import retail_sync as sync
import database
import main
from test_retail_phase1 import node,Reader


@pytest.fixture
def pg(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Dedicated disposable TEST_POSTGRESQL_URL not configured')
    parsed=make_url(url)
    assert parsed.host in {'127.0.0.1','localhost'} and parsed.database=='pixiepinks_test'
    admin=create_engine(url);schema='retail_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine,expire_on_commit=False)
        monkeypatch.setattr(database,'engine',engine);monkeypatch.setattr(main.settings,'RETAIL_ENABLED',True);monkeypatch.setattr(main.settings,'SHOPIFY_SHOP','retail-pg-fixture')
        with sessions() as db:
            user=User(username='retail-pg-admin',password_hash='not-a-real-login',role='ADMIN');db.add(user);db.add(Item(name='Existing accounting item',sku='UNCHANGED'));db.commit();uid=user.id
        yield engine,sessions,uid
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_additive_migration_and_import_does_not_reject_legacy_duplicates(pg):
    engine,sessions,uid=pg
    models=[RetailShop,RetailVariant,RetailSyncJob,RetailImportRow,RetailCounter,RetailIdentifier,RetailPreview,RetailAudit,RetailException,RetailLabelTemplate,RetailPrintJob,RetailBarcodeJob,RetailBarcodeItem,RetailBarcodeReservation,RetailBarcodeEvent]
    # Reconstruct the pre-retail schema in dependency order, including additive Phase 3 tables.
    from inventory_models import InventoryDocument, InventoryEvent, InventoryObservation, InventoryPolicy, InventoryPolicyEvent
    models += [InventoryDocument, InventoryEvent, InventoryObservation, InventoryPolicy, InventoryPolicyEvent]
    from hardware_models import PrintDevice, HardwareJob, HardwareEvent, HardwareRate, POSReceiptTest
    models += [PrintDevice, HardwareJob, HardwareEvent, HardwareRate, POSReceiptTest]
    for model in reversed(models):model.__table__.drop(engine)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(cfg,'20261111_41');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.get(User,uid).username=='retail-pg-admin' and db.query(Item).one().sku=='UNCHANGED'
        job=sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';rid=job.id;db.commit()
    sync.run_sync(sessions,rid,Reader([([node(1,sku='DUP',barcode='SAME'),node(2,sku='DUP',barcode='SAME')],False)]))
    with sessions() as db:
        assert db.get(RetailSyncJob,rid).status=='COMPLETE'
        assert db.query(RetailVariant).filter_by(barcode='SAME').count()==2
        job=sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';rid=job.id;db.commit()
    sync.run_sync(sessions,rid,Reader([([node(1,barcode='SAME')],False)]))
    with sessions() as db:assert db.get(RetailSyncJob,rid).status=='COMPLETE' and db.query(RetailVariant).filter_by(missing=True).count()==1


def test_database_backed_counter_concurrency_and_idempotency(pg):
    _,sessions,uid=pg
    with sessions() as db:
        job=sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';rid=job.id;db.commit()
    sync.run_sync(sessions,rid,Reader([([node(1),node(2)],False)]))
    previews=[]
    for rid in [1,2]:
        with sessions() as db:
            user=db.get(User,uid);token=service.selection(db,user,{'ids':[rid]})['token'];db.commit()
        with sessions() as db:
            result=service.allocate_preview(db,db.get(User,uid),token,'barcode');db.commit();previews.append(result['token'])
    def commit(token):
        with sessions() as db:
            try:result=service.allocate_commit(db,db.get(User,uid),token,True);db.commit();return result
            except HTTPException as error:db.rollback();return error.status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(commit,previews))
    assert sum(isinstance(result,dict) for result in results)==1 and 409 in results
    with sessions() as db:
        committed=db.query(RetailPreview).filter_by(kind='ALLOCATION',committed=True).one();token=committed.token
        assert db.query(RetailIdentifier).one().value=='P1'
    assert commit(token)['committed'] is True
    with sessions() as db:
        assert db.query(RetailIdentifier).count()==1 and db.get(RetailCounter,(service.shop(),'barcode')).value==1
        with pytest.raises(Exception):
            db.add(RetailIdentifier(shop=service.shop(),variant_id=2,kind='barcode',value='P1',actor='attempt'));db.flush()
        db.rollback()
