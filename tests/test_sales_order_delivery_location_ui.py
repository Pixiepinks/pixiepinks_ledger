import shutil
import pytest
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting
from test_question_payment_followup import paying
from test_question_payment_review import reviewing
from test_sales_order_delivery_location import editing
from test_question_payment_ui import serve


def test_staff_edit_city_district_on_confirmed_paid_order_desktop_mobile(editing):
    _,v,oid,_=editing
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:serve(v.client,route))
        page.goto(f'https://question.test/crm/sales-orders/{oid}')
        page.get_by_role('button',name='Edit delivery details',exact=True).click()
        city=page.get_by_label('City (optional)',exact=True);city.wait_for()
        page.wait_for_function('document.querySelector("#delivery-location-dialog [type=submit]")?.disabled===false')
        assert not city.get_attribute('required')
        city.fill('Homagama');page.get_by_label('District (optional)',exact=True).fill('Colombo')
        page.get_by_label('Postal code (optional)',exact=True).fill('10200')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path='/tmp/staff-delivery-location-mobile.png',full_page=True)
        page.get_by_role('button',name='Save delivery details',exact=True).click()
        page.wait_for_function('!document.querySelector("#delivery-location-dialog")?.open')
        page.locator('dt').filter(has_text='City').locator('xpath=following-sibling::dd[1]').get_by_text('Homagama',exact=True).wait_for()
        assert page.locator('dt').filter(has_text='District').locator('xpath=following-sibling::dd[1]').inner_text()=='Colombo'
        page.set_viewport_size({'width':1366,'height':1000})
        page.screenshot(path='/tmp/staff-delivery-location-desktop.png',full_page=True)
        assert not errors
        browser.close()
