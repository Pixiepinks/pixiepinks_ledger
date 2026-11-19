"""Real authenticated agent protocol into TestClient; Windows and Shopify mocked."""
from pathlib import Path
from uuid import uuid4
import httpx,pytest
from fastapi.testclient import TestClient
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos
from test_hardware import hardware,finish_sale
from test_print_agent import Backend
from windows_print_agent.transport import Transport,AgentError
from windows_print_agent.store import Store
from windows_print_agent.engine import Engine
import hardware_service as h
from hardware_models import HardwareJob
from pos_models import POSCheckout
import main


def test_real_protocol_prints_receipt_and_drawer_once_without_financial_replay(hardware,tmp_path):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions)
    native=TestClient(main.app,base_url='https://native.test');seen=[]
    def handler(request):
        assert 'cookie' not in request.headers
        seen.append(request.url.path)
        response=native.request(request.method,request.url.path,content=request.content,headers=dict(request.headers))
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    client=httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False)
    transport=Transport('https://native.test',r.token,client)
    proof={'device_id':r.did,'normal_drawers_none':True,'media_and_cutter_tested':True,'drawer_queue_isolated':True,'drawer_driver_tested':True,'drawer_no_replay_tested':True,'fingerprints':{name:'verified-'+name for name in [h.RECEIPT_QUEUE,h.LABEL_QUEUE,h.DRAWER_QUEUE]}}
    backend=Backend();store=Store(tmp_path/'actions.sqlite');engine=Engine(transport,backend,store,proof,r.did)
    engine.cycle();engine.cycle();engine.cycle()
    assert [c[0] for c in backend.calls]==[h.RECEIPT_QUEUE,h.DRAWER_QUEUE]
    assert len(r.pos.calls)==1 and not r.remote.mutations
    assert any(p.endswith('/progress') for p in seen)
    with r.sessions() as db:
        assert {j.status for j in db.query(HardwareJob)}=={'SUBMITTED_TO_PRINTER'}
        assert db.get(POSCheckout,rid).status=='COMPLETE'
    store.close();native.close();client.close()


def test_pdf_stream_is_bounded_and_integrity_checked(monkeypatch):
    import windows_print_agent.transport as module
    import hashlib
    raw=b'%PDF-1.7\ncontents';jid=str(uuid4())
    def handler(request):return httpx.Response(200,headers={'content-type':'application/pdf'},content=raw)
    client=httpx.Client(transport=httpx.MockTransport(handler));t=Transport('https://native.test','device-secret',client)
    job={'id':jid,'claim':'one-time-claim','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    assert t.pdf(job)==raw
    with pytest.raises(AgentError):t.pdf({**job,'sha256':'0'*64})
    monkeypatch.setattr(module,'MAX_PDF',4)
    with pytest.raises(AgentError):t.pdf(job)
    client.close()


def test_hardware_ui_media_flow_and_existing_scanner(hardware):
    from playwright.sync_api import sync_playwright
    r=hardware;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(request):
            path=request.request.url.removeprefix('https://hardware.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():request.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://hardware.test/crm/pos');page.get_by_role('button',name='Hardware: Connected',exact=False).wait_for()
        page.locator('#hardware-status').click();page.get_by_role('heading',name='PixiePinks Main Counter PC',exact=True).wait_for()
        card=page.locator('.hardware-device').first;card.get_by_label('Installed media',exact=True).select_option('LABEL');card.get_by_label('I inspected installed media and both DBL queues are empty',exact=True).check();card.get_by_role('button',name='Confirm media change',exact=True).click()
        page.get_by_text('UNKNOWN → LABEL',exact=False).wait_for();page.get_by_role('button',name='Close hardware',exact=True).click()
        page.locator('#pos-scan').fill('P1');page.locator('#pos-scan').press('Enter');page.locator('.pos-line').wait_for()
        page.locator('#pos-scan').fill('P1');page.locator('#pos-scan').press('Enter');page.wait_for_function("document.querySelector('.pos-line input[type=number]').value==='2'")
        page.screenshot(path='/workspace/scratch/phase4f-hardware-desktop.png',full_page=True)
        assert not errors and not r.pos.calls
        browser.close()
