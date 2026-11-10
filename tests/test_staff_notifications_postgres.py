"""Optional disposable-PostgreSQL validation. Uses unique schemas, no production DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from models import User, GlobalAIReplyControl, CustomerQuestionFlow, WhatsAppConversation as C, WhatsAppConversationMessage as M
from staff_notification_models import (StaffNotificationControl as Control, StaffNotificationPreference as Preference,
    StaffNotification as N, StaffPushDevice as D, StaffPushDelivery as Delivery, StaffNotificationInbound as Live)
from test_ad_flows_postgres_review import postgres_review
from test_staff_notifications import subscription, device
import staff_notifications as svc


def migrate(engine):
    for model in (Delivery, N, Live, D, Preference, Control): model.__table__.drop(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.stamp(config, '20261110_38'); command.upgrade(config, 'head'); command.upgrade(config, 'head')


def test_postgres_additive_repeatable_migration_and_off_defaults(postgres_review):
    engine, sessions = postgres_review; migrate(engine)
    with sessions() as db:
        control = db.get(Control, 1)
        assert not control.reminders_enabled and not control.escalations_enabled
        assert control.reminder_minutes == 5 and control.escalation_minutes == 15
        assert not control.shared_users and not control.escalation_users
        assert db.query(C).one().customer_name == 'Preserved thread'
        assert db.query(CustomerQuestionFlow).count() == 1
        assert db.get(GlobalAIReplyControl, 1).enabled is False


def test_postgres_concurrent_enqueue_fanout_and_worker_claim_once(postgres_review, subscription, monkeypatch):
    engine, sessions = postgres_review; migrate(engine)
    monkeypatch.setenv('STAFF_PUSH_ENABLED', 'true'); monkeypatch.setenv('STAFF_PUSH_VAPID_PUBLIC_KEY', subscription['keys']['p256dh'])
    monkeypatch.setenv('STAFF_PUSH_VAPID_PRIVATE_KEY', 'test-key'); monkeypatch.setenv('STAFF_PUSH_VAPID_SUBJECT', 'mailto:test@example.com')
    calls = []; monkeypatch.setattr(svc, 'transport', lambda sub, data: calls.append(data) or 201)
    with sessions() as db:
        uid = db.query(User.id).scalar(); c = db.query(C).one(); cid = c.id; c.assigned_to_user_id = uid
        m = M(phone_number=c.phone_number, direction='inbound', message_text='Hi', created_at=datetime.utcnow()); db.add(m); db.commit(); mid = m.id
    device(sessions, subscription, uid)
    def enqueue(_):
        with sessions() as db:
            svc.enqueue(db, db.get(C, cid), [uid], 'MESSAGE', 'one-inbound', mid); db.commit()
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(enqueue, range(4)))
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: svc.prepare(sessions), range(2)))
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: svc.dispatch_one(sessions), range(2)))
    with sessions() as db:
        assert db.query(N).count() == 1 and db.query(Delivery).count() == 1
        assert db.query(Delivery).one().status == 'SENT'
    assert len(calls) == 1
