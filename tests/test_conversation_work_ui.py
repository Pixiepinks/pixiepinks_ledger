import shutil
from datetime import datetime,timedelta
from urllib.parse import urlsplit
import pytest
from models import WhatsAppConversation as C,WhatsAppConversationMessage as M
from test_whatsapp_quick_messages import inbox
from test_conversation_work import seed,current


def test_desktop_mobile_status_actions_bulk_and_dashboard(inbox):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,*_=inbox
    for cid in ids:seed(sessions,cid)
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='work.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto(f'https://work.test/crm/whatsapp?conversation={ids[0]}&reply_queue=needs_reply')
        actions=page.locator('#conversation-work-actions');actions.get_by_role('button',name='Resolve',exact=True).click()
        assert page.locator('#work-dialog').is_visible()
        page.locator('#work-note').fill('Handled through another channel');page.locator('#work-confirm').click()
        actions.get_by_role('button',name='Reopen',exact=True).wait_for()
        page.wait_for_function('(id)=>!document.querySelector(`#conversation-list [data-conversation-id="${id}"]`)',arg=ids[0])
        actions.get_by_role('button',name='Reopen',exact=True).click();page.locator('#work-confirm').click()
        actions.get_by_role('button',name='Follow up',exact=True).wait_for();actions.get_by_role('button',name='Follow up',exact=True).click()
        assert 'Sri Lanka' in page.locator('#work-schedule').inner_text()
        page.locator('#work-confirm').click();actions.get_by_role('button',name='Reschedule',exact=True).wait_for()
        page.screenshot(path='/tmp/conversation-work-desktop.png',full_page=True)
        page.goto('https://work.test/crm');page.locator('#dash-kpis strong').first.wait_for()
        assert 'Needs our reply 1' in page.locator('#dash-queues').inner_text()
        page.locator('#dash-attention').get_by_role('button',name='Resolve',exact=True).click();page.locator('#work-confirm').click()
        page.wait_for_function('document.querySelector("#dash-attention").textContent.includes("No matching")')
        page.goto('https://work.test/crm/whatsapp')
        page.locator('#work-select-visible').click();assert page.locator('#work-selected-count').inner_text()=='2 selected'
        page.locator('#work-bulk-resolve').click();assert '2 explicitly selected' in page.locator('#work-customer').inner_text()
        page.locator('#work-close').click();assert current(client,ids[0])['work']['status']=='FOLLOW_UP_LATER'
        for width in [320,360,390,430]:
            page.set_viewport_size({'width':width,'height':844})
            page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]').click()
            assert page.locator('#conversation-work-actions').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if width==390:page.screenshot(path='/tmp/conversation-work-mobile.png',full_page=True)
            page.locator('#back-list').click()
        assert not errors and not sends
        browser.close()
