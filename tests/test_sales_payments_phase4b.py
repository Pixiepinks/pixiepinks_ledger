from datetime import date
from decimal import Decimal
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import (AccountingPostingLink, JournalEntry, SalesOrder, SalesPayment,
                    SalesPaymentAuditEvent, User)
from sales_payment_service import (PAYMENT_CATEGORIES, PaymentValidationError, decide_payment,
                                   record_payment_claim, refresh_shopify_payment_status)
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(engine); upgrade_database()
    with SessionLocal() as db:
        db.query(SalesPaymentAuditEvent).delete()
        db.query(SalesPayment).delete()
        db.query(SalesOrder).delete()
        for username, role in (("payment-admin", "ADMIN"), ("payment-agent", "SALES_AGENT")):
            user = db.query(User).filter_by(username=username).one_or_none()
            if not user:
                db.add(User(username=username, password_hash=hash_password("password"), role=role))
            else:
                user.role = role
        db.commit()


def _order(db, total="1000.00", shopify=False):
    order = SalesOrder(
        internal_order_number="SO-20261024-ABCDEF12", idempotency_key="payment-order",
        customer_name="Payment Test", whatsapp_phone="94770000000", created_by="payment-admin",
        order_status="CONFIRMED", payment_status="UNPAID", fulfillment_status="UNFULFILLED",
        subtotal=total, discount_total=0, shipping_total=0, tax_total=0,
        grand_total=total, amount_paid=0, currency="LKR",
        shopify_order_id="gid://shopify/Order/1803" if shopify else None)
    db.add(order); db.commit(); db.refresh(order)
    return order


def _claim(amount="400.00", method="BANK_TRANSFER", reference="BANK-100"):
    return {"payment_method": method, "amount": amount,
            "payment_date": date.today().isoformat(), "payment_reference": reference,
            "notes": "Customer claim; independently verify receipt"}


def _client(username="payment-admin"):
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username": username, "password": "password",
                                       "next": "/"}).status_code == 200
    return client


def _csrf(client):
    return client.get("/api/sales-orders/csrf").json()["csrf_token"]


def test_claim_is_pending_idempotent_and_does_not_count_as_receipt():
    with SessionLocal() as db:
        order = _order(db)
        first, created = record_payment_claim(db, order.id, _claim(), "payment-agent", "claim-1")
        second, created_again = record_payment_claim(db, order.id, _claim(), "payment-agent", "claim-1")
        db.refresh(order)
        assert created is True and created_again is False and first.id == second.id
        assert first.verification_status == "PENDING"
        assert order.amount_paid == Decimal("0.00")
        assert order.payment_status == "PAYMENT_VERIFICATION"
        assert db.query(SalesPaymentAuditEvent).count() == 1


@pytest.mark.parametrize("method,reference", [
    ("BANK_TRANSFER", "BANK-1"), ("CASH", None), ("COD", None),
    ("CARD_PAYMENT", "CARD-1"), ("PAYMENT_LINK", "LINK-1")])
def test_supported_methods_preserve_individual_method(method, reference):
    with SessionLocal() as db:
        order = _order(db)
        payment, _ = record_payment_claim(
            db, order.id, _claim("100.00", method, reference), "payment-agent", f"method-{method}")
        assert payment.payment_method == method and payment.verification_status == "PENDING"
        assert PAYMENT_CATEGORIES[method] == (
            "ONLINE_PAYMENT" if method in {"CARD_PAYMENT", "PAYMENT_LINK"} else method)


def test_partial_then_full_verification_and_repeated_decision():
    with SessionLocal() as db:
        order = _order(db)
        first, _ = record_payment_claim(db, order.id, _claim("400", reference="TX-1"),
                                        "payment-agent", "partial-1")
        second, _ = record_payment_claim(db, order.id, _claim("600", reference="TX-2"),
                                         "payment-agent", "partial-2")
        decide_payment(db, order.id, first.id, approve=True, actor="payment-admin",
                       idempotency_key="verify-1")
        db.refresh(order)
        assert order.amount_paid == Decimal("400.00") and order.payment_status == "PARTIALLY_PAID"
        _, changed = decide_payment(db, order.id, first.id, approve=True, actor="payment-admin",
                                    idempotency_key="verify-1")
        assert changed is False
        decide_payment(db, order.id, second.id, approve=True, actor="payment-admin",
                       idempotency_key="verify-2")
        db.refresh(order)
        assert order.amount_paid == Decimal("1000.00") and order.payment_status == "PAID"


def test_duplicate_reference_invalid_amount_and_overpayment_are_rejected():
    with SessionLocal() as db:
        order = _order(db)
        record_payment_claim(db, order.id, _claim(reference=" Same Ref "), "payment-agent", "one")
        with pytest.raises(PaymentValidationError, match="already been recorded"):
            record_payment_claim(db, order.id, _claim(reference="same ref"), "payment-agent", "two")
        for invalid in ("0", "-1", "not-money"):
            with pytest.raises(PaymentValidationError):
                record_payment_claim(db, order.id, _claim(invalid, reference=f"X-{invalid}"),
                                     "payment-agent", f"invalid-{invalid}")
        with pytest.raises(PaymentValidationError, match="exceeds"):
            record_payment_claim(db, order.id, _claim("700", reference="OVER"),
                                 "payment-agent", "over")


