"""Ordinary Shopify collection image messages, isolated DBs and mocked providers."""
import copy
import hashlib
import hmac
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import main
import shopify_collection_routes as routes
import shopify_collection_service as service
import whatsapp_service
from models import WhatsAppConversation, WhatsAppConversationMessage, WhatsAppManualSend, WhatsAppOutboundProductMessage
from settings import settings
from test_whatsapp_quick_messages import inbox

BASE = "/crm/whatsapp/api"
COLLECTION = "gid://shopify/Collection/20"


def product(number=1, **changes):
    return {"id": f"gid://shopify/Product/{number}", "variant_id": f"gid://shopify/ProductVariant/{number}01",
        "handle": f"bicycle-{number}", "title": f"Bicycle {number}", "variant_title": "Red", "sku": f"B{number}",
        "price": Decimal("45000.00"), "currency": "LKR", "published": True, "available": True,
        "inventory_tracked": True, "inventory_quantity": 2, "image": "https://cdn.shopify.com/bicycle.png",
        "url": f"https://www.pixiepinks.shop/products/bicycle-{number}", **changes}


def listing(products):
    return {"collection": {"id": COLLECTION, "title": 'Size 20" Boys Bicycles', "handle": "size-20-boys"},
        "products": [{"id": p["id"], "title": p["title"], "variants": [service.snapshot(p)]} for p in products],
        "excluded_products": 1, "max_products": 20}


@pytest.fixture
def catalog(monkeypatch):
    products = [product(1), product(2), product(3)]
    live = listing(products); calls = []; modes = {}
    monkeypatch.setattr(service, "load_collection", lambda *args: copy.deepcopy(live))
    monkeypatch.setattr(routes, "discover_collections", lambda *args: [live["collection"]])
    monkeypatch.setattr(main, "resolve_inbox_product", lambda pid, vid, **kw: copy.deepcopy(next(
        (p for p in products if p["id"] == pid and p["variant_id"] == vid), None)))
    def image(phone, image_url, caption, **kwargs):
        calls.append((phone, image_url, caption, kwargs))
        number = len(calls)
        outcome = modes.get("outcome", "SENT")
        if modes.get("error"):
            raise httpx.ReadTimeout("private upstream URL")
        return outcome, f"wamid.collection-{number}" if outcome == "SENT" else None
    monkeypatch.setattr(main, "send_whatsapp_image", image)
    # None of these Meta-catalog settings exist or are needed in this branch.
    return live, products, calls, modes


def preview(client, cid):
    response = client.get(f"{BASE}/conversations/{cid}/collection-preview", params={"collection_id": COLLECTION})
    assert response.status_code == 200, response.text
    return response.json()


def prepare(client, cid, csrf, data, key=None, items=None):
    return client.post(f"{BASE}/conversations/{cid}/collection-batches", json={
        "collection_id": COLLECTION, "preview_token": data["preview_token"], "items": items if items is not None else [
            {"product_id": p["id"], "variant_id": p["variants"][0]["variant_id"]} for p in data["products"]]},
        headers={"X-CSRF-Token": csrf or "", "Idempotency-Key": key or str(uuid4())})


def send(client, cid, batch_id, csrf, number=1):
    return client.post(f"{BASE}/conversations/{cid}/collection-batches/{batch_id}/send-item", json={
        "product_id": f"gid://shopify/Product/{number}"}, headers={"X-CSRF-Token": csrf})


def retry(client, cid, bid, csrf, key):
    return client.post(f"{BASE}/conversations/{cid}/collection-batches/{bid}/retry-failed", json={},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key})


