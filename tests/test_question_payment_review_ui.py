import shutil
from urllib.parse import urlsplit
import pytest
import customer_questions as q
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting
from test_question_payment_followup import paying
from test_question_payment_review import reviewing
from test_question_payment_ui import serve


def test_staff_verification_form_and_confirmation_desktop_mobile(reviewing):
    _,v,url,calls=reviewing
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:serve(v.client,route))
        page.goto(f'https://question.test/crm/whatsapp?conversation={v.ids[0]}')
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.get_by_role('button',name='Create order from confirmed details',exact=True).click()
        form=page.locator('.payment-review-form');form.wait_for()
        page.wait_for_function('document.querySelector(".payment-review-form select")?.disabled===true')
        assert page.get_by_label('Actual amount received (LKR)',exact=True).input_value()=='46800.00'
        page.get_by_label('Bank transaction reference',exact=True).fill('UI-BANK-001')
        page.get_by_label('I checked this transaction in the bank account',exact=True).check()
        # Incoming Inbox refreshes must not erase typed bank details.
        page.locator('#inbox').evaluate("el=>el.dispatchEvent(new CustomEvent('inbox-conversation-data',{detail:{conversation:{id:"+str(v.ids[0])+"}}}))")
        page.get_by_label('Bank transaction reference',exact=True).wait_for()
        assert page.get_by_label('Bank transaction reference',exact=True).input_value()=='UI-BANK-001'
        assert page.get_by_label('I checked this transaction in the bank account',exact=True).is_checked()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/staff-payment-review-mobile.png',full_page=True)
        page.get_by_role('button',name='Verify payment & queue confirmation',exact=True).click()
        page.locator('.payment-review').get_by_text('Bank payment verified by',exact=False).wait_for()
        assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions) and len(calls)==1
        page.get_by_role('button',name='Close',exact=True).click()
        page.get_by_role('button',name='More message actions',exact=True).click()
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.payment-review').get_by_text('Customer confirmation: SENT',exact=False).wait_for()
        page.set_viewport_size({'width':1366,'height':1000})
        page.screenshot(path='/tmp/staff-payment-verified-desktop.png',full_page=True)
        assert not errors
        browser.close()
