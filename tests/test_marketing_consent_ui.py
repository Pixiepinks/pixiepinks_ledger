import shutil
from urllib.parse import urlsplit
import pytest
from models import MarketingConsent as Consent,MarketingConsentEvent as Event
from test_marketing_consent import consent
from test_whatsapp_quick_messages import inbox


def serve(page,v,fail=None):
    def handle(route):
        req=route.request;url=urlsplit(req.url)
        if url.hostname!='consent.test':route.fulfill(status=200,body='');return
        if fail and fail.get('enabled') and url.path=='/crm/marketing-consent/api':route.fulfill(status=503,json={'detail':'Consent storage is unavailable.'});return
        response=v.client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
            headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    page.route('**/*',handle)


def test_dashboard_record_verify_history_filters_and_mobile(consent):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=consent;failure={'enabled':False}
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));serve(page,v,failure)
            page.goto('https://consent.test/crm/marketing-consent')
            api.expect(page.locator('#marketing-rows tr')).to_have_count(2)
            row=page.locator('#marketing-rows tr').filter(has_text='Quick customer 1');row.get_by_role('button',name='View details').click()
            content=page.locator('#marketing-consent-content')
            api.expect(content.locator('.marketing-status')).to_have_text('NOT REQUESTED')
            page.get_by_label('Source description',exact=True).fill('Signed affirmative customer form')
            page.get_by_label('When the customer agreed (your local time)',exact=True).fill('2026-01-01T10:00')
            page.get_by_label('Exact consent wording identifying PixiePinks and WhatsApp promotions',exact=True).fill('I agree to PixiePinks WhatsApp promotions. <script>never-executed</script>')
            page.get_by_label('Wording version',exact=True).fill('fixture-v1')
            page.get_by_label('Evidence reference (secure internal reference or HTTPS URL)',exact=True).fill('signed-form:fixture')
            page.get_by_label('I verified affirmative permission for PixiePinks WhatsApp promotions',exact=True).check()
            page.get_by_label('Administrative reason',exact=True).fill('Verified the original form.')
            page.get_by_role('button',name='Record / correct consent',exact=True).click()
            api.expect(content.locator('.marketing-status')).to_have_text('OPTED IN')
            page.get_by_role('button',name='View history',exact=True).click()
            api.expect(content).to_contain_text('CONSENT VERIFIED')
            assert content.locator('script').count()==0
            page.get_by_label('Customer withdrawal evidence / reason',exact=True).fill('Customer explicitly requested STOP by telephone.')
            page.get_by_role('button',name='Mark opted out',exact=True).click()
            api.expect(content.locator('.marketing-status')).to_have_text('OPTED OUT')
            page.locator('#marketing-consent-close').click()
            page.locator('[name="status"]').select_option('OPTED_OUT');page.locator('#marketing-search button').click()
            api.expect(page.locator('#marketing-rows tr')).to_have_count(1)
            page.locator('[name="q"]').fill('no matching customer');page.locator('#marketing-search button').click()
            api.expect(page.locator('#marketing-dashboard-status')).to_have_text('No matching consent contacts.')
            for width in [320,390,430]:
                page.set_viewport_size({'width':width,'height':812});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            failure['enabled']=True;page.locator('#marketing-search button').click()
            api.expect(page.locator('#marketing-dashboard-status')).to_have_text('Consent storage is unavailable.')
            assert not errors and not v.sends
        finally:browser.close()


def test_mobile_inbox_request_is_previewed_then_sent_through_existing_route(consent):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=consent
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});serve(page,v)
            page.goto(f'https://consent.test/crm/whatsapp?conversation={v.ids[0]}')
            page.locator('#mobile-chat-menu').click();page.locator('#details-action').click()
            page.locator('#marketing-consent-from-details').click()
            api.expect(page.locator('#marketing-consent-dialog')).to_be_visible()
            api.expect(page.locator('#marketing-consent-content .marketing-status')).to_have_text('NOT REQUESTED')
            page.get_by_label('Consent request type',exact=True).select_option('text')
            page.get_by_role('button',name='Request consent — preview',exact=True).click()
            api.expect(page.locator('#marketing-consent-content')).to_contain_text('WhatsApp හි Reply')
            assert not v.sends
            page.get_by_role('button',name='Send reviewed consent request',exact=True).click();assert not v.sends
            page.get_by_label('I reviewed this exact request for this customer.',exact=True).check()
            page.get_by_role('button',name='Send reviewed consent request',exact=True).click()
            api.expect(page.locator('#marketing-consent-content .marketing-status')).to_have_text('PENDING')
            assert len(v.sends)==1
            assert page.locator('#marketing-consent-dialog').bounding_box()['width']<=390
            with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING' and db.query(Event).filter_by(event_type='CONSENT_REQUESTED').count()==1
        finally:browser.close()


