import shutil
from urllib.parse import urlsplit
import pytest
from test_ad_flows import flows,incoming
from test_ad_assignment import routing,AD
from test_whatsapp_quick_messages import inbox
from models import CustomerQuestionRun as Run,AdFlowExecution as Exec


def test_frontend_mapping_master_dry_run_inbox_pause_and_mobile(flows):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    f=flows;client=f[0][0]
    def serve(page):
        def handle(route):
            req=route.request;u=urlsplit(req.url)
            if '/referrals/' in u.path and '/preview' in u.path:route.fulfill(status=410,body='Preview unavailable');return
            response=client.request(req.method,u.path+('?' +u.query if u.query else ''),headers={k:v for k,v in req.headers.items() if k not in {'host','cookie'}},content=req.post_data_buffer,follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',handle)
    errors=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));serve(page)
            page.goto('https://flow.test/crm/ad-flows');api.expect(page.locator('#flow-feedback')).to_contain_text('Mappings loaded')
            page.locator('#flow-ad-id').fill(AD);page.locator('#flow-rule-name').fill('Bicycle saved flow');page.locator('#flow-id').select_option('1')
            page.get_by_role('button',name='Save mapping',exact=True).click();api.expect(page.locator('#flow-feedback')).to_contain_text('Mapping saved')
            page.locator('#flow-master').check();api.expect(page.locator('#flow-controls-status')).to_contain_text('Ad flows ON')
            page.get_by_role('button',name='Test rule',exact=True).click();page.locator('#flow-action-confirm').click()
            api.expect(page.locator('#flow-action-feedback')).to_contain_text('Eligible')
            assert not f[1]
            with f[0][1]() as db:assert db.query(Run).count()==0
            page.locator('#flow-action-close').click()
            page.screenshot(path='/tmp/ad-flows-desktop.png',full_page=True)
            assert incoming(f).status_code==200
            with f[0][1]() as db:cid=db.query(Run).one().conversation_id
            page.goto(f'https://flow.test/crm/whatsapp?conversation={cid}')
            api.expect(page.locator('#inbox-ad-flow')).to_contain_text('QUEUED')
            page.locator('#inbox-ad-flow').get_by_role('button',name='Pause Flow',exact=True).click()
            api.expect(page.locator('#inbox-ad-flow')).to_contain_text('PAUSED')
            page.locator('#inbox-ad-flow').get_by_role('button',name='Stop Flow',exact=True).click()
            api.expect(page.locator('#inbox-ad-flow')).to_contain_text('STOPPED')
            for width in [390,320]:
                mobile=browser.new_page(viewport={'width':width,'height':850});mobile.on('pageerror',lambda e:errors.append(str(e)));serve(mobile)
                mobile.goto('https://flow.test/crm/ad-flows');api.expect(mobile.locator('#flow-feedback')).to_contain_text('Mappings loaded')
                assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if width==390:mobile.screenshot(path='/tmp/ad-flows-mobile.png',full_page=True)
                mobile.close()
            assert not errors
        finally:browser.close()
