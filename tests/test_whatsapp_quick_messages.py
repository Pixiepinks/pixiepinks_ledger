"""Isolated Phase 7 management, shared-send, security and browser regressions."""
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event
from sqlalchemy.orm import sessionmaker

import database
import main
from models import Base,User,WhatsAppConversation,WhatsAppConversationMessage,WhatsAppManualSend,WhatsAppQuickMessage
from utils_auth import hash_password


@pytest.fixture
def inbox(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path}/quick.db',connect_args={'check_same_thread':False,'timeout':20})
    @event.listens_for(engine,'connect')
    def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database,'engine',engine)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(config,'20261025_11');command.upgrade(config,'head')
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    def get_db():
        with sessions() as db:yield db
    old=dict(main.app.dependency_overrides);main.app.dependency_overrides[main.get_db]=get_db
    monkeypatch.setattr(main,'SessionLocal',sessions)
    password=hash_password('password')
    with sessions() as db:
        db.add_all([User(username=name,password_hash=password,role=role) for name,role in [('quick-admin','ADMIN'),('quick-manager','SALES_MANAGER'),('quick-staff','STAFF'),('quick-agent','SALES_AGENT'),('quick-denied','OTHER')]])
        convos=[WhatsAppConversation(phone_number=f'9477001230{n}',customer_name=f'Quick customer {n}',mode=mode,last_customer_message_at=datetime.utcnow(),unread_count=2) for n,mode in [(1,'HUMAN'),(2,'AI')]]
        db.add_all(convos);db.commit();ids=[row.id for row in convos]
    client=TestClient(main.app,base_url='https://testserver')
    def login(name='quick-admin'):
        client.post('/login',data={'username':name,'password':'password'})
        return client.get('/crm/whatsapp/api/quick-messages').json().get('csrf_token')
    csrf=login()
    sends=[]
    monkeypatch.setattr(main,'send_whatsapp_text',lambda phone,text,**kw:sends.append((phone,text)) or f'wamid.quick-{len(sends)}')
    yield client,sessions,ids,sends,login,csrf
    main.app.dependency_overrides.clear();main.app.dependency_overrides.update(old);engine.dispose()


def body(**overrides):
    return {'title':'Website link','shortcut':'/newwebsite','category':'General','language':'en',
        'body':'🌐 Visit https://www.pixiepinks.shop\nLatest offers!','active':True,'display_order':1,**overrides}


def create(client,csrf,**values):
    return client.post('/crm/quick-messages/api',json=body(**values),headers={'X-CSRF-Token':csrf})


def send(client,cid,csrf,qid=None,text='Edited reply https://www.pixiepinks.shop',key='quick-staff-action'):
    return client.post(f'/crm/whatsapp/api/conversations/{cid}/send',json={'message':text,**({'quick_message_id':qid} if qid is not None else {})},headers={'Idempotency-Key':key,'X-CSRF-Token':csrf})


def test_defaults_are_editable_and_seeded_once(inbox):
    client,sessions,_,sends,_,csrf=inbox
    rows=client.get('/crm/whatsapp/api/quick-messages').json()['messages']
    assert len(rows)==4 and {r['shortcut'] for r in rows}=={'website','delivery','orderinfo','thankyou'}
    website=next(row for row in rows if row['shortcut']=='website')
    assert website['body']=='🌐 Visit our website: https://www.pixiepinks.shop\nExplore our latest products and offers!'
    changed=body(title='Our edited site',shortcut='website',body='Edited URL https://www.pixiepinks.shop',active=False)
    response=client.put(f'/crm/quick-messages/api/{website["id"]}',json=changed,headers={'X-CSRF-Token':csrf})
    assert response.status_code==200 and not response.json()['message']['active']
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.upgrade(config,'head')
    with sessions() as db:
        row=db.get(WhatsAppQuickMessage,website['id']);assert row.title=='Our edited site' and not row.active and row.updated_by=='quick-admin'
    assert not sends


