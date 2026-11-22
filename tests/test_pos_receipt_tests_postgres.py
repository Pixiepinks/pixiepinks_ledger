"""Real disposable PostgreSQL concurrency, additive migration and audit fences."""
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
from models import User,SalesOrder,SalesPayment,JournalEntry
from hardware_models import POSReceiptTest,HardwareJob,HardwareEvent,PrintDevice
from pos_models import POSCheckout,POSOperation
from settings import settings
import hardware_service as h
import pos_receipt_tests as tests


def prepare(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    monkeypatch.setattr(settings,'POS_TEST_CHECKOUT_ENABLED',True)
    monkeypatch.setattr(settings,'PRINT_CONNECTOR_ENCRYPTED_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    with r.sessions() as db:
        d=db.get(PrintDevice,r.did)
        h.configure(db,db.get(User,r.uid),r.did,{**d.config,'receipt_only':True,'receipt_profile':'80','auto_receipts':False,'auto_cash_movements':False,'drawer_queue':'','drawer_verified':False,'a4_queue':''})
    return r


def test_concurrent_create_and_finalize_one_non_financial_job(pg,monkeypatch):
    r=prepare(pg,monkeypatch);key=uuid4().hex;barrier=Barrier(2)
    def create(_):
        barrier.wait()
        with r.sessions() as db:return tests.create(db,db.get(User,r.uid),r.did,key).id
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(create,range(2)))
    assert ids[0]==ids[1];barrier=Barrier(2)
    def finalize(_):
        barrier.wait()
        with r.sessions() as db:return tests.finalize(db,db.get(User,r.uid),ids[0],True).job_id
    with ThreadPoolExecutor(2) as pool:jobs=list(pool.map(finalize,range(2)))
    assert jobs[0]==jobs[1]
    with r.sessions() as db:
        assert db.query(POSReceiptTest).count()==1 and db.query(HardwareJob).count()==1
        assert db.query(HardwareEvent).filter_by(action='TEST_CHECKOUT_FINALIZED').count()==1
        for m in [POSCheckout,POSOperation,SalesOrder,SalesPayment,JournalEntry]:assert db.query(m).count()==0
    assert not r.pos.calls and not r.remote.mutations


def test_additive_migration_and_immutable_test_identity(pg,monkeypatch):
    r=prepare(pg,monkeypatch);engine=pg[0]
    spec=importlib.util.spec_from_file_location('pos_receipt_test_migration',Path(__file__).parents[1]/'alembic/versions/20261122_50_pos_receipt_tests.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    POSReceiptTest.__table__.drop(engine)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        migration.upgrade();migration.upgrade();migration.downgrade()
        assert 'pos_receipt_tests' in inspect(conn).get_table_names()
    with r.sessions() as db:
        tid=tests.create(db,db.get(User,r.uid),r.did,uuid4().hex).id
        jid=tests.finalize(db,db.get(User,r.uid),tid,True).job_id
    for sql in ["UPDATE pos_receipt_tests SET frozen='{}'","UPDATE pos_receipt_tests SET status='DRAFT'",'UPDATE pos_receipt_tests SET job_id=NULL','DELETE FROM pos_receipt_tests']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()
    with r.sessions() as db:
        assert db.get(HardwareJob,jid).attempts==0
        assert db.get(PrintDevice,r.did).config['auto_receipts'] is False
