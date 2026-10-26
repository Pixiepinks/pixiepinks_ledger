"""Native collection messages: mocked providers and isolated staff databases."""
import copy
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import main
import whatsapp_collection_routes as routes
import whatsapp_collection_service as service
from models import WhatsAppConversation, WhatsAppConversationMessage, WhatsAppManualSend
from settings import settings
from test_whatsapp_quick_messages import inbox

CID = "gid://shopify/Collection/20"
VARIANT = "gid://shopify/ProductVariant/201"
BASE = "/crm/whatsapp/api"


def snapshot(count=2):
    return {"collection": {"id": CID, "title": 'Size 20" Boys Bicycles', "handle": "size-20-boys"},
        "items": [{"product_id": f"gid://shopify/Product/{i}", "variant_id": f"gid://shopify/ProductVariant/{201+i}",
                   "title": f"Bicycle {i}", "variant_title": "Red", "price": "45000.00",
                   "image": None, "url": f"https://www.pixiepinks.shop/products/bicycle-{i}",
                   "retailer_id": f"verified-retailer-{i}"} for i in range(count)],
        "excluded": {"unavailable": 1, "unmapped": 1, "meta_unavailable": 0}}


@pytest.fixture
def catalog(monkeypatch):
    for key, value in {"META_WHATSAPP_BUSINESS_ACCOUNT_ID": "100", "META_WHATSAPP_PHONE_NUMBER_ID": "200",
            "META_WHATSAPP_CATALOG_ID": "300", "META_WHATSAPP_ACCESS_TOKEN": "PRIVATE-TOKEN",
            "META_WHATSAPP_PRODUCT_MAPPING_JSON": json.dumps({VARIANT: "verified-retailer-0"})}.items():
        monkeypatch.setattr(settings, key, value)
    current = snapshot()
    calls = []
    monkeypatch.setattr(service, "preview", lambda _: copy.deepcopy(current))
    monkeypatch.setattr(routes, "discover_collections", lambda q, limit: [current["collection"]])
    monkeypatch.setattr(service, "send_product_list", lambda phone, title, items: calls.append((phone, title, items)) or ("SENT", "wamid.collection"))
    return current, calls


def preview(client, cid):
    response = client.get(f"{BASE}/conversations/{cid}/collection-preview", params={"collection_id": CID})
    assert response.status_code == 200, response.text
    return response.json()


def send(client, cid, csrf, data, key=None, variants=None):
    return client.post(f"{BASE}/conversations/{cid}/send-collection", json={"collection_id": CID,
        "variant_ids": variants if variants is not None else [item["variant_id"] for item in data["items"]],
        "preview_token": data["preview_token"]}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())})


def test_preview_never_sends_and_explicit_send_is_scoped_and_idempotent(inbox, catalog, monkeypatch):
    client, sessions, ids, _, login, csrf = inbox
    current, calls = catalog
    listing = client.get(f"{BASE}/collections")
    assert listing.status_code == 200 and listing.json()["configured"]
    data = preview(client, ids[0])
    assert data["max_items"] == 30 and "retailer_id" not in json.dumps(data) and not calls
    with sessions() as db:
        assert db.query(WhatsAppManualSend).count() == 0
        old_mode = db.get(WhatsAppConversation, ids[0]).mode
    key = str(uuid4())
    assert send(client, ids[0], csrf, data, key).status_code == 200
    assert send(client, ids[0], csrf, data, key).status_code == 200
    assert send(client, ids[1], csrf, data, key).status_code == 409
    assert len(calls) == 1 and len(calls[0][2]) == 2
    # Native messages use the existing signed Meta delivery-status webhook.
    import hashlib
    import hmac
    monkeypatch.setattr(settings, "META_APP_SECRET", "isolated-status-secret")
    webhook = json.dumps({"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.collection", "status": "read"}]}}]}]}).encode()
    signature = "sha256=" + hmac.new(b"isolated-status-secret", webhook, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=webhook, headers={"X-Hub-Signature-256": signature}).status_code == 200
    with sessions() as db:
        assert db.get(WhatsAppConversation, ids[0]).mode == old_mode
        assert db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id="wamid.collection").count() == 1
        message = db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id="wamid.collection").one()
        assert message.response_kind == "staff_product" and message.sent_by == "quick-admin"
        assert message.send_status == "read"
        assert "Bicycle 0" in message.message_text and "Bicycle 1" in message.message_text
    csrf = login("quick-manager")
    assert send(client, ids[0], csrf, data, key).status_code == 409


