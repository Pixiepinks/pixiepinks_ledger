"""Legacy chats remain unassigned; PostgreSQL concurrent updates audit once."""
import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import MetaData,Table,create_engine,text
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
import database
import conversation_access as access
from models import User,WhatsAppConversation,ConversationAssignmentAudit


def exercise(engine,monkeypatch):
    metadata=MetaData();users=User.__table__.to_metadata(metadata)
    new={'assigned_to_user_id','assigned_by_user_id','assigned_at','assignment_version'}
    old=Table('whatsapp_conversations',metadata,*[c.copy() for c in WhatsAppConversation.__table__.columns if c.name not in new])
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(users.insert(),[{'username':'admin','password_hash':'preserved-admin-hash','role':'ADMIN','section_access':None},
                                    {'username':'staff','password_hash':'preserved-staff-hash','role':'STAFF','section_access':['whatsapp']}])
        conn.execute(old.insert().values(phone_number='94770000001',customer_name='Preserved chat',mode='HUMAN',unread_count=4,taken_over_by='Historical staff'))
    monkeypatch.setattr(database,'engine',engine)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(config,'20261103_22');command.upgrade(config,'head');command.upgrade(config,'head')
    sessions=sessionmaker(bind=engine)
    with sessions() as db:
        row=db.query(WhatsAppConversation).one();cid=row.id
        assert row.assigned_to_user_id is None and row.assignment_version==0
        assert row.mode=='HUMAN' and row.unread_count==4 and row.taken_over_by=='Historical staff'
        admin_id=db.query(User.id).filter_by(username='admin').scalar();staff_id=db.query(User.id).filter_by(username='staff').scalar()
        assert db.get(User,staff_id).password_hash=='preserved-staff-hash'
        assert not access.scope(db.query(WhatsAppConversation),db.get(User,staff_id)).all()
    def assign(uid):
        with sessions() as db:
            try:access.assign(db,db.get(User,admin_id),cid,uid,0);return 200
            except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(assign,[admin_id,staff_id]))
    assert sorted(results)==[200,409]
    with sessions() as db:
        row=db.get(WhatsAppConversation,cid)
        assert row.assignment_version==1 and row.unread_count==4 and row.mode=='HUMAN'
        assert db.query(ConversationAssignmentAudit).count()==1


def test_assignment_migration_and_concurrent_updates_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'old-conversations.db'),connect_args={'check_same_thread':False,'timeout':20})
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


def test_assignment_migration_and_concurrent_updates_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL','')
    if not url:pytest.skip('Task-owned PostgreSQL not configured')
    admin=create_engine(url);assert admin.url.database=='pixiepinks_test' and admin.url.host in {'127.0.0.1','localhost'}
    schema='assignment_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
