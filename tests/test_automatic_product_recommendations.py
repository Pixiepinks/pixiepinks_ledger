from ai_reply_test_support import reply_text, deliver_products
import copy
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import ai_service
import automatic_product_delivery as delivery
import main
import product_recommendation_service as service
from bot_configuration_service import default_snapshot, invalidate_cache, simulation_preferences, validate_snapshot
from database import Base
from models import (BotConfiguration, WhatsAppConversation, WhatsAppConversationMessage,
                    WhatsAppManualSend, WhatsAppOutboundProductMessage)

PHONE = "94770012345"


def product(index=1):
    return {"id": f"gid://shopify/Product/{index}", "handle": f"school-bag-{index}",
        "title": f"School bag {index}", "description": "Suitable for school-age children; check dimensions for fit.",
        "product_type": "School bags", "tags": ["unisex", "age 7"],
        "status": "ACTIVE", "published": True, "featured_image": "https://cdn.shopify.com/bag.jpg",
        "variants": [{"id": f"gid://shopify/ProductVariant/{index}", "title": "Default Title",
            "price": "1500.00", "currency": "LKR", "available": True,
            "inventory_tracked": True, "inventory_quantity": 3, "selected_options": {}}],
        "selected_variant_id": f"gid://shopify/ProductVariant/{index}"}


def resolved(index=1):
    p = product(index)
    return {"id": p["id"], "variant_id": p["selected_variant_id"], "handle": p["handle"], "title": p["title"],
        "variant_title": "Default Title", "sku": "BAG", "price": Decimal("1500.00"), "currency": "LKR",
        "published": True, "available": True, "inventory_tracked": True, "inventory_quantity": 3,
        "image": p["featured_image"], "url": "https://www.pixiepinks.shop/products/" + p["handle"]}


def config():
    snapshot = default_snapshot()
    snapshot["global"].update(maximum_products=10, automatic_product_attachments=True,
                              instructions="Always reply in Singlish, even for English input.")
    return snapshot


@pytest.fixture
def provider(monkeypatch):
    queue, calls = [], []
    monkeypatch.setattr(ai_service.settings, "OPENAI_API_KEY", "fake-key")
    def create(**kwargs):
        calls.append(kwargs)
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(output_text=json.dumps(value), usage=SimpleNamespace(total_tokens=100))
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    return queue, calls


def ready(queue, count=10):
    queue.extend([{"action": "search", "reply": "Hari, balamu.", "query": "school bag"},
        {"reply": "Oyagen thorathuru anuwa me options balanna.", "products": [
            {"product_index": index, "variant_id": f"gid://shopify/ProductVariant/{index + 1}"}
            for index in range(count)]}])


def test_discovery_asks_without_search_and_preserves_known_answers(provider):
    queue, calls = provider
    queue.append({"action": "clarify", "reply": "Daruwage wayasa keeyada?", "query": ""})
    result = service.recommend(config(), "Do you have bags?", [{"direction": "inbound", "message_text": "For a girl"}],
        search=lambda *args, **kwargs: pytest.fail("Searched before clarification"))
    assert not result.products and result.reply == "Daruwage wayasa keeyada?" and len(calls) == 1
    assert "For a girl" in calls[0]["input"][0]["content"]
    assert "Singlish" in calls[0]["instructions"] and calls[0]["store"] is False


def test_ten_relevant_allowlisted_products_and_lower_config_cap(provider):
    queue, calls = provider
    ready(queue)
    result = service.recommend(config(), "Unisex school bags for age 7", search=lambda *a, **k: [product(i) for i in range(1, 13)])
    assert len(result.products) == 10 and result.tokens == 200 and len(calls) == 2
    assert "check dimensions for fit" in calls[1]["input"][0]["content"]
    assert all(p["selected_variant_id"] == p["variants"][0]["id"] for p in result.products)
    ready(queue)
    snapshot = config(); snapshot["global"]["maximum_products"] = 2
    result = service.recommend(snapshot, "Bags for age 7 unisex", search=lambda *a, **k: [product(i) for i in range(1, 11)])
    assert len(result.products) == 2


