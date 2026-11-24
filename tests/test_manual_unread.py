"""Internal markers stay separate from automatic customer-message state."""
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import os
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.orm import sessionmaker
from models import User,WhatsAppConversation,ConversationShare,ConversationManualUnread as Mark,ConversationManualUnreadAudit as Audit,StaffAccessAudit
from test_whatsapp_quick_messages import inbox
from test_conversation_assignment import assignments
from test_inbox_collaboration import token
import main,database,manual_unread

BASE='/crm/whatsapp/api/conversations'

def headers(client):return {'X-CSRF-Token':token(client)}
def mark(client,cid):return client.post(f'{BASE}/{cid}/manual-unread',headers=headers(client))
def clear(client,cid,uid):return client.delete(f'{BASE}/{cid}/manual-unread/{uid}',headers=headers(client))


def test_independent_persistent_marks_and_read_counts(assignments):
    v=assignments;cid=v.ids[0]
    with v.sessions() as db:
        db.add(ConversationShare(conversation_id=cid,user_id=v.users['quick-agent'],can_reply=False,granted_by=v.users['quick-admin']));db.commit()
    from staff_notification_models import StaffNotification,StaffPushDelivery
    with v.sessions() as db:notifications=(db.query(StaffNotification).count(),db.query(StaffPushDelivery).count())
    assert mark(v.staff,cid).status_code==200
    assert mark(v.staff,cid).status_code==200
    assert mark(v.other,cid).status_code==200
    assert v.other.post(f'{BASE}/{cid}/takeover').status_code==403
    data=v.staff.get(BASE).json();assert data['unread_total']==2
    assert {m['staff_id'] for m in data['conversations'][0]['manual_unread']}=={v.users['quick-staff'],v.users['quick-agent']}
    assert v.staff.post(f'{BASE}/{cid}/read').status_code==200
    assert v.staff.get(BASE).json()['unread_total']==0
    assert len(v.staff.get(f'{BASE}/{cid}').json()['conversation']['manual_unread'])==2
    assert clear(v.staff,cid,v.users['quick-agent']).status_code==403
    assert clear(v.staff,cid,v.users['quick-staff']).status_code==200
    assert clear(v.staff,cid,v.users['quick-staff']).status_code==200
    assert len(v.other.get(f'{BASE}/{cid}').json()['conversation']['manual_unread'])==1
    assert clear(v.admin,cid,v.users['quick-agent']).status_code==200
    with v.sessions() as db:assert db.query(Mark).count()==0 and db.query(Audit).count()==4
    with v.sessions() as db:assert notifications==(db.query(StaffNotification).count(),db.query(StaffPushDelivery).count())
    assert not v.sends


def test_filter_pagination_and_visibility(assignments):
    v=assignments
    for cid in [*v.ids,v.third]:assert mark(v.admin,cid).status_code==200
    one=v.admin.get(BASE+'?marked_unread=true&limit=1').json();two=v.admin.get(BASE+'?marked_unread=true&limit=1&offset=1').json()
    assert one['has_more'] and one['conversations'][0]['id']!=two['conversations'][0]['id']
    assert len(v.staff.get(BASE+'?marked_unread=true&mailbox=mine').json()['conversations'])==1
    assert mark(v.staff,v.ids[1]).status_code==404
    assert clear(v.staff,v.ids[1],v.users['quick-admin']).status_code==404
    assert len(v.admin.get(BASE+'?marked_unread=true&mailbox=unassigned').json()['conversations'])==1
    assert v.admin.get(BASE).json()['unread_total']==13
    staff_headers=headers(v.staff)
    with v.sessions() as db:
        staff=db.get(User,v.users['quick-staff']);staff.section_access=[];db.commit()
    assert v.staff.post(f'{BASE}/{v.ids[0]}/manual-unread',headers=staff_headers).status_code==403
    with v.sessions() as db:
        db.get(User,v.users['quick-staff']).section_access=['whatsapp'];db.commit()
    assert v.staff.post(f'{BASE}/{v.ids[0]}/manual-unread').status_code==403
    from fastapi.testclient import TestClient
    with TestClient(main.app,base_url='https://testserver') as client:
        assert client.post(f'{BASE}/{v.ids[0]}/manual-unread',follow_redirects=False).status_code==303


