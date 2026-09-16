import hashlib
import hmac

import httpx
from fastapi.testclient import TestClient

import main
import ai_service
import whatsapp_service
from database import Base, SessionLocal, engine
from models import (
    ProcessedWhatsAppMessage, WhatsAppConversation, WhatsAppConversationMessage,
    WhatsAppManualSend, WhatsAppOutboundProductMessage,
)


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
    main.ensure_whatsapp_inbox_columns()
    with SessionLocal() as db:
        db.query(WhatsAppManualSend).delete()
        db.query(WhatsAppConversation).delete()
        db.query(ProcessedWhatsAppMessage).delete()
        db.query(WhatsAppConversationMessage).delete()
        db.query(WhatsAppOutboundProductMessage).delete()
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


def test_shopify_image_outbound_request_shape_and_configuration(monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_ACCESS_TOKEN", "secret-token")
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(whatsapp_service.settings, "META_GRAPH_API_VERSION", "v26.0")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(whatsapp_service.httpx, "post", fake_post)
    image_url = "https://cdn.shopify.com/s/files/1/0000/products/bike.jpg"
    assert whatsapp_service.send_whatsapp_image("94770000000", image_url, "Bike") is True
    assert captured["url"] == "https://graph.facebook.com/v26.0/123456/messages"
    assert captured["json"] == {
        "messaging_product": "whatsapp", "to": "94770000000", "type": "image",
        "image": {"link": image_url, "caption": "Bike"},
    }


def test_image_sender_rejects_arbitrary_url_without_http_call(monkeypatch):
    monkeypatch.setattr(
        whatsapp_service.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    assert whatsapp_service.send_whatsapp_image("94770000000", "http://evil.example/bike.jpg") is False


def test_bicycle_images_use_shopify_url_cap_at_three_and_failure_falls_back(monkeypatch):
    texts, images = [], []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda to, body: texts.append(body) or True)

    def image_sender(to, url, caption):
        images.append((url, caption))
        return len(images) != 2

    monkeypatch.setattr(main, "send_whatsapp_image", image_sender)
    products = [{
        "title": f"Bike {number}", "url": f"https://www.pixiepinks.shop/products/bike-{number}",
        "featured_image": f"https://cdn.shopify.com/bike-{number}.jpg",
        "featured_image_verified": True, "variants": [{"title": "26 inch", "price": "37100",
                                                          "available": True}],
    } for number in range(4)]
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda *args, **kwargs:
                        {"collection": {"title": 'Boys Size 26"'}, "products": products})
    main._reply_to_text_message("94770000000", "wamid.images",
                                "bicycle for my 12 year old boy", "Customer")
    assert len(images) == 3
    assert images[0][0] == products[0]["featured_image"]
    assert "Rs. 37,100" in images[0][1]
    assert any("Bike 1" in text for text in texts)  # failed second image's text fallback
    assert not any("Bike 3" in caption for _, caption in images)


def test_missing_featured_image_uses_text_recommendation(monkeypatch):
    texts = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda to, body: texts.append(body) or True)
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda *args, **kwargs: {
        "collection": {"title": 'Boys Size 26"'}, "products": [{
        "title": "Image-free Bike", "url": "https://www.pixiepinks.shop/products/no-image",
        "featured_image": None, "featured_image_verified": False,
        "variants": [{"title": "26 inch", "price": "37100", "available": True}],
        }]})
    main._reply_to_text_message("94770000000", "wamid.noimage",
                                "bicycle for my 12 year old boy", "Customer")
    assert any("Image-free Bike" in text and "Rs. 37,100" in text for text in texts)


