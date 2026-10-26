"""Inbox lead editing uses the one CRM relation and preserves message/media flows."""
from datetime import datetime
import shutil
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
import main
from models import Lead,WhatsAppConversation,WhatsAppConversationMessage,WhatsAppMessageAttachment
from test_whatsapp_quick_messages import inbox
from test_crm_whatsapp_leads import BASE,save
from test_whatsapp_referral_cards import PNG


def test_browsing_shows_real_linked_statuses_without_creating_leads(inbox):
    client,sessions,ids,sends,_,_=inbox
    with sessions() as db:
        db.add(Lead(lead_no='LD-current',customer_name='Existing CRM',mobile='94770012301',status='FUTURE_BUYER',whatsapp_conversation_id=ids[0]));db.commit()
    response=client.get('/crm/whatsapp/api/conversations');assert response.headers['cache-control']=='private, no-store'
    rows={row['id']:row for row in response.json()['conversations']}
    assert rows[ids[0]]['lead_status']=='FUTURE_BUYER' and rows[ids[0]]['lead_status_label']=='Future Buyer'
    assert rows[ids[1]]['lead_id'] is None and rows[ids[1]]['lead_status_label']=='Not organized'
    detail=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['conversation']
    assert detail['lead_id']==rows[ids[0]]['lead_id'] and detail['lead_status']=='FUTURE_BUYER'
    editor=client.get(f'{BASE}/{ids[0]}/status').json()
    assert len(editor['statuses'])==17 and editor['lead']['id']==detail['lead_id']
    assert 'referrals' not in editor and 'messages' not in editor
    with sessions() as db:assert db.query(Lead).count()==1 and db.get(WhatsAppConversation,ids[0]).unread_count==2
    assert not sends


def test_list_status_lookup_is_batched_not_one_query_per_conversation(inbox):
    client,sessions,_,_,_,_=inbox
    with sessions() as db:
        db.add_all([WhatsAppConversation(phone_number=f'44770000{n:04}',customer_name=f'Contact {n}') for n in range(25)]);db.commit();engine=db.get_bind()
    statements=[]
    def query(conn,cursor,sql,*a):statements.append(sql)
    event.listen(engine,'before_cursor_execute',query)
    try:assert len(client.get('/crm/whatsapp/api/conversations').json()['conversations'])==27
    finally:event.remove(engine,'before_cursor_execute',query)
    assert sum('FROM leads' in sql for sql in statements)==1
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)


@pytest.mark.parametrize('name',['quick-admin','quick-manager','quick-agent','quick-staff'])
def test_status_metadata_and_shared_update_permissions(inbox,name):
    client,sessions,ids,sends,login,_=inbox;csrf=login(name)
    response=client.get(f'{BASE}/{ids[0]}/status');assert response.status_code==200
    assert response.headers['cache-control']=='private, no-store'
    assert response.json()['lead'] is None
    with sessions() as db:activity=db.get(WhatsAppConversation,ids[0]).last_message_at
    key=str(uuid4());response=save(client,ids[0],csrf,{'status':'DISPATCHED'},key)
    assert response.status_code==200
    assert save(client,ids[0],csrf,{'status':'DISPATCHED'},key).json()==response.json()
    row=next(row for row in client.get('/crm/whatsapp/api/conversations').json()['conversations'] if row['id']==ids[0])
    assert row['lead_status_label']=='Dispatched' and row['lead_id']==response.json()['lead']['id']
    with sessions() as db:
        assert db.query(Lead).count()==1 and db.get(WhatsAppConversation,ids[0]).mode=='HUMAN'
        assert db.get(WhatsAppConversation,ids[0]).last_message_at==activity
    assert not sends


def test_metadata_auth_missing_id_and_matching_lead_preservation(inbox):
    client,sessions,ids,_,login,csrf=inbox
    assert TestClient(main.app).get(f'{BASE}/{ids[0]}/status',follow_redirects=False).status_code==303
    login('quick-denied');assert client.get(f'{BASE}/{ids[0]}/status').status_code==403
    csrf=login();assert client.get(f'{BASE}/999999/status').status_code==404
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0])
        lead=Lead(lead_no='LD-match',customer_name='Legacy lead',mobile=conversation.phone_number,status='INTERESTED',assigned_to='Existing owner',notes='Keep original details')
        db.add(lead);db.commit();lid=lead.id
    data=client.get(f'{BASE}/{ids[0]}/status').json();assert data['matching_leads'][0]['id']==lid and data['lead'] is None
    assert save(client,ids[0],csrf,{'status':'PAID'}).status_code==409
    assert save(client,ids[0],csrf,{'status':'PAID','lead_id':lid}).status_code==200
    with sessions() as db:
        lead=db.get(Lead,lid);assert lead.status=='PAID' and lead.assigned_to=='Existing owner' and lead.notes=='Keep original details'
    assert client.post(f'{BASE}/{ids[0]}/lead',json={'status':'NEW'}).status_code==403
    assert save(client,ids[0],csrf,{'status':'invented'}).status_code==422


