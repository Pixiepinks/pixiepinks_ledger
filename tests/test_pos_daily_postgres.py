"""Disposable PostgreSQL daily scope/receipt identity and financial concurrency."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from pathlib import Path
import importlib.util
import pytest
from sqlalchemy import text,inspect
from sqlalchemy.exc import DatabaseError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_retail_postgres import pg
from test_hardware_postgres import setup
from test_hardware import ready
from test_pos import newcart,cash
from models import User,JournalEntry,SalesPayment
from pos_models import POSDailyPolicy,POSDailySale,POSCheckout,POSOperation,POSPosting,POSPilotAuthorization
from hardware_models import HardwareJob,PrintDevice
from settings import settings
import pos_daily as daily
import pos_service as s
import hardware_service as h


def prepare(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    for k in ['POS_DAILY_OPERATIONS_ENABLED','PRINT_CONNECTOR_ENCRYPTED_ENABLED']:monkeypatch.setattr(settings,k,True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    with r.sessions() as db:
        u=db.get(User,r.uid);d=db.get(PrintDevice,r.did)
        h.configure(db,u,r.did,{**d.config,'receipt_only':True,'receipt_profile':'80','auto_receipts':False,'auto_cash_movements':False,'drawer_queue':'','drawer_verified':False,'a4_queue':''})
    ready(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);j=h.printer_test(db,u,r.did,uuid4().hex);j.status='SUBMITTED_TO_PRINTER';db.commit();h.review_job(db,u,j.id,True,'Mock physical acceptance',False)
        daily.configure(db,u,r.did,True,'Scoped daily PostgreSQL release',True)
    return r


def test_competing_cashier_checkout_and_workers_one_original(pg,monkeypatch):
    r=prepare(pg,monkeypatch);cid,v=newcart(r);key=uuid4().hex;barrier=Barrier(2)
    def checkout(_):
        barrier.wait()
        with r.sessions() as db:return s.checkout(db,db.get(User,r.uid),cid,v,key,cash(),r.pos).id
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(checkout,range(2)))
    assert ids[0]==ids[1];barrier=Barrier(2)
    def dispatch(_):barrier.wait();return s.dispatch_one(r.sessions,r.pos)
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(dispatch,range(2)))==1
    h.tick(r.sessions)
    with r.sessions() as db:
        assert db.get(POSCheckout,ids[0]).status=='COMPLETE'
        assert db.query(POSDailySale).count()==1 and db.query(POSPilotAuthorization).count()==0
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==db.query(SalesPayment).count()==1
        assert db.query(HardwareJob).filter_by(kind='RECEIPT').count()==1 and db.query(HardwareJob).filter_by(kind='DRAWER').count()==0
    assert len(r.pos.calls)==1


def test_additive_daily_migration_and_immutable_sale_binding(pg,monkeypatch):
    r=prepare(pg,monkeypatch)
    spec=importlib.util.spec_from_file_location('daily_migration',Path(__file__).parents[1]/'alembic/versions/20261122_52_daily_pos.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with pg[0].begin() as c:
        monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(c)));m.upgrade();m.upgrade();m.downgrade()
        assert {'pos_daily_policies','pos_daily_sales'}<=set(inspect(c).get_table_names())
    cid,v=newcart(r)
    with r.sessions() as db:rid=s.checkout(db,db.get(User,r.uid),cid,v,uuid4().hex,cash(),r.pos).id
    for sql in ["UPDATE pos_daily_sales SET device_id='other'",'UPDATE pos_daily_sales SET device_version=99','DELETE FROM pos_daily_sales','DELETE FROM pos_daily_policies']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()
    with r.sessions() as db:assert db.query(POSDailySale).count()==1 and db.get(POSCheckout,rid).status=='QUEUED'
