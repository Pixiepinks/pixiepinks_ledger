from pathlib import Path
from uuid import uuid4
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv


def test_inventory_ui_offline_receiving_and_stock(inv):
    from playwright.sync_api import sync_playwright
    r=inv;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        def route(request):
            url=request.request.url
            if url.startswith('https://inventory.test/'):
                path=url.removeprefix('https://inventory.test')
                if path.startswith('/static/'):
                    local=root/path.split('?',1)[0].lstrip('/')
                    if local.is_file():request.fulfill(path=str(local));return
                elif path.startswith('/crm/retail/'):
                    response=r.client.request(request.request.method,path,content=request.request.post_data,
                        headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                    request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://inventory.test/crm/retail/inventory')
        page.get_by_text('Stand Fan 1',exact=True).wait_for();page.locator('#inventory-location').select_option('gid://shopify/Location/1')
        first=page.locator('#inventory-products tr').first
        first.get_by_role('button',name='Refresh stock',exact=True).click();page.get_by_text('8 available',exact=True).wait_for();assert '10 on hand' in first.inner_text()
        page.evaluate('window.scrollTo(0,0)');page.screenshot(path='/tmp/inventory-phase3-desktop.png',full_page=True)
        first.get_by_role('button',name='Add to document').click();page.get_by_role('button',name='Receiving & adjustments',exact=True).click()
        page.locator('#inventory-supplier').select_option(str(r.supplier));page.locator('#inventory-invoice').fill('BROWSER-'+uuid4().hex[:8])
        page.get_by_role('button',name='Save document / start count',exact=True).click()
        page.get_by_role('button',name='Submit for approval',exact=True).wait_for();page.get_by_role('button',name='Submit for approval',exact=True).click()
        page.get_by_role('button',name='Approve exact transaction',exact=True).wait_for()
        panel=page.locator('.inventory-document').first;heading=panel.locator('h3').inner_text();reference=heading.split(' · ')[0]
        panel.get_by_role('textbox',name='Confirm exact document reference '+reference).fill(reference)
        panel.get_by_role('button',name='Approve exact transaction',exact=True).click();page.get_by_text('APPROVED',exact=True).wait_for()
        assert r.remote.mutations==[]
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250);page.get_by_role('button',name='Stock overview',exact=True).click()
        page.screenshot(path='/tmp/inventory-phase3-mobile.png',full_page=True)
        assert page.locator('#inventory').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        browser.close()
