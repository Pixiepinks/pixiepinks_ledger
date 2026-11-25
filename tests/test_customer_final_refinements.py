from datetime import datetime,timezone
from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text,inspect,create_engine
from fastapi import HTTPException
from alembic import command
from alembic.config import Config
from models import Customer,User,SalesOrder
from loyalty_models import LoyaltyMember
from test_loyalty import loyalty,inbox,payload
from test_retail_postgres import pg
from test_customer_registration_postgres import config
import customer_registration as registry
from customer_display import local_datetime,lkr


def cfg():return Config(str(Path(__file__).parents[1]/'alembic.ini'))


def test_display_formatting_keeps_utc_and_money_inputs():
    value=datetime(2026,11,25,13,50,tzinfo=timezone.utc)
    assert local_datetime(value)=='25 Nov 2026, 7:20 PM' and value.hour==13
    assert local_datetime('2026-11-25T13:50:00Z')==local_datetime(value)
    assert lkr('12000')=='Rs. 12,000.00' and lkr('350.50')=='Rs. 350.50' and lkr(None)=='—'


def test_same_name_different_phone_and_reviewed_profile_edit_are_independent(loyalty):
    v=loyalty
    with v.sessions() as db:
        actor=db.get(User,v.uid)
        a,_=registry.register(db,actor,'Same legitimate name','0779876543','CRM',str(uuid4()));db.commit()
        b,_=registry.register(db,actor,'Same legitimate name','0719876543','POS',str(uuid4()));db.commit()
        assert a.id!=b.id and len(registry.search(db,'Same legitimate name')['rows'])==2
        with pytest.raises(HTTPException):registry.register(db,actor,'Different spelling','0779876543','CRM',str(uuid4()))
        db.rollback()
        p={'name':'Same legitimate name','phone':'+94719876543','email':'','fingerprint':__import__('loyalty_service').fingerprint(b)}
        registry.edit_profile(db,actor,b.id,p);db.commit()
        assert db.get(Customer,a.id).phone!=db.get(Customer,b.id).phone and db.query(LoyaltyMember).count()==0


def test_revision59_preserves_ids_orders_members_and_refuses_unsafe_rollback(pg):
    engine,sessions,uid=config(pg)
    with sessions() as db:
        c=Customer(name='Retained display name',phone='0771234567');db.add(c);db.flush();cid=c.id
        from sales_order_service import create_draft
        o,_=create_draft(db,payload(cid),'test',str(uuid4()));oid=o.id
        m=LoyaltyMember(customer_id=cid,status='PENDING',source='SYNTHETIC_BASELINE');db.add(m);db.commit();mid=m.id
    with engine.begin() as c:
        c.execute(text('DROP INDEX ix_customers_name'))
        c.execute(text('ALTER TABLE customers ADD CONSTRAINT legacy_customer_name_key UNIQUE (name)'))
    command.stamp(cfg(),'20261125_58');command.upgrade(cfg(),'20261125_59');command.upgrade(cfg(),'20261125_59')
    with sessions() as db:
        assert db.get(Customer,cid).name=='Retained display name' and db.get(SalesOrder,oid).customer_id==cid
        assert db.get(LoyaltyMember,mid).customer_id==cid
        other,_=registry.register(db,db.get(User,uid),'Retained display name','0711234567','CRM',str(uuid4()));db.commit();other_id=other.id
    with engine.connect() as c:assert not any(v['column_names']==['name'] for v in inspect(c).get_unique_constraints('customers'))
    with pytest.raises(RuntimeError,match='duplicate names exist'):command.downgrade(cfg(),'20261125_58')
    with sessions() as db:
        assert db.get(Customer,other_id).name==db.get(Customer,cid).name and db.get(SalesOrder,oid).customer_id==cid
        assert db.get(LoyaltyMember,mid).source=='SYNTHETIC_BASELINE'
    with engine.connect() as c:assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261125_59'


