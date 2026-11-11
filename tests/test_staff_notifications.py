"""Staff alerts use mocked push only; no customer sends or production configuration."""
import base64
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from models import User, WhatsAppConversation as C, WhatsAppConversationMessage as M, ConversationShare
from staff_notification_models import (StaffNotification as N, StaffPushDevice as D, StaffPushDelivery as Delivery,
    StaffNotificationControl as Control, StaffNotificationPreference as Pref)
import staff_notifications as svc
import conversation_access
import main
from test_whatsapp_quick_messages import inbox
from test_ad_assignment import routing, incoming, put


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


@pytest.fixture
def subscription():
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {'endpoint': 'https://fcm.googleapis.com/fcm/send/test-device', 'keys': {'p256dh': b64(public), 'auth': b64(b'a' * 16)}}


@pytest.fixture
def notifications(inbox, monkeypatch, subscription):
    monkeypatch.setenv('STAFF_PUSH_ENABLED', 'true')
    monkeypatch.setenv('STAFF_PUSH_VAPID_PUBLIC_KEY', subscription['keys']['p256dh'])
    monkeypatch.setenv('STAFF_PUSH_VAPID_PRIVATE_KEY', 'test-private-not-a-live-key')
    monkeypatch.setenv('STAFF_PUSH_VAPID_SUBJECT', 'mailto:admin@example.com')
    calls = []
    monkeypatch.setattr(svc, 'transport', lambda sub, payload: calls.append((sub, payload)) or 201)
    return inbox, calls, subscription


def user_id(sessions, name='quick-staff'):
    with sessions() as db:
        return db.query(User.id).filter_by(username=name).scalar()


def device(sessions, subscription, uid, suffix='1', version=0):
    sub = {**subscription, 'endpoint': subscription['endpoint'] + suffix}
    with sessions() as db:
        row = D(user_id=uid, browser_hash=svc.digest('browser' + suffix), endpoint_hash=svc.digest(sub['endpoint']),
            subscription_encrypted=svc.cipher().encrypt(json.dumps(sub).encode()).decode(), label='Test Android',
            session_version=version, vapid_key_hash=svc.digest(svc.push_config()['public_key']), active=True)
        db.add(row); db.commit(); return row.id


def inbound(sessions, cid, text='Can you send bicycle prices?', minutes=0, live=True):
    with sessions() as db:
        c = db.get(C, cid)
        m = M(phone_number=c.phone_number, message_text=text, direction='inbound', created_at=datetime.utcnow() - timedelta(minutes=minutes))
        db.add(m); db.flush(); svc.inbound(db, c, m, live=live); db.commit(); return m.id


def test_message_multiple_devices_encrypted_private_payload_and_previews(notifications):
    f, calls, sub = notifications; client, sessions, ids, _, login, _ = f; uid = user_id(sessions)
    device(sessions, sub, uid); device(sessions, sub, uid, '2')
    mid = inbound(sessions, ids[0], 'hello https://private.example/?access_token=secret')
    with sessions() as db:
        n = db.query(N).one(); assert n.kind == 'MESSAGE'
        item = svc.serialize(db, n, db.get(User, uid))
        assert 'private.example' not in item['body'] and 'access_token' not in item['body']
        m = db.get(M, mid); m.media_id = 'private-media'; m.media_mime_type = 'audio/ogg'; db.commit()
        assert svc.preview(m) == 'Voice message'
        assert db.query(D).first().subscription_encrypted.find('fcm.googleapis') == -1
    svc.prepare(sessions)
    assert svc.dispatch_one(sessions) and svc.dispatch_one(sessions) and not svc.dispatch_one(sessions)
    assert len(calls) == 2 and all(set(payload) == {'id'} for _, payload in calls)
    assert not f[3]  # No WhatsApp customer sends.


def test_signed_webhook_retry_emits_one_message_and_ad_assignment(routing):
    f = routing; assert put(f).status_code == 200
    assert incoming(f,timestamp=int(datetime.utcnow().timestamp())).status_code == 200 and incoming(f,timestamp=int(datetime.utcnow().timestamp())).status_code == 200
    with f[1]() as db:
        assert db.query(N).filter_by(kind='MESSAGE').count() == 1
        assert db.query(N).filter_by(kind='ASSIGNMENT').count() == 1
        assert {n.user_id for n in db.query(N)} == {f[6]['quick-staff']}


