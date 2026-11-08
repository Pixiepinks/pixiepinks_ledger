import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import pytest
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config as AlembicConfig
import database
import customer_questions as q
from models import Base,CustomerQuestionConfig as Config,CustomerQuestionRun as Run,CustomerQuestionEvent as Event,WhatsAppConversation as C,WhatsAppConversationMessage as M,GlobalAIReplyControl,User


def exercise(engine,monkeypatch):
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t not in {Config.__table__,Run.__table__,Event.__table__}])
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False
        c=C(phone_number='94770012301',mode='HUMAN',unread_count=2,last_customer_message_at=datetime.utcnow());db.add(c)
        user=User(username='question-admin',role='ADMIN',password_hash='retained');db.add(user);db.commit();cid=c.id;uid=user.id
    monkeypatch.setattr(database,'engine',engine);cfg=AlembicConfig(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261107_28');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.execute(text('select version_num from alembic_version')).scalar()=='20261108_29'
        control=q.config(db);assert control.enabled is False and control.definition==q.DEFAULT
        assert db.query(Run).count()==0 and db.query(Event).count()==0
        assert db.get(C,cid).unread_count==2 and db.get(C,cid).mode=='HUMAN'
        assert db.get(User,uid).password_hash=='retained' and db.get(GlobalAIReplyControl,1).enabled is False
        control.enabled=True;db.commit()
        run=q.start(db,db.get(C,cid),db.get(User,uid));db.commit();rid=run.id
    calls=[]
    monkeypatch.setattr(q,'send_buttons',lambda *args:calls.append(args) or 'wamid.migration.questions')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:q.dispatch_one(sessions),range(2)))
    assert results.count(True)==1 and len(calls)==1
    with sessions() as db:
        run=db.get(Run,rid);assert run.status=='WAITING'
        message=M(phone_number=db.get(C,cid).phone_number,direction='inbound',message_text='Girl',
            created_at=datetime.utcnow(),whatsapp_message_id='wamid.answer.migration',reply_to_whatsapp_message_id=run.provider_id,response_kind='question_reply')
        db.add(message);db.flush()
        q.inbound(db,db.get(C,cid),message,f'ppq:{run.token}:0:0');db.commit()
        assert run.answers=={'gender':'Girl'} and run.status=='QUEUED'
    command.downgrade(cfg,'20261107_28');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.get(Run,rid).answers=={'gender':'Girl'} and db.query(Event).count()==4
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert {c['name']:c for c in inspect(engine).get_columns('whatsapp_conversation_messages')}['response_kind']['type'].length==20


def test_question_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'question.db'),connect_args={'check_same_thread':False})
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


def test_question_migration_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Isolated PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='question_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
