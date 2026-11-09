import shutil
from urllib.parse import urlsplit
import pytest
from test_ad_assignment import routing,history_chat,AD
from test_whatsapp_quick_messages import inbox
from models import AdAssignmentRule as Rule,WhatsAppConversation as C,ConversationAssignmentAudit as Audit


def test_frontend_create_preview_apply_edit_history_and_mobile(routing):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    f=routing;client=f[0]
    with f[1]() as db:
        c=history_chat(db,'94776666666');db.commit();cid=c.id
    def serve(page):
        def handle(route):
            req=route.request;u=urlsplit(req.url)
            response=client.request(req.method,u.path+('?' +u.query if u.query else ''),headers={k:v for k,v in req.headers.items() if k not in {'host','cookie'}},content=req.post_data_buffer,follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',handle)
    errors=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));serve(page)
            page.goto('https://routing.test/crm/ad-assignments')
            api.expect(page.locator('#ad-rule-feedback')).to_contain_text('Rules loaded')
            page.locator('#ad-rule-picker').select_option(AD)
            page.locator('#ad-rule-label').fill('Bicycle enquiries')
            page.locator('#ad-rule-staff').select_option(str(f[6]['quick-staff']))
            page.get_by_role('button',name='Save rule',exact=True).click()
            api.expect(page.locator('#ad-rule-feedback')).to_contain_text('Rule saved')
            api.expect(page.locator('#ad-rule-list')).to_contain_text('quick-staff')
            page.get_by_role('button',name='Apply to existing unassigned chats',exact=True).click()
            api.expect(page.locator('#ad-rule-preview')).to_be_visible()
            api.expect(page.locator('#ad-preview-apply')).to_be_disabled()
            page.locator('#ad-preview-confirm').check();page.locator('#ad-preview-apply').click()
            api.expect(page.locator('#ad-rule-feedback')).to_contain_text('Assigned 1 chats')
            page.get_by_role('button',name='History',exact=True).click()
            api.expect(page.locator('#ad-rule-history')).to_contain_text('Applied to existing chat')
            page.get_by_role('button',name='Edit',exact=True).click()
            page.locator('#ad-rule-staff').select_option(str(f[6]['quick-agent']))
            page.locator('#ad-rule-enabled').uncheck();page.get_by_role('button',name='Save rule',exact=True).click()
            api.expect(page.locator('#ad-rule-feedback')).to_contain_text('Rule saved')
            api.expect(page.locator('#ad-rule-list')).to_contain_text('Disabled')
            page.screenshot(path='/tmp/ad-assignment-desktop.png',full_page=True)
            for width in [390,320]:
                mobile=browser.new_page(viewport={'width':width,'height':850});mobile.on('pageerror',lambda e:errors.append(str(e)));serve(mobile)
                mobile.goto('https://routing.test/crm/ad-assignments?ad_id='+AD)
                api.expect(mobile.locator('#ad-rule-heading')).to_contain_text('Edit rule')
                assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if width==390:mobile.screenshot(path='/tmp/ad-assignment-mobile.png',full_page=True)
                mobile.close()
            assert not errors
        finally:browser.close()
    with f[1]() as db:
        assert db.query(Rule).one().version==2 and db.query(Rule).one().enabled is False
        assert db.get(C,cid).assigned_to_user_id==f[6]['quick-staff'] and db.query(Audit).count()==1
    assert not f[3]