def test_preview_prepare_and_selection_never_send_then_each_product_uses_existing_workflow(inbox, catalog, monkeypatch):
    client, sessions, ids, text_sends, _, csrf = inbox
    listing_response = client.get(f"{BASE}/collections")
    assert listing_response.status_code == 200 and listing_response.headers["cache-control"] == "private, no-store"
    data = preview(client, ids[0]); key = str(uuid4())
    batch = prepare(client, ids[0], csrf, data, key).json()
    assert batch["counts"]["QUEUED"] == 3 and not catalog[2] and not text_sends
    assert prepare(client, ids[0], csrf, data, key).json()["batch_id"] == batch["batch_id"]
    for number in [1, 2, 3]:
        response = send(client, ids[0], batch["batch_id"], csrf, number)
        assert response.status_code == 200, response.text
        assert response.json()["counts"]["SENT"] == number
        assert send(client, ids[0], batch["batch_id"], csrf, number).status_code == 200
        assert len(catalog[2]) == number
    assert response.json()["status"] == "COMPLETE"
    with sessions() as db:
        assert db.get(WhatsAppConversation, ids[0]).mode == "HUMAN"
        messages = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_product").all()
        assert len(messages) == 3 and db.query(WhatsAppOutboundProductMessage).count() == 3
        assert all(message.product_image_url and message.product_url and message.shopify_variant_reference for message in messages)
        assert all("Price: Rs. 45,000.00" in message.message_text for message in messages)
    # Preserve delivery-status updates and product-context mapping.
    monkeypatch.setattr(settings, "META_APP_SECRET", "isolated-secret")
    body = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.collection-1", "status": "read"}]}}]}]}).encode()
    signature = "sha256=" + hmac.new(b"isolated-secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"X-Hub-Signature-256": signature}).status_code == 200
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id="wamid.collection-1").one().send_status == "read"


@pytest.mark.parametrize("name", ["quick-admin", "quick-manager", "quick-staff", "quick-agent"])
def test_known_staff_can_send_in_ai_and_human_modes(inbox, catalog, name):
    client, sessions, ids, _, login, _ = inbox; csrf = login(name)
    for cid in ids:
        mode = "HUMAN" if cid == ids[0] else "AI"
        batch = prepare(client, cid, csrf, preview(client, cid)).json()
        assert send(client, cid, batch["batch_id"], csrf).json()["counts"]["SENT"] == 1
        with sessions() as db:
            assert db.get(WhatsAppConversation, cid).mode == mode


def test_staff_csrf_customer_scope_actor_scope_and_window(inbox, catalog):
    client, sessions, ids, _, login, csrf = inbox
    anonymous = TestClient(main.app, base_url="https://testserver")
    assert anonymous.get(f"{BASE}/collections", follow_redirects=False).status_code == 303
    data = preview(client, ids[0]); batch = prepare(client, ids[0], csrf, data).json(); bid = batch["batch_id"]
    assert prepare(client, ids[1], csrf, data).status_code == 409
    assert send(client, ids[0], bid, "bad").status_code == 403
    assert send(client, ids[1], bid, csrf).status_code == 404
    assert send(client, 99999, bid, csrf).status_code == 404
    with sessions() as db:
        db.get(WhatsAppConversation, ids[0]).last_customer_message_at = datetime.utcnow() - timedelta(hours=25); db.commit()
    assert send(client, ids[0], bid, csrf).status_code == 409
    csrf = login("quick-manager")
    assert send(client, ids[0], bid, csrf).status_code == 404
    assert client.get(f"{BASE}/conversations/{ids[0]}/collection-batches/{bid}").status_code == 404
    csrf = login("quick-denied")
    assert client.get(f"{BASE}/collections").status_code == 403
    assert prepare(client, ids[0], csrf, data).status_code == 403
    assert not catalog[2]


