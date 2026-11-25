"""Real Chromium, synthetic CRM/POS data and mocked Shopify only."""
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from models import Customer,SalesOrder,JournalEntry,WhatsAppConversation
from loyalty_models import LoyaltyMember
from test_loyalty import loyalty,inbox
from test_loyalty_browser import browser
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos

@pytest.mark.parametrize('width',[390,1280])
def test_inbox_suggestions_quick_register_and_attach(loyalty,width,monkeypatch):
    from playwright.sync_api import expect
    import main
    v=loyalty
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).first();c.phone_number='96550012345';c.customer_name='New overseas contact';conversation=c.id;db.commit()
    product={'id':'gid://shopify/Product/1','title':'School bag','status':'ACTIVE','published':True,'image':None,'variants':[{'id':'gid://shopify/ProductVariant/1','title':'Pink','sku':'1001','price':'4500','available':True}]}
    monkeypatch.setattr(main,'search_products_for_inbox',lambda *args:{'products':[product],'has_more':False})
    with browser(v,width) as (page,errors,calls):
        page.goto('https://loyalty.test/crm/whatsapp?conversation='+str(conversation));expect(page.locator('#chat-phone')).to_have_text('96550012345')
        with v.sessions() as db:assert db.query(Customer).count()==1 and db.query(LoyaltyMember).count()==0
        if width<600:page.get_by_role('button',name='More message actions').click()
        page.locator('#create-order-button').click();expect(page.locator('#order-loyalty-search')).to_have_value('96550012345')
        page.locator('#order-identity-confirm').check();page.locator('#order-register-customer').click();expect(page.locator('#order-registration-feedback')).to_contain_text('Customer registered and selected')
        page.locator('#order-add-product').click();page.locator('#order-product-results button').filter(has_text='Add').click()
        page.locator('.order-drawer-body').evaluate('(element)=>element.scrollTop=0')
        page.screenshot(path=f'/tmp/registration-crm-{width}.png',full_page=True)
        page.locator('#save-order').click();page.wait_for_url('**/crm/sales-orders/*')
        with v.sessions() as db:
            c=db.query(Customer).filter_by(name='New overseas contact').one();order=db.query(SalesOrder).order_by(SalesOrder.id.desc()).first()
            assert order.customer_id==c.id and order.crm_conversation_id==conversation and c.registration_source=='CRM'
            assert db.query(JournalEntry).count()==0 and db.query(LoyaltyMember).count()==0 and not order.shopify_order_id
        page.goto('https://loyalty.test/customers');page.screenshot(path=f'/tmp/registration-management-{width}.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
    assert not v.sends


def test_pos_register_attach_and_walkin_reset_without_leaving_billing(pos):
    from playwright.sync_api import sync_playwright,expect
    from pos_models import POSCart
    r=pos
    with sync_playwright() as runtime:
        b=runtime.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1280,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;u=urlsplit(req.url)
            response=r.client.request(req.method,u.path+('?' +u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}})
            h.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k=='content-type'})
        page.route('https://register.test/**',route);page.goto('https://register.test/crm/pos');expect(page.locator('#pos-status')).to_contain_text('Register open')
        page.locator('#pos-customer').click();modal=page.locator('#pos-modal');modal.get_by_label('Optional name',exact=True).fill('POS registration buyer');modal.get_by_label('Optional international customer mobile',exact=True).fill('+971501234567');modal.get_by_label('I have consent to save this operational contact',exact=True).check()
        modal.get_by_role('button',name='Create new CRM customer',exact=True).click();expect(modal).to_contain_text('Customer registered and selected')
        page.screenshot(path='/tmp/registration-pos-desktop.png',full_page=True)
        with r.sessions() as db:
            c=db.query(Customer).filter_by(name='POS registration buyer').one();cart=db.query(POSCart).filter_by(status='DRAFT').one();assert cart.payload['customer_id']==c.id and c.registration_source=='POS'
        modal.get_by_role('button',name='Use walk-in (no customer profile)',exact=True).click();modal.get_by_role('button',name='Save sale details',exact=True).click();expect(modal).not_to_be_visible()
        page.locator('#pos-customer').click();expect(page.locator('#pos-modal').get_by_label('Optional name',exact=True)).to_have_value('')
        with r.sessions() as db:assert db.query(POSCart).filter_by(status='DRAFT').one().payload['customer_id'] is None
        assert not errors and not r.pos.calls
        b.close()


def test_late_pos_registration_response_cannot_attach_to_next_cart(pos):
    from playwright.sync_api import sync_playwright,expect
    from pos_models import POSCart
    r=pos
    with sync_playwright() as runtime:
        b=runtime.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page()
        def route(h):
            req=h.request;u=urlsplit(req.url);response=r.client.request(req.method,u.path+('?' +u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}});h.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k=='content-type'})
        page.route('https://late-register.test/**',route);page.goto('https://late-register.test/crm/pos');expect(page.locator('#pos-status')).to_contain_text('Register open')
        page.evaluate("""()=>{const original=window.fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/api/customers')&&args[1]?.method==='POST')return new Promise(resolve=>{window.releaseRegistration=()=>resolve(response);});return response;};}""")
        page.locator('#pos-customer').click();modal=page.locator('#pos-modal');modal.get_by_label('Optional name',exact=True).fill('Delayed customer contact');modal.get_by_label('Optional international customer mobile',exact=True).fill('+96550012345');modal.get_by_label('I have consent to save this operational contact',exact=True).check();modal.get_by_role('button',name='Create new CRM customer',exact=True).click();page.wait_for_function('typeof window.releaseRegistration==="function"')
        modal.get_by_role('button',name='Close',exact=True).click();page.locator('#pos-clear').click();expect(page.locator('#pos-status')).to_contain_text('Unsent cart cancelled')
        page.locator('#pos-customer').click();page.evaluate('window.releaseRegistration()');expect(page.locator('#pos-status')).to_contain_text('active cart changed')
        with r.sessions() as db:
            assert db.query(Customer).filter_by(name='Delayed customer contact').count()==1
            assert all(c.payload['customer_id'] is None for c in db.query(POSCart))
            assert db.query(LoyaltyMember).count()==0
        assert not r.pos.calls
        b.close()


def test_late_crm_registration_cannot_attach_to_reopened_order(loyalty):
    from playwright.sync_api import expect
    v=loyalty
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).first();c.phone_number='96550012345';c.customer_name='Delayed inquiry buyer';conversation=c.id;db.commit()
    with browser(v,1280) as (page,errors,calls):
        page.goto('https://loyalty.test/crm/whatsapp?conversation='+str(conversation))
        expect(page.locator('#chat-phone')).to_have_text('96550012345')
        page.evaluate("""()=>{const original=window.fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/api/customer-registration/register'))return new Promise(resolve=>{window.releaseCRMRegistration=()=>resolve(response);});return response;};}""")
        page.locator('#create-order-button').click();page.locator('#order-identity-confirm').check();page.locator('#order-register-customer').click()
        page.wait_for_function('typeof window.releaseCRMRegistration==="function"')
        page.locator('#close-order').click();page.locator('#create-order-button').click()
        page.evaluate('window.releaseCRMRegistration()')
        expect(page.locator('#order-loyalty-status')).to_contain_text('order details changed')
        expect(page.locator('#order-loyalty-customer')).to_have_value('')
        with v.sessions() as db:
            assert db.query(Customer).filter_by(name='Delayed inquiry buyer').count()==1
            assert db.query(SalesOrder).count()==0 and db.query(JournalEntry).count()==0
        assert not errors
