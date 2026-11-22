"""Phase22 uses mocked Shopify, canonical accounting and real encrypted/journal APIs."""
import copy,io
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from test_receipt_connector import connector
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,newcart,cash,checkout
from models import User,JournalEntry,SalesPayment
from pos_models import POSCheckout,POSOperation,POSPosting,POSPilotScope,POSPilotAuthorization
from hardware_models import HardwareJob,HardwareEvent,PrintDevice
from settings import settings
from windows_print_agent.store import Store
import pos_real_sale_pilot as pilot
import pos_service as service
import hardware_service as h


def ready(r,monkeypatch):
    monkeypatch.setattr(settings,'POS_REAL_SALE_PILOT_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    with r.sessions() as db:j=h.printer_test(db,db.get(User,r.uid),r.did,uuid4().hex);db.commit();jid=j.id
    r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        u=db.get(User,r.uid);h.review_job(db,u,jid,True,'Mocked physical acceptance',False)
        pilot.configure(db,u,r.did,'Mocked explicit shop/register/device approval',True)
    r.backend.calls.clear()
    return r


def approved_cart(r):
    cid,v=newcart(r)
    with r.sessions() as db:pilot.authorize(db,db.get(User,r.uid),cid,v,uuid4().hex,'One genuine fixture purchase',True)
    return cid,v


def capture(r,cid,v,tenders=None,key=None):
    with r.sessions() as db:return service.checkout(db,db.get(User,r.uid),cid,v,key or uuid4().hex,tenders or cash(),r.pos).id


def jobs(db):return db.query(HardwareJob).filter_by(kind='RECEIPT').all()


@pytest.mark.parametrize('method',['CASH','CARD_PAYMENT','BANK_TRANSFER','MIXED'])
def test_verified_real_sale_one_atomic_receipt_and_no_drawer(connector,monkeypatch,method):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r)
    tenders=cash() if method=='CASH' else [{'method':'CARD_PAYMENT','amount':'8500','received':'8500','reference':'TERMINAL-TEST','confirmed':True}] if method=='CARD_PAYMENT' else [{'method':'BANK_TRANSFER','amount':'8500','received':'8500','reference':'BANK-TEST','confirmed':False}] if method=='BANK_TRANSFER' else cash('1000','1000')+[{'method':'CARD_PAYMENT','amount':'7500','received':'7500','reference':'T','confirmed':True}]
    key=uuid4().hex;rid=capture(r,cid,v,tenders,key)
    assert capture(r,cid,v,tenders,key)==rid
    if method=='BANK_TRANSFER':
        with r.sessions() as db:
            u=db.get(User,r.uid);p=db.query(SalesPayment).filter_by(payment_method='BANK_TRANSFER').one()
            service.verify_bank(db,u,rid,p.id,uuid4().hex)
            service.finalize(db,u,rid);service.finalize(db,u,rid)
    assert service.dispatch_one(r.sessions,r.pos)
    assert not service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        s=db.get(POSCheckout,rid);assert s.status=='COMPLETE'
        assert db.query(POSPosting).count()==1 and db.query(JournalEntry).count()==1
        j=jobs(db)[0];assert len(jobs(db))==1 and j.business_key=='receipt:auto:'+rid and j.detail['pilot_cart_id']==cid
        assert j.status=='QUEUED' and h.source_valid(db,j)
        output=''.join(p.extract_text() for p in PdfReader(io.BytesIO(j.pdf)).pages)
        assert '#POS1' in output and s.reference in output
        assert 'TEST — NOT A VALID SALE' not in output and 'PAID / Shopify verified' in output
        assert '8,500.00' in output
        assert not db.get(PrintDevice,r.did).config['auto_receipts']
        jid=j.id
    r.backend.submit=lambda *a,**kw:r.backend.calls.append(('one-receipt',))
    r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        j=db.get(HardwareJob,jid);assert j.status=='SUBMITTED_TO_PRINTER' and j.result['spool_id'] is None and not j.result['physical_output_verified']
        assert db.query(HardwareEvent).filter_by(job_id=jid,action='AGENT_SUBMITTED_TO_PRINTER').count()==1
        assert not db.query(HardwareJob).filter_by(kind='DRAWER').first()
        assert pilot.sale_status(db,db.get(POSCheckout,rid))['accounting_verified']
    path=Path(r.store.db.execute('PRAGMA database_list').fetchone()[2]);r.store.close();r.store=Store(path);r.engine.store=r.store
    r.engine.cycle();assert len(r.backend.calls)==1 and len(r.pos.calls)==1
    h.tick(r.sessions)
    with r.sessions() as db:assert len(jobs(db))==1