def test_revision59_clean_downgrade_and_reupgrade(pg):
    engine,sessions,uid=config(pg);command.stamp(cfg(),'20261125_58');command.upgrade(cfg(),'20261125_59');command.downgrade(cfg(),'20261125_58')
    with engine.connect() as c:assert any(v['column_names']==['name'] for v in inspect(c).get_unique_constraints('customers'))
    command.upgrade(cfg(),'20261125_59')
    # A migration-aware rollback may retain legacy ORM uniqueness metadata;
    # create_all(checkfirst) must not reimpose it on the existing revision59 table.
    import sqlalchemy as sa
    legacy=sa.MetaData();sa.Table('customers',legacy,sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String,unique=True));legacy.create_all(engine)
    with engine.connect() as c:assert not any(v['column_names']==['name'] for v in inspect(c).get_unique_constraints('customers'))


def test_postgres_concurrent_identical_names_keep_distinct_customer_ids(pg):
    engine,sessions,uid=config(pg)
    def run(i):
        with sessions() as db:
            c,_=registry.register(db,db.get(User,uid),'Identical display names',f'077{1000000+i:07d}','POS',str(uuid4()));db.commit();return c.id
    with ThreadPoolExecutor(4) as pool:ids=list(pool.map(run,range(8)))
    assert len(set(ids))==8
    with sessions() as db:assert db.query(Customer).count()==8 and db.query(LoyaltyMember).count()==0


def test_sqlite_legacy_unique_name_rebuild_preserves_foreign_keys(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('migration59',Path(__file__).parents[1]/'alembic/versions/20261125_59_customer_names_nonunique.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,phone TEXT)')
        c.exec_driver_sql('CREATE TABLE linked(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id))')
        c.exec_driver_sql("INSERT INTO customers VALUES(101,'Original synthetic name','0771234567')")
        c.exec_driver_sql('INSERT INTO linked VALUES(1,101)')
        with Operations.context(MigrationContext.configure(c)):
            mod.upgrade();mod.upgrade()
            c.exec_driver_sql("INSERT INTO customers VALUES(102,'Original synthetic name','0711234567')")
            with pytest.raises(RuntimeError):mod.downgrade()
        assert c.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
        assert c.exec_driver_sql('SELECT customer_id FROM linked').scalar()==101 and c.exec_driver_sql('SELECT count(*) FROM customers').scalar()==2
    engine.dispose()


def test_historical_proposal_name_is_not_an_identity_key(loyalty):
    import loyalty_recovery
    v=loyalty
    with v.sessions() as db:
        name=db.get(Customer,v.cid).name
        result=loyalty_recovery.proposal(db,{'name':name,'phone':'+94779876543','identity_conflict':False,'financial_error':None})
        assert result['can_create'] and result['suggested_customer_id'] is None
        assert db.query(Customer).count()==1


def test_same_name_memberships_remain_per_customer_not_per_name(loyalty):
    from models import SalesPayment
    import sales_order_service as orders
    import loyalty_events,loyalty_service
    v=loyalty
    with v.sessions() as db:
        for raw in ['0779876543','0719876543']:
            c,_=registry.register(db,db.get(User,v.uid),'Independent same-name member',raw,'CRM',str(uuid4()));db.flush()
            o,_=orders.create_draft(db,payload(c.id,whatsapp_phone=c.phone),'test',str(uuid4()));o.order_status='DELIVERED';o.payment_status='PAID';o.amount_paid=o.grand_total
            db.add(SalesPayment(sales_order_id=o.id,payment_method='CASH',amount=o.grand_total,currency='LKR',verification_status='VERIFIED',payment_date=datetime.utcnow().date()));db.commit()
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        members=db.query(LoyaltyMember).all();assert len(members)==2 and len({m.customer_id for m in members})==2
        for m in members:loyalty_service.refresh_member(db,m.customer_id);loyalty_service.refresh_member(db,m.customer_id)
        db.commit();assert db.query(LoyaltyMember).count()==2 and not loyalty_service.config(db).enabled


def test_member_interface_formats_colombo_time_and_lkr_without_changing_totals(loyalty):
    from test_loyalty import source
    import loyalty_service as service
    v=loyalty;source(v)
    with v.sessions() as db:
        m=service.refresh_member(db,v.cid);m.activated_at=datetime(2026,11,25,13,50);db.commit();mid=m.id
    response=v.client.get('/crm/loyalty/members/'+str(mid))
    assert response.status_code==200 and '25 Nov 2026, 7:20 PM' in response.text and 'Rs. 4,500.00' in response.text
    with v.sessions() as db:assert db.get(LoyaltyMember,mid).lifetime_net==4500 and db.get(LoyaltyMember,mid).activated_at.hour==13
