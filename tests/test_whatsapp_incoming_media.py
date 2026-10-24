"""Incoming media registration exercises the webhook with isolated local data."""
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

import main
from models import (Base, ProcessedWhatsAppMessage, WhatsAppConversation,
                    WhatsAppConversationMessage, WhatsAppMessageAttachment)


@pytest.fixture
def media_webhook(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'media.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main.settings, "META_APP_SECRET", None)
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))

    def forbidden(*args, **kwargs):
        raise AssertionError("Media registration must not download media or invoke AI")

    monkeypatch.setattr(main, "download_whatsapp_media", forbidden)
    monkeypatch.setattr(main, "generate_customer_reply", forbidden)
    yield TestClient(main.app), sessions, sent
    engine.dispose()


def payload(kind="image", media=None, message_id="wamid.media"):
    message = {"id": message_id, "from": "94770000000", "timestamp": "1710000000", "type": kind}
    if media is not None:
        message[kind] = media
    return {"entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]}


@pytest.mark.parametrize("kind", ["image", "document", "video", "audio", "sticker"])
def test_supported_media_metadata_and_duplicate_delivery(media_webhook, kind):
    client, sessions, sent = media_webhook
    data = payload(kind, {"id": "media-123", "mime_type": "audio/ogg" if kind == "audio" else "image/test",
                          "filename": "example.file", "caption": "Customer caption", "sha256": "a" * 64,
                          "file_size": 1024, "duration": 1.5,
                          "url": "https://temporary.invalid/token", "access_token": "do-not-store",
                          "data": "do-not-store-binary", "voice": True, "animated": True})
    assert client.post("/webhook", json=data).status_code == 200
    assert client.post("/webhook", json=data).status_code == 200
    with sessions() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(direction="inbound").one()
        attachment = db.query(WhatsAppMessageAttachment).one()
        assert attachment.message_id == message.id
        assert message.whatsapp_message_id == "wamid.media"
        assert (attachment.media_type, attachment.media_id) == (kind, "media-123")
        assert (attachment.filename, attachment.caption, attachment.sha256) == ("example.file", "Customer caption", "a" * 64)
        assert (attachment.file_size_bytes, attachment.duration_seconds) == (1024, 1.5)
        if kind == "audio":
            assert (message.message_text, message.response_kind, message.media_id, message.media_mime_type) == (
                "Voice message", "customer_audio", "media-123", "audio/ogg")
        else:
            assert message.media_id is None  # Other media must not use audio playback.
        assert db.query(ProcessedWhatsAppMessage).count() == 1
        assert db.query(WhatsAppConversation).one().unread_count == 1
        stored = repr({c.name: getattr(attachment, c.name) for c in attachment.__table__.columns})
        assert "temporary.invalid" not in stored and "do-not-store" not in stored
    assert len(sent) == 1


@pytest.mark.parametrize("kind", ["image", "document", "video", "audio", "sticker"])
@pytest.mark.parametrize("media", [None, [], "bad", {}, {"id": []}, {"id": ""}, {"id": "x" * 256},
                                  {"id": "https://temporary.invalid/file"}])
def test_missing_or_malformed_media_is_safe_and_idempotent(media_webhook, kind, media):
    client, sessions, _ = media_webhook
    data = payload(kind, media)
    assert client.post("/webhook", json=data).status_code == 200
    assert client.post("/webhook", json=data).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageAttachment).count() == 0
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(ProcessedWhatsAppMessage).count() == 1


def test_malformed_optional_fields_and_bounded_strings(media_webhook):
    client, sessions, _ = media_webhook
    data = payload("document", {"id": "safe", "mime_type": {}, "filename": "x" * 300,
                                "caption": "y" * 3000, "sha256": [], "file_size": True,
                                "duration": "bad", "access_token": "secret"})
    assert client.post("/webhook", json=data).status_code == 200
    with sessions() as db:
        attachment = db.query(WhatsAppMessageAttachment).one()
        assert len(attachment.filename) == 255 and len(attachment.caption) == 2000
        assert attachment.mime_type is None and attachment.sha256 is None
        assert attachment.file_size_bytes is None and attachment.duration_seconds is None


@pytest.mark.parametrize("kind", ["image", "document", "video", "audio", "sticker"])
def test_media_id_alone_is_sufficient(media_webhook, kind):
    client, sessions, _ = media_webhook
    assert client.post("/webhook", json=payload(kind, {"id": "minimal"})).status_code == 200
    with sessions() as db:
        attachment = db.query(WhatsAppMessageAttachment).one()
        assert attachment.media_type == kind
        assert attachment.mime_type is None and attachment.filename is None and attachment.caption is None


