"""Authenticated profile rendering and unchanged conversation/customer data."""
import shutil
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import main
from models import WhatsAppConversation,WhatsAppConversationMessage
from test_whatsapp_quick_messages import inbox


@pytest.mark.parametrize('mode',['AI','HUMAN'])
def test_profile_metadata_preserves_storage_name_mode_and_messages(inbox,mode):
    client,sessions,ids,sends,_,_=inbox
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0]);conversation.phone_number='94712345678';conversation.customer_name='Existing customer';conversation.mode=mode
        db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',message_text='Existing message'));db.commit()
    queries=[]
    with sessions() as db:engine=db.get_bind()
    def statement(conn,cursor,sql,*args):queries.append(sql)
    event.listen(engine,'before_cursor_execute',statement)
    try:response=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}')
    finally:event.remove(engine,'before_cursor_execute',statement)
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    payload=response.json();profile=payload['conversation']
    assert profile['phone_country']=={'region':'LK','name':'Sri Lanka','dialing_code':'+94','flag':'🇱🇰'}
    assert profile['customer_name']=='Existing customer' and profile['phone_number']=='94712345678' and profile['mode']==mode
    assert payload['messages'][0]['text']=='Existing message' and payload['order'] is None
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in queries)
    assert not any('country' in sql.lower() for sql in queries)
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,ids[0]);assert conversation.phone_number=='94712345678' and conversation.unread_count==2
    assert not sends
    # Country parsing is confined to the selected customer's existing API.
    assert 'phone_country' not in client.get('/crm/whatsapp/api/conversations').json()['conversations'][0]


def test_authenticated_access_and_country_unknown(inbox):
    client,sessions,ids,_,_,_=inbox
    assert TestClient(main.app).get(f'/crm/whatsapp/api/conversations/{ids[0]}',follow_redirects=False).status_code==303
    for value in ['invalid','18005551234','80012345678','']:
        with sessions() as db:db.get(WhatsAppConversation,ids[0]).phone_number=value;db.commit()
        assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['conversation']['phone_country'] is None


def test_chromium_country_position_switching_and_unknown_fallback(inbox):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,_,_=inbox
    with sessions() as db:
        db.get(WhatsAppConversation,ids[0]).phone_number='94712345678'
        db.get(WhatsAppConversation,ids[1]).phone_number='14165550123';db.commit()
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[];requested=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;parsed=urlsplit(request.url);requested.append(parsed.hostname)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.locator('#profile').filter(has_text='🇱🇰 Sri Lanka (+94)').wait_for()
            labels=page.locator('#profile .detail-row>span:first-child').all_text_contents()
            assert labels==['Name','WhatsApp','Phone country','Mode']
            assert '94712345678' in page.locator('#profile').inner_text()
            assert 'HUMAN' in page.locator('#profile').inner_text()
            row=page.locator('#profile .detail-row').filter(has_text='Phone country')
            assert 'does not indicate current location' in row.get_attribute('title')
            page.locator('.conversation-row').filter(has_text='Quick customer 2').click()
            page.locator('#profile').filter(has_text='🇨🇦 Canada (+1)').wait_for()
            assert 'AI' in page.locator('#profile').inner_text()
            with sessions() as db:db.get(WhatsAppConversation,ids[1]).phone_number='18005551234';db.commit()
            page.reload();page.locator('.conversation-row').filter(has_text='Quick customer 2').click()
            page.locator('#profile .detail-row').filter(has_text='Phone country').filter(has_text='Unknown').wait_for()
            assert not errors and not sends
            # Existing static CDNs are unchanged; country detection adds no remote service.
            assert set(requested)<={'inbox.test','unpkg.com','cdn.tailwindcss.com'}
        finally:browser.close()
