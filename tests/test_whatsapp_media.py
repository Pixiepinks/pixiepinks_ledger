from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from database import Base, SessionLocal, engine
from models import (
    ProcessedWhatsAppMessage, User, WhatsAppConversation, WhatsAppConversationMessage,
    WhatsAppMediaAttachment,
)
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(bind=engine)
    main.ensure_whatsapp_inbox_columns()
    with SessionLocal() as db:
        db.query(WhatsAppMediaAttachment).delete()
        db.query(WhatsAppConversationMessage).delete()
        db.query(WhatsAppConversation).delete()
        db.query(ProcessedWhatsAppMessage).delete()
        if not db.query(User).filter_by(username="media-test").first():
            db.add(User(username="media-test", password_hash=hash_password("password")))
        db.commit()


def payload(kind="image", media_id="media-1", caption=None, referral=None, message_id="wamid.media-1"):
    media = {"id": media_id, "mime_type": "image/jpeg" if kind == "image" else "application/pdf"}
    if caption is not None:
        media["caption"] = caption
    if kind == "document":
        media["filename"] = "catalog.pdf"
    message = {"id": message_id, "from": "94771112233", "timestamp": "1700000000",
               "type": kind, kind: media}
    if referral:
        message["referral"] = referral
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": "94771112233", "profile": {"name": "Media Customer"}}],
        "messages": [message],
    }}]}]}


def authenticated_client():
    client = TestClient(main.app, base_url="https://testserver")
    response = client.post("/login", data={"username": "media-test", "password": "password"})
    assert response.status_code == 200
    return client


def test_image_caption_and_ad_referral_are_persisted_retry_safely(monkeypatch):
    processed = []
    def mark_processed(*args):
        processed.append(args)
        with SessionLocal() as db:
            attachment = db.get(WhatsAppMediaAttachment, args[0])
            attachment.status = "READY"
            attachment.storage_key = "whatsapp/test.jpg"
            db.commit()
    monkeypatch.setattr(main, "_process_inbound_media", mark_processed)
    body = payload(caption="Pink bicycle", referral={"source_type": "ad", "headline": "Bicycle sale",
                                                        "unknown": "discard me"})
    client = TestClient(main.app)
    assert client.post("/webhook", json=body).status_code == 200
    assert client.post("/webhook", json=body).status_code == 200
    with SessionLocal() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(
            whatsapp_message_id="wamid.media-1").one()
        attachment = db.query(WhatsAppMediaAttachment).one()
        assert (message.message_text, message.message_type) == ("Pink bicycle", "image")
        assert message.referral == {"source_type": "ad", "headline": "Bicycle sale"}
        assert attachment.meta_media_id == "media-1"
        assert attachment.status == "READY"
        assert db.query(ProcessedWhatsAppMessage).count() == 1
    assert len(processed) == 1


def test_document_metadata_and_safe_filename(monkeypatch):
    monkeypatch.setattr(main, "_process_inbound_media", lambda *args: None)
    body = payload(kind="document", media_id="doc-1", message_id="wamid.doc-1")
    body["entry"][0]["changes"][0]["value"]["messages"][0]["document"]["filename"] = "../../invoice.pdf"
    assert TestClient(main.app).post("/webhook", json=body).status_code == 200
    with SessionLocal() as db:
        attachment = db.query(WhatsAppMediaAttachment).one()
        assert attachment.filename == "invoice.pdf"
        assert attachment.media_type == "document"


@pytest.mark.parametrize("kind,mime", [
    ("video", "video/mp4"), ("audio", "audio/ogg"), ("sticker", "image/webp"),
])
def test_supported_media_types_are_persisted(monkeypatch, kind, mime):
    monkeypatch.setattr(main, "_process_inbound_media", lambda *args: None)
    body = payload(kind=kind, media_id=f"{kind}-1", message_id=f"wamid.{kind}-1")
    media = body["entry"][0]["changes"][0]["value"]["messages"][0][kind]
    media["mime_type"] = mime
    if kind == "audio":
        media["voice"] = True
    assert TestClient(main.app).post("/webhook", json=body).status_code == 200
    with SessionLocal() as db:
        attachment = db.query(WhatsAppMediaAttachment).one()
        message = db.query(WhatsAppConversationMessage).filter_by(
            whatsapp_message_id=f"wamid.{kind}-1").one()
        assert attachment.media_type == kind
        assert message.message_type == ("voice" if kind == "audio" else kind)


