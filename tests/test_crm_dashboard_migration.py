import os
from uuid import uuid4
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import create_engine,text,inspect,select
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
import database
import crm_dashboard_service as reporting
from models import Base,CRMAnalyticsPhone as Phone,CRMAnalyticsDelivery as Delivery,WhatsAppConversationMessage as Message,WhatsAppManualSend as Manual,User,WhatsAppConversation as Conversation,GlobalAIReplyControl,MarketingConsent,MarketingConsentEvent


def exercise(engine,monkeypatch):
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t not in {Phone.__table__,Delivery.__table__}])
    # Remove precisely the new columns to exercise an actual pre-26 schema.
    with engine.begin() as conn:
        for table in ['whatsapp_conversation_messages','whatsapp_manual_sends']:
            cols=['analytics_actor_id']+(['analytics_actor_kind','analytics_category'] if table=='whatsapp_conversation_messages' else [])
            for index in inspect(conn).get_indexes(table):
                if any(c in index['column_names'] for c in cols):conn.execute(text('DROP INDEX "'+index['name']+'"'))
            for column in cols:conn.execute(text('ALTER TABLE '+table+' DROP COLUMN '+column))
        conn.execute(text("INSERT INTO users (id,username,password_hash,role,is_active,session_version) VALUES (1,'dashboard-admin','preserved','ADMIN',true,0)"))
        conn.execute(text('UPDATE global_ai_reply_control SET enabled=false'))
        conn.execute(text("INSERT INTO whatsapp_conversations(id,phone_number,customer_name,mode,unread_count,last_message_at,created_at,updated_at,assignment_version) VALUES (1,'94770012301','Existing','HUMAN',3,:now,:now,:now,0)"),{'now':datetime(2026,11,7,1)})
        conn.execute(text("INSERT INTO whatsapp_conversation_messages(id,phone_number,direction,message_text,whatsapp_message_id,response_kind,send_status,sent_by,created_at) VALUES (1,'+94770012301','inbound','retained','in-old','customer',NULL,NULL,:now),(2,'94770012301','outbound','retained','out-old','staff','read','dashboard-admin',:now)"),{'now':datetime(2026,11,7,1)})
        conn.execute(text("INSERT INTO marketing_consents(id,identity_key,phone_e164,conversation_id,display_name,status,scope,identity_conflict,affirmative,interests,needs_review,version,created_at,updated_at) VALUES (1,'wa:+94770012301','+94770012301',1,'Existing','NOT_REQUESTED','PIXIEPINKS_WHATSAPP_PROMOTIONS',false,false,'[]',false,0,:now,:now)"),{'now':datetime(2026,11,7,1)})
    monkeypatch.setattr(database,'engine',engine);cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261106_25');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    sessions=sessionmaker(bind=engine,autoflush=False)
    with sessions() as db:
        assert db.execute(text('select version_num from alembic_version')).scalar()=='20261108_33'
        assert db.get(User,1).password_hash=='preserved' and db.get(Conversation,1).unread_count==3
        assert db.get(GlobalAIReplyControl,1).enabled is False and db.get(MarketingConsent,1).status=='NOT_REQUESTED'
        assert db.query(MarketingConsentEvent).count()==0
        assert db.get(Message,2).analytics_actor_id is None
        phones=db.query(Phone).all();assert len(phones)==2 and {p.canonical_phone for p in phones}=={'+94770012301'}
        assert db.get(Delivery,'out-old').status=='read'
        report=reporting.Report(db,db.get(User,1),reporting.window('custom','2026-11-07','2026-11-07'))
        data=report.metrics(report.events());assert data['unique_contacts']==1 and data['staff_sent']==1 and data['unattributed']==1
        assert data['read']==1 and data['delivered']==1
        staff=report.staff(report.events());assert staff['total']==1 and staff['rows'][0]['total']==1
        for metric in ['incoming','unique_contacts','first_contacts','staff_sent','contacted']:
            assert report.details(metric,report.events())['total']==1
        db.add(Message(id=99,phone_number='94770012301',direction='outbound',message_text='future snapshot',response_kind='staff',
            sent_by='dashboard-admin',send_status='sent',whatsapp_message_id='future-out',created_at=datetime(2026,11,7,2)))
        db.commit()
        assert db.get(Message,99).analytics_actor_id==1
        assert report.metrics(report.events())['staff_sent']==2
    command.downgrade(cfg,'20261106_25');command.upgrade(cfg,'head')
    with sessions() as db:assert db.get(Delivery,'out-old').status=='read' and db.get(GlobalAIReplyControl,1).enabled is False


def test_dashboard_migration_sqlite(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'dashboard.db'))
    try:exercise(engine,monkeypatch)
    finally:engine.dispose()


def test_dashboard_migration_postgresql(monkeypatch):
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Local PostgreSQL fixture not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='dashboard_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:exercise(engine,monkeypatch)
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
