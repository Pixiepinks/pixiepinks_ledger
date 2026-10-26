"""Real PostgreSQL/SQLite additive upgrades, legacy preservation and create races."""
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os

import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,MetaData,Table,event,inspect,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema,DropSchema
import database,main
from models import Base,Lead,LeadNote,LeadTask,User,WhatsAppConversation
from utils_auth import hash_password
from test_crm_whatsapp_leads import add_referral,BASE,save


@pytest.mark.parametrize('backend',['sqlite','postgresql'])
@pytest.mark.parametrize('orm_first',[False,True])
def test_additive_link_migration_and_concurrent_management(tmp_path,monkeypatch,backend,orm_first):
    admin=None
    if backend=='postgresql':
        url=os.getenv('TEST_POSTGRESQL_URL')
        if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
        assert make_url(url).database=='pixiepinks_test';admin=create_engine(url);schema='crm_leads_'+uuid4().hex
        with admin.begin() as connection:connection.execute(CreateSchema(schema))
        engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    else:
        engine=create_engine(f'sqlite:///{tmp_path}/leads.db',connect_args={'check_same_thread':False,'timeout':20})
        @event.listens_for(engine,'connect')
        def foreign_keys(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    old_overrides=dict(main.app.dependency_overrides)
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    try:
        if not orm_first:
            old_leads=Table('leads',MetaData(),*[column._copy() for column in Lead.__table__.columns if column.name!='whatsapp_conversation_id'])
            old_leads.create(engine)
            with engine.begin() as connection:connection.execute(old_leads.insert().values(id=901,lead_no='LD-legacy',customer_name='Retained CRM customer',mobile='44770000000',status='INTERESTED',notes='Retain original notes',created_at=datetime.utcnow()))
        Base.metadata.create_all(engine)
        with sessions() as db:
            if orm_first:db.add(Lead(id=901,lead_no='LD-legacy',customer_name='Retained CRM customer',mobile='44770000000',status='INTERESTED',notes='Retain original notes'))
            db.add(User(username='crm-staff',password_hash=hash_password('password'),role='STAFF'))
            db.add_all([WhatsAppConversation(id=10,phone_number='94770012301',customer_name='WhatsApp customer',mode='HUMAN'),WhatsAppConversation(id=11,phone_number='94770012302',mode='AI')])
            db.flush();db.add(LeadNote(lead_id=901,note='Retain note history'));db.add(LeadTask(lead_id=901,status='PENDING',note='Retain task history'));db.commit()
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261025_15');command.upgrade(config,'head');command.upgrade(config,'head')
        assert any(item['unique'] for item in inspect(engine).get_indexes('leads') if item['name']=='ix_leads_whatsapp_conversation_id')
        assert any(item['referred_table']=='whatsapp_conversations' for item in inspect(engine).get_foreign_keys('leads'))
        with sessions() as db:
            old=db.get(Lead,901);assert old.notes=='Retain original notes' and old.status=='INTERESTED' and old.whatsapp_conversation_id is None
        def get_db():
            with sessions() as db:yield db
        main.app.dependency_overrides[main.get_db]=get_db
        client=TestClient(main.app,base_url='https://testserver')
        client.post('/login',data={'username':'crm-staff','password':'password'})
        add_referral(sessions,10)
        data=client.get(BASE).json();csrf=data['csrf_token'];assert data['total']==2
        assert client.get(BASE,params={'source':'ad'}).json()['total']==1
        barrier=Barrier(2)
        def create_once(_):barrier.wait(timeout=10);return save(client,10,csrf)
        with ThreadPoolExecutor(max_workers=2) as executor:responses=list(executor.map(create_once,range(2)))
        assert [response.status_code for response in responses]==[200,200]
        assert responses[0].json()['lead']['id']==responses[1].json()['lead']['id']
        inbox_rows=client.get('/crm/whatsapp/api/conversations').json()['conversations']
        inbox_row=next(row for row in inbox_rows if row['id']==10)
        assert inbox_row['lead_id']==responses[0].json()['lead']['id'] and inbox_row['lead_status']=='NEW'
        assert client.get(f'{BASE}/10/status').json()['lead']['id']==inbox_row['lead_id']
        with sessions() as db:
            assert db.query(Lead).count()==2 and db.query(Lead).filter_by(whatsapp_conversation_id=10).count()==1
            assert db.query(LeadNote).one().note=='Retain note history' and db.query(LeadTask).one().note=='Retain task history'
        if backend=='sqlite':
            with pytest.raises(RuntimeError,match='Retain the additive lead link'):command.downgrade(config,'20261025_15')
        else:command.downgrade(config,'20261025_15')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM leads')).scalar()==2
            assert connection.execute(text('SELECT note FROM lead_notes')).scalar()=='Retain note history'
            assert connection.execute(text('SELECT note FROM lead_tasks')).scalar()=='Retain task history'
        command.upgrade(config,'head')
    finally:
        main.app.dependency_overrides.clear();main.app.dependency_overrides.update(old_overrides)
        engine.dispose()
        if admin:
            with admin.begin() as connection:connection.execute(DropSchema(schema,cascade=True))
            admin.dispose()
