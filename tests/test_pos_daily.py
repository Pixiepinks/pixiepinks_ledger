"""Daily authorized cashiers use real POS/receipt services with mocked external I/O."""
import copy,io
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
from pos_models import POSDailyPolicy,POSDailySale,POSPilotAuthorization,POSCheckout,POSOperation,POSPosting,POSSession
from hardware_models import PrintDevice,HardwareJob,HardwareEvent
from settings import settings
from windows_print_agent.store import Store
import pos_daily as daily
import pos_service as s
import hardware_service as h
import pos_receipts


def release(r,monkeypatch,staff=True):
    monkeypatch.setattr(settings,'POS_DAILY_OPERATIONS_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    with r.sessions() as db:
        u=db.get(User,r.uid);j=h.printer_test(db,u,r.did,uuid4().hex);db.commit();jid=j.id
    r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        u=db.get(User,r.uid);h.review_job(db,u,jid,True,'Prior physical acceptance (mock only)',False)
        r.register_id=db.get(PrintDevice,r.did).register_id
        daily.configure(db,u,r.did,True,'Approved exact daily scope',True)
        r.admin_id=r.uid
        if staff:
            s.close_register(db,u,r.sid,'10000','Cashier handover fixture')
            user=User(username='daily-cashier',role='STAFF',password_hash=u.password_hash,section_access=['pos.view','pos.sell','pos.register_open','pos.register_close','pos.hold'])
            db.add(user);db.commit();r.uid=user.id;r.sid=s.open_register(db,user,r.register_id,'1000').id
    r.backend.calls.clear();return r


def sale(r,tenders=None,key=None,cid=None,v=None):
    if cid is None:cid,v=newcart(r)
    with r.sessions() as db:return s.checkout(db,db.get(User,r.uid),cid,v,key or uuid4().hex,tenders or cash(),r.pos).id


def receipt_jobs(db):return db.query(HardwareJob).filter_by(kind='RECEIPT').all()


@pytest.mark.parametrize('method',['CASH','CARD','BANK','MIXED'])
def test_cashier_checkout_without_per_cart_admin_one_atomic_receipt(connector,monkeypatch,method):
    r=release(connector,monkeypatch)
    tenders=cash() if method=='CASH' else [{'method':'CARD_PAYMENT','amount':'8500','received':'8500','reference':'TERMINAL-TEST','confirmed':True}] if method=='CARD' else [{'method':'BANK_TRANSFER','amount':'8500','received':'8500','reference':'BANK-TEST','confirmed':False}] if method=='BANK' else cash('1000','1000')+[{'method':'CARD_PAYMENT','amount':'7500','received':'7500','reference':'CARD-TEST','confirmed':True}]
    cid,v=newcart(r);key=uuid4().hex;rid=sale(r,tenders,key,cid,v)
    assert sale(r,tenders,key,cid,v)==rid
    if method=='BANK':
        with r.sessions() as db:
            admin=db.get(User,r.admin_id);p=db.query(SalesPayment).filter_by(payment_method='BANK_TRANSFER').one()
            s.verify_bank(db,admin,rid,p.id,uuid4().hex)
            s.finalize(db,db.get(User,r.uid),rid);s.finalize(db,db.get(User,r.uid),rid)
    assert s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE'
        assert db.query(POSDailySale).count()==1 and db.query(POSPilotAuthorization).count()==0
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==1
        job=receipt_jobs(db)[0];jid=job.id;assert len(receipt_jobs(db))==1
        assert job.business_key=='receipt:auto:'+rid and h.source_valid(db,job)
        output=''.join(p.extract_text() for p in PdfReader(io.BytesIO(job.pdf)).pages)
        assert '#POS1' in output and 'TEST — NOT A VALID SALE' not in output
        assert '8,500.00' in output
        assert not db.get(PrintDevice,r.did).config['auto_receipts']
        assert not settings.POS_CASH_DRAWER_AUTOMATION_ENABLED
        assert db.query(HardwareJob).filter_by(kind='DRAWER').count()==0
    r.backend.submit=lambda *args,**kw:r.backend.calls.append('receipt')
    r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        job=db.get(HardwareJob,jid)
        assert job.status=='SUBMITTED_TO_PRINTER' and job.result['spool_id'] is None and job.result['physical_output_verified'] is False
    path=Path(r.store.db.execute('PRAGMA database_list').fetchone()[2]);r.store.close();r.store=Store(path);r.engine.store=r.store
    r.engine.cycle();assert r.backend.calls==['receipt'] and len(r.pos.calls)==1
    h.tick(r.sessions)
    with r.sessions() as db:assert len(receipt_jobs(db))==1


@pytest.mark.parametrize('failure',['FLAG_OFF','POLICY_OFF','NO_SELL','VERSION','PAUSED','WRONG_SHOP','UNKNOWN','REVIEW'])
def test_daily_gates_before_accepting_payment(connector,monkeypatch,failure):
    r=release(connector,monkeypatch);cid,v=newcart(r)
    if failure=='FLAG_OFF':monkeypatch.setattr(settings,'POS_DAILY_OPERATIONS_ENABLED',False)
    with r.sessions() as db:
        u=db.get(User,r.uid);d=db.get(PrintDevice,r.did)
        if failure=='POLICY_OFF':daily.configure(db,db.get(User,r.admin_id),r.did,False,'Daily release suspended',True)
        if failure=='NO_SELL':u.section_access=['pos.view'];db.commit()
        if failure=='VERSION':h.configure(db,db.get(User,r.admin_id),r.did,dict(d.config))
        if failure=='PAUSED':h.manage(db,db.get(User,r.admin_id),r.did,'pause')
        if failure=='UNKNOWN':j=h.printer_test(db,db.get(User,r.admin_id),r.did,uuid4().hex);j.status='UNKNOWN';db.commit()
        if failure=='WRONG_SHOP':monkeypatch.setattr(h.retail,'shop',lambda:'different-shop')
        if failure=='REVIEW':
            # Real existing REVIEW fixture is not rewritten or bypassed by daily mode.
            existing=sale(r,cash(),uuid4().hex,cid,v)
            op=db.query(POSOperation).filter_by(checkout_id=existing).one();op.status='REVIEW';db.get(POSCheckout,existing).status='REVIEW';db.commit()
            cid,v=newcart(r)
        count=db.query(SalesPayment).count()
        with pytest.raises(HTTPException):s.checkout(db,u,cid,v,uuid4().hex,cash(),r.pos)
        db.rollback();assert db.query(SalesPayment).count()==count
    assert not r.pos.calls


@pytest.mark.parametrize('failure',['LOST_RESPONSE','READBACK','POSTING'])
def test_daily_no_print_until_verified_and_no_resend_recovery(connector,monkeypatch,failure):
    r=release(connector,monkeypatch);rid=sale(r)
    original=r.pos.read if failure=='READBACK' else s.post_journal
    def unavailable(*a,**kw):raise RuntimeError('Private unavailable detail')
    if failure=='LOST_RESPONSE':r.pos.fail_after_create=True
    elif failure=='READBACK':monkeypatch.setattr(r.pos,'read',unavailable)
    else:monkeypatch.setattr(s,'post_journal',unavailable)
    s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status in {'REVIEW','UNCERTAIN'}
        assert not receipt_jobs(db) and db.query(POSPosting).count()==0
    assert not s.dispatch_one(r.sessions,r.pos)
    if failure=='READBACK':monkeypatch.setattr(r.pos,'read',original)
    elif failure=='POSTING':monkeypatch.setattr(s,'post_journal',original)
    with r.sessions() as db:
        s.reconcile(db,db.get(User,r.admin_id),db.query(POSOperation).one().id,'Supervisor verified existing exact order',r.pos)
        assert db.get(POSCheckout,rid).status=='COMPLETE' and len(receipt_jobs(db))==1
    assert len(r.pos.calls)==1


@pytest.mark.parametrize('state',['OFFLINE','PAUSED','UNKNOWN','RENDER'])
def test_hardware_failure_never_duplicates_or_reverses_finance(connector,monkeypatch,state):
    r=release(connector,monkeypatch);rid=sale(r)
    if state=='RENDER':monkeypatch.setattr(pos_receipts,'render',lambda *a,**kw:(_ for _ in ()).throw(ValueError('private error')))
    if state=='PAUSED':
        with r.sessions() as db:h.manage(db,db.get(User,r.admin_id),r.did,'pause')
    s.dispatch_one(r.sessions,r.pos)
    if state=='OFFLINE':r.backend.offline=True
    if state=='UNKNOWN':r.backend.error=TypeError('private driver result')
    if state=='UNKNOWN':
        with pytest.raises(TypeError):r.engine.cycle()
        r.engine.cycle();assert len(r.backend.calls)==1
    else:r.engine.cycle();assert not r.backend.calls
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==1
        assert len(receipt_jobs(db))==1
        if state=='RENDER':assert receipt_jobs(db)[0].result['code']=='DAILY_RECEIPT_REQUIRES_REVIEW'
        if state=='UNKNOWN':assert receipt_jobs(db)[0].status=='UNKNOWN'
    assert len(r.pos.calls)==1


def test_scope_and_duplicate_recovery_require_admin(connector,monkeypatch):
    r=release(connector,monkeypatch)
    with r.sessions() as db:
        with pytest.raises(HTTPException):daily.configure(db,db.get(User,r.uid),r.did,True,'Cashier cannot authorize rollout',True)
    rid=sale(r);s.dispatch_one(r.sessions,r.pos);r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        job=receipt_jobs(db)[0];admin=db.get(User,r.admin_id);h.review_job(db,admin,job.id,True,'Actual output observed (mock)',False)
        with pytest.raises(HTTPException):h.controlled_reprint(db,db.get(User,r.uid),job.id,uuid4().hex,'Cashier duplicate forbidden',True)
        with pytest.raises(HTTPException):pos_receipts.generate(db,db.get(User,r.uid),rid)
        duplicate=h.controlled_reprint(db,admin,job.id,uuid4().hex,'Supervised duplicate approval',True);db.commit()
        assert duplicate.parent_id==job.id
        assert db.query(HardwareJob).filter_by(kind='DRAWER').count()==0


def test_gate_default_and_staff_api_access(connector,monkeypatch):
    r=connector
    with r.sessions() as db:
        with pytest.raises(HTTPException):daily.configure(db,db.get(User,r.uid),r.did,True,'Flag still off',True)
    r=release(r,monkeypatch,staff=False)
    assert r.client.get('/crm/pos/api/daily').json()['policies'][0]['device_id']==r.did
    assert r.client.post('/crm/pos/api/daily',json={'device_id':r.did,'enabled':True,'notes':'Missing CSRF','confirm':True}).status_code==403
    r.login('quick-staff');assert r.client.get('/crm/pos/api/daily').status_code==403


def test_register_handover_float_count_and_variance(connector,monkeypatch):
    r=release(connector,monkeypatch)
    with r.sessions() as db:
        cashier=db.get(User,r.uid);assert s.drawer(db,db.get(POSSession,r.sid))==1000
        with pytest.raises(HTTPException):s.close_register(db,cashier,r.sid,'900','Variance needs supervisor')
        s.close_register(db,cashier,r.sid,'1000','Counted by cashier; not sensor verified')
        sid=s.open_register(db,db.get(User,r.admin_id),r.register_id,'500').id
        assert db.get(POSSession,sid).cashier_id==r.admin_id


def test_daily_browser_cashier_checkout_refresh_status_and_next_sale(connector,monkeypatch):
    from playwright.sync_api import sync_playwright
    r=release(connector,monkeypatch);r.login('daily-cashier');cid,v=newcart(r);root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("sessionStorage.setItem('pixiepinks-pos-cart',"+repr(cid)+")")
        def route(handler):
            req=handler.request;path=req.url.removeprefix('https://daily.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():handler.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                assert response.status_code<400,(path,response.status_code,response.text)
                handler.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            handler.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://daily.test/crm/pos')
        assert page.get_by_role('button',name='Daily POS setup · Admin',exact=True).count()==0
        page.locator('#pos-checkout').click()
        assert page.get_by_role('button',name='Authorize this genuine sale for pilot',exact=True).count()==0
        page.get_by_role('button',name='Confirm tender & checkout',exact=True).click()
        page.get_by_text('Shopify: QUEUED · Accounting: PENDING',exact=True).wait_for()
        s.dispatch_one(r.sessions,r.pos);r.engine.cycle();r.engine.cycle()
        page.get_by_text('Shopify: VERIFIED · Accounting: COMPLETE',exact=True).wait_for()
        page.get_by_text('Automatic Receipt Status: SUBMITTED_TO_PRINTER',exact=False).wait_for()
        page.reload();page.get_by_text('Shopify: VERIFIED · Accounting: COMPLETE',exact=True).wait_for()
        page.get_by_role('button',name='Next sale',exact=True).click()
        page.locator('#pos-modal').wait_for(state='hidden')
        assert page.locator('#pos-scan').evaluate('(e)=>e===document.activeElement')
        assert not errors and len(r.pos.calls)==len(r.backend.calls)==1
        with r.sessions() as db:assert len(receipt_jobs(db))==1 and db.query(POSPosting).count()==1
        browser.close()


def test_daily_admin_browser_scope_and_gate_off(connector,monkeypatch):
    from playwright.sync_api import sync_playwright
    r=connector;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page();page.on('dialog',lambda d:d.accept())
        def route(handler):
            req=handler.request;path=req.url.removeprefix('https://daily.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():handler.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                handler.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            handler.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://daily.test/crm/pos')
        page.get_by_role('button',name='Daily POS setup · Admin',exact=True).click()
        page.get_by_text('Daily feature flag OFF',exact=False).wait_for()
        page.get_by_label('Authorize daily cashier checkout',exact=True).check()
        page.get_by_label('Daily release approval notes',exact=True).fill('Not released')
        page.get_by_role('button',name='Save daily register authorization',exact=True).click()
        page.get_by_text('Daily POS feature flag is OFF',exact=True).wait_for()
        with r.sessions() as db:assert db.query(POSDailyPolicy).count()==0
        browser.close()


def test_daily_readonly_preflight_no_writes_or_remote_calls(connector,monkeypatch):
    from scripts.diagnose_pos_daily_readonly import run
    from sqlalchemy import event
    r=release(connector,monkeypatch);statements=[]
    engine=r.sessions.kw['bind']
    def record(conn,cursor,statement,parameters,context,many):statements.append(statement.strip().upper())
    event.listen(engine,'before_cursor_execute',record)
    try:result=run(r.sessions)
    finally:event.remove(engine,'before_cursor_execute',record)
    assert result['status']=='READ_ONLY_CHECK_COMPLETE' and result['daily_schema_present']
    assert result['write_authorized'] is False and result['policies'][0]['device_id']==r.did
    assert not any(s.startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP')) for s in statements)
    assert not r.pos.calls and not r.backend.calls


def test_daily_switch_off_holds_existing_create_and_receipt_without_replay(connector,monkeypatch):
    r=release(connector,monkeypatch);rid=sale(r)
    monkeypatch.setattr(settings,'POS_DAILY_OPERATIONS_ENABLED',False)
    assert not s.dispatch_one(r.sessions,r.pos) and not r.pos.calls
    with r.sessions() as db:
        op=db.query(POSOperation).one();assert op.attempts==0 and op.status=='QUEUED'
    monkeypatch.setattr(settings,'POS_DAILY_OPERATIONS_ENABLED',True)
    assert s.dispatch_one(r.sessions,r.pos)
    monkeypatch.setattr(settings,'POS_DAILY_OPERATIONS_ENABLED',False)
    r.engine.cycle()
    assert not r.backend.calls
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE' and receipt_jobs(db)[0].status=='QUEUED'
        assert len(receipt_jobs(db))==db.query(POSPosting).count()==1


def test_daily_admin_policy_revision_rejects_pending_financial_binding(connector,monkeypatch):
    r=release(connector,monkeypatch);rid=sale(r)
    with r.sessions() as db:
        version=db.get(POSDailyPolicy,r.register_id).version
        with pytest.raises(HTTPException):daily.configure(db,db.get(User,r.admin_id),r.did,False,'Pending sale cannot be silently rerouted',True)
        db.rollback();assert db.get(POSDailyPolicy,r.register_id).version==version
        assert db.get(POSDailySale,rid).policy_version==version
    assert not r.pos.calls
