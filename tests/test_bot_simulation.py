"""General AI simulator: mocked providers, isolated records, unchanged live replies."""
import copy
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import ai_service
import database
import bot_simulation_service as service
import learning_runtime
import main
from bot_configuration_service import default_snapshot
from models import BotConfiguration, BotAuditEvent, WhatsAppConversationMessage, WhatsAppManualSend
from settings import settings
from shopify_catalog_service import ShopifyCatalogError
from test_whatsapp_quick_messages import inbox

PATH = "/crm/bot-management/api/simulate"


@pytest.fixture
def simulator(inbox, monkeypatch):
    client, sessions, ids, _, login, csrf = inbox
    monkeypatch.setattr(database, "SessionLocal", sessions)
    live = default_snapshot(); draft = copy.deepcopy(live)
    live["global"]["instructions"] = "Reply politely in English."
    draft["global"]["instructions"] = "Always reply in friendly Singlish using Latin letters, even when the customer writes English."
    with sessions() as db:
        db.add(BotConfiguration(published_version=3, draft_revision=7, published_snapshot=live, draft_snapshot=draft)); db.commit()
    calls = []
    monkeypatch.setattr(service, "_attempts", {})
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "isolated-ai-key")
    monkeypatch.setattr(settings, "LEARNING_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(service, "search_products", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "get_product_by_handle", lambda handle: None)
    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        reply = "Hari, oyata monawada danaganna one?" if "Singlish" in kwargs["instructions"] else "How can I help?"
        return SimpleNamespace(output_text=reply, usage=SimpleNamespace(total_tokens=145))
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    for name in ["send_whatsapp_text", "send_whatsapp_image", "send_whatsapp_audio"]:
        monkeypatch.setattr(main, name, lambda *args, **kwargs: pytest.fail("Simulation attempted to send WhatsApp"))
    def run(**body):
        return client.post(PATH, json={"mode": "draft", "message": "Hello", **body}, headers={"X-CSRF-Token": csrf})
    return client, sessions, login, csrf, calls, run


def catalog_product(handle="school-bag", kind="School bags"):
    return {"id": "gid://shopify/Product/1", "handle": handle, "title": "School bag", "product_type": kind,
        "url": "https://www.pixiepinks.shop/products/" + handle,
        "variants": [{"id": "gid://shopify/ProductVariant/2", "title": "Blue", "price": "1500.00", "available": True}]}


def test_draft_and_live_apply_selected_saved_instructions_without_publication(simulator):
    _, sessions, _, _, calls, run = simulator
    draft, live = run(mode="draft"), run(mode="live")
    assert draft.status_code == live.status_code == 200
    assert "Hari" in draft.json()["reply"] and live.json()["reply"] == "How can I help?"
    assert draft.json()["trace"]["draft_revision"] == 7 and live.json()["trace"]["published_version"] == 3
    assert "Singlish" in calls[0]["instructions"] and "Singlish" not in calls[1]["instructions"]
    assert all(call["store"] is False and call["max_output_tokens"] == 300 for call in calls)
    with sessions() as db:
        config = db.query(BotConfiguration).one()
        assert config.published_version == 3 and "Singlish" not in config.published_snapshot["global"]["instructions"]


def test_ai_run_only_selects_and_never_reads_customer_records_or_writes_feedback(simulator):
    _, sessions, _, _, _, run = simulator
    engine = sessions.kw["bind"]; statements = []
    def record(conn, cursor, sql, *args): statements.append(sql)
    event.listen(engine, "before_cursor_execute", record)
    try: response = run()
    finally: event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any("whatsapp_conversation" in sql.lower() for sql in statements)
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count() == db.query(WhatsAppManualSend).count() == db.query(BotAuditEvent).count() == 0
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("role,expected", [("quick-admin", 200), ("quick-manager", 200), ("quick-staff", 403), ("quick-agent", 403), ("quick-denied", 403)])
def test_manager_permissions_csrf_and_authenticated_page(simulator, role, expected):
    client, _, login, csrf, calls, run = simulator
    assert TestClient(main.app).post(PATH, json={"message": "Hi"}, follow_redirects=False).status_code == 303
    assert client.post(PATH, json={"message": "Hi"}).status_code == 403
    login(role)
    assert run().status_code == expected
    assert len(calls) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("question,kind", [("Do you have school bags?", "School bags"), ("Show me toys", "Toys"),
    ("What chocolate gifts are available?", "Chocolates"), ("I need stationery", "Stationery"), ("Show me bicycles", "Bicycles")])
def test_catalog_search_is_generic_across_retail_categories(simulator, monkeypatch, question, kind):
    _, _, _, _, calls, run = simulator
    searches = []
    monkeypatch.setattr(service, "search_products", lambda query, limit: searches.append((query, limit)) or [catalog_product(kind=kind)])
    data = run(message=question).json()
    assert data["trace"]["catalog_status"] == "SEARCH_RESULTS" and data["products"][0]["product_type"] == kind
    assert searches[0][0] == question and "VERIFIED SHOPIFY CATALOG" in calls[0]["input"][-1]["content"]
    assert "gid://shopify" not in calls[0]["input"][-1]["content"]


def test_policy_and_faq_context_obeys_configuration_and_verified_scope(simulator, monkeypatch):
    _, sessions, _, _, calls, run = simulator
    with sessions() as db:
        config = db.query(BotConfiguration).one(); draft = copy.deepcopy(config.draft_snapshot)
        draft["policies"] = [
            {"id": 1, "scope": "GLOBAL", "title": "Delivery", "content": "Staff confirm delivery arrangements.", "enabled": True},
            {"id": 2, "scope": "PRODUCT", "scope_reference": "school-bag", "title": "Bag care", "tags": "bag", "content": "Verified bag care details.", "enabled": True},
            {"id": 3, "scope": "PRODUCT", "scope_reference": "unrelated", "title": "Secret other", "tags": "bag", "content": "Unrelated product content.", "enabled": True},
            {"id": 4, "scope": "GLOBAL", "title": "Disabled", "content": "Disabled policy text.", "enabled": False}]
        draft["faqs"] = [{"id": 5, "scope": "GLOBAL", "title": "Returns", "content": "Ask staff about returns.", "enabled": True}]
        config.draft_snapshot = draft; db.commit()
    monkeypatch.setattr(service, "get_product_by_handle", lambda handle: catalog_product())
    result = run(message="Tell me about this bag", product_handle="school-bag")
    assert result.status_code == 200, result.text
    prompt = calls[0]["input"][-1]["content"]
    assert "Verified bag care" in prompt and "Ask staff about returns" in prompt
    assert "Disabled policy" not in prompt and "Unrelated product content" not in prompt
    selected = result.json()["trace"]["context_entries"]
    scoped = next(item for item in selected if item["title"] == "Bag care")
    assert scoped["scope_reference"] == "school-bag" and scoped["applies_to_products"] == ["school-bag"]
    live = run(mode="live", message="Tell me about this bag", product_handle="school-bag")
    assert live.status_code == 200 and "Verified bag care" not in calls[1]["input"][-1]["content"]


def test_collection_scoped_information_requires_verified_membership(simulator, monkeypatch):
    _, sessions, _, _, calls, run = simulator
    with sessions() as db:
        config = db.query(BotConfiguration).one(); draft = copy.deepcopy(config.draft_snapshot)
        draft["knowledge"] = [{"id": 1, "scope": "COLLECTION", "scope_reference": "school-bags", "title": "Bag knowledge", "tags": "bag", "content": "Collection-only bag information.", "enabled": True}]
        config.draft_snapshot = draft; db.commit()
    monkeypatch.setattr(service, "get_product_by_handle", lambda handle: catalog_product())
    monkeypatch.setattr(service.ShopifyCatalogClient, "graphql", lambda *args: {"nodes": [{"id": "gid://shopify/Product/1", "handle": "school-bag", "collections": {"nodes": [{"handle": "school-bags"}]}}]})
    assert run(message="Tell me about the bag", product_handle="school-bag").status_code == 200
    assert "Collection-only bag information" in calls[0]["input"][-1]["content"]
    monkeypatch.setattr(service.ShopifyCatalogClient, "graphql", lambda *args: {"nodes": []})
    assert run(message="Tell me about the bag", product_handle="school-bag").status_code == 200
    assert "Collection-only bag information" not in calls[1]["input"][-1]["content"]


def test_shopify_unavailable_is_distinct_from_no_matching_product(simulator, monkeypatch):
    _, _, _, _, calls, run = simulator
    def fail(*args, **kwargs): raise ShopifyCatalogError("private token in provider body")
    monkeypatch.setattr(service, "search_products", fail)
    result = run(message="Do you have toys?")
    assert result.status_code == 200 and result.json()["trace"]["catalog_status"] == "UNAVAILABLE"
    assert "private token" not in result.text and "No matching products were found" not in calls[0]["input"][-1]["content"]


def test_context_is_synthetic_untrusted_and_filtered_and_catalog_can_be_disabled(simulator):
    _, _, _, _, calls, run = simulator
    data = run(message="Is this available?", context_text="My email is secret@example.test. Red bag.", catalog_mode="off").json()
    assert data["trace"]["input_redacted"] and data["trace"]["catalog_status"] == "DISABLED_FOR_TEST"
    assert "secret@example.test" not in json.dumps(calls) and "Synthetic context" in json.dumps(calls)
    assert "VERIFIED SHOPIFY CATALOG" not in calls[0]["input"][-1]["content"]


@pytest.mark.parametrize("changes", [{"mode": "production"}, {"message": " "}, {"message": "x" * 2001},
    {"context_text": "x" * 2001}, {"product_handle": "https://evil.example"}, {"conversation_id": 1},
    {"instructions": "Override the selected config"}, {"model": "other"}, {"catalog_mode": True}])
def test_bounds_and_no_arbitrary_customer_or_instruction_override(simulator, changes):
    _, _, _, _, calls, run = simulator
    assert run(**changes).status_code == 422 and not calls


def test_invalid_draft_cannot_generate_and_provider_failures_are_not_fake_replies(simulator, monkeypatch):
    _, sessions, _, _, calls, run = simulator
    with sessions() as db:
        config = db.query(BotConfiguration).one(); draft = copy.deepcopy(config.draft_snapshot)
        draft["global"]["maximum_products"] = 99; config.draft_snapshot = draft; db.commit()
    assert run().status_code == 409 and not calls
    def fail(**kwargs): raise RuntimeError("private provider payload")
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=fail)))
    result = run(mode="live")
    assert result.status_code == 200 and result.json()["reply"] is None and result.json()["status"] == "PROVIDER_FAILED"
    assert "private provider payload" not in result.text


