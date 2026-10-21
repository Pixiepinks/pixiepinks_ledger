from decimal import Decimal
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

import main
import shopify_draft_order_service as sync
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import SalesOrder, SalesOrderLine, ShopifySyncEvent, User
from sales_order_service import create_draft
from utils_auth import hash_password


class FakeShopify:
    def __init__(self, *, existing=None, total="1000.00", tax="0.00", error=None, completed=False):
        self.existing = existing or []
        self.total = total
        self.tax = tax
        self.error = error
        self.completed = completed
        self.calls = []

    def graphql(self, query, variables):
        self.calls.append((query, variables))
        if self.error:
            raise self.error
        draft = {"id": "gid://shopify/DraftOrder/123", "name": "#D123", "status": "OPEN",
                 "updatedAt": "2026-10-21T00:00:00Z",
                 "totalPriceSet": {"shopMoney": {"amount": self.total, "currencyCode": "LKR"}},
                 "totalTaxSet": {"shopMoney": {"amount": self.tax, "currencyCode": "LKR"}},
                 "order": ({"id": "gid://shopify/Order/9", "name": "#1009"}
                           if self.completed else None)}
        if "FindLedgerDraft" in query:
            return {"draftOrders": {"nodes": self.existing}}
        if "CreateLedgerDraft" in query:
            return {"draftOrderCreate": {"draftOrder": draft, "userErrors": []}}
        return {"draftOrder": draft}


def setup_function():
    Base.metadata.create_all(engine); upgrade_database()
    with SessionLocal() as db:
        db.query(ShopifySyncEvent).delete(); db.query(SalesOrderLine).delete(); db.query(SalesOrder).delete()
        for username, role in (("sync-admin", "ADMIN"), ("sync-agent", "SALES_AGENT")):
            user = db.query(User).filter_by(username=username).one_or_none()
            if not user: db.add(User(username=username, password_hash=hash_password("password"), role=role))
            else: user.role = role
        db.commit()


def order(db, *, tax="0", shipping="0", discount="0", line_discount="0"):
    payload = {"customer_name": "Prakash", "whatsapp_phone": "94771234567",
               "delivery_contact_person": "Prakash", "delivery_address": "1 Main Street",
               "city": "Colombo", "district": "Western", "postal_code": "00100",
               "shipping_total": shipping, "tax_total": tax, "order_discount": discount,
               "lines": [{"shopify_product_id": "gid://shopify/Product/1",
                           "shopify_variant_id": "gid://shopify/ProductVariant/11",
                           "product_name": "Bicycle", "variant_name": "16 inch",
                           "sku": "BIKE-16", "quantity": "1", "unit_price": "1000.00",
                           "discount_amount": line_discount}]}
    return create_draft(db, payload, "sync-admin", "draft-sync-test")[0]


def available(*_):
    return {"id": "gid://shopify/Product/1", "variant_id": "gid://shopify/ProductVariant/11",
            "title": "Bicycle", "price": Decimal("1000.00"), "available": True,
            "taxable": True}


def client_for(username="sync-admin"):
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username": username, "password": "password", "next": "/"}).status_code == 200
    return client


def test_phase_three_migration_is_additive():
    order_columns = {column["name"] for column in inspect(engine).get_columns("sales_orders")}
    event_columns = {column["name"] for column in inspect(engine).get_columns("shopify_sync_events")}
    assert {"shopify_draft_order_name", "shopify_sync_updated_at", "shopify_sync_error"} <= order_columns
    assert {"actor", "updated_at", "claim_token", "claim_expires_at"} <= event_columns


def test_create_maps_variant_customer_delivery_discounts_and_shipping(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db, shipping="100", discount="50", tax="10")
        remote = FakeShopify(total="1060.00", tax="10.00")
        saved, created = sync.create_shopify_draft(db, draft, "sync-admin", remote)
        assert created and saved.shopify_draft_order_id.endswith("/123") and saved.shopify_sync_status == "SYNCED"
        create_call = next(call for call in remote.calls if "CreateLedgerDraft" in call[0])
        data = create_call[1]["input"]
        assert data["lineItems"][0]["variantId"].endswith("/11")
        assert data["shippingAddress"]["address1"] == "1 Main Street"
        assert data["appliedDiscount"]["value"] == 50.0
        assert isinstance(data["appliedDiscount"]["value"], float)
        assert "amount" not in data["appliedDiscount"]
        assert data["shippingLine"]["price"] == "100.00"
        assert data["taxExempt"] is False and "taxable" not in data["lineItems"][0]
        assert "ledger-" in data["tags"][1]


