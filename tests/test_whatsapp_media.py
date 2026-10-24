from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
import whatsapp_media_service
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
            attachment.status = "AVAILABLE"
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
        assert attachment.status == "AVAILABLE"
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
            meta_media_id="retry-media", media_type="image", status="AVAILABLE")
        db.add_all([attachment, WhatsAppConversation(phone_number="94771112233", mode="AI")])
        db.commit(); attachment_id = attachment.id
    attempts = []
    def download(*args):
        attempts.append(1)
        if len(attempts) < 3:
            raise main.MediaDownloadError("temporary")
        return b"image bytes", "image/jpeg", "a" * 64
    monkeypatch.setattr(main, "media_storage_mode", lambda: "meta")
    monkeypatch.setattr(main, "download_meta_media", download)
    monkeypatch.setattr(main, "put_private_object", lambda *args: (_ for _ in ()).throw(AssertionError()))
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
        assert attachment.status == "AVAILABLE"
        assert attachment.retry_count == 3
        assert attachment.storage_key is None


def test_human_mode_stores_image_but_never_invokes_vision_or_replies(monkeypatch):
    with SessionLocal() as db:
        message = WhatsAppConversationMessage(phone_number="94771112233", direction="inbound",
                                               message_text="[Image]", message_type="image")
        db.add(message); db.flush()
        attachment = WhatsAppMediaAttachment(conversation_message_id=message.id,
            meta_media_id="human-media", media_type="image", status="AVAILABLE")
        db.add_all([attachment, WhatsAppConversation(phone_number="94771112233", mode="HUMAN")])
        db.commit(); attachment_id = attachment.id
    monkeypatch.setattr(main, "download_meta_media", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "analyze_product_image", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: (_ for _ in ()).throw(AssertionError()))
    main._process_inbound_media(attachment_id, "94771112233", None, "", "wamid")
    with SessionLocal() as db:
        assert db.get(WhatsAppMediaAttachment, attachment_id).status == "AVAILABLE"


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


def _meta_attachment(status="AVAILABLE", media_id="meta-on-demand"):
    with SessionLocal() as db:
        message = WhatsAppConversationMessage(phone_number="94771112233", direction="inbound",
                                               message_text="[Image]", message_type="image")
        db.add(message); db.flush()
        attachment = WhatsAppMediaAttachment(conversation_message_id=message.id,
            meta_media_id=media_id, media_type="image", mime_type="image/jpeg", status=status)
        db.add(attachment); db.commit()
        return attachment.id


def test_meta_mode_fetches_on_demand_without_s3_or_permanent_copy(monkeypatch):
    attachment_id = _meta_attachment()
    monkeypatch.setattr(main, "download_meta_media",
                        lambda *args: (b"temporary meta bytes", "image/jpeg", "c" * 64))
    monkeypatch.setattr(main, "get_private_object",
                        lambda *args: (_ for _ in ()).throw(AssertionError()))
    response = authenticated_client().get(f"/crm/whatsapp/api/attachments/{attachment_id}")
    assert response.status_code == 200
    assert response.content == b"temporary meta bytes"
    with SessionLocal() as db:
        attachment = db.get(WhatsAppMediaAttachment, attachment_id)
        assert attachment.status == "AVAILABLE"
        assert attachment.storage_key is None


def test_expired_meta_media_becomes_terminal_and_is_not_retried(monkeypatch):
    attachment_id = _meta_attachment(media_id="expired-media")
    calls = []
    def expired(*args):
        calls.append(1)
        raise main.MediaDownloadError("expired", status="EXPIRED")
    monkeypatch.setattr(main, "download_meta_media", expired)
    client = authenticated_client()
    first = client.get(f"/crm/whatsapp/api/attachments/{attachment_id}")
    second = client.get(f"/crm/whatsapp/api/attachments/{attachment_id}")
    assert first.status_code == second.status_code == 410
    assert len(calls) == 1
    with SessionLocal() as db:
        assert db.get(WhatsAppMediaAttachment, attachment_id).status == "EXPIRED"


def test_temporary_meta_failure_is_retryable(monkeypatch):
    attachment_id = _meta_attachment(media_id="temporary-media")
    monkeypatch.setattr(main, "download_meta_media", lambda *args: (_ for _ in ()).throw(
        main.MediaDownloadError("temporary", status="TEMPORARILY_UNAVAILABLE")))
    response = authenticated_client().get(f"/crm/whatsapp/api/attachments/{attachment_id}")
    assert response.status_code == 503
    with SessionLocal() as db:
        attachment = db.get(WhatsAppMediaAttachment, attachment_id)
        assert attachment.status == "TEMPORARILY_UNAVAILABLE"
        assert attachment.error_code == "META_FETCH_FAILED"


def test_meta_mode_startup_requires_no_s3_configuration(monkeypatch):
    monkeypatch.setattr(main.settings, "WHATSAPP_MEDIA_STORAGE_MODE", "meta")
    monkeypatch.setattr(main.settings, "MEDIA_STORAGE_BUCKET", None)
    monkeypatch.setattr(main.settings, "MEDIA_STORAGE_ACCESS_KEY_ID", None)
    monkeypatch.setattr(main.settings, "MEDIA_STORAGE_SECRET_ACCESS_KEY", None)
    with TestClient(main.app, base_url="https://testserver") as client:
        assert client.get("/login").status_code == 200
    assert main.media_storage_mode() == "meta"


def test_official_meta_metadata_then_temporary_url_workflow(monkeypatch):
    class MetadataResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/1",
                    "mime_type": "image/jpeg", "file_size": 4}
    class DownloadResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg", "content-length": "4"}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
        def iter_bytes(self): yield b"data"
    requested = []
    monkeypatch.setattr(whatsapp_media_service.settings, "META_WHATSAPP_ACCESS_TOKEN", "server-secret")
    monkeypatch.setattr(whatsapp_media_service.httpx, "get",
                        lambda url, **kwargs: requested.append((url, kwargs)) or MetadataResponse())
    monkeypatch.setattr(whatsapp_media_service.httpx, "stream",
                        lambda method, url, **kwargs: requested.append((url, kwargs)) or DownloadResponse())
    content, mime, digest = whatsapp_media_service.download_meta_media("media-123", "image")
    assert (content, mime) == (b"data", "image/jpeg")
    assert len(digest) == 64
    assert requested[0][0].endswith("/media-123")
    assert requested[1][0].startswith("https://lookaside.fbsbx.com/")
    assert all(call[1]["headers"]["Authorization"] == "Bearer server-secret" for call in requested)

