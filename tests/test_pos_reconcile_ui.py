from pathlib import Path
from playwright.sync_api import sync_playwright
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
import pos_service as s
from pos_models import POSOperation,POSPosting,POSCheckout


def test_reconciliation_label_explains_local_writes_and_requires_confirmation(pos):
    r=pos;rid=checkout(r);r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos);root=Path(__file__).parents[1];posts=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page()
        page.add_init_script('sessionStorage.setItem("pixiepinks-pos-sale",'+repr(rid)+')')
        def route(request):
            path=request.request.url.removeprefix('https://pos.test')
            if path.startswith('/static/'):
                file=root/path.split('?',1)[0].lstrip('/')
                if file.is_file():request.fulfill(path=str(file));return
            if path.startswith('/crm/pos'):
                if request.request.method=='POST':posts.append(path)
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404)
        page.route('**/*',route);page.goto('https://pos.test/crm/pos')
        button=page.get_by_role('button',name='Verify Shopify & finalize locally (no resend)',exact=True);button.wait_for()
        page.get_by_text('SHOPIFY_READBACK / SHOPIFY_READBACK_UNAVAILABLE',exact=False).wait_for()
        page.get_by_label('Evidence notes — reconciliation may post local accounting').fill('Synthetic authorized local recovery')
        messages=[]
        def dismiss(dialog):messages.append(dialog.message);dialog.dismiss()
        page.on('dialog',dismiss);button.click();assert not any('/reconcile' in path for path in posts)
        assert 'post local accounting' in messages[-1]
        page.remove_listener('dialog',dismiss);page.on('dialog',lambda dialog:dialog.accept());r.pos.fail_read=False;button.click()
        page.get_by_role('button',name='Print Receipt (80 mm)',exact=True).wait_for()
        with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==1
        assert len(r.pos.calls)==1
        browser.close()