def test_create_edit_deactivate_and_filters(inbox):
    client,_,_,_,_,csrf=inbox
    response=create(client,csrf,shortcut='/HELLO_si',language='si',body='ආයුබෝවන් https://www.pixiepinks.shop',category='Sinhala',title='සිංහල')
    assert response.status_code==200
    row=response.json()['message'];assert row['shortcut']=='hello_si' and row['usage_count']==0
    assert client.get('/crm/whatsapp/api/quick-messages',params={'q':'HELLO_si','category':'Sinhala','language':'si'}).json()['total']==1
    assert client.get('/crm/whatsapp/api/quick-messages',params={'category':'General','language':'si'}).json()['total']==0
    response=client.put(f'/crm/quick-messages/api/{row["id"]}',json=body(shortcut='hello_si',language='si',active=False),headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    assert client.get('/crm/whatsapp/api/quick-messages',params={'q':'hello_si'}).json()['messages']==[]
    assert client.get('/crm/quick-messages/api',params={'status':'inactive'}).json()['total']==1
    assert client.get('/crm/whatsapp/api/quick-messages',params={'q':'%'}).json()['total']==0


@pytest.mark.parametrize('role',['quick-staff','quick-agent','quick-denied'])
def test_management_permissions_and_staff_use(inbox,role):
    client,_,_,_,login,csrf=inbox
    login(role)
    assert client.get('/crm/quick-messages').status_code==403
    assert client.get('/crm/quick-messages/api').status_code==403
    assert create(client,csrf).status_code==403
    assert client.put('/crm/quick-messages/api/1',json=body(),headers={'X-CSRF-Token':csrf}).status_code==403
    assert client.get('/crm/whatsapp/api/quick-messages').status_code==(403 if role=='quick-denied' else 200)
    assert 'href="/crm/quick-messages"' not in client.get('/crm/whatsapp').text


def test_manager_and_csrf_authentication(inbox):
    client,_,ids,_,login,csrf=inbox
    anonymous=TestClient(main.app,base_url='https://testserver')
    for path in ['/crm/quick-messages','/crm/quick-messages/api','/crm/whatsapp/api/quick-messages']:
        assert anonymous.get(path,follow_redirects=False).status_code==303
    assert client.post('/crm/quick-messages/api',json=body()).status_code==403
    qid=client.get('/crm/whatsapp/api/quick-messages').json()['messages'][0]['id']
    assert client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/send',json={'message':'Hello','quick_message_id':qid},headers={'Idempotency-Key':'csrf-failure'}).status_code==403
    csrf=login('quick-manager')
    assert client.get('/crm/quick-messages').status_code==200 and create(client,csrf).status_code==200
    assert 'href="/crm/quick-messages"' in client.get('/crm/whatsapp').text


@pytest.mark.parametrize('values',[
    {'title':''},{'title':'x'*101},{'shortcut':'bad shortcut'},{'shortcut':'x'*41},
    {'category':'x'*81},{'body':'x'*2001},{'body':'\0bad'},{'active':'true'},
    {'language':'ta'},{'language':{}},{'display_order':True},{'display_order':-1},{'display_order':10001},
    {'usage_count':1000},
])
def test_bounded_input_and_read_only_usage(inbox,values):
    client,_,_,_,_,csrf=inbox
    assert create(client,csrf,**values).status_code==422


def test_unique_shortcut_and_concurrent_create(inbox):
    client,_,_,_,_,csrf=inbox
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(lambda _:create(client,csrf,shortcut='/Unique'),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert create(client,csrf,shortcut='UNIQUE').status_code==409


def test_use_is_successful_send_only_and_remains_editable(inbox):
    client,sessions,ids,sends,login,csrf=inbox
    row=client.get('/crm/whatsapp/api/quick-messages',params={'q':'website'}).json()['messages'][0]
    # Listing/selection is read-only: no provider call or usage mutation.
    assert row['usage_count']==0 and not sends
    csrf=login('quick-staff')
    text=row['body']+'\nStaff edited this reply.'
    assert send(client,ids[0],csrf,row['id'],text=text).json()['status']=='SENT'
    assert send(client,ids[0],csrf,row['id'],text=text).json()['status']=='SENT'
    assert len(sends)==1 and sends[0][1]==text and 'https://www.pixiepinks.shop' in sends[0][1]
    with sessions() as db:
        assert db.get(WhatsAppQuickMessage,row['id']).usage_count==1
        message=db.query(WhatsAppConversationMessage).one();assert message.direction=='outbound' and message.send_status=='sent' and message.response_kind=='staff'
        assert db.query(WhatsAppManualSend).one().quick_message_id==row['id']
        conversation=db.get(WhatsAppConversation,ids[0]);assert conversation.mode=='HUMAN' and conversation.unread_count==2
    assert send(client,ids[1],csrf,row['id'],key='second-conversation').json()['status']=='SENT'
    with sessions() as db:
        assert db.get(WhatsAppConversation,ids[1]).mode=='AI'
        assert db.get(WhatsAppQuickMessage,row['id']).usage_count==2
    assert send(client,ids[1],csrf,row['id']).status_code==409  # Cross-conversation key reuse.


def test_window_missing_expired_future_and_legacy_history(inbox):
    client,sessions,ids,sends,_,csrf=inbox
    qid=client.get('/crm/whatsapp/api/quick-messages').json()['messages'][0]['id']
    for value in [None,datetime.utcnow()-timedelta(hours=25),datetime.utcnow()+timedelta(hours=1)]:
        with sessions() as db:db.get(WhatsAppConversation,ids[0]).last_customer_message_at=value;db.commit()
        assert send(client,ids[0],csrf,qid,key='window-denied').status_code==409
        assert send(client,ids[0],csrf,key='plain-window-denied').status_code==409
    assert not sends
    with sessions() as db:
        db.get(WhatsAppConversation,ids[0]).last_customer_message_at=None
        db.add(WhatsAppConversationMessage(phone_number='94770012301',direction='inbound',message_text='Legacy customer reply'));db.commit()
    assert send(client,ids[0],csrf,qid,key='legacy-open').json()['ok']


def test_inactive_template_failure_and_usage_rollback(inbox,monkeypatch):
    client,sessions,ids,sends,_,csrf=inbox
    row=create(client,csrf).json()['message'];qid=row['id']
    with sessions() as db:db.get(WhatsAppQuickMessage,qid).active=False;db.commit()
    assert send(client,ids[0],csrf,qid).status_code==409 and not sends
    with sessions() as db:db.get(WhatsAppQuickMessage,qid).active=True;db.commit()
    monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:False)
    assert send(client,ids[0],csrf,qid).status_code==502
    with sessions() as db:
        assert db.get(WhatsAppQuickMessage,qid).usage_count==0
        assert db.query(WhatsAppConversationMessage).count()==0
    assert send(client,ids[0],csrf,qid).status_code==409


def test_concurrent_duplicate_and_distinct_sends_count_once_each(inbox):
    client,sessions,ids,sends,_,csrf=inbox
    qid=client.get('/crm/whatsapp/api/quick-messages').json()['messages'][0]['id']
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(lambda _:send(client,ids[0],csrf,qid,key='concurrent-same'),range(2)))
    assert any(r.status_code==200 for r in responses)
    assert len(sends)==1
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(lambda n:send(client,ids[0],csrf,qid,key=f'distinct-{n}'),range(2)))
    assert all(r.status_code==200 for r in responses) and len(sends)==3
    with sessions() as db:assert db.get(WhatsAppQuickMessage,qid).usage_count==3


