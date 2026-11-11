"""Transactional staff-only outbox. Network delivery is exclusively a worker job."""
import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from sqlalchemy import or_, and_
from fastapi import HTTPException
from models import User, WhatsAppConversation as Conversation, WhatsAppConversationMessage as Message
from staff_notification_models import (StaffNotificationPreference as Preference, StaffNotificationControl as Control,
    StaffPushDevice as Device, StaffNotification as Notice, StaffPushDelivery as Delivery, StaffNotificationInbound as LiveInbound)
import conversation_access
from staff_access import allowed
from settings import settings

log = logging.getLogger(__name__)
KINDS = {'MESSAGE': 'messages', 'ASSIGNMENT': 'assignments', 'REMINDER': 'reminders', 'ESCALATION': 'reminders'}
TITLES = {'MESSAGE': 'New WhatsApp message', 'ASSIGNMENT': 'New conversation assigned',
          'REMINDER': 'Customer waiting for a reply', 'ESCALATION': 'Unanswered conversation escalation', 'TEST': 'Notification test'}
PUSH_HOSTS = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cipher():
    # Separate purpose-derived encryption; changing SECRET_KEY revokes existing devices.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('staff-push-v1:' + settings.SECRET_KEY).encode()).digest()))


def push_config():
    return {'enabled': os.getenv('STAFF_PUSH_ENABLED', 'false').lower() == 'true',
            'public_key': os.getenv('STAFF_PUSH_VAPID_PUBLIC_KEY', ''),
            'private_key': os.getenv('STAFF_PUSH_VAPID_PRIVATE_KEY', ''),
            'subject': os.getenv('STAFF_PUSH_VAPID_SUBJECT', '')}


def configured():
    c = push_config()
    return bool(c['enabled'] and c['public_key'] and c['private_key'] and
                (c['subject'].startswith('mailto:') or c['subject'].startswith('https://')))


def eligible(user):
    return bool(user and user.is_active and user.role in {'ADMIN', 'STAFF', 'SALES_AGENT', 'SALES_MANAGER'} and allowed(user, 'whatsapp'))


def manager(user):
    return eligible(user) and user.role in {'ADMIN', 'SALES_MANAGER'}


def prefs(db, uid):
    row = db.get(Preference, uid)
    return {k: getattr(row, k) if row else default for k, default in
            [('messages', True), ('assignments', True), ('reminders', True), ('sound', False)]}


def visible(db, user, c):
    return eligible(user) and bool(conversation_access.scope(db.query(Conversation.id), user).filter(Conversation.id == c.id).first())


def safe_text(value, limit=120):
    text = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', value or '')
    text = re.sub(r'(?i)(?:https?://|www\.)\S+|\S*(?:access_token|bearer|password|secret|token=)\S*', '[link or private value]', text)
    return ' '.join(text.split())[:limit]


def preview(message):
    if message.media_id or message.media_mime_type or message.response_kind in {'image', 'video', 'audio', 'document', 'sticker'}:
        mime = message.media_mime_type or ''
        return ('Voice message' if mime.startswith('audio/') else 'Image message' if mime.startswith('image/') else
                'Video message' if mime.startswith('video/') else 'Attachment received')
    if message.response_kind in {'consent_button', 'question_reply', 'question_list'}:
        return 'Customer selected a reply option'
    return safe_text(message.message_text) or 'New customer message'


def enqueue(db, c, users, kind, key, message_id=None, actor_id=None):
    from crm_analytics_tracking import insert_for
    now = datetime.utcnow()
    for uid in set(users):
        user = db.get(User, uid)
        if not visible(db, user, c) or not prefs(db, uid)[KINDS[kind]]:
            continue
        db.execute(insert_for(db.connection(), Notice.__table__).values(user_id=uid, conversation_id=c.id,
            kind=kind, event_key=key, message_id=message_id, actor_id=actor_id,
            assignment_version=c.assignment_version, created_at=now, expanded=False).on_conflict_do_nothing(index_elements=['user_id', 'event_key']))


def best_effort(db, callback):
    # A notification persistence problem cannot reject the customer's message/assignment.
    # Savepoint rollback leaves the outer business transaction intact; no HTTP here.
    try:
        with db.begin_nested():
            callback()
    except Exception:
        log.error('STAFF_NOTIFICATION_ENQUEUE_FAILED')  # No SQL/provider payloads or secrets.