def test_lost_response_reconciles_existing_order_without_create_retry(connector,monkeypatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v);r.pos.fail_after_create=True
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='UNCERTAIN' and not jobs(db)
        op=db.query(POSOperation).one();assert op.attempts==1
        service.reconcile(db,db.get(User,r.uid),op.id,'Explicit existing-order recovery fixture',r.pos)
        assert db.get(POSCheckout,rid).status=='COMPLETE' and len(jobs(db))==1 and db.query(POSPosting).count()==1
    assert not service.dispatch_one(r.sessions,r.pos) and len(r.pos.calls)==1


@pytest.mark.parametrize('failure',['READBACK','POSTING'])
def test_pending_financial_stages_never_print_then_reconcile_once(connector,monkeypatch,failure):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v)
    original=r.pos.read if failure=='READBACK' else service.post_journal
    def fail(*a,**kw):raise RuntimeError('Private unavailable detail')
    if failure=='READBACK':monkeypatch.setattr(r.pos,'read',fail)
    else:monkeypatch.setattr(service,'post_journal',fail)
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        s=db.get(POSCheckout,rid);assert s.status=='REVIEW' and not jobs(db) and db.query(POSPosting).count()==0
        assert pilot.sale_status(db,s)['phase']==('SHOPIFY_READBACK_PENDING' if failure=='READBACK' else 'ACCOUNTING_PENDING')
    if failure=='READBACK':monkeypatch.setattr(r.pos,'read',original)
    else:monkeypatch.setattr(service,'post_journal',original)
    with r.sessions() as db:
        service.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Explicit read-only Shopify / local posting approval',r.pos)
        assert len(jobs(db))==db.query(POSPosting).count()==1
    assert len(r.pos.calls)==1


def test_historical_review_prevents_pilot_authorization_and_checkout(connector,monkeypatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r)
    legacy=checkout(r)
    with r.sessions() as db:
        op=db.query(POSOperation).one();op.status='REVIEW';op.attempts=1
        old=db.get(POSCheckout,legacy);old.status='REVIEW';db.commit()
        before=copy.deepcopy((op.status,op.attempts,old.frozen,old.status))
        with pytest.raises(HTTPException):service.checkout(db,db.get(User,r.uid),cid,v,uuid4().hex,cash(),r.pos)
        db.rollback()
        assert (op.status,op.attempts,old.frozen,old.status)==before
        assert db.query(POSCheckout).count()==1 and not jobs(db)
    assert not service.dispatch_one(r.sessions,r.pos) and not r.pos.calls


@pytest.mark.parametrize('failure',['PAUSED','DISCONNECTED','UNKNOWN'])
def test_printer_issue_after_capture_never_reverses_money_or_bypasses_fence(connector,monkeypatch,failure):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v)
    with r.sessions() as db:
        d=db.get(PrintDevice,r.did)
        if failure=='PAUSED':h.manage(db,db.get(User,r.uid),r.did,'pause')
        elif failure=='DISCONNECTED':d.last_seen=h.now()-timedelta(minutes=3);db.commit()
        else:
            j=h.printer_test(db,db.get(User,r.uid),r.did,uuid4().hex);j.status='UNKNOWN';db.commit()
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE' and len(jobs(db))==1
    if failure=='DISCONNECTED':r.backend.offline=True
    r.engine.cycle();assert not r.backend.calls
    if failure=='DISCONNECTED':
        with r.sessions() as db:assert pilot.sale_status(db,db.get(POSCheckout,rid))['printer_ready'] is False
    assert len(r.pos.calls)==1


def test_gate_off_permissions_and_stale_cart(connector,monkeypatch):
    r=connector
    with r.sessions() as db:
        with pytest.raises(HTTPException):pilot.configure(db,db.get(User,r.uid),r.did,'No release approval',True)
    ready(r,monkeypatch);cid,v=approved_cart(r)
    with r.sessions() as db:
        c=service.get_cart(db,db.get(User,r.uid),cid);c.version+=1;db.commit()
        with pytest.raises(HTTPException):service.checkout(db,db.get(User,r.uid),cid,c.version,uuid4().hex,cash(),r.pos)
        assert db.query(POSCheckout).count()==0
    assert not r.pos.calls


def test_receipt_render_failure_preserves_financial_commit_and_reserves_identity(connector,monkeypatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v)
    def fail(*a,**kw):raise ValueError('private renderer failure')
    monkeypatch.setattr(h.pos_receipts,'generate',fail)
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        s=db.get(POSCheckout,rid);assert s.status=='COMPLETE' and db.query(POSPosting).count()==1
        j=jobs(db)[0];assert j.status=='FAILED' and j.result=={'code':'PILOT_RECEIPT_REQUIRES_REVIEW'}
        pilot.reserve_receipt(db,s,r.pos.read('gid://shopify/Order/1'));assert len(jobs(db))==1
    assert len(r.pos.calls)==1


