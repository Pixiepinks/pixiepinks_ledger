from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

import main
from database import Base, SessionLocal, engine
from migrations import upgrade_database
from models import SalesOrder, SalesOrderLine, User, WhatsAppConversation
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(engine); upgrade_database()
    with SessionLocal() as db:
        db.query(SalesOrderLine).delete(); db.query(SalesOrder).delete(); db.query(WhatsAppConversation).delete()
        for username, role in (("phase2-admin", "ADMIN"), ("phase2-staff", "STAFF")):
            user = db.query(User).filter_by(username=username).one_or_none()
            if not user: db.add(User(username=username, password_hash=hash_password("password"), role=role))
            else: user.role = role
        db.add(WhatsAppConversation(phone_number="94771234567", customer_name="Shopper"))
        db.commit()


def client_for(username="phase2-admin"):
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username": username, "password": "password", "next": "/"}).status_code == 200
    return client


def verified(product_id, variant_id):
    return {"id": product_id, "variant_id": variant_id, "handle": "pink-bike", "title": "Pink Bike",
            "variant_title": "16 inch", "sku": "BIKE-16", "price": Decimal("16400.00"),
            "currency": "LKR", "image": "https://cdn.example/pink-bike.jpg",
            "url": "https://www.pixiepinks.shop/products/pink-bike"}


def order_payload(conversation_id):
    return {"crm_conversation_id": conversation_id, "customer_name": "Shopper",
            "whatsapp_phone": "+94 77 123 4567", "shipping_total": "0", "lines": [{
                "shopify_product_id": "gid://shopify/Product/1",
                "shopify_variant_id": "gid://shopify/ProductVariant/11", "quantity": "1",
                "unit_price": "1.00", "product_name": "Untrusted browser title"}]}


def create_order(client, monkeypatch, key="phase2-order"):
    monkeypatch.setattr(main, "resolve_inbox_product", verified)
    conversation_id = SessionLocal().query(WhatsAppConversation).one().id
    token = client.get("/api/sales-orders/csrf").json()["csrf_token"]
    return client.post("/api/sales-orders", json=order_payload(conversation_id), headers={
        "X-CSRF-Token": token, "Idempotency-Key": key})


def test_phase_two_migration_has_product_snapshot_columns():
    columns = {item["name"] for item in inspect(engine).get_columns("sales_order_lines")}
    assert {"product_image_url", "product_url"} <= columns


def test_quantity_input_accepts_backend_thousandth_precision():
    source = Path("static/whatsapp_inbox.js").read_text()
    assert 'quantity.min = "0.001"' in source
    assert 'quantity.step = "0.001"' in source
    assert 'quantity.step = "1"' not in source
    assert 'quantity.oninput = () => { line.quantity = quantity.value; calculateOrderPreview(); }' in source


def test_inbox_create_order_drawer_is_feature_and_role_guarded(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    admin = client_for(); html = admin.get("/crm/whatsapp").text
    assert "＋ Create Order" in html and 'id="order-drawer"' in html
    staff = client_for("phase2-staff")
    assert "＋ Create Order" not in staff.get("/crm/whatsapp").text


def test_server_revalidates_shopify_snapshot_and_idempotently_saves(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    client = client_for(); first = create_order(client, monkeypatch)
    assert first.status_code == 201
    assert first.json()["order"]["grand_total"] == "16400.00"
    line = first.json()["order"]["lines"][0]
    assert line["product_name"] == "Pink Bike"
    assert line["unit_price"] == "16400.00"
    assert line["product_image_url"] == "https://cdn.example/pink-bike.jpg"
    # A retry succeeds from the local idempotency record even if Shopify is unavailable.
    monkeypatch.setattr(main, "resolve_inbox_product", lambda *_: (_ for _ in ()).throw(AssertionError()))
    conversation_id = SessionLocal().query(WhatsAppConversation).one().id
    token = client.get("/api/sales-orders/csrf").json()["csrf_token"]
    retry = client.post("/api/sales-orders", json=order_payload(conversation_id), headers={
        "X-CSRF-Token": token, "Idempotency-Key": "phase2-order"})
    assert retry.status_code == 200 and retry.json()["order"]["id"] == first.json()["order"]["id"]
    with SessionLocal() as db:
        assert db.query(SalesOrder).count() == 1


def test_dashboard_detail_and_authenticated_read_api(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", True)
    client = client_for(); created = create_order(client, monkeypatch, "dashboard-order").json()["order"]
    dashboard = client.get("/crm/sales-orders", params={"q": created["internal_order_number"]})
    assert dashboard.status_code == 200 and created["internal_order_number"] in dashboard.text
    detail = client.get(f"/crm/sales-orders/{created['id']}")
    assert detail.status_code == 200 and "Pink Bike" in detail.text and "Open WhatsApp" in detail.text
    listing = client.get("/api/sales-orders", params={"conversation_id": created["crm_conversation_id"]})
    assert listing.status_code == 200 and listing.json()["total"] == 1
    anonymous = TestClient(main.app, base_url="https://testserver")
    assert anonymous.get(f"/api/sales-orders/{created['id']}", follow_redirects=False).status_code == 303


def test_phase_two_pages_are_disabled_by_feature_flag(monkeypatch):
    monkeypatch.setattr(main.settings, "SALES_ORDERS_ENABLED", False)
    client = client_for()
    assert client.get("/crm/sales-orders").status_code == 404
    assert client.get("/api/sales-orders").status_code == 404