def test_idempotency_scope_active_replay_and_plain_text_regression(inbox):
    client,sessions,ids,sends,login,csrf=inbox
    row=create(client,csrf).json()['message'];qid=row['id']
    assert send(client,ids[0],csrf,qid).json()['ok']
    assert send(client,ids[0],csrf,qid,text='Different edited body').status_code==409
    with sessions() as db:
        db.get(WhatsAppQuickMessage,qid).active=False
        db.get(WhatsAppConversation,ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    assert send(client,ids[0],csrf,qid).json()['ok']
    csrf=login('quick-agent')
    assert send(client,ids[0],csrf,qid).status_code==409
    assert send(client,ids[1],csrf,key='ordinary-text',text='Ordinary text\nhttps://www.pixiepinks.shop').json()['ok']
    with sessions() as db:assert db.get(WhatsAppQuickMessage,qid).usage_count==1


def test_database_failure_rolls_back_usage_and_history_and_blocks_duplicate(inbox,monkeypatch):
    from sqlalchemy.exc import OperationalError
    client,sessions,ids,sends,_,csrf=inbox
    qid=client.get('/crm/whatsapp/api/quick-messages').json()['messages'][0]['id']
    original=sessions.class_.commit
    commits=0
    def commit(db):
        nonlocal commits
        commits+=1
        if commits==2:
            raise OperationalError('safe-test',{},Exception('simulated'))
        return original(db)
    monkeypatch.setattr(sessions.class_,'commit',commit)
    assert send(client,ids[0],csrf,qid).status_code==503
    assert send(client,ids[0],csrf,qid).status_code==409 and len(sends)==1
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count()==0
        assert db.get(WhatsAppQuickMessage,qid).usage_count==0
        assert db.query(WhatsAppManualSend).one().status=='PROCESSING'


def test_ambiguous_provider_acceptance_never_counts_as_success(inbox,monkeypatch):
    client,sessions,ids,_,_,csrf=inbox
    qid=client.get('/crm/whatsapp/api/quick-messages').json()['messages'][0]['id']
    monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:True)
    assert send(client,ids[0],csrf,qid).status_code==503
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count()==0
        assert db.get(WhatsAppQuickMessage,qid).usage_count==0
    assert send(client,ids[0],csrf,qid).status_code==409


