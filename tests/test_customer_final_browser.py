from contextlib import contextmanager
from urllib.parse import urlsplit
import json
import pytest
from models import Customer,WhatsAppConversation,User
from pos_models import POSCart
from test_loyalty import loyalty,inbox
from test_loyalty_browser import browser
from test_customer_registration_ui_fix import open_order
from test_pos import pos,inv
from test_retail_phase1 import retail


@pytest.mark.parametrize('width',[390,1280])
def test_single_crm_suggestion_is_not_attached_until_confirmed(loyalty,width):
    from playwright.sync_api import expect
    with browser(loyalty,width) as (page,errors,calls):
        open_order(page,loyalty,number='94771234567',width=width)
        expect(page.locator('#order-loyalty-customer')).to_have_value(str(loyalty.cid))
        expect(page.locator('#order-customer-confirmed')).to_have_value('')
        expect(page.locator('#order-lookup-status')).to_contain_text('suggested')
        expect(page.get_by_role('button',name='Confirm & attach customer')).to_be_visible()
        page.locator('.order-drawer-body').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=f'/tmp/phase2b2-crm-suggestion-{width}.png',full_page=True)
        page.locator('#order-select-customer').click()
        expect(page.locator('#order-customer-confirmed')).to_have_value(str(loyalty.cid))
        expect(page.locator('#order-customer-name')).to_have_value('Existing buyer')
        expect(page.locator('#order-registration-feedback')).to_contain_text('confirmed')
        page.locator('#order-customer-name').fill('Changed purchaser entry')
        expect(page.locator('#order-customer-confirmed')).to_have_value('')
        page.locator('#order-select-customer').click()
        expect(page.locator('#order-customer-confirmed')).to_have_value(str(loyalty.cid))
        expect(page.locator('#order-customer-name')).to_have_value('Existing buyer')
        assert not errors


@contextmanager
def pos_browser(r,width=1280):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;u=urlsplit(req.url)
            if u.hostname!='pos-final.test':h.fulfill(status=200,body='');return
            response=r.client.request(req.method,u.path+('?' +u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}})
            h.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k in {'content-type','cache-control'}})
        page.route('**/*',route)
        try:yield page,errors
        finally:b.close()


def seed(r):
    with r.sessions() as db:
        c=Customer(name='Distinct counter buyer',phone='+96550012345');db.add(c);db.commit();return c.id


def attach(page):
    from playwright.sync_api import expect
    page.locator('#pos-customer').click();modal=page.locator('#pos-modal')
    modal.get_by_label('Search customer by mobile or name').fill('96550012345');modal.get_by_role('button',name='Search customers',exact=True).click()
    modal.get_by_role('button').filter(has_text='Distinct counter buyer').click()
    modal.get_by_role('button',name='Save & attach customer',exact=True).click();expect(modal).not_to_be_visible()


@pytest.mark.parametrize('state,expected',[('ACTIVE','Active loyalty member'),('PENDING_FIRST_PURCHASE','pending first qualifying purchase'),('SUSPENDED','suspended'),('INELIGIBLE','ineligible'),('UNAVAILABLE','temporarily unavailable')])
def test_pos_membership_summary_only_uses_server_verified_state(pos,state,expected):
    from playwright.sync_api import expect
    r=pos;cid=seed(r)
    with pos_browser(r) as (page,errors):
        def summary(route):
            if state=='UNAVAILABLE':route.abort('failed');return
            with r.sessions() as db:c=db.query(POSCart).filter_by(status='DRAFT').one();cartid=c.id;version=c.version
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'cart_id':cartid,'cart_version':version,'customer':{'id':cid,'name':'Distinct counter buyer','phone':'+96550012345','membership_id':'SYNTHETIC-MEMBER' if state in {'ACTIVE','SUSPENDED'} else None,'membership_status':state,'qualifying_purchases':1 if state=='ACTIVE' else 0}}))
        page.route('**/api/carts/*/customer',summary);page.goto('https://pos-final.test/crm/pos');attach(page)
        expect(page.locator('#pos-customer-summary')).to_contain_text(expected)
        if state!='UNAVAILABLE':expect(page.locator('#pos-customer-summary')).to_contain_text('Distinct counter buyer')
        else:expect(page.locator('#pos-customer-summary')).not_to_contain_text('Active loyalty member')
        assert not errors and not r.pos.calls


