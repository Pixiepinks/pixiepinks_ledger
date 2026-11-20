"""Encrypted app tunnel through untrusted Railway HTTP; only synthetic printing."""
import copy,json,secrets,time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from test_print_agent import Backend
from models import User,JournalEntry,SalesPayment
from pos_models import POSSession,POSCheckout
from hardware_models import HardwareJob,HardwareEvent,PrintDevice,PrintConnectorNonce
from settings import settings
import hardware_service as h
from windows_print_agent import envelope as wire
from windows_print_agent.transport import EncryptedTransport,AgentError
from windows_print_agent.engine import Engine
from windows_print_agent.store import Store
from windows_print_agent.windows import WindowsPrinters


@pytest.fixture
def connector(pos,monkeypatch,tmp_path):
    r=pos;r.packets=[]
    monkeypatch.setattr(settings,'POS_LOCAL_PRINT_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',True)
    monkeypatch.setattr(settings,'RETAIL_HARDWARE_LABEL_PRINT_ENABLED',True)
    monkeypatch.setattr(settings,'PRINT_CONNECTOR_ENCRYPTED_ENABLED',True)
    monkeypatch.setattr(settings,'PRINT_CONNECTOR_PRIVATE_KEY',secrets.token_hex(32))
    monkeypatch.setattr(settings,'PRINT_AGENT_PUBLIC_ORIGIN','https://crm.test')
    monkeypatch.setattr(settings,'PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED',False)
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS','')
    r.pin=wire.public_hex(settings.PRINT_CONNECTOR_PRIVATE_KEY)
    with r.sessions() as db:
        d,code=h.enroll(db,db.get(User,r.uid),db.get(POSSession,r.sid).register_id)
        h.configure(db,db.get(User,r.uid),d.id,{**h.DEFAULT,'receipt_only':True})
        r.did=d.id;r.code=code
    def handle(request):
        r.packets.append(request.content)
        response=r.client.post('/api/print-connector/envelope',content=request.content,headers={'content-type':'application/json','x-forwarded-proto':'https','x-forwarded-for':'attacker-controlled'})
        return httpx.Response(response.status_code,content=response.content,headers={'content-type':'application/json'})
    r.connector=EncryptedTransport('https://crm.test',r.pin,http=httpx.Client(transport=httpx.MockTransport(handle)))
    r.credential=r.connector.pair(code)
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'activate')
    r.backend=Backend();r.store=Store(tmp_path/'actions.sqlite')
    proof={'device_id':r.did,'normal_drawers_none':True,'media_and_cutter_tested':True,'fingerprints':{h.RECEIPT_QUEUE:'verified-'+h.RECEIPT_QUEUE}}
    r.engine=Engine(r.connector,r.backend,r.store,proof,r.did)
    with r.sessions() as db:h.request_mode(db,db.get(User,r.uid),r.did,'RECEIPT',True)
    yield r
    r.store.close()


def rpc_packet(r,method='config',credential=None,**changes):
    message={'timestamp':int(time.time()),'origin':'https://crm.test','method':method,'credential':r.credential if credential is None else credential,'body':{}}
    message.update(changes);return wire.seal_request(r.pin,message)


def send(r,packet,ctx,headers=None):
    response=r.client.post('/api/print-connector/envelope',json=packet,headers=headers or {})
    if response.status_code!=200:return response.status_code,None
    opened=wire.open_response(ctx,response.json());return opened['status'],opened['value']


def pilot(r):
    with r.sessions() as db:
        j=h.printer_test(db,db.get(User,r.uid),r.did,uuid4().hex);db.commit();jid=j.id
    r.engine.cycle()
    with r.sessions() as db:
        assert db.get(HardwareJob,jid).status=='SUBMITTED_TO_PRINTER'
        h.review_job(db,db.get(User,r.uid),jid,True,'Synthetic physical confirmation in mocked test only',False)
        h.configure(db,db.get(User,r.uid),r.did,{**h.DEFAULT,'receipt_only':True,'auto_receipts':True})
        h.request_mode(db,db.get(User,r.uid),r.did,'RECEIPT',True)
    return jid