@pytest.mark.parametrize("change", ["zero", "negative", "untracked", "missing", "unavailable", "unpublished", "draft", "image", "price"])
def test_unsafe_or_unavailable_products_are_excluded(change):
    p = product()
    if change == "zero": p["variants"][0]["inventory_quantity"] = 0
    if change == "negative": p["variants"][0]["inventory_quantity"] = -1
    if change == "untracked": p["variants"][0]["inventory_tracked"] = False
    if change == "missing": p["variants"][0]["inventory_quantity"] = None
    if change == "unavailable": p["variants"][0]["available"] = False
    if change == "unpublished": p["published"] = False
    if change == "draft": p["status"] = "DRAFT"
    if change == "image": p["featured_image"] = "https://attacker.test/x.jpg"
    if change == "price": p["variants"][0]["price"] = "NaN"
    assert service.eligible_catalog([p]) == []


def test_no_matches_or_seen_products_never_fill_with_unrelated_items(provider):
    queue, _ = provider
    queue.extend([{"action": "search", "reply": "Hari", "query": "school bag"},
                  {"reply": "Thawa wisthara kiyanna puluwanda?", "products": []}])
    result = service.recommend(config(), "School bags", search=lambda *a, **k: [product()], seen_handles=["school-bag-1"])
    assert result.status == "GENERATED" and not result.products


def test_unknown_model_variant_cannot_send_and_provider_failure_is_safe(provider):
    queue, _ = provider
    ready(queue, 1); queue[-1]["products"][0]["variant_id"] = "gid://shopify/ProductVariant/999"
    result = service.recommend(config(), "School bags", search=lambda *a, **k: [product()])
    assert result.status == "PROVIDER_FAILED" and not result.products
    queue.append(RuntimeError("private provider data"))
    assert service.recommend(config(), "Hi").status == "PROVIDER_FAILED"


@pytest.fixture
def store(tmp_path, monkeypatch):
    pg_url = os.environ.get("AUTO_PRODUCTS_TEST_DATABASE_URL")
    base_engine = None
    if pg_url:
        base_engine = create_engine(pg_url)
        if base_engine.url.database != "pixiepinks_test":
            pytest.fail("Use the task-owned pixiepinks_test database only")
        schema = "auto_products_" + uuid.uuid4().hex
        with base_engine.begin() as conn: conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = base_engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'auto.db'}", connect_args={"check_same_thread": False, "timeout": 20})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(BotConfiguration(published_version=1, published_snapshot=config(), draft_snapshot=config()))
        db.add(WhatsAppConversation(phone_number=PHONE, mode="AI", last_customer_message_at=datetime.utcnow()))
        db.commit()
    monkeypatch.setattr(main, "SessionLocal", sessions)
    invalidate_cache()
    yield sessions
    invalidate_cache()
    if base_engine:
        with base_engine.begin() as conn: conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base_engine.dispose()
    else:
        engine.dispose()


def test_ten_attachments_have_price_image_quote_mapping_and_no_replay(store):
    sends = []
    def send(phone, image, body):
        sends.append((phone, image, body)); return "SENT", f"wamid.{len(sends)}"
    def resolve(pid, vid): return resolved(int(pid.rsplit("/", 1)[1]))
    products = [product(i) for i in range(1, 13)]
    assert len(deliver_products(delivery, store, PHONE, "inbound-1", products, resolve, send)) == 10
    assert len(sends) == 10 and "Rs. 1,500.00" in sends[0][2]
    assert deliver_products(delivery, store, PHONE, "inbound-1", products, resolve, send) == []
    assert len(sends) == 10
    with store() as db:
        rows = db.query(WhatsAppConversationMessage).all()
        assert len(rows) == 10 and all(row.product_image_url and row.product_price == 1500 for row in rows)
        assert db.query(WhatsAppOutboundProductMessage).count() == 10


