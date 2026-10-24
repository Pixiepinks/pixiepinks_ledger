"""Phase 1 schema checks use isolated databases, without contacting Meta."""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import WhatsAppConversationMessage, WhatsAppMessageAttachment


@pytest.fixture
def attachment_db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    WhatsAppConversationMessage.__table__.create(engine)
    yield engine
    engine.dispose()


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261024_09_whatsapp_attachment_metadata.py"
    spec = importlib.util.spec_from_file_location("attachment_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_message(db):
    message = WhatsAppConversationMessage(phone_number="94770000000", direction="INBOUND",
                                          message_text="Existing text", created_at=datetime.utcnow())
    db.add(message)
    db.commit()
    return message.id


def test_migration_preserves_messages_and_supports_multiple_attachments(attachment_db):
    with Session(attachment_db) as db:
        message_id = add_message(db)
    module = migration()
    with attachment_db.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
    with Session(attachment_db) as db:
        assert db.get(WhatsAppConversationMessage, message_id).message_text == "Existing text"
        for media_id, media_type in [("image-one", "image"), ("document-one", "document")]:
            db.add(WhatsAppMessageAttachment(message_id=message_id, media_id=media_id,
                                             media_type=media_type, filename="example.pdf"))
        db.commit()
        assert len(db.scalars(select(WhatsAppMessageAttachment)).all()) == 2
        db.add(WhatsAppMessageAttachment(message_id=message_id, media_id="image-one", media_type="image"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.delete(db.get(WhatsAppConversationMessage, message_id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_create_all_schema_matches_migration_and_rejects_orphans(attachment_db):
    WhatsAppMessageAttachment.__table__.create(attachment_db)
    before = inspect(attachment_db).get_columns("whatsapp_message_attachments")
    with attachment_db.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
    after = inspect(attachment_db).get_columns("whatsapp_message_attachments")
    assert [(c["name"], str(c["type"]), c["nullable"]) for c in before] == [
        (c["name"], str(c["type"]), c["nullable"]) for c in after]
    assert not any("url" in c["name"] or "token" in c["name"] or "blob" in c["name"] for c in after)
    with Session(attachment_db) as db:
        db.add(WhatsAppMessageAttachment(message_id=999, media_id="orphan", media_type="audio"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_migrated_schema_matches_orm(attachment_db):
    with attachment_db.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
    actual = inspect(attachment_db)
    expected = WhatsAppMessageAttachment.__table__
    assert {(c["name"], str(c["type"]), c["nullable"]) for c in actual.get_columns(expected.name)} == {
        (c.name, str(c.type), c.nullable) for c in expected.columns}
    assert actual.get_foreign_keys(expected.name)[0]["referred_table"] == "whatsapp_conversation_messages"
    assert actual.get_unique_constraints(expected.name)[0]["column_names"] == ["message_id", "media_id"]
    assert actual.get_indexes(expected.name)[0]["column_names"] == ["message_id"]