def test_pinned_transport_on_untrusted_http_keeps_legacy_tls_gate(connector):
    r=connector;packet,ctx=rpc_packet(r);status,value=send(r,packet,ctx,{'x-forwarded-proto':'https'})
    assert status==200 and value['id']==r.did and value['config']['receipt_only']
    assert r.client.get('/api/print-agent/config',headers={'x-forwarded-proto':'https','authorization':'Bearer '+r.credential}).status_code==403
    assert send(r,packet,ctx)[0]==409
    assert r.credential not in r.packets[-1].decode() and r.code not in r.packets[0].decode()
    assert 'request_key' not in repr(ctx)
    response=r.client.post('/api/print-connector/envelope',json=rpc_packet(r)[0],headers={'origin':'https://crm.test'})
    assert response.status_code==403


@pytest.mark.parametrize('change,expected',[
 ({'credential':'invalid'},401),({'timestamp':0},401),({'origin':'https://other.test'},403),
 ({'method':'create_order'},422),({'method':'claim','body':{'kind':'DRAWER'}},422)])
def test_invalid_auth_freshness_origin_and_unlisted_methods(connector,change,expected):
    packet,ctx=rpc_packet(connector,**change)
    assert send(connector,packet,ctx)[0]==expected
    with connector.sessions() as db:assert db.query(HardwareJob).count()==0


def test_wrong_pin_tamper_and_response_replay_are_rejected(connector):
    r=connector;packet,ctx=rpc_packet(r)
    packet['ciphertext']=packet['ciphertext'][:-4]+'AAAA'
    assert send(r,packet,ctx)[0]==401
    wrong=EncryptedTransport('https://crm.test',wire.public_hex(secrets.token_hex(32)),http=r.connector.http)
    with pytest.raises(httpx.HTTPStatusError):wrong.config()
    packet,ctx=rpc_packet(r);response=r.client.post('/api/print-connector/envelope',json=packet)
    _,other=rpc_packet(r)
    with pytest.raises(ValueError):wire.open_response(other,response.json())


def test_pairing_single_use_expiry_rotation_and_revocation(connector):
    r=connector
    with pytest.raises(httpx.HTTPStatusError):r.connector.pair(r.code)
    with r.sessions() as db:
        code=h.manage(db,db.get(User,r.uid),r.did,'rotate')
        d=db.get(PrintDevice,r.did);d.pair_expires=h.now()-timedelta(seconds=1);db.commit()
    with pytest.raises(httpx.HTTPStatusError):r.connector.config()
    with pytest.raises(httpx.HTTPStatusError):r.connector.pair(code)
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'revoke')
    with pytest.raises(httpx.HTTPStatusError):r.connector.pair(code)


def test_durable_server_auto_enqueue_single_job_silent_submit_and_no_drawer(connector):
    r=connector;pilot(r)
    rid=checkout(r);assert h.pos.dispatch_one(r.sessions,r.pos)
    h.tick(r.sessions);h.tick(r.sessions)
    with r.sessions() as db:
        jobs=db.query(HardwareJob).filter_by(checkout_id=rid).all()
        assert len(jobs)==1 and jobs[0].kind=='RECEIPT' and jobs[0].detail['automatic']
        assert jobs[0].queue==h.RECEIPT_QUEUE and jobs[0].business_key=='receipt:auto:'+rid
        assert db.query(JournalEntry).count()==1 and db.query(SalesPayment).count()==1
    r.engine.cycle();r.engine.cycle();h.tick(r.sessions)
    assert len(r.backend.calls)==2 # one synthetic test, one completed sale
    assert all(call[0]==h.RECEIPT_QUEUE for call in r.backend.calls)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE'
        job=db.query(HardwareJob).filter_by(checkout_id=rid).one()
        assert job.status=='SUBMITTED_TO_PRINTER' and job.result['physical_output_verified'] is False
        assert db.query(HardwareJob).filter(HardwareJob.kind.in_(['LABEL','DRAWER'])).count()==0
        assert db.query(JournalEntry).count()==1 and db.query(SalesPayment).count()==1
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_pause_retains_jobs_offline_reconnect_and_controlled_reprint(connector):
    r=connector;pilot(r);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions)
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'pause')
    r.engine.cycle();assert len(r.backend.calls)==1
    with r.sessions() as db:assert db.query(HardwareJob).filter_by(checkout_id=rid).one().attempts==0;h.manage(db,db.get(User,r.uid),r.did,'resume')
    r.backend.offline=True;r.engine.cycle();assert len(r.backend.calls)==1
    r.backend.offline=False;r.engine.cycle();assert len(r.backend.calls)==2
    with r.sessions() as db:
        job=db.query(HardwareJob).filter_by(checkout_id=rid).one()
        with pytest.raises(HTTPException):h.controlled_reprint(db,db.get(User,r.uid),job.id,uuid4().hex,'Original not physically inspected',True)
        h.review_job(db,db.get(User,r.uid),job.id,True,'Physical observation simulated by test',False)
        key=uuid4().hex
        duplicate=h.controlled_reprint(db,db.get(User,r.uid),job.id,key,'Customer explicitly requests a second copy',True);db.commit();duplicate_id=duplicate.id
        again=h.controlled_reprint(db,db.get(User,r.uid),job.id,key,'Customer explicitly requests a second copy',True);db.commit()
        assert again.id==duplicate_id and db.query(HardwareEvent).filter_by(job_id=duplicate_id,action='REPRINT_AUTHORIZED').count()==1
    r.engine.cycle();assert len(r.backend.calls)==3
    from test_pos_receipts_phase4g import text
    assert 'DUPLICATE / REPRINT' in text(r.backend.calls[-1][1])
    with r.sessions() as db:
        assert db.get(HardwareJob,duplicate_id).parent_id==job.id
        assert db.query(JournalEntry).count()==1 and db.query(SalesPayment).count()==1


