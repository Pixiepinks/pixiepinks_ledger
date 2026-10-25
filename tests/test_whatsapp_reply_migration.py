"""Additive quote-reference upgrade with retained history on SQLite/PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData,Table,create_engine,inspect,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema,DropSchema

import database
import main
from models import Base,WhatsAppConversationMessage,ProcessedWhatsAppMessage


@pytest.mark.parametrize('backend',['sqlite','postgresql'])
@pytest.mark.parametrize('orm_first',[False,True])
def test_quote_migration_preserves_history_and_receipt_atomicity(tmp_path,monkeypatch,backend,orm_first):
    admin=None;schema=None
    if backend=='postgresql':
        url=os.getenv('TEST_POSTGRESQL_URL')
        if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
        assert make_url(url).database=='pixiepinks_test'
        admin=create_engine(url);schema='quoted_'+uuid4().hex
        with admin.begin() as conn:conn.execute(CreateSchema(schema))
        engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    else:engine=create_engine(f'sqlite:///{tmp_path}/upgrade.db',connect_args={'check_same_thread':False})
    try:
        old_table=None
        if not orm_first:
            old_table=Table('whatsapp_conversation_messages',MetaData(),*[column._copy() for column in WhatsAppConversationMessage.__table__.columns if column.name!='reply_to_whatsapp_message_id'])
            old_table.create(engine)
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute((old_table if old_table is not None else WhatsAppConversationMessage.__table__).insert().values(phone_number='94770012301',direction='outbound',message_text='Retained original',whatsapp_message_id='wamid.old-original'))
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261025_12');command.upgrade(config,'20261025_13');command.upgrade(config,'20261025_13')
        columns={column['name']:column for column in inspect(engine).get_columns('whatsapp_conversation_messages')}
        assert columns['reply_to_whatsapp_message_id']['nullable'] and columns['reply_to_whatsapp_message_id']['type'].length==255
        with engine.connect() as conn:
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261025_13'
            assert conn.execute(text('SELECT message_text FROM whatsapp_conversation_messages')).scalar()=='Retained original'
            assert conn.execute(text('SELECT reply_to_whatsapp_message_id FROM whatsapp_conversation_messages')).scalar() is None
        sessions=sessionmaker(bind=engine);monkeypatch.setattr(main,'SessionLocal',sessions)
        def capture(_):
            return main._store_conversation_message('94770012301','inbound','Quoted response',whatsapp_message_id='wamid.pg-new-reply',reply_to_whatsapp_message_id='wamid.old-original',
                processed_record=ProcessedWhatsAppMessage(message_id='wamid.pg-new-reply',sender_phone='94770012301',message_type='text',received_at=datetime.utcnow()))
        if backend=='postgresql':
            with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(capture,range(2)))
            assert sorted(results)==['duplicate','stored']
        else:
            assert capture(0)=='stored' and capture(1)=='duplicate'
        with sessions() as db:
            assert db.query(WhatsAppConversationMessage).filter_by(direction='inbound').one().reply_to_whatsapp_message_id=='wamid.old-original'
            assert db.query(ProcessedWhatsAppMessage).count()==1
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as conn:conn.execute(DropSchema(schema,cascade=True))
            admin.dispose()
