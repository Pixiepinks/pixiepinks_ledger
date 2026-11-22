"""Purchasing ERP UI exercised against real local APIs and fake Shopify."""
from pathlib import Path
from contextlib import contextmanager
from test_purchasing import purchase,inv,retail,inbox,make_bill,receive,bill_data

@contextmanager
def browser(r):
    from playwright.sync_api import sync_playwright
    root=Path(__file__).parents[1]
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1366,'height':768});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;path=req.url.removeprefix('https://purchase.test')
            if path.startswith('/static/'):
                f=root/path.split('?',1)[0].lstrip('/')
                if f.is_file():h.fulfill(path=str(f));return
            if path.startswith('/crm/retail/'):
                res=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                h.fulfill(status=res.status_code,body=res.content,content_type=res.headers.get('content-type','text/plain'));return
            if req.url.startswith('https://cdn.shopify.com/'):
                h.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#f5eef3"/><circle cx="32" cy="23" r="16" fill="none" stroke="#667085" stroke-width="3"/><path d="M32 40v15M22 56h20M20 12l24 23M20 35l24-23" stroke="#667085" stroke-width="3"/></svg>');return
            h.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://purchase.test/crm/retail/purchases');page.locator('#bill-list').get_by_text('No purchase bills',exact=False).wait_for() if not r.existing_bill else page.locator('#bill-list article').first.wait_for()
        try:yield page,errors
        finally:b.close()

def enter(page,r):
    page.locator('nav [data-pane=new]').click();page.locator('#supplier-search').fill('Inventory');page.locator('#bill-form select[name=supplier_id]').select_option(str(r.supplier));page.locator('#bill-form select[name=location_id]').select_option('gid://shopify/Location/1');page.locator('#bill-form input[name=invoice]').fill('BROWSER-ISSUE200');page.locator('#product-query').fill('Stand Fan');page.locator('#find-products').click();page.get_by_role('button',name='Add',exact=True).first.click();page.locator('#bill-lines input[data-field=quantity]').fill('20');page.locator('#bill-lines input[data-field=unit_cost]').fill('100');page.locator('#calculation-state').get_by_text('backend verified',exact=False).wait_for()

def test_purchase_receiving_browser(purchase):
    r=purchase;r.existing_bill=False
    with browser(r) as (page,errors):
        enter(page,r)
        assert page.locator('#invoice-totals').inner_text().count('2,000.00')>=1
        assert page.locator('#bill-lines tr').count()==1
        page.get_by_role('button',name='Added',exact=True).first.wait_for()
        assert page.get_by_role('button',name='Added',exact=True).first.is_disabled()
        page.locator('#bill-form input[name=discount]').fill('5');page.locator('#bill-form select[name=discount_type]').select_option('PERCENT');page.locator('#bill-lines input[data-field=tax_rate]').fill('18')
        page.locator('#invoice-totals').get_by_text('LKR 2,242.00',exact=True).first.wait_for()
        page.get_by_role('button',name='Preview invoice',exact=True).click();page.locator('#purchase-detail').get_by_text('UNSAVED PREVIEW',exact=True).wait_for();Path('/tmp/purchasing-ui').mkdir(exist_ok=True);page.screenshot(path='/tmp/purchasing-ui/invoice-review.png');page.locator('#close-detail').click()
        page.get_by_role('button',name='Save and Submit for Review',exact=True).click();page.locator('#purchase-detail').get_by_text('Status: REVIEW',exact=True).wait_for();page.locator('#close-detail').click()
        page.on('dialog',lambda d:d.accept());page.get_by_role('button',name='Approve bill',exact=True).click();page.get_by_role('button',name='Receive goods',exact=True).click();page.locator('#receipt-form input[name=delivery_reference]').fill('DELIVERY-BROWSER-1');page.locator('#receipt-lines input[data-field=accepted]').fill('8');page.locator('#receipt-form button').click();page.get_by_role('button',name='Submit receipt',exact=True).click();page.get_by_role('button',name='Approve received stock',exact=True).click();page.locator('#receipt-list').get_by_text('APPROVED',exact=False).wait_for()
        assert not r.remote.mutations and not errors