@pytest.mark.parametrize("change", ["stock", "price", "image", "variant", "variant_title", "human", "expired", "disabled", "newer"])
def test_delivery_rechecks_and_stops_invalid_cards(store, change):
    def resolve(pid, vid):
        fresh = resolved()
        if change == "stock": fresh["inventory_quantity"] = 0
        if change == "price": fresh["price"] = Decimal("9999")
        if change == "image": fresh["image"] = "https://bad.test/x.jpg"
        if change == "variant": fresh["variant_id"] = "gid://shopify/ProductVariant/999"
        if change == "variant_title": fresh["variant_title"] = "Different size"
        with store() as db:
            conv = db.query(WhatsAppConversation).one()
            if change == "human": conv.mode = "HUMAN"
            if change == "expired": conv.last_customer_message_at = datetime.utcnow() - timedelta(hours=25)
            if change == "disabled":
                row = db.query(BotConfiguration).one(); snapshot = copy.deepcopy(row.published_snapshot)
                snapshot["global"]["automatic_product_attachments"] = False; row.published_snapshot = snapshot
            if change == "newer": db.add(WhatsAppConversationMessage(phone_number=PHONE, direction="inbound",
                message_text="Stop, different product", whatsapp_message_id="newer"))
            db.commit()
        return fresh
    assert not deliver_products(delivery, store, PHONE, "old", [product()], resolve,
        lambda *a: pytest.fail("Sent an invalid card"))


def test_uncertain_provider_send_is_not_retried_or_fallen_back(store):
    calls = []
    def send(*args): calls.append(args); return "UNKNOWN", None
    assert deliver_products(delivery, store, PHONE, "uncertain", [product(), product(2)], lambda *a: resolved(), send) == []
    assert len(calls) == 1
    deliver_products(delivery, store, PHONE, "uncertain", [product()], lambda *a: resolved(), send)
    assert len(calls) == 1
    with store() as db:
        assert db.query(WhatsAppManualSend).filter_by(status="UNKNOWN").count() == 1


def test_concurrent_batch_claim_sends_only_once(store):
    calls = []
    def send(*args): calls.append(args); return "SENT", "wamid.once"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver_products(delivery, store, PHONE, "concurrent", [product()], lambda *a: resolved(), send), range(2)))
    assert len(calls) == 1 and sum(len(value) for value in results) == 1


def test_settings_accept_ten_and_old_snapshots_default_off():
    snapshot = config()
    assert validate_snapshot(snapshot)[0] == [] and simulation_preferences(snapshot)["maximum_products"] == 10
    del snapshot["global"]["automatic_product_attachments"]
    assert simulation_preferences(snapshot)["automatic_product_attachments"] is False
    snapshot["global"]["maximum_products"] = 11
    assert validate_snapshot(snapshot)[0]


def test_catalog_filters_zero_stock_before_limit_and_never_browses_empty_query(monkeypatch):
    import shopify_catalog_service as catalog
    nodes = []
    for index in range(1, 21):
        nodes.append({"id": f"gid://shopify/Product/{index}", "handle": f"school-bag-{index}",
            "title": f"School bag {index}", "productType": "School bags", "status": "ACTIVE", "publishedAt": "2026-10-01",
            "featuredImage": {"url": "https://cdn.shopify.com/bag.jpg"},
            "variants": {"nodes": [{"id": f"gid://shopify/ProductVariant/{index}", "title": "Default Title",
                "price": "1500", "availableForSale": True, "inventoryQuantity": 0 if index <= 10 else 3,
                "inventoryItem": {"tracked": True}}]}})
    monkeypatch.setattr(catalog.client, "graphql", lambda *a, **k: {"products": {"nodes": nodes}})
    result = catalog.search_available_products("school bag", limit=10)
    assert len(result) == 10 and result[0]["handle"] == "school-bag-11"
    monkeypatch.setattr(catalog.client, "graphql", lambda *a, **k: pytest.fail("Empty search browsed catalog"))
    assert catalog.search_available_products("?") == []


def test_database_failure_after_provider_send_never_replays(store):
    from sqlalchemy import event
    calls = []
    with store() as db: engine = db.get_bind()
    def fail(conn, cursor, statement, *args):
        if statement.startswith("INSERT INTO whatsapp_conversation_messages"):
            raise RuntimeError("simulated database outage")
    def send(*args):
        calls.append(args)
        event.listen(engine, "before_cursor_execute", fail)
        return "SENT", "wamid.saved-by-provider"
    try:
        deliver_products(delivery, store, PHONE, "db-outage", [product()], lambda *a: resolved(), send)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    deliver_products(delivery, store, PHONE, "db-outage", [product()], lambda *a: resolved(), send)
    assert len(calls) == 1