@pytest.mark.parametrize("change", ["stock", "backorder", "unpublished", "unavailable", "membership", "price", "image"])
def test_each_product_is_rechecked_and_skipped_if_no_longer_safe(inbox, catalog, change):
    client, _, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json()
    if change == "stock": catalog[1][0]["inventory_quantity"] = 0
    elif change == "backorder": catalog[1][0]["inventory_quantity"] = -2
    elif change == "unpublished": catalog[1][0]["published"] = False
    elif change == "unavailable": catalog[1][0]["available"] = False
    elif change == "price": catalog[1][0]["price"] = Decimal("46000.00")
    elif change == "image": catalog[1][0]["image"] = "https://evil.example/image.png"
    else: catalog[0]["products"].pop(0)
    result = send(client, ids[0], batch["batch_id"], csrf).json()
    assert result["counts"]["SKIPPED"] == 1 and not catalog[2]
    assert send(client, ids[0], batch["batch_id"], csrf, 2).json()["counts"]["SENT"] == 1


def test_failed_only_retry_is_explicit_idempotent_and_does_not_repeat_success(inbox, catalog):
    client, _, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json(); bid = batch["batch_id"]
    assert send(client, ids[0], bid, csrf, 1).json()["counts"]["SENT"] == 1
    catalog[3]["outcome"] = "FAILED"
    assert send(client, ids[0], bid, csrf, 2).json()["counts"]["FAILED"] == 1
    catalog[3]["outcome"] = "SENT"
    assert send(client, ids[0], bid, csrf, 3).json()["status"] == "PARTIAL"
    key = str(uuid4()); result = retry(client, ids[0], bid, csrf, key)
    assert result.status_code == 200, result.text
    assert result.json()["counts"]["QUEUED"] == 1 and result.json()["counts"]["SENT"] == 2
    assert retry(client, ids[0], bid, csrf, key).status_code == 200
    assert len(catalog[2]) == 3  # Resetting failures itself never sends.
    assert send(client, ids[0], bid, csrf, 2).json()["status"] == "COMPLETE"
    assert retry(client, ids[0], bid, csrf, key).json()["counts"]["SENT"] == 3
    assert len(catalog[2]) == 4


@pytest.mark.parametrize("error", [False, True])
def test_unknown_transport_outcome_blocks_batch_and_cannot_be_retried(inbox, catalog, error):
    client, _, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json(); bid = batch["batch_id"]
    catalog[3].update(outcome="UNKNOWN", error=error)
    response = send(client, ids[0], bid, csrf)
    assert response.json()["status"] == "BLOCKED" and response.json()["counts"]["UNKNOWN"] == 1
    assert send(client, ids[0], bid, csrf).json()["counts"]["UNKNOWN"] == 1
    assert send(client, ids[0], bid, csrf, 2).json()["counts"]["QUEUED"] == 2
    assert retry(client, ids[0], bid, csrf, str(uuid4())).status_code == 409
    assert prepare(client, ids[0], csrf, preview(client, ids[0])).status_code == 409
    assert len(catalog[2]) == 1 and "private upstream URL" not in response.text


def test_preview_tampering_expiry_changed_price_and_key_binding(inbox, catalog, monkeypatch):
    client, _, ids, _, _, csrf = inbox
    data = preview(client, ids[0])
    assert prepare(client, ids[0], csrf, {**data, "preview_token": "bad"}).status_code == 409
    from itsdangerous import TimestampSigner, URLSafeTimedSerializer
    class OldSigner(TimestampSigner):
        def get_timestamp(self): return 1
    with monkeypatch.context() as context:
        context.setattr(routes, "signer", lambda: URLSafeTimedSerializer(settings.SECRET_KEY, salt="shopify-image-collection-v1", signer=OldSigner))
        old = preview(client, ids[0])
    assert prepare(client, ids[0], csrf, old).status_code == 409
    catalog[0]["products"][0]["variants"][0]["price"] = "46000.00"
    assert prepare(client, ids[0], csrf, data).status_code == 409
    current = preview(client, ids[0]); key = str(uuid4())
    batch = prepare(client, ids[0], csrf, current, key).json()
    assert prepare(client, ids[0], csrf, current, key, []).status_code == 422
    assert prepare(client, ids[0], csrf, current, key, [{"product_id": "gid://shopify/Product/2", "variant_id": "gid://shopify/ProductVariant/201"}]).status_code == 409
    assert batch["counts"]["QUEUED"] == 3 and not catalog[2]


