import base64
import hashlib
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import shopify_order_lifecycle_service as lifecycle
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import (SalesOrder, SalesOrderLine, SalesOrderStatusHistory,
                    ShopifyWebhookEvent, User)
from shopify_catalog_service import ShopifyCatalogError
from shopify_order_lifecycle_service import process_orders_create, reconcile_order
from utils_auth import hash_password


SECRET = "test-only-webhook-secret"
SHOP = "pixiepinks-test"


def setup_function():
    Base.metadata.create_all(engine); upgrade_database()
    with SessionLocal() as db:
        db.query(ShopifyWebhookEvent).delete()
        db.query(SalesOrderStatusHistory).delete()
        db.query(SalesOrderLine).delete()
        db.query(SalesOrder).delete()
        user = db.query(User).filter_by(username="lifecycle-admin").one_or_none()
        if user is None:
            db.add(User(username="lifecycle-admin", password_hash=hash_password("password"), role="ADMIN"))
        db.commit()


def _order(db, number="SO-20261023-ABCDEF12", **values):
    row = SalesOrder(internal_order_number=number, idempotency_key=f"test-{number}",
        customer_name="Test Customer", whatsapp_phone="94770000000", created_by="lifecycle-admin",
        shopify_draft_order_id=f"gid://shopify/DraftOrder/{int(number[-8:], 16)}",
        shopify_draft_order_name="#DTEST", shopify_sync_status="SYNCED", currency="LKR",
        subtotal=100, discount_total=0, shipping_total=0, tax_total=0, grand_total=100,
        amount_paid=0, **values)
    db.add(row); db.commit(); db.refresh(row)
    return row


def _payload(number="SO-20261023-ABCDEF12", order_id=9001):
    return {"id": order_id, "admin_graphql_api_id": f"gid://shopify/Order/{order_id}",
            "name": "#1001", "created_at": "2026-10-23T10:00:00Z",
            "tags": f"whatsapp, ledger-{number}",
            "note": f"PixiePinks Ledger order {number}",
            "note_attributes": [{"name": "PixiePinks Ledger Order", "value": number}]}


def _headers(body, webhook_id="delivery-1", signature=None):
    signature = signature or base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return {"X-Shopify-Hmac-Sha256": signature, "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": webhook_id, "X-Shopify-Shop-Domain": f"{SHOP}.myshopify.com",
            "Content-Type": "application/json"}


def _client(monkeypatch, remote_order_id=9001):
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(main.settings, "SHOPIFY_SHOP", SHOP)
    class VerifiedRelationship:
        def graphql(self, query, variables):
            return {"draftOrder": {"id": variables["id"], "name": "#DTEST",
                "order": {"id": f"gid://shopify/Order/{remote_order_id}", "name": "#1001",
                          "createdAt": "2026-10-23T10:00:00Z"}}}
    monkeypatch.setattr(lifecycle, "ShopifyCatalogClient", VerifiedRelationship)
    return TestClient(main.app, base_url="https://testserver")


def test_valid_webhook_links_order_and_preserves_financial_states(monkeypatch):
    with SessionLocal() as db: order_id = _order(db).id
    body = json.dumps(_payload(), separators=(",", ":")).encode()
    response = _client(monkeypatch).post("/webhooks/shopify/orders", content=body, headers=_headers(body))
    assert response.status_code == 200 and response.json()["status"] == "PROCESSED"
    with SessionLocal() as db:
        saved = db.get(SalesOrder, order_id)
        assert saved.shopify_order_id == "gid://shopify/Order/9001"
        assert saved.shopify_order_name == "#1001" and saved.order_status == "CONFIRMED"
        assert saved.payment_status == "UNPAID" and saved.fulfillment_status == "UNFULFILLED"
        assert saved.amount_paid == 0
        assert [x.new_status for x in db.query(SalesOrderStatusHistory).filter_by(
            sales_order_id=order_id).order_by(SalesOrderStatusHistory.id)] == [
                "AWAITING_CUSTOMER_CONFIRMATION", "CONFIRMED"]


def test_invalid_signature_and_disabled_flag_are_rejected(monkeypatch):
    body = json.dumps(_payload()).encode()
    client = _client(monkeypatch)
    assert client.post("/webhooks/shopify/orders", content=body,
                       headers=_headers(body, signature="invalid")).status_code == 401
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED", False)
    assert client.post("/webhooks/shopify/orders", content=body, headers=_headers(body)).status_code == 404


