from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import inspect

import main
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import (AccountingPostingLink, SalesOrder, SalesOrderLine, SalesOrderStatusHistory,
                    SalesPayment, ShopifySyncEvent, User)
from sales_order_service import SalesOrderValidationError, calculate_order
from utils_auth import hash_password


def _verified_product(product_id, variant_id):
    suffix = variant_id.rsplit("/", 1)[-1]
    return {"id": product_id, "variant_id": variant_id, "handle": f"product-{suffix}",
            "title": "Product One" if suffix == "11" else "Product Two",
            "variant_title": "Pink" if suffix == "11" else "Standard", "sku": f"SKU-{suffix}",
            "price": Decimal("1000.00") if suffix == "11" else Decimal("200.00"),
            "currency": "LKR", "image": "https://cdn.example/product.jpg",
            "url": f"https://www.pixiepinks.shop/products/product-{suffix}"}


def setup_function():
    Base.metadata.create_all(engine)
    upgrade_database()
    with SessionLocal() as db:
        db.query(AccountingPostingLink).delete()
        db.query(ShopifySyncEvent).delete()
        db.query(SalesOrderStatusHistory).delete()
        db.query(SalesPayment).delete()
        db.query(SalesOrderLine).delete()
        db.query(SalesOrder).delete()
        user = db.query(User).filter_by(username="sales-admin").one_or_none()
        if user is None:
            user = User(username="sales-admin", password_hash=hash_password("password"), role="ADMIN")
            db.add(user)
        else:
            user.role = "ADMIN"
        staff = db.query(User).filter_by(username="ordinary-staff").one_or_none()
        if staff is None:
            db.add(User(username="ordinary-staff", password_hash=hash_password("password"), role="STAFF"))
        else:
            staff.role = "STAFF"
        db.commit()


def client_for(username="sales-admin"):
    client = TestClient(main.app, base_url="https://testserver")
    response = client.post("/login", data={"username": username, "password": "password", "next": "/"})
    assert response.status_code == 200
    return client


