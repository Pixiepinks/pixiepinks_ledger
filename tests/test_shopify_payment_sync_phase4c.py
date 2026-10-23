from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import (JournalEntry, SalesOrder, SalesPayment, ShopifyPaymentSyncAuditEvent,
                    ShopifyPaymentSyncOperation, User)
from shopify_payment_sync_service import (MARK_PAID_MUTATION, ShopifyPaymentSyncError,
    create_preflight, execute_operation, recover_operation)
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(engine); upgrade_database()
    with SessionLocal() as db:
        db.query(ShopifyPaymentSyncAuditEvent).delete()
        db.query(ShopifyPaymentSyncOperation).delete()
        db.query(SalesPayment).delete()
        db.query(SalesOrder).delete(); db.commit()
        for username, role in (("sync-admin", "ADMIN"), ("sync-agent", "SALES_AGENT")):
            user = db.query(User).filter_by(username=username).one_or_none()
            if not user: db.add(User(username=username, password_hash=hash_password("password"), role=role))
            else: user.role = role
        db.commit()


def _order(db, *, status="CONFIRMED", payment_status="PAID", amount_paid="350.00"):
    order = SalesOrder(internal_order_number="SO-20261024-C0FFEE01", idempotency_key="phase4c-order",
        customer_name="Safe Test", whatsapp_phone="94700000000", created_by="admin",
        order_status=status, payment_status=payment_status, fulfillment_status="UNFULFILLED",
        shopify_order_id="gid://shopify/Order/1803", shopify_lifecycle_status="ORDER_LINKED",
        subtotal=350, discount_total=0, shipping_total=0, tax_total=0, grand_total=350,
        amount_paid=amount_paid, currency="LKR")
    db.add(order); db.flush()
    if Decimal(amount_paid) > 0:
        db.add(SalesPayment(sales_order_id=order.id, payment_reference="VERIFIED-1",
            payment_method="BANK_TRANSFER", amount=amount_paid, currency="LKR", payment_date=date.today(),
            verification_status="VERIFIED", source="STAFF_RECORDED", claimed_by="agent",
            verified_by="admin"))
    db.commit(); db.refresh(order); return order


def _remote(*, financial="PENDING", received="0.00", outstanding="350.00", total="350.00",
            currency="LKR", can_mark=True, transactions=None, cancelled=None):
    transactions = transactions or []
    return {"id":"gid://shopify/Order/1803", "name":"#1803", "displayFinancialStatus":financial,
        "canMarkAsPaid":can_mark, "cancelledAt":cancelled, "updatedAt":"2026-10-24T00:00:00Z",
        "totalPriceSet":{"shopMoney":{"amount":total,"currencyCode":currency}},
        "totalReceivedSet":{"shopMoney":{"amount":received,"currencyCode":currency}},
        "totalOutstandingSet":{"shopMoney":{"amount":outstanding,"currencyCode":currency}},
        "totalRefundedSet":{"shopMoney":{"amount":"0.00","currencyCode":currency}},
        "transactionsCount":{"count":len(transactions)}, "transactions":transactions}


def _tx(id="99", kind="SALE", status="SUCCESS", amount="350.00", capturable=False):
    return {"id":f"gid://shopify/OrderTransaction/{id}", "kind":kind, "status":status,
        "gateway":"manual", "createdAt":"2026-10-24T00:00:01Z", "manuallyCapturable":capturable,
        "amountSet":{"shopMoney":{"amount":amount,"currencyCode":"LKR"}}, "parentTransaction":None}


class Shopify:
    def __init__(self, before=None, after=None, *, scopes=True, timeout=False):
        self.before=before or _remote(); self.after=after; self.scopes=scopes
        self.timeout=timeout; self.mutations=0; self.reads=0
    def graphql(self, query, variables):
        if "currentAppInstallation" in query:
            return {"currentAppInstallation":{"accessScopes":[{"handle":"read_orders"}] +
                ([{"handle":"write_orders"}] if self.scopes else [])}}
        if "orderMarkAsPaid" in query:
            self.mutations += 1
            if self.timeout:
                from shopify_catalog_service import ShopifyCatalogError
                raise ShopifyCatalogError("safe")
            return {"orderMarkAsPaid":{"order":self.after,"userErrors":[]}}
        self.reads += 1
        return {"order": self.after if self.mutations and self.after is not None else self.before}


def test_eligible_full_payment_preflight_and_successful_readback():
    after = _remote(financial="PAID", received="350.00", outstanding="0.00", transactions=[_tx()])
    api = Shopify(after=after)
    with SessionLocal() as db:
        order = _order(db); journals = db.query(JournalEntry).count()
        op = create_preflight(db, order.id, "admin", "preflight-1", api)
        assert op.status == "PREFLIGHT_READY" and api.mutations == 0
        op = execute_operation(db, order.id, op.operation_id, "admin", "MARK SHOPIFY ORDER PAID", api)
        assert op.status == "SYNCED" and op.shopify_transaction_id.endswith("/99")
        assert api.mutations == 1
        db.refresh(order)
        assert order.amount_paid == Decimal("350.00") and order.payment_status == "PAID"
        assert order.fulfillment_status == "UNFULFILLED" and db.query(JournalEntry).count() == journals


@pytest.mark.parametrize("changes, message", [
    ({"status":"DRAFT"}, "confirmed"), ({"payment_status":"PARTIALLY_PAID","amount_paid":"100"}, "fully paid")])
def test_unpaid_or_unconfirmed_ledger_is_blocked(changes, message):
    with SessionLocal() as db:
        order = _order(db, **changes)
        with pytest.raises(ShopifyPaymentSyncError, match=message):
            create_preflight(db, order.id, "admin", "blocked", Shopify())