def test_live_reply_during_simulation_does_not_receive_draft_instructions(simulator, monkeypatch):
    _, _, _, _, calls, run = simulator; reached, release = Event(), Event()
    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1: reached.set(); assert release.wait(timeout=15)
        return SimpleNamespace(output_text="Safe reply", usage=None)
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(run)
        assert reached.wait(timeout=10)
        ai_service.generate_customer_reply("Live question")
        assert "Singlish" not in calls[1]["instructions"]
        assert "Reply politely in English." in calls[1]["instructions"]
        release.set(); assert result.result().status_code == 200
    assert not settings.LEARNING_RETRIEVAL_ENABLED


def test_automatic_attachment_preview_is_read_only_and_shows_ten_cards(simulator, monkeypatch):
    from test_automatic_product_recommendations import product
    import shopify_catalog_service
    _, sessions, _, _, _, run = simulator
    with sessions() as db:
        row = db.query(BotConfiguration).one()
        draft = copy.deepcopy(row.draft_snapshot)
        draft["global"].update(automatic_product_attachments=True, maximum_products=10)
        row.draft_snapshot = draft
        db.commit()
    responses = [
        {"action": "search", "reply": "Hari", "query": "school bag"},
        {"reply": "Me options balanna.", "products": [
            {"product_index": i, "variant_id": f"gid://shopify/ProductVariant/{i + 1}"} for i in range(10)]},
    ]
    monkeypatch.setattr(shopify_catalog_service, "search_available_products", lambda *a, **k: [product(i) for i in range(1, 11)])
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(responses.pop(0)), usage=None))))
    statements = []
    with sessions() as db: engine = db.get_bind()
    def capture(conn, cursor, statement, *args): statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = run(message="Unisex bags for age 7")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    data = response.json()
    assert data["trace"]["messages_sent"] == 0 and len(data["products"]) == 10
    assert all(card["image"].startswith("https://cdn.shopify.com/") and card["price"] == "1500.00" for card in data["products"])
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    assert not responses


