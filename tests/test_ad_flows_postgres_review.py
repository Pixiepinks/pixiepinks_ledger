"""Optional disposable-PostgreSQL migration and ad-webhook concurrency review."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import hashlib,hmac,json,os
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import database,main,customer_questions as questions
from models import (Base,User,GlobalAIReplyControl,WhatsAppConversation as Conversation,CustomerQuestionConfig,
    CustomerQuestionFlow,CustomerQuestionRun,AdFlowControl,AdFlowRule,AdFlowExecution,AdFlowAudit,ProcessedWhatsAppMessage)
from test_whatsapp_referrals import payload


@pytest.fixture
def postgres_review(monkeypatch):
    url=os.getenv('AD_FLOW_TEST_DATABASE_URL')
    if not url:pytest.skip('Disposable AD_FLOW_TEST_DATABASE_URL not configured')
    admin=create_engine(url)
    assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='ad_flow_review_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setattr(database,'engine',engine)
        sessions=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
        monkeypatch.setattr(main,'SessionLocal',sessions)
        monkeypatch.setattr(main.settings,'META_APP_SECRET','review-pg-signature')
        with sessions() as db:
            db.get(GlobalAIReplyControl,1).enabled=False
            db.add(User(username='review-admin',password_hash='not-a-login-hash',role='ADMIN'))
            db.add(CustomerQuestionConfig(id=1,enabled=True,version=0,definition=questions.DEFAULT))
            db.add(CustomerQuestionFlow(id=1,definition=questions.DEFAULT,version=0,created_by='review-admin'))
            db.add(Conversation(phone_number='94770000001',mode='HUMAN',customer_name='Preserved thread',unread_count=3))
            db.commit()
        yield engine,sessions
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def upgrade_legacy(engine):
    for table in (AdFlowAudit.__table__,AdFlowExecution.__table__,AdFlowRule.__table__,AdFlowControl.__table__):table.drop(engine)
    with engine.begin() as conn:conn.execute(text('ALTER TABLE whatsapp_conversations DROP COLUMN ad_flow_opted_out'))
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261109_37');command.upgrade(cfg,'head');command.upgrade(cfg,'head')


def test_postgres_migration_off_default_and_legacy_preservation(postgres_review):
    engine,sessions=postgres_review;upgrade_legacy(engine)
    with sessions() as db:
        assert not db.get(AdFlowControl,1).enabled
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.get(CustomerQuestionConfig,1).enabled is True
        assert db.get(CustomerQuestionFlow,1).definition==questions.DEFAULT
        c=db.query(Conversation).one();assert c.mode=='HUMAN' and c.unread_count==3 and not c.ad_flow_opted_out
        assert db.query(AdFlowRule).count()==0


def test_postgres_concurrent_signed_webhook_starts_and_sends_once(postgres_review,monkeypatch):
    engine,sessions=postgres_review;upgrade_legacy(engine)
    with sessions() as db:
        db.get(AdFlowControl,1).enabled=True
        actor=db.query(User).filter_by(username='review-admin').one()
        db.add(AdFlowRule(ad_id='120000000000001',name='PG review',flow_id=1,flow_version=0,policy='FIRST',cooldown_hours=24,allow_assigned=False,
            enabled=True,deleted=False,version=1,updated_by=actor.id));db.commit()
    body=payload(mid='wamid.pg-concurrent');body['entry'][0]['changes'][0]['value']['messages'][0]['timestamp']=str(int(datetime.utcnow().timestamp()))
    raw=json.dumps(body).encode();headers={'Content-Type':'application/json','X-Hub-Signature-256':'sha256='+hmac.new(b'review-pg-signature',raw,hashlib.sha256).hexdigest()}
    def deliver(_):
        client=TestClient(main.app,base_url='https://testserver')
        return client.post('/webhook',content=raw,headers=headers).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(deliver,[1,2]))
    assert statuses==[200,200]
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count()==1 and db.query(CustomerQuestionRun).count()==1 and db.query(AdFlowExecution).count()==1
    sent=[];monkeypatch.setattr(questions,'send_buttons',lambda *a,**k:sent.append(True) or 'wamid.pg-first')
    assert questions.dispatch_one(sessions) and not questions.dispatch_one(sessions) and sent==[True]
    with sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False
