import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DatabaseError
from fastapi import HTTPException
import database
from models import Base,User,Customer,Lead,WhatsAppConversation,MarketingConsent as Consent,MarketingConsentEvent as Event,MarketingConsentRequest as Request
import marketing_consent_service as service


def exercise(engine,monkeypatch):
    new={Consent.__table__,Event.__table__,Request.__table__}
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t not in new])
    with engine.begin() as conn:
        conn.execute(User.__table__.insert().values(id=1,username='migration-admin',password_hash='preserved-hash',role='ADMIN'))
        conn.execute(Customer.__table__.insert(),[{'id':1,'name':'Existing customer','phone':'0770012301'},{'id':2,'name':'No number','phone':None}])
        conn.execute(WhatsAppConversation.__table__.insert().values(id=1,phone_number='94770012301',customer_name='Existing chat',mode='HUMAN',unread_count=4,last_customer_message_at=datetime.utcnow()))
        conn.execute(Lead.__table__.insert().values(id=1,customer_name='Facebook lead',mobile='94770012301',source='Facebook',status='PAID'))
    monkeypatch.setattr(database,'engine',engine);cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261103_24');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    sessions=sessionmaker(bind=engine)
    with sessions() as db:
        rows=db.query(Consent).all();assert len(rows)==2 and all(r.status=='NOT_REQUESTED' for r in rows)
        row=db.query(Consent).filter_by(conversation_id=1).one();cid=row.id
        assert row.customer_id==1 and row.lead_id==1
        assert db.get(Customer,1).phone=='0770012301' and db.get(Lead,1).status=='PAID'
        assert db.get(WhatsAppConversation,1).unread_count==4 and db.get(User,1).password_hash=='preserved-hash'
    def prepare(_):
        with sessions() as db:
            try:
                result=service.prepare_request(db,db.get(Consent,cid),db.get(WhatsAppConversation,1),db.get(User,1),'en',str(uuid4()));db.commit();return 200
            except HTTPException as exc:db.rollback();return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(prepare,[1,2]))==[200,409]
    with sessions() as db:
        row=db.get(Consent,cid);before=row.status
        service.change(db,row,row.version,{'status':'OPTED_OUT'})
        service.event(db,row,before,'CONSENT_WITHDRAWN','migration-stop',reason='Preserved audit fixture.');db.commit()
        with pytest.raises(DatabaseError):db.execute(text("UPDATE marketing_consent_events SET reason='changed'"))
        db.rollback()
    command.downgrade(cfg,'20261103_24')
    assert 'marketing_consent_events' in inspect(engine).get_table_names()
    command.upgrade(cfg,'head')
    with sessions() as db:assert db.get(Consent,cid).status=='OPTED_OUT' and db.query(Event).count()==1


def test_marketing_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'migration.db'),connect_args={'check_same_thread':False,'timeout':20})
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


def test_marketing_migration_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Temporary local PostgreSQL not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='consent_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