@pytest.mark.parametrize('outcome',['SUCCESS','TIMEOUT'])
def test_discounted_frozen_sale_and_timeout_before_remote_outcome(connector,monkeypatch,outcome):
    r=ready(connector,monkeypatch);cid,v=newcart(r)
    with r.sessions() as db:
        original=service.get_cart(db,db.get(User,r.uid),cid)
        details={k:original.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']}
        details.update(order_discount={'type':'FIXED','value':'500'},discount_reason='Approved genuine fixture discount')
        c=service.edit_cart(db,db.get(User,r.uid),cid,v,'details',details)
        v=c.version
        pilot.authorize(db,db.get(User,r.uid),cid,v,uuid4().hex,'Discounted genuine sale',True)
    rid=capture(r,cid,v,cash('8000','8500'))
    if outcome=='SUCCESS':
        service.dispatch_one(r.sessions,r.pos)
        with r.sessions() as db:
            assert db.get(POSCheckout,rid).status=='COMPLETE' and len(jobs(db))==1
            output=''.join(p.extract_text() for p in PdfReader(io.BytesIO(jobs(db)[0].pdf)).pages)
            assert '8,000.00' in output and '500.00' in output
        return
    def timeout(w):raise TimeoutError('No known outcome; never automatically resend')
    monkeypatch.setattr(r.pos,'create',timeout)
    service.dispatch_one(r.sessions,r.pos)
    assert not service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        s=db.get(POSCheckout,rid);assert s.status=='UNCERTAIN' and s.frozen['quote']['grand_total']=='8000.00'
        assert not jobs(db) and not r.pos.orders
        with pytest.raises(HTTPException):service.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'No remote evidence cannot finalize',r.pos)
        assert db.query(POSPosting).count()==0


def test_flag_off_holds_pilot_create_and_source_gate(connector,monkeypatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v)
    monkeypatch.setattr(settings,'POS_REAL_SALE_PILOT_ENABLED',False)
    assert not service.dispatch_one(r.sessions,r.pos) and not r.pos.calls
    monkeypatch.setattr(settings,'POS_REAL_SALE_PILOT_ENABLED',True)
    service.dispatch_one(r.sessions,r.pos)
    monkeypatch.setattr(settings,'POS_REAL_SALE_PILOT_ENABLED',False)
    r.engine.cycle();assert not r.backend.calls
    with r.sessions() as db:assert jobs(db)[0].status=='QUEUED' and db.get(POSCheckout,rid).status=='COMPLETE'


def test_preflight_and_admin_api_authorization(connector,monkeypatch):
    import pos_shopify
    r=ready(connector,monkeypatch);cid,v=approved_cart(r)
    assert r.client.get('/crm/pos/api/real-pilot').json()['scope']['device_id']==r.did
    assert r.client.post('/crm/pos/api/real-pilot/authorize',json={'cid':cid,'version':v,'key':uuid4().hex,'notes':'unauthorized request','confirm':True}).status_code==403
    rid=capture(r,cid,v)
    def unavailable():raise pos_shopify.POSRemoteError('status/version unavailable')
    monkeypatch.setattr(r.pos,'capabilities',unavailable)
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='REVIEW' and not jobs(db)
        assert db.query(POSOperation).one().attempts==0
    assert not r.pos.calls


def test_printer_unknown_after_permit_never_replays_or_reposts(connector,monkeypatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v);service.dispatch_one(r.sessions,r.pos)
    r.backend.error=TypeError('private driver error')
    with pytest.raises(TypeError):r.engine.cycle()
    r.engine.cycle()
    with r.sessions() as db:
        assert jobs(db)[0].status=='UNKNOWN' and len(jobs(db))==1
        assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==1
    assert len(r.backend.calls)==1 and len(r.pos.calls)==1


