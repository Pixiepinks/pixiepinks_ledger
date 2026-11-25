"""Real disposable PostgreSQL; mock-free SQL concurrency, never production."""
import os
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config
from models import Base,Customer,User,SalesOrder,SalesPayment
from retail_models import RetailVariant
from loyalty_models import *
import database
import loyalty_service as svc
import loyalty_events
import sales_order_service as orders
from test_loyalty import line,payload

TABLES=(LoyaltyConfig,LoyaltyMember,LoyaltyRule,LoyaltyRuleVersion,LoyaltyPricing,LoyaltyRedemption,LoyaltyRedemptionEvent,LoyaltyAudit,LoyaltyIdentityReview,LoyaltyOrderIdentity,LoyaltyMigration,LoyaltyMigrationRow,LoyaltyRecalculation,LoyaltyOrderExclusion)
@pytest.fixture
def pg(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Disposable TEST_POSTGRESQL_URL not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='loyalty_'+uuid4().hex
    with admin.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema});sessions=sessionmaker(bind=engine,expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        for table in reversed(TABLES):table.__table__.drop(engine)
        monkeypatch.setattr(database,'engine',engine);monkeypatch.setattr(svc.settings,'SHOPIFY_SHOP','loyalty-pg')
        cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261123_54')
        with sessions() as db:
            user=User(username='loyalty-admin',password_hash='retained-hash',role='ADMIN');customer=Customer(name='Preserved customer',phone='0771234567');db.add_all([user,customer]);db.commit();uid=user.id;cid=customer.id
            order,_=orders.create_draft(db,payload(cid),'fixture','preserved-order')
            order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=4500
            db.add(SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=4500,currency='LKR',payment_date=datetime.utcnow().date(),verification_status='VERIFIED'))
            variant=RetailVariant(shop='loyalty-pg',product_id='gid://shopify/Product/1',variant_id='gid://shopify/ProductVariant/1',title='School bag',variant_title='Pink',price=4500,currency='LKR',product_status='ACTIVE',missing=False,stale=False,options=[],collections=[]);db.add(variant);db.commit();oid=order.id;rid=variant.id
        command.upgrade(cfg,'20261124_55');command.upgrade(cfg,'20261124_55')
        yield SimpleNamespace(engine=engine,sessions=sessions,cfg=cfg,uid=uid,cid=cid,oid=oid,rid=rid)
    finally:
        engine.dispose()
        with admin.begin() as c:c.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()


def test_additive_migration_and_rollback_preserve_legacy(pg):
    v=pg
    with v.sessions() as db:
        assert not svc.config(db).enabled
        assert db.get(SalesOrder,v.oid).grand_total==4500 and db.get(User,v.uid).password_hash=='retained-hash'
        assert db.get(RetailVariant,v.rid).price==4500
        svc.refresh_member(db,v.cid);db.commit()
    with v.engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261124_55'
        assert inspect(c).get_unique_constraints('loyalty_members')[0]['column_names']==['customer_id']
    command.downgrade(v.cfg,'20261123_54')
    with v.engine.connect() as c:
        assert 'loyalty_members' not in inspect(c).get_table_names()
        assert c.execute(text('SELECT amount_paid FROM sales_orders WHERE id=:id'),{'id':v.oid}).scalar()==4500
        assert c.execute(text('SELECT password_hash FROM users WHERE id=:id'),{'id':v.uid}).scalar()=='retained-hash'
    command.upgrade(v.cfg,'20261124_55')
    with v.sessions() as db:assert not svc.config(db).enabled


def test_concurrent_activation_one_membership_one_activation_audit(pg):
    def activate(_):
        with pg.sessions() as db:
            m=svc.refresh_member(db,pg.cid,'management');db.commit();return m.membership_id
    with ThreadPoolExecutor(6) as pool:ids=list(pool.map(activate,range(12)))
    assert len(set(ids))==1 and ids[0].startswith('PP-L-')
    with pg.sessions() as db:
        assert db.query(LoyaltyMember).count()==1
        assert db.query(LoyaltyAudit).filter_by(kind='MEMBERSHIP_RECALCULATED').count()==1


