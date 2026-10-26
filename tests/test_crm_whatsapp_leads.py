"""WhatsApp lead pipeline, verified referral attribution and shared preview security."""
from datetime import datetime,timedelta
import shutil
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
import main
from models import Lead,LeadNote,CRMUser,WhatsAppConversation,WhatsAppConversationMessage,WhatsAppMessageReferral
from test_whatsapp_quick_messages import inbox
from test_whatsapp_referral_cards import PNG

BASE='/crm/leads/api/whatsapp'


def add_referral(sessions,cid,*,source_type='ad',source_id='ad-123',headline='Kids bicycle advertisement',when=None,image=True):
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,cid)
        message=WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',message_text='Hello! Can I get more info on this?',created_at=when or datetime.utcnow())
        db.add(message);db.flush()
        referral=WhatsAppMessageReferral(message_id=message.id,source_id=source_id,source_type=source_type,headline=headline,
            body='Available colors and details',source_url='https://www.facebook.com/ads/example',media_type='image',
            image_url='https://scontent.xx.fbcdn.net/creative.jpg' if image else None,ctwa_clid='private-attribution-value',captured_at=when or datetime.utcnow())
        db.add(referral);db.commit();return message.id,referral.id


def save(client,cid,csrf,body=None,key=None):
    return client.post(f'{BASE}/{cid}/lead',json=body or {},headers={'X-CSRF-Token':csrf,'Idempotency-Key':key or str(uuid4())})


def test_all_conversations_visible_and_referrals_are_verified_and_private(inbox):
    client,sessions,ids,sends,_,_=inbox
    first,_=add_referral(sessions,ids[0],source_id='first-ad',when=datetime(2026,1,1))
    second,_=add_referral(sessions,ids[0],source_id='second-ad',when=datetime(2026,2,1))
    data=client.get(BASE).json();assert data['total']==2
    rows={row['conversation_id']:row for row in data['items']};referral=rows[ids[0]]['latest_referral']
    assert referral['source_id']=='second-ad' and referral['message_id']==second
    assert rows[ids[0]]['referral_count']==2 and rows[ids[0]]['ad_count']==2
    assert rows[ids[1]]['latest_referral'] is None and rows[ids[1]]['lead'] is None
    assert referral['preview_url'].startswith(f'/crm/whatsapp/api/conversations/{ids[0]}/messages/{second}/referrals/')
    body=client.get(BASE).text
    assert 'ctwa_clid' not in body and 'private-attribution-value' not in body and 'fbcdn.net' not in body
    assert 'image_url' not in body and 'thumbnail_url' not in body
    detail=client.get(f'{BASE}/{ids[0]}').json()
    assert [row['source_id'] for row in detail['referrals']]==['second-ad','first-ad']
    assert [row['message_id'] for row in detail['referrals']]==[second,first]
    with sessions() as db:assert db.query(Lead).count()==0  # Browsing is not a bulk import.
    assert not sends


def test_filters_pagination_and_no_organic_inference(inbox):
    client,sessions,ids,_,_,_=inbox
    add_referral(sessions,ids[0]);add_referral(sessions,ids[1],source_type='post')
    assert client.get(BASE,params={'source':'ad'}).json()['total']==1
    assert client.get(BASE,params={'source':'post'}).json()['total']==1
    assert client.get(BASE,params={'source':'none'}).json()['total']==0
    assert client.get(BASE,params={'q':'Quick customer 2'}).json()['items'][0]['conversation_id']==ids[1]
    with sessions() as db:
        db.add_all([WhatsAppConversation(phone_number=f'44770000{n:04}',customer_name=f'New contact {n}',mode='AI') for n in range(52)]);db.commit()
    first=client.get(BASE).json();second=client.get(BASE,params={'offset':50}).json()
    assert first['total']==54 and len(first['items'])==50 and len(second['items'])==4
    assert {r['conversation_id'] for r in first['items']}.isdisjoint({r['conversation_id'] for r in second['items']})
    assert client.get(BASE,params={'source':'organic'}).status_code==422
    assert client.get(BASE,params={'status':'invented'}).status_code==422


