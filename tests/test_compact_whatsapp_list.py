from datetime import datetime,timedelta
import shutil
from urllib.parse import urlsplit
import pytest
from models import User,WhatsAppConversation
from test_whatsapp_quick_messages import inbox


def seed(sessions):
    with sessions() as db:
        owner=db.query(User.id).filter_by(username='quick-agent').scalar()
        now=datetime.utcnow()
        rows=[WhatsAppConversation(phone_number=f'pagination-{i}',customer_name=f'Pagination contact {i:03}',last_message_at=now-timedelta(minutes=i),assigned_to_user_id=owner) for i in range(120)]
        db.add_all(rows);db.commit();return [r.id for r in rows]


def test_paginated_chats_have_no_fifty_chat_ceiling_and_remain_private(inbox):
    client,sessions,ids,_,login,_=inbox;seed(sessions)
    base='/crm/whatsapp/api/conversations';seen=[];offset=0
    while True:
        data=client.get(base,params={'offset':offset}).json();page=data['conversations']
        assert not (set(seen)&{r['id'] for r in page})
        seen.extend(r['id'] for r in page)
        if not data['has_more']:break
        assert data['next_offset']>offset;offset=data['next_offset']
    assert len(seen)==122
    assert client.get(base,params={'offset':-1}).status_code==422
    login('quick-staff')
    assert [r['id'] for r in client.get(base).json()['conversations']]==[ids[0]]
    assert client.get(base,params={'offset':50}).json()['conversations']==[]


def test_compact_header_scrolls_beyond_first_page_and_refresh_keeps_loaded_chats(inbox):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,_,sends,_,_=inbox;rows=seed(sessions)
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});errors=[];requests=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                if url.path=='/crm/whatsapp/api/conversations':requests.append(url.query)
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/whatsapp')
            api.expect(page.locator('.topbar h1')).to_have_text('WhatsApp')
            assert page.locator('.topbar small').count()==0 and page.locator('#list-status').count()==0
            assert page.locator('.conversation-panel>.panel-head').count()==0
            api.expect(page.locator('.conversation-row')).to_have_count(50)
            for total in [100,122]:
                page.locator('#conversation-list').evaluate('el=>el.scrollTop=el.scrollHeight')
                api.expect(page.locator('.conversation-row')).to_have_count(total)
            assert page.locator(f'[data-conversation-id="{rows[-1]}"]').count()==1
            # Existing refresh mechanism must preserve the loaded range.
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function('document.querySelectorAll(".conversation-row").length===122')
            api.expect(page.locator('.chat-load-more')).to_have_count(0)
            for width in [320,360,390,430,1366]:
                page.set_viewport_size({'width':width,'height':812})
                assert page.locator('.topbar').bounding_box()['height']==44
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert any('offset=50' in query for query in requests)
            assert not errors and not sends
        finally:browser.close()
