"""Real desktop/mobile Chromium, local mocked catalogue; no real customers or orders."""
import copy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import contextmanager
import pytest
from models import WhatsAppConversation,SalesOrder,SalesPayment,JournalEntry
from loyalty_models import *
from test_loyalty import loyalty,inbox,source,offer,enabled
import loyalty_service as svc

@contextmanager
def browser(v,width=1280):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        b=runtime.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':width,'height':900});errors=[];calls=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;url=urlsplit(req.url)
            if url.hostname!='loyalty.test':h.fulfill(status=200,body='');return
            calls.append((req.method,url.path))
            response=v.client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
            h.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k in {'content-type','cache-control','location'}})
        page.route('**/*',route)
        try:yield page,errors,calls
        finally:b.close()

@pytest.mark.parametrize('width',[390,1280])
def test_management_pages_bulk_offer_preview_and_history(loyalty,width):
    from playwright.sync_api import expect
    v=loyalty;source(v)
    with v.sessions() as db:svc.refresh_member(db,v.cid);db.commit()
    with browser(v,width) as (page,errors,calls):
        for key,title in [('dashboard','Dashboard'),('members','Loyalty Members'),('products','Loyalty Products'),('redemptions','Redemption History'),('migration','Historical Customer Migration'),('reports','Reports'),('settings','Settings')]:
            page.goto('https://loyalty.test/crm/loyalty/'+key)
            expect(page.locator('main.loyalty h1')).to_have_text(title)
            expect(page.locator('.loyalty-state')).to_have_text('Programme OFF')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),key
        page.goto('https://loyalty.test/crm/loyalty/products');page.locator('#loyalty-product-query').fill('School');page.locator('#loyalty-product-search').click();page.locator('#loyalty-product-results input[type=checkbox]').first.check()
        form=page.locator('#loyalty-rule-form');form.locator('[name=method]').select_option('PRICE');form.locator('[name=value]').fill('4000');form.locator('[name=cost]').fill('2000');form.locator('[name=cost_reference]').fill('Verified supplier bill');form.locator('[name=cost_verified_at]').fill(datetime.utcnow().isoformat());page.locator('#loyalty-rule-preview').click();expect(page.locator('#loyalty-rule-calculation')).to_contain_text('4000.00')
        with v.sessions() as db:assert db.query(LoyaltyRule).count()==0
        form.locator('[name=enabled]').check();form.get_by_role('button',name='Save selected variants').click();expect(page.locator('.loyalty-existing-rule')).to_contain_text('PRICE 4000.00')
        with v.sessions() as db:assert not svc.config(db).enabled and db.query(LoyaltyRule).count()==1 and db.query(SalesOrder).count()==1 and db.query(JournalEntry).count()==0
        page.goto('https://loyalty.test/crm/loyalty/migration');page.get_by_role('button',name='Preview historical customers',exact=True).click();expect(page.locator('#loyalty-job-detail')).to_contain_text('eligible customers')
        page.goto('https://loyalty.test/crm/loyalty/members');page.locator('main.loyalty a').filter(has_text='PP-L-').first.click();expect(page.locator('main.loyalty h1')).to_contain_text('Existing buyer')
        page.screenshot(path=f'/tmp/loyalty-member-{width}.png',full_page=True)
        page.goto('https://loyalty.test/crm/loyalty/dashboard');page.screenshot(path=f'/tmp/loyalty-dashboard-{width}.png',full_page=True)
        assert not errors,errors
        assert not v.sends


def test_crm_existing_customer_server_quote_and_saved_draft(loyalty,monkeypatch):
    from playwright.sync_api import expect
    import main
    v=loyalty;source(v);offer(v,method='PRICE',value='4000');enabled(v)
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).order_by(WhatsAppConversation.id).first();c.phone_number='94771234567';c.customer_name='Existing buyer';cid=c.id;db.commit()
    product={'id':'gid://shopify/Product/1','title':'School bag','status':'ACTIVE','published':True,'image':'https://cdn.shopify.com/test.jpg','url':'https://test.invalid/item','variants':[{'id':'gid://shopify/ProductVariant/1','title':'Pink','sku':'1001','price':'4500','available':True}]}
    monkeypatch.setattr(main,'search_products_for_inbox',lambda *args:{'products':[copy.deepcopy(product)],'has_more':False})
    with browser(v,1280) as (page,errors,calls):
        page.goto(f'https://loyalty.test/crm/whatsapp?conversation={cid}');expect(page.locator('#chat-phone')).to_have_text('94771234567');page.locator('#create-order-button').click()
        page.locator('#order-customer-search summary').click();page.locator('#order-loyalty-search').fill('Existing buyer');page.wait_for_function('document.getElementById("order-loyalty-customer").options.length>1');page.locator('#order-loyalty-customer').select_option(str(v.cid));page.locator('#order-select-customer').click()
        page.locator('#order-add-product').click();page.locator('#order-product-results button').filter(has_text='Add').click()
        expect(page.locator('#order-loyalty-status')).to_contain_text('Server quote: Rs. 4,000.00')
        expect(page.locator('#order-preview-totals')).to_contain_text('Rs. 4,000.00')
        page.locator('#save-order').click();page.wait_for_url('**/crm/sales-orders/*')
        with v.sessions() as db:
            order=db.query(SalesOrder).order_by(SalesOrder.id.desc()).first();assert order.customer_id==v.cid and order.grand_total==4000 and order.order_status=='DRAFT'
            assert db.query(LoyaltyPricing).count()==1 and db.query(LoyaltyRedemption).count()==0 and db.query(JournalEntry).count()==0
        assert not errors,errors
        assert not v.sends
