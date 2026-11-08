import shutil
from datetime import datetime,timedelta
from urllib.parse import urlsplit
import pytest
from models import WhatsAppConversation as C,WhatsAppConversationMessage as M
from test_whatsapp_quick_messages import inbox
from test_conversation_work import seed,current


def test_desktop_mobile_individual_status_actions_and_dashboard(inbox):
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
        def clean_list():
            assert page.locator('#work-bulk,#work-select-visible,#work-clear,#work-selected-count,#work-bulk-resolve,.work-select-row,#conversation-list input[type=checkbox]').count()==0
            assert page.locator('.inbox-search').evaluate('el=>el.nextElementSibling.id')=='conversation-list'
            assert page.locator('#conversation-list > .conversation-row').count()==2
            assert page.locator('.conversation-row').first.locator('.conversation-line strong,time').count()==2
            assert page.locator('.conversation-row').first.locator('p,.conversation-meta').count()==2
            gap=page.locator('.conversation-panel').evaluate('el=>el.querySelector("#conversation-list").getBoundingClientRect().top-el.querySelector(".inbox-search").getBoundingClientRect().bottom')
            assert 0<=gap<=2
        clean_list()
        page.screenshot(path='/tmp/individual-inbox-desktop.png',full_page=True)
        assert current(client,ids[0])['work']['status']=='FOLLOW_UP_LATER'
        for width in [320,360,390,430]:
            page.set_viewport_size({'width':width,'height':844})
            clean_list()
            page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]').click()
            assert page.locator('#conversation-work-actions').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if width==390:page.screenshot(path='/tmp/individual-inbox-mobile.png',full_page=True)
            page.locator('#back-list').click()
        # Individual history and reopening remain available after the UI cleanup.
        page.locator(f'.conversation-row[data-conversation-id="{ids[1]}"]').click()
        page.locator('#chat-name').filter(has_text='Quick customer 2').wait_for()
        actions.get_by_role('button',name='Reopen',exact=True).wait_for()
        actions.get_by_role('button',name='Reopen',exact=True).click()
        assert page.locator('#work-dialog').is_visible()
        page.locator('#work-confirm').click()
        actions.get_by_role('button',name='Resolve',exact=True).wait_for()
        actions.get_by_role('button',name='History',exact=True).click()
        page.get_by_role('heading',name='Conversation status history').wait_for()
        page.locator('.work-history').get_by_role('button',name='Close',exact=True).click()
        page.locator('.work-history').wait_for(state='detached')
        assert not errors and not sends
        browser.close()
