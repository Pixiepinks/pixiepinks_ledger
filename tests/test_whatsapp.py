import hashlib
import hmac

import httpx
from fastapi.testclient import TestClient

import main
import whatsapp_service
from database import Base, SessionLocal, engine
from models import ProcessedWhatsAppMessage


def _message_payload(message_id="wamid.123", message_type="text"):
    message = {
        "from": "94770000000",
        "id": message_id,
        "timestamp": "1710000000",
        "type": message_type,
    }
    if message_type == "text":
        message["text"] = {"body": "Hello"}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"profile": {"name": "Customer"}, "wa_id": "94770000000"}],
            "messages": [message],
        }}]}],
    }


def setup_function():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.query(ProcessedWhatsAppMessage).delete()
        db.commit()
    main.settings.META_APP_SECRET = None


def test_verification_and_existing_route():
    client = TestClient(main.app)
    good = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": main.settings.META_VERIFY_TOKEN,
        "hub.challenge": "12345",
    })
    assert good.status_code == 200
    assert good.text == "12345"
    assert client.get("/webhook", params={"hub.verify_token": "bad"}).status_code == 403
    assert client.get("/login").status_code == 200


def test_empty_malformed_and_status_only_payloads_do_not_send(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    client = TestClient(main.app)
    assert client.post("/webhook", content=b"").status_code == 200
    assert client.post("/webhook", content=b"not-json").status_code == 200
    response = client.post("/webhook", json={
        "entry": [{"changes": [{"field": "messages", "value": {
            "statuses": [{"id": "wamid.status", "status": "read"}]
        }}]}]
    })
    assert response.status_code == 200
    assert sent == []


def test_text_message_sends_once_and_is_recorded(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    client = TestClient(main.app)
    payload = _message_payload()
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200
    assert sent == [("94770000000", whatsapp_service.FIXED_AUTO_REPLY)]
    with SessionLocal() as db:
        record = db.query(ProcessedWhatsAppMessage).one()
        assert record.message_id == "wamid.123"
        assert record.sender_phone == "94770000000"
        assert record.message_type == "text"


def test_signature_verification(monkeypatch):
    monkeypatch.setattr(main.settings, "META_APP_SECRET", "test-secret")
    body = b'{"entry": []}'
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    client = TestClient(main.app)
    assert client.post("/webhook", content=body).status_code == 403
    assert client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": signature}
    ).status_code == 200


def test_outbound_request_shape(monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_ACCESS_TOKEN", "secret-token")
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(whatsapp_service.settings, "META_GRAPH_API_VERSION", "v26.0")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(whatsapp_service.httpx, "post", fake_post)
    assert whatsapp_service.send_whatsapp_text("94770000000", "Reply") is True
    assert captured["url"] == "https://graph.facebook.com/v26.0/123456/messages"
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}
    assert captured["json"] == {
        "messaging_product": "whatsapp", "to": "94770000000", "type": "text",
        "text": {"body": "Reply"},
    }
    assert captured["timeout"] == 10.0


def test_missing_outbound_configuration(monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_ACCESS_TOKEN", None)
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_PHONE_NUMBER_ID", None)
    assert whatsapp_service.send_whatsapp_text("94770000000", "Reply") is False
