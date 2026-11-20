"""Browser PPA flow, no Windows print calls or live financial operations."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from test_pos_provisional_receipts import snapshot
from test_pos_receipts_phase4g import text
from models import JournalEntry
from hardware_models import HardwareJob
import pos_provisional_receipts as provisional


def test_pending_sale_status_and_history_use_distinct_confirmed_provisional_path(pos):
    r=pos;rid=checkout(r);before=snapshot(r,rid);root=Path(__file__).parents[1];pdfs=[];paths=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1400,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script('window.open=()=>null;sessionStorage.setItem("pixiepinks-pos-sale",'+json.dumps(rid)+');')
        def route(request):
            path=request.request.url.removeprefix('https://pos.test');paths.append(path)
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():request.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                if path.endswith('/provisional/pdf'):
                    assert json.loads(request.request.post_data)=={'confirm_received':True}
                    pdfs.append(response.content)
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://pos.test/crm/pos')
        primary=page.get_by_role('button',name='Print Provisional Receipt (80 mm)',exact=True);primary.wait_for()
        assert page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).count()==0
        page.once('dialog',lambda dialog:dialog.dismiss());primary.click();assert not pdfs
        page.once('dialog',lambda dialog:dialog.accept());primary.click()
        page.get_by_role('link',name='Open provisional PDF',exact=True).wait_for()
        assert 'PROVISIONAL — FINAL CONFIRMATION PENDING' in ' '.join(text(pdfs[0]).split())
        # Polling must not erase the download links while a cashier is printing.
        page.wait_for_timeout(2200)
        assert page.get_by_role('link',name='Download provisional PDF',exact=True).count()==1
        page.screenshot(path='/workspace/scratch/pos-provisional-browser.png',full_page=True)
        page.get_by_role('button',name='Close',exact=True).click();page.locator('#pos-history').click()
        page.get_by_role('button',name='Provisional receipt / status',exact=True).first.click()
        page.once('dialog',lambda dialog:dialog.accept());page.get_by_role('button',name='Print Provisional Receipt (80 mm)',exact=True).click()
        page.get_by_role('link',name='Open provisional PDF',exact=True).wait_for()
        assert len(pdfs)==2 and 'COPY / REPRINT' in text(pdfs[1])
        assert not any('/receipt/pdf' in path or '/api/print-agent/' in path or '/hardware/' in path for path in paths)
        assert not errors;browser.close()
    assert snapshot(r,rid)==before and not r.pos.calls and not r.remote.mutations
    with r.sessions() as db:assert db.query(HardwareJob).count()==0 and db.query(JournalEntry).count()==0


def test_designated_test_sale_has_no_provisional_ui(pos,monkeypatch):
    rid=checkout(pos)
    with pos.sessions() as db:
        from pos_models import POSCheckout
        ref=db.get(POSCheckout,rid).reference
    monkeypatch.setattr(provisional,'TEST_REFERENCES',{ref})
    response=pos.client.get('/crm/pos/api/sales/'+rid)
    assert response.status_code==200 and response.json()['provisional_receipt']['allowed'] is False