def inbound(db, c, message, live=False):
    if not live or message.direction != 'inbound':
        return
    def create():
        from crm_analytics_tracking import insert_for
        db.execute(insert_for(db.connection(), LiveInbound.__table__).values(message_id=message.id,
            conversation_id=c.id, created_at=datetime.utcnow()).on_conflict_do_nothing(index_elements=['message_id']))
        control = db.get(Control, 1)
        users = [c.assigned_to_user_id] if c.assigned_to_user_id else (control.shared_users if control else [])
        enqueue(db, c, users, 'MESSAGE', f'message:{message.id}', message.id)
    best_effort(db, create)


def assignment(db, c, actor_id):
    if not c.assigned_to_user_id or c.assigned_to_user_id == actor_id:
        return
    best_effort(db, lambda: enqueue(db, c, [c.assigned_to_user_id], 'ASSIGNMENT',
        f'assignment:{c.id}:{c.assignment_version}', actor_id=actor_id))


def unanswered(db, c, message, now=None):
    import inbox_reply_queue
    pending, _ = inbox_reply_queue.summary(db)
    waiting = db.query(pending.c.waiting_since).filter(pending.c.phone == c.phone_number).scalar()
    return waiting is not None and waiting <= message.created_at


def deliverable(db, notice, user):
    if not eligible(user):
        return False
    if notice.kind == 'TEST':
        return True
    c = db.get(Conversation, notice.conversation_id)
    if not c or not visible(db, user, c) or not prefs(db, user.id)[KINDS[notice.kind]]:
        return False
    if notice.kind in {'MESSAGE', 'ASSIGNMENT', 'REMINDER'}:
        if c.assignment_version != notice.assignment_version:
            return False  # No previous assignee/shared-team alerts after reassignment.
        if c.assigned_to_user_id and c.assigned_to_user_id != user.id:
            return False
    if notice.kind == 'MESSAGE' and c.assigned_to_user_id is None:
        control = db.get(Control, 1)
        if not control or user.id not in control.shared_users:
            return False
    if notice.kind in {'REMINDER', 'ESCALATION'}:
        control = db.get(Control, 1)
        enabled = control and (control.reminders_enabled if notice.kind == 'REMINDER' else control.escalations_enabled)
        message = db.get(Message, notice.message_id)
        if not enabled or not message or not unanswered(db, c, message):
            return False
        if notice.kind == 'ESCALATION' and (not manager(user) or user.id not in control.escalation_users):
            return False
    return True


def history_query(db, user):
    """Same access checks as delivery, batched for history/counts instead of N+1 checks."""
    from sqlalchemy import select
    import inbox_reply_queue
    preference = prefs(db, user.id)
    query = db.query(Notice).outerjoin(Conversation, Conversation.id == Notice.conversation_id).filter(Notice.user_id == user.id)
    accessible = conversation_access.scope(db.query(Conversation.id), user).subquery()
    query = query.filter(or_(Notice.kind == 'TEST', and_(
        Conversation.id.in_(select(accessible.c.id)),
        or_(Notice.kind == 'ESCALATION', and_(Notice.assignment_version == Conversation.assignment_version,
            or_(Conversation.assigned_to_user_id.is_(None), Conversation.assigned_to_user_id == user.id))))))
    for kind, preference_key in KINDS.items():
        if not preference[preference_key]:
            query = query.filter(Notice.kind != kind)
    control = db.get(Control, 1)
    if not control or user.id not in control.shared_users:
        query = query.filter(or_(Notice.kind != 'MESSAGE', Conversation.assigned_to_user_id.isnot(None)))
    if not control or not control.reminders_enabled:
        query = query.filter(Notice.kind != 'REMINDER')
    if not control or not control.escalations_enabled or not manager(user) or user.id not in control.escalation_users:
        query = query.filter(Notice.kind != 'ESCALATION')
    pending, _ = inbox_reply_queue.summary(db)
    query = query.outerjoin(Message, Message.id == Notice.message_id).outerjoin(pending, pending.c.phone == Conversation.phone_number)
    return query.filter(or_(Notice.kind.notin_(['REMINDER', 'ESCALATION']),
        and_(pending.c.waiting_since.isnot(None), pending.c.waiting_since <= Message.created_at)))


def serialize(db, notice, user, *, checked=False):
    if not checked and not deliverable(db, notice, user):
        return None
    c = db.get(Conversation, notice.conversation_id) if notice.conversation_id else None
    body = safe_text(c.customer_name, 60) or 'Customer' if c else 'Your device is ready for staff alerts.'
    if notice.kind == 'MESSAGE':
        m = db.get(Message, notice.message_id)
        body += ': ' + (preview(m) if m else 'New customer message')
    elif notice.kind == 'ASSIGNMENT':
        actor = db.get(User, notice.actor_id) if notice.actor_id else None
        body += ' · Assigned by ' + safe_text(actor.username if actor else 'Administrator', 50)
    return {'id': notice.id, 'type': notice.kind, 'title': 'PixiePinks CRM — ' + TITLES[notice.kind],
            'body': body, 'customer': safe_text(c.customer_name, 60) if c else '',
            'url': f'/crm/whatsapp?conversation={c.id}' if c else '/crm/notifications',
            'created_at': notice.created_at.isoformat() + 'Z', 'read': bool(notice.read_at),
            'sound': prefs(db, user.id)['sound']}