def test_duplicate_and_out_of_order_deliveries_are_idempotent(monkeypatch):
    with SessionLocal() as db: order_id = _order(db).id
    body = json.dumps(_payload()).encode(); client = _client(monkeypatch)
    assert client.post("/webhooks/shopify/orders", content=body, headers=_headers(body)).status_code == 200
    assert client.post("/webhooks/shopify/orders", content=body, headers=_headers(body)).json()["status"] == "PROCESSED"
    payload = _payload(); payload["tags"] = ""; payload["note"] = None; payload["note_attributes"] = []
    later = json.dumps(payload).encode()
    assert client.post("/webhooks/shopify/orders", content=later,
                       headers=_headers(later, "delivery-2")).json()["status"] == "PROCESSED"
    with SessionLocal() as db:
        assert db.query(ShopifyWebhookEvent).count() == 2
        assert db.query(SalesOrderStatusHistory).filter_by(sales_order_id=order_id).count() == 2


def test_unrelated_missing_and_ambiguous_references_are_not_linked(monkeypatch):
    with SessionLocal() as db:
        first = _order(db)
        second = _order(db, "SO-20261023-12345678")
        first_id, second_id = first.id, second.id
    client = _client(monkeypatch)
    missing = _payload("SO-20261023-99999999")
    body = json.dumps(missing).encode()
    assert client.post("/webhooks/shopify/orders", content=body,
                       headers=_headers(body, "missing")).json()["status"] == "IGNORED"
    ambiguous = _payload()
    ambiguous["tags"] += ", ledger-SO-20261023-12345678"
    ambiguous["note"] = None; ambiguous["note_attributes"] = []
    body = json.dumps(ambiguous).encode()
    assert client.post("/webhooks/shopify/orders", content=body,
                       headers=_headers(body, "ambiguous")).json()["status"] == "RECONCILIATION_REQUIRED"
    with SessionLocal() as db:
        assert db.get(SalesOrder, first_id).shopify_order_id is None
        assert db.get(SalesOrder, second_id).shopify_order_id is None


def test_conflicting_order_gid_requires_reconciliation(monkeypatch):
    with SessionLocal() as db:
        row = _order(db, shopify_order_id="gid://shopify/Order/111")
        row_id = row.id
    body = json.dumps(_payload(order_id=222)).encode()
    assert _client(monkeypatch, 222).post("/webhooks/shopify/orders", content=body,
        headers=_headers(body, "conflict")).json()["status"] == "RECONCILIATION_REQUIRED"
    with SessionLocal() as db:
        saved = db.get(SalesOrder, row_id)
        assert saved.shopify_order_id == "gid://shopify/Order/111"
        assert saved.shopify_lifecycle_status == "RECONCILIATION_REQUIRED"


def test_unverified_tag_cannot_link_a_shopify_order(monkeypatch):
    with SessionLocal() as db: order_id = _order(db).id
    body = json.dumps(_payload()).encode()
    response = _client(monkeypatch, 9999).post("/webhooks/shopify/orders", content=body,
                                               headers=_headers(body, "relationship-mismatch"))
    assert response.json()["status"] == "RECONCILIATION_REQUIRED"
    with SessionLocal() as db:
        assert db.get(SalesOrder, order_id).shopify_order_id is None


def test_transient_verification_failure_is_retryable_and_recoverable(monkeypatch):
    with SessionLocal() as db: order_id = _order(db).id
    body = json.dumps(_payload()).encode()
    client = _client(monkeypatch)
    class Unavailable:
        def graphql(self, *_): raise ShopifyCatalogError("temporary secret provider error")
    monkeypatch.setattr(lifecycle, "ShopifyCatalogClient", Unavailable)
    assert client.post("/webhooks/shopify/orders", content=body,
                       headers=_headers(body, "retryable")).status_code == 503
    with SessionLocal() as db:
        event = db.query(ShopifyWebhookEvent).filter_by(webhook_id="retryable").one()
        assert event.status == "FAILED_RETRYABLE" and event.claim_token is None
    _client(monkeypatch)
    assert client.post("/webhooks/shopify/orders", content=body,
                       headers=_headers(body, "retryable")).json()["status"] == "PROCESSED"
    with SessionLocal() as db:
        event = db.query(ShopifyWebhookEvent).filter_by(webhook_id="retryable").one()
        assert event.attempts == 2 and db.get(SalesOrder, order_id).shopify_order_id is not None


class _ReadOnlyShopify:
    def __init__(self, result=None, error=None): self.result, self.error, self.calls = result, error, []
    def graphql(self, query, variables):
        self.calls.append((query, variables))
        if self.error: raise self.error
        return self.result