@pytest.mark.parametrize("items", [[], [{"product_id": "bad", "variant_id": "bad"}],
    [{"product_id": "gid://shopify/Product/1", "variant_id": "gid://shopify/ProductVariant/101"}] * 2,
    [{"product_id": f"gid://shopify/Product/{i}", "variant_id": f"gid://shopify/ProductVariant/{i}01"} for i in range(21)]])
def test_selection_limits_and_one_variant_per_product(inbox, catalog, items):
    client, _, ids, _, _, csrf = inbox
    assert prepare(client, ids[0], csrf, preview(client, ids[0]), items=items).status_code == 422
    assert not catalog[2]


def test_recent_batch_recovers_progress_without_resending(inbox, catalog):
    client, _, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json()
    send(client, ids[0], batch["batch_id"], csrf)
    recent = client.get(f"{BASE}/conversations/{ids[0]}/collection-batches/recent").json()
    assert recent["batch"]["batch_id"] == batch["batch_id"] and recent["batch"]["counts"]["SENT"] == 1
    assert len(catalog[2]) == 1


def test_concurrent_same_product_sends_once_and_other_requests_observe_busy(inbox, catalog, monkeypatch):
    client, _, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json()
    reached, release = Event(), Event()
    def blocked(*args):
        reached.set(); assert release.wait(timeout=15); return copy.deepcopy(catalog[0])
    monkeypatch.setattr(service, "load_collection", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, client, ids[0], batch["batch_id"], csrf)
        assert reached.wait(timeout=10)
        second = send(client, ids[0], batch["batch_id"], csrf)
        assert second.status_code == 200 and second.json()["counts"]["VERIFYING"] == 1
        release.set(); assert first.result().json()["counts"]["SENT"] == 1
    assert len(catalog[2]) == 1


def test_db_failure_before_claim_allows_retry_without_sending(inbox, catalog):
    client, sessions, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json(); failed = []
    def before_commit(session):
        if not failed and any(isinstance(row, WhatsAppManualSend) and row.status == "VERIFYING" for row in session.dirty):
            failed.append(True); raise OperationalError("isolated", {}, Exception("private database details"))
    event.listen(sessions.class_, "before_commit", before_commit)
    try:
        response = send(client, ids[0], batch["batch_id"], csrf)
        assert response.status_code == 503 and "private database details" not in response.text and not catalog[2]
        assert send(client, ids[0], batch["batch_id"], csrf).json()["counts"]["SENT"] == 1
    finally:
        event.remove(sessions.class_, "before_commit", before_commit)


def test_db_failure_after_external_send_cannot_repeat_it(inbox, catalog):
    client, sessions, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json()
    def before_commit(session, *args):
        if any(isinstance(row, WhatsAppConversationMessage) for row in session.new):
            raise OperationalError("isolated", {}, Exception("private data"))
    event.listen(sessions.class_, "before_flush", before_commit)
    try:
        assert send(client, ids[0], batch["batch_id"], csrf).status_code == 503
    finally:
        event.remove(sessions.class_, "before_flush", before_commit)
    assert send(client, ids[0], batch["batch_id"], csrf).json()["counts"]["PROCESSING"] == 1
    assert len(catalog[2]) == 1


