import shutil
from urllib.parse import urlsplit
import pytest
from models import GlobalAIReplyControl
from test_whatsapp_quick_messages import inbox


def test_inbox_ai_dot_status_refresh_mobile_layout_and_accessibility(inbox):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,_,sends,_,_=inbox
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    mocked={'available':True,'enabled':False};posts=[];errors=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                if req.method!='GET':posts.append(url.path)
                if url.path=='/crm/whatsapp/api/global-ai-state':
                    route.fulfill(status=503 if mocked.get('error') else 200,json=mocked);return
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/whatsapp')
            button=page.locator('[data-ai-status-button]');dot=page.locator('.inbox-ai-dot');tip=page.locator('#inbox-ai-description')
            api.expect(button).to_have_attribute('aria-label','Global AI auto replies off')
            api.expect(dot).to_have_css('background-color','rgb(171, 38, 52)')
            assert dot.bounding_box()['width']==8 and dot.bounding_box()['height']==8
            assert page.locator('.topbar').bounding_box()['height']==44
            assert page.locator('.topbar .global-ai-status').count()==0
            assert page.locator('.topbar a[href="/crm/bot-management/settings"]').count()==0
            assert button.inner_text()==''
            for width in [320,360,390,430]:
                page.set_viewport_size({'width':width,'height':812})
                title=page.locator('.topbar h1').bounding_box();target=button.bounding_box();menu=page.locator('#mobile-menu').bounding_box()
                assert target['x']>=title['x']+title['width']
                assert title['x']>=menu['x']+menu['width']
                assert page.locator('.topbar').bounding_box()['height']==44
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                button.click()
                api.expect(tip).to_be_visible();api.expect(tip).to_have_text('Global AI auto replies off')
                page.keyboard.press('Escape');api.expect(tip).to_be_hidden()
            mocked['enabled']=True
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            api.expect(button).to_have_attribute('aria-label','Global AI auto replies on')
            api.expect(dot).to_have_css('background-color','rgb(37, 131, 72)')
            mocked['error']=True
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            api.expect(button).to_have_attribute('aria-label','Global AI auto replies unavailable')
            api.expect(dot).to_have_css('background-color','rgb(152, 162, 179)')
            assert not posts and not sends and not errors
            with sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False
        finally:browser.close()
