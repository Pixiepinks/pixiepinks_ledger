"""Offline Phase 1B contract tests. ALL remote mutations are fixture-only."""
import copy
from datetime import datetime,timedelta
import json
import pytest
import httpx
from fastapi import HTTPException
from models import User
from retail_models import *
from settings import settings
import retail_service as service
import retail_barcode_sync as sync
from retail_barcode_shopify import BarcodeClient,RemoteError,PROBE,READ
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate


class Remote:
    def __init__(self):
        self.states={};self.calls=[];self.writes=[];self.scope=True;self.supported=True;self.version='2026-10';self.uncertain=False;self.reject=False;self.unavailable=False
    def capabilities(self):return {'supported':self.supported,'scope':self.scope,'version':self.version,'reason':'FIXTURE_ONLY'}
    def inspect(self,snap):
        self.calls.append(snap['variant_id'])
        if self.unavailable:raise RemoteError('THROTTLED')
        value=copy.deepcopy(self.states.get(snap['variant_id'],{'complete':True,'variant_id':snap['variant_id'],'product_id':snap['product_id'],'status':'ACTIVE','codes':[]}))
        value.update(version=self.version,collision_complete=True,collision_ids=[vid for vid,state in self.states.items() if snap['code'] in state.get('codes',[])])
        return value
    def write_empty(self,snap):
        self.writes.append(copy.deepcopy(snap))
        if not self.reject:self.states[snap['variant_id']]={'complete':True,'variant_id':snap['variant_id'],'product_id':snap['product_id'],'status':'ACTIVE','codes':[snap['code']]}
        if self.uncertain:raise RemoteError('uncertain')


@pytest.fixture
def fixture(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1,sku='ONE'),node(2,sku='TWO')]);allocate(r,choose(r,ids=[1,2]))
    remote=Remote();monkeypatch.setattr(sync,'client',remote)
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',True);monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
    r.remote=remote
    return r


def preview(r,ids=None):
    response=r.post('barcode-sync/preview',{'selection':choose(r,ids=ids or [1])});assert response.status_code==200,response.text
    return response.json()


def approve(r,p):
    return r.post(f"barcode-sync/{p['id']}/approve",{'token':p['approval_token'],'confirmation':p['confirmation']})


def run(r):assert sync.dispatch_one(r.sessions,r.remote)


def item(r):
    with r.sessions() as db:return db.query(RetailBarcodeItem).order_by(RetailBarcodeItem.id.desc()).first()


def test_verified_readback_minimal_identity_preserved_idempotency(fixture):
    r=fixture;p=preview(r);assert p['rows'][0]['status']=='ELIGIBLE'
    assert approve(r,p).status_code==200
    assert approve(r,p).status_code==200
    run(r);assert item(r).status=='VERIFIED' and item(r).attempts==1
    assert len(r.remote.writes)==1 and r.remote.writes[0]['code']=='P1'
    assert not sync.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        row=db.get(RetailVariant,1);assert row.barcode is None and row.local_barcode=='P1' and row.sku=='ONE' and str(row.price)=='8500.00'
        assert db.query(RetailBarcodeReservation).count()==0
        assert db.query(RetailBarcodeEvent).filter_by(action='DISPATCH_INTENT').count()==1
    listed=r.client.get('/crm/retail/api/products').json()['rows'][0]
    assert 'VERIFIED_ON_SHOPIFY' in listed['issues'] and listed['identifier_source']=='LOCAL_VERIFIED_SHOPIFY'
    # A later read-only import observes the remote value without erasing the ledger.
    import_nodes(r,[node(1,sku='ONE',barcode='P1'),node(2,sku='TWO')])
    with r.sessions() as db:assert db.get(RetailVariant,1).local_barcode=='P1' and db.query(RetailIdentifier).count()==2


@pytest.mark.parametrize('change,reason',[
    ('existing','REMOTE_BARCODE_NONEMPTY'),('secondary','REMOTE_BARCODE_NONEMPTY'),
    ('ambiguous','AMBIGUOUS_REMOTE_BARCODES'),('identity','REMOTE_IDENTITY_CHANGED'),
    ('inactive','REMOTE_INACTIVE_OR_REMOVED'),('reused','REMOTE_BARCODE_REUSED')])
def test_remote_changes_never_write(fixture,change,reason):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    state=r.remote.inspect(p['rows'][0]['snapshot'])
    if change=='existing':state['codes']=['supplier']
    elif change=='secondary':state['codes']=['','secondary']
    elif change=='ambiguous':state['complete']=False
    elif change=='identity':state['product_id']='gid://shopify/Product/999'
    elif change=='inactive':state['status']='ARCHIVED'
    elif change=='reused':r.remote.states['gid://shopify/ProductVariant/999']={'codes':['P1']}
    r.remote.states['gid://shopify/ProductVariant/1']=state
    run(r);assert item(r).status=='CONFLICT' and item(r).reason==reason
    assert not r.remote.writes