def test_incoming_and_logout_leave_mark_intact(inbox):
    client,sessions,ids,sends,login,csrf=inbox;cid=ids[0]
    assert mark(client,cid).status_code==200
    with sessions() as db:phone=db.get(WhatsAppConversation,cid).phone_number
    main._store_conversation_message(phone,'inbound','Synthetic incoming',whatsapp_message_id='manual-marker-fixture')
    assert client.get('/logout',follow_redirects=False).status_code==303
    assert client.get(BASE,follow_redirects=False).status_code==303
    login()
    data=client.get(f'{BASE}/{cid}').json()['conversation']
    assert data['unread_count']==3 and len(data['manual_unread'])==1
    assert not sends


def test_colour_administration_and_palette(assignments):
    v=assignments;uid=v.users['quick-staff'];cid=v.ids[0];mark(v.staff,cid)
    page=v.admin.get('/admin/staff');csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    with v.sessions() as db:previous=db.get(User,uid).unread_indicator_colour
    payload={'colour':'pink','expected_colour':previous,'csrf_token':csrf}
    assert v.admin.post(f'/admin/staff/{uid}/unread-colour',data=payload).status_code==200
    m=v.staff.get(BASE).json()['conversations'][0]['manual_unread'][0];assert m['colour']=='pink' and m['name']=='quick-staff'
    assert v.staff.post(f'/admin/staff/{uid}/unread-colour',data=payload).status_code==403
    assert v.admin.post(f'/admin/staff/{uid}/unread-colour',data={**payload,'colour':'red'}).status_code==422
    if previous!='pink':assert v.admin.post(f'/admin/staff/{uid}/unread-colour',data=payload).status_code==409
    assert v.admin.post(f'/admin/staff/{uid}/unread-colour',data={**payload,'expected_colour':'pink','csrf_token':'bad'}).status_code==403
    with v.sessions() as db:assert db.query(StaffAccessAudit).filter_by(action='UNREAD_COLOUR_CHANGED').count()==1


def test_postgresql_upgrade_concurrency_and_rollback(monkeypatch):
    url=os.environ.get('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Disposable PostgreSQL not configured')
    admin=create_engine(url);assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='manual_'+uuid4().hex
    with admin.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema});monkeypatch.setattr(database,'engine',engine)
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'))
    try:
        # Start with a genuine legacy schema, without the new field or tables.
        with engine.begin() as c:
            c.execute(text("CREATE TABLE users(id SERIAL PRIMARY KEY,username VARCHAR(100) NOT NULL,password_hash VARCHAR(255) NOT NULL,role VARCHAR(30) NOT NULL,section_access JSON,is_active BOOLEAN NOT NULL DEFAULT true,session_version INTEGER NOT NULL DEFAULT 0)"))
            c.execute(text("CREATE TABLE whatsapp_conversations(id SERIAL PRIMARY KEY,phone_number VARCHAR(50) NOT NULL,unread_count INTEGER NOT NULL DEFAULT 0,assigned_to_user_id INTEGER)"))
            c.execute(text("INSERT INTO users(username,password_hash,role) VALUES ('admin','PRESERVED','ADMIN'),('staff','PRESERVED','STAFF')"))
            c.execute(text("INSERT INTO whatsapp_conversations(phone_number,unread_count) VALUES ('94770000001',4)"))
        # This minimal legacy fixture tests revision 54 only; later migrations
        # require the full pre-existing commerce schema (covered in their suites).
        command.stamp(cfg,'20261122_53');command.upgrade(cfg,'20261123_54');command.upgrade(cfg,'20261123_54')
        # Add the remaining mapped conversation fields to exercise service locks.
        with engine.begin() as c:
            for column in WhatsAppConversation.__table__.columns:
                if column.name not in {v['name'] for v in inspect(c).get_columns('whatsapp_conversations')}:
                    from sqlalchemy.schema import CreateColumn
                    # Nullable additions only: synthetic legacy fixture, no application migration.
                    copied=column._copy();copied.nullable=True;c.execute(text('ALTER TABLE whatsapp_conversations ADD COLUMN '+str(CreateColumn(copied).compile(dialect=engine.dialect))))
        sessions=sessionmaker(bind=engine)
        def action(_):
            with sessions() as db:return manual_unread.change(db,db.get(User,1),1,1,True)
        with ThreadPoolExecutor(4) as pool:list(pool.map(action,range(8)))
        with sessions() as db:
            assert db.query(Mark).count()==1 and db.query(Audit).count()==1
            assert db.get(User,2).unread_indicator_colour=='purple'
            assert db.get(WhatsAppConversation,1).unread_count==4
        command.downgrade(cfg,'20261122_53')
        with engine.connect() as c:
            assert 'conversation_manual_unread' not in inspect(c).get_table_names()
            assert 'unread_indicator_colour' not in {v['name'] for v in inspect(c).get_columns('users')}
            assert c.execute(text('SELECT unread_count FROM whatsapp_conversations')).scalar()==4
            assert c.execute(text('SELECT password_hash FROM users WHERE id=1')).scalar()=='PRESERVED'
    finally:
        engine.dispose()
        with admin.begin() as c:c.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()