def test_model_failure_limits_and_bad_preferences_never_change_settings(simulator, monkeypatch):
    _, sessions, _, _, calls, run = simulator
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    assert run().status_code == 409 and not calls
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "isolated-key")
    with sessions() as db:
        config = db.query(BotConfiguration).one(); draft = copy.deepcopy(config.draft_snapshot)
        draft["global"]["instructions"] = "x" * 4001; config.draft_snapshot = draft; db.commit()
    assert run().status_code == 409 and not calls
    for _ in range(20): assert run(mode="live").status_code == 200
    assert run(mode="live").status_code == 429 and len(calls) == 20


def test_postgresql_simulator_only_reads_config_and_user_records(simulator):
    import os
    from uuid import uuid4
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema
    from models import Base, User
    url = os.getenv("TEST_POSTGRESQL_URL")
    if not url: pytest.skip("Dedicated TEST_POSTGRESQL_URL not configured")
    assert make_url(url).database == "pixiepinks_test"
    _, old_sessions, _, _, _, run = simulator
    schema = "simulator_" + uuid4().hex; admin = create_engine(url)
    with admin.begin() as conn: conn.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    sessions = sessionmaker(bind=engine, autoflush=False)
    old_override = main.app.dependency_overrides[main.get_db]
    try:
        Base.metadata.create_all(engine)
        with old_sessions() as old, sessions() as db:
            for model in [User, BotConfiguration]:
                for row in old.query(model): db.add(model(**{column.name:getattr(row,column.name) for column in model.__table__.columns}))
                db.flush()
            db.commit()
        def get_db():
            with sessions() as db: yield db
        main.app.dependency_overrides[main.get_db] = get_db
        statements = []
        def record(conn, cursor, sql, *args): statements.append(sql)
        event.listen(engine, "before_cursor_execute", record)
        try: response = run()
        finally: event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200 and "Hari" in response.json()["reply"]
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert not any("whatsapp_conversation" in sql.lower() for sql in statements)
    finally:
        main.app.dependency_overrides[main.get_db] = old_override
        engine.dispose()
        with admin.begin() as conn: conn.execute(DropSchema(schema,cascade=True))
        admin.dispose()


