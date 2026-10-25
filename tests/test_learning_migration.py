"""Real additive schema, PostgreSQL job locking and concurrent publication checks."""
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,inspect,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema,DropSchema

import database
from models import Base,User,WhatsAppConversationMessage,BotKnowledgeEntry
from learning_models import *
from learning_service import configuration,review,publish_candidate
from settings import settings
import learning_worker as worker
from test_learning_center import source,extractor,enable


@pytest.mark.parametrize('orm_first',[False,True])
@pytest.mark.parametrize('backend',['sqlite','postgresql'])
def test_migration_and_actual_concurrent_worker_publication(tmp_path,monkeypatch,orm_first,backend):
    admin=None
    if backend=='postgresql':
        url=os.getenv('TEST_POSTGRESQL_URL')
        if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
        assert make_url(url).database=='pixiepinks_test';admin=create_engine(url);schema='learning_'+uuid4().hex
        with admin.begin() as conn:conn.execute(CreateSchema(schema))
        engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    else:engine=create_engine(f'sqlite:///{tmp_path}/migration.db',connect_args={'check_same_thread':False})
    sessions=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
    try:
        monkeypatch.setattr(database,'engine',engine)
        Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if orm_first or not t.name.startswith('learning_')])
        with sessions() as db:
            db.add(WhatsAppConversationMessage(phone_number='94770000000',direction='inbound',message_text='Preserved original customer message'))
            db.add(User(id=1,username='learning-staff',password_hash='not-a-login',role='STAFF'));db.commit()
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(config,'20261025_13');command.upgrade(config,'head');command.upgrade(config,'head')
        actual=inspect(engine)
        learning_tables=[table for table in Base.metadata.sorted_tables if table.name.startswith('learning_')]
        assert len(learning_tables)==14
        for table in learning_tables:
            assert {(column['name'],column['nullable']) for column in actual.get_columns(table.name)}=={(column.name,column.nullable) for column in table.columns}
            assert len(actual.get_foreign_keys(table.name))==len(table.foreign_key_constraints)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261025_14'
            assert conn.execute(text('SELECT message_text FROM whatsapp_conversation_messages')).scalar()=='Preserved original customer message'
        with sessions() as db:assert not configuration(db)['enabled'] and db.query(LearningLease).one().owner is None
        enable(sessions,monkeypatch)
        for n in range(1,4):source(sessions,n)
        if backend=='postgresql':
            with worker.worker_lease(engine,sessions) as owner:
                assert owner
                with ThreadPoolExecutor(max_workers=1) as executor:assert executor.submit(worker.tick,engine,sessions,extractor).result()=='BUSY'
        assert worker.tick(engine,sessions,extractor)=='PROCESSED'
        with sessions() as db:
            row=db.query(LearningCandidate).one();assert row.evidence_count==3
            cid=row.id;review(db,cid,'approve',1,{'verified':True});db.commit()
        barrier=Barrier(2) if backend=='postgresql' else None
        def publish_once(_):
            from learning_service import LearningError
            with sessions() as db:
                try:
                    candidate=db.get(LearningCandidate,cid)
                    if barrier:barrier.wait(timeout=5)
                    publish_candidate(db,candidate,1);db.commit();return 'published'
                except LearningError:db.rollback();return 'already-published'
        if backend=='postgresql':
            with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(publish_once,range(2)))
            assert 'published' in results
        else:assert publish_once(0)=='published'
        with sessions() as db:
            assert db.query(LearningKnowledgeVersion).count()==1 and db.query(BotKnowledgeEntry).one().enabled
            assert db.query(LearningReservation).count()==3
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as conn:conn.execute(DropSchema(schema,cascade=True))
            admin.dispose()
