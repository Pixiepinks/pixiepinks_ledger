"""Phase 5A capture only: isolated databases, signed webhooks, no live providers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import hmac
import json
import logging
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

import main
from bot_configuration_service import invalidate_cache
from models import (Base, ProcessedWhatsAppMessage, WhatsAppConversation, WhatsAppConversationMessage,
                    WhatsAppMessageAttachment, WhatsAppMessageReferral, User)
from utils_auth import hash_password
from whatsapp_referral_service import extract_referral

REFERRAL = {
    "source_id": "120000000000001", "source_type": "ad", "source_url": "https://fb.me/example-ad",
    "headline": "Advertised bicycle", "body": "Advertisement copy, not Shopify facts",
    "media_type": "image", "image_url": "https://scontent.example.fbcdn.net/image.jpg?oh=public-reference",
    "video_url": "https://video.example.fbcdn.net/video.mp4", "thumbnail_url": "https://lookaside.fbsbx.com/thumb.jpg",
    "ctwa_clid": "private-click-attribution-123", "captured_at": "2000-01-01T00:00:00Z",
    "access_token": "never-store-this-token", "unknown": {"raw_payload": "do-not-store"},
}


@pytest.fixture
def webhook(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'referrals.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main.settings, "META_APP_SECRET", "referral-test-secret")
    monkeypatch.setattr(main.settings, "OPENAI_API_KEY", None)
    invalidate_cache()
    calls = {"ai": [], "sent": [], "image": []}
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: calls["ai"].append(args) or "Existing reply.")
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args, **kwargs: calls["sent"].append(args) or True)
    monkeypatch.setattr(main, "_reply_to_image_message", lambda *args: calls["image"].append(args))
    monkeypatch.setattr(main, "search_products", lambda *args, **kwargs: pytest.fail("Referral must not identify a Shopify product"))
    monkeypatch.setattr(main, "download_whatsapp_attachment", lambda *args, **kwargs: pytest.fail("Capture must not fetch referral media"))

    class SignedClient(TestClient):
        def post(self, url, **kwargs):
            if url == "/webhook" and "json" in kwargs:
                body = json.dumps(kwargs.pop("json")).encode()
                signature = "sha256=" + hmac.new(b"referral-test-secret", body, hashlib.sha256).hexdigest()
                kwargs["content"] = body
                kwargs["headers"] = {**kwargs.get("headers", {}), "X-Hub-Signature-256": signature, "Content-Type": "application/json"}
            return super().post(url, **kwargs)
    yield SignedClient(main.app, base_url="https://testserver"), sessions, calls
    invalidate_cache()
    engine.dispose()


def payload(kind="text", referral=REFERRAL, mid="wamid.referral", body="Hello! Can I get more info on this?"):
    message = {"id": mid, "from": "94770000000", "timestamp": "1710000000", "type": kind}
    if kind == "text":
        message["text"] = {"body": body}
    else:
        message[kind] = {"id": "incoming-media-123", "mime_type": "audio/ogg" if kind == "audio" else "image/png"}
    if referral is not None:
        message["referral"] = deepcopy(referral)
    return {"entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]}


def test_text_referral_is_private_and_does_not_change_ai_or_product_routing(webhook):
    client, sessions, calls = webhook
    assert client.post("/webhook", json=payload()).status_code == 200
    with sessions() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(direction="inbound").one()
        referral = db.query(WhatsAppMessageReferral).one()
        assert referral.message_id == message.id and message.referral.id == referral.id
        assert referral.message.id == message.id and message.whatsapp_message_id == "wamid.referral"
        for key in ("source_id", "source_type", "source_url", "headline", "body", "media_type", "image_url", "video_url", "thumbnail_url", "ctwa_clid"):
            assert getattr(referral, key) == REFERRAL[key]
        assert isinstance(referral.captured_at, datetime) and referral.captured_at.year != 2000
        assert message.shopify_product_reference is None and message.shopify_variant_reference is None
        assert db.query(ProcessedWhatsAppMessage).one().message_id == "wamid.referral"
        assert db.query(WhatsAppConversation).one().unread_count == 1
    assert len(calls["ai"]) == 1
    assert calls["ai"][0][0] == "Hello! Can I get more info on this?"
    assert all(REFERRAL[key] not in repr(calls["ai"]) for key in ("headline", "source_id", "ctwa_clid"))
    assert calls["sent"] == [("94770000000", "Existing reply.")]


@pytest.mark.parametrize("kind", ["image", "audio", "video", "document", "sticker"])
def test_media_referral_links_to_same_message_without_changing_dispatch(webhook, kind):
    client, sessions, calls = webhook
    assert client.post("/webhook", json=payload(kind)).status_code == 200
    with sessions() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(direction="inbound").one()
        assert db.query(WhatsAppMessageAttachment).one().message_id == message.id
        assert db.query(WhatsAppMessageReferral).one().message_id == message.id
        if kind == "audio":
            assert (message.media_id, message.media_mime_type, message.response_kind) == ("incoming-media-123", "audio/ogg", "customer_audio")
        else:
            assert message.media_id is None
    assert calls["ai"] == []
    if kind == "image":
        assert calls["image"] == [("94770000000", "wamid.referral", None)] and calls["sent"] == []
    else:
        assert calls["sent"] == [("94770000000", main.UNSUPPORTED_MESSAGE_REPLY)]


@pytest.mark.parametrize("kind", ["text", "image", "audio"])
def test_human_mode_still_captures_without_ai_or_reply(webhook, kind):
    client, sessions, calls = webhook
    with sessions() as db:
        db.add(WhatsAppConversation(phone_number="94770000000", mode="HUMAN", unread_count=2)); db.commit()
    assert client.post("/webhook", json=payload(kind)).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).count() == 1
        conversation = db.query(WhatsAppConversation).one()
        assert conversation.mode == "HUMAN" and conversation.unread_count == 3
    assert calls == {"ai": [], "sent": [], "image": []}


@pytest.mark.parametrize("referral", [None, [], "bad", 123, {}, {"source_id": []}, {"body": {"nested": "bad"}}, {"access_token": "secret"}])
def test_absent_or_malformed_referral_preserves_text(webhook, referral):
    client, sessions, calls = webhook
    assert client.post("/webhook", json=payload(referral=referral)).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).count() == 0
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(ProcessedWhatsAppMessage).count() == 1
    assert len(calls["ai"]) == 1


def test_partial_referral_and_bounded_fields():
    metadata = extract_referral({"referral": {"headline": "x" * 600, "body": "y" * 5000, "source_id": "1" * 256,
        "ctwa_clid": "z" * 1025, "source_type": False, "media_type": {"bad": "type"}, "captured_at": "spoof"}})
    assert metadata == {"headline": "x" * 500, "body": "y" * 4000}
    assert extract_referral({"referral": {"headline": "\x00\ud800Title\n"}}) == {"headline": "Title"}


@pytest.mark.parametrize("field", ["source_url", "image_url", "video_url", "thumbnail_url"])
@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "http://fb.me/ad", "https://127.0.0.1/ad", "https://fb.me.attacker.test/ad",
    "https://user:pass@facebook.com/ad", "https://facebook.com:8443/ad", "https://facebook.com:bad/ad",
    "https://facebook.com/ad?access_token=secret", "https://facebook.com/ad?TOKEN=secret",
    "https://facebook.com/ad?access%255ftoken=secret", "https://facebook.com/ad#ctwa_clid=private",
    "https://facebook.com/ad?redirect=https%3A%2F%2Fexample.test%2F%3Ftoken%3Dsecret",
    "https://facebook.com/\\ad", "https://facebook.com/\nsecret", "https://facebook.com/\ud800",
    "https://facebook.com/" + "a" * 2048,
])
def test_unsafe_or_credential_urls_are_not_stored(field, url):
    assert extract_referral({"referral": {field: url}}) is None


@pytest.mark.parametrize("field", ["headline", "body", "source_id", "ctwa_clid"])
def test_credential_shaped_values_are_omitted(field):
    for secret in ("access_token=secret", "Authorization: Bearer secret", "EAA" + "A" * 40, "sk-" + "x" * 40):
        assert extract_referral({"referral": {field: secret}}) is None


@pytest.mark.parametrize("kind", ["text", "image", "audio"])
def test_repeated_delivery_is_created_once(webhook, kind):
    client, sessions, calls = webhook
    original = payload(kind)
    assert client.post("/webhook", json=original).status_code == 200
    sent = deepcopy(calls)
    assert client.post("/webhook", json=original).status_code == 200
    assert calls == sent
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).count() == 1
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(ProcessedWhatsAppMessage).count() == 1
        assert db.query(WhatsAppConversation).one().unread_count == 1


@pytest.mark.parametrize("kind", ["text", "image"])
def test_concurrent_duplicate_delivery(webhook, kind):
    client, sessions, calls = webhook
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: client.post("/webhook", json=payload(kind)).status_code, range(2))) == [200, 200]
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).count() == db.query(ProcessedWhatsAppMessage).count() == 1
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(WhatsAppConversation).one().unread_count == 1
    assert len(calls["ai"] if kind == "text" else calls["image"]) == 1


@pytest.mark.parametrize("kind", ["text", "image", "audio"])
@pytest.mark.parametrize("failure_model", [WhatsAppConversationMessage, WhatsAppMessageReferral, ProcessedWhatsAppMessage])
def test_database_failure_rolls_back_everything_and_retry_succeeds(webhook, kind, failure_model, caplog):
    client, sessions, calls = webhook
    def fail(*args):
        logging.getLogger("sqlalchemy.engine").error("private-click-attribution-123 never-store-this-token")
        raise SQLAlchemyError("private-click-attribution-123")
    event.listen(failure_model, "before_insert", fail)
    try:
        with caplog.at_level(logging.DEBUG):
            assert client.post("/webhook", json=payload(kind)).status_code == 503
        assert "private-click-attribution-123" not in caplog.text and "never-store-this-token" not in caplog.text
    finally:
        event.remove(failure_model, "before_insert", fail)
    with sessions() as db:
        for model in (WhatsAppConversationMessage, WhatsAppMessageReferral, ProcessedWhatsAppMessage, WhatsAppMessageAttachment, WhatsAppConversation):
            assert db.query(model).count() == 0
    assert calls == {"ai": [], "sent": [], "image": []}
    assert client.post("/webhook", json=payload(kind)).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).count() == db.query(ProcessedWhatsAppMessage).count() == 1


def test_multiple_advertisements_and_batch_duplicate_keep_original_links(webhook):
    client, sessions, _ = webhook
    data = payload()
    messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages.append(deepcopy(messages[0]))
    second = payload(referral={**REFERRAL, "source_id": "second-ad", "ctwa_clid": "second-click"}, mid="wamid.second")
    messages.extend(second["entry"][0]["changes"][0]["value"]["messages"])
    assert client.post("/webhook", json=data).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppConversation).count() == 1
        rows = db.query(WhatsAppMessageReferral).order_by(WhatsAppMessageReferral.id).all()
        assert [(r.source_id, r.message.whatsapp_message_id) for r in rows] == [
            (REFERRAL["source_id"], "wamid.referral"), ("second-ad", "wamid.second")]


@pytest.mark.parametrize("kind", ["location", "interactive", "unknown"])
def test_other_referral_messages_have_an_originating_history_row(webhook, kind):
    client, sessions, _ = webhook
    assert client.post("/webhook", json=payload(kind)).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).one().message.message_text == f"{kind.title()} message"


def test_referral_with_empty_text_is_captured_without_creating_ai_request(webhook):
    client, sessions, calls = webhook
    assert client.post("/webhook", json=payload(body=" ")).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppMessageReferral).one().message.message_text == "Text message"
    assert calls == {"ai": [], "sent": [], "image": []}


def test_referral_is_not_exposed_in_inbox_api_and_requires_signed_webhook(webhook):
    client, sessions, calls = webhook
    unsigned = TestClient(main.app, base_url="https://testserver")
    assert unsigned.post("/webhook", json=payload()).status_code == 403
    assert calls == {"ai": [], "sent": [], "image": []}
    assert client.post("/webhook", json=payload()).status_code == 200
    with sessions() as db:
        db.add(User(username="referral-staff", password_hash=hash_password("password"))); db.commit()
        conversation_id = db.query(WhatsAppConversation).one().id
    assert client.post("/login", data={"username": "referral-staff", "password": "password"}).status_code == 200
    response = client.get(f"/crm/whatsapp/api/conversations/{conversation_id}")
    assert response.status_code == 200
    assert not any(value in response.text for value in (REFERRAL["ctwa_clid"], REFERRAL["headline"], REFERRAL["source_id"]))
    assert unsigned.get(f"/crm/whatsapp/api/conversations/{conversation_id}", follow_redirects=False).status_code == 303