def test_assignment_reassignment_noop_self_and_permissions(notifications):
    f, calls, sub = notifications; client, sessions, ids, _, login, csrf = f
    uid = user_id(sessions, 'quick-agent'); device(sessions, sub, uid)
    path = f'/crm/whatsapp/api/conversations/{ids[0]}/assignment'
    assert client.post(path, json={'owner_id': uid, 'expected_version': 0}, headers={'X-CSRF-Token': csrf}).status_code == 200
    with sessions() as db:
        assert db.query(N).count() == 1
        n = db.query(N).one(); assert n.kind == 'ASSIGNMENT' and n.user_id == uid
    assert client.post(path, json={'owner_id': uid, 'expected_version': 1}, headers={'X-CSRF-Token': csrf}).status_code == 200
    with sessions() as db: assert db.query(N).count() == 1
    admin = user_id(sessions, 'quick-admin')
    assert client.post(path, json={'owner_id': admin, 'expected_version': 1}, headers={'X-CSRF-Token': csrf}).status_code == 200
    svc.prepare(sessions); assert not svc.dispatch_one(sessions) and not calls
    login('quick-staff')
    assert client.post(path, json={'owner_id': uid, 'expected_version': 2}, headers={'X-CSRF-Token': csrf}).status_code in (403,404)
    with sessions() as db: assert db.query(N).count() == 1


def test_unassigned_team_never_grants_conversation_access(notifications):
    f, _, _ = notifications; sessions = f[1]; uid = user_id(sessions); admin = user_id(sessions, 'quick-admin')
    with sessions() as db:
        c = db.get(C, f[2][0]); c.assigned_to_user_id = None
        cfg = db.get(Control, 1); cfg.shared_users = [uid, admin]; db.commit()
    inbound(sessions, f[2][0])
    with sessions() as db:
        assert [n.user_id for n in db.query(N)] == [admin]
        db.add(ConversationShare(conversation_id=f[2][0], user_id=uid, can_reply=False, granted_by=admin)); db.commit()
    inbound(sessions, f[2][0])
    with sessions() as db: assert db.query(N).filter_by(user_id=uid).count() == 1


@pytest.mark.parametrize('live,minutes', [(False, 0), (False, 60)])
def test_historical_imports_do_not_alert(notifications, live, minutes):
    f, _, _ = notifications; inbound(f[1], f[2][0], live=live, minutes=minutes)
    with f[1]() as db: assert db.query(N).count() == 0


def test_enqueue_failure_does_not_roll_back_business_message(notifications, monkeypatch):
    f, _, _ = notifications
    monkeypatch.setattr(svc, 'enqueue', lambda *a, **k: (_ for _ in ()).throw(SQLAlchemyError('private token')))
    mid = inbound(f[1], f[2][0])
    with f[1]() as db: assert db.get(M, mid) is not None and not db.query(N).first()


@pytest.mark.parametrize('code,status,active', [(410, 'EXPIRED', False), (404, 'EXPIRED', False), (403, 'FAILED', True), (503, 'PENDING', True)])
def test_provider_outcomes_cleanup_and_backoff(notifications, monkeypatch, code, status, active):
    f, _, sub = notifications; sessions = f[1]; uid = user_id(sessions); device(sessions, sub, uid)
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    monkeypatch.setattr(svc, 'transport', lambda *a: code)
    assert svc.dispatch_one(sessions)
    with sessions() as db:
        delivery = db.query(Delivery).one(); assert delivery.status == status and delivery.attempts == 1
        assert db.query(D).one().active is active
        if status == 'PENDING': assert delivery.next_attempt_at > datetime.utcnow()
        if not active: assert db.query(D).one().subscription_encrypted == ''