def test_toy_picture_follow_up_requeries_shopify_caps_three_and_survives_failures(monkeypatch):
    texts, images, searches = [], [], []
    main._store_conversation_message("94770000000", "inbound", "do you have toys")
    main._store_conversation_message("94770000000", "outbound", "Toy results")
    products = [{
        "title": f"Toy {number}",
        "url": f"https://www.pixiepinks.shop/products/toy-{number}",
        "featured_image": (None if number == 2 else
                           f"https://cdn.shopify.com/toy-{number}.jpg"),
        "featured_image_verified": number != 2,
        "variants": [{"title": "Default Title", "price": str(2400 + number),
                      "available": True}],
    } for number in range(4)]
    monkeypatch.setattr(main, "search_products",
                        lambda query, limit=5: searches.append((query, limit)) or products)
    monkeypatch.setattr(main, "send_whatsapp_text",
                        lambda _to, body: texts.append(body) or True)

    def send_image(_to, url, caption):
        images.append((url, caption))
        if "toy-1" in url:
            raise RuntimeError("Meta unavailable")
        return True

    monkeypatch.setattr(main, "send_whatsapp_image", send_image)
    monkeypatch.setattr(main, "search_bicycles_by_collection",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    main._reply_to_text_message("94770000000", "wamid.toy-pics", "pictures ewanna", "Customer")

    assert searches == [("do you have toys", 3)]  # live re-verification uses original intent
    assert len(images) == 2
    assert images[0][0] == products[0]["featured_image"]
    assert all("Toy 3" not in caption for _, caption in images)
    assert any("Toy 1" in text for text in texts)  # Meta exception fallback
    assert any("Toy 2" in text for text in texts)  # missing-image fallback


def test_bag_photo_follow_up_uses_bag_context(monkeypatch):
    searches = []
    main._store_conversation_message("94770000000", "inbound", "show me school bags")
    main._store_conversation_message("94770000000", "outbound", "Bag results")
    monkeypatch.setattr(main, "search_products",
                        lambda query, limit=5: searches.append(query) or [])
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    main._reply_to_text_message("94770000000", "wamid.bag-pics", "send photos", "Customer")
    assert searches == ["show me school bags"]


def test_inbound_idempotency_prevents_duplicate_product_image_batch(monkeypatch):
    main._store_conversation_message("94770000000", "inbound", "do you have toys")
    main._store_conversation_message("94770000000", "outbound", "Toy results")
    product = {
        "title": "Live Toy", "url": "https://www.pixiepinks.shop/products/live-toy",
        "featured_image": "https://cdn.shopify.com/live-toy.jpg",
        "featured_image_verified": True,
        "variants": [{"title": "Default Title", "price": "2450", "available": True}],
    }
    images = []
    monkeypatch.setattr(main, "search_products", lambda query, limit=5: [product])
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    monkeypatch.setattr(main, "send_whatsapp_image",
                        lambda *args: images.append(args) or True)
    payload = _message_payload(message_id="wamid.picture-once", body="send pictures")
    client = TestClient(main.app)
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200
    assert len(images) == 1


def test_handover_overrides_product_image_follow_up(monkeypatch):
    monkeypatch.setattr(main, "search_products",
                        lambda *args: (_ for _ in ()).throw(AssertionError()))
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda _to, body: sent.append(body) or True)
    main._reply_to_text_message("94770000000", "wamid.handover-pics",
                                "send pictures to a human agent", "Customer")
    assert sent == [main.HUMAN_HANDOVER_REPLY]


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


def _live_product(handle="second-bike", available=True, price="16400"):
    return {
        "id": "gid://shopify/Product/2", "handle": handle, "title": "Kenton Racer",
        "url": f"https://www.pixiepinks.shop/products/{handle}",
        "featured_image": "https://cdn.shopify.com/bike.jpg",
        "featured_image_verified": True,
        "variants": [{"title": "Default Title", "price": price, "available": available}],
    }


