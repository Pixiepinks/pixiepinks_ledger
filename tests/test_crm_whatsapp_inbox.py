from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import main
from database import Base, SessionLocal, engine
from models import (User, WhatsAppConversation, WhatsAppConversationMessage, WhatsAppManualSend,
                    WhatsAppOutboundProductMessage)
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(bind=engine)
    main.ensure_whatsapp_inbox_columns()
    with SessionLocal() as db:
        db.query(WhatsAppManualSend).delete()
        db.query(WhatsAppOutboundProductMessage).delete()
        db.query(WhatsAppConversationMessage).delete()
        db.query(WhatsAppConversation).delete()
        if not db.query(User).filter_by(username="inbox-test").first():
            db.add(User(username="inbox-test", password_hash=hash_password("password")))
        db.commit()


def authenticated_client():
    client = TestClient(main.app, base_url="https://testserver")
    response = client.post("/login", data={"username": "inbox-test", "password": "password", "next": "/crm/whatsapp"})
    assert response.status_code == 200
    return client


def test_inbox_requires_authentication_and_renders_for_staff():
    anonymous = TestClient(main.app)
    assert anonymous.get("/crm/whatsapp", follow_redirects=False).status_code == 303
    response = authenticated_client().get("/crm/whatsapp")
    assert response.status_code == 200
    assert 'class="app-body whatsapp-page"' in response.text
    assert '/static/crm.css?v=whatsapp-referrals-phase5c' in response.text
    assert '/static/whatsapp_inbox.js?v=whatsapp-referrals-phase5c' in response.text
    assert 'id="send-button">Send</button>' in response.text
    assert response.text.index('id="message-input"') < response.text.index('id="send-button"')
    assert all(control in response.text for control in (
        'id="record-button"', 'id="stop-recording"', 'id="cancel-recording"',
        'id="voice-preview-audio"', 'id="delete-recording"', 'id="send-voice"',
        'id="catalog-button"', 'id="product-modal"',
    ))


def test_inbox_layout_uses_dynamic_viewport_and_flexible_message_history():
    repository = Path(__file__).resolve().parents[1]
    css = (repository / "static" / "crm.css").read_text()
    javascript = (repository / "static" / "whatsapp_inbox.js").read_text()
    assert ".whatsapp-page{height:100vh;height:100dvh;overflow:hidden}" in css
    assert ".whatsapp-page [hidden]{display:none!important}" in css
    assert "#messages{flex:1 1 auto;min-height:0;overflow-y:auto" in css
    assert "#composer{flex:0 0 auto;max-height:min(48%,420px);overflow-y:auto" in css
    assert "@media(max-width:380px)" in css
    assert "@media(min-width:701px)" in css
    assert ".whatsapp-page .inbox{height:100%;max-height:100%}" in css
    assert "recorder.onstart = () =>" in javascript
    assert javascript.index("recorder.onstart = () =>") < javascript.index("recorder.start(1000)")
    assert "reconcileMessages(data.messages)" in javascript
    assert "wrapper.dataset.messageId = String(message.id)" in javascript
    assert "messages.replaceChildren(...data.messages.map(renderMessage))" not in javascript
    assert "if (conversationLoading) return" in javascript


def test_conversation_order_search_unread_and_selected_read():
    now = datetime.utcnow()
    with SessionLocal() as db:
        older = WhatsAppConversation(phone_number="94770000001", customer_name="Nimali", unread_count=2,
                                     last_message_at=now - timedelta(hours=1))
        newer = WhatsAppConversation(phone_number="94770000002", customer_name="Amali", unread_count=1,
                                     last_message_at=now)
        db.add_all([older, newer])
        db.add_all([WhatsAppConversationMessage(phone_number="94770000001", direction="inbound", message_text="Old"),
                    WhatsAppConversationMessage(phone_number="94770000002", direction="inbound", message_text="New")])
        db.commit(); older_id, newer_id = older.id, newer.id
    client = authenticated_client()
    rows = client.get("/crm/whatsapp/api/conversations").json()["conversations"]
    assert [row["id"] for row in rows] == [newer_id, older_id]
    assert client.get("/crm/whatsapp/api/conversations", params={"search": "Nimali"}).json()["conversations"][0]["id"] == older_id
    assert client.get("/crm/whatsapp/api/conversations", params={"search": "000002"}).json()["conversations"][0]["id"] == newer_id
    assert client.post(f"/crm/whatsapp/api/conversations/{older_id}/read").json() == {"ok": True}
    with SessionLocal() as db:
        assert db.get(WhatsAppConversation, older_id).unread_count == 0
        assert db.get(WhatsAppConversation, newer_id).unread_count == 1