def test_concurrent_rule_overlap_and_immutable_versions(pg):
    data={'variant_ids':[pg.rid],'method':'PRICE','value':'4000','cost':'2000','cost_reference':'Verified bill','cost_verified_at':datetime.utcnow().isoformat(),'enabled':True}
    def save(_):
        from fastapi import HTTPException
        with pg.sessions() as db:
            try:svc.save_rules(db,db.get(User,pg.uid),data);db.commit();return 200
            except HTTPException as exc:db.rollback();return exc.status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(save,range(2)))
    assert sorted(results)==[200,409]
    with pg.sessions() as db:
        assert db.query(LoyaltyRule).count()==1 and db.query(LoyaltyRuleVersion).count()==1
        version=db.query(LoyaltyRuleVersion).one();version.value={'tamper':True}
        with pytest.raises(DBAPIError):db.commit()
        db.rollback()


def test_restartable_batched_migration_concurrent_execution(pg):
    with pg.sessions() as db:
        for i in range(104):
            customer=Customer(name=f'Historical customer {i}',phone='+1416555'+f'{i:04d}');db.add(customer);db.flush()
            order,_=orders.create_draft(db,payload(customer.id,whatsapp_phone=customer.phone),'fixture','historical-'+str(i),commit=False)
            order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=4500
            db.add(SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=4500,currency='LKR',payment_date=datetime.utcnow().date(),verification_status='VERIFIED'))
        db.commit();user=db.get(User,pg.uid);job=svc.preview_migration(db,user,{},'postgres-historical-batches');db.commit()
        assert db.query(LoyaltyMember).count()==0
        assert job.counts['new_proposed']==105
        svc.approve_migration(db,user,job.id,job.digest,'Approved local records and identity checks');db.commit();jid=job.id;token=job.digest
    def execute(_):
        with pg.sessions() as db:
            svc.execute_migration_batch(db,db.get(User,pg.uid),jid,token);db.commit()
    # Separate sessions model interruption/restart; competing execution remains idempotent.
    execute(0)
    with pg.sessions() as db:assert db.get(LoyaltyMigration,jid).status=='RUNNING' and db.query(LoyaltyMember).count()==100
    with ThreadPoolExecutor(2) as pool:list(pool.map(execute,range(2)))
    with pg.sessions() as db:
        assert db.get(LoyaltyMigration,jid).status=='COMPLETE' and db.query(LoyaltyMember).count()==105
        assert db.query(LoyaltyMigrationRow).filter_by(status='EXECUTED').count()==105 and not svc.config(db).enabled


def test_freeze_and_worker_no_duplicate_redemption(pg):
    now=datetime.utcnow()
    with pg.sessions() as db:
        user=db.get(User,pg.uid);svc.config(db).enabled=True
        svc.save_rules(db,user,{'variant_ids':[pg.rid],'method':'PRICE','value':'4000','cost':'2000','cost_reference':'Verified bill','cost_verified_at':now.isoformat(),'enabled':True})
        prepared=svc.prepare_order(db,payload(pg.cid),actor='loyalty-admin');order,_=orders.create_draft(db,prepared,'loyalty-admin','postgres-priced-order');oid=order.id
    def freeze(_):
        with pg.sessions() as db:
            svc.freeze_order(db,db.get(SalesOrder,oid),[line()],'loyalty-admin');db.commit()
    with ThreadPoolExecutor(2) as pool:list(pool.map(freeze,range(2)))
    with pg.sessions() as db:
        assert db.query(LoyaltyRedemption).count()==1 and db.get(LoyaltyPricing,oid).frozen
        order=db.get(SalesOrder,oid);order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=4000
        db.add(SalesPayment(sales_order_id=oid,payment_method='CASH',amount=4000,currency='LKR',payment_date=now.date(),verification_status='VERIFIED'));db.commit()
    with ThreadPoolExecutor(4) as pool:list(pool.map(lambda _:loyalty_events.process_one(pg.sessions),range(8)))
    with pg.sessions() as db:
        assert db.query(LoyaltyRedemptionEvent).filter_by(kind='FINALIZED').count()==1
        assert svc.member_summary(db,pg.cid)['lifetime_savings']=='500.00'
        saved=db.get(LoyaltyPricing,oid);saved.value={'tampered':True}
        with pytest.raises(DBAPIError):db.commit()
        db.rollback()