def test_line_and_order_discounts_use_2026_07_graphql_input_shape(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db, discount="50.00", line_discount="25.00")
        data = sync._shopify_input(draft)
        line_discount = data["lineItems"][0]["appliedDiscount"]
        order_discount = data["appliedDiscount"]
        assert line_discount["value"] == 25.0 and order_discount["value"] == 50.0
        for discount in (line_discount, order_discount):
            assert discount["valueType"] == "FIXED_AMOUNT"
            assert isinstance(discount["value"], float)
            assert "amount" not in discount and "amountWithCurrency" not in discount


def test_catalog_preflight_requests_variant_taxability():
    from shopify_catalog_service import INBOX_PRODUCT_PICKER_QUERY
    assert "sku price taxable availableForSale" in INBOX_PRODUCT_PICKER_QUERY


def test_duplicate_and_concurrent_requests_do_not_create_again(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db); remote = FakeShopify()
        first, created = sync.create_shopify_draft(db, draft, "sync-admin", remote)
        second, duplicate_created = sync.create_shopify_draft(db, first, "sync-admin", remote)
        assert created is True and duplicate_created is False and second.id == first.id
        assert sum("CreateLedgerDraft" in query for query, _ in remote.calls) == 1
        assert db.query(ShopifySyncEvent).filter_by(event_type=sync.CREATE_EVENT).count() == 1


def test_uncertain_outcome_reconciles_remote_before_retry(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db)
        db.add(ShopifySyncEvent(event_type=sync.CREATE_EVENT, sales_order_id=draft.id,
            direction="OUTBOUND", idempotency_key=f"shopify-draft-create:{draft.id}",
            payload_hash="x" * 64, status="RECONCILIATION_REQUIRED", attempts=1, actor="sync-admin"))
        db.commit()
        found = FakeShopify().graphql(sync.GET_QUERY, {"id": "x"})["draftOrder"]
        saved, created = sync.create_shopify_draft(db, draft, "sync-admin", FakeShopify(existing=[found]))
        assert created is False and saved.shopify_draft_order_id == found["id"]


def test_uncertain_without_remote_match_never_blindly_retries():
    with SessionLocal() as db:
        draft = order(db)
        db.add(ShopifySyncEvent(event_type=sync.CREATE_EVENT, sales_order_id=draft.id,
            direction="OUTBOUND", idempotency_key=f"shopify-draft-create:{draft.id}",
            payload_hash="x" * 64, status="RECONCILIATION_REQUIRED", attempts=1))
        db.commit(); remote = FakeShopify()
        try: sync.create_shopify_draft(db, draft, "sync-admin", remote)
        except sync.ShopifyOutcomeUncertain: pass
        else: raise AssertionError("uncertain creation must remain blocked")
        assert not any("CreateLedgerDraft" in query for query, _ in remote.calls)