def test_uncertain_spool_and_network_loss_never_reprint_on_restart(connector):
    r=connector;pilot(r);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions)
    r.backend.error=TimeoutError('mock USB interrupted')
    with pytest.raises(TimeoutError):r.engine.cycle()
    count=len(r.backend.calls);path=r.store.db.execute('PRAGMA database_list').fetchone()[2]
    r.store.close();r.store=Store(path);r.engine.store=r.store;r.backend.error=None
    r.engine.cycle();assert len(r.backend.calls)==count
    with r.sessions() as db:
        job=db.query(HardwareJob).filter_by(checkout_id=rid).one()
        assert job.status=='UNKNOWN' and job.attempts==1
        with pytest.raises(HTTPException):h.controlled_reprint(db,db.get(User,r.uid),job.id,uuid4().hex,'Uncertain action cannot be blindly repeated',True)
        assert db.query(SalesPayment).count()==1 and db.query(JournalEntry).count()==1


def test_pilot_and_queue_separation_permissions_and_unverified_sale(connector):
    r=connector
    with r.sessions() as db:
        u=db.get(User,r.uid)
        with pytest.raises(HTTPException):h.configure(db,u,r.did,{**h.DEFAULT,'receipt_only':True,'auto_receipts':True})
        with pytest.raises(HTTPException):h.configure(db,u,r.did,{**h.DEFAULT,'receipt_only':True,'drawer_queue':h.DRAWER_QUEUE})
        with pytest.raises(HTTPException):h.request_mode(db,u,r.did,'LABEL',True)
        with pytest.raises(HTTPException):h.profile_queue(h.device(db,r.did),'DRAWER','80')
    rid=checkout(r);h.tick(r.sessions)
    with r.sessions() as db:assert db.query(HardwareJob).count()==0 and db.get(POSCheckout,rid).status=='QUEUED'
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='STAFF';user.section_access=['pos.view'];db.commit()
    assert r.client.get('/crm/pos/api/hardware/connector-transport').status_code==403


def test_real_windows_status_reports_paper_out_without_printing():
    printer=object.__new__(WindowsPrinters)
    names=['OFFLINE','ERROR','PAPER_OUT','PAPER_JAM','DOOR_OPEN','PAUSED','NOT_AVAILABLE','USER_INTERVENTION']
    printer.print=SimpleNamespace(**{'PRINTER_STATUS_'+name:1<<index for index,name in enumerate(names)},PRINTER_ATTRIBUTE_WORK_OFFLINE=1<<20)
    printer.info=lambda name:{'Status':printer.print.PRINTER_STATUS_PAPER_OUT,'Attributes':0,'cJobs':0}
    state=printer.status([h.RECEIPT_QUEUE])[0]
    assert state['state']=='ERROR' and state['conditions']==['PAPER_OUT']


def test_actual_80mm_only_validation_and_https_only_client():
    from fpdf import FPDF
    pdf=FPDF(unit='mm',format=(58,60));pdf.add_page();raw=bytes(pdf.output())
    with pytest.raises(AgentError):WindowsPrinters.validate_receipt80(raw)
    with pytest.raises(AgentError):EncryptedTransport('http://crm.test','a'*64)