def test_takeover_return_and_manual_send_idempotency(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000003", customer_name="Kumari")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    client = authenticated_client()
    assert client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/takeover").json()["mode"] == "HUMAN"
    assert client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/return-to-ai").json()["mode"] == "AI"
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda phone, text, **kwargs: sent.append((phone, text)) or "wamid.manual")
    headers = {"Idempotency-Key": "same-staff-action"}
    first = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send", json={"message": "Payment details"}, headers=headers)
    second = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send", json={"message": "Payment details"}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert sent == [("94770000003", "Payment details")]
    with SessionLocal() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff").one()
        assert (message.send_status, message.sent_by) == ("sent", "inbox-test")


def test_manual_send_failure_is_not_persisted_as_success(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000004")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args, **kwargs: False)
    response = authenticated_client().post(f"/crm/whatsapp/api/conversations/{conversation_id}/send",
        json={"message": "Hello"}, headers={"Idempotency-Key": "failed-action"})
    assert response.status_code == 502
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(response_kind="staff").count() == 0


def test_manual_send_preserves_multiline_text(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000014")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    delivered = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda phone, text, **kwargs: delivered.append(text) or "wamid.multiline")
    message = "First line\nSecond line"
    response = authenticated_client().post(
        f"/crm/whatsapp/api/conversations/{conversation_id}/send",
        json={"message": message}, headers={"Idempotency-Key": "multiline-action"},
    )
    assert response.status_code == 200
    assert delivered == [message]
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(
            whatsapp_message_id="wamid.multiline"
        ).one().message_text == message


def test_voice_send_requires_authentication():
    response = TestClient(main.app).post(
        "/crm/whatsapp/api/conversations/1/send-audio",
        files={"audio": ("voice.webm", b"audio", "audio/webm")},
        headers={"Idempotency-Key": "voice-anonymous"}, follow_redirects=False,
    )
    assert response.status_code == 303


def test_voice_send_converts_uploads_sends_and_persists(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000005", mode="HUMAN")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    calls = []
    monkeypatch.setattr(main, "normalize_voice_audio", lambda raw, mime: (b"OggS-opus", 4.25))
    monkeypatch.setattr(main, "upload_whatsapp_audio", lambda audio: calls.append(("upload", audio)) or "media-1")
    monkeypatch.setattr(main, "send_whatsapp_audio", lambda phone, media, voice: calls.append((phone, media, voice)) or "wamid.voice")
    client = authenticated_client()
    headers = {"Idempotency-Key": "voice-action"}
    first = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send-audio",
        files={"audio": ("voice.webm", b"browser-audio", "audio/webm")}, headers=headers)
    second = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send-audio",
        files={"audio": ("voice.webm", b"browser-audio", "audio/webm")}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert calls == [("upload", b"OggS-opus"), ("94770000005", "media-1", True)]
    with SessionLocal() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_audio").one()
        assert (message.media_id, message.media_mime_type, message.media_duration_seconds) == (
            "media-1", "audio/ogg", 4.25,
        )
        detail = client.get(f"/crm/whatsapp/api/conversations/{conversation_id}").json()
        assert detail["messages"][0]["message_type"] == "audio"
        message_id = message.id
    monkeypatch.setattr(main, "download_whatsapp_media", lambda media_id: (b"voice-bytes", "audio/ogg"))
    media = client.get(f"/crm/whatsapp/api/conversations/{conversation_id}/messages/{message_id}/media")
    assert (media.status_code, media.content, media.headers["content-type"]) == (200, b"voice-bytes", "audio/ogg")
    assert media.headers["accept-ranges"] == "bytes"
    partial = client.get(
        f"/crm/whatsapp/api/conversations/{conversation_id}/messages/{message_id}/media",
        headers={"Range": "bytes=2-6"},
    )
    assert (partial.status_code, partial.content) == (206, b"ice-b")
    assert partial.headers["content-range"] == "bytes 2-6/11"


def test_voice_upload_failure_is_not_persisted_as_sent(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000006", mode="HUMAN")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    monkeypatch.setattr(main, "normalize_voice_audio", lambda raw, mime: (b"OggS-opus", 2.0))
    monkeypatch.setattr(main, "upload_whatsapp_audio", lambda audio: None)
    response = authenticated_client().post(
        f"/crm/whatsapp/api/conversations/{conversation_id}/send-audio",
        files={"audio": ("voice.webm", b"browser-audio", "audio/webm")},
        headers={"Idempotency-Key": "failed-voice"},
    )
    assert response.status_code == 502
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_audio").count() == 0


def test_voice_provider_exception_is_reported_without_sent_history(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000015", mode="HUMAN")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    monkeypatch.setattr(main, "normalize_voice_audio", lambda raw, mime: (b"OggS-opus", 2.0))
    monkeypatch.setattr(main, "upload_whatsapp_audio", lambda audio: (_ for _ in ()).throw(RuntimeError("provider error")))
    response = authenticated_client().post(
        f"/crm/whatsapp/api/conversations/{conversation_id}/send-audio",
        files={"audio": ("voice.webm", b"browser-audio", "audio/webm")},
        headers={"Idempotency-Key": "exception-voice"},
    )
    assert response.status_code == 502
    assert response.json()["error"] == "Meta could not upload this voice message."
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_audio").count() == 0



def test_product_catalog_requires_authentication(monkeypatch):
    response = TestClient(main.app).get("/crm/whatsapp/api/products", follow_redirects=False)
    assert response.status_code == 303


def test_product_catalog_search_is_authenticated_and_paginated(monkeypatch):
    monkeypatch.setattr(main, "search_products_for_inbox", lambda q, limit, cursor: {
        "products": [{"id": "gid://shopify/Product/1", "title": "Pink Bag",
                      "handle": "pink-bag", "variants": []}],
        "next_cursor": "next", "has_next_page": True,
    })
    response = authenticated_client().get("/crm/whatsapp/api/products?q=Pink")
    assert response.status_code == 200
    assert response.json()["products"][0]["title"] == "Pink Bag"
    assert response.json()["has_next_page"] is True


def test_product_send_verifies_shopify_persists_snapshot_and_is_idempotent(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000020", mode="HUMAN",
            last_customer_message_at=datetime.utcnow())
        db.add(conversation); db.commit(); conversation_id = conversation.id
    product = {"id": "gid://shopify/Product/1", "variant_id": "gid://shopify/ProductVariant/2",
        "handle": "pink-bag", "title": "Pink Bag", "variant_title": "Large", "sku": "PB-L",
        "price": main.Decimal("4250.00"), "currency": "LKR",
        "image": "https://cdn.shopify.com/pink.jpg",
        "url": "https://www.pixiepinks.shop/products/pink-bag"}
    monkeypatch.setattr(main, "resolve_inbox_product", lambda product_id, variant_id: product)
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_image",
        lambda phone, image, caption, **kwargs: sent.append((phone, image, caption)) or "wamid.product")
    client = authenticated_client()
    headers = {"Idempotency-Key": "product-action"}
    payload = {"product_id": product["id"], "variant_id": product["variant_id"]}
    first = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send-product",
                        json=payload, headers=headers)
    second = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send-product",
                         json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert len(sent) == 1
    assert "Price: Rs. 4,250.00" in sent[0][2]
    with SessionLocal() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_product").one()
        assert (message.product_title, message.variant_title, message.product_sku) == (
            "Pink Bag", "Large", "PB-L")
        detail = client.get(f"/crm/whatsapp/api/conversations/{conversation_id}").json()
        assert detail["messages"][-1]["product"]["url"].endswith("/products/pink-bag")


def test_product_send_rejects_closed_customer_service_window(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000021", mode="HUMAN",
            last_customer_message_at=datetime.utcnow() - timedelta(hours=25))
        db.add(conversation); db.commit(); conversation_id = conversation.id
    response = authenticated_client().post(
        f"/crm/whatsapp/api/conversations/{conversation_id}/send-product",
        json={"product_id": "gid://shopify/Product/1",
              "variant_id": "gid://shopify/ProductVariant/2"},
        headers={"Idempotency-Key": "closed-window"},
    )
    assert response.status_code == 409
    assert "24-hour" in response.json()["detail"]