def test_create_update_note_idempotency_and_modes_unchanged(inbox):
    client,sessions,ids,sends,_,csrf=inbox
    with sessions() as db:db.add(CRMUser(name='CRM Manager'));db.commit()
    key=str(uuid4());created=save(client,ids[0],csrf,key=key)
    assert created.status_code==200 and save(client,ids[0],csrf,key=key).json()==created.json()
    # A new create request for the same conversation returns its one linked record.
    assert save(client,ids[0],csrf).json()['lead']['id']==created.json()['lead']['id']
    body={'status':'FOLLOW_UP','assigned_to':'CRM Manager','next_followup':'2026-11-05','product_interest':'Customer asked about a bicycle','note':'Follow up after checking availability'}
    key=str(uuid4());assert save(client,ids[0],csrf,body,key).status_code==200
    assert save(client,ids[0],csrf,body,key).status_code==200
    with sessions() as db:
        lead=db.query(Lead).one();assert lead.whatsapp_conversation_id==ids[0] and lead.status=='FOLLOW_UP'
        assert lead.assigned_to=='CRM Manager' and str(lead.next_followup)=='2026-11-05'
        assert db.query(LeadNote).count()==1 and db.get(WhatsAppConversation,ids[0]).mode=='HUMAN'
        assert db.get(WhatsAppConversation,ids[0]).unread_count==2
    assert client.get(BASE,params={'status':'FOLLOW_UP','assigned':'CRM Manager'}).json()['total']==1
    assert client.get(BASE,params={'status':'UNORGANIZED'}).json()['total']==1
    assert not sends


def test_existing_manual_lead_requires_explicit_link_and_keeps_details(inbox):
    client,sessions,ids,_,_,csrf=inbox
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0]);lead=Lead(lead_no='LD-existing',customer_name='Existing CRM name',mobile='+'+conversation.phone_number,
            status='INTERESTED',notes='Keep original notes',assigned_to='Legacy owner',city='Existing delivery city',product_interest='Verified original interest')
        db.add(lead);db.commit();lid=lead.id
    assert save(client,ids[0],csrf).status_code==409
    assert client.get(f'{BASE}/{ids[0]}').json()['matching_leads'][0]['id']==lid
    assert save(client,ids[0],csrf,{'lead_id':lid}).status_code==200
    with sessions() as db:
        lead=db.get(Lead,lid);assert lead.status=='INTERESTED' and lead.notes=='Keep original notes'
        assert lead.customer_name=='Existing CRM name' and lead.assigned_to=='Legacy owner' and lead.city=='Existing delivery city'
        assert db.query(Lead).count()==1
    assert save(client,ids[1],csrf,{'lead_id':lid}).status_code==409
    assert client.get(f'/crm/leads/{lid}').status_code==200


@pytest.mark.parametrize('name',['quick-admin','quick-manager','quick-agent','quick-staff'])
def test_staff_auth_permissions_and_csrf(inbox,name):
    client,_,ids,_,login,_=inbox;csrf=login(name)
    assert client.get(BASE).status_code==200
    assert client.get(BASE).headers['cache-control']=='private, no-store'
    assert client.post(f'{BASE}/{ids[0]}/lead',json={}).status_code==403
    assert save(client,ids[0],csrf).status_code==200


def test_unauthorized_and_malformed_requests(inbox):
    client,sessions,ids,_,login,csrf=inbox
    assert TestClient(main.app).get(BASE,follow_redirects=False).status_code==303
    login('quick-denied');assert client.get(BASE).status_code==403
    assert save(client,ids[0],csrf).status_code==403
    csrf=login()
    assert client.get(f'{BASE}/999999').status_code==404
    for body in [{'status':'INVALID'},{'assigned_to':'Unknown'},{'next_followup':'tomorrow'},{'note':'x'*2001},{'lead_id':True}]:
        assert save(client,ids[0],csrf,body).status_code in {422,409}
    with sessions() as db:assert db.query(Lead).count()==0