@pytest.mark.parametrize('change,reason',[
    ('stale','STALE_IMPORT'),('inactive','INACTIVE_OR_REMOVED'),('missing','INACTIVE_OR_REMOVED'),
    ('duplicate_code','DUPLICATE_BARCODE'),('duplicate_sku','DUPLICATE_SKU_NEEDS_REVIEW'),('nonempty','IMPORTED_BARCODE_NONEMPTY'),('no_ledger','NO_COMMITTED_LOCAL_BARCODE')])
def test_local_candidate_classification(fixture,change,reason):
    r=fixture
    with r.sessions() as db:
        row=db.get(RetailVariant,1);other=db.get(RetailVariant,2)
        if change=='stale':row.stale=True
        elif change=='inactive':row.product_status='DRAFT'
        elif change=='missing':row.missing=True
        elif change=='duplicate_code':other.barcode='P1'
        elif change=='duplicate_sku':other.sku=row.sku
        elif change=='nonempty':row.barcode='supplier'
        elif change=='no_ledger':row.local_barcode='UNAPPROVED'
        db.commit()
    p=preview(r);assert p['rows'][0]['status']=='BLOCKED' and p['rows'][0]['reason']==reason
    assert approve(r,p).status_code==409 and not r.remote.writes


def test_documented_supplier_sharing_preserved_and_duplicate_skus_distinct(fixture):
    r=fixture
    with r.sessions() as db:
        db.get(RetailVariant,1).sku='shared';db.get(RetailVariant,2).sku='shared'
        db.add(RetailException(shop=service.shop(),kind='sku',value='shared',variant_ids=[1,2],reason='Intentional supplier SKU sharing',actor='admin'));db.commit()
    assert preview(r)['rows'][0]['status']=='ELIGIBLE'
    with r.sessions() as db:
        db.get(RetailVariant,1).barcode='SUPPLIER';db.get(RetailVariant,2).barcode='SUPPLIER'
        db.add(RetailException(shop=service.shop(),kind='barcode',value='SUPPLIER',variant_ids=[1,2],reason='Intentional supplier barcode sharing',actor='admin'));db.commit()
    assert preview(r)['rows'][0]['reason']=='IMPORTED_BARCODE_NONEMPTY'
    assert r.client.get('/crm/retail/api/scan?code=SUPPLIER').json()['total']==2


@pytest.mark.parametrize('setting',['SHOPIFY_PRODUCT_WRITE_ENABLED','RETAIL_BARCODE_SYNC_ENABLED'])
def test_each_default_off_flag_blocks_approval_and_dispatch(fixture,monkeypatch,setting):
    r=fixture;p=preview(r);monkeypatch.setattr(settings,setting,False)
    assert approve(r,p).status_code==409 and not r.remote.writes
    monkeypatch.setattr(settings,setting,True);assert approve(r,p).status_code==200
    monkeypatch.setattr(settings,setting,False);assert not sync.dispatch_one(r.sessions,r.remote)


def test_scope_schema_version_expiry_and_exact_confirmation(fixture):
    r=fixture;p=preview(r)
    bad=r.post(f"barcode-sync/{p['id']}/approve",{'token':p['approval_token'],'confirmation':'yes'})
    assert bad.status_code==422
    for field in ['scope','supported']:
        setattr(r.remote,field,False);assert approve(r,p).status_code==409;setattr(r.remote,field,True)
    r.remote.version='2027-01';assert approve(r,p).status_code==409;r.remote.version='2026-10'
    with r.sessions() as db:db.get(RetailBarcodeJob,p['id']).expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    assert approve(r,p).status_code==409
    assert not r.remote.writes


def test_stale_preview_and_queued_import_version(fixture):
    r=fixture;p=preview(r)
    with r.sessions() as db:db.get(RetailVariant,1).version+=1;db.commit()
    assert approve(r,p).status_code==409
    p=preview(r);assert approve(r,p).status_code==200
    import_nodes(r,[node(1,sku='ONE'),node(2,sku='TWO')]);run(r)
    assert item(r).reason=='LOCAL_SNAPSHOT_CHANGED' and not r.remote.writes


@pytest.mark.parametrize('uncertain,reject,expected',[(True,False,'VERIFIED'),(True,True,'UNKNOWN'),(False,True,'UNKNOWN')])
def test_uncertain_mutation_is_read_back_never_blindly_resent(fixture,uncertain,reject,expected):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    r.remote.uncertain=uncertain;r.remote.reject=reject;run(r)
    assert item(r).status==expected and len(r.remote.writes)==1
    assert not sync.dispatch_one(r.sessions,r.remote)
    if expected=='UNKNOWN':
        assert r.post(f"barcode-sync/{p['id']}/reconcile",{}).status_code==200
        run(r);assert item(r).status=='UNKNOWN' and len(r.remote.writes)==1
        assert preview(r)['rows'][0]['reason']=='ALREADY_RESERVED'


