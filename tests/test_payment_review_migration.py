"""Retained financial totals and concurrent decisions on SQLite/PostgreSQL."""
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
import database
import customer_questions as q
import question_payment_review as review
from models import Base,SalesOrder,SalesOrderLine,SalesPayment,CustomerQuestionConfig as Control,CustomerQuestionRun as Run,WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,WhatsAppMessageAttachment as Attachment,User,GlobalAIReplyControl


def exercise_migration(engine,monkeypatch):
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        order=SalesOrder(internal_order_number='SO-PRESERVED',idempotency_key='preserved-order',customer_name='Retained',
            whatsapp_phone='94770000001',created_by='admin',order_status='CONFIRMED',payment_status='PARTIALLY_PAID',
            subtotal=1000,grand_total=1000,amount_paid=400)
        db.add(order);db.commit();oid=order.id
        db.add(SalesPayment(sales_order_id=oid,payment_method='BANK_TRANSFER',amount=400,currency='LKR',payment_date=datetime.utcnow().date(),
            verification_status='VERIFIED',payment_reference='PRESERVED-REFERENCE',verified_by='admin'))
        db.commit()
    with engine.begin() as conn:conn.execute(text('ALTER TABLE sales_orders DROP COLUMN service_total'))
    monkeypatch.setattr(database,'engine',engine)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(cfg,'20261108_33');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        order=db.get(SalesOrder,oid)
        assert order.service_total==0 and order.grand_total==1000 and order.amount_paid==400 and order.payment_status=='PARTIALLY_PAID'
        assert db.query(SalesPayment).one().payment_reference=='PRESERVED-REFERENCE'
        order.service_total=1800;order.grand_total=2800;db.commit()
    command.downgrade(cfg,'20261108_33');command.upgrade(cfg,'head')
    with sessions() as db:
        order=db.get(SalesOrder,oid);assert order.service_total==1800 and order.grand_total==2800 and order.amount_paid==400


def exercise_concurrency(engine,monkeypatch):
    Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        db.add(Control(id=1,enabled=True,definition=q.DEFAULT,version=1))
        user=User(username='review-admin',role='ADMIN',password_hash='not-a-login');db.add(user)
        conversation=Conversation(phone_number='94770000001',mode='HUMAN',last_customer_message_at=datetime.utcnow());db.add(conversation);db.flush()
        message=Message(phone_number=conversation.phone_number,direction='inbound',message_text='Slip',whatsapp_message_id='wamid.concurrent.slip');db.add(message);db.flush()
        attachment=Attachment(message_id=message.id,media_type='image',media_id='concurrent-slip');db.add(attachment);db.flush()
        answer={'product_id':'gid://shopify/Product/1','variant_id':'gid://shopify/ProductVariant/1','price':'45000.00','title':'Bicycle',
            'greasing_service':{'charge':'1800.00','quoted_total':'46800.00'},'delivery_details':{'name':'Test Name','address':'123 Test Road','status':'CONFIRMED'},
            'payment':{'status':'SLIP_REVIEW','receipt_message_id':message.id,'receipt_attachment_id':attachment.id}}
        run=Run(conversation_id=conversation.id,definition={'title':'Payment test','steps':[{'key':'p','type':'products','service':{'collect_delivery':True,'payment':{'voice_id':1}}}]},
            answers={'p':answer},step=1,status='COMPLETE',token='concurrent-test-token',created_by=user.username,version=1);db.add(run)
        order=SalesOrder(internal_order_number='SO-CONCURRENT',idempotency_key='concurrent-order',crm_conversation_id=conversation.id,customer_name='Test Name',
            whatsapp_phone=conversation.phone_number,delivery_address='123 Test Road',created_by=user.username,order_status='AWAITING_PAYMENT',payment_status='UNPAID',
            subtotal=45000,service_total=1800,grand_total=46800,amount_paid=0,lines=[SalesOrderLine(shopify_product_id=answer['product_id'],shopify_variant_id=answer['variant_id'],
                product_name_snapshot='Bicycle',quantity=1,unit_price=45000,line_total=45000)])
        db.add(order);db.commit();cid=conversation.id;rid=run.id;uid=user.id;oid=order.id
    payload={'bank_checked':True,'sales_order_id':oid,'amount':'46800.00','payment_date':datetime.utcnow().date().isoformat(),'payment_reference':'CONCURRENT-REFERENCE'}
    def decide(_):
        with sessions() as db:
            q.write_transaction(db)
            c,run,key,index,answer=review.load(db,db.get(User,uid),cid,rid,True)
            review.verify(db,c,run,key,index,answer,db.get(User,uid),payload)
            db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(decide,range(2)))
    calls=[];monkeypatch.setattr(review,'send_whatsapp_text',lambda *a,**kw:calls.append(a) or 'wamid.concurrent.confirmation')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:q.dispatch_one(sessions),range(2)))
    assert results.count(True)==1 and len(calls)==1
    with sessions() as db:
        assert db.query(SalesPayment).count()==1 and db.get(SalesOrder,oid).amount_paid==Decimal('46800.00')
        assert db.query(Message).filter_by(response_kind='staff_payment').count()==1


@pytest.mark.parametrize('exercise',[exercise_migration,exercise_concurrency])
def test_service_migration_and_payment_concurrency_sqlite(tmp_path,monkeypatch,exercise):
    engine=create_engine('sqlite:///'+str(tmp_path/'payment-review.db'),connect_args={'check_same_thread':False,'timeout':30})
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


@pytest.mark.parametrize('exercise',[exercise_migration,exercise_concurrency])
def test_service_migration_and_payment_concurrency_postgresql(monkeypatch,exercise):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Isolated PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='payment_review_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