def test_live_followup_uses_history_then_attaches_verified_products(store, provider, monkeypatch):
    queue, _ = provider
    ready(queue, 1)
    with store() as db:
        db.add_all([WhatsAppConversationMessage(phone_number=PHONE, direction="inbound", message_text="Do you have school bags?"),
            WhatsAppConversationMessage(phone_number=PHONE, direction="outbound", message_text="Age and preference?"),
            WhatsAppConversationMessage(phone_number=PHONE, direction="inbound", message_text="Girl age 7", whatsapp_message_id="followup")])
        db.commit()
    import shopify_catalog_service
    monkeypatch.setattr(shopify_catalog_service, "search_available_products", lambda *a, **k: [product()])
    monkeypatch.setattr(main, "resolve_inbox_product", lambda *a: resolved())
    texts, images = [], []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *a, **k: texts.append(a) or "wamid.intro")
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *a, **k: images.append(a) or ("SENT", "wamid.product"))
    reply_text(main, PHONE, "followup", "Girl age 7", None)
    assert len(images) == 1 and "1,500.00" in images[0][2] and len(texts) == 1
    assert texts[0][1] == "Oyagen thorathuru anuwa me options balanna."
    assert len(provider[1]) == 2


def test_published_water_bottle_request_uses_automatic_route_not_legacy_links(store, provider, monkeypatch, caplog):
    import shopify_catalog_service
    queue, _ = provider
    queue.extend([{"action": "search", "reply": "Hari", "query": "water bottle"},
                  {"reply": "Galapena stock thiyena bottles hambune naha.", "products": []}])
    unavailable = product(); unavailable["title"] = "Water bottle"
    unavailable["variants"][0]["inventory_quantity"] = 0
    monkeypatch.setattr(shopify_catalog_service, "search_available_products", lambda *a, **k: [unavailable])
    monkeypatch.setattr(main, "_generate_with_learning", lambda *a: pytest.fail("Fell through to legacy text/links"))
    texts = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *a, **k: texts.append(a[1]) or "wamid.no-stock")
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *a, **k: pytest.fail("Sent zero-stock product"))
    with caplog.at_level("INFO"):
        reply_text(main, PHONE, "water-bottle-turn", "Do you have water bottle", None)
    assert texts == ["Galapena stock thiyena bottles hambune naha."]
    assert "automatic_products=True product_images=True bicycle_route=False" in caplog.text


def test_live_requirement_question_is_not_overwritten_by_legacy_reply(store, provider, monkeypatch):
    queue, calls = provider
    queue.append({"action": "clarify", "reply": "Daruwage wayasa keeyada?", "query": ""})
    monkeypatch.setattr(main, "search_products", lambda *a, **k: pytest.fail("Searched before requirements were known"))
    monkeypatch.setattr(main, "_generate_with_learning", lambda *a: pytest.fail("Overwrote clarification"))
    texts = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *a, **k: texts.append(a[1]) or "wamid.question")
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *a, **k: pytest.fail("Sent product before clarification"))
    reply_text(main, PHONE, "clarify-bags", "Do you have school bags?", None)
    assert texts == ["Daruwage wayasa keeyada?"] and len(calls) == 1


@pytest.mark.parametrize("age_answer", ["6", "6 years", "age 6", "awurudu 6"])
@pytest.mark.parametrize("action", ["clarify", "reply"])
def test_answered_age_moves_to_catalog_even_if_model_repeats_question(provider, age_answer, action):
    queue, calls = provider
    queue.extend([{"action": action, "reply": "Age eka kiyanna puluwan da?", "query": ""},
        {"reply": "Me galapena bottles balanna.", "products": [{"product_index": 0,
            "variant_id": "gid://shopify/ProductVariant/1"}]}])
    context = [
        {"direction": "inbound", "message_text": "Do you have water bottle"},
        {"direction": "outbound", "message_text": "Kids lata da? Boys, girls, unisex da?"},
        {"direction": "inbound", "message_text": "Boy"},
        {"direction": "outbound", "message_text": "Boys kids lata water bottle ekak? Age eka kiyanna puluwan da?"},
    ]
    queries = []
    result = service.recommend(config(), age_answer, context,
        search=lambda query, **kwargs: queries.append(query) or [product()])
    assert queries == ["water bottle"] and len(result.products) == 1
    assert result.reply == "Me galapena bottles balanna."
    for call in calls:
        known = json.loads(call["input"][0]["content"])["customer_requirements"]
        assert known["category"] == "water bottle" and known["age_years"] == 6 and known["preference"] == "boy"