def test_chromium_mobile_badges_edit_errors_polling_switches_and_media_identity(inbox):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,csrf=inbox
    with sessions() as db:
        first=db.get(WhatsAppConversation,ids[0]);second=db.get(WhatsAppConversation,ids[1])
        first.last_message_at=datetime(2026,10,26,9,5)
        lead=Lead(id=901,lead_no='LD-match',customer_name='Legacy CRM customer',mobile=second.phone_number,status='INTERESTED',notes='Keep legacy notes')
        message=WhatsAppConversationMessage(phone_number=first.phone_number,direction='inbound',message_text='Image message',response_kind='customer_media',created_at=datetime(2026,10,26,9,5))
        db.add_all([lead,message]);db.flush();db.add(WhatsAppMessageAttachment(message_id=message.id,media_id='test-image',media_type='image',mime_type='image/png'));db.commit()
    with playwright.sync_playwright() as api:
        browser=api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':760},timezone_id='America/New_York');errors=[];fail={'save':False};media_requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                if '/attachments/' in url.path and url.path.endswith('/media'):
                    media_requests.append(url.path);route.fulfill(status=200,body=PNG,headers={'content-type':'image/png'});return
                if request.method=='POST' and url.path.endswith('/lead') and fail['save']:
                    route.fulfill(status=503,json={'detail':'Save temporarily unavailable'});return
                headers={k:v for k,v in request.headers.items() if k in {'content-type','idempotency-key','x-csrf-token'}}
                result=client.request(request.method,url.path+('?' + url.query if url.query else ''),headers=headers,content=request.post_data_buffer,follow_redirects=False)
                route.fulfill(status=result.status_code,body=result.content,headers={k:v for k,v in result.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/whatsapp')
            first_row=page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]')
            first_row.locator('.lead-status').filter(has_text='Not organized').wait_for()
            assert first_row.locator('.badge.human').count()==1
            assert '02:35 pm' in first_row.locator('time').inner_text().lower()
            first_row.click();page.wait_for_function('!document.getElementById("inbox-lead-status-select").disabled')
            assert page.locator('#inbox-lead-status-select option').count()==18
            page.locator('#messages .message-attachment img').wait_for()
            assert '02:35:00 pm' in page.locator('#messages').inner_text().lower()
            page.evaluate('window.retainedImage=document.querySelector("#messages .message-attachment img");document.getElementById("inbox").addEventListener("inbox-conversation-data",e=>{if(e.detail.conversation.lead_status==="FUTURE_BUYER")window.leadStatusRefreshed=true;});')
            initial_media_requests=len(media_requests)
            page.locator('#inbox-lead-status-select').select_option('FUTURE_BUYER');page.locator('#inbox-lead-status-save').click()
            page.locator('#inbox-lead-status-feedback').filter(has_text='Saved: Future Buyer').wait_for()
            page.wait_for_function('window.leadStatusRefreshed&&window.retainedImage===document.querySelector("#messages .message-attachment img")')
            assert len(media_requests)==initial_media_requests
            page.locator('#back-list').click()
            first_row.locator('.lead-status').filter(has_text='Future Buyer').wait_for()
            assert first_row.locator('.lead-status').evaluate('(node)=>getComputedStyle(node).backgroundColor')=='rgb(237, 233, 254)'
            assert first_row.locator('.lead-status').bounding_box()['x']>first_row.locator('.badge.human').bounding_box()['x']
            page.reload();first_row.locator('.lead-status').filter(has_text='Future Buyer').wait_for()
            page.set_viewport_size({'width':1366,'height':900});first_row.click()
            page.wait_for_function('document.getElementById("inbox-lead-status-select").value==="FUTURE_BUYER"')
            assert save(client,ids[0],csrf,{'status':'PAID'}).status_code==200
            page.wait_for_function('document.getElementById("inbox-lead-status-select").value==="PAID"',timeout=10000)
            page.locator('#inbox-lead-status-select').select_option('DISPATCHED');fail['save']=True;page.locator('#inbox-lead-status-save').click()
            page.locator('#inbox-lead-status-feedback').filter(has_text='Save temporarily unavailable').wait_for()
            assert not page.locator('#inbox-lead-status-save').is_disabled()
            fail['save']=False;page.locator('#inbox-lead-status-save').click()
            page.locator('#inbox-lead-status-feedback').filter(has_text='Saved: Dispatched').wait_for()
            # A late metadata response for conversation1 must not overwrite conversation2.
            previous=client.get(f'{BASE}/{ids[0]}/status').json()
            page.evaluate('''id=>{const original=window.fetch;window.fetch=(url,options)=>String(url).endsWith(`/${id}/status`)&&!window.heldStatus?(window.heldStatus=true,new Promise(resolve=>window.resolveOldStatus=resolve)):original(url,options);document.getElementById("inbox").dispatchEvent(new CustomEvent("inbox-conversation",{detail:{id}}));}''',ids[0])
            page.wait_for_function('Boolean(window.resolveOldStatus)')
            page.locator(f'.conversation-row[data-conversation-id="{ids[1]}"]').click()
            page.wait_for_function('!document.getElementById("inbox-lead-status-select").disabled')
            page.evaluate('(data)=>window.resolveOldStatus(new Response(JSON.stringify(data),{status:200}))',previous)
            page.evaluate('()=>new Promise(resolve=>setTimeout(resolve,0))')
            assert page.locator('#inbox-lead-status-select').input_value()==''
            page.locator('#inbox-lead-status-select').select_option('INTERESTED')
            assert page.locator('#inbox-lead-status-save').is_disabled()
            page.locator('#inbox-lead-link').select_option('901');page.locator('#inbox-lead-status-save').click()
            page.locator('#inbox-lead-status-feedback').filter(has_text='Saved: INTERESTED').wait_for()
            with sessions() as db:
                assert db.query(Lead).count()==2
                assert db.query(Lead).filter_by(whatsapp_conversation_id=ids[0]).one().status=='DISPATCHED'
                assert db.get(Lead,901).whatsapp_conversation_id==ids[1] and db.get(Lead,901).notes=='Keep legacy notes'
                assert db.get(WhatsAppConversation,ids[1]).mode=='AI'
            assert media_requests and not errors and not sends
        finally:browser.close()


def test_chromium_all_status_colors_are_distinct_readable_and_mobile_safe(inbox):
    import re
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,_=inbox;status_ids={}
    with sessions() as db:
        for number,status in enumerate(main.LEAD_STATUSES+['LEGACY_STATE']):
            conversation=WhatsAppConversation(phone_number=f'44770077{number:04d}',customer_name='Color '+status,mode='AI')
            db.add(conversation);db.flush()
            db.add(Lead(lead_no='LD-color-'+str(number),customer_name=conversation.customer_name,mobile=conversation.phone_number,status=status,whatsapp_conversation_id=conversation.id));status_ids[status]=conversation.id
        db.commit()
    def luminance(value):
        rgb=[float(number)/255 for number in re.findall(r'[\d.]+',value)[:3]]
        linear=[number/12.92 if number<=.04045 else ((number+.055)/1.055)**2.4 for number in rgb]
        return sum(number*weight for number,weight in zip(linear,[.2126,.7152,.0722]))
    with playwright.sync_playwright() as api:
        browser=api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':760});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(request.method,url.path+('?' + url.query if url.query else ''),follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={key:value for key,value in response.headers.items() if key not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/whatsapp')
            page.wait_for_function('document.querySelectorAll(".conversation-row").length===20')
            colors=set()
            for status in main.LEAD_STATUSES:
                row=page.locator(f'.conversation-row[data-conversation-id="{status_ids[status]}"]')
                badge=row.locator('.lead-status');style=badge.evaluate('(node)=>({bg:getComputedStyle(node).backgroundColor,fg:getComputedStyle(node).color})')
                colors.add(style['bg'])
                first,second=luminance(style['bg']),luminance(style['fg'])
                assert (max(first,second)+.05)/(min(first,second)+.05)>=4.5,status
                assert badge.get_attribute('data-lead-status')==status
                assert badge.get_attribute('aria-label').startswith('Lead status: ')
                assert badge.inner_text() and badge.bounding_box()['width']<=row.bounding_box()['width']
                assert row.locator('.badge.ai').count()==1
            assert len(colors)==len(main.LEAD_STATUSES)
            neutral=page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"] .lead-status')
            unknown=page.locator(f'.conversation-row[data-conversation-id="{status_ids["LEGACY_STATE"]}"] .lead-status')
            assert neutral.inner_text()=='Not organized'
            assert neutral.evaluate('(node)=>getComputedStyle(node).backgroundColor')=='rgb(241, 245, 249)'
            assert unknown.evaluate('(node)=>getComputedStyle(node).backgroundColor')=='rgb(241, 245, 249)'
            assert not errors and not sends
        finally:browser.close()


def test_inbox_timestamp_serialization_preserves_utc_instant(inbox):
    from datetime import timezone, timedelta
    client,sessions,ids,*_=inbox
    instant=datetime(2026,10,26,9,5)
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0]);conversation.last_message_at=instant
        db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction="inbound",message_text="Timezone regression",created_at=instant))
        db.commit()
    rows=client.get('/crm/whatsapp/api/conversations').json()['conversations']
    assert next(row for row in rows if row['id']==ids[0])['last_message_at']=='2026-10-26T09:05:00Z'
    messages=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages']
    assert next(row for row in messages if row['text']=='Timezone regression')['timestamp']=='2026-10-26T09:05:00Z'
    assert main._inbox_utc_timestamp(None) is None
    assert main._inbox_utc_timestamp(datetime(2026,10,26,14,35,tzinfo=timezone(timedelta(hours=5,minutes=30))))=='2026-10-26T09:05:00Z'
    with sessions() as db:
        assert db.get(WhatsAppConversation,ids[0]).last_message_at==instant