def test_retry_limit_crash_lease_and_concurrent_workers(notifications, monkeypatch):
    f, calls, sub = notifications; sessions = f[1]; uid = user_id(sessions); device(sessions, sub, uid)
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    with sessions() as db:
        delivery = db.query(Delivery).one(); delivery.status = 'SENDING'; delivery.attempts = 4
        delivery.lease_until = datetime.utcnow() - timedelta(seconds=1); db.commit()
    monkeypatch.setattr(svc, 'transport', lambda *a: 503)
    assert svc.dispatch_one(sessions)
    with sessions() as db: assert db.query(Delivery).one().status == 'FAILED' and db.query(Delivery).one().attempts == 5
    mid = inbound(sessions, f[2][0]); svc.prepare(sessions)
    monkeypatch.setattr(svc, 'transport', lambda sub, payload: calls.append(payload) or 201)
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: svc.dispatch_one(sessions), [1, 2]))
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['revoke', 'session', 'owner', 'inactive', 'permission', 'preference'])
def test_pending_alerts_respect_current_account_and_device_state(notifications, change):
    f, calls, sub = notifications; sessions = f[1]; uid = user_id(sessions); did = device(sessions, sub, uid)
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    with sessions() as db:
        user = db.get(User, uid)
        if change == 'revoke': db.get(D, did).active = False
        if change == 'session': user.session_version += 1
        if change == 'owner': db.get(C, f[2][0]).assigned_to_user_id = user_id(sessions, 'quick-agent')
        if change == 'inactive': user.is_active = False
        if change == 'permission': user.section_access = ['crm']
        if change == 'preference': db.add(Pref(user_id=uid, messages=False))
        db.commit()
    assert svc.dispatch_one(sessions) and not calls