def validate_subscription(data):
    if not isinstance(data, dict) or not isinstance(data.get('endpoint'), str) or not isinstance(data.get('keys'), dict):
        raise HTTPException(422, 'Invalid push subscription')
    endpoint = data['endpoint']
    try:
        parsed = urlsplit(endpoint)
        if (len(endpoint) > 2048 or parsed.scheme != 'https' or parsed.hostname not in PUSH_HOSTS or
            parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment or
            any(ord(ch) < 33 for ch in endpoint)):
            raise ValueError()
        keys = {}
        for key, size in [('p256dh', 65), ('auth', 16)]:
            value = data['keys'].get(key)
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}={0,2}', value):
                raise ValueError()
            raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            if len(raw) != size or (key == 'p256dh' and raw[0] != 4):
                raise ValueError()
            keys[key] = value
        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey, SECP256R1
        EllipticCurvePublicKey.from_encoded_point(SECP256R1(), base64.urlsafe_b64decode(keys['p256dh'] + '=' * (-len(keys['p256dh']) % 4)))
    except (ValueError, TypeError):
        raise HTTPException(422, 'Unsupported push endpoint or invalid encryption keys') from None
    return {'endpoint': endpoint, 'keys': keys}


def scan_reminders(db, now=None):
    now = now or datetime.utcnow()
    control = db.get(Control, 1)
    if not control or not (control.reminders_enabled or control.escalations_enabled):
        return
    import inbox_reply_queue
    pending, _ = inbox_reply_queue.summary(db)
    rows = db.query(Conversation, pending.c.waiting_since).join(pending, pending.c.phone == Conversation.phone_number).filter(
        pending.c.waiting_since <= now - timedelta(minutes=min(control.reminder_minutes, control.escalation_minutes))).all()
    for c, since in rows:
        # Only messages recorded after feature installation: no historical reminder blasts.
        message = db.query(Message).join(LiveInbound, LiveInbound.message_id == Message.id).filter(
            Message.phone_number == c.phone_number, Message.direction == 'inbound',
            Message.created_at >= since).order_by(Message.created_at, Message.id).first()
        if not message:
            continue
        since = max(since, message.created_at)
        if control.reminders_enabled and c.assigned_to_user_id and now - since >= timedelta(minutes=control.reminder_minutes):
            enqueue(db, c, [c.assigned_to_user_id], 'REMINDER',
                    f'reminder:{c.id}:{message.id}:{c.assignment_version}', message.id)
        if control.escalations_enabled and now - since >= timedelta(minutes=control.escalation_minutes):
            users = [uid for uid in control.escalation_users if manager(db.get(User, uid))]
            enqueue(db, c, users, 'ESCALATION', f'escalation:{c.id}:{message.id}', message.id)


def transport(subscription, data):
    from pywebpush import webpush
    import requests
    class NoRedirectSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(*args, **kwargs)
    c = push_config()
    with NoRedirectSession() as session:
        response = webpush(subscription_info=subscription, data=json.dumps(data),
            vapid_private_key=c['private_key'], vapid_claims={'sub': c['subject']},
            ttl=300, timeout=8, requests_session=session)
        return response.status_code