def test_purchase_validation_preserves_cart_and_screenshots(purchase):
    r=purchase;r.existing_bill=False
    with browser(r) as (page,errors):
        enter(page,r);page.locator('#bill-lines input[data-field=discount]').fill('3000');page.locator('#calculation-state').get_by_text('Discount exceeds',exact=False).wait_for();page.get_by_role('button',name='Save Draft',exact=True).click();page.locator('#purchase-message').get_by_text('Discount exceeds',exact=False).wait_for();assert page.locator('#bill-lines tr').count()==1
        page.locator('#bill-lines input[data-field=discount]').fill('50');page.locator('#bill-form input[name=charges]').fill('100');page.locator('#bill-form input[name=charge_tax]').fill('18');page.locator('#calculation-state').get_by_text('backend verified',exact=False).wait_for()
        out=Path('/tmp/purchasing-ui');out.mkdir(exist_ok=True)
        page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(out/'purchase-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.wait_for_function("document.querySelector('#sidebar').getBoundingClientRect().right <= 1");page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(out/'purchase-mobile.png'),full_page=True)
        assert page.locator('#purchasing').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), page.evaluate('({viewport:innerWidth,body:document.body.scrollWidth,root:document.querySelector("#purchasing").getBoundingClientRect().toJSON()})')
        assert not errors
        confirmations=[]
        page.on('dialog',lambda d:(confirmations.append(d.message),d.accept()))
        page.locator('#cancel-entry').click();assert confirmations and page.locator('#bill-lines tr').count()==0 and page.locator('#product-list').inner_text()==''

def test_empty_destination_warns_and_admin_setup_link(purchase):
    from purchasing_models import PurchaseRecord
    r=purchase;r.existing_bill=False
    with r.sessions() as db:
        for row in db.query(PurchaseRecord).filter_by(kind='LOCATION'):row.payload={**row.payload,'enabled':False}
        db.commit()
    with browser(r) as (page,errors):
        page.locator('nav [data-pane=new]').click();page.locator('#destination-warning').wait_for();Path('/tmp/purchasing-ui').mkdir(exist_ok=True);page.evaluate('window.scrollTo(0,0)');page.screenshot(path='/tmp/purchasing-ui/destination-setup.png');assert page.locator('#configure-locations').is_visible();assert page.locator('#bill-form select[name=location_id]').is_disabled();page.locator('#configure-locations').click();page.locator('#locations').wait_for();assert not errors

def test_receiving_remaining_quantities(purchase):
    r=purchase;bid=make_bill(r);receive(r,bid,8);r.existing_bill=True
    with browser(r) as (page,errors):
        page.get_by_role('button',name='Receive goods',exact=True).click();row=page.locator('#receipt-lines tr').first
        assert row.locator('td').nth(1).inner_text()=='20' and row.locator('td').nth(2).inner_text()=='8' and row.locator('td').nth(3).inner_text()=='12'
        assert row.locator('input[data-field=accepted]').get_attribute('max')=='12';assert not errors

def test_scanner_and_duplicate_submit_do_not_create_duplicate_bill(purchase):
    from purchasing_models import PurchaseRecord,PurchaseMovement
    r=purchase;r.existing_bill=False
    with browser(r) as (page,errors):
        enter(page,r);page.locator('#barcode-input').fill('P1');page.locator('#barcode-input').press('Enter');page.get_by_role('button',name='Added',exact=True).wait_for();assert page.locator('#bill-lines tr').count()==1
        page.evaluate("const f=document.querySelector('#bill-form');f.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}));f.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}));")
        page.locator('#purchase-detail').get_by_text('Status: DRAFT',exact=True).wait_for()
        with r.sessions() as db:assert db.query(PurchaseRecord).filter_by(kind='BILL').count()==1 and db.query(PurchaseMovement).count()==0
        assert not errors and not r.remote.mutations

def test_staff_cannot_configure_destinations(purchase):
    from models import User
    r=purchase;r.existing_bill=False
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.purchases.view','retail.purchases.prepare'];db.commit()
    with browser(r) as (page,errors):
        page.locator('nav [data-pane=sync]').click();assert page.locator('#locations').is_hidden()
        assert page.locator('#configure-locations').is_hidden() and not errors
