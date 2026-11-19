from pathlib import Path
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos


def test_cashier_scan_shared_picker_hold_and_responsive(pos):
    from playwright.sync_api import sync_playwright
    r=pos;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1500,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(request):
            path=request.request.url.removeprefix('https://pos.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():request.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://pos.test/crm/pos');page.locator('.pos-product').first.wait_for()
        page.locator('#pos-scan').fill('P1');page.locator('#pos-scan').press('Enter');page.locator('.pos-line').wait_for()
        page.locator('#pos-scan').fill('P1');page.locator('#pos-scan').press('Enter')
        page.wait_for_function("document.querySelector('.pos-line input[type=number]').value==='2'")
        page.locator('#pos-scan').fill('SHARED');page.locator('#pos-scan').press('Enter');page.get_by_text('Shared barcode — choose colour or size.').wait_for()
        assert page.locator('#pos-modal .pos-product').count()==2
        page.get_by_role('button',name='Close',exact=True).click()
        page.locator('#pos-hold').click();page.get_by_text('Sale held on the server.').wait_for()
        page.locator('#pos-held').click();page.locator('#pos-modal-content button').first.click();page.locator('.pos-line').wait_for()
        for width,name in [(1500,'desktop'),(1024,'tablet'),(390,'mobile')]:
            page.set_viewport_size({'width':width,'height':1000});page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(250);page.screenshot(path='/tmp/pos-'+name+'.png',full_page=True)
            assert page.locator('#pos').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        assert not errors and not r.pos.calls
        browser.close()