@pytest.mark.parametrize('width',[390,1280])
def test_pos_consent_summary_refresh_remove_and_new_cart(pos,width):
    from playwright.sync_api import expect
    r=pos;cid=seed(r)
    with pos_browser(r,width) as (page,errors):
        page.goto('https://pos-final.test/crm/pos');attach(page)
        expect(page.locator('#pos-customer-summary')).to_contain_text('Distinct counter buyer')
        page.reload();expect(page.locator('#pos-customer-summary')).to_contain_text('Distinct counter buyer')
        page.screenshot(path=f'/tmp/phase2b2-pos-summary-{width}.png',full_page=True)
        page.locator('#pos-customer').click();modal=page.locator('#pos-modal');consent=modal.get_by_label('I have consent to save this operational contact (not marketing consent)',exact=True)
        expect(consent).not_to_be_checked();assert consent.bounding_box()['height']<=20
        text_rect=consent.evaluate('(e)=>{const t=[...e.parentElement.childNodes].find(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim());const r=document.createRange();r.selectNode(t);const b=r.getBoundingClientRect();return {x:b.x,y:b.y};}')
        assert consent.bounding_box()['x']+16<=text_rect['x']+1 and consent.bounding_box()['y']<=text_rect['y']+5
        assert not modal.locator('details').evaluate('(e)=>e.open')
        page.screenshot(path=f'/tmp/phase2b2-pos-modal-{width}.png',full_page=True)
        modal.get_by_role('button',name='Close',exact=True).click();page.locator('#pos-remove-customer').click()
        expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        with r.sessions() as db:c=db.query(POSCart).filter_by(status='DRAFT').one();assert c.payload['customer_id'] is None and c.payload['phone']==''
        page.locator('#pos-clear').click();page.reload();expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        assert not errors and not r.pos.calls


def test_pos_cart_summary_is_private_bounded_and_owner_scoped(pos):
    from test_pos import newcart
    import pos_service as service
    r=pos;cid=seed(r);cartid,version=newcart(r)
    with r.sessions() as db:
        c=service.get_cart(db,db.get(User,r.uid),cartid)
        service.edit_cart(db,db.get(User,r.uid),cartid,c.version,'details',{'customer_id':cid,'customer_name':'Distinct counter buyer','phone':'+96550012345','invoice_details':'','order_discount':c.payload['order_discount'],'discount_reason':c.payload['discount_reason']},r.pos)
    response=r.client.get('/crm/pos/api/carts/'+cartid+'/customer')
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    assert set(response.json()['customer'])=={'id','name','phone','membership_id','membership_status','qualifying_purchases'}
    with r.sessions() as db:
        u=db.query(User).filter_by(username='quick-agent').one();u.section_access=['pos.view','pos.sell'];db.commit()
    r.client.post('/login',data={'username':'quick-agent','password':'password'})
    assert r.client.get('/crm/pos/api/carts/'+cartid+'/customer').status_code==403
    assert not r.pos.calls


def test_held_cart_preserves_its_identity_while_new_cart_is_walkin(pos):
    from playwright.sync_api import expect
    r=pos;cid=seed(r)
    with pos_browser(r) as (page,errors):
        page.goto('https://pos-final.test/crm/pos');attach(page);page.locator('#pos-products .pos-product').first.click()
        expect(page.locator('.pos-line')).to_be_visible();page.locator('#pos-hold').click()
        expect(page.locator('#pos-status')).to_contain_text('held on the server')
        page.locator('#pos-new').click();expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        page.locator('#pos-clear').click();page.locator('#pos-held').click();page.locator('#pos-modal-content button').first.click()
        expect(page.locator('#pos-customer-summary')).to_contain_text('Distinct counter buyer')
        with r.sessions() as db:assert db.query(POSCart).filter_by(status='DRAFT').one().payload['customer_id']==cid
        assert not errors and not r.pos.calls


def test_delayed_pos_summary_cannot_leak_into_a_new_cart(pos):
    from playwright.sync_api import expect
    r=pos;seed(r)
    with pos_browser(r) as (page,errors):
        page.goto('https://pos-final.test/crm/pos')
        page.evaluate("""()=>{const original=fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/customer'))return new Promise(resolve=>{window.releaseSummary=()=>resolve(response);});return response;};}""")
        attach(page);page.wait_for_function('typeof window.releaseSummary==="function"')
        page.locator('#pos-clear').click();expect(page.locator('#pos-status')).to_contain_text('cancelled');page.locator('#pos-new').click()
        page.evaluate('window.releaseSummary()');expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        with r.sessions() as db:assert db.query(POSCart).filter_by(status='DRAFT').one().payload['customer_id'] is None
        assert not errors and not r.pos.calls


def test_completed_mock_checkout_and_new_sale_do_not_reuse_customer(pos):
    from playwright.sync_api import expect
    from pos_models import POSCheckout
    from models import SalesOrder
    import pos_service as service
    r=pos;cid=seed(r)
    with pos_browser(r) as (page,errors):
        page.goto('https://pos-final.test/crm/pos');attach(page);page.locator('#pos-products .pos-product').first.click()
        page.locator('#pos-checkout').click();page.get_by_role('button',name='Confirm tender & checkout',exact=True).click()
        expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        assert service.dispatch_one(r.sessions,r.pos)
        with r.sessions() as db:
            checkout=db.query(POSCheckout).one();assert checkout.status=='COMPLETE' and db.get(SalesOrder,checkout.sales_order_id).customer_id==cid
        page.get_by_role('button',name='Close',exact=True).click();page.locator('#pos-new').click()
        expect(page.locator('#pos-customer-summary')).to_contain_text('Unidentified walk-in')
        with r.sessions() as db:assert db.query(POSCart).filter_by(status='DRAFT').one().payload['customer_id'] is None
        assert not errors and len(r.pos.calls)==1