@pytest.mark.parametrize('width,role',[(1280,'admin'),(390,'staff')])
def test_browser_mark_refresh_header_clear_and_filter(assignments,width,role):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright,expect
    v=assignments;cid=v.ids[0];client=v.admin if role=='admin' else v.staff;other=v.staff if role=='admin' else v.admin
    assert mark(other,cid).status_code==200
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':width,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def serve(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='unread.test':route.fulfill(status=200,body='');return
            response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token'}})
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k in {'content-type','cache-control'}})
        page.route('**/*',serve)
        try:
            page.goto('https://unread.test/crm/whatsapp');row=page.locator(f'.conversation-entry[data-manual-conversation-id="{cid}"]');row.locator('summary').click();row.get_by_role('button',name='Mark as Unread',exact=True).click()
            expect(row.locator('.manual-unread-dot')).to_have_count(2);expect(row.locator('.unread')).to_have_text('2')
            row.locator('.conversation-row').click();expect(page.locator('#chat-manual-unread .manual-unread-dot')).to_have_count(2)
            page.reload();expect(row.locator('.manual-unread-dot')).to_have_count(2);row.locator('.conversation-row').click();expect(page.locator('#chat-manual-unread .manual-unread-dot')).to_have_count(2)
            assert client.get(f'{BASE}/{cid}').json()['conversation']['unread_count']==0
            header=page.locator('#chat-manual-unread');header.locator('summary').click();header.get_by_role('button',name='Clear My Unread Mark',exact=True).click();expect(header.locator('.manual-unread-dot')).to_have_count(1)
            if width<700:page.locator('#back-list').click()
            page.get_by_role('button',name='Marked Unread',exact=True).click();expect(page.locator('.conversation-row')).to_have_count(1);assert 'marked_unread=true' in page.url
            expect(page.locator('.manual-unread-dot').first).to_have_attribute('aria-label',re.compile('Marked unread by '))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'/tmp/manual-unread-{role}-{width}.png',full_page=True)
            assert not errors,errors
        finally:browser.close()


def test_sqlite_rollback_preserves_users_with_existing_foreign_keys(tmp_path,monkeypatch):
    from sqlalchemy import event
    from models import Base
    engine=create_engine(f'sqlite:///{tmp_path}/rollback.db')
    @event.listens_for(engine,'connect')
    def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine);monkeypatch.setattr(database,'engine',engine)
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'))
    try:
        sessions=sessionmaker(bind=engine)
        with sessions() as db:
            user=User(username='retained',password_hash='PRESERVED',role='ADMIN');db.add(user);db.flush()
            c=WhatsAppConversation(phone_number='94770000001',unread_count=5,assigned_to_user_id=user.id);db.add(c);db.flush()
            db.add(StaffAccessAudit(actor_id=user.id,target_id=user.id,action='CREATED',changes={}))
            db.add(Mark(conversation_id=c.id,staff_id=user.id));db.commit()
        command.stamp(cfg,'20261122_53');command.upgrade(cfg,'head');command.downgrade(cfg,'20261122_53')
        with engine.connect() as c:
            assert c.execute(text('SELECT password_hash FROM users')).scalar()=='PRESERVED'
            assert c.execute(text('SELECT unread_count FROM whatsapp_conversations')).scalar()==5
            assert c.execute(text('SELECT count(*) FROM staff_access_audits')).scalar()==1
            assert c.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
            assert 'unread_indicator_colour' not in {col['name'] for col in inspect(c).get_columns('users')}
    finally:engine.dispose()