def test_budget_answer_is_not_an_age_and_a_distinct_question_can_remain(provider):
    from product_requirements import requirements
    context = [{"direction": "inbound", "message_text": "Water bottle for a boy"},
               {"direction": "outbound", "message_text": "What is your budget?"}]
    assert requirements("6", context)["age_years"] is None
    assert requirements("size 6", context)["age_years"] is None
    queue, _ = provider
    queue.append({"action": "clarify", "reply": "Capacity eka 500ml da 750ml da?", "query": ""})
    context = [{"direction": "inbound", "message_text": "Water bottle for a boy"},
               {"direction": "outbound", "message_text": "Daruwage wayasa keeyada?"}]
    result = service.recommend(config(), "6", context, search=lambda *a, **k: pytest.fail("Ignored distinct missing detail"))
    assert result.stage == "clarify" and not result.products


def test_cancellation_is_not_overridden_by_completed_requirements(provider):
    queue, _ = provider
    queue.append({"action": "reply", "reply": "Hari, nawaththannam.", "query": ""})
    context = [{"direction": "inbound", "message_text": "Water bottle for a boy age 6"},
               {"direction": "outbound", "message_text": "Age eka kiyanna puluwan da?"}]
    result = service.recommend(config(), "No need, age 6", context,
        search=lambda *a, **k: pytest.fail("Searched after cancellation"))
    assert result.stage == "reply" and not result.products


@pytest.mark.parametrize("question", ["Age eka kiyanna puluwan da?", "How old is he?"])
def test_live_bare_age_in_water_bottle_flow_never_becomes_a_bicycle_request(store, provider, monkeypatch, question):
    queue, _ = provider
    queue.extend([{"action": "clarify", "reply": "Age eka kiyanna puluwan da?", "query": ""},
                  {"reply": "Galapena available bottles hambune naha.", "products": []}])
    with store() as db:
        for direction, text in [("inbound", "Do you have water bottle"), ("outbound", "Boy, girl or unisex?"),
                                ("inbound", "Boy"), ("outbound", question)]:
            db.add(WhatsAppConversationMessage(phone_number=PHONE, direction=direction, message_text=text))
        db.commit()
    import shopify_catalog_service
    queries, texts = [], []
    monkeypatch.setattr(shopify_catalog_service, "search_available_products", lambda query, **k: queries.append(query) or [])
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda *a, **k: pytest.fail("Interpreted bottle age as bicycle sizing"))
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *a, **k: texts.append(a[1]) or "wamid.none")
    reply_text(main, PHONE, "age-six", "6", None)
    assert queries == ["water bottle"] and texts == ["Galapena available bottles hambune naha."]


@pytest.mark.parametrize("answer, preference", [("Duwata", "girl"), ("Puthata", "boy")])
def test_singlish_preference_answer_does_not_repeat_bicycle_discovery(provider, answer, preference):
    from bicycle_recommendation import detect_bicycle_preference
    queue, calls = provider
    queue.extend([
        {"action": "clarify", "reply": "Puthata da duwata da?", "query": ""},
        {"reply": "Galapena available products hambune naha.", "products": []},
    ])
    context = [
        {"direction": "inbound", "message_text": "Bicycle for 7 years"},
        {"direction": "outbound", "message_text": "Puthata da, duwata da?"},
    ]
    queries = []
    result = service.recommend(config(), answer, context,
        search=lambda query, **kwargs: queries.append(query) or [])
    assert detect_bicycle_preference(answer) == preference
    assert queries == ["bicycle"]
    assert result.reply == "Galapena available products hambune naha."
    assert not result.products
    for call in calls:
        known = json.loads(call["input"][0]["content"])["customer_requirements"]
        assert known["age_years"] == 7
        assert known["preference"] == preference
