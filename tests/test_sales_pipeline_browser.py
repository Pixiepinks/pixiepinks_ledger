"""Real Chromium with synthetic TestClient routes; no network or live customers."""
from types import SimpleNamespace
import pytest
from test_sales_pipeline import fixture,inbox
from test_loyalty_browser import browser
import sales_pipeline as s

@pytest.mark.parametrize('width',[1648,1920,1366,412])
def test_board_commitment_cancel_filter_and_mobile(fixture,width):
    from playwright.sync_api import expect
    client,_,_,_,_=fixture
    with browser(SimpleNamespace(client=client),width) as (page,errors,calls):
        page.set_viewport_size({'width':width,'height':{1648:928,1920:1080,1366:768,412:915}[width]})
        page.goto('https://loyalty.test/crm/sales-pipeline');expect(page.locator('.pl-card')).to_have_count(1);assert page.locator('.pl-column').count()==5
        page.locator('.pl-card').first.click();expect(page.locator('.pl-profile')).to_be_visible()
        page.get_by_role('button',name='Set Payment Date').click();f=page.locator('#pl-edit form');expect(f).to_be_visible()
        page.locator('#pl-edit-cancel').click();assert not any(method=='POST' for method,path in calls if path.startswith('/crm/sales-pipeline'))
        page.get_by_role('button',name='Set Payment Date').click();f.locator('[name=precision]').select_option('EXACT');f.locator('[name=payment_date]').fill(str(s.today()));f.locator('[name=agreed]').fill('1200.00');f.get_by_role('button',name='Save commitment').click();expect(page.locator('#pl-feedback')).to_contain_text('Commitment saved')
        assert page.locator('[data-stage=PAYMENT] .pl-card').count()==1
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if width==412:expect(page.locator('#pl-details')).to_have_class('mobile-open')
        assert not errors,errors
