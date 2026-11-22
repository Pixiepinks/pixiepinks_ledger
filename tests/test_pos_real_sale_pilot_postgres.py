"""Disposable PostgreSQL locks, immutable approval and atomic posting+receipt."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_retail_postgres import pg
from test_hardware_postgres import setup
from test_hardware import ready as hardware_ready
from test_pos import newcart,cash
from pos_models import POSPilotScope,POSPilotAuthorization,POSOperation,POSCheckout,POSPosting
from models import User,SalesPayment,JournalEntry
from hardware_models import PrintDevice,HardwareJob
from settings import settings
import hardware_service as h
import pos_real_sale_pilot as pilot
import pos_service as service


def prepare(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    for name in ['POS_REAL_SALE_PILOT_ENABLED','PRINT_CONNECTOR_ENCRYPTED_ENABLED']:monkeypatch.setattr(settings,name,True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    with r.sessions() as db:
        u=db.get(User,r.uid);d=db.get(PrintDevice,r.did)
        h.configure(db,u,r.did,{**d.config,'receipt_only':True,'auto_receipts':False,'drawer_queue':'','drawer_verified':False,'auto_cash_movements':False,'a4_queue':''})
    hardware_ready(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);j=h.printer_test(db,u,r.did,uuid4().hex);j.status='SUBMITTED_TO_PRINTER';db.commit()
        h.review_job(db,u,j.id,True,'Mocked current version physical confirmation',False)
        pilot.configure(db,u,r.did,'Approved isolated PG target',True)
    return r


def test_competing_checkout_and_worker_one_order_posting_receipt(pg,monkeypatch):
    r=prepare(pg,monkeypatch);cid,v=newcart(r);key=uuid4().hex
    with r.sessions() as db:pilot.authorize(db,db.get(User,r.uid),cid,v,uuid4().hex,'One PG fixture genuine sale',True)
    barrier=Barrier(2)
    def capture(_):
        barrier.wait()
        with r.sessions() as db:return service.checkout(db,db.get(User,r.uid),cid,v,key,cash(),r.pos).id
    with ThreadPoolExecutor(2) as p:ids=list(p.map(capture,range(2)))
    assert ids[0]==ids[1];barrier=Barrier(2)
    def dispatch(_):barrier.wait();return service.dispatch_one(r.sessions,r.pos)
    with ThreadPoolExecutor(2) as p:assert sum(p.map(dispatch,range(2)))==1
    with r.sessions() as db:
        assert db.get(POSCheckout,ids[0]).status=='COMPLETE'
        assert db.query(HardwareJob).filter_by(kind='RECEIPT').count()==1
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==db.query(SalesPayment).count()==1
    assert len(r.pos.calls)==1


def test_additive_scope_migration_and_immutable_approvals(pg,monkeypatch):
    r=prepare(pg,monkeypatch);cid,v=newcart(r)
    # Tables already exist in metadata fixture; upgrade must be safely repeatable.
    spec=importlib.util.spec_from_file_location('pilot_migration',Path(__file__).parents[1]/'alembic/versions/20261122_51_pos_real_sale_pilot.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with pg[0].begin() as c:
        monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(c)));m.upgrade();m.upgrade();m.downgrade()
    with r.sessions() as db:pilot.authorize(db,db.get(User,r.uid),cid,v,uuid4().hex,'One approved PG cart',True)
    for sql in ["UPDATE pos_pilot_scopes SET device_id='other'",'DELETE FROM pos_pilot_scopes','UPDATE pos_pilot_authorizations SET device_version=99','DELETE FROM pos_pilot_authorizations']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()
    with r.sessions() as db:
        assert db.query(POSPilotAuthorization).count()==db.query(POSPilotScope).count()==1
        assert db.query(POSCheckout).count()==0
