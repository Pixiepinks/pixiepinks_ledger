"""Admin-created accounts: server authorization, sessions, audit and frontend."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,inspect
import main
import database
import staff_access as access
from models import User,StaffAccessAudit,GlobalAIReplyControl
from utils_auth import verify_password
from test_whatsapp_quick_messages import inbox,body

PASSWORD='Staff-Test-Password-2026'


@pytest.fixture
def staff(inbox):
    admin,sessions,ids,sends,login,csrf=inbox
    with sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    def create(sections=('whatsapp',),name='new-staff',**kw):
        return admin.post('/admin/staff',data={'username':name,'new_password':PASSWORD,
            'sections':list(sections),'csrf_token':csrf,**kw},follow_redirects=False)
    def signin(name='new-staff',password=PASSWORD):
        client=TestClient(main.app,base_url='https://testserver')
        result=client.post('/login',data={'username':name,'password':password},follow_redirects=False)
        return client,result
    def target():
        with sessions() as db:return db.query(User).filter_by(username='new-staff').one().id
    def update(sections=('whatsapp',),active=True,password='',version=None):
        with sessions() as db:row=db.get(User,target());current=row.session_version
        return admin.post('/admin/staff/'+str(target()),data={'sections':list(sections),
            **({'active':'true'} if active else {}),'new_password':password,
            'expected_version':current if version is None else version,'csrf_token':csrf},follow_redirects=False)
    return SimpleNamespace(admin=admin,sessions=sessions,ids=ids,csrf=csrf,create=create,signin=signin,target=target,update=update)


def test_create_login_navigation_and_server_gates(staff):
    s=staff;assert s.create(role='ADMIN').status_code==303
    client,result=s.signin();assert result.status_code==303 and result.headers['location']=='/crm/whatsapp'
    page=client.get('/crm/whatsapp');assert page.status_code==200
    assert 'href="/dashboard"' not in page.text and 'href="/accounts"' not in page.text
    assert 'href="/admin/staff"' not in page.text
    for path in ['/dashboard','/accounts','/entries','/customers','/suppliers','/items','/crm','/crm/leads',
                 '/crm/bot-management','/crm/quick-messages/api','/crm/saved-voices/api','/api/sales-orders','/admin/staff']:
        assert client.get(path).status_code==403,path
    assert client.post('/entries',data={}).status_code==403
    assert client.post('/admin/staff',data={'username':'attack','new_password':PASSWORD,'csrf_token':s.csrf}).status_code==403
    with s.sessions() as db:
        target=db.get(User,s.target());assert target.role=='STAFF' and target.section_access==['whatsapp']
        assert verify_password(PASSWORD,target.password_hash)
        audit=db.query(StaffAccessAudit).one();assert audit.action=='CREATED'
        assert PASSWORD not in json.dumps(audit.changes) and target.password_hash not in s.admin.get('/admin/staff').text


def test_permission_update_revokes_session_and_respects_new_grants(staff):
    s=staff;s.create();client,_=s.signin()
    assert s.update(sections=['customers']).status_code==303
    assert client.get('/crm/whatsapp',follow_redirects=False).status_code==303
    client,result=s.signin();assert result.headers['location']=='/customers'
    assert client.get('/customers').status_code==200 and client.get('/crm/whatsapp').status_code==403
    assert s.update(sections=['whatsapp'],version=0).status_code==409
    assert s.admin.get('/admin/staff').status_code==200


def test_multiple_sessions_observe_database_revocation(staff):
    s=staff;s.create();one,_=s.signin();two,_=s.signin()
    assert one.get('/crm/whatsapp').status_code==two.get('/crm/whatsapp').status_code==200
    s.update(sections=['customers'])
    assert one.get('/crm/whatsapp',follow_redirects=False).status_code==303
    assert two.get('/crm/whatsapp',follow_redirects=False).status_code==303


def test_inbox_does_not_bypass_saved_voice_or_quick_section_grants(staff):
    s=staff;s.create();client,_=s.signin()
    assert client.get('/crm/whatsapp/api/quick-messages').status_code==403
    assert client.post(f'/crm/whatsapp/api/conversations/{s.ids[0]}/send-saved-voice',json={'voice_id':1,'revision':1},headers={'Idempotency-Key':'forbidden-saved-voice'}).status_code==403
    page=client.get('/crm/whatsapp').text
    assert re.search(r'id="saved-voices-open"\s+hidden',page)


def test_deactivate_and_password_reset_invalidate_old_sessions(staff):
    s=staff;s.create();client,_=s.signin()
    new='Replacement-Password-2026'
    assert s.update(password=new).status_code==303
    assert client.get('/crm/whatsapp',follow_redirects=False).status_code==303
    assert '/login?error=' in s.signin()[1].headers['location']
    client,result=s.signin(password=new);assert result.headers['location']=='/crm/whatsapp'
    assert s.update(active=False).status_code==303
    assert client.get('/crm/whatsapp',follow_redirects=False).status_code==303
    assert '/login?error=' in s.signin(password=new)[1].headers['location']
    with s.sessions() as db:
        logs=db.query(StaffAccessAudit).all()
        assert len(logs)==3 and all(new not in json.dumps(log.changes) for log in logs)
        assert logs[1].changes['password_reset'] is True


def test_empty_grants_unknown_grants_csrf_and_admin_protection(staff):
    s=staff
    assert s.create(csrf_token='bad').status_code==403
    assert s.create(sections=['invented']).status_code==422
    assert s.create(new_password='short').status_code==422
    assert s.create(sections=[]).status_code==303
    assert s.create().status_code==409
    client,result=s.signin();assert result.headers['location']=='/staff-home'
    assert client.get('/staff-home').status_code==200 and client.get('/crm/whatsapp').status_code==403
    with s.sessions() as db:admin_id=db.query(User).filter_by(username='quick-admin').one().id
    assert s.admin.post(f'/admin/staff/{admin_id}',data={'csrf_token':s.csrf,'expected_version':0,'active':'true'}).status_code==403


def test_failed_account_switch_does_not_retain_an_admin_session(staff):
    s=staff;s.create()
    response=s.admin.post('/login',data={'username':'new-staff','password':'wrong'},follow_redirects=False)
    assert '/login?error=' in response.headers['location']
    assert s.admin.get('/admin/staff',follow_redirects=False).status_code==303


def test_staff_login_returns_to_section_not_api_or_mutating_get(staff):
    s=staff;s.create(sections=['journal','whatsapp'])
    client=TestClient(main.app,base_url='https://testserver')
    for next_url,expected in [('/entries/123/delete','/entries'),('/crm/whatsapp/api/conversations','/crm/whatsapp')]:
        result=client.post('/login',data={'username':'new-staff','password':PASSWORD,'next':next_url},follow_redirects=False)
        assert result.headers['location']==expected


def test_failed_commit_rolls_back_access_password_and_audit(staff,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    s=staff;s.create();client,_=s.signin()
    with s.sessions() as db:before=db.get(User,s.target()).password_hash
    with monkeypatch.context() as patch:
        patch.setattr(s.sessions.class_,'commit',lambda db:(_ for _ in ()).throw(SQLAlchemyError('private-database-error')))
        result=s.update(sections=['finance'],password='Replacement-Password-2026')
        assert result.status_code==503 and 'private-database-error' not in result.text
    with s.sessions() as db:
        row=db.get(User,s.target())
        assert row.section_access==['whatsapp'] and row.session_version==0 and row.password_hash==before
        assert db.query(StaffAccessAudit).count()==1
    assert client.get('/crm/whatsapp').status_code==200


@pytest.mark.parametrize('key,label,path',access.SECTIONS)
def test_each_section_can_be_granted_without_granting_others(staff,monkeypatch,key,label,path):
    s=staff;monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    monkeypatch.setattr(main.settings,'SALES_FULFILLMENT_ENABLED',True)
    assert s.create(sections=[key]).status_code==303
    client,result=s.signin();assert result.headers['location']==path
    response=client.get(path)
    assert response.status_code==200,(key,response.text[:300])
    assert access.section_for(path)==key
    for other,_,url in access.SECTIONS:
        if other!=key:assert client.get(url).status_code==403,(key,other)


def test_quick_and_voice_management_and_admin_only_controls(staff):
    s=staff;s.create(sections=['whatsapp','quick_messages','saved_voices','bot','sales_orders','fulfillments'])
    client,_=s.signin()
    page=client.get('/crm/quick-messages');token=re.search(r'data-csrf="([^"]+)"',page.text).group(1)
    assert client.post('/crm/quick-messages/api',json=body(),headers={'X-CSRF-Token':token}).status_code==200
    assert 'id="saved-voice-form"' in client.get('/crm/saved-voices').text
    assert client.post('/crm/bot-management/global-ai',data={'enabled':'true','expected_generation':0,'confirm':'CONFIRM','csrf_token':token}).status_code==403
    assert client.get('/crm/bot-management/voice-diagnostics').status_code==403
    with s.sessions() as db:
        user=db.get(User,s.target());perms=access.sales_permissions(user,main.SALES_ORDER_PERMISSIONS)
        assert {'create','edit','fulfill_operate'}<=perms and 'verify_payment' not in perms
        assert db.get(GlobalAIReplyControl,1).enabled is False


def test_legacy_migration_keeps_accounts_and_repeats_safely(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'old-users.db'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role VARCHAR(30) NOT NULL)'))
        conn.execute(text("INSERT INTO users VALUES (1,'admin','retained-hash','ADMIN'),(2,'staff','retained-staff-hash','STAFF')"))
    monkeypatch.setattr(database,'engine',engine)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(config,'20261030_21');command.upgrade(config,'head');command.upgrade(config,'head')
    with engine.connect() as conn:
        rows=conn.execute(text('SELECT username,password_hash,section_access,is_active,session_version FROM users ORDER BY id')).all()
        assert rows[0]==('admin','retained-hash',None,1,0)
        assert rows[1]==('staff','retained-staff-hash',None,1,0)
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261103_22'
    assert 'staff_access_audits' in inspect(engine).get_table_names()
    engine.dispose()


def test_browser_admin_creates_account_with_section_checkboxes(staff):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    s=staff;client=TestClient(main.app,base_url='https://testserver')
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':1366,'height':900})
            context.add_cookies([{'name':'session','value':s.admin.cookies.get('session'),'domain':'staff.test','path':'/','secure':True}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='staff.test':route.fulfill(status=200,body='');return
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:v for k,v in req.all_headers().items() if k in {'cookie','content-type'}},follow_redirects=True)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding'}})
            page.route('**/*',serve);page.goto('https://staff.test/admin/staff')
            form=page.locator('form[action="/admin/staff"]')
            assert form.locator('input[name="sections"]').count()==18
            form.locator('[name="username"]').fill('front-staff');form.locator('[name="new_password"]').fill(PASSWORD)
            form.locator('[name="sections"][value="whatsapp"]').check()
            form.get_by_role('button',name='Create staff account',exact=True).click()
            page.wait_for_load_state('networkidle')
            assert page.locator('.staff-account').filter(has_text='front-staff').count(),page.locator('body').inner_text()[:1200]
            api.expect(page.locator('.staff-account').filter(has_text='front-staff').locator('h3')).to_contain_text('front-staff')
            with s.sessions() as db:assert db.query(User).filter_by(username='front-staff').one().section_access==['whatsapp']
            page.screenshot(path='/workspace/staff-access-preview.png',full_page=True)
            assert not errors
        finally:browser.close()