def test_network_recovery_does_not_create_another_receipt_intent(connector,monkeypatch):
    r=connector;pilot(r);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions)
    original=r.connector.config
    r.connector.config=lambda:(_ for _ in ()).throw(httpx.ConnectError('mock network down'))
    with pytest.raises(httpx.ConnectError):r.engine.cycle()
    assert len(r.backend.calls)==1
    r.connector.config=original;r.engine.cycle();assert len(r.backend.calls)==2
    with r.sessions() as db:assert db.query(HardwareJob).filter_by(checkout_id=rid,kind='RECEIPT').count()==1


def test_nonce_privacy_limits_and_disabled_default(connector,monkeypatch):
    r=connector;packet,ctx=rpc_packet(r)
    response=r.client.post('/api/print-connector/envelope',json=packet)
    assert response.status_code==200 and r.credential not in response.text
    with r.sessions() as db:assert db.get(PrintConnectorNonce,ctx.request_id) is not None
    monkeypatch.setattr(settings,'PRINT_CONNECTOR_ENCRYPTED_ENABLED',False)
    assert r.client.post('/api/print-connector/envelope',json=packet).status_code==404
    monkeypatch.setattr(settings,'PRINT_CONNECTOR_ENCRYPTED_ENABLED',True)
    assert r.client.post('/api/print-connector/envelope',content=b'x'*65537).status_code==413


def test_pinned_client_has_no_redirect_or_proxy_environment_dependency():
    client=EncryptedTransport('https://crm.test',wire.public_hex(secrets.token_hex(32)))
    assert client.http.follow_redirects is False and client.http._trust_env is False
    client.http.close()


@pytest.mark.parametrize('kind,queue,media',[
    ('LABEL',h.LABEL_QUEUE,'LABEL'),('DRAWER',h.DRAWER_QUEUE,'RECEIPT'),
    ('RECEIPT',h.LABEL_QUEUE,'RECEIPT'),('TEST',h.RECEIPT_QUEUE,'A4'),
])
def test_local_receipt_only_engine_rejects_cross_media_before_io(connector,kind,queue,media):
    r=connector
    config=r.connector.config()
    with pytest.raises(AgentError):r.engine.execute({'kind':kind,'queue':queue,'media':media},config,False)
    assert not r.backend.calls and not list(r.store.pending(r.did))


def test_receipt_admin_browser_policy_pause_and_pilot_gate(connector):
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    r=connector;root=Path(__file__).parents[1]
    r.engine.cycle()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(request):
            path=request.request.url.removeprefix('https://receipt.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():request.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(request.request.method,path,content=request.request.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            request.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://receipt.test/crm/pos')
        page.locator('#hardware-status').click()
        page.get_by_role('heading',name='Secure receipt connector transport').wait_for()
        page.get_by_text('Pinned public key (not a secret): '+r.pin,exact=True).wait_for()
        card=page.locator('.hardware-device').first
        assert card.get_by_label('Receipt printer',exact=True).input_value()==h.RECEIPT_QUEUE
        assert card.get_by_label('Installed media',exact=True).locator('option').count()==1
        assert card.get_by_role('button',name='Authorized drawer open / test',exact=True).count()==0
        card.get_by_label('Automatic finalized receipts — enable only after confirmed physical pilot',exact=True).check()
        card.get_by_role('button',name='Save hardware policy',exact=True).click()
        page.get_by_text('Active encrypted receipt connector and physically confirmed current-version synthetic test required before automatic enablement',exact=True).wait_for()
        page.on('dialog',lambda dialog:dialog.accept())
        card.get_by_role('button',name='pause',exact=True).click()
        page.get_by_text('Printing PAUSED — queued jobs retained',exact=True).wait_for()
        card.get_by_role('button',name='resume',exact=True).click()
        page.get_by_text('Printing not paused',exact=True).wait_for()
        assert not errors and not r.pos.calls and not r.backend.calls
        browser.close()


def test_encrypted_response_is_bounded_while_streaming(monkeypatch):
    monkeypatch.setattr(wire,'MAX_RESPONSE',16)
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*100))) as client:
        remote=EncryptedTransport('https://crm.test',wire.public_hex(secrets.token_hex(32)),http=client)
        with pytest.raises(AgentError,match='response too large'):remote.config()
