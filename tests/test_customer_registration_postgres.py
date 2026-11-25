"""Disposable PostgreSQL, concurrency-safe registration and additive revision 58."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from pathlib import Path
import pytest
from fastapi import HTTPException
from sqlalchemy import text,inspect
from alembic import command
from alembic.config import Config
from test_retail_postgres import pg
from models import Customer,User,CustomerRegistration,CustomerPhoneLock,SalesOrder,SalesPayment
from loyalty_models import LoyaltyConfig,LoyaltyMember,LoyaltyAudit,LoyaltyRecalculation,LoyaltyOrderIdentity
from test_loyalty import payload
import customer_registration as registry
import loyalty_service as svc
import loyalty_events
import sales_order_service as orders


def config(v):
    engine,sessions,uid=v
    with sessions() as db:
        if not svc.config(db):db.add(LoyaltyConfig(id=1,enabled=False,version=1,value=svc.DEFAULTS));db.commit()
    return engine,sessions,uid


def test_revision58_preserves_existing_identity_membership_and_orders(pg):
    engine,sessions,uid=config(pg)
    with sessions() as db:
        c=Customer(name='Retained synthetic buyer',phone='0771111111');db.add(c);db.flush();cid=c.id
        order,_=orders.create_draft(db,payload(cid,whatsapp_phone='94771111111'),'test','retained-synthetic-purchase');oid=order.id
        order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
        db.add(SalesPayment(sales_order_id=oid,payment_method='CASH',amount=order.grand_total,currency='LKR',verification_status='VERIFIED',payment_date=datetime.utcnow().date()));db.commit()
        member=svc.refresh_member(db,cid,'test','EXISTING_REVIEW');db.commit();mid=member.id;identity=member.membership_id
        db.add(Customer(name='Legitimate separate shared contact',phone='+94771111111'));db.commit()
    CustomerRegistration.__table__.drop(engine);CustomerPhoneLock.__table__.drop(engine)
    with engine.begin() as c:
        c.execute(text('ALTER TABLE sales_orders DROP COLUMN unidentified_reason'))
        for column in ('registered_at','registration_source','normalized_mobile'):c.execute(text('ALTER TABLE customers DROP COLUMN '+column))
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261124_57');command.upgrade(cfg,'20261125_58');command.upgrade(cfg,'20261125_58')
    with sessions() as db:
        assert db.get(Customer,cid).phone=='0771111111' and db.get(Customer,cid).registered_at is None
        m=db.get(LoyaltyMember,mid);assert m.membership_id==identity and m.status=='ACTIVE' and m.lifetime_net==4500
        assert db.get(SalesOrder,oid).customer_id==cid and db.query(LoyaltyOrderIdentity).count()==0 and not svc.config(db).enabled
    with engine.connect() as c:
        index=next(i for i in inspect(c).get_indexes('customers') if i['name']=='ix_customers_normalized_mobile');assert not index['unique']
    command.downgrade(cfg,'20261124_57')
    with engine.connect() as c:
        assert c.execute(text('SELECT phone FROM customers WHERE id=:i'),{'i':cid}).scalar()=='0771111111'
        assert c.execute(text('SELECT lifetime_net FROM loyalty_members WHERE id=:i'),{'i':mid}).scalar()==4500
        assert c.execute(text('SELECT customer_id FROM sales_orders WHERE id=:i'),{'i':oid}).scalar()==cid
        assert 'customer_registrations' not in inspect(c).get_table_names()


def test_concurrent_duplicate_request_returns_one_customer(pg):
    engine,sessions,uid=config(pg);key=str(uuid4())
    def run(_):
        with sessions() as db:
            c,created=registry.register(db,db.get(User,uid),'One registered buyer','0772222222','CRM',key);db.commit();return c.id
    with ThreadPoolExecutor(6) as pool:ids=list(pool.map(run,range(12)))
    assert len(set(ids))==1
    with sessions() as db:
        assert db.query(Customer).count()==1 and db.query(CustomerRegistration).count()==1 and db.query(LoyaltyAudit).filter_by(kind='CUSTOMER_REGISTERED').count()==1
        assert db.query(LoyaltyMember).count()==0


def test_concurrent_distinct_requests_cannot_duplicate_phone(pg):
    engine,sessions,uid=config(pg)
    def run(i):
        with sessions() as db:
            try:
                c,_=registry.register(db,db.get(User,uid),'Conflicting registration '+str(i),'0773333333','POS',str(uuid4()));db.commit();return 201
            except HTTPException as exc:db.commit();return exc.status_code
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(run,range(8)))
    assert results.count(201)==1 and results.count(409)==7
    with sessions() as db:assert db.query(Customer).count()==1 and db.query(LoyaltyAudit).filter_by(kind='CUSTOMER_IDENTITY_CONFLICT').count()==7


def test_worker_does_not_lock_outbox_ahead_of_busy_customer(pg):
    engine,sessions,uid=config(pg)
    with sessions() as db:
        c,_=registry.register(db,db.get(User,uid),'Lock ordering buyer','0774444444','CRM',str(uuid4()));db.commit();cid=c.id
    with sessions() as writer:
        writer.query(Customer).filter_by(id=cid).with_for_update().one()
        with ThreadPoolExecutor(1) as pool:assert pool.submit(loyalty_events.process_one,sessions).result(timeout=5) is False
        order,_=orders.create_draft(writer,payload(cid,whatsapp_phone='94774444444'),'test','completed-lock-order',commit=False)
        order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
        writer.add(SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=order.grand_total,currency='LKR',verification_status='VERIFIED',payment_date=datetime.utcnow().date()));writer.commit()
    with ThreadPoolExecutor(4) as pool:list(pool.map(lambda _:loyalty_events.process_one(sessions),range(8)))
    with sessions() as db:
        assert db.query(LoyaltyMember).count()==1 and db.query(LoyaltyRecalculation).count()==0 and not svc.config(db).enabled


def test_readonly_release_probe_emits_no_customer_personal_fields(pg,monkeypatch):
    import scripts.validate_customer_registration_readonly as diagnostic
    engine,sessions,uid=config(pg);monkeypatch.setattr(diagnostic,'engine',engine)
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261125_58')
    with sessions() as db:
        c,_=registry.register(db,db.get(User,uid),'Diagnostic private identity','0775555555','CRM',str(uuid4()));db.commit();cid=c.id
    result=diagnostic.run(cid,[])
    assert result['status']=='READ_ONLY_CHECK_COMPLETE' and result['transaction_read_only'] and result['programme_enabled'] is False
    assert '0775555555' not in str(result) and 'Diagnostic private identity' not in str(result)
    with sessions() as db:assert db.query(Customer).count()==1 and db.query(LoyaltyMember).count()==0


def test_reviewed_creation_and_live_registration_share_duplicate_fence(pg):
    import loyalty_recovery as recovery
    engine,sessions,uid=config(pg)
    with sessions() as db:
        u=db.get(User,uid);order,_=orders.create_draft(db,payload(None,customer_name='Reviewed historical buyer',whatsapp_phone='94776666666'),'test','synthetic-reviewed-history');oid=order.id
        plan=recovery.preview(db,u,[{'kind':'LOCAL','id':oid,'action':'CREATE'}],'synthetic-review-plan');db.commit();recovery.approve(db,u,plan,plan.digest,'Independently verified synthetic historical identity');db.commit();pid=plan.id;digest=plan.digest
    def modern():
        with sessions() as db:
            try:registry.register(db,db.get(User,uid),'Modern registered buyer','0776666666','CRM',str(uuid4()));db.commit();return 201
            except HTTPException as exc:db.rollback();return exc.status_code
    def reviewed():
        with sessions() as db:
            try:
                recovery.require_off(db);p=db.query(recovery.LoyaltyRecoveryPlan).filter_by(id=pid).populate_existing().with_for_update().one();recovery.execute(db,db.get(User,uid),p,digest);db.commit();return 201
            except HTTPException as exc:db.rollback();return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(modern);b=pool.submit(reviewed);results=[a.result(timeout=10),b.result(timeout=10)]
    assert sorted(results)==[201,409]
    with sessions() as db:
        assert db.query(Customer).count()==1 and db.query(LoyaltyMember).count()==0
        assert db.get(SalesOrder,oid).customer_id is None