def test_real_collection_reader_filters_stock_and_unpublished_products(monkeypatch):
    variant = {"id": "gid://shopify/ProductVariant/101", "title": "Red", "sku": "B1", "price": "45000.00", "availableForSale": True,
        "inventoryQuantity": 2, "inventoryItem": {"tracked": True}}
    node = {"id": "gid://shopify/Product/1", "title": "Bicycle", "handle": "bicycle", "status": "ACTIVE", "publishedAt": "2026-01-01",
        "featuredImage": {"url": "https://cdn.shopify.com/bicycle.png"}, "variants": {"nodes": [variant]}}
    connection = {"nodes": [node]}
    collection = {"id": COLLECTION, "title": "Boys bicycles", "handle": "boys", "products": connection}
    monkeypatch.setattr(service.ShopifyCatalogClient, "graphql", lambda *args: {"collection": collection})
    assert len(service.load_collection(COLLECTION)["products"]) == 1
    for quantity in [0, -1]:
        variant["inventoryQuantity"] = quantity
        assert service.load_collection(COLLECTION)["products"] == []
    variant["inventoryItem"]["tracked"] = False
    assert len(service.load_collection(COLLECTION)["products"]) == 1
    variant["availableForSale"] = False
    assert service.load_collection(COLLECTION)["products"] == []
    variant["availableForSale"] = True; node["publishedAt"] = None
    assert service.load_collection(COLLECTION)["products"] == []
    node["publishedAt"] = "2026-01-01"; node["status"] = "DRAFT"
    assert service.load_collection(COLLECTION)["products"] == []


@pytest.mark.parametrize("url", ["http://cdn.shopify.com/a.png", "https://evil.example/a.png", "https://user:password@cdn.shopify.com/a.png", "https://cdn.shopify.com:444/a.png"])
def test_collection_images_reject_untrusted_urls(url):
    assert not service.safe_image(url)


def test_collection_reader_paginates_deduplicates_and_rejects_partial_results(monkeypatch):
    node = {"id": "gid://shopify/Product/1", "title": "Bicycle", "handle": "bicycle", "status": "ACTIVE", "publishedAt": "2026-01-01",
        "featuredImage": {"url": "https://cdn.shopify.com/bicycle.png"}, "variants": {"nodes": [{
        "id": "gid://shopify/ProductVariant/101", "title": "Red", "price": "45000", "availableForSale": True,
        "inventoryQuantity": 1, "inventoryItem": {"tracked": True}}]}}
    calls = []
    def graphql(self, query, variables):
        calls.append(variables)
        return {"collection": {"id": COLLECTION, "title": "Boys", "handle": "boys", "products": {
            "nodes": [node], "pageInfo": {"hasNextPage": len(calls) == 1, "endCursor": "page2"}}}}
    monkeypatch.setattr(service.ShopifyCatalogClient, "graphql", graphql)
    assert len(service.load_collection(COLLECTION)["products"]) == 1
    assert calls[1]["after"] == "page2" and "products(first: 5" in service.COLLECTION_QUERY
    node["variants"]["pageInfo"] = {"hasNextPage": True}
    with pytest.raises(service.CollectionError, match="more than 50 variants"):
        service.load_collection(COLLECTION)
    node["variants"]["pageInfo"] = {}
    clock = iter([0, 31])
    monkeypatch.setattr(service, "monotonic", lambda: next(clock))
    with pytest.raises(service.CollectionError, match="timed out"):
        service.load_collection(COLLECTION)


def test_window_is_rechecked_after_slow_verification_and_provider_failure_is_retryable(inbox, catalog, monkeypatch):
    client, sessions, ids, _, _, csrf = inbox
    batch = prepare(client, ids[0], csrf, preview(client, ids[0])).json(); bid = batch["batch_id"]
    def unavailable(*args):
        from shopify_catalog_service import ShopifyCatalogError
        raise ShopifyCatalogError("private provider error")
    monkeypatch.setattr(service, "load_collection", unavailable)
    result = send(client, ids[0], bid, csrf)
    assert result.json()["counts"]["FAILED"] == 1 and "private provider error" not in result.text and not catalog[2]
    def expired(*args):
        with sessions() as db:
            db.get(WhatsAppConversation, ids[0]).last_customer_message_at = datetime.utcnow() - timedelta(hours=25)
            db.commit()
        return copy.deepcopy(catalog[0])
    monkeypatch.setattr(service, "load_collection", expired)
    result = send(client, ids[0], bid, csrf, 2)
    assert result.json()["counts"]["SKIPPED"] == 1 and not catalog[2]


