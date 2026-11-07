import os
from uuid import uuid4
from datetime import datetime,timedelta
from pathlib import Path
from threading import Event as Signal
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from models import Base,User,WhatsAppConversation as C,WhatsAppConversationMessage as M,ConversationWorkState as W,ConversationWorkEvent as E,ProcessedWhatsAppMessage as Receipt,GlobalAIReplyControl
import database
import main
import conversation_work_service as work
import inbox_reply_queue


def exercise(engine,monkeypatch,concurrent=False):
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t not in {W.__table__,E.__table__}])
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        user=User(username='work-admin',password_hash='retained',role='ADMIN');db.add(user)
        c=C(phone_number='94770012301',customer_name='Retained',mode='HUMAN',unread_count=3,last_customer_message_at=datetime.utcnow()-timedelta(days=2));db.add(c);db.flush();cid=c.id;uid=user.id
        db.add(M(phone_number=c.phone_number,direction='inbound',message_text='Keep history',whatsapp_message_id='initial',created_at=datetime.utcnow()-timedelta(minutes=10)))
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setattr(database,'engine',engine);cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261107_27');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    assert {c['name']:c for c in inspect(engine).get_columns('conversation_work_states')}['status']['type'].length==24
    with sessions() as db:
        c=db.get(C,cid);assert c.unread_count==3 and c.mode=='HUMAN'
        assert db.get(User,uid).password_hash=='retained' and db.get(GlobalAIReplyControl,1).enabled is False
        assert db.query(E).count()==0 and db.get(W,cid).status=='NEEDS_REPLY' and not db.get(W,cid).force_reply
        pending,_=inbox_reply_queue.summary(db);assert db.query(pending).count()==1
        snap=work.snapshots(db,[c])[cid];payload={'action':'resolve','key':uuid4().hex,**{k:snap[k] for k in ['version','last_inbound_id','review_token']}}
        work.transition(db,db.get(User,uid),cid,payload);db.commit()
        assert db.query(M).count()==1 and db.query(E).count()==1
        pending,_=inbox_reply_queue.summary(db);assert db.query(pending).count()==0
    command.downgrade(cfg,'20261107_27');command.upgrade(cfg,'head')
    with sessions() as db:assert db.get(W,cid).status=='RESOLVED' and db.query(E).count()==1
    if not concurrent:return
    # Reopen first, obtain a review snapshot, then hold an uncommitted inbound
    # until a newer manual resolution has committed. Its provider time is before
    # the resolution commit but AFTER the staff's review: it must win finally.
    with sessions() as db:
        snap=work.snapshots(db,[db.get(C,cid)])[cid]
        work.transition(db,db.get(User,uid),cid,{'action':'reopen','key':uuid4().hex,**{k:snap[k] for k in ['version','last_inbound_id','review_token']}});db.commit()
        snap=work.snapshots(db,[db.get(C,cid)])[cid]
    ready=Signal();release=Signal()
    def inbound_transaction():
        with sessions() as db:
            now=datetime.utcnow();c=db.get(C,cid)
            db.add(Receipt(message_id='racing-inbound',sender_phone=c.phone_number,message_type='text',received_at=now,processed_at=now))
            m=M(phone_number=c.phone_number,direction='inbound',message_text='Concurrent inquiry',whatsapp_message_id='racing-inbound',created_at=now)
            db.add(m);db.flush();ready.set()
            assert release.wait(10)
            work.inbound(db,c,m);db.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(inbound_transaction);assert ready.wait(10)
        try:
            with sessions() as db:
                work.transition(db,db.get(User,uid),cid,{'action':'resolve','key':uuid4().hex,**{k:snap[k] for k in ['version','last_inbound_id','review_token']}});db.commit()
        finally:release.set()
        future.result(timeout=15)
    with sessions() as db:
        assert db.get(W,cid).status=='NEEDS_REPLY' and db.query(E).filter_by(trigger='inbound').count()==1
        pending,_=inbox_reply_queue.summary(db);assert db.query(pending).count()==1
    monkeypatch.setattr(main,'SessionLocal',sessions)
    assert main._store_conversation_message('94770012301','inbound','Retry',whatsapp_message_id='racing-inbound',processed_record=Receipt(message_id='racing-inbound',sender_phone='94770012301',message_type='text',received_at=datetime.utcnow(),processed_at=datetime.utcnow()))=='duplicate'
    with sessions() as db:assert db.query(E).filter_by(trigger='inbound').count()==1


def test_work_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'work.db'))
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


def test_work_migration_postgresql_and_concurrent_inbound(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Local isolated PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='work_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch,True)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