def test_chromium_picker_shortcuts_editing_urls_conversations_and_management(inbox,monkeypatch):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,csrf=inbox
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    create(client,csrf,title='Unrelated website mention',shortcut='different',body='A website mention should not override /website.',category='Aardvark')
    malicious=create(client,csrf,title='<script>window.injected=1</script>',shortcut='escape',body='<img src=x onerror="window.injected=1"> https://www.pixiepinks.shop',category='Security').json()['message']
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;parsed=urlsplit(request.url)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}}
                response=client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),headers=headers,content=request.post_data_buffer,follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.locator('#chat-name').filter(has_text='Quick customer 1').wait_for()
            for control in ['record-button','catalog-button','create-order-button','attachment-button','quick-messages-button','send-button']:assert page.locator('#'+control).is_visible()
            page.locator('#quick-messages-button').click();page.get_by_role('option').filter(has_text='Website Link').click()
            assert 'https://www.pixiepinks.shop' in page.locator('#message-input').input_value() and not sends
            page.locator('#message-input').fill(page.locator('#message-input').input_value()+'\nStaff edited before sending.')
            page.locator('#send-button').click();page.wait_for_function('document.getElementById("send-status").textContent==="Message sent."')
            assert len(sends)==1 and sends[0][1].endswith('Staff edited before sending.')
            link=page.locator('.message .bubble a');assert link.get_attribute('href')=='https://www.pixiepinks.shop'
            assert link.get_attribute('rel')=='noopener noreferrer'
            page.locator('#message-input').fill('/website');page.get_by_role('option').filter(has_text='Unrelated website mention').wait_for();page.locator('#message-input').press('Enter')
            assert page.locator('#message-input').input_value().startswith('🌐 Visit our website:') and len(sends)==1
            page.locator('#message-input').fill('/delivery');page.get_by_role('option').filter(has_text='Free Bicycle Delivery').wait_for();page.locator('#message-input').press('Enter')
            assert page.locator('#message-input').input_value()=='🚚 We offer FREE island-wide delivery for bicycles.' and len(sends)==1
            page.locator('#message-input').fill('/thankyou');page.get_by_role('option').filter(has_text='Thank You').wait_for();page.locator('#message-input').press('Tab')
            assert page.locator('#message-input').input_value()=='💖 Thank you for contacting PixiePinks!' and len(sends)==1
            page.locator('.conversation-row').filter(has_text='Quick customer 2').click()
            page.wait_for_function('document.getElementById("inbox").dataset.activeConversation==="2"')
            assert page.locator('#message-input').input_value()==''
            page.locator('#message-input').fill('Ordinary text with https://www.pixiepinks.shop/path and /delivery inside it')
            assert page.locator('#quick-message-panel').is_hidden()
            page.locator('#quick-messages-button').click();page.locator('#quick-message-search').fill('escape')
            page.get_by_role('option').filter(has_text='<script>').wait_for()
            page.locator('#quick-message-search').press('Enter');assert len(sends)==1
            assert page.locator('#quick-message-results img,#quick-message-results script').count()==0
            assert page.evaluate('window.injected') is None
            page.get_by_role('option').filter(has_text='<script>').click();assert '<img' in page.locator('#message-input').input_value()
            page.locator('#send-button').click();page.wait_for_function('document.getElementById("send-status").textContent==="Message sent."')
            page.locator('.message .bubble a').wait_for();assert page.locator('.message .bubble img').count()==0 and page.evaluate('window.injected') is None
            # Management stores plain text, supports create/edit/deactivate and shows usage.
            page.goto('https://inbox.test/crm/quick-messages');page.locator('#quick-new').click()
            for name,value in {'title':'Browser-created reply','shortcut':'browserreply','category':'Browser','body':'Browser https://www.pixiepinks.shop'}.items():page.locator(f'#quick-editor [name="{name}"]').fill(value)
            page.locator('#quick-save').click();page.locator('#quick-admin-rows tr').filter(has_text='Browser-created reply').wait_for()
            row=page.locator('#quick-admin-rows tr').filter(has_text='Browser-created reply');row.get_by_role('button',name='Edit',exact=True).click()
            page.locator('#quick-editor [name="body"]').fill('Edited browser reply');page.locator('#quick-save').click();row.get_by_role('button',name='Deactivate',exact=True).click()
            page.wait_for_function('document.getElementById("quick-admin-rows").textContent.includes("Inactive")')
            assert not errors
            page.set_viewport_size({'width':375,'height':800});assert page.locator('#quick-new').is_visible()
        finally:browser.close()
