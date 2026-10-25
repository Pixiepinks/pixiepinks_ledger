"""Direct/ORM-first additive upgrade and actual PostgreSQL request concurrency."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import count
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData,Table,create_engine,event,inspect,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema,DropSchema

import database
import main
from models import Base,User,WhatsAppConversation,WhatsAppConversationMessage,WhatsAppManualSend,WhatsAppQuickMessage
from utils_auth import hash_password


@pytest.mark.parametrize('backend',['sqlite','postgresql'])
@pytest.mark.parametrize('orm_first',[False,True])
def test_additive_migration_history_defaults_and_postgresql_requests(tmp_path,monkeypatch,backend,orm_first):
    admin=None;schema=None
    if backend=='postgresql':
        url=os.getenv('TEST_POSTGRESQL_URL')
        if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
        assert make_url(url).database=='pixiepinks_test'
        admin=create_engine(url);schema='quick_'+uuid4().hex
        with admin.begin() as conn:conn.execute(CreateSchema(schema))
        engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    else:
        engine=create_engine(f'sqlite:///{tmp_path}/upgrade.db',connect_args={'check_same_thread':False})
        @event.listens_for(engine,'connect')
        def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    sessions=sessionmaker(bind=engine,expire_on_commit=False);old_overrides=dict(main.app.dependency_overrides)
    try:
        monkeypatch.setattr(database,'engine',engine)
        if orm_first:Base.metadata.create_all(engine)
        else:
            Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t.name not in {'whatsapp_quick_messages','whatsapp_manual_sends'}])
            legacy=MetaData();WhatsAppConversation.__table__.to_metadata(legacy)
            Table('whatsapp_manual_sends',legacy,*[column._copy() for column in WhatsAppManualSend.__table__.columns if column.name!='quick_message_id']).create(engine)
        with sessions() as db:
            user=User(username='migration-manager',password_hash=hash_password('password'),role='ADMIN')
            conversation=WhatsAppConversation(phone_number='94771110000',mode='HUMAN',last_customer_message_at=datetime.utcnow())
            db.add_all([user,conversation]);db.commit();cid=conversation.id
            db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',message_text='Retained message history'));db.commit()
            db.execute(text("INSERT INTO whatsapp_manual_sends (id,idempotency_key,conversation_id,message_text,status,sent_by,created_at) VALUES (999,'historical',:cid,'Retained staff send','SENT','migration-manager',:now)"),{'cid':cid,'now':datetime.utcnow()});db.commit()
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261025_11');command.upgrade(config,'head');command.upgrade(config,'head')
        with engine.connect() as conn:
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261025_12'
            assert conn.execute(text('SELECT message_text FROM whatsapp_conversation_messages')).scalar()=='Retained message history'
            assert conn.execute(text('SELECT quick_message_id FROM whatsapp_manual_sends')).scalar() is None
        actual=inspect(engine)
        assert actual.get_unique_constraints('whatsapp_quick_messages')[0]['column_names']==['shortcut']
        assert any(fk['referred_table']=='whatsapp_quick_messages' and fk['options']['ondelete']=='RESTRICT' for fk in actual.get_foreign_keys('whatsapp_manual_sends'))
        with sessions() as db:
            assert db.query(WhatsAppQuickMessage).count()==4
            row=db.query(WhatsAppQuickMessage).filter_by(shortcut='website').one();qid=row.id
            row.body='Staff edit https://www.pixiepinks.shop';row.usage_count=7;db.commit()
        # Re-enter the upgrade to test idempotent seed handling, not only Alembic stamping.
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        file=Path(__file__).resolve().parents[1]/'alembic/versions/20261025_12_whatsapp_quick_messages.py'
        spec=importlib.util.spec_from_file_location('quick_migration',file);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):migration.upgrade()
        with sessions() as db:
            row=db.get(WhatsAppQuickMessage,qid);assert row.body=='Staff edit https://www.pixiepinks.shop' and row.usage_count==7
        with pytest.raises(RuntimeError):migration.downgrade()
        if backend=='postgresql':
            def get_db():
                with sessions() as db:yield db
            main.app.dependency_overrides[main.get_db]=get_db
            client=TestClient(main.app,base_url='https://testserver')
            client.post('/login',data={'username':'migration-manager','password':'password'})
            csrf=client.get('/crm/whatsapp/api/quick-messages').json()['csrf_token']
            calls=[];sequence=count()
            monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**k:calls.append(1) or f'wamid.pg-quick-{next(sequence)}')
            def send(key):
                return client.post(f'/crm/whatsapp/api/conversations/{cid}/send',json={'message':'Edited saved reply','quick_message_id':qid},headers={'X-CSRF-Token':csrf,'Idempotency-Key':key})
            with ThreadPoolExecutor(max_workers=2) as executor:responses=list(executor.map(send,['duplicate','duplicate']))
            assert any(response.status_code==200 for response in responses) and len(calls)==1
            with ThreadPoolExecutor(max_workers=2) as executor:responses=list(executor.map(send,['different-1','different-2']))
            assert all(response.status_code==200 for response in responses) and len(calls)==3
            with sessions() as db:assert db.get(WhatsAppQuickMessage,qid).usage_count==10
    finally:
        main.app.dependency_overrides.clear();main.app.dependency_overrides.update(old_overrides);engine.dispose()
        if admin:
            with admin.begin() as conn:conn.execute(DropSchema(schema,cascade=True))
            admin.dispose()