@pytest.mark.parametrize("value", [-1, True, "123", [], {}, float("nan"), float("inf"), 10**100])
def test_invalid_numeric_metadata_is_ignored(value):
    message = {"type": "audio", "audio": {"id": "safe", "file_size": value, "duration": value}}
    metadata = main._incoming_media_metadata(message)
    assert "file_size_bytes" not in metadata and "duration_seconds" not in metadata


def test_concurrent_duplicate_deliveries(media_webhook):
    _, sessions, sent = media_webhook
    data = payload(media={"id": "concurrent"})

    def deliver(_):
        return TestClient(main.app).post("/webhook", json=data).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(deliver, range(2))) == [200, 200]
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count() == 1
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(WhatsAppMessageAttachment).count() == 1
        assert db.query(WhatsAppConversation).one().unread_count == 1
    assert len(sent) == 1


def test_batch_duplicates_and_same_media_on_distinct_messages(media_webhook):
    client, sessions, _ = media_webhook
    data = payload(media={"id": "shared"})
    messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages.extend([dict(messages[0]), {**messages[0], "id": "wamid.second", "from": "94771111111"}])
    assert client.post("/webhook", json=data).status_code == 200
    with sessions() as db:
        attachments = db.query(WhatsAppMessageAttachment).all()
        assert len(attachments) == 2
        assert len({a.message_id for a in attachments}) == 2
        assert {db.get(WhatsAppConversationMessage, a.message_id).phone_number for a in attachments} == {
            "94770000000", "94771111111"}


def test_human_owned_media_is_registered_without_reply_or_state_change(media_webhook):
    client, sessions, sent = media_webhook
    with sessions() as db:
        db.add(WhatsAppConversation(phone_number="94770000000", mode="HUMAN", unread_count=2,
                                    workflow_state="AWAITING_BICYCLE_AGE", bicycle_gender="girl"))
        db.commit()
    assert client.post("/webhook", json=payload(media={"id": "human-media"})).status_code == 200
    with sessions() as db:
        conversation = db.query(WhatsAppConversation).one()
        assert (conversation.mode, conversation.workflow_state, conversation.bicycle_gender) == (
            "HUMAN", "AWAITING_BICYCLE_AGE", "girl")
        assert conversation.unread_count == 3
        assert db.query(WhatsAppMessageAttachment).count() == 1
    assert sent == []


def test_attachment_write_failure_rolls_back_receipt_and_can_retry(media_webhook):
    client, sessions, sent = media_webhook

    def fail(*args):
        raise SQLAlchemyError("Simulated unavailable database")

    event.listen(WhatsAppMessageAttachment, "before_insert", fail)
    try:
        assert client.post("/webhook", json=payload(media={"id": "retry"})).status_code == 503
    finally:
        event.remove(WhatsAppMessageAttachment, "before_insert", fail)
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count() == 0
        assert db.query(WhatsAppConversationMessage).count() == 0
        assert db.query(WhatsAppMessageAttachment).count() == 0
        assert db.query(WhatsAppConversation).count() == 0
    assert sent == []
    assert client.post("/webhook", json=payload(media={"id": "retry"})).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageAttachment).count() == 1


def test_text_retains_reply_path_without_attachments(media_webhook, monkeypatch):
    client, sessions, _ = media_webhook
    calls = []
    monkeypatch.setattr(main, "_reply_to_text_message", lambda *args: calls.append(args))
    data = payload("text", {"body": "Hello"})
    assert client.post("/webhook", json=data).status_code == 200
    assert client.post("/webhook", json=data).status_code == 200
    assert len(calls) == 1 and calls[0][2] == "Hello"
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).one().message_text == "Hello"
        assert db.query(WhatsAppMessageAttachment).count() == 0


def test_media_requires_existing_webhook_signature(media_webhook, monkeypatch):
    client, sessions, _ = media_webhook
    monkeypatch.setattr(main.settings, "META_APP_SECRET", "test-signature-secret")
    body = json.dumps(payload(media={"id": "signed"})).encode()
    assert client.post("/webhook", content=body).status_code == 403
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count() == 0
    signature = "sha256=" + hmac.new(b"test-signature-secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"X-Hub-Signature-256": signature}).status_code == 200