def test_pending_claim_blocks_and_rejected_claim_is_excluded():
    with SessionLocal() as db:
        order = _order(db)
        db.add(SalesPayment(sales_order_id=order.id, payment_reference="PENDING", payment_method="CASH",
            amount=1, currency="LKR", payment_date=date.today(), verification_status="PENDING")); db.commit()
        with pytest.raises(ShopifyPaymentSyncError, match="Pending"):
            create_preflight(db, order.id, "admin", "pending", Shopify())
        db.query(SalesPayment).filter_by(payment_reference="PENDING").update({"verification_status":"REJECTED"}); db.commit()
        assert create_preflight(db, order.id, "admin", "rejected-excluded", Shopify()).status == "PREFLIGHT_READY"


@pytest.mark.parametrize("remote,message", [
    (_remote(financial="PAID",received="350",outstanding="0"), "financial status"),
    (_remote(received="10",outstanding="340"), "totals do not match"),
    (_remote(currency="USD"), "currencies"),
    (_remote(can_mark=False), "does not permit"),
    (_remote(cancelled="2026-10-24T00:00:00Z"), "cancelled"),
])
def test_remote_financial_mismatches_fail_closed(remote, message):
    with SessionLocal() as db:
        order = _order(db)
        with pytest.raises(ShopifyPaymentSyncError, match=message):
            create_preflight(db, order.id, "admin", "remote-block", Shopify(before=remote))


@pytest.mark.parametrize("transaction,message", [
    (_tx(kind="AUTHORIZATION"), "authorization"),
    (_tx(kind="AUTHORIZATION",status="FAILURE",capturable=True), "capturable"),
    (_tx(kind="SALE",status="PENDING"), "pending"),
    (_tx(kind="SALE",status="AWAITING_RESPONSE"), "pending"),
    (_tx(kind="MYSTERY",status="FAILURE"), "not safely recognized"),
])
def test_authorization_and_ambiguous_transaction_states_are_blocked(transaction, message):
    with SessionLocal() as db:
        order = _order(db)
        with pytest.raises(ShopifyPaymentSyncError, match=message):
            create_preflight(db, order.id, "admin", "tx-block", Shopify(before=_remote(transactions=[transaction])))


def test_missing_write_scope_blocks_without_mutation():
    api=Shopify(scopes=False)
    with SessionLocal() as db:
        order=_order(db)
        with pytest.raises(ShopifyPaymentSyncError, match="write_orders"):
            create_preflight(db, order.id, "admin", "scope", api)
        assert api.mutations == 0


def test_duplicate_preflight_and_execute_are_idempotent():
    api=Shopify(after=_remote(financial="PAID",received="350",outstanding="0",transactions=[_tx()]))
    with SessionLocal() as db:
        order=_order(db)
        first=create_preflight(db,order.id,"admin","same",api)
        second=create_preflight(db,order.id,"admin","same",api)
        assert first.id == second.id
        execute_operation(db,order.id,first.operation_id,"admin","MARK SHOPIFY ORDER PAID",api)
        execute_operation(db,order.id,first.operation_id,"admin","MARK SHOPIFY ORDER PAID",api)
        assert api.mutations == 1


def test_timeout_is_uncertain_and_recovery_never_mutates():
    api=Shopify(timeout=True)
    with SessionLocal() as db:
        order=_order(db); op=create_preflight(db,order.id,"admin","timeout",api)
        with pytest.raises(ShopifyPaymentSyncError, match="uncertain"):
            execute_operation(db,order.id,op.operation_id,"admin","MARK SHOPIFY ORDER PAID",api)
        db.refresh(op); assert op.status == "OUTCOME_UNCERTAIN" and api.mutations == 1
        api.timeout=False; api.after=_remote(financial="PAID",received="350",outstanding="0",transactions=[_tx()])
        recovered=recover_operation(db,order.id,op.operation_id,"admin",api)
        assert recovered.status == "SYNCED" and api.mutations == 1


def test_incomplete_transaction_history_is_blocked():
    remote=_remote(); remote["transactionsCount"]={"count":1}
    with SessionLocal() as db:
        order=_order(db)
        with pytest.raises(ShopifyPaymentSyncError, match="incomplete"):
            create_preflight(db,order.id,"admin","incomplete",Shopify(before=remote))


def test_mutation_constant_is_only_order_mark_as_paid():
    assert "orderMarkAsPaid" in MARK_PAID_MUTATION
    assert "orderCreateManualPayment" not in MARK_PAID_MUTATION


def test_api_feature_flag_role_and_csrf_are_enforced(monkeypatch):
    with SessionLocal() as db: order_id = _order(db).id
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username":"sync-agent", "password":"password", "next":"/"}).status_code == 200
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    response = client.post(f"/api/sales-orders/{order_id}/shopify-payment-sync/preflight")
    assert response.status_code == 404
    monkeypatch.setattr(main.settings, "SALES_PAYMENT_TRACKING_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED", True)
    monkeypatch.setattr(main.settings, "SHOPIFY_PAYMENT_SYNC_ENABLED", True)
    token = client.get("/api/sales-orders/csrf").json()["csrf_token"]
    assert client.post(f"/api/sales-orders/{order_id}/shopify-payment-sync/preflight",
        headers={"X-CSRF-Token":token,"Idempotency-Key":"api"}).status_code == 403
    admin = TestClient(main.app, base_url="https://testserver")
    assert admin.post("/login", data={"username":"sync-admin", "password":"password", "next":"/"}).status_code == 200
    assert admin.post(f"/api/sales-orders/{order_id}/shopify-payment-sync/preflight",
        headers={"Idempotency-Key":"api"}).status_code == 403