@pytest.mark.parametrize("name", ["quick-admin", "quick-manager", "quick-staff", "quick-agent"])
def test_known_staff_can_preview_and_send_in_ai_and_human_modes(inbox, catalog, name):
    client, sessions, ids, _, login, _ = inbox
    csrf = login(name)
    for cid in ids:
        data = preview(client, cid)
        assert send(client, cid, csrf, data).status_code == 200
    assert len(catalog[1]) == 2


def test_auth_csrf_window_invalid_scope_and_no_secrets(inbox, catalog):
    client, sessions, ids, _, login, csrf = inbox
    anonymous = TestClient(main.app, base_url="https://testserver")
    assert anonymous.get(f"{BASE}/collections", follow_redirects=False).status_code == 303
    data = preview(client, ids[0])
    assert send(client, ids[0], "bad", data).status_code == 403
    assert send(client, ids[1], csrf, data).status_code == 409
    assert send(client, 99999, csrf, data).status_code == 404
    assert send(client, ids[0], csrf, {**data, "preview_token": "tampered"}).status_code == 409
    with sessions() as db:
        db.get(WhatsAppConversation, ids[0]).last_customer_message_at = datetime.utcnow() - timedelta(hours=25)
        db.commit()
    assert send(client, ids[0], csrf, data).status_code == 409
    assert client.get(f"{BASE}/conversations/{ids[0]}/collection-preview", params={"collection_id": CID}).status_code == 409
    login("quick-denied")
    assert client.get(f"{BASE}/collections").status_code == 403
    assert send(client, ids[1], csrf, data).status_code == 403
    assert not catalog[1]


@pytest.mark.parametrize("variants", [[], [VARIANT] * 2, [f"gid://shopify/ProductVariant/{n}" for n in range(31)], ["invalid"], [1], ["gid://shopify/ProductVariant/99999"]])
def test_invalid_or_unverified_selections_cannot_send(inbox, catalog, variants):
    client, _, ids, _, _, csrf = inbox
    assert send(client, ids[0], csrf, preview(client, ids[0]), variants=variants).status_code == 422
    assert not catalog[1]


def test_changed_stock_price_mapping_and_preview_age_require_review(inbox, catalog, monkeypatch):
    client, _, ids, _, _, csrf = inbox
    data = preview(client, ids[0]); catalog[0]["items"][0]["price"] = "50000.00"
    assert send(client, ids[0], csrf, data).status_code == 409
    data = preview(client, ids[0]); catalog[0]["items"][0]["retailer_id"] = "different-retailer"
    assert send(client, ids[0], csrf, data).status_code == 409
    data = preview(client, ids[0]); catalog[0]["items"].pop()
    assert send(client, ids[0], csrf, data).status_code == 409
    data = preview(client, ids[0])
    from itsdangerous import TimestampSigner, URLSafeTimedSerializer
    class OldSigner(TimestampSigner):
        def get_timestamp(self): return 1
    with monkeypatch.context() as context:
        context.setattr(routes, "signer", lambda: URLSafeTimedSerializer(settings.SECRET_KEY,
            salt="whatsapp-collection-preview-v1", signer=OldSigner))
        stale = preview(client, ids[0])
    assert send(client, ids[0], csrf, stale).status_code == 409
    assert not catalog[1]


@pytest.mark.parametrize("state", ["UNKNOWN", "FAILED"])
def test_uncertain_or_rejected_sends_never_automatically_repeat(inbox, catalog, monkeypatch, state):
    client, sessions, ids, _, _, csrf = inbox
    calls = []
    monkeypatch.setattr(service, "send_product_list", lambda *args: calls.append(args) or (state, None))
    data = preview(client, ids[0]); key = str(uuid4())
    assert send(client, ids[0], csrf, data, key).status_code == 502
    assert send(client, ids[0], csrf, data, key).status_code == 409
    assert len(calls) == 1
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(direction="outbound").count() == 0