def test_rejected_claim_is_audited_and_never_counts_as_paid():
    with SessionLocal() as db:
        order = _order(db)
        payment, _ = record_payment_claim(db, order.id, _claim(), "payment-agent", "reject-claim")
        decide_payment(db, order.id, payment.id, approve=False, actor="payment-admin",
                       idempotency_key="reject-action", reason="No matching bank receipt")
        db.refresh(order); db.refresh(payment)
        assert payment.verification_status == "REJECTED"
        assert order.amount_paid == Decimal("0.00") and order.payment_status == "UNPAID"
        assert [event.action for event in db.query(SalesPaymentAuditEvent).order_by(
            SalesPaymentAuditEvent.id)] == ["CLAIM_RECORDED", "CLAIM_REJECTED"]


def test_feature_flag_permissions_and_csrf(monkeypatch):
    with SessionLocal() as db:
        order_id = _order(db).id
    client = _client("payment-agent")
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    assert client.post(f"/api/sales-orders/{order_id}/payment-claims", json=_claim()).status_code == 404
    monkeypatch.setattr(main.settings, "SALES_PAYMENT_TRACKING_ENABLED", True)
    assert client.post(f"/api/sales-orders/{order_id}/payment-claims", json=_claim(),
                       headers={"Idempotency-Key": "missing-csrf"}).status_code == 403
    token = _csrf(client)
    created = client.post(f"/api/sales-orders/{order_id}/payment-claims", json=_claim(), headers={
        "Idempotency-Key": "api-claim", "X-CSRF-Token": token})
    assert created.status_code == 201
    payment_id = created.json()["payment"]["id"]
    assert client.post(f"/api/sales-orders/{order_id}/payments/{payment_id}/verify", headers={
        "Idempotency-Key": "agent-verify", "X-CSRF-Token": token}).status_code == 403


class _ShopifyRead:
    def graphql(self, query, variables):
        assert "mutation" not in query.lower()
        return {"order": {"id": variables["id"], "name": "#1803",
            "displayFinancialStatus": "PAID", "updatedAt": "2026-10-24T00:00:00Z",
            "totalPriceSet": {"shopMoney": {"amount": "1000.00", "currencyCode": "LKR"}},
            "totalReceivedSet": {"shopMoney": {"amount": "1000.00", "currencyCode": "LKR"}},
            "totalOutstandingSet": {"shopMoney": {"amount": "0.00", "currencyCode": "LKR"}},
            "totalRefundedSet": {"shopMoney": {"amount": "0.00", "currencyCode": "LKR"}}}}


def test_shopify_paid_is_observation_only_and_accounting_is_untouched():
    with SessionLocal() as db:
        order = _order(db, shopify=True)
        journal_count = db.query(JournalEntry).count()
        link_count = db.query(AccountingPostingLink).count()
        refresh_shopify_payment_status(db, order, _ShopifyRead())
        assert order.shopify_financial_status == "PAID"
        assert order.shopify_payment_reconciliation_status == "WARNING"
        assert "Ledger receipt verification is incomplete" in order.shopify_payment_reconciliation_warning
        assert order.payment_status == "UNPAID" and order.amount_paid == Decimal("0.00")
        assert db.query(SalesPayment).count() == 0
        assert db.query(JournalEntry).count() == journal_count
        assert db.query(AccountingPostingLink).count() == link_count


@pytest.mark.skipif(not os.getenv("TEST_POSTGRESQL_URL"),
                    reason="dedicated TEST_POSTGRESQL_URL not configured")
def test_postgresql_concurrent_payment_claim_is_created_once():
    url = os.environ["TEST_POSTGRESQL_URL"]
    assert url.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"))
    assert "test" in url.lower(), "refusing a PostgreSQL URL not explicitly named for testing"
    pg_engine = create_engine(url, pool_size=4)
    TestingSession = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(pg_engine)
    number = "SO-20261024-CONCUR01"
    with TestingSession() as db:
        existing = db.query(SalesOrder).filter_by(internal_order_number=number).one_or_none()
        if existing:
            payment_ids = [row[0] for row in db.query(SalesPayment.id).filter_by(
                sales_order_id=existing.id).all()]
            if payment_ids:
                db.query(SalesPaymentAuditEvent).filter(
                    SalesPaymentAuditEvent.sales_payment_id.in_(payment_ids)).delete()
                db.query(SalesPayment).filter(SalesPayment.id.in_(payment_ids)).delete()
            db.delete(existing); db.commit()
        order = SalesOrder(internal_order_number=number, idempotency_key="pg-payment-order",
            customer_name="Concurrency Test", whatsapp_phone="94770000000", created_by="test",
            order_status="CONFIRMED", payment_status="UNPAID", fulfillment_status="UNFULFILLED",
            subtotal=1000, discount_total=0, shipping_total=0, tax_total=0,
            grand_total=1000, amount_paid=0, currency="LKR")
        db.add(order); db.commit(); order_id = order.id
    def submit():
        with TestingSession() as db:
            payment, created = record_payment_claim(
                db, order_id, _claim(), "test-worker", "pg-concurrent-payment")
            return payment.id, created
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert len({payment_id for payment_id, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    with TestingSession() as db:
        assert db.query(SalesPayment).filter_by(sales_order_id=order_id).count() == 1
        assert db.query(SalesPaymentAuditEvent).filter_by(sales_order_id=order_id).count() == 1