def test_read_throttle_retry_backoff_and_terminal_failure(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200;r.remote.unavailable=True
    for attempt in range(1,4):
        run(r);i=item(r);assert i.attempts==attempt
        if attempt<3:
            assert i.status=='QUEUED' and i.next_attempt_at>datetime.utcnow()
            assert not sync.dispatch_one(r.sessions,r.remote)
            with r.sessions() as db:db.get(RetailBarcodeItem,i.id).next_attempt_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    assert item(r).status=='FAILED' and not r.remote.writes


def test_crash_recovery_sent_marker_only_reads_and_recovers_checking(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    with r.sessions() as db:
        i=db.query(RetailBarcodeItem).one();i.status='SENT';i.sent_at=datetime.utcnow();i.lease_until=datetime.utcnow()-timedelta(minutes=1);db.commit()
    run(r);assert item(r).status=='UNKNOWN' and not r.remote.writes


def test_cancel_reservation_repeat_submission_and_limits(fixture,monkeypatch):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    assert r.post(f"barcode-sync/{p['id']}/cancel",{}).status_code==200
    assert not sync.dispatch_one(r.sessions,r.remote)
    assert r.post(f"barcode-sync/{p['id']}/cancel",{}).status_code==200
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_BATCH_LIMIT',1)
    assert r.post('barcode-sync/preview',{'selection':choose(r,ids=[1,2])}).status_code==422


def test_permission_csrf_wrong_shop_ownership_safe_export(fixture):
    r=fixture
    with r.sessions() as db:db.get(RetailVariant,1).title='=PRIVATE()';db.commit()
    p=preview(r)
    exported=r.client.get(f"/crm/retail/api/barcode-sync/{p['id']}/export.csv")
    assert "'=PRIVATE()" in exported.text
    response=r.client.post('/crm/retail/api/barcode-sync/preview',json={'selection':'bad'})
    assert response.status_code==403
    assert r.post(f"barcode-sync/{p['id']}/approve",{'token':'wrong','confirmation':p['confirmation']}).status_code==403
    with r.sessions() as db:db.get(RetailBarcodeJob,p['id']).shop='another-shop';db.commit()
    assert approve(r,p).status_code==404
    r.login('quick-staff');assert r.client.get('/crm/retail/api/barcode-sync').status_code==403
    assert r.client.get('/crm/retail/barcode-sync').status_code==403


def test_rate_limit_and_admin_only_default(fixture):
    r=fixture
    for _ in range(5):preview(r)
    assert r.post('barcode-sync/preview',{'selection':choose(r,ids=[1])}).status_code==429
    with r.sessions() as db:
        staff=db.query(User).filter_by(username='quick-staff').one();assert not staff.section_access or sync.PERMISSION not in staff.section_access


def test_real_adapter_legacy_and_multiple_schemas_never_authorize_writes(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','fixture');monkeypatch.setattr(settings,'SHOPIFY_CLIENT_ID','fixture');monkeypatch.setattr(settings,'SHOPIFY_CLIENT_SECRET','fixture')
    calls=[]
    def handle(request):
        body=json.loads(request.content);calls.append(body)
        assert body['query'].lstrip().startswith('query ')  # Introspection may name mutation types.
        data={'shop':{'myshopifyDomain':'fixture.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'write_products'}]},'variant':{'fields':[{'name':'barcode'},{'name':'barcodes'}]},'input':{'inputFields':[{'name':'barcode'},{'name':'barcodes'}]}}
        return httpx.Response(200,json={'data':data},headers={'X-Shopify-API-Version':'2026-10'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        remote=BarcodeClient(http_client=http);remote._token='fixture';remote._expires_at=10**12
        cap=remote.capabilities();assert cap['scope'] and cap['multiple_barcode_field'] and not cap['supported']
        with pytest.raises(RemoteError):remote.write_empty({})
        assert len(calls)==1


def test_browser_review_confirmation_queue_and_verified_history(fixture,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    r=fixture;p=preview(r);errors=[]
    with api.sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=chromium,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1366,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
        def serve(route):
            request=route.request;url=urlsplit(request.url)
            if url.hostname!='barcode.test':route.fulfill(status=200,body='');return
            response=r.client.request(request.method,url.path+('?'+url.query if url.query else ''),content=request.post_data_buffer,
                headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',serve);page.goto('https://barcode.test/crm/retail/barcode-sync')
        page.get_by_role('heading',name=f"Batch #{p['id']} · PREVIEW · quick-admin").wait_for()
        button=page.get_by_role('button',name=f"Approve and queue batch {p['id']}",exact=True)
        monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',False)
        page.get_by_role('button',name='Refresh history and gates').click()
        page.get_by_text('Write flags: disabled',exact=False).wait_for();assert button.is_disabled()
        monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
        page.get_by_role('button',name='Refresh history and gates').click();page.get_by_text('Write flags: enabled',exact=False).wait_for()
        page.get_by_label(f"Confirmation for batch {p['id']}").fill(p['confirmation']);button.click()
        page.get_by_role('heading',name=f"Batch #{p['id']} · QUEUED · quick-admin").wait_for()
        run(r);page.get_by_role('button',name='Refresh history and gates').click()
        page.get_by_role('heading',name=f"Batch #{p['id']} · COMPLETE · quick-admin").wait_for()
        assert 'FRESH_READBACK_CONFIRMED' in page.locator('#barcode-sync-jobs').inner_text()
        assert not errors
        page.screenshot(path='/tmp/phase1b-review.png',full_page=True);browser.close()


def test_readback_unavailable_then_reconcile_with_writes_disabled(fixture,monkeypatch):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    original=r.remote.write_empty
    def uncertain(snap):original(snap);r.remote.unavailable=True;raise RemoteError('SECRET_DO_NOT_LOG')
    r.remote.write_empty=uncertain;run(r)
    assert item(r).status=='UNKNOWN' and len(r.remote.writes)==1
    r.remote.unavailable=False;monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',False)
    assert r.post(f"barcode-sync/{p['id']}/reconcile",{}).status_code==200
    run(r);assert item(r).status=='VERIFIED' and len(r.remote.writes)==1
    with r.sessions() as db:assert 'SECRET' not in str([e.detail for e in db.query(RetailBarcodeEvent)])


def test_revoked_approver_prevents_dispatch_and_other_actor_cannot_approve(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    with r.sessions() as db:
        user=db.get(User,r.uid);user.is_active=False;db.commit()
    run(r);assert item(r).reason=='APPROVER_ACCESS_REVOKED' and not r.remote.writes


def test_preview_digest_tamper_is_detected_and_scope_required_at_dispatch(fixture):
    r=fixture;p=preview(r)
    with r.sessions() as db:
        i=db.query(RetailBarcodeItem).one()
        with pytest.raises(Exception):
            i.snapshot={**i.snapshot,'code':'TAMPERED'};db.commit()
        db.rollback()
        db.get(RetailBarcodeJob,p['id']).digest='0'*64;db.commit()
    assert approve(r,p).status_code==409
    p=preview(r);assert approve(r,p).status_code==200;r.remote.scope=False;run(r)
    assert item(r).reason=='SCOPE_OR_SCHEMA_UNVERIFIED' and not r.remote.writes


def test_api_version_change_at_final_dispatch_boundary_blocks_write(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200
    original=r.remote.capabilities;calls=[]
    def changed():
        calls.append(True)
        if len(calls)==2:r.remote.version='2027-01'
        return original()
    r.remote.capabilities=changed;run(r)
    assert item(r).reason=='API_VERSION_CHANGED' and not r.remote.writes


def test_contradictory_legacy_barcode_never_marks_verified(fixture):
    r=fixture;p=preview(r);assert approve(r,p).status_code==200;original=r.remote.write_empty
    def write(snap):
        original(snap);r.remote.states[snap['variant_id']]['legacy_barcode']='UNEXPECTED'
    r.remote.write_empty=write;run(r)
    assert item(r).status=='CONFLICT' and item(r).reason=='READBACK_MISMATCH'


def test_disabled_writes_do_not_starve_later_readonly_recovery(fixture,monkeypatch):
    r=fixture;first=preview(r,[1]);assert approve(r,first).status_code==200
    second=preview(r,[2]);assert approve(r,second).status_code==200
    with r.sessions() as db:
        i=db.query(RetailBarcodeItem).filter_by(job_id=second['id']).one();i.status='UNKNOWN';i.sent_at=datetime.utcnow();db.commit()
    snap=second['rows'][0]['snapshot'];r.remote.states[snap['variant_id']]={'complete':True,'variant_id':snap['variant_id'],'product_id':snap['product_id'],'status':'ACTIVE','codes':[snap['code']]}
    assert r.post(f"barcode-sync/{second['id']}/reconcile",{}).status_code==200
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',False);run(r)
    with r.sessions() as db:
        assert db.query(RetailBarcodeItem).filter_by(job_id=first['id']).one().status=='QUEUED'
        assert db.query(RetailBarcodeItem).filter_by(job_id=second['id']).one().status=='VERIFIED'
    assert not r.remote.writes