def dispatch_one(sessions):
    """Short durable claim + bounded HTTP attempt. Rechecks revocation and authorization."""
    if not configured():
        return False
    now = datetime.utcnow()
    with sessions() as db:
        # A crashed claim is retried with the same notification tag/id (at-least-once push).
        row = db.query(Delivery).filter(or_(
            (Delivery.status == 'PENDING') & (Delivery.next_attempt_at <= now),
            (Delivery.status == 'SENDING') & (Delivery.lease_until <= now))).order_by(Delivery.id).first()
        if not row:
            return False
        if row.attempts >= 5:
            db.query(Delivery).filter_by(id=row.id, status=row.status, attempts=row.attempts).update(
                {'status': 'FAILED', 'last_code': 'RETRY_LIMIT', 'lease_until': None}, synchronize_session=False)
            db.commit()
            return True
        token = now + timedelta(seconds=45)
        changed = db.query(Delivery).filter(Delivery.id == row.id, Delivery.status == row.status,
            Delivery.attempts == row.attempts).update({'status': 'SENDING', 'attempts': row.attempts + 1, 'lease_until': token}, synchronize_session=False)
        if not changed:
            db.rollback()
            return True
        ident = row.id
        db.commit()
    with sessions() as db:
        delivery = db.query(Delivery).filter_by(id=ident, status='SENDING', lease_until=token).with_for_update().one_or_none()
        if not delivery:
            return True
        device = db.query(Device).filter_by(id=delivery.device_id).with_for_update().one()
        notice = db.get(Notice, delivery.notification_id)
        user = db.get(User, device.user_id)
        if (notice.created_at < datetime.utcnow() - timedelta(minutes=5) or
            not device.active or not user or device.user_id != notice.user_id or
            device.session_version != user.session_version or device.vapid_key_hash != digest(push_config()['public_key']) or
            not deliverable(db, notice, user)):
            delivery.status = 'CANCELED'
            db.commit()
            return True
        try:
            subscription = json.loads(cipher().decrypt(device.subscription_encrypted.encode()))
            validate_subscription(subscription)
        except Exception:
            delivery.status = 'EXPIRED'; delivery.last_code = 'INVALID_SUBSCRIPTION'
            device.active = False; device.subscription_encrypted = ''; device.last_status = 'INVALID_SUBSCRIPTION'
            db.commit()
            return True
        # Never hold a DB transaction/lock across push-service HTTP. The payload is
        # opaque and its authenticated preview rechecks revocation/ownership later.
        notice_id = notice.id
        device_revision = (device.updated_at, device.user_id, device.session_version, device.subscription_encrypted)
        db.commit()
    try:
        code = transport(subscription, {'id': notice_id})
    except Exception as error:
        response = getattr(error, 'response', None)
        code = getattr(response, 'status_code', None)
    with sessions() as db:
        delivery = db.query(Delivery).filter_by(id=ident, status='SENDING', lease_until=token).with_for_update().one_or_none()
        if not delivery:
            return True
        device = db.get(Device, delivery.device_id)
        if not device.active or device_revision != (device.updated_at, device.user_id, device.session_version, device.subscription_encrypted):
            delivery.status = 'CANCELED'; delivery.last_code = 'DEVICE_CHANGED'; delivery.lease_until = None
            db.commit()
            return True
        if code is not None and 200 <= code < 300:
            delivery.status = 'SENT'; delivery.last_code = 'ACCEPTED'; device.last_status = 'ACCEPTED'
        elif code in (400, 404, 410):
            delivery.status = 'EXPIRED'; delivery.last_code = 'EXPIRED'; device.active = False; device.last_status = 'EXPIRED'
            device.subscription_encrypted = ''
        elif code is not None and 400 <= code < 500 and code not in (408, 429):
            delivery.status = 'FAILED'; delivery.last_code = 'PUSH_REJECTED'; device.last_status = 'PUSH_REJECTED'
        elif delivery.attempts >= 5:
            delivery.status = 'FAILED'; delivery.last_code = 'RETRY_LIMIT'; device.last_status = 'RETRY_LIMIT'
        else:
            delivery.status = 'PENDING'; delivery.last_code = 'TEMPORARY_FAILURE'
            delivery.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(900, 15 * 2 ** (delivery.attempts - 1)))
        delivery.lease_until = None
        db.commit()
    return True


def prepare(sessions):
    from crm_analytics_tracking import insert_for
    with sessions() as db:
        scan_reminders(db)
        # Claims use conditional UPDATE, so multiple Railway workers can safely fan out.
        for notice in db.query(Notice).filter_by(expanded=False).order_by(Notice.id).limit(100).all():
            changed = db.query(Notice).filter_by(id=notice.id, expanded=False).update({'expanded': True}, synchronize_session=False)
            if not changed:
                continue
            if notice.created_at < datetime.utcnow() - timedelta(minutes=5):
                continue  # Do not send stale lock-screen alerts after a long outage.
            if not deliverable(db, notice, db.get(User, notice.user_id)):
                continue
            for device in db.query(Device).filter_by(user_id=notice.user_id, active=True):
                db.execute(insert_for(db.connection(), Delivery.__table__).values(notification_id=notice.id,
                    device_id=device.id, status='PENDING', attempts=0, next_attempt_at=datetime.utcnow()).on_conflict_do_nothing(
                        index_elements=['notification_id', 'device_id']))
        db.commit()


def worker(sessions, stop):
    while not stop.is_set():
        try:
            prepare(sessions)
            for _ in range(25):
                if stop.is_set() or not dispatch_one(sessions):
                    break
        except Exception:
            log.error('STAFF_NOTIFICATION_WORKER_FAILED')
        stop.wait(10)