def test_missed_webhook_reconciliation_is_read_only_and_links_order():
    with SessionLocal() as db:
        row = _order(db); draft_id = row.shopify_draft_order_id
        remote = _ReadOnlyShopify({"draftOrder": {"id": draft_id, "name": "#D1",
            "order": {"id": "gid://shopify/Order/77", "name": "#1077",
                      "createdAt": "2026-10-23T10:00:00Z"}}})
        saved = reconcile_order(db, row, "lifecycle-admin", remote)
        assert saved.shopify_order_id.endswith("/77") and saved.order_status == "CONFIRMED"
        assert len(remote.calls) == 1 and "mutation" not in remote.calls[0][0].lower()


def test_reconciliation_reports_missing_shopify_permission_safely():
    with SessionLocal() as db:
        row = _order(db)
        with pytest.raises(Exception, match="reconciliation is unavailable"):
            reconcile_order(db, row, "lifecycle-admin",
                            _ReadOnlyShopify(error=ShopifyCatalogError("access denied with token secret")))


def test_staff_reconciliation_requires_flags_auth_role_and_csrf(monkeypatch):
    with SessionLocal() as db:
        order_id = _order(db).id
        staff = db.query(User).filter_by(username="lifecycle-staff").one_or_none()
        if staff is None:
            db.add(User(username="lifecycle-staff", password_hash=hash_password("password"), role="STAFF"))
        db.commit()
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_SYNC_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED", True)
    anonymous = TestClient(main.app, base_url="https://testserver", follow_redirects=False)
    assert anonymous.post(f"/api/sales-orders/{order_id}/shopify-lifecycle-reconcile").status_code == 303
    admin = TestClient(main.app, base_url="https://testserver")
    assert admin.post("/login", data={"username": "lifecycle-admin", "password": "password",
                                      "next": "/"}).status_code == 200
    assert admin.post(f"/api/sales-orders/{order_id}/shopify-lifecycle-reconcile").status_code == 403
    staff_client = TestClient(main.app, base_url="https://testserver")
    assert staff_client.post("/login", data={"username": "lifecycle-staff", "password": "password",
                                             "next": "/"}).status_code == 200
    token = staff_client.get("/api/sales-orders/csrf").json()["csrf_token"]
    assert staff_client.post(f"/api/sales-orders/{order_id}/shopify-lifecycle-reconcile",
                             headers={"X-CSRF-Token": token}).status_code == 403
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED", False)
    assert admin.post(f"/api/sales-orders/{order_id}/shopify-lifecycle-reconcile").status_code == 404


@pytest.mark.skipif(not os.getenv("TEST_POSTGRESQL_URL"), reason="dedicated TEST_POSTGRESQL_URL not configured")
def test_postgresql_concurrent_duplicate_delivery_is_processed_once():
    url = os.environ["TEST_POSTGRESQL_URL"]
    assert "test" in url.lower(), "refusing a PostgreSQL URL not explicitly named for testing"
    pg_engine = create_engine(url, pool_size=4)
    TestingSession = sessionmaker(bind=pg_engine)
    Base.metadata.create_all(pg_engine)
    number = "SO-20261023-87654321"
    with TestingSession() as db:
        db.query(ShopifyWebhookEvent).filter_by(webhook_id="pg-lifecycle-concurrency").delete()
        db.query(SalesOrderStatusHistory).delete()
        db.query(SalesOrderLine).delete()
        db.query(SalesOrder).filter_by(internal_order_number=number).delete()
        _order(db, number)
    body = json.dumps(_payload(number)).encode(); payload = _payload(number)
    def deliver():
        with TestingSession() as db:
            return process_orders_create(db, payload, webhook_id="pg-lifecycle-concurrency",
                                         shop_domain="test.myshopify.com", raw_body=body,
                                         client=_ReadOnlyShopify({"draftOrder": {
                                             "id": f"gid://shopify/DraftOrder/{int(number[-8:], 16)}",
                                             "order": {"id": "gid://shopify/Order/9001",
                                                       "name": "#1001", "createdAt": None}}}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver(), range(2)))
    assert results == ["PROCESSED", "PROCESSED"]
    with TestingSession() as db:
        row = db.query(SalesOrder).filter_by(internal_order_number=number).one()
        assert db.query(ShopifyWebhookEvent).filter_by(webhook_id="pg-lifecycle-concurrency").count() == 1
        assert db.query(SalesOrderStatusHistory).filter_by(sales_order_id=row.id).count() == 2