def test_meta_context_id_resolves_exact_product_and_requeries_shopify(monkeypatch):
    with SessionLocal() as db:
        for number in range(1, 4):
            db.add(WhatsAppOutboundProductMessage(
                whatsapp_message_id=f"wamid.product-{number}",
                customer_phone="94770000000", shopify_product_handle=f"bike-{number}",
            ))
        db.commit()
    lookups, sent = [], []
    monkeypatch.setattr(main, "get_product_by_handle",
                        lambda handle: lookups.append(handle) or _live_product(handle, price="17900"))
    monkeypatch.setattr(main, "send_whatsapp_text",
                        lambda _to, body: sent.append(body) or True)
    payload = _message_payload(message_id="wamid.reply", body="I want this")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["context"] = {
        "id": "wamid.product-2", "from": "15550001111", "forwarded": False,
    }
    assert TestClient(main.app).post("/webhook", json=payload).status_code == 200
    assert lookups == ["bike-2"]
    assert "Kenton Racer" in sent[0] and "Rs. 17,900" in sent[0]
    assert "Would you like to place an order?" in sent[0]


def test_product_reply_price_availability_link_and_languages(monkeypatch):
    with SessionLocal() as db:
        db.add(WhatsAppOutboundProductMessage(
            whatsapp_message_id="wamid.selected", customer_phone="94770000000",
            shopify_product_handle="selected-bike",
        ))
        db.commit()
    sent = []
    monkeypatch.setattr(main, "get_product_by_handle", lambda _handle: _live_product())
    monkeypatch.setattr(main, "send_whatsapp_text", lambda _to, body: sent.append(body) or True)
    for text in ("how much?", "is this available?", "send link", "මේක ඕන", "meka ona"):
        main._reply_to_text_message("94770000000", f"in-{len(sent)}", text, "Customer",
                                    "wamid.selected")
    assert "Rs. 16,400" in sent[0]
    assert "currently available" in sent[1]
    assert "https://www.pixiepinks.shop/products/second-bike" in sent[2]
    assert all("Kenton Racer" in item for item in (sent[3], sent[4]))


def test_unavailable_reply_and_unknown_context_are_safe(monkeypatch):
    with SessionLocal() as db:
        db.add(WhatsAppOutboundProductMessage(
            whatsapp_message_id="wamid.sold", customer_phone="94770000000",
            shopify_product_handle="sold-bike",
        ))
        db.commit()
    sent = []
    monkeypatch.setattr(main, "get_product_by_handle",
                        lambda _handle: _live_product(available=False))
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: "Please clarify the product.")
    monkeypatch.setattr(main, "send_whatsapp_text", lambda _to, body: sent.append(body) or True)
    main._reply_to_text_message("94770000000", "in-sold", "I want this", None, "wamid.sold")
    main._reply_to_text_message("94770000000", "in-unknown", "I want this", None, "wamid.unknown")
    assert "currently unavailable" in sent[0]
    assert "Available" not in sent[0]
    assert sent[1] in ("Please clarify the product.", main.SHOPIFY_FALLBACK_REPLY)


def test_successful_product_send_maps_wamid_and_failed_send_does_not(monkeypatch):
    product = _live_product()
    monkeypatch.setattr(main, "send_whatsapp_image",
                        lambda *args, **kwargs: "wamid.outbound-product")
    assert main._send_product_message("94770000000", product, "caption") == "wamid.outbound-product"
    with SessionLocal() as db:
        mapping = db.query(WhatsAppOutboundProductMessage).one()
        assert (mapping.whatsapp_message_id, mapping.shopify_product_handle) == (
            "wamid.outbound-product", "second-bike")
        db.delete(mapping)
        db.commit()
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args, **kwargs: False)
    assert main._send_product_message("94770000000", product, "caption") is None
    with SessionLocal() as db:
        assert db.query(WhatsAppOutboundProductMessage).count() == 0


def test_meta_sender_can_return_outbound_message_id(monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_ACCESS_TOKEN", "token")
    monkeypatch.setattr(whatsapp_service.settings, "META_WHATSAPP_PHONE_NUMBER_ID", "123")
    response = httpx.Response(200, json={"messages": [{"id": "wamid.meta-response"}]},
                              request=httpx.Request("POST", "https://graph.facebook.com"))
    monkeypatch.setattr(whatsapp_service.httpx, "post", lambda *args, **kwargs: response)
    assert whatsapp_service.send_whatsapp_image(
        "94770000000", "https://cdn.shopify.com/bike.jpg", return_message_id=True
    ) == "wamid.meta-response"