def test_chromium_general_simulator_presets_draft_style_no_auto_calls_and_stale_results(simulator):
    playwright = pytest.importorskip("playwright.sync_api"); chromium = shutil.which("chromium")
    if not chromium: pytest.skip("Chromium unavailable")
    client, _, _, _, calls, _ = simulator
    with playwright.sync_playwright() as api:
        browser = api.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 390, "height": 780}); errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                req = route.request; url = urlsplit(req.url)
                if url.hostname != "simulator.test": route.fulfill(status=200, body=""); return
                headers = {k:v for k,v in req.headers.items() if k in {"content-type", "x-csrf-token"}}
                response = client.request(req.method, url.path, content=req.post_data_buffer, headers=headers, follow_redirects=False)
                route.fulfill(status=response.status_code, body=response.content, headers={k:v for k,v in response.headers.items() if k not in {"set-cookie", "content-length", "content-encoding"}})
            page.route("**/*", serve); page.goto("https://simulator.test/crm/bot-management/test")
            page.locator('[data-simulator-preset="greeting"]').click(); assert not calls
            page.locator("#bot-simulation-run").click(); page.locator("#bot-simulation-status").filter(has_text="AI preview generated").wait_for()
            assert "Hari" in page.locator("#bot-simulation-reply").inner_text() and len(calls) == 1
            page.locator('#bot-simulation-form [name="mode"]').select_option("live")
            assert not page.locator("#bot-simulation-results").is_visible() and len(calls) == 1
            page.locator("#bot-simulation-run").click(); page.locator("#bot-simulation-status").filter(has_text="AI preview generated").wait_for()
            assert page.locator("#bot-simulation-reply").inner_text() == "How can I help?" and not errors
        finally: browser.close()


def test_chromium_attachment_previews_render_images_prices_without_sending(simulator):
    playwright = pytest.importorskip("playwright.sync_api")
    chromium = shutil.which("chromium")
    if not chromium: pytest.skip("Chromium unavailable")
    client, _, _, _, calls, _ = simulator
    html = client.get("/crm/bot-management/test").text
    preview = {"reply": "Me options balanna.", "status": "GENERATED", "model": "mock", "tokens": 100,
        "trace": {"mode": "DRAFT", "messages_sent": 0}, "warnings": [], "notice": "Preview only",
        "products": [{"handle": f"school-bag-{i}", "title": f"Bag {i}", "price": "1500.00", "currency": "LKR",
                      "image": "https://cdn.shopify.com/bag.jpg"} for i in range(10)]}
    with playwright.sync_playwright() as api:
        browser = api.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(); errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                path = urlsplit(route.request.url).path
                if path == "/crm/bot-management/test": route.fulfill(status=200, content_type="text/html", body=html)
                elif path == "/crm/bot-management/api/simulate": route.fulfill(status=200, json=preview)
                elif path.startswith("/static/"):
                    response = client.get(path); route.fulfill(status=response.status_code, body=response.content,
                        content_type=response.headers.get("content-type", "text/plain"))
                else: route.fulfill(status=200, body="")
            page.route("**/*", serve)
            page.goto("https://simulator.test/crm/bot-management/test")
            page.locator('[data-simulator-preset="bag"]').click()
            page.locator("#bot-simulation-run").click()
            page.locator("#bot-simulation-status").filter(has_text="AI preview generated").wait_for()
            assert page.locator("#bot-simulation-products img").count() == 10
            assert page.locator("#bot-simulation-products a").count() == 10
            assert page.locator("#bot-simulation-products").inner_text().count("LKR 1500.00") == 10
            assert not errors and not calls
        finally:
            browser.close()