@pytest.mark.parametrize("status,expected", [(400, "FAILED"), (403, "FAILED"), (429, "UNKNOWN"), (500, "UNKNOWN")])
def test_shared_image_transport_reports_safe_retry_classification(monkeypatch, status, expected):
    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "private-token")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "12345")
    monkeypatch.setattr(whatsapp_service.httpx, "post", lambda *args, **kwargs: httpx.Response(status, request=httpx.Request("POST", "https://graph.facebook.com/messages")))
    assert whatsapp_service.send_whatsapp_image("94770000001", "https://cdn.shopify.com/a.png", "Product", return_outcome=True) == (expected, None)
    assert whatsapp_service.send_whatsapp_image("94770000001", "https://cdn.shopify.com/a.png", "Product") is False


def test_chromium_mobile_preview_stop_resume_and_edit_selection_without_auto_send(inbox, catalog):
    playwright = pytest.importorskip("playwright.sync_api"); chromium = shutil.which("chromium")
    if not chromium: pytest.skip("Chromium unavailable")
    client, _, ids, _, _, _ = inbox
    with playwright.sync_playwright() as api:
        browser = api.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 390, "height": 760}); errors = []; lose_retry_ack = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                request = route.request; url = urlsplit(request.url)
                if url.hostname != "inbox.test": route.fulfill(status=200, body=""); return
                if url.path == f"{BASE}/products": route.fulfill(status=200, json={"products": [], "has_next_page": False}); return
                headers = {key: value for key, value in request.headers.items() if key in {"content-type", "idempotency-key", "x-csrf-token"}}
                result = client.request(request.method, url.path + ("?" + url.query if url.query else ""), headers=headers, content=request.post_data_buffer, follow_redirects=False)
                if url.path.endswith("/retry-failed") and lose_retry_ack:
                    lose_retry_ack.pop(); route.abort("failed"); return
                route.fulfill(status=result.status_code, body=result.content, headers={key: value for key, value in result.headers.items() if key not in {"set-cookie", "content-length", "content-encoding"}})
            page.route("**/*", serve); page.goto("https://inbox.test/crm/whatsapp")
            page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]').click()
            page.locator("#catalog-button").click(); page.locator("#collection-open").click()
            page.locator("#collection-select option").wait_for(state="attached")
            page.locator("#collection-preview").click(); page.locator(".collection-card").first.wait_for()
            assert len(catalog[2]) == 0 and page.locator(".collection-card").count() == 3
            page.locator(".collection-card input").last.uncheck()
            assert page.locator("#collection-send").inner_text() == "Send selected products (2)" and not catalog[2]
            page.locator("#collection-send").click()
            page.wait_for_function('document.querySelectorAll(".collection-result").length===2')
            page.locator("#collection-stop").click()
            page.locator("#collection-status").filter(has_text="Paused.").wait_for()
            assert len(catalog[2]) == 1
            page.locator("#collection-send").click()
            page.locator("#collection-status").filter(has_text="2/2 sent").wait_for()
            assert len(catalog[2]) == 2
            page.locator("#collection-close").click(); page.locator("#catalog-button").click(); page.locator("#collection-open").click()
            page.locator("#collection-status").filter(has_text="2/2 sent").wait_for()
            assert page.locator("#collection-send").is_disabled() and len(catalog[2]) == 2 and not errors
            # A lost retry-reset response must retain its action key, including
            # after a progress check shows the reset already committed.
            page.locator("#collection-new").click()
            page.locator("#collection-select option").wait_for(state="attached")
            page.locator("#collection-preview").click(); page.locator(".collection-card").first.wait_for()
            page.locator(".collection-card input").last.uncheck(); catalog[3]["outcome"] = "FAILED"
            page.locator("#collection-send").click()
            page.locator("#collection-status").filter(has_text="2 failed").wait_for()
            lose_retry_ack.append(True); page.locator("#collection-retry").click()
            page.locator("#collection-retry").filter(has_text="Check failed reset").wait_for()
            page.locator("#collection-check").click()
            page.locator("#collection-status").filter(has_text="2 remaining").wait_for()
            assert page.locator("#collection-send").is_disabled() and page.locator("#collection-retry").is_visible()
            page.locator("#collection-retry").click()
            page.wait_for_function('!document.getElementById("collection-send").disabled')
            catalog[3]["outcome"] = "SENT"; page.locator("#collection-send").click()
            page.locator("#collection-status").filter(has_text="2/2 sent").wait_for()
            assert len(catalog[2]) == 6 and not errors
        finally:
            browser.close()


