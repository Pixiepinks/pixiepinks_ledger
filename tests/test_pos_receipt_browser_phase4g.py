"""Chromium preview behavior only; no real printing or external transactions."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
import pos_service as service
from pos_models import POSAudit
from hardware_models import HardwareJob

SCRIPT=Path(__file__).parents[1]/'static'/'pos_receipt_print.js'


def test_preview_popup_and_pdf_errors_are_recoverable():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('https://receipt.test/',lambda route:route.fulfill(body='<main id="receipt"></main>',content_type='text/html'))
        page.goto('https://receipt.test/');page.add_script_tag(path=str(SCRIPT))
        page.evaluate("window.open=()=>null;window.PixiePinksReceiptPrint.mount({saleId:'sale-1',csrf:'test-csrf',container:document.querySelector('main')})")
        calls=[]
        def failed(route):calls.append(route.request.post_data_json);route.fulfill(status=409,json={'detail':'Verified COMPLETE sale required'})
        page.route('**/receipt/pdf',failed);page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click()
        page.get_by_text('Verified COMPLETE sale required',exact=True).wait_for();assert not page.locator('a').count()
        page.unroute('**/receipt/pdf')
        page.route('**/receipt/pdf',lambda route:route.fulfill(status=200,body='<html>login</html>',content_type='text/html'))
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_text('The server did not return a valid receipt PDF.',exact=False).wait_for()
        page.unroute('**/receipt/pdf');page.route('**/receipt/pdf',lambda route:route.abort('failed'))
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_text('Receipt preview unavailable. Check your connection',exact=False).wait_for()
        page.unroute('**/receipt/pdf')
        def success(route):
            calls.append(route.request.post_data_json);assert route.request.headers['x-csrf-token']=='test-csrf'
            route.fulfill(body=b'%PDF-1.7\nmock browser document',content_type='application/pdf')
        page.route('**/receipt/pdf',success)
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_role('link',name='Open receipt PDF',exact=True).wait_for()
        page.get_by_text('Receipt PDF ready. If the preview was blocked or closed',exact=False).wait_for()
        assert page.get_by_role('link',name='Download receipt PDF').get_attribute('download').endswith('80.pdf')
        page.get_by_label('Receipt format',exact=True).select_option('58');page.get_by_role('button',name='Preview selected format').click();page.get_by_role('link',name='Download receipt PDF').get_attribute('href')
        page.wait_for_function("document.querySelector('a[download]').download.endsWith('58.pdf')")
        assert calls[-1]=={'profile':'58','refund_id':None}
        page.evaluate("window.popups=[];window.open=()=>{const popup={opener:window,closed:false,document:{body:{}},location:{replace:url=>window.popupURL=url},close(){this.closed=true}};window.popups.push(popup);return popup};void 0")
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_text('Receipt PDF opened. Physical printing is not confirmed.',exact=True).wait_for()
        state=page.evaluate('({count:window.popups.length,detached:window.popups[0].opener===null,url:window.popupURL})');assert state['detached'] and state['url'].startswith('blob:'),state
        assert not errors;browser.close()


def test_complete_sale_history_browser_printing_without_agent(pos):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos);root=Path(__file__).parents[1];pdfs=[];paths=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1400,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script('window.open=()=>null;sessionStorage.setItem("pixiepinks-pos-sale",'+json.dumps(rid)+');')
        def route(request):
            path=request.request.url.removeprefix('https://pos.test');paths.append(path)
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():request.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                if path.endswith('/receipt/pdf'):
                    assert request.request.headers['x-csrf-token']==r.csrf;pdfs.append(response.content)
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://pos.test/crm/pos')
        page.locator('#pos-history').click();page.get_by_role('button',name='Open receipt / status').first.click();page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).wait_for()
        assert page.get_by_label('Receipt format',exact=True).input_value()=='80'
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_role('link',name='Open receipt PDF',exact=True).wait_for()
        page.screenshot(path='/workspace/scratch/phase4g-browser-receipt.png',full_page=True)
        page.get_by_role('button',name='Close',exact=True).click();page.locator('#pos-history').click();page.get_by_role('button',name='Open receipt / status').first.click()
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).click();page.get_by_role('link',name='Open receipt PDF',exact=True).wait_for()
        from test_pos_receipts_phase4g import text
        assert len(pdfs)==2 and 'DUPLICATE / REPRINT' not in text(pdfs[0]) and 'DUPLICATE / REPRINT' in text(pdfs[1])
        with r.sessions() as db:
            assert db.query(HardwareJob).count()==0
            assert db.query(POSAudit).filter_by(action='RECEIPT_GENERATED').count()==2
        assert not any('/api/print-agent/' in path or '/hardware/devices/' in path for path in paths)
        assert not errors and len(r.pos.calls)==1 and not r.remote.mutations
        browser.close()
