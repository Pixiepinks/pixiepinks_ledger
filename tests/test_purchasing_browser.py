"""Exercise the real purchasing frontend against disposable API fixtures."""
from pathlib import Path
from test_purchasing import purchase,inv,retail,inbox

def test_purchase_receiving_browser(purchase):
    from playwright.sync_api import sync_playwright
    r=purchase;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;path=req.url.removeprefix('https://purchase.test')
            if path.startswith('/static/'):
                file=root/path.split('?',1)[0].lstrip('/')
                if file.is_file():h.fulfill(path=str(file));return
            if path.startswith('/crm/retail/'):
                res=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                h.fulfill(status=res.status_code,body=res.content,content_type=res.headers.get('content-type','text/plain'));return
            h.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://purchase.test/crm/retail/purchases');page.get_by_text('Inventory supplier',exact=False).first.wait_for()
        page.get_by_role('button',name='New purchase',exact=True).click();page.locator('#product-search input').fill('Stand Fan');page.locator('#product-search button').click();page.get_by_role('button',name='Add',exact=True).first.click()
        page.locator('#bill-form input[name=invoice]').fill('BROWSER-ISSUE200');page.locator('#bill-form input[name=date]').fill('2026-11-22');page.locator('#bill-lines input[data-field=quantity]').fill('20');page.locator('#bill-lines input[data-field=unit_cost]').fill('100')
        page.get_by_role('button',name='Save draft for review',exact=True).click();page.locator('#purchase-detail').wait_for();page.locator('#close-detail').click()
        page.get_by_role('button',name='Submit for review',exact=True).wait_for();page.on('dialog',lambda d:d.accept());page.get_by_role('button',name='Submit for review',exact=True).click();page.get_by_role('button',name='Approve bill',exact=True).click();page.get_by_role('button',name='Receive goods',exact=True).click()
        page.locator('#receipt-form input[name=delivery_reference]').fill('DELIVERY-BROWSER-1');page.locator('#receipt-lines input[data-field=accepted]').fill('8');page.locator('#receipt-form button').click();page.get_by_role('button',name='Submit receipt',exact=True).click();page.get_by_role('button',name='Approve received stock',exact=True).click()
        page.locator('#receipt-list').get_by_text('APPROVED',exact=False).wait_for();assert not r.remote.mutations and not errors
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/purchasing-mobile.png',full_page=True)
        browser.close()