def test_postgresql_concurrent_creation_send_and_failed_retry_with_production_session_settings(inbox, catalog, monkeypatch):
    import os
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema
    from models import Base, User
    url = os.getenv("TEST_POSTGRESQL_URL")
    if not url: pytest.skip("Dedicated TEST_POSTGRESQL_URL not configured")
    assert make_url(url).database == "pixiepinks_test"
    schema = "shopify_collection_" + uuid4().hex; admin = create_engine(url)
    with admin.begin() as conn: conn.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
    client, old_sessions, ids, _, _, csrf = inbox
    previous = main.app.dependency_overrides[main.get_db]
    try:
        Base.metadata.create_all(engine)
        with old_sessions() as old, sessions() as db:
            user = old.query(User).filter_by(username="quick-admin").one()
            db.add(User(id=user.id, username=user.username, role=user.role, password_hash=user.password_hash))
            db.add(WhatsAppConversation(id=ids[0], phone_number="94770012301", mode="HUMAN", last_customer_message_at=datetime.utcnow()))
            db.commit()
        def get_db():
            with sessions() as db: yield db
        main.app.dependency_overrides[main.get_db] = get_db
        data = preview(client, ids[0]); barrier = Barrier(2); key = str(uuid4())
        def synchronized_load(*args):
            barrier.wait(timeout=15); return copy.deepcopy(catalog[0])
        monkeypatch.setattr(service, "load_collection", synchronized_load)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: prepare(client, ids[0], csrf, data, key), range(2)))
        assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
        bid = responses[0].json()["batch_id"]
        assert responses[1].json()["batch_id"] == bid
        reached, release = Event(), Event()
        def delayed_load(*args):
            reached.set(); assert release.wait(timeout=15); return copy.deepcopy(catalog[0])
        monkeypatch.setattr(service, "load_collection", delayed_load)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(send, client, ids[0], bid, csrf)
            assert reached.wait(timeout=10)
            assert send(client, ids[0], bid, csrf).json()["counts"]["VERIFYING"] == 1
            release.set(); assert first.result().json()["counts"]["SENT"] == 1
        monkeypatch.setattr(service, "load_collection", lambda *args: copy.deepcopy(catalog[0]))
        assert send(client, ids[0], bid, csrf, 2).json()["counts"]["SENT"] == 2
        catalog[3]["outcome"] = "FAILED"
        assert send(client, ids[0], bid, csrf, 3).json()["status"] == "PARTIAL"
        retry_key = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: retry(client, ids[0], bid, csrf, retry_key), range(2)))
        assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
        catalog[3]["outcome"] = "SENT"
        assert send(client, ids[0], bid, csrf, 3).json()["status"] == "COMPLETE"
        assert len(catalog[2]) == 4
        with sessions() as db:
            assert db.query(WhatsAppConversationMessage).count() == 3
    finally:
        main.app.dependency_overrides[main.get_db] = previous
        engine.dispose()
        with admin.begin() as conn: conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()
