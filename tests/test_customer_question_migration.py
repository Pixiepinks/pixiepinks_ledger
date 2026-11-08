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
from models import Base,CustomerQuestionProductSend as ProductSend,CustomerQuestionConfig as Config,CustomerQuestionRun as Run,CustomerQuestionEvent as Event,WhatsAppConversation as C,WhatsAppConversationMessage as M,GlobalAIReplyControl,User


def exercise(engine,monkeypatch):
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t not in {Config.__table__,Run.__table__,Event.__table__,ProductSend.__table__}])
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False
        c=C(phone_number='94770012301',mode='HUMAN',unread_count=2,last_customer_message_at=datetime.utcnow());db.add(c)
        user=User(username='question-admin',role='ADMIN',password_hash='retained');db.add(user);db.commit();cid=c.id;uid=user.id
    monkeypatch.setattr(database,'engine',engine);cfg=AlembicConfig(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261107_28');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.execute(text('select version_num from alembic_version')).scalar()=='20261108_31'
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


def exercise_library(engine,monkeypatch):
    from models import CustomerQuestionFlow as Flow
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t is not Flow.__table__])
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    definition={'title':'අපේ existing flow','steps':[{'key':'product','body':'Choose product','type':'list','options':['Bicycle','Gift packs'],'next':[None,None]}]}
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False
        db.add(Config(id=1,enabled=True,version=9,definition=definition))
        c=C(phone_number='94770012301',mode='HUMAN',unread_count=4);db.add(c);db.flush()
        run=Run(conversation_id=c.id,definition=q.DEFAULT,status='WAITING',step=1,answers={'gender':'Girl'},token='retained-fixture-token',provider_id='wamid.retained',created_by='staff',version=7)
        db.add(run);db.commit();rid=run.id
    with engine.begin() as connection:connection.execute(text('ALTER TABLE customer_question_runs DROP COLUMN flow_id'))
    monkeypatch.setattr(database,'engine',engine);cfg=AlembicConfig(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261108_29');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.get(Flow,1).definition==definition and not db.get(Flow,1).archived
        assert db.get(Config,1).enabled is True and db.get(Config,1).version==9
        run=db.get(Run,rid);assert run.flow_id==1 and run.definition==q.DEFAULT and run.answers=={'gender':'Girl'}
        assert run.version==7 and run.status=='WAITING' and run.token=='retained-fixture-token'
        assert db.get(GlobalAIReplyControl,1).enabled is False and db.query(Event).count()==0
        db.add(Flow(definition=q.DEFAULT,archived=False,created_by='admin'));db.flush()
        assert db.query(Flow).count()==2 and db.query(Flow).order_by(Flow.id.desc()).first().id>1
        db.get(Flow,1).archived=True;db.commit()
    command.downgrade(cfg,'20261108_29');command.upgrade(cfg,'head')
    with sessions() as db:assert db.query(Flow).count()==2 and db.get(Flow,1).archived is True and db.get(Run,rid).flow_id==1


def test_library_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'library.db'))
    try:exercise_library(engine,monkeypatch)
    finally:engine.dispose()


def test_library_migration_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Isolated PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='library_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise_library(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()


def exercise_products(engine,monkeypatch):
    from models import CustomerQuestionProductSend as ProductSend,CustomerQuestionFlow as Flow
    from test_question_products import flow
    from test_shopify_collection_sends import product
    import question_product_service as products
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t is not ProductSend.__table__])
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        db.add(Config(id=1,definition=flow(),enabled=True,version=5))
        db.add(Flow(id=1,definition=flow(),archived=False,created_by='admin',version=3))
        c=C(phone_number='94770012301',mode='HUMAN',unread_count=4,last_customer_message_at=datetime.utcnow());db.add(c)
        user=User(username='products-admin',password_hash='retained',role='ADMIN');db.add(user)
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit();cid=c.id;uid=user.id
    monkeypatch.setattr(database,'engine',engine);cfg=AlembicConfig(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261108_30');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        assert db.get(Config,1).enabled and db.get(Config,1).version==5
        assert db.get(Flow,1).definition==flow() and db.get(Flow,1).version==3
        assert db.get(GlobalAIReplyControl,1).enabled is False and db.query(ProductSend).count()==0
        run=q.start(db,db.get(C,cid),db.get(User,uid));db.commit();rid=run.id
    calls=[]
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:product(int(pid.rsplit('/',1)[-1])))
    monkeypatch.setattr(products,'send_whatsapp_image',lambda phone,image,body,**kw:calls.append(body) or ('SENT','wamid.concurrent.'+str(len(calls))))
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:q.dispatch_one(sessions),range(2)))
    while q.dispatch_one(sessions):pass
    assert len(calls)==2 and 'Bicycle 1' in calls[0] and 'Bicycle 2' in calls[1]
    with sessions() as db:
        assert db.query(ProductSend).filter_by(status='SENT').count()==2 and db.get(Run,rid).status=='COMPLETE'
    command.downgrade(cfg,'20261108_30');command.upgrade(cfg,'head')
    with sessions() as db:assert db.query(ProductSend).count()==2 and db.get(GlobalAIReplyControl,1).enabled is False


def test_product_step_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'product_steps.db'),connect_args={'check_same_thread':False})
    try:exercise_products(engine,monkeypatch)
    finally:engine.dispose()


def test_product_step_migration_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Isolated PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='products_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise_products(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
