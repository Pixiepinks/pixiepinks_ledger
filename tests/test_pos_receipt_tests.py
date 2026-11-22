"""Non-financial checkout acceptance using real receipt/encrypted/journal paths."""
import copy,io,importlib.util
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_receipt_connector import connector
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from test_print_agent_diagnostics import records
from models import User,SalesOrder,SalesPayment,JournalEntry,JournalLine
from pos_models import POSCheckout,POSOperation,POSPosting,POSCashMovement,POSAudit,POSCart,POSSession
from hardware_models import POSReceiptTest,HardwareJob,HardwareEvent,PrintDevice
from settings import settings
from windows_print_agent.store import Store
import hardware_service as h
import pos_receipt_tests as tests


def enable(r,monkeypatch):
    monkeypatch.setattr(settings,'POS_TEST_CHECKOUT_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    return r


def new(r):
    with r.sessions() as db:
        return tests.create(db,db.get(User,r.uid),r.did,uuid4().hex).id


def finish(r,tid):
    with r.sessions() as db:return tests.finalize(db,db.get(User,r.uid),tid,True).job_id


def financial_counts(db):
    return {m.__tablename__:db.query(m).count() for m in [SalesOrder,SalesPayment,JournalEntry,JournalLine,POSCheckout,POSOperation,POSPosting,POSCashMovement,POSAudit,POSCart,POSSession]}


def test_finalization_ignores_pos_review_but_creates_no_financial_records(connector,monkeypatch):
    r=enable(connector,monkeypatch)
    # Existing attempted sale is real fixture data, not part of test mode.
    rid=checkout(r)
    with r.sessions() as db:
        op=db.query(POSOperation).filter_by(checkout_id=rid).one();op.status='REVIEW';op.attempts=1
        sale=db.get(POSCheckout,rid);sale.status='REVIEW';db.commit()
        before=financial_counts(db);old=copy.deepcopy((op.status,op.attempts,op.wire,op.outcome,sale.frozen,sale.status))
        config=copy.deepcopy(db.get(PrintDevice,r.did).config)
    def forbidden(*a,**kw):raise AssertionError('Test called a production financial path')
    for name in ['create','read','find','capabilities','inspect']:
        monkeypatch.setattr(r.pos,name,forbidden)
    monkeypatch.setattr(h.pos,'publish_sale',forbidden)
    monkeypatch.setattr(h.pos,'dispatch_one',forbidden)
    tid=new(r);jid=finish(r,tid)
    for _ in range(3):assert finish(r,tid)==jid
    with r.sessions() as db:
        assert financial_counts(db)==before
        op=db.query(POSOperation).filter_by(checkout_id=rid).one();sale=db.get(POSCheckout,rid)
        assert (op.status,op.attempts,op.wire,op.outcome,sale.frozen,sale.status)==old
        assert db.get(PrintDevice,r.did).config==config
        job=db.get(HardwareJob,jid)
        assert job.kind=='TEST' and job.checkout_id is None and job.session_id is None
        assert job.detail['automatic'] is True and job.detail['test_only'] is True
        assert db.query(HardwareJob).count()==1
        assert db.query(HardwareEvent).filter_by(action='TEST_CHECKOUT_FINALIZED').count()==1
        assert db.query(HardwareEvent).filter_by(action='TEST_RECEIPT_QUEUED').count()==1
    r.engine.cycle();r.engine.cycle()
    with r.sessions() as db:
        assert financial_counts(db)==before and db.get(HardwareJob,jid).status=='SUBMITTED_TO_PRINTER'
        assert db.get(POSCheckout,rid).status=='REVIEW'
    assert not r.remote.mutations and not r.pos.calls


def test_default_disabled_admin_csrf_and_create_idempotency(connector,monkeypatch):
    r=connector
    with r.sessions() as db:
        with pytest.raises(HTTPException) as e:tests.create(db,db.get(User,r.uid),r.did,uuid4().hex)
        assert e.value.status_code==404
    enable(r,monkeypatch);key=uuid4().hex
    with r.sessions() as db:
        u=db.get(User,r.uid);a=tests.create(db,u,r.did,key);b=tests.create(db,u,r.did,key)
        assert a.id==b.id and db.query(POSReceiptTest).count()==1
        assert db.query(HardwareJob).count()==0
        other=User(username='not-admin-test',password_hash='fixture',role='STAFF',section_access=['pos.view','pos.sell']);db.add(other);db.commit()
        with pytest.raises(HTTPException):tests.create(db,other,r.did,uuid4().hex)
        with pytest.raises(HTTPException):tests.finalize(db,other,a.id,True)
        with pytest.raises(HTTPException):tests.finalize(db,u,a.id,False)
        with pytest.raises(HTTPException):tests.create(db,u,r.did,'bad')
    url='/crm/pos/api/hardware/test-checkouts'
    assert r.client.post(url,json={'device_id':r.did,'key':key}).status_code==403
    headers={'X-CSRF-Token':r.csrf}
    response=r.client.post(url,json={'device_id':r.did,'key':key},headers=headers)
    assert response.status_code==200
    tid=response.json()['id']
    for _ in range(2):assert r.client.post(url+'/'+tid+'/finalize',json={'confirm':True},headers=headers).status_code==200
    assert r.client.get(url+'/'+tid).json()['status']=='FINALIZED'
    r.client.get('/logout')
    assert r.client.get(url,follow_redirects=False).status_code in {302,303,401,403}


def test_receipt_80mm_marked_and_no_false_verified_payment(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r);jid=finish(r,tid)
    with r.sessions() as db:
        job=db.get(HardwareJob,jid);pdf=PdfReader(io.BytesIO(job.pdf))
        assert len(pdf.pages)==1 and abs(float(pdf.pages[0].mediabox.width)*25.4/72-80)<.01
        output=pdf.pages[0].extract_text()
        for value in ['TEST — NOT A VALID SALE','SIMULATED CHECKOUT','NO PAYMENT RECEIVED','TEST ONLY — NOT SHOPIFY VERIFIED','350.00','400.00','50.00','500.00','150.00']:
            assert value in output
        assert 'TEST-'+tid in ''.join(output.split())
        from test_pos_receipts_phase4g import assert_geometry
        assert_geometry(job.pdf,80)
        from windows_print_agent.windows import WindowsPrinters
        WindowsPrinters.validate_receipt80(job.pdf)
        import pypdfium2 as pdfium
        doc=pdfium.PdfDocument(job.pdf);page=doc[0];bitmap=page.render(scale=203/72)
        assert bitmap.width>600 and bitmap.height>0
        bitmap.close();page.close();doc.close()
        assert 'Payment: PAID / Shopify verified' not in output and 'SALES RECEIPT' not in output
        assert h.source_valid(db,job)


@pytest.mark.parametrize('state',['UNKNOWN','DELIVERED_TO_AGENT','QUEUED','PAUSED','VERSION','INACTIVE','NOT_RECEIPT_ONLY'])
def test_hardware_fences_are_not_bypassed(connector,monkeypatch,state):
    r=enable(connector,monkeypatch);tid=new(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);d=db.get(PrintDevice,r.did)
        if state in {'UNKNOWN','DELIVERED_TO_AGENT','QUEUED'}:
            job=h.printer_test(db,u,r.did,uuid4().hex);job.status=state;db.commit()
        elif state=='PAUSED':h.manage(db,u,r.did,'pause')
        elif state=='INACTIVE':h.manage(db,u,r.did,'deactivate')
        else:
            h.configure(db,u,r.did,{**d.config,'receipt_only':state!='NOT_RECEIPT_ONLY'})
        before=[(j.id,j.status,j.attempts) for j in db.query(HardwareJob)]
        with pytest.raises(HTTPException):tests.finalize(db,u,tid,True)
        db.rollback()
        assert [(j.id,j.status,j.attempts) for j in db.query(HardwareJob)]==before
        assert db.get(POSReceiptTest,tid).status=='DRAFT'


def test_nullable_submission_restart_and_operator_confirmation(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r);jid=finish(r,tid)
    r.backend.submit=lambda *a,**kw:r.backend.calls.append(('test-only',))  # returns None
    r.engine.cycle();r.engine.cycle()
    assert r.store.db.execute('SELECT state,spool_id,acked FROM jobs').fetchone()==('SUBMITTED_TO_PRINTER',None,1)
    with r.sessions() as db:
        j=db.get(HardwareJob,jid);assert j.status=='SUBMITTED_TO_PRINTER' and j.result['physical_output_verified'] is False
        assert db.query(HardwareEvent).filter_by(job_id=jid,action='AGENT_SUBMITTED_TO_PRINTER').count()==1
    path=Path(r.store.db.execute('PRAGMA database_list').fetchone()[2]);r.store.close();r.store=Store(path);r.engine.store=r.store
    r.engine.cycle();r.engine.cycle();assert len(r.backend.calls)==1 and finish(r,tid)==jid
    with r.sessions() as db:
        h.review_job(db,db.get(User,r.uid),jid,True,'Mocked operator physical observation',False)
        result=tests.public(db,tests.find(db,db.get(User,r.uid),tid))
        assert result['receipt']['status']=='CONFIRMED'
        assert result['receipt']['result']['physical_output_verified'] is True
        assert db.get(PrintDevice,r.did).config['auto_receipts'] is False


def test_unknown_never_reprinted_or_recreated(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r);jid=finish(r,tid)
    calls=[]
    def ambiguous(*a,**kw):calls.append('attempt');raise TypeError('private simulated detail')
    r.backend.submit=ambiguous
    with pytest.raises(TypeError):r.engine.cycle()
    r.engine.cycle();assert finish(r,tid)==jid and calls==['attempt']
    with r.sessions() as db:assert db.get(HardwareJob,jid).status=='UNKNOWN' and db.query(HardwareJob).count()==1


def test_disable_gate_stops_dispatch_without_new_job(connector,monkeypatch):
    r=enable(connector,monkeypatch);jid=finish(r,new(r))
    monkeypatch.setattr(settings,'POS_TEST_CHECKOUT_ENABLED',False)
    r.engine.cycle();assert not r.backend.calls
    with r.sessions() as db:assert db.get(HardwareJob,jid).status=='QUEUED'


def test_render_failure_atomic_and_frozen_migration(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r)
    def fail(*a,**kw):raise ValueError('render failed')
    monkeypatch.setattr(tests.pos_receipts,'render',fail)
    with r.sessions() as db:
        with pytest.raises(ValueError):tests.finalize(db,db.get(User,r.uid),tid,True)
        db.rollback();assert db.get(POSReceiptTest,tid).status=='DRAFT' and db.query(HardwareJob).count()==0
    spec=importlib.util.spec_from_file_location('test_migration',Path(__file__).parents[1]/'alembic/versions/20261122_50_pos_receipt_tests.py');migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with r.sessions() as db:
        conn=db.connection();monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)));migration.upgrade();db.commit()
    for sql in ["UPDATE pos_receipt_tests SET frozen='{}'",'DELETE FROM pos_receipt_tests']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()