def test_api_devices_csrf_logout_and_click_permission(notifications):
    f, calls, sub = notifications; client, sessions, ids, _, login, _ = f
    csrf = login('quick-staff'); data = client.get('/crm/notifications/api').json(); csrf = data['csrf_token']
    assert client.post('/crm/notifications/api/devices', json={'subscription': sub}).status_code == 403
    response = client.post('/crm/notifications/api/devices', json={'subscription': sub, 'label': 'Android'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200; did = response.json()['id']
    mid = inbound(sessions, ids[0])
    with sessions() as db: nid = db.query(N.id).filter_by(message_id=mid).scalar()
    response = client.get(f'/crm/notifications/api/push/{nid}')
    assert response.status_code == 200 and response.json()['url'] == f'/crm/whatsapp?conversation={ids[0]}'
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'private' not in json.dumps(client.get('/crm/notifications/api').json())
    assert client.post('/crm/notifications/api/test', json={}, headers={'X-CSRF-Token': csrf}).status_code == 200
    assert client.post('/crm/notifications/api/test', json={}, headers={'X-CSRF-Token': csrf}).status_code == 429
    client.get('/logout')
    with sessions() as db: assert not db.get(D, did).active and not db.get(D, did).subscription_encrypted
    assert client.get(f'/crm/notifications/api/push/{nid}', follow_redirects=False).status_code == 303
    login('quick-agent')
    assert client.get(f'/crm/notifications/api/push/{nid}').status_code == 404
    assert client.post(f'/crm/notifications/api/{nid}/read', json={}, headers={'X-CSRF-Token': client.get('/crm/notifications/api').json()['csrf_token']}).status_code == 404


@pytest.mark.parametrize('endpoint', ['http://fcm.googleapis.com/a', 'https://127.0.0.1/a', 'https://fcm.googleapis.com.evil/a', 'https://user:pass@fcm.googleapis.com/a', 'https://fcm.googleapis.com:8080/a'])
def test_subscription_ssrf_allowlist(subscription, endpoint):
    with pytest.raises(HTTPException): svc.validate_subscription({**subscription, 'endpoint': endpoint})


def test_reminder_escalation_once_reassign_and_reply_cancel(notifications):
    f, calls, sub = notifications; sessions = f[1]; cid = f[2][0]; uid = user_id(sessions); admin = user_id(sessions, 'quick-admin')
    mid = inbound(sessions, cid)
    with sessions() as db:
        cfg = db.get(Control, 1); assert not cfg.reminders_enabled and not cfg.escalations_enabled
        cfg.reminders_enabled = True; cfg.escalations_enabled = True; cfg.escalation_users = [admin]
        db.get(M, mid).created_at -= timedelta(minutes=16); db.commit()
        svc.scan_reminders(db); svc.scan_reminders(db); db.commit()
        assert db.query(N).filter_by(kind='REMINDER').count() == 1
        assert db.query(N).filter_by(kind='ESCALATION').count() == 1
        c = db.get(C, cid); c.assignment_version += 1; c.assigned_to_user_id = admin; db.commit()
        svc.scan_reminders(db); db.commit()
        assert db.query(N).filter_by(kind='REMINDER').count() == 2
        db.add(M(phone_number=c.phone_number, direction='outbound', message_text='Reply', send_status='sent')); db.commit()
        for n in db.query(N).filter(N.kind.in_(['REMINDER', 'ESCALATION'])):
            assert not svc.deliverable(db, n, db.get(User, n.user_id))


def test_controls_permissions_and_pwa_resources(notifications):
    f, _, _ = notifications; client, sessions, ids, _, login, csrf = f
    controls = client.get('/crm/notifications/api/control').json()['control']
    assert not controls['reminders_enabled'] and not controls['escalations_enabled']
    assert client.post('/crm/notifications/api/control', json={**controls, 'reminders_enabled': True}, headers={'X-CSRF-Token': csrf}).status_code == 200
    csrf = login('quick-staff')
    assert client.get('/crm/notifications/api/control').status_code == 403
    assert client.post('/crm/notifications/api/control', json=controls, headers={'X-CSRF-Token': csrf}).status_code == 403
    assert client.get('/crm/notifications').status_code == 200
    assert client.get('/crm/manifest.webmanifest').json()['scope'] == '/crm/'
    sw = client.get('/crm/staff-push-sw.js'); assert sw.headers['service-worker-allowed'] == '/crm/'
    assert 'caches.open' not in sw.text and 'clients.openWindow' in sw.text


def test_unassigned_escalation_without_message_recipients_and_resolution_cancel(notifications):
    f, _, _ = notifications; sessions = f[1]; cid = f[2][0]; admin = user_id(sessions, 'quick-admin')
    with sessions() as db:
        db.get(C, cid).assigned_to_user_id = None; db.commit()
    mid = inbound(sessions, cid)
    with sessions() as db:
        assert not db.query(N).first()
        cfg = db.get(Control, 1); cfg.escalations_enabled = True; cfg.escalation_users = [admin]
        db.get(M, mid).created_at -= timedelta(minutes=16); db.commit()
        svc.scan_reminders(db); db.commit()
        notice = db.query(N).one(); assert notice.kind == 'ESCALATION'
        from models import ConversationWorkState
        import conversation_work_service
        state = conversation_work_service.ensure(db, db.get(C, cid)); state.status = 'RESOLVED'; db.commit()
        assert not svc.deliverable(db, notice, db.get(User, admin))


def test_outer_rollback_cannot_leave_an_alert_without_the_message(notifications):
    f, _, _ = notifications; sessions = f[1]
    with sessions() as db:
        c = db.get(C, f[2][0]); m = M(phone_number=c.phone_number, direction='inbound', message_text='rollback')
        db.add(m); db.flush(); svc.inbound(db, c, m, live=True); db.rollback()
    with sessions() as db:
        assert not db.query(N).first() and not db.query(M).filter_by(message_text='rollback').first()


def test_disabled_push_still_retains_center_without_http(notifications, monkeypatch):
    f, calls, sub = notifications; uid = user_id(f[1]); device(f[1], sub, uid)
    monkeypatch.setenv('STAFF_PUSH_ENABLED', 'false')
    inbound(f[1], f[2][0]); svc.prepare(f[1]); assert not svc.dispatch_one(f[1]) and not calls
    with f[1]() as db: assert db.query(N).count() == 1


def test_worker_releases_database_transaction_before_network(notifications, monkeypatch):
    f, _, sub = notifications; sessions = f[1]; uid = user_id(sessions); device(sessions, sub, uid)
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    def send(*args):
        # A separate write must commit during HTTP, including on SQLite.
        with sessions() as db:
            db.get(C, f[2][0]).customer_name = 'Changed during push HTTP'; db.commit()
        return 201
    monkeypatch.setattr(svc, 'transport', send)
    assert svc.dispatch_one(sessions)
    with sessions() as db: assert db.query(Delivery).one().status == 'SENT'


def test_crash_on_final_attempt_does_not_create_sixth_http_request(notifications):
    f, calls, sub = notifications; sessions = f[1]; device(sessions, sub, user_id(sessions))
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    with sessions() as db:
        d = db.query(Delivery).one(); d.status = 'SENDING'; d.attempts = 5; d.lease_until = datetime.utcnow() - timedelta(seconds=1); db.commit()
    assert svc.dispatch_one(sessions) and not calls
    with sessions() as db: assert db.query(Delivery).one().status == 'FAILED' and db.query(Delivery).one().attempts == 5


def test_exact_unread_count_and_permissions_match_preview(notifications):
    f, _, _ = notifications; sessions = f[1]; uid = user_id(sessions)
    with sessions() as db:
        c = db.get(C, f[2][0])
        for n in range(105): svc.enqueue(db, c, [uid], 'MESSAGE', f'count:{n}')
        db.commit()
        q = svc.history_query(db, db.get(User, uid)); assert q.count() == 105
        assert all(svc.deliverable(db, n, db.get(User, uid)) for n in q)
    f[4]('quick-staff')
    state = f[0].get('/crm/notifications/api').json()
    assert state['unread'] == 105 and len(state['notices']) == 100


def test_real_webpush_encryption_and_vapid_without_network(subscription, monkeypatch):
    import requests
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv('STAFF_PUSH_VAPID_PRIVATE_KEY', b64(key.private_numbers().private_value.to_bytes(32, 'big')))
    monkeypatch.setenv('STAFF_PUSH_VAPID_PUBLIC_KEY', b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)))
    monkeypatch.setenv('STAFF_PUSH_VAPID_SUBJECT', 'mailto:test@example.com')
    recorded = []
    def request(self, method, url, **kwargs):
        recorded.append((method, url, kwargs)); response = requests.Response(); response.status_code = 201; return response
    monkeypatch.setattr(requests.Session, 'request', request)
    assert svc.transport(subscription, {'id': 42}) == 201
    method, url, kwargs = recorded[0]
    assert method.lower() == 'post' and url == subscription['endpoint']
    assert kwargs['allow_redirects'] is False and kwargs['timeout'] == 8
    assert str(kwargs['headers']['ttl']) == '300'
    assert kwargs['headers']['content-encoding'] == 'aes128gcm'
    assert b'"id": 42' not in kwargs['data']


