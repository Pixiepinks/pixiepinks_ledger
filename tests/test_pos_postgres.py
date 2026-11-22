"""Real PostgreSQL additive fences and competing POS transactions."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_retail_postgres import pg
from test_inventory_postgres import setup as inventory_setup
from test_pos import FakePOS,newcart,cash,checkout
from models import User,Account,SalesOrder,SalesPayment
from pos_models import *
from settings import settings
import inventory_shopify,pos_shopify,pos_service as s


def setup(pg,monkeypatch):
    r=inventory_setup(pg,monkeypatch)
    for key in ['POS_ENABLED','POS_CHECKOUT_WRITE_ENABLED','POS_REFUND_WRITE_ENABLED']:monkeypatch.setattr(settings,key,True)
    monkeypatch.setattr(inventory_shopify,'client',r.remote);r.pos=FakePOS(r);monkeypatch.setattr(pos_shopify,'client',r.pos)
    with r.sessions() as db:
        accounts={}
        for i,k in enumerate(['sales','discount','tax','cash','card','bank','returns']):
            a=Account(code='P'+str(i),name=k,type='ASSET' if k in ['cash','card','bank'] else 'INCOME');db.add(a);db.flush();accounts[k]=a.id
        u=db.get(User,r.uid);s.configure(db,u,{**copy.deepcopy(s.DEFAULT),'tax_mode':'NONE','accounts':accounts})
        reg=s.register(db,u,'PG POS','gid://shopify/Location/1');r.sid=s.open_register(db,u,reg.id,'10000').id
    return r


def test_pos_postgres_competing_checkout_and_workers(pg,monkeypatch):
    r=setup(pg,monkeypatch);cid,v=newcart(r);key=uuid4().hex;barrier=Barrier(2)
    def confirm(_):
        barrier.wait()
        with r.sessions() as db:return s.checkout(db,db.get(User,r.uid),cid,v,key,cash(),r.pos).id
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(confirm,range(2)))
    assert len(set(ids))==1
    barrier=Barrier(2)
    def dispatch(_):barrier.wait();return s.dispatch_one(r.sessions,r.pos)
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(dispatch,range(2)))==1
    with r.sessions() as db:
        assert db.query(POSCheckout).one().status=='COMPLETE' and db.query(POSPosting).count()==1
        assert db.query(POSOperation).one().attempts==1 and db.query(SalesPayment).count()==1
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_pos_postgres_frozen_snapshots_and_audit(pg,monkeypatch):
    r=setup(pg,monkeypatch);checkout(r);s.dispatch_one(r.sessions,r.pos)
    for sql in ["UPDATE pos_checkouts SET frozen='{}'",'UPDATE pos_operations SET attempts=0',"UPDATE sales_orders SET grand_total=1 WHERE sales_channel='POS'",'DELETE FROM pos_audits','DELETE FROM pos_cash_movements','DELETE FROM journal_lines',"UPDATE sales_payments SET amount=0 WHERE source='POS'"]:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()


def test_pos_postgres_active_register_unique(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    with r.sessions() as db:
        current=db.get(POSSession,r.sid)
        db.add(POSSession(id=str(uuid4()),shop=current.shop,register_id=current.register_id,active_register_id=current.register_id,cashier_id=r.uid,opening_float=0,status='OPEN'))
        with pytest.raises(DatabaseError):db.commit()


def test_pos_additive_migration_from_phase3_preserves_legacy(pg):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import inspect
    engine,sessions,uid=pg
    from hardware_models import HardwareEvent, HardwareJob, HardwareRate, PrintDevice, POSReceiptTest
    for model in [POSDailySale,POSDailyPolicy,POSPilotAuthorization,POSPilotScope,POSReceiptTest,HardwareEvent,HardwareJob,HardwareRate,PrintDevice]:model.__table__.drop(engine)
    for model in [POSCustomerBinding,POSPosting,POSAudit,POSCashMovement,POSRefund,POSOperation,POSCheckout,POSCart,POSSession,POSRegister,POSConfig]:model.__table__.drop(engine)
    with engine.begin() as conn:conn.execute(text('ALTER TABLE sales_orders DROP COLUMN sales_channel'))
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261119_46');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:assert db.get(User,uid).username=='retail-pg-admin'
    assert 'sales_channel' in {c['name'] for c in inspect(engine).get_columns('sales_orders')}
    assert set(['pos_checkouts','pos_audits','pos_customer_bindings'])<=set(inspect(engine).get_table_names())