def test_frontend_refresh_recovers_same_identity_and_auto_status(connector,monkeypatch):
    from playwright.sync_api import sync_playwright
    r=enable(connector,monkeypatch);root=Path(__file__).parents[1]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(handler):
            req=handler.request;path=req.url.removeprefix('https://receipt.test')
            if path.startswith('/static/'):
                local=root/path.split('?',1)[0].lstrip('/')
                if local.is_file():handler.fulfill(path=str(local));return
            if path.startswith('/crm/pos'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':r.csrf,'Content-Type':'application/json'})
                handler.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            handler.fulfill(status=404,body='')
        page.route('**/*',route);page.on('dialog',lambda d:d.accept());page.goto('https://receipt.test/crm/pos')
        page.get_by_role('button',name='TEST checkout · not a sale',exact=True).click()
        page.get_by_role('button',name='Create Test Checkout',exact=True).click()
        page.get_by_role('button',name='Finalize Test',exact=True).wait_for()
        page.reload();page.get_by_role('button',name='TEST checkout · not a sale',exact=True).click()
        page.get_by_role('button',name='Create Test Checkout',exact=True).click()
        page.get_by_role('button',name='Finalize Test',exact=True).click()
        page.get_by_text('Automatic Receipt Status: QUEUED',exact=False).wait_for()
        r.engine.cycle();r.engine.cycle()
        page.reload();page.get_by_role('button',name='TEST checkout · not a sale',exact=True).click()
        page.get_by_text('Automatic Receipt Status: SUBMITTED_TO_PRINTER',exact=False).wait_for()
        page.get_by_text('Physical output NOT verified by printer submission',exact=True).wait_for()
        assert not errors
        with r.sessions() as db:
            assert db.query(POSReceiptTest).count()==1 and db.query(HardwareJob).count()==1
            assert db.query(POSCheckout).count()==0 and db.query(SalesPayment).count()==0
        assert len(r.backend.calls)==1
        browser.close()


def test_test_identity_scope_admin_review_and_source_guard(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r);jid=finish(r,tid)
    with r.sessions() as db:
        u=db.get(User,r.uid);row=db.get(POSReceiptTest,tid);job=db.get(HardwareJob,jid)
        other=User(username='test-view-only',role='STAFF',password_hash='fixture',section_access=['pos.view']);db.add(other);db.commit()
        job.status='SUBMITTED_TO_PRINTER';db.commit()
        with pytest.raises(HTTPException):h.review_job(db,other,jid,True,'Not authorized',False)
        with pytest.raises(HTTPException):tests.find(db,other,tid)
        with pytest.raises(HTTPException):tests.create(db,u,'other-device',row.request_key)
        assert h.source_valid(db,job)
        original=copy.deepcopy(row.frozen)
        with db.no_autoflush:
            row.frozen={**original,'customer_name':'tampered'}
            assert not h.source_valid(db,job)
        db.rollback()
    monkeypatch.setattr(h.retail,'shop',lambda:'another-shop')
    with r.sessions() as db:
        with pytest.raises(HTTPException) as e:tests.find(db,db.get(User,r.uid),tid)
        assert e.value.status_code==404


def test_job_and_test_finalize_rollback_together(connector,monkeypatch):
    r=enable(connector,monkeypatch);tid=new(r);real_event=h.event
    def fail_after_job(db,d,action,*args,**kwargs):
        if action=='TEST_CHECKOUT_FINALIZED':raise RuntimeError('simulated commit boundary')
        return real_event(db,d,action,*args,**kwargs)
    monkeypatch.setattr(h,'event',fail_after_job)
    with r.sessions() as db:
        with pytest.raises(RuntimeError):tests.finalize(db,db.get(User,r.uid),tid,True)
        db.rollback()
        row=db.get(POSReceiptTest,tid)
        assert row.status=='DRAFT' and row.job_id is None and db.query(HardwareJob).count()==0
        assert db.query(HardwareEvent).filter_by(action='JOB_CREATED').count()==0
    monkeypatch.setattr(h,'event',real_event)
    jid=finish(r,tid);assert finish(r,tid)==jid