def test_media_processing_retries_and_ai_mode_uses_verified_shopify(monkeypatch):
    with SessionLocal() as db:
        message = WhatsAppConversationMessage(phone_number="94771112233", direction="inbound",
                                               message_text="Pink bicycle", message_type="image")
        db.add(message); db.flush()
        attachment = WhatsAppMediaAttachment(conversation_message_id=message.id,
            meta_media_id="retry-media", media_type="image", status="PENDING")
        db.add_all([attachment, WhatsAppConversation(phone_number="94771112233", mode="AI")])
        db.commit(); attachment_id = attachment.id
    attempts = []
    def download(*args):
        attempts.append(1)
        if len(attempts) < 3:
            raise main.MediaDownloadError("temporary")
        return b"image bytes", "image/jpeg", "a" * 64
    monkeypatch.setattr(main, "storage_configured", lambda: True)
    monkeypatch.setattr(main, "download_meta_media", download)
    monkeypatch.setattr(main, "put_private_object", lambda *args: None)
    monkeypatch.setattr(main, "analyze_product_image", lambda *args: {
        "product_related": True, "attributes": ["pink bicycle"], "confidence": "high"})
    monkeypatch.setattr(main, "search_products", lambda *args, **kwargs: [{
        "title": "Verified Pink Bicycle", "handle": "pink-bike", "variants": []}])
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args, **kwargs: "Verified Shopify reply")
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda phone, text: sent.append((phone, text)) or True)
    main._process_inbound_media(attachment_id, "94771112233", "Customer", "Pink bicycle", "wamid")
    assert len(attempts) == 3
    assert sent == [("94771112233", "Verified Shopify reply")]
    with SessionLocal() as db:
        attachment = db.get(WhatsAppMediaAttachment, attachment_id)
        assert attachment.status == "READY"
        assert attachment.retry_count == 3
        assert attachment.storage_key.startswith("whatsapp/")


def test_human_mode_stores_image_but_never_invokes_vision_or_replies(monkeypatch):
    with SessionLocal() as db:
        message = WhatsAppConversationMessage(phone_number="94771112233", direction="inbound",
                                               message_text="[Image]", message_type="image")
        db.add(message); db.flush()
        attachment = WhatsAppMediaAttachment(conversation_message_id=message.id,
            meta_media_id="human-media", media_type="image", status="PENDING")
        db.add_all([attachment, WhatsAppConversation(phone_number="94771112233", mode="HUMAN")])
        db.commit(); attachment_id = attachment.id
    monkeypatch.setattr(main, "storage_configured", lambda: True)
    monkeypatch.setattr(main, "download_meta_media", lambda *args: (b"x", "image/jpeg", "b" * 64))
    monkeypatch.setattr(main, "put_private_object", lambda *args: None)
    monkeypatch.setattr(main, "analyze_product_image", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: (_ for _ in ()).throw(AssertionError()))
    main._process_inbound_media(attachment_id, "94771112233", None, "", "wamid")
    with SessionLocal() as db:
        assert db.get(WhatsAppMediaAttachment, attachment_id).status == "READY"


def test_attachment_requires_authentication_and_streams_privately(monkeypatch):
    with SessionLocal() as db:
        message = WhatsAppConversationMessage(phone_number="94771112233", direction="inbound",
                                               message_text="[Image]", message_type="image")
        db.add(message); db.flush()
        attachment = WhatsAppMediaAttachment(conversation_message_id=message.id,
            meta_media_id="stored-media", media_type="image", mime_type="image/jpeg",
            storage_key="whatsapp/key.jpg", status="READY")
        db.add(attachment); db.commit(); attachment_id = attachment.id
    anonymous = TestClient(main.app)
    assert anonymous.get(f"/crm/whatsapp/api/attachments/{attachment_id}",
                         follow_redirects=False).status_code == 303
    monkeypatch.setattr(main, "get_private_object", lambda key: (b"private image", "image/jpeg"))
    response = authenticated_client().get(f"/crm/whatsapp/api/attachments/{attachment_id}")
    assert response.status_code == 200
    assert response.content == b"private image"
    assert response.headers["cache-control"] == "private, no-store"
    assert "META_WHATSAPP_ACCESS_TOKEN" not in response.text

