"""Additive referral migration: isolated SQLite and dedicated PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

import database
import main
from models import Base, ProcessedWhatsAppMessage, WhatsAppConversationMessage, WhatsAppMessageReferral


def migration():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/20261024_10_whatsapp_message_referrals.py'
    spec = importlib.util.spec_from_file_location('referral_migration', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('orm_first', [False, True])
def test_sqlite_migration_preserves_existing_messages_and_matches_orm(orm_first):
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        WhatsAppConversationMessage.__table__.create(engine)
        if orm_first:
            WhatsAppMessageReferral.__table__.create(engine)
        with engine.begin() as connection:
            message_id = connection.execute(WhatsAppConversationMessage.__table__.insert().values(
                phone_number='94770000000', direction='inbound', message_text='Existing message')).inserted_primary_key[0]
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade(); migration().upgrade()
            assert connection.execute(text('SELECT message_text FROM whatsapp_conversation_messages')).scalar() == 'Existing message'
        actual = inspect(engine)
        expected = WhatsAppMessageReferral.__table__
        assert {(c['name'],str(c['type']),c['nullable']) for c in actual.get_columns(expected.name)} == {
            (c.name,str(c.type),c.nullable) for c in expected.columns}
        assert actual.get_unique_constraints(expected.name)[0]['column_names'] == ['message_id']
        assert actual.get_foreign_keys(expected.name)[0]['options']['ondelete'] == 'RESTRICT'
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            row = WhatsAppMessageReferral(message_id=message_id, source_id='ad-one'); db.add(row); db.commit()
            assert row.captured_at is not None
            db.add(WhatsAppMessageReferral(message_id=message_id, source_id='ad-two'))
            with pytest.raises(IntegrityError): db.commit()
            db.rollback()
            db.add(WhatsAppMessageReferral(message_id=99999, source_id='orphan'))
            with pytest.raises(IntegrityError): db.commit()
            db.rollback()
            db.delete(db.get(WhatsAppConversationMessage, message_id))
            with pytest.raises(IntegrityError): db.commit()
            db.rollback()
            assert db.query(WhatsAppMessageReferral).count() == 1
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv('TEST_POSTGRESQL_URL'), reason='dedicated TEST_POSTGRESQL_URL not configured')
@pytest.mark.parametrize('orm_first', [False, True])
def test_postgresql_alembic_upgrade_and_atomic_capture(monkeypatch, orm_first):
    url = os.environ['TEST_POSTGRESQL_URL']
    parsed = make_url(url)
    assert parsed.get_backend_name() == 'postgresql' and parsed.database == 'pixiepinks_test', 'Dedicated pixiepinks_test database required'
    schema = 'referral_test_' + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'}, pool_size=4)
    sessions = sessionmaker(bind=engine)
    try:
        monkeypatch.setattr(database, 'engine', engine)
        monkeypatch.setattr(main, 'SessionLocal', sessions)
        tables = list(Base.metadata.sorted_tables) if orm_first else [t for t in Base.metadata.sorted_tables if t.name != 'whatsapp_message_referrals']
        Base.metadata.create_all(engine, tables=tables)
        with sessions() as db:
            db.add(WhatsAppConversationMessage(phone_number='94770000000', direction='inbound', message_text='Existing PostgreSQL history'))
            db.commit()
        config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
        command.stamp(config, '20261024_09')
        command.upgrade(config, 'head'); command.upgrade(config, 'head')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar() == '20261024_10'
            assert connection.execute(text('SELECT message_text FROM whatsapp_conversation_messages')).scalar() == 'Existing PostgreSQL history'
        columns = {c['name']:c for c in inspect(engine).get_columns('whatsapp_message_referrals')}
        assert columns['captured_at']['type'].timezone is True
        assert inspect(engine).get_unique_constraints('whatsapp_message_referrals')[0]['column_names'] == ['message_id']

        def capture(mid):
            return main._store_conversation_message('94770000000','inbound','Hello',
                whatsapp_message_id=mid, referral_metadata={'source_id':'postgres-ad','ctwa_clid':'private-test-click'},
                processed_record=ProcessedWhatsAppMessage(message_id=mid,sender_phone='94770000000',
                    message_type='text',received_at=datetime.utcnow()))
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(capture, ['pg-concurrent','pg-concurrent'])) == ['duplicate','stored']
        with sessions() as db:
            referral = db.query(WhatsAppMessageReferral).one()
            assert referral.message.whatsapp_message_id == 'pg-concurrent'
            assert referral.captured_at.tzinfo is not None
            assert db.query(ProcessedWhatsAppMessage).count() == 1

        def fail(*args): raise SQLAlchemyError('Isolated referral failure')
        event.listen(WhatsAppMessageReferral, 'before_insert', fail)
        try:
            assert capture('pg-retry') == 'failed'
        finally:
            event.remove(WhatsAppMessageReferral, 'before_insert', fail)
        with sessions() as db:
            assert db.query(ProcessedWhatsAppMessage).filter_by(message_id='pg-retry').count() == 0
            assert db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id='pg-retry').count() == 0
        assert capture('pg-retry') == 'stored'
        with sessions() as db:
            assert db.query(WhatsAppMessageReferral).count() == 2
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