def test_device_revoke_during_http_cannot_be_overwritten_by_transport_result(notifications, monkeypatch):
    f, _, sub = notifications; sessions = f[1]; uid = user_id(sessions); did = device(sessions, sub, uid)
    inbound(sessions, f[2][0]); svc.prepare(sessions)
    def send(*args):
        with sessions() as db:
            d = db.get(D, did); d.active = False; d.subscription_encrypted = ''; d.last_status = 'REVOKED'; db.commit()
        return 201
    monkeypatch.setattr(svc, 'transport', send); assert svc.dispatch_one(sessions)
    with sessions() as db:
        assert db.query(Delivery).one().status == 'CANCELED' and db.get(D, did).last_status == 'REVOKED'


def test_shared_team_removal_suppresses_pending_alert(notifications):
    f, _, _ = notifications; sessions = f[1]; admin = user_id(sessions, 'quick-admin')
    with sessions() as db:
        db.get(C, f[2][0]).assigned_to_user_id = None; db.get(Control, 1).shared_users = [admin]; db.commit()
    inbound(sessions, f[2][0])
    with sessions() as db:
        n = db.query(N).one(); assert svc.deliverable(db, n, db.get(User, admin))
        db.get(Control, 1).shared_users = []; db.commit()
        assert not svc.deliverable(db, n, db.get(User, admin))
        assert svc.history_query(db, db.get(User, admin)).count() == 0


def test_vapid_generator_writes_private_file_without_printing_secrets(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    target = tmp_path / 'staff-push.env'
    script = Path(__file__).resolve().parents[1] / 'scripts/generate_staff_push_keys.py'
    result = subprocess.run([sys.executable, str(script), '--output', str(target), '--subject', 'mailto:test@example.com'], capture_output=True, text=True, check=True)
    values = dict(line.split('=', 1) for line in target.read_text().splitlines())
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert values['STAFF_PUSH_ENABLED'] == 'false'
    assert values['STAFF_PUSH_VAPID_PRIVATE_KEY'] not in result.stdout
    assert len(base64.urlsafe_b64decode(values['STAFF_PUSH_VAPID_PRIVATE_KEY'] + '=')) == 32
    again = subprocess.run([sys.executable, str(script), '--output', str(target), '--subject', 'mailto:test@example.com'], capture_output=True)
    assert again.returncode != 0 and target.read_text().splitlines()[0] == 'STAFF_PUSH_ENABLED=false'


def test_delayed_live_webhook_message_still_alerts_when_first_persisted(notifications):
    f, _, _ = notifications
    inbound(f[1], f[2][0], minutes=60, live=True)
    with f[1]() as db: assert db.query(N).count() == 1