def test_duplicate_concurrent_send_reserves_once(inbox, catalog):
    client, sessions, ids, _, _, csrf = inbox
    data = preview(client, ids[0]); key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(client, ids[0], csrf, data, key), range(2)))
    assert all(response.status_code in {200, 409} for response in responses)
    assert len(catalog[1]) == 1
    with sessions() as db:
        assert db.query(WhatsAppManualSend).filter_by(idempotency_key=key).count() == 1


def test_database_failure_before_send_rolls_back_and_same_key_can_retry(inbox, catalog):
    client, sessions, ids, _, _, csrf = inbox
    data = preview(client, ids[0]); key = str(uuid4()); failed = []
    def before_commit(session):
        if not failed and any(isinstance(row, WhatsAppManualSend) for row in session.new):
            failed.append(True)
            raise OperationalError("test commit", {}, Exception("private database detail"))
    event.listen(sessions.class_, "before_commit", before_commit)
    try:
        response = send(client, ids[0], csrf, data, key)
        assert response.status_code == 503 and "private database detail" not in response.text
        assert not catalog[1]
        assert send(client, ids[0], csrf, data, key).status_code == 200
        assert len(catalog[1]) == 1
    finally:
        event.remove(sessions.class_, "before_commit", before_commit)


def test_database_failure_after_external_send_does_not_resend(inbox, catalog):
    client, sessions, ids, _, _, csrf = inbox
    data = preview(client, ids[0]); key = str(uuid4())
    def before_commit(session):
        if any(isinstance(row, WhatsAppConversationMessage) for row in session.new):
            raise OperationalError("test commit", {}, Exception("do not expose"))
    event.listen(sessions.class_, "before_commit", before_commit)
    try:
        assert send(client, ids[0], csrf, data, key).status_code == 503
    finally:
        event.remove(sessions.class_, "before_commit", before_commit)
    assert send(client, ids[0], csrf, data, key).status_code == 409
    assert len(catalog[1]) == 1


def test_missing_configuration_is_actionable_and_private(inbox, monkeypatch):
    client, _, _, _, _, _ = inbox
    monkeypatch.setattr(settings, "META_WHATSAPP_CATALOG_ID", None)
    response = client.get(f"{BASE}/collections")
    assert response.status_code == 200 and not response.json()["configured"]
    assert response.headers["cache-control"] == "private, no-store"
    assert "catalog IDs" in response.json()["detail"]


def setup_config(monkeypatch):
    for key, value in {"META_WHATSAPP_BUSINESS_ACCOUNT_ID": "100", "META_WHATSAPP_PHONE_NUMBER_ID": "200",
            "META_WHATSAPP_CATALOG_ID": "300", "META_WHATSAPP_ACCESS_TOKEN": "PRIVATE-TOKEN",
            "META_WHATSAPP_PRODUCT_MAPPING_JSON": json.dumps({VARIANT: "retailer-red"})}.items():
        monkeypatch.setattr(settings, key, value)


@pytest.mark.parametrize("mapping", ["broken", "[]", "{}", '{"1":"guessed"}', json.dumps({VARIANT: "\nsecret"}), json.dumps({VARIANT: "same", "gid://shopify/ProductVariant/202": "same"})])
def test_mapping_validation_never_guesses_or_discloses_values(monkeypatch, mapping):
    setup_config(monkeypatch)
    monkeypatch.setattr(settings, "META_WHATSAPP_PRODUCT_MAPPING_JSON", mapping)
    with pytest.raises(service.CollectionError, match="Configure unique verified"):
        service.configuration()


