"""Real authenticated context + Chromium; disposable DB and mocked Shopify only."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import pytest
from playwright.sync_api import sync_playwright
from fastapi import HTTPException
from test_pos_daily import connector,inbox,retail,inv,pos,release,sale
from test_pos import newcart,checkout
from models import User
from pos_models import POSSession,POSCheckout,POSAudit
import pos_service as s


@contextmanager
def browser(r):
    root=Path(__file__).parents[1]
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(handler):
            req=handler.request;path=req.url.removeprefix('https://state.test')
            if path.startswith('/static/'):
                file=root/path.split('?',1)[0].lstrip('/')
                if file.is_file():handler.fulfill(path=str(file));return
            if path.startswith('/crm/pos'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                handler.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            handler.fulfill(status=404,body='')
        page.route('**/*',route)
        yield page
        assert not errors;b.close()


@pytest.mark.parametrize('staff',[False,True])
def test_completed_popup_dismiss_refresh_history_and_login(connector,monkeypatch,staff):
    r=release(connector,monkeypatch,staff=staff);r.login('daily-cashier' if staff else 'quick-admin')
    if staff:
        with r.sessions() as db:
            u=db.get(User,r.uid);u.section_access=[*u.section_access,'pos.reports'];db.commit()
    cid,v=newcart(r)
    with browser(r) as page:
        # Upgrade from old unscoped cart; server still validates ownership/session.
        page.goto('https://state.test/crm/pos');page.evaluate('(id)=>sessionStorage.setItem("pixiepinks-pos-cart",id)',cid);page.reload()
        page.locator('#pos-checkout').click();page.get_by_role('button',name='Confirm tender & checkout',exact=True).click()
        page.get_by_text('Shopify: QUEUED · Accounting: PENDING',exact=True).wait_for()
        s.dispatch_one(r.sessions,r.pos)
        page.get_by_text('Shopify: VERIFIED · Accounting: COMPLETE',exact=True).wait_for()
        page.locator('#pos-modal-close').click();page.reload();page.locator('#pos-cashier').filter(has_text='daily-cashier' if staff else 'quick-admin').wait_for()
        assert not page.locator('#pos-modal').evaluate('(e)=>e.open')
        r.client.get('/logout');r.login('daily-cashier' if staff else 'quick-admin');page.reload()
        page.locator('#pos-session').filter(has_text='Open').wait_for();assert not page.locator('#pos-modal').evaluate('(e)=>e.open')
        page.locator('#pos-history').click();page.get_by_role('button',name='Open receipt / status',exact=True).first.click()
        page.get_by_text('Shopify: VERIFIED · Accounting: COMPLETE',exact=True).wait_for()
    assert len(r.pos.calls)==1


@pytest.mark.parametrize('state',['QUEUED','REVIEW','UNCERTAIN','AWAITING_PAYMENT','PAYMENT_READY'])
def test_owned_unresolved_sale_recovers_across_close_refresh(pos,state):
    r=pos;rid=checkout(r)
    with r.sessions() as db:db.get(POSCheckout,rid).status=state;db.commit()
    with browser(r) as page:
        page.goto('https://state.test/crm/pos');page.evaluate('(id)=>sessionStorage.setItem("pixiepinks-pos-sale",id)',rid);page.reload()
        page.get_by_text(state,exact=True).wait_for();page.locator('#pos-modal-close').click();page.reload();page.get_by_text(state,exact=True).wait_for()
        assert page.locator('#pos-recovery button').count()==1
    assert not r.pos.calls


def test_cross_account_state_does_not_adopt_admin_session_or_sale(connector,monkeypatch):
    r=release(connector,monkeypatch);r.login('daily-cashier');rid=sale(r);s.dispatch_one(r.sessions,r.pos)
    with browser(r) as page:
        page.goto('https://state.test/crm/pos')
        scope=r.client.get('/crm/pos/api/context').json();key=f"pixiepinks-pos:{scope['shop']}:{scope['cashier_id']}:sale"
        page.evaluate('(v)=>{sessionStorage.setItem(v.key,v.id);sessionStorage.setItem("pixiepinks-pos-sale",v.id)}',{'key':key,'id':rid})
        r.client.get('/logout');r.login('quick-admin');page.reload()
        page.locator('#pos-cashier').filter(has_text='quick-admin').wait_for()
        page.get_by_text('No open session',exact=True).wait_for()
        assert not page.locator('#pos-modal').evaluate('(e)=>e.open')
        assert r.client.get('/crm/pos/api/context').json()['sessions']==[]
        r.login('daily-cashier');page.reload();page.locator('#pos-session').filter(has_text='Open').wait_for()
        assert not page.locator('#pos-modal').evaluate('(e)=>e.open')


def test_register_open_confirmation_refresh_idempotency_conflict_and_handover(connector,monkeypatch):
    r=release(connector,monkeypatch);r.login('daily-cashier')
    with r.sessions() as db:s.close_register(db,db.get(User,r.uid),r.sid,'1000','Opening UI fixture')
    with browser(r) as page:
        page.goto('https://state.test/crm/pos');page.get_by_text('No open session',exact=True).wait_for()
        page.locator('#pos-open').click();page.get_by_label('Counted opening float',exact=True).fill('0.00')
        page.get_by_role('button',name='Confirm opening float',exact=True).dblclick()
        page.get_by_text('Register opened successfully · daily-cashier · opening float Rs. 0.00',exact=True).wait_for()
        ctx=r.client.get('/crm/pos/api/context').json();sid=ctx['sessions'][0]['id']
        page.reload();page.get_by_text('Register open · daily-cashier · opening float Rs. 0.00. Ready to scan products.',exact=True).wait_for()
        with r.sessions() as db:
            u=db.get(User,r.uid);assert s.open_register(db,u,r.register_id,'900').id==sid
            assert str(db.get(POSSession,sid).opening_float)=='0.00'
            assert db.query(POSSession).filter_by(status='OPEN').count()==1
            with pytest.raises(HTTPException):s.open_register(db,db.get(User,r.admin_id),r.register_id,'0')
            db.rollback()
        r.login('quick-admin');page.reload();page.get_by_text('No open session',exact=True).wait_for()
        assert r.client.get('/crm/pos/api/context').json()['sessions']==[]
        page.locator('#pos-open').click();page.get_by_role('button',name='Confirm opening float',exact=True).click()
        page.get_by_text('Register already open; close it before handover',exact=True).first.wait_for()
        page.locator('#pos-modal-close').click()
        with r.sessions() as db:
            assert db.query(POSSession).filter_by(status='OPEN').one().id==sid
            assert db.query(POSAudit).filter_by(reference=sid,action='REGISTER_OPENED').count()==1
        # Normal explicit close then handover on disposable fixtures, never production.
        with r.sessions() as db:
            s.close_register(db,db.get(User,r.uid),sid,'0','Authorized handover fixture')
            admin_sid=s.open_register(db,db.get(User,r.admin_id),r.register_id,'0').id
        page.reload();page.locator('#pos-session').filter(has_text='Open').wait_for()
        assert r.client.get('/crm/pos/api/context').json()['sessions'][0]['id']==admin_sid
        r.login('daily-cashier');page.reload();page.get_by_text('No open session',exact=True).wait_for()


def test_open_committed_but_context_refresh_fails_keeps_success_and_existing_session(connector,monkeypatch):
    r=release(connector,monkeypatch);r.login('daily-cashier')
    with r.sessions() as db:s.close_register(db,db.get(User,r.uid),r.sid,'1000','UI fault fixture')
    with browser(r) as page:
        page.goto('https://state.test/crm/pos');page.get_by_text('No open session',exact=True).wait_for()
        page.locator('#pos-open').click()
        page.route('**/crm/pos/api/context',lambda handler:handler.fulfill(status=503,json={'detail':'Unavailable'}))
        page.get_by_role('button',name='Confirm opening float',exact=True).click()
        page.get_by_text('Do not submit another opening float.',exact=False).wait_for()
        with r.sessions() as db:
            row=db.query(POSSession).filter_by(status='OPEN',cashier_id=r.uid).one();sid=row.id
            assert row.opening_float==0 and db.query(POSAudit).filter_by(reference=sid,action='REGISTER_OPENED').count()==1
        page.unroute('**/crm/pos/api/context');page.reload()
        page.get_by_text('Register open · daily-cashier · opening float Rs. 0.00. Ready to scan products.',exact=True).wait_for()
        assert r.client.get('/crm/pos/api/context').json()['sessions'][0]['id']==sid


def test_context_shop_identity_and_readonly_session_lookup(pos,monkeypatch):
    r=pos;rid=checkout(r)
    from pos_models import POSCashMovement
    with r.sessions() as db:
        before=(db.query(POSSession).count(),db.query(POSCashMovement).count(),db.query(POSAudit).count())
    context=r.client.get('/crm/pos/api/context').json()
    assert context['cashier_id']==r.uid and context['sessions'][0]['id']==r.sid
    assert context['recovery_sales'][0]['id']==rid
    monkeypatch.setattr(s.retail,'shop',lambda:'unrelated-shop')
    other=r.client.get('/crm/pos/api/context').json()
    assert other['shop']=='unrelated-shop' and other['sessions']==other['recovery_sales']==[]
    with r.sessions() as db:
        assert before==(db.query(POSSession).count(),db.query(POSCashMovement).count(),db.query(POSAudit).count())