def test_timeout_after_mutation_is_marked_for_reconciliation(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    class TimeoutAfterPreflight(FakeShopify):
        def graphql(self, query, variables):
            if "FindLedgerDraft" in query:
                return {"draftOrders": {"nodes": []}}
            raise sync.ShopifyCatalogError("Shopify catalogue request failed")
    with SessionLocal() as db:
        draft = order(db)
        try: sync.create_shopify_draft(db, draft, "sync-admin", TimeoutAfterPreflight())
        except sync.ShopifyOutcomeUncertain: pass
        else: raise AssertionError("a timed-out write must be uncertain")
        db.refresh(draft)
        assert draft.shopify_sync_status == "RECONCILIATION_REQUIRED"


def test_preflight_outage_is_safe_to_retry(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db)
        try:
            sync.create_shopify_draft(db, draft, "sync-admin",
                FakeShopify(error=sync.ShopifyCatalogError("Shopify catalogue request failed")))
        except sync.ShopifyDraftSyncError: pass
        else: raise AssertionError("preflight outage must fail")
        db.refresh(draft)
        assert draft.shopify_sync_status == "SYNC_FAILED"


def test_database_failure_after_shopify_success_blocks_duplicate(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db); real_commit = db.commit; calls = 0
        def fail_remote_result_once():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SQLAlchemyError("simulated commit failure")
            return real_commit()
        db.commit = fail_remote_result_once
        try: sync.create_shopify_draft(db, draft, "sync-admin", FakeShopify())
        except sync.ShopifyOutcomeUncertain: pass
        else: raise AssertionError("local persistence failure must be uncertain")
        recovered = db.get(SalesOrder, draft.id)
        assert recovered.shopify_sync_status == "RECONCILIATION_REQUIRED"
        event = db.query(ShopifySyncEvent).filter_by(sales_order_id=draft.id).one()
        assert event.status == "RECONCILIATION_REQUIRED"


def test_financial_mismatch_is_visible_and_refresh_records_completed_order(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db); saved, _ = sync.create_shopify_draft(db, draft, "sync-admin", FakeShopify(total="999.00"))
        assert saved.shopify_sync_status == "RECONCILIATION_REQUIRED" and "differs" in saved.shopify_sync_error
        refreshed = sync.refresh_shopify_draft(db, saved, "sync-admin", FakeShopify(completed=True))
        assert refreshed.shopify_order_name == "#1009" and refreshed.order_status == "DRAFT"


def test_tax_policy_reconciles_zero_tax_and_detects_tax_and_rounding_mismatches(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        zero_tax = order(db)
        assert sync.create_shopify_draft(db, zero_tax, "sync-admin", FakeShopify())[0].shopify_sync_status == "SYNCED"
    setup_function()
    with SessionLocal() as db:
        taxable = order(db, tax="80.00")
        saved, _ = sync.create_shopify_draft(
            db, taxable, "sync-admin", FakeShopify(total="1080.00", tax="79.99"))
        assert saved.shopify_sync_status == "RECONCILIATION_REQUIRED"
        assert "Shopify tax LKR 79.99 differs from Ledger LKR 80.00" in saved.shopify_sync_error


def test_non_taxable_product_rejects_nonzero_ledger_tax(monkeypatch):
    def non_taxable(*_):
        product = available()
        product["taxable"] = False
        return product
    monkeypatch.setattr(sync, "resolve_inbox_product", non_taxable)
    with SessionLocal() as db:
        draft = order(db, tax="10.00")
        try:
            sync.create_shopify_draft(db, draft, "sync-admin", FakeShopify(total="1010.00", tax="10.00"))
        except sync.ShopifyDraftSyncError as exc:
            assert "non-taxable" in str(exc)
        else:
            raise AssertionError("non-taxable products must not accept Ledger tax")


def test_taxable_product_requires_address(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db)
        draft.delivery_address = None
        try:
            sync._shopify_input(draft)
        except sync.ShopifyDraftSyncError as exc:
            assert "delivery address" in str(exc)
        else:
            raise AssertionError("taxable products require an address for Shopify tax")


def test_shipping_tax_difference_is_never_silently_accepted(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db, shipping="100.00", tax="0.00")
        saved, _ = sync.create_shopify_draft(
            db, draft, "sync-admin", FakeShopify(total="1115.00", tax="15.00"))
        assert saved.shopify_sync_status == "RECONCILIATION_REQUIRED"
        assert "Shopify total" in saved.shopify_sync_error and "Shopify tax" in saved.shopify_sync_error


def test_failed_retry_is_atomically_claimed_once():
    with SessionLocal() as db:
        draft = order(db)
        event = ShopifySyncEvent(event_type=sync.CREATE_EVENT, sales_order_id=draft.id,
            direction="OUTBOUND", idempotency_key=f"shopify-draft-create:{draft.id}",
            payload_hash="x" * 64, status="SYNC_FAILED", attempts=1)
        db.add(event); db.commit(); event_id = event.id
    with SessionLocal() as first, SessionLocal() as second:
        token = sync._claim_failed_event(first, event_id, "first")
        assert token
        try:
            sync._claim_failed_event(second, event_id, "second")
        except sync.ShopifyDraftSyncError as exc:
            assert "already in progress" in str(exc)
        else:
            raise AssertionError("a second worker must not claim the same retry")


@pytest.mark.skipif(engine.dialect.name != "postgresql",
                    reason="requires the configured PostgreSQL integration database")
def test_postgresql_two_sessions_only_one_retry_claims():
    with SessionLocal() as db:
        draft = order(db)
        event = ShopifySyncEvent(event_type=sync.CREATE_EVENT, sales_order_id=draft.id,
            direction="OUTBOUND", idempotency_key=f"shopify-draft-create:{draft.id}",
            payload_hash="x" * 64, status="SYNC_FAILED", attempts=1)
        db.add(event); db.commit(); event_id = event.id
    barrier = Barrier(2)

    def claim(actor):
        with SessionLocal() as session:
            barrier.wait()
            try:
                return sync._claim_failed_event(session, event_id, actor)
            except sync.ShopifyDraftSyncError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-one", "worker-two")))
    assert sum(result is not None for result in results) == 1


def test_stale_processing_claim_requires_reconciliation_not_retry(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db:
        draft = order(db)
        db.add(ShopifySyncEvent(event_type=sync.CREATE_EVENT, sales_order_id=draft.id,
            direction="OUTBOUND", idempotency_key=f"shopify-draft-create:{draft.id}",
            payload_hash="x" * 64, status="PROCESSING", attempts=1,
            claim_token="ab" * 32, claim_expires_at=datetime.utcnow() - timedelta(seconds=1)))
        db.commit(); remote = FakeShopify()
        try:
            sync.create_shopify_draft(db, draft, "sync-admin", remote)
        except sync.ShopifyOutcomeUncertain:
            pass
        else:
            raise AssertionError("an expired write claim must require reconciliation")
        assert not any("CreateLedgerDraft" in query for query, _ in remote.calls)


def test_shopify_admin_url_requires_exact_draft_order_gid():
    candidate = SalesOrder(shopify_draft_order_id="gid://shopify/DraftOrder/123")
    assert sync.shopify_admin_draft_url(candidate, "pixiepinks") == (
        "https://admin.shopify.com/store/pixiepinks/draft_orders/123")
    for malicious in (
        "gid://shopify/Order/123", "gid://shopify/DraftOrder/not-a-number",
        "gid://shopify/DraftOrder/123?next=https://evil.example", "https://evil.example/123",
        "gid://shopify/DraftOrder/123/../../settings",
    ):
        candidate.shopify_draft_order_id = malicious
        assert sync.shopify_admin_draft_url(candidate, "pixiepinks") is None
    candidate.shopify_draft_order_id = "gid://shopify/DraftOrder/123"
    assert sync.shopify_admin_draft_url(candidate, "pixiepinks/evil") is None


def test_invalid_remote_draft_gid_is_not_persisted(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    remote = FakeShopify()
    original = remote.graphql

    def malicious_response(query, variables):
        response = original(query, variables)
        if "CreateLedgerDraft" in query:
            response["draftOrderCreate"]["draftOrder"]["id"] = "gid://shopify/Order/123"
        return response

    remote.graphql = malicious_response
    with SessionLocal() as db:
        draft = order(db)
        with pytest.raises(sync.ShopifyOutcomeUncertain):
            sync.create_shopify_draft(db, draft, "sync-admin", remote)
        db.refresh(draft)
        assert draft.shopify_draft_order_id is None
        assert draft.shopify_sync_status == "RECONCILIATION_REQUIRED"


def test_endpoint_flags_authorization_csrf_confirmation_and_success(monkeypatch):
    monkeypatch.setattr(sync, "resolve_inbox_product", available)
    with SessionLocal() as db: draft_id = order(db).id
    admin = client_for(); agent = client_for("sync-agent")
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_SYNC_ENABLED", False)
    assert admin.post(f"/api/sales-orders/{draft_id}/shopify-draft", json={}).status_code == 404
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_SYNC_ENABLED", True)
    token = admin.get("/api/sales-orders/csrf").json()["csrf_token"]
    assert agent.post(f"/api/sales-orders/{draft_id}/shopify-draft", json={},
                      headers={"X-CSRF-Token": token}).status_code == 403
    assert admin.post(f"/api/sales-orders/{draft_id}/shopify-draft", json={}).status_code == 403
    assert admin.post(f"/api/sales-orders/{draft_id}/shopify-draft", json={},
                      headers={"X-CSRF-Token": token}).status_code == 422
    monkeypatch.setattr(sync, "ShopifyCatalogClient", lambda: FakeShopify())
    result = admin.post(f"/api/sales-orders/{draft_id}/shopify-draft",
        json={"confirmation": "CREATE SHOPIFY DRAFT"}, headers={"X-CSRF-Token": token})
    assert result.status_code == 200 and result.json()["order"]["shopify_sync_status"] == "SYNCED"


def test_detail_controls_dashboard_filter_and_whatsapp_payload(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_SYNC_ENABLED", True)
    with SessionLocal() as db:
        draft = order(db); draft.shopify_sync_status = "SYNC_FAILED"; db.commit(); draft_id = draft.id
    client = client_for()
    detail = client.get(f"/crm/sales-orders/{draft_id}")
    assert "Create Shopify Draft" in detail.text and "Shopify integration" in detail.text
    dashboard = client.get("/crm/sales-orders", params={"sync_status": "SYNC_FAILED"})
    assert dashboard.status_code == 200 and "SYNC FAILED" in dashboard.text