def test_real_service_filters_stock_publication_mapping_and_meta(monkeypatch):
    setup_config(monkeypatch)
    variant = {"id": VARIANT, "title": "Red", "price": "45000.00", "availableForSale": True,
               "inventoryQuantity": 2, "inventoryItem": {"tracked": True}}
    product = {"id": "gid://shopify/Product/20", "title": "Bicycle", "handle": "bicycle", "status": "ACTIVE",
               "publishedAt": "2026-01-01", "variants": {"nodes": [variant]}}
    collection = {"id": CID, "title": "Boys bicycles", "handle": "boys", "products": {"nodes": [product]}}
    monkeypatch.setattr(service.ShopifyCatalogClient, "graphql", lambda *args: {"collection": collection})
    monkeypatch.setattr(service.MetaCatalog, "available_retailers", lambda self, wanted: {"retailer-red"})
    result = service.preview(CID)
    assert len(result["items"]) == 1 and result["items"][0]["retailer_id"] == "retailer-red"
    variant["inventoryQuantity"] = 0
    assert service.preview(CID)["items"] == []
    variant["inventoryQuantity"] = 2; variant["availableForSale"] = False
    assert service.preview(CID)["items"] == []
    variant["availableForSale"] = True; product["publishedAt"] = None
    assert service.preview(CID)["items"] == []
    product["publishedAt"] = "2026-01-01"; variant["id"] = "gid://shopify/ProductVariant/999"
    assert service.preview(CID)["excluded"]["unmapped"] == 1
    variant["id"] = VARIANT
    monkeypatch.setattr(service.MetaCatalog, "available_retailers", lambda *args: set())
    assert service.preview(CID)["excluded"]["meta_unavailable"] == 1


def test_meta_linkage_catalog_membership_and_fixed_pagination_destinations(monkeypatch):
    setup_config(monkeypatch); requests = []
    def get(url, **kwargs):
        requests.append((url, kwargs))
        if url.endswith("phone_numbers"): data = {"data": [{"id": "200"}]}
        elif url.endswith("product_catalogs"): data = {"data": [{"id": "300"}]}
        elif "after" not in kwargs["params"]:
            data = {"data": [{"retailer_id": "other", "availability": "in stock"}],
                "paging": {"next": "https://evil.example/?access_token=PRIVATE", "cursors": {"after": "page2"}}}
        else: data = {"data": [{"retailer_id": "retailer-red", "availability": "in stock"}]}
        return httpx.Response(200, json=data)
    monkeypatch.setattr(service.httpx, "get", get)
    assert service.MetaCatalog().available_retailers({"retailer-red"}) == {"retailer-red"}
    assert all(url.startswith("https://graph.facebook.com/") and "PRIVATE" not in url for url, _ in requests)
    assert all(not kwargs["follow_redirects"] for _, kwargs in requests)
    monkeypatch.setattr(service.MetaCatalog, "rows", lambda *args: iter([{"id": "different"}]))
    with pytest.raises(service.CollectionError, match="phone number"):
        service.MetaCatalog().available_retailers({"retailer-red"})


@pytest.mark.parametrize("status", [301, 400, 403, 500])
def test_meta_errors_do_not_expose_tokens_or_responses(monkeypatch, caplog, status):
    setup_config(monkeypatch)
    monkeypatch.setattr(service.httpx, "get", lambda *args, **kwargs: httpx.Response(status, json={"error": "PRIVATE-TOKEN"}))
    with pytest.raises(service.CollectionError) as error:
        service.MetaCatalog().available_retailers({"retailer-red"})
    assert "PRIVATE-TOKEN" not in str(error.value) + caplog.text


def test_native_product_list_payload_uses_verified_retailer_ids(monkeypatch):
    setup_config(monkeypatch); calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"messages": [{"id": "wamid.native"}]})
    monkeypatch.setattr(service.httpx, "post", post)
    assert service.send_product_list("94770000001", "Size 20 Boys", snapshot()["items"]) == ("SENT", "wamid.native")
    payload = calls[0][1]["json"]
    assert payload["interactive"]["type"] == "product_list" and payload["type"] == "interactive"
    action = payload["interactive"]["action"]
    assert action["catalog_id"] == "300"
    assert action["sections"][0]["product_items"] == [{"product_retailer_id": "verified-retailer-0"}, {"product_retailer_id": "verified-retailer-1"}]
    assert "shopify" not in json.dumps(payload) and "PRIVATE-TOKEN" not in json.dumps(payload)
    assert calls[0][1]["headers"]["Authorization"] == "Bearer PRIVATE-TOKEN"