def csrf(client):
    response = client.get("/api/sales-orders/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def payload(**overrides):
    value = {
        "customer_name": "Test Customer", "whatsapp_phone": "+94 77 123 4567",
        "shipping_total": "500.00", "order_discount": "100.00", "tax_total": "50.00",
        "lines": [
            {"shopify_product_id": "gid://shopify/Product/1",
             "shopify_variant_id": "gid://shopify/ProductVariant/11", "sku": "SKU-1",
             "product_name": "Product One", "variant_name": "Pink", "quantity": "2",
             "unit_price": "1000.00", "discount_amount": "100.00", "tax_amount": "20.00"},
            {"shopify_product_id": "gid://shopify/Product/2",
             "shopify_variant_id": "gid://shopify/ProductVariant/22", "sku": "SKU-2",
             "product_name": "Product Two", "variant_name": "Standard", "quantity": "1.5",
             "unit_price": "200.00", "discount_amount": "0", "tax_amount": "0"},
        ],
    }
    value.update(overrides)
    return value


def test_versioned_migration_is_idempotent_and_creates_phase_one_tables():
    upgrade_database(); upgrade_database()
    inspector = inspect(engine)
    assert inspector.has_table("alembic_version")
    for table in ("sales_orders", "sales_order_lines", "sales_payments",
                  "sales_order_status_history", "shopify_sync_events", "accounting_posting_links"):
        assert inspector.has_table(table)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "20261021_02"


def test_decimal_calculation_handles_quantity_discounts_shipping_and_tax():
    totals = calculate_order(payload()["lines"], "100", "500", "50")
    assert totals.subtotal == Decimal("2300.00")
    assert totals.discount_total == Decimal("200.00")
    assert totals.tax_total == Decimal("70.00")
    assert totals.grand_total == Decimal("2670.00")
    assert totals.lines[1].quantity == Decimal("1.500")


def test_supported_whole_and_fractional_quantities_and_totals():
    for raw_quantity, expected_quantity, expected_total in (
        ("1", Decimal("1.000"), Decimal("125.00")),
        ("2", Decimal("2.000"), Decimal("250.00")),
        ("3", Decimal("3.000"), Decimal("375.00")),
        ("1.5", Decimal("1.500"), Decimal("187.50")),
        ("0.001", Decimal("0.001"), Decimal("0.13")),
    ):
        totals = calculate_order([{"quantity": raw_quantity, "unit_price": "125.00"}])
        assert totals.lines[0].quantity == expected_quantity
        assert totals.grand_total == expected_total


def test_zero_and_negative_quantities_are_rejected():
    for invalid in ("0", "-1", "-0.001"):
        try:
            calculate_order([{"quantity": invalid, "unit_price": "125.00"}])
        except SalesOrderValidationError as exc:
            assert "quantity is outside the allowed range" in str(exc)
        else:
            raise AssertionError(f"invalid quantity {invalid} was accepted")


def test_invalid_discount_is_rejected():
    bad = payload()["lines"]
    bad[0]["discount_amount"] = "3000"
    try:
        calculate_order(bad)
    except SalesOrderValidationError as exc:
        assert "discount exceeds" in str(exc)
    else:
        raise AssertionError("invalid discount was accepted")


def test_feature_flag_is_disabled_by_default_and_blocks_mutation():
    client = client_for()
    assert client.get("/api/sales-orders/config").json()["sales_orders_enabled"] is False
    response = client.post("/api/sales-orders", json=payload(), headers={"Idempotency-Key": "disabled"})
    assert response.status_code == 404


def test_csrf_and_role_are_enforced_server_side(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    admin = client_for()
    assert admin.post("/api/sales-orders", json=payload(),
                      headers={"Idempotency-Key": "no-csrf"}).status_code == 403
    staff = client_for("ordinary-staff")
    token = csrf(staff)
    response = staff.post("/api/sales-orders", json=payload(),
                          headers={"Idempotency-Key": "staff", "X-CSRF-Token": token})
    assert response.status_code == 403


def test_draft_creation_is_idempotent_and_persists_decimal_totals(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    monkeypatch.setattr(main, "resolve_inbox_product", _verified_product)
    client = client_for(); token = csrf(client)
    headers = {"Idempotency-Key": "same-draft", "X-CSRF-Token": token}
    first = client.post("/api/sales-orders", json=payload(), headers=headers)
    second = client.post("/api/sales-orders", json=payload(), headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["order"]["id"] == second.json()["order"]["id"]
    assert first.json()["order"]["grand_total"] == "2670.00"
    assert len(first.json()["order"]["lines"]) == 2
    with SessionLocal() as db:
        assert db.query(SalesOrder).count() == 1
        order = db.query(SalesOrder).one()
        assert order.internal_order_number.startswith("SO-")
        assert order.whatsapp_phone == "94771234567"


def test_draft_update_recalculates_and_status_transition_is_audited(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    monkeypatch.setattr(main, "resolve_inbox_product", _verified_product)
    client = client_for(); token = csrf(client)
    created = client.post("/api/sales-orders", json=payload(order_discount="0"),
        headers={"Idempotency-Key": "editable", "X-CSRF-Token": token}).json()["order"]
    changed = payload(order_discount="0", shipping_total="0")
    changed["lines"] = [changed["lines"][0]]
    updated = client.put(f"/api/sales-orders/{created['id']}", json=changed,
                         headers={"X-CSRF-Token": token})
    assert updated.status_code == 200
    assert updated.json()["order"]["grand_total"] == "1970.00"
    transition = client.post(f"/api/sales-orders/{created['id']}/transition",
        json={"status": "AWAITING_CUSTOMER_CONFIRMATION", "reason": "Draft checked"},
        headers={"X-CSRF-Token": token})
    assert transition.status_code == 200
    invalid = client.post(f"/api/sales-orders/{created['id']}/transition",
        json={"status": "DELIVERED"}, headers={"X-CSRF-Token": token})
    assert invalid.status_code == 409
    with SessionLocal() as db:
        history = db.query(SalesOrderStatusHistory).one()
        assert (history.previous_status, history.new_status, history.changed_by) == (
            "DRAFT", "AWAITING_CUSTOMER_CONFIRMATION", "sales-admin")
