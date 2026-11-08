import shutil
import pytest
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting
from test_question_payment_followup import paying
from test_question_payment_review import reviewing
from test_prompt_xpress_delivery import dispatching
from test_question_payment_ui import serve


def test_prompt_dispatch_and_delivered_desktop_mobile(dispatching):
    _,v,url,oid,sends=dispatching
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:serve(v.client,route))
        page.goto(f'https://question.test/crm/sales-orders/{oid}')
        page.get_by_role('button',name='Mark packed and ready',exact=True).click()
        number=page.get_by_label('Prompt Xpress tracking number',exact=True);number.wait_for()
        number.fill('MC123')
        assert not number.evaluate('element=>element.checkValidity()')
        number.fill('MC12345678')
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function("document.querySelector('#sidebar').getBoundingClientRect().right<=1")
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#prompt-xpress-delivery').screenshot(path='/tmp/prompt-xpress-mobile.png',animations='disabled')
        page.get_by_role('button',name='Dispatch & send tracking',exact=True).click()
        page.get_by_role('button',name='Mark Delivered',exact=True).wait_for()
        assert page.get_by_role('link',name='Track on Prompt Xpress',exact=True).get_attribute('href')=='https://www.promptxpress.lk/TrackItem.aspx#'
        assert len(sends)==1 and 'MC12345678' in sends[0][1]
        page.get_by_role('button',name='Mark Delivered',exact=True).click()
        page.locator('#prompt-xpress-delivery').get_by_text('Order: DELIVERED · Shipment: DELIVERED',exact=True).wait_for()
        page.set_viewport_size({'width':1366,'height':1000})
        page.locator('#prompt-xpress-delivery').screenshot(path='/tmp/prompt-xpress-delivered.png')
        assert not errors and len(sends)==1
        browser.close()
