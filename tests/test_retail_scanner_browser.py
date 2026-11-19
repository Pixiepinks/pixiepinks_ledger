"""Chromium exercises real scanner JS with intercepted offline API responses."""
import json
from pathlib import Path
import pytest
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail


def test_scanner_browser(retail):
    from playwright.sync_api import sync_playwright
    r=retail;html=r.client.get('/crm/retail/scanner').text
    root=Path(__file__).parents[1]
    row={'id':1,'version':1,'title':'Kids Projector Torch <script>alert(1)</script>','variant_title':'Pink / Small','price':'600.00','currency':'LKR','sku':'1001200','local_barcode':'P1','barcode':'','effective_barcode':'P1','options':[{'name':'Colour','value':'Pink'}],'status':'ACTIVE','issues':[],'missing':False,'synced_at':'2026-11-19T00:00:00Z','product_id':'gid://shopify/Product/1','variant_id':'gid://shopify/ProductVariant/1'}
    scans=[];delayed=[];stock={'status':'UNAVAILABLE','available':None,'reason':'INVENTORY_SCOPE_UNAVAILABLE'}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':1000})
        def route(request):
            url=request.request.url
            if url=='https://scanner.test/crm/retail/scanner':request.fulfill(body=html,content_type='text/html');return
            if '/static/' in url:
                path=root/'static'/url.split('/static/',1)[1].split('?')[0]
                if path.is_file():request.fulfill(path=str(path));return
            if '/api/scan?' in url:
                from urllib.parse import urlparse,parse_qs
                code=parse_qs(urlparse(url).query)['code'][0];scans.append(code)
                if code=='SLOW':delayed.append(request);return
                rows=[row,{**row,'id':2,'variant_title':'Blue / Large'}] if code=='SHARED' else [row] if code in {'P1','0001'} else []
                request.fulfill(json={'total':len(rows),'rows':rows});return
            if '/inventory?' in url:request.fulfill(json=stock);return
            if '/api/scanner/products/' in url:request.fulfill(json={'row':row});return
            request.fulfill(body='',status=404)
        page.route('**/*',route);page.goto('https://scanner.test/crm/retail/scanner')
        field=page.locator('#scanner-input');field.fill('SHARED');field.press('Enter')
        page.locator('.scanner-choice').first.wait_for();assert page.locator('.scanner-choice').count()==2
        assert page.locator('#scanner-result').is_hidden()
        page.locator('.scanner-choice').first.click();page.get_by_role('heading',name=row['title'],exact=True).wait_for()
        assert 'Stock quantity unavailable' in page.locator('.scanner-stock').inner_text()
        assert page.locator('#scanner-result script').count()==0
        assert 'Rs. 600.00' in page.locator('#scanner-result').inner_text()
        page.screenshot(path='/tmp/scanner-desktop.png',full_page=True)
        page.get_by_role('button',name='Scan next',exact=True).click();field.fill('0001');field.press('Enter');page.get_by_role('heading',name=row['title'],exact=True).wait_for();assert scans[-1]=='0001'
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250);page.screenshot(path='/tmp/scanner-mobile.png',full_page=True)
        assert page.locator('#barcode-scanner').evaluate('(e)=>e.scrollWidth <= e.clientWidth+1')
        field.fill('SLOW');field.press('Enter');page.wait_for_timeout(50);field.fill('P1');field.press('Enter');page.get_by_role('heading',name=row['title'],exact=True).wait_for()
        assert delayed
        delayed.pop().fulfill(json={'total':0,'rows':[]});page.wait_for_timeout(50)
        assert page.locator('#scanner-result').is_visible() and 'No matching' not in page.locator('#scanner-status').inner_text()
        page.get_by_role('button',name='Clear / Scan next').click();page.locator('#scanner-ending').select_option('tab');field.fill('UNKNOWN');field.press('Tab');page.wait_for_function("document.getElementById('scanner-status').textContent.includes('No matching')")
        assert scans[-1]=='UNKNOWN'
        field.fill('P1');field.press('Enter');page.get_by_role('heading',name=row['title'],exact=True).wait_for()
        stock.update(status='VERIFIED',available=0,locations=[{'location':{'name':'Colombo','isActive':True},'available':0}],read_at='2026-11-19T00:00:00Z',valid_for_seconds=.1)
        page.get_by_role('button',name='Refresh stock').click();page.get_by_text('0 available',exact=True).wait_for()
        assert 'Colombo: 0' in page.locator('.scanner-stock').inner_text()
        page.wait_for_function("document.querySelector('.scanner-stock').textContent.includes('Stock check expired')")
        browser.close()