def test_unsafe_links_and_partial_referral_never_expose_tracking_or_creative_urls(inbox):
    client,sessions,ids,_,_,_=inbox
    _,rid=add_referral(sessions,ids[0],headline='<script>alert("bad")</script>')
    with sessions() as db:
        ref=db.get(WhatsAppMessageReferral,rid);ref.source_url='javascript:alert(1)';ref.image_url='https://evil.example/file.jpg';ref.thumbnail_url='http://127.0.0.1/secret';db.commit()
    data=next(row for row in client.get(BASE).json()['items'] if row['conversation_id']==ids[0])['latest_referral']
    assert data.get('preview_url') is None and data.get('source_url') is None
    assert data['headline']=='<script>alert("bad")</script>'  # Text only; DOM escapes it.
    assert 'ctwa_clid' not in str(data) and 'evil.example' not in str(data)


def test_chromium_leads_ad_preview_history_manage_and_saved_records(inbox):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,_=inbox
    with sessions() as db:db.add(CRMUser(name='Lead manager'));db.commit()
    attack='<img src=x onerror="window.leadInjected=true">'
    add_referral(sessions,ids[0],source_id='first-ad',headline=attack,when=datetime(2026,1,1))
    add_referral(sessions,ids[0],source_id='second-ad',when=datetime(2026,2,1))
    with playwright.sync_playwright() as api:
        browser=api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[];preview_urls=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                if url.path.endswith('/preview'):
                    preview_urls.append(request.url);route.fulfill(status=200,body=PNG,headers={'content-type':'image/png','cache-control':'private, no-store'});return
                headers={key:value for key,value in request.headers.items() if key in {'content-type','idempotency-key','x-csrf-token'}}
                response=client.request(request.method,url.path+('?' + url.query if url.query else ''),headers=headers,content=request.post_data_buffer,follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={key:value for key,value in response.headers.items() if key not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/leads')
            page.locator('#wa-leads-result').filter(has_text='of 2 WhatsApp').wait_for()
            assert 'No referral received' in page.locator('#wa-leads-rows').inner_text()
            page.locator('#wa-leads-source').select_option('ad')
            page.locator('#wa-leads-result').filter(has_text='of 1 WhatsApp').wait_for()
            assert 'second-ad' in page.locator('#wa-leads-rows').inner_text()
            page.get_by_role('button',name='Organize / referrals').click()
            page.locator('#wa-lead-referrals').filter(has_text='first-ad').wait_for()
            assert attack in page.locator('#wa-lead-referrals').inner_text()
            assert not page.evaluate('Boolean(window.leadInjected)')
            assert page.locator('#wa-lead-referrals img').count()==2
            page.get_by_role('button',name='Create CRM record',exact=True).click()
            page.locator('#wa-lead-fields').wait_for(state='visible')
            page.wait_for_function('!document.getElementById("wa-lead-fields").disabled')
            page.locator('#wa-lead-edit-status').select_option('INTERESTED')
            page.locator('#wa-lead-edit-assigned').select_option('Lead manager')
            page.locator('#wa-lead-followup').fill('2026-11-08')
            page.locator('#wa-lead-note').fill('Contact after availability check')
            page.locator('#wa-lead-save').click()
            page.wait_for_function('document.getElementById("wa-lead-note").value===""')
            page.locator('#wa-leads-rows').filter(has_text='INTERESTED').wait_for()
            page.set_viewport_size({'width':390,'height':760})
            bounds=page.locator('#wa-lead-dialog').bounding_box();assert bounds['x']>=0 and bounds['x']+bounds['width']<=390
            page.keyboard.press('Escape');assert not page.locator('#wa-lead-dialog').is_visible()
            page.set_viewport_size({'width':1366,'height':900})
            page.locator('#saved-leads-tab').click();page.wait_for_url('**/crm/leads?view=saved')
            assert page.locator('#saved-leads-pane').is_visible()
            assert 'LD-WA-' in page.locator('#saved-leads-pane').inner_text()
            assert preview_urls and all('/crm/whatsapp/api/conversations/' in url for url in preview_urls)
            assert not errors and not sends
            with sessions() as db:assert db.query(LeadNote).count()==1
        finally:browser.close()


def test_referral_history_pagination_and_original_message_scoping(inbox,monkeypatch):
    client,sessions,ids,_,_,_=inbox
    for number in range(22):add_referral(sessions,ids[0],source_id=f'ad-{number}',when=datetime(2026,1,1)+timedelta(minutes=number))
    first=client.get(f'{BASE}/{ids[0]}').json();second=client.get(f'{BASE}/{ids[0]}',params={'offset':20}).json()
    assert first['referral_total']==22 and len(first['referrals'])==20 and len(second['referrals'])==2
    assert first['referrals'][0]['source_id']=='ad-21' and second['referrals'][-1]['source_id']=='ad-0'
    url=first['referrals'][0]['preview_url']
    monkeypatch.setattr(main,'get_preview',lambda *args:PNG)
    assert client.get(url).status_code==200
    assert client.get(url.replace(f'conversations/{ids[0]}/',f'conversations/{ids[1]}/')).status_code==404
    assert TestClient(main.app).get(url,follow_redirects=False).status_code==303
    assert client.get(f'{BASE}/{ids[1]}').json()['referrals']==[]


def test_unrelated_lead_cannot_be_attached_and_ad_does_not_assign_a_product(inbox,monkeypatch):
    client,sessions,ids,_,_,csrf=inbox
    add_referral(sessions,ids[0])
    monkeypatch.setattr(main,'get_product_by_handle',lambda *a:pytest.fail('Advertisement ID used for product matching'))
    with sessions() as db:
        lead=Lead(lead_no='LD-other',customer_name='Different customer',mobile='44770000000');db.add(lead);db.commit();lid=lead.id
    assert save(client,ids[0],csrf,{'lead_id':lid}).status_code==409
    assert save(client,ids[0],csrf).status_code==200
    with sessions() as db:
        lead=db.query(Lead).filter_by(whatsapp_conversation_id=ids[0]).one();assert not lead.product_interest
        assert db.get(Lead,lid).whatsapp_conversation_id is None


def test_partial_unrecognized_referral_is_not_claimed_to_be_an_ad(inbox):
    client,sessions,ids,_,_,_=inbox
    _,rid=add_referral(sessions,ids[0],source_type='unknown',source_id=None,headline=None,image=False)
    with sessions() as db:
        row=db.get(WhatsAppMessageReferral,rid);row.body=None;row.source_url=None;db.commit()
    item=next(row for row in client.get(BASE).json()['items'] if row['conversation_id']==ids[0])
    assert item['referral_count']==1 and item['ad_count']==0
    assert not item['latest_referral'].get('source_type') and not item['latest_referral'].get('source_id')
    assert client.get(BASE,params={'source':'ad'}).json()['total']==0
    assert client.get(BASE,params={'source':'none'}).json()['total']==1


def test_existing_order_lead_with_alternative_contact_phone_can_be_linked(inbox):
    client,sessions,ids,_,_,csrf=inbox
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0])
        lead=Lead(lead_no='LD-order',customer_name='Order contact',mobile='94771119999',status='PAYMENT_PENDING',notes=f'PAYMENT DETAILS REQUIRED\nWhatsApp: {conversation.phone_number}\nDelivery address: retained')
        db.add(lead);db.commit();lid=lead.id
    assert save(client,ids[0],csrf).status_code==409
    assert save(client,ids[0],csrf,{'lead_id':lid}).status_code==200
    with sessions() as db:
        assert db.query(Lead).count()==1 and db.get(Lead,lid).status=='PAYMENT_PENDING'
        assert 'Delivery address: retained' in db.get(Lead,lid).notes


def test_chromium_unavailable_creative_keeps_useful_text_card(inbox):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,_=inbox
    add_referral(sessions,ids[0],headline='Verified referral headline')
    with playwright.sync_playwright() as api:
        browser=api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                if url.path.endswith('/preview'):route.fulfill(status=502,json={'detail':'Advertisement preview unavailable'},headers={'cache-control':'private, no-store'});return
                response=client.request(request.method,url.path+('?' + url.query if url.query else ''),follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={key:value for key,value in response.headers.items() if key not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/leads')
            row=page.locator(f'#wa-leads-rows tr[data-conversation-id="{ids[0]}"]')
            row.filter(has_text='Preview unavailable').wait_for()
            assert 'Verified referral headline' in row.inner_text() and 'ad-123' in row.inner_text()
            assert row.locator('img').count()==0 and row.get_by_role('link',name='View source').count()==1
            assert not errors and not sends
        finally:browser.close()
