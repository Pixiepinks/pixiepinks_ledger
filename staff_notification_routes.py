"""Authenticated notification preferences, devices and on-demand private previews."""
import json
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from models import User
from staff_notification_models import (StaffNotificationPreference as Preference, StaffNotificationControl as Control,
    StaffPushDevice as Device, StaffNotification as Notice)
import staff_notifications as service

ROOT = Path(__file__).resolve().parent
COOKIE = 'staff_push_browser'


def revoke_browser(db, request):
    browser = request.cookies.get(COOKIE)
    if browser:
        db.query(Device).filter_by(browser_hash=service.digest(browser)).update(
            {'active': False, 'subscription_encrypted': '', 'last_status': 'LOGGED_OUT'}, synchronize_session=False)
        db.commit()


def build_router(get_db, require_user, csrf, csrf_token, templates):
    router = APIRouter()

    def staff(user):
        if not service.eligible(user):
            raise HTTPException(403, 'WhatsApp staff access required')

    def admin(user):
        if not service.manager(user):
            raise HTTPException(403, 'Inbox manager access required')

    async def body(request):
        csrf(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise HTTPException(422, 'Provide valid JSON') from None

    @router.get('/crm/staff-push-sw.js')
    def worker():
        return FileResponse(ROOT / 'static/staff_push_sw.js', media_type='application/javascript',
            headers={'Cache-Control': 'no-store', 'Service-Worker-Allowed': '/crm/'})

    @router.get('/crm/manifest.webmanifest')
    def manifest():
        return Response(json.dumps({'id': '/crm/whatsapp', 'name': 'PixiePinks CRM', 'short_name': 'PixiePinks',
            'start_url': '/crm/whatsapp', 'scope': '/crm/', 'display': 'standalone', 'background_color': '#f6f7fb',
            'theme_color': '#bf1766', 'icons': [{'src': '/static/pwa-icon-192.png?v=pixiepinks-logo-1', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                {'src': '/static/pwa-icon-512.png?v=pixiepinks-logo-1', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                {'src': '/static/pwa-icon-maskable-512.png?v=pixiepinks-logo-1', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}]}),
            media_type='application/manifest+json', headers={'Cache-Control': 'public, max-age=3600'})

    @router.get('/crm/notifications')
    def page(request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user)
        return templates.TemplateResponse('staff_notifications.html', {'request': request, 'user': user,
            'page_title': 'Staff notifications', 'csrf_token': csrf_token(request), 'notification_manager': service.manager(user)},
            headers={'Cache-Control': 'private, no-store'})

    @router.get('/crm/notifications/api')
    def state(request: Request, response: Response, db=Depends(get_db), user=Depends(require_user)):
        staff(user); response.headers['Cache-Control'] = 'private, no-store'
        if not request.cookies.get(COOKIE):
            response.set_cookie(COOKIE, secrets.token_urlsafe(32), secure=True, httponly=True, samesite='strict', max_age=31536000)
        history = service.history_query(db, user)
        unread = history.filter(Notice.read_at.is_(None)).count()
        rows = history.order_by(Notice.id.desc()).limit(100).all()
        notices = [service.serialize(db, row, user, checked=True) for row in rows]
        devices = db.query(Device).filter_by(user_id=user.id).order_by(Device.updated_at.desc()).limit(50).all()
        return {'csrf_token': csrf_token(request), 'preferences': service.prefs(db, user.id), 'notices': notices,
            'unread': unread, 'history_limit': 100,
            'push_configured': service.configured(), 'public_key': service.push_config()['public_key'] if service.configured() else '',
            'user_id': user.id,
            'devices': [{'id': d.id, 'label': d.label, 'active': d.active and d.session_version == user.session_version and
                d.vapid_key_hash == service.digest(service.push_config()['public_key']),
                'current': d.browser_hash == service.digest(request.cookies.get(COOKIE, '')),
                'updated_at': d.updated_at.isoformat() + 'Z', 'status': d.last_status} for d in devices]}

    @router.post('/crm/notifications/api/preferences')
    async def preferences(request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user); data = await body(request)
        if not data or set(data) - {'messages', 'assignments', 'reminders', 'sound'} or any(type(v) is not bool for v in data.values()):
            raise HTTPException(422, 'Provide notification preferences as booleans')
        row = db.get(Preference, user.id)
        if not row:
            row = Preference(user_id=user.id); db.add(row)
        for key, value in data.items():
            setattr(row, key, value)
        db.commit()
        return {'preferences': service.prefs(db, user.id)}

    @router.post('/crm/notifications/api/devices')
    async def register(request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user); data = await body(request)
        if not service.configured():
            raise HTTPException(409, 'Server Web Push is not configured')
        browser = request.cookies.get(COOKIE)
        if not browser:
            raise HTTPException(409, 'Refresh notification settings before registering')
        # Serialize each account's device count/renewals across Railway workers.
        db.query(User).filter_by(id=user.id).with_for_update().one()
        subscription = service.validate_subscription(data.get('subscription'))
        hashed = service.digest(subscription['endpoint'])
        row = db.query(Device).filter_by(endpoint_hash=hashed).with_for_update().one_or_none()
        if row and (row.browser_hash != service.digest(browser) or (row.user_id != user.id and row.active)):
            raise HTTPException(409, 'Subscription is already registered. Disable it in the previous browser/account first.')
        if not row:
            if db.query(Device).filter_by(user_id=user.id, active=True).count() >= 10:
                raise HTTPException(409, 'Remove an old device before adding another (maximum 10)')
            row = Device(endpoint_hash=hashed)
        # One active subscription per browser. Account changes cannot retain old-user pushes.
        db.query(Device).filter(Device.browser_hash == service.digest(browser), Device.endpoint_hash != hashed).update(
            {'active': False, 'subscription_encrypted': ''}, synchronize_session=False)
        row.user_id = user.id; row.browser_hash = service.digest(browser)
        row.subscription_encrypted = service.cipher().encrypt(json.dumps(subscription).encode()).decode()
        row.label = service.safe_text(data.get('label') if isinstance(data.get('label'), str) else 'Chrome device', 80)
        row.session_version = user.session_version; row.vapid_key_hash = service.digest(service.push_config()['public_key'])
        row.active = True; row.last_status = 'REGISTERED'; row.updated_at = datetime.utcnow()
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Device registration changed. Refresh and try again.') from None
        return {'id': row.id, 'active': True}

    @router.post('/crm/notifications/api/devices/{ident}/remove')
    async def remove(ident: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user); await body(request)
        row = db.query(Device).filter_by(id=ident, user_id=user.id).with_for_update().one_or_none()
        if not row:
            raise HTTPException(404, 'Device not found')
        row.active = False; row.subscription_encrypted = ''; row.last_status = 'REVOKED'; db.commit()
        return {'removed': True}

    @router.post('/crm/notifications/api/{ident}/read')
    async def read(ident: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user); await body(request)
        row = db.query(Notice).filter_by(id=ident, user_id=user.id).one_or_none()
        if not row or not service.deliverable(db, row, user):
            raise HTTPException(404, 'Notification unavailable')
        row.read_at = datetime.utcnow(); db.commit()
        return {'read': True}

    @router.get('/crm/notifications/api/push/{ident}')
    def private_preview(ident: int, request: Request, response: Response, db=Depends(get_db), user=Depends(require_user)):
        staff(user); response.headers['Cache-Control'] = 'private, no-store'
        browser = request.cookies.get(COOKIE)
        device = db.query(Device.id).filter_by(user_id=user.id, active=True, browser_hash=service.digest(browser or ''),
            session_version=user.session_version, vapid_key_hash=service.digest(service.push_config()['public_key'])).first()
        row = db.query(Notice).filter_by(id=ident, user_id=user.id).one_or_none()
        item = service.serialize(db, row, user) if row and device and service.configured() else None
        if not item:
            raise HTTPException(404, 'Notification unavailable')
        item['csrf_token'] = csrf_token(request)
        return item

    @router.post('/crm/notifications/api/test')
    async def test(request: Request, db=Depends(get_db), user=Depends(require_user)):
        staff(user); await body(request)
        if not service.configured():
            raise HTTPException(409, 'Server Web Push is not configured')
        if db.query(Notice.id).filter(Notice.user_id == user.id, Notice.kind == 'TEST',
            Notice.created_at > datetime.utcnow() - timedelta(minutes=1)).first():
            raise HTTPException(429, 'Wait one minute between test alerts')
        if not db.query(Device.id).filter_by(user_id=user.id, active=True, session_version=user.session_version).first():
            raise HTTPException(409, 'Enable this device first')
        row = Notice(user_id=user.id, kind='TEST', event_key='test:' + secrets.token_hex(16)); db.add(row); db.commit()
        return {'queued': True}

    @router.get('/crm/notifications/api/control')
    def get_control(response: Response, db=Depends(get_db), user=Depends(require_user)):
        admin(user); response.headers['Cache-Control'] = 'private, no-store'
        row = db.get(Control, 1)
        return {'control': {k: getattr(row, k) for k in ('shared_users', 'escalation_users', 'reminders_enabled',
            'escalations_enabled', 'reminder_minutes', 'escalation_minutes')},
            'staff': [{'id': u.id, 'name': u.username, 'manager': service.manager(u)} for u in db.query(User) if service.eligible(u)]}

    @router.post('/crm/notifications/api/control')
    async def set_control(request: Request, db=Depends(get_db), user=Depends(require_user)):
        admin(user); data = await body(request)
        if set(data) != {'shared_users', 'escalation_users', 'reminders_enabled', 'escalations_enabled', 'reminder_minutes', 'escalation_minutes'}:
            raise HTTPException(422, 'Provide all notification controls')
        if any(type(data[k]) is not bool for k in ('reminders_enabled', 'escalations_enabled')) or any(
            type(data[k]) is not int or not 1 <= data[k] <= 1440 for k in ('reminder_minutes', 'escalation_minutes')) or data['escalation_minutes'] <= data['reminder_minutes']:
            raise HTTPException(422, 'Use whole minutes from 1–1440; escalation must follow the reminder')
        for key in ('shared_users', 'escalation_users'):
            if not isinstance(data[key], list) or len(data[key]) > 100 or any(type(uid) is not int for uid in data[key]):
                raise HTTPException(422, 'Choose active staff recipients')
            for uid in data[key]:
                target = db.get(User, uid)
                if not service.eligible(target) or (key == 'escalation_users' and not service.manager(target)):
                    raise HTTPException(422, 'Choose active staff, and managers for escalations')
        row = db.query(Control).filter_by(id=1).with_for_update().one()
        for key, value in data.items():
            setattr(row, key, value)
        row.updated_by = user.id; row.updated_at = datetime.utcnow(); db.commit()
        return {'saved': True}

    return router
