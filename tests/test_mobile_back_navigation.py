import shutil
from urllib.parse import urlsplit
import pytest
from test_whatsapp_quick_messages import inbox


def test_back_stays_on_list_through_polling_visibility_and_late_responses(inbox):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,_,ids,sends,_,_=inbox;cid=ids[0]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});errors=[];held=[];delay={'enabled':False};history=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                if url.path==f'/crm/whatsapp/api/conversations/{cid}':
                    history.append(url.path)
                    if delay['enabled']:held.append((route,response));return
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={cid}')
            api.expect(page.locator('#chat-name')).to_have_text('Quick customer 1')
            page.locator('#back-list').click()
            api.expect(page.locator('.conversation-panel')).to_be_visible()
            before=len(history);page.wait_for_timeout(4500)
            assert len(history)>before  # The real four-second refresh ran.
            api.expect(page.locator('.conversation-panel')).to_be_visible()
            api.expect(page.locator('.topbar')).to_be_visible()
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(150)
            api.expect(page.locator('.conversation-panel')).to_be_visible()
            # Explicitly reopening the same selected chat still works.
            page.locator(f'[data-conversation-id="{cid}"]').click()
            api.expect(page.locator('.chat-panel')).to_be_visible()
            page.wait_for_timeout(150);delay['enabled']=True
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(150);assert held
            page.locator('#back-list').click()
            for route,response in held:
                route.fulfill(status=response.status_code,body=response.content,headers={'content-type':'application/json'})
            delay['enabled']=False
            page.wait_for_timeout(150)
            page.evaluate("document.getElementById('inbox').dispatchEvent(new CustomEvent('saved-voice-sent'))")
            page.wait_for_timeout(150)
            api.expect(page.locator('.conversation-panel')).to_be_visible()
            assert not page.locator('#inbox').evaluate("el=>el.classList.contains('chat-open')")
            page.locator(f'[data-conversation-id="{ids[1]}"]').click()
            api.expect(page.locator('#chat-name')).to_have_text('Quick customer 2')
            api.expect(page.locator('.chat-panel')).to_be_visible()
            assert not sends and not errors
        finally:browser.close()
