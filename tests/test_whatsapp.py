import hashlib
import hmac

import httpx
from fastapi.testclient import TestClient

import main
import ai_service
import whatsapp_service
from database import Base, SessionLocal, engine
from models import ProcessedWhatsAppMessage, WhatsAppConversationMessage


def _message_payload(message_id="wamid.123", message_type="text", body="Hello"):
    message = {
        "from": "94770000000",
        "id": message_id,
        "timestamp": "1710000000",
        "type": message_type,
    }
    if message_type == "text":
        message["text"] = {"body": body}
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
        db.query(WhatsAppConversationMessage).delete()
        db.commit()
    main.settings.META_APP_SECRET = None
    main.settings.OPENAI_API_KEY = None


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
    for protected_route in ("/dashboard", "/accounts", "/crm", "/crm/leads"):
        assert client.get(protected_route, follow_redirects=False).status_code == 303


def test_empty_malformed_and_status_only_payloads_do_not_send(monkeypatch):
    sent = []
    ai_calls = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: ai_calls.append(args))
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
    assert ai_calls == []


def test_text_message_sends_once_and_is_recorded(monkeypatch):
    sent = []
    ai_calls = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main,
        "generate_customer_reply",
        lambda *args: ai_calls.append(args) or "Hello from PixiePinks!",
    )
    client = TestClient(main.app)
    payload = _message_payload()
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200
    assert sent == [("94770000000", "Hello from PixiePinks!")]
    assert len(ai_calls) == 1
    with SessionLocal() as db:
        record = db.query(ProcessedWhatsAppMessage).one()
        assert record.message_id == "wamid.123"
        assert record.sender_phone == "94770000000"
        assert record.message_type == "text"
        messages = db.query(WhatsAppConversationMessage).order_by(
            WhatsAppConversationMessage.id
        ).all()
        assert [(item.direction, item.message_text) for item in messages] == [
            ("inbound", "Hello"),
            ("outbound", "Hello from PixiePinks!"),
        ]


def test_human_handover_bypasses_ai(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "generate_customer_reply", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(body="Can I speak to a person?")
    )
    assert response.status_code == 200
    assert sent == [("94770000000", main.HUMAN_HANDOVER_REPLY)]
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(
            response_kind="handover"
        ).count() == 1


def test_ai_failure_uses_safe_fallback_and_webhook_stays_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "generate_customer_reply", lambda *args: (_ for _ in ()).throw(RuntimeError())
    )
    response = TestClient(main.app).post("/webhook", json=_message_payload())
    assert response.status_code == 200
    assert sent == [("94770000000", ai_service.FALLBACK_REPLY)]


def test_missing_openai_key_returns_fallback():
    main.settings.OPENAI_API_KEY = None
    assert ai_service.generate_customer_reply("Hello") == ai_service.FALLBACK_REPLY


def test_ai_service_uses_responses_api_and_cleans_reply(monkeypatch):
    captured = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"output_text": "**Hello** [shop](https://example.com)"})()

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setattr(ai_service, "OpenAI", FakeClient)
    monkeypatch.setattr(ai_service.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ai_service.settings, "OPENAI_MODEL", "test-model")
    reply = ai_service.generate_customer_reply(
        "Hello", recent_context=[{"direction": "outbound", "message_text": "Welcome"}]
    )
    assert reply == "Hello shop"
    assert captured["client"] == {
        "api_key": "test-key", "timeout": 12.0, "max_retries": 1
    }
    assert captured["model"] == "test-model"
    assert captured["input"][-1] == {"role": "user", "content": "Hello"}
    assert captured["max_output_tokens"] == 300


def test_unsupported_media_does_not_call_ai(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "generate_customer_reply", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(message_type="image")
    )
    assert response.status_code == 200
    assert sent == [("94770000000", whatsapp_service.UNSUPPORTED_MESSAGE_REPLY)]


def test_sinhala_text_uses_ai_path(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    monkeypatch.setattr(
        main, "generate_customer_reply", lambda *args: calls.append(args) or "ආයුබෝවන්!"
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(body="මට තෑග්ගක් ගැන දැනගන්න ඕනේ")
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "මට තෑග්ගක් ගැන දැනගන්න ඕනේ"


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


def test_generic_bicycle_question_starts_guided_flow_without_shopify(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "search_products", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(message_id="wamid.product", body="Do you have bicycles?")
    )
    assert response.status_code == 200
    assert sent == [("94770000000", "Yes, we do 🚲 Is the bicycle for a boy or a girl?")]


def test_complete_product_request_does_not_add_unrelated_history(monkeypatch):
    searches = []
    monkeypatch.setattr(main, "search_products", lambda query: searches.append(query) or [])
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: "No matches")
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    main._store_conversation_message("94770000000", "inbound", "Hello there")

    main._reply_to_text_message(
        "94770000000", "wamid.clean-search", "Show me size 20 bicycles", "Customer"
    )

    assert searches == ["Show me size 20 bicycles"]


def test_non_product_greeting_does_not_query_shopify(monkeypatch):
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    monkeypatch.setattr(
        main, "search_products", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: "Hello!")
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(message_id="wamid.greeting", body="Hello")
    )
    assert response.status_code == 200


def test_shopify_failure_uses_catalog_fallback_and_webhook_stays_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "search_products",
        lambda *args: (_ for _ in ()).throw(main.ShopifyCatalogError("safe")),
    )
    monkeypatch.setattr(
        main, "generate_customer_reply", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(
            message_id="wamid.shopify-fail", body="Any size 20 bikes in stock?"
        )
    )
    assert response.status_code == 200
    assert sent == [("94770000000", main.SHOPIFY_FALLBACK_REPLY)]


def test_handover_bypasses_shopify_too(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args))
    monkeypatch.setattr(
        main, "search_products", lambda *args: (_ for _ in ()).throw(AssertionError())
    )
    response = TestClient(main.app).post(
        "/webhook", json=_message_payload(
            message_id="wamid.handover-product", body="I need a human to check bicycle stock"
        )
    )
    assert response.status_code == 200
    assert sent == [("94770000000", main.HUMAN_HANDOVER_REPLY)]
