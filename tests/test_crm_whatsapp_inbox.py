from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from database import Base, SessionLocal, engine
from models import User, WhatsAppConversation, WhatsAppConversationMessage, WhatsAppManualSend
from utils_auth import hash_password


def setup_function():
    Base.metadata.create_all(bind=engine)
    main.ensure_whatsapp_inbox_columns()
    with SessionLocal() as db:
        db.query(WhatsAppManualSend).delete()
        db.query(WhatsAppConversationMessage).delete()
        db.query(WhatsAppConversation).delete()
        if not db.query(User).filter_by(username="inbox-test").first():
            db.add(User(username="inbox-test", password_hash=hash_password("password")))
        db.commit()


def authenticated_client():
    client = TestClient(main.app, base_url="https://testserver")
    response = client.post("/login", data={"username": "inbox-test", "password": "password", "next": "/crm/whatsapp"})
    assert response.status_code == 200
    return client


def test_inbox_requires_authentication_and_renders_for_staff():
    anonymous = TestClient(main.app)
    assert anonymous.get("/crm/whatsapp", follow_redirects=False).status_code == 303
    assert authenticated_client().get("/crm/whatsapp").status_code == 200


def test_conversation_order_search_unread_and_selected_read():
    now = datetime.utcnow()
    with SessionLocal() as db:
        older = WhatsAppConversation(phone_number="94770000001", customer_name="Nimali", unread_count=2,
                                     last_message_at=now - timedelta(hours=1))
        newer = WhatsAppConversation(phone_number="94770000002", customer_name="Amali", unread_count=1,
                                     last_message_at=now)
        db.add_all([older, newer])
        db.add_all([WhatsAppConversationMessage(phone_number="94770000001", direction="inbound", message_text="Old"),
                    WhatsAppConversationMessage(phone_number="94770000002", direction="inbound", message_text="New")])
        db.commit(); older_id, newer_id = older.id, newer.id
    client = authenticated_client()
    rows = client.get("/crm/whatsapp/api/conversations").json()["conversations"]
    assert [row["id"] for row in rows] == [newer_id, older_id]
    assert client.get("/crm/whatsapp/api/conversations", params={"search": "Nimali"}).json()["conversations"][0]["id"] == older_id
    assert client.get("/crm/whatsapp/api/conversations", params={"search": "000002"}).json()["conversations"][0]["id"] == newer_id
    assert client.post(f"/crm/whatsapp/api/conversations/{older_id}/read").json() == {"ok": True}
    with SessionLocal() as db:
        assert db.get(WhatsAppConversation, older_id).unread_count == 0
        assert db.get(WhatsAppConversation, newer_id).unread_count == 1


def test_takeover_return_and_manual_send_idempotency(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000003", customer_name="Kumari")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    client = authenticated_client()
    assert client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/takeover").json()["mode"] == "HUMAN"
    assert client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/return-to-ai").json()["mode"] == "AI"
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda phone, text, **kwargs: sent.append((phone, text)) or "wamid.manual")
    headers = {"Idempotency-Key": "same-staff-action"}
    first = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send", json={"message": "Payment details"}, headers=headers)
    second = client.post(f"/crm/whatsapp/api/conversations/{conversation_id}/send", json={"message": "Payment details"}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert sent == [("94770000003", "Payment details")]
    with SessionLocal() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff").one()
        assert (message.send_status, message.sent_by) == ("sent", "inbox-test")


def test_manual_send_failure_is_not_persisted_as_success(monkeypatch):
    with SessionLocal() as db:
        conversation = WhatsAppConversation(phone_number="94770000004")
        db.add(conversation); db.commit(); conversation_id = conversation.id
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args, **kwargs: False)
    response = authenticated_client().post(f"/crm/whatsapp/api/conversations/{conversation_id}/send",
        json={"message": "Hello"}, headers={"Idempotency-Key": "failed-action"})
    assert response.status_code == 502
    with SessionLocal() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(response_kind="staff").count() == 0