def test_response_review_mobile_desktop_and_audited_approval(consent):
    from test_marketing_consent import prepare,send_request,incoming
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=consent;preview=prepare(v,'si').json();assert send_request(v,preview).status_code==200;incoming(v,'Yes')
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));serve(page,v)
            page.goto('https://consent.test/crm/marketing-consent')
            page.get_by_label('Needs Staff Review',exact=True).check();page.locator('#marketing-search button').click()
            api.expect(page.locator('#marketing-rows tr')).to_have_count(1)
            page.get_by_role('button',name='View details').click()
            section=page.locator('.marketing-review')
            api.expect(section).to_contain_text('Customer response has no WhatsApp Reply reference.')
            api.expect(section).to_contain_text('PixiePinks WhatsApp Updates')
            api.expect(section).to_contain_text('Yes')
            page.screenshot(path='/tmp/phase1b-desktop.png')
            for width in [320,360,390,430]:
                page.set_viewport_size({'width':width,'height':812})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert section.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            page.screenshot(path='/tmp/phase1b-mobile.png')
            page.get_by_label('Review outcome',exact=True).select_option('approve')
            page.get_by_label('Review decision reason',exact=True).fill('Verified original request and explicit customer agreement.')
            page.get_by_role('button',name='Save review decision',exact=True).click()
            api.expect(page.locator('#marketing-consent-feedback')).to_contain_text('Explicit verification confirmation is required.')
            page.get_by_label('I verified that this customer explicitly agreed to receive PixiePinks WhatsApp promotional messages.',exact=True).check()
            page.get_by_role('button',name='Save review decision',exact=True).click()
            api.expect(page.locator('#marketing-consent-content .marketing-status')).to_have_text('OPTED IN')
            with v.sessions() as db:assert db.query(Event).filter_by(event_type='CONSENT_VERIFIED').count()==1
            assert len(v.sends)==1 and not errors
        finally:browser.close()


def test_default_consent_buttons_preview_and_explicit_send_mobile_desktop(consent,monkeypatch):
    import main
    from models import MarketingConsentRequest
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=consent;calls=[]
    monkeypatch.setattr(main.settings,'META_APP_SECRET','fixture-secret')
    monkeypatch.setattr(main,'send_whatsapp_consent_buttons',lambda *args:calls.append(args) or 'wamid.preview-buttons')
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});errors=[];page.on('pageerror',lambda error:errors.append(str(error)));serve(page,v)
            page.goto(f'https://consent.test/crm/whatsapp?conversation={v.ids[0]}')
            # Open the existing mobile Details entry, then verify the modal at both sizes.
            page.locator('#mobile-chat-menu').click();page.locator('#details-action').click()
            page.locator('#marketing-consent-from-details').click()
            api.expect(page.get_by_label('Consent request type',exact=True)).to_have_value('buttons')
            api.expect(page.get_by_label('Consent request language',exact=True)).to_have_value('si')
            page.get_by_role('button',name='Request consent — preview',exact=True).click()
            api.expect(page.locator('.marketing-button-preview')).to_contain_text('Yes, I agree')
            api.expect(page.locator('.marketing-button-preview')).to_contain_text('No, thanks')
            assert not calls and not v.sends
            page.set_viewport_size({'width':1366,'height':900});page.locator('.marketing-button-preview').scroll_into_view_if_needed();page.screenshot(path='/tmp/phase1c-desktop.png')
            for width in [320,360,390,430]:
                page.set_viewport_size({'width':width,'height':812})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert page.locator('.marketing-button-preview').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            page.locator('.marketing-button-preview').scroll_into_view_if_needed()
            page.screenshot(path='/tmp/phase1c-mobile.png')
            page.get_by_role('button',name='Send reviewed consent request',exact=True).click();assert not calls
            page.get_by_label('I reviewed this exact request for this customer.',exact=True).check()
            page.get_by_role('button',name='Send reviewed consent request',exact=True).click()
            api.expect(page.locator('#marketing-consent-content .marketing-status')).to_have_text('PENDING')
            assert len(calls)==1 and not v.sends and not errors
            with v.sessions() as db:assert db.query(MarketingConsentRequest).one().wording_version.startswith('pixiepinks-wa-marketing-v3-buttons-si')
        finally:browser.close()