@pytest.mark.parametrize("response,expected", [(httpx.Response(400), "FAILED"), (httpx.Response(500), "UNKNOWN"), (httpx.Response(200, json={}), "UNKNOWN"), (httpx.Response(429), "UNKNOWN")])
def test_native_transport_does_not_retry_ambiguous_results(monkeypatch, response, expected):
    setup_config(monkeypatch); calls = []
    monkeypatch.setattr(service.httpx, "post", lambda *args, **kwargs: calls.append(True) or response)
    assert service.send_product_list("94770000001", "Title", snapshot()["items"])[0] == expected
    assert len(calls) == 1


def test_chromium_preview_selection_never_sends_until_explicit_click(inbox, catalog):
    playwright = pytest.importorskip("playwright.sync_api")
    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("Chromium unavailable")
    client, _, ids, _, _, _ = inbox
    with playwright.sync_playwright() as api:
        browser = api.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 390, "height": 760})
            errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                request = route.request; url = urlsplit(request.url)
                if url.hostname != "inbox.test":
                    route.fulfill(status=200, body=""); return
                headers = {key: value for key, value in request.headers.items() if key in {"content-type", "idempotency-key", "x-csrf-token"}}
                result = client.request(request.method, url.path + ("?" + url.query if url.query else ""), headers=headers, content=request.post_data_buffer, follow_redirects=False)
                route.fulfill(status=result.status_code, body=result.content, headers={key: value for key, value in result.headers.items() if key not in {"set-cookie", "content-length", "content-encoding"}})
            page.route("**/*", serve); page.goto("https://inbox.test/crm/whatsapp")
            page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]').click()
            page.locator("#catalog-button").click(); page.locator("#collection-open").click()
            page.locator("#collection-select option").wait_for(state="attached")
            assert not catalog[1] and page.locator("#collection-send").is_disabled()
            page.locator("#collection-preview").click(); page.locator(".collection-item").first.wait_for()
            assert page.locator(".collection-item").count() == 2 and not catalog[1]
            assert page.locator("#collection-send").inner_text() == "Send collection (2)"
            page.locator(".collection-item input").last.uncheck()
            assert not catalog[1] and page.locator("#collection-send").inner_text() == "Send collection (1)"
            page.locator("#collection-send").click()
            page.locator("#collection-status").filter(has_text="Collection sent to").wait_for()
            assert len(catalog[1]) == 1 and len(catalog[1][0][2]) == 1
            assert page.locator("#collection-send").is_disabled() and not errors
        finally:
            browser.close()


def test_postgresql_concurrent_collection_send_uses_one_durable_receipt(inbox, catalog, monkeypatch):
    import os
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema
    from models import Base, User
    url = os.getenv("TEST_POSTGRESQL_URL")
    if not url:
        pytest.skip("Dedicated TEST_POSTGRESQL_URL not configured")
    assert make_url(url).database == "pixiepinks_test"
    schema = "collections_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    client, original_sessions, ids, _, _, csrf = inbox
    previous = main.app.dependency_overrides[main.get_db]
    try:
        Base.metadata.create_all(engine)
        with original_sessions() as old, sessions() as db:
            user = old.query(User).filter_by(username="quick-admin").one()
            db.add(User(id=user.id, username=user.username, role=user.role, password_hash=user.password_hash))
            for cid in ids:
                row = old.get(WhatsAppConversation, cid)
                db.add(WhatsAppConversation(id=cid, phone_number=row.phone_number, mode=row.mode,
                    last_customer_message_at=datetime.utcnow()))
            db.commit()
        def get_db():
            with sessions() as db:
                yield db
        main.app.dependency_overrides[main.get_db] = get_db
        data = preview(client, ids[0]); key = str(uuid4()); barrier = Barrier(2)
        def synchronized_preview(_):
            barrier.wait(timeout=15)
            return copy.deepcopy(catalog[0])
        monkeypatch.setattr(service, "preview", synchronized_preview)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: send(client, ids[0], csrf, data, key), range(2)))
        assert all(response.status_code in {200, 409} for response in responses)
        assert any(response.status_code == 200 for response in responses)
        assert len(catalog[1]) == 1
        with sessions() as db:
            assert db.query(WhatsAppManualSend).filter_by(idempotency_key=key).count() == 1
            assert db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id="wamid.collection").count() == 1
    finally:
        main.app.dependency_overrides[main.get_db] = previous
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()