def test_browser_authorize_normal_checkout_and_separate_receipt_status(connector,monkeypatch):
    from playwright.sync_api import sync_playwright
    r=ready(connector,monkeypatch);cid,v=newcart(r);root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("sessionStorage.setItem('pixiepinks-pos-cart',"+repr(cid)+")")
        def route(handler):
            req=handler.request;path=req.url.removeprefix('https://pilot.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():handler.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                handler.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            handler.fulfill(status=404,body='')
        page.route('**/*',route);page.on('dialog',lambda d:d.accept('One real fixture sale') if d.type=='prompt' else d.accept())
        page.goto('https://pilot.test/crm/pos');page.get_by_role('button',name='Real-sale pilot setup · Admin',exact=True).wait_for(state='visible')
        page.locator('#pos-checkout').click();page.get_by_role('button',name='Authorize this genuine sale for pilot',exact=True).click()
        page.get_by_text('Pilot authorized for this frozen cart.',exact=False).wait_for()
        page.get_by_role('button',name='Confirm tender & checkout',exact=True).click()
        page.get_by_text('Pilot phase: SHOPIFY_CREATE_PENDING',exact=False).wait_for()
        service.dispatch_one(r.sessions,r.pos);r.engine.cycle();r.engine.cycle()
        page.get_by_text('Pilot phase: COMPLETE',exact=False).wait_for()
        page.get_by_text('Automatic Receipt Status: SUBMITTED_TO_PRINTER',exact=False).wait_for()
        page.reload();page.locator('#pos-session').filter(has_text='Open').wait_for()
        assert not page.locator('#pos-modal').evaluate('(e)=>e.open')
        page.locator('#pos-history').click();page.get_by_role('button',name='Open receipt / status',exact=True).first.click()
        page.get_by_text('Pilot phase: COMPLETE',exact=False).wait_for()
        assert not errors and len(r.pos.calls)==1 and len(r.backend.calls)==1
        browser.close()


def test_pilot_readonly_diagnostic_has_no_mutation_side_effect(connector,monkeypatch):
    from types import SimpleNamespace
    from scripts.diagnose_pos_pilot_readonly import run
    r=ready(connector,monkeypatch)
    class ReadProbe:
        def probe(self,*args):return SimpleNamespace(report={'status':'SANITIZED_READ_UNAVAILABLE'},order=None)
    with r.sessions() as db:
        counts=(db.query(POSCheckout).count(),db.query(JournalEntry).count(),db.query(HardwareJob).count())
    report=run(r.sessions,r.pos,ReadProbe())
    assert report['write_authorized'] is False and report['financial_domain_clear'] is True
    assert report['pilot_scope']['device_id']==r.did
    with r.sessions() as db:assert counts==(db.query(POSCheckout).count(),db.query(JournalEntry).count(),db.query(HardwareJob).count())
    assert not r.pos.calls and not r.remote.mutations


def test_admin_only_and_immutable_target_binding(connector,monkeypatch):
    from pos_models import POSSession
    r=ready(connector,monkeypatch);cid,v=approved_cart(r)
    with r.sessions() as db:
        staff=User(username='pilot-staff',role='STAFF',password_hash='fixture',section_access=['pos.view','pos.sell']);db.add(staff);db.commit()
        with pytest.raises(HTTPException):pilot.authorize(db,staff,cid,v,uuid4().hex,'Not administrator',True)
        with pytest.raises(HTTPException):pilot.configure(db,db.get(User,r.uid),'other-device','Wrong immutable scope',True)
    rid=capture(r,cid,v)
    with r.sessions() as db:
        s=db.get(POSCheckout,rid);session=db.get(POSSession,s.session_id)
        # In-memory corruption check, no production mutation and no SQL trigger bypass.
        with db.no_autoflush:
            s.frozen={**s.frozen,'location':'gid://shopify/Location/999'}
            with pytest.raises(HTTPException):pilot.dispatch_allowed(db,s)
        db.rollback()
    assert not r.pos.calls


@pytest.mark.parametrize('mismatch',['READ_RESPONSE','RECORDED_SEARCH'])
def test_mismatched_remote_order_identity_cannot_finalize(connector,monkeypatch,mismatch):
    r=ready(connector,monkeypatch);cid,v=approved_cart(r);rid=capture(r,cid,v)
    original=r.pos.read
    def wrong_id(oid):
        order=copy.deepcopy(original(oid));order['id']='gid://shopify/Order/999';return order
    if mismatch=='READ_RESPONSE':monkeypatch.setattr(r.pos,'read',wrong_id)
    else:r.pos.fail_after_create=True
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        op=db.query(POSOperation).one()
        if mismatch=='RECORDED_SEARCH':op.outcome={'remote_id':'gid://shopify/Order/999'};db.commit()
        with pytest.raises(HTTPException):service.reconcile(db,db.get(User,r.uid),op.id,'Conflicting identity cannot recover',r.pos)
        assert db.get(POSCheckout,rid).status in {'REVIEW','UNCERTAIN'} and not jobs(db)
        assert db.query(POSPosting).count()==0
    assert len(r.pos.calls)==1
