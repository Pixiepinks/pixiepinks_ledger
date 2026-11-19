"""Real disposable PostgreSQL; every Shopify response uses MockTransport."""
import copy
import json
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException,FastAPI,Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from retail_models import *
from models import User
from settings import settings
from test_retail_postgres import pg
from test_shopify_barcode_pagination import probe
import retail_barcode_p1_pilot as pilot
from retail_barcode_p1_remote import P1Client,IDENTITY,PAYLOAD,PROTECTED,exact_identity
from retail_barcode_shopify import PROBE,READ,CATALOG,CATALOG_IDS,MUTATION,RemoteError
import retail_barcode_contract as contract
import retail_barcode_sync as ordinary


def attrs():
    return {'id':IDENTITY['variant_id'],'sku':'1001200','price':'600.00','compareAtPrice':None,'taxable':False,
        'taxCode':None,'position':1,'inventoryPolicy':'DENY','inventoryQuantity':4,
        'selectedOptions':[{'name':'Colour','value':'Pink'}],'image':{'id':'image1','url':'https://cdn.shopify.com/1'},
        'inventoryItem':{'id':'gid://shopify/InventoryItem/1','sku':'1001200','tracked':True,'requiresShipping':True},
        'product':{'id':IDENTITY['product_id'],'title':'Kids Projector Torch - Pink','handle':'pink-torch','status':'ACTIVE',
            'descriptionHtml':'<p>Torch</p>','vendor':'PixiePinks','productType':'Toy','tags':['torch'],
            'options':[{'id':'option1','name':'Colour','values':['Pink']}],'featuredImage':None}}


@pytest.fixture
def p1(pg,monkeypatch):
    engine,sessions,uid=pg
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','eb2b70')
    monkeypatch.setattr(settings,'SHOPIFY_CLIENT_ID','fixture');monkeypatch.setattr(settings,'SHOPIFY_CLIENT_SECRET','fixture')
    assert contract.MUTATION_SEMANTICS_VERIFIED is False
    assert not settings.SHOPIFY_PRODUCT_WRITE_ENABLED and not settings.RETAIL_BARCODE_SYNC_ENABLED
    with sessions() as db:
        db.add(RetailShop(shop='eb2b70'))
        row=RetailVariant(shop='eb2b70',product_id=IDENTITY['product_id'],variant_id=IDENTITY['variant_id'],
            title='Kids Projector Torch - Pink',variant_title='Default Title',sku='1001200',local_barcode='P1',
            product_status='ACTIVE',synced_at=datetime.utcnow(),version=2)
        db.add(row);db.flush();rid=row.id
        db.add(RetailIdentifier(id=1,shop='eb2b70',variant_id=rid,kind='barcode',value='P1',actor='admin'));db.commit()
    state=SimpleNamespace(codes=[],other=[],attributes=attrs(),writes=[],calls=[],mode=None,levels=[],images=[],protected_calls=0)
    def connection(values,after):
        start=int(after or 0);nodes=values[start:start+20]
        return {'nodes':nodes,'pageInfo':{'hasNextPage':start+len(nodes)<len(values),'endCursor':str(start+len(nodes))}}
    def node(vid):
        codes=state.codes if vid==IDENTITY['variant_id'] else state.other
        return {'id':vid,'barcode':None,'product':{'id':IDENTITY['product_id'],'status':'ACTIVE'},
            'barcodes':{'nodes':copy.deepcopy(codes),'pageInfo':{'hasNextPage':False,'endCursor':str(len(codes))}}}
    def handle(req):
        body=json.loads(req.content);state.calls.append(body);q=body['query'];v=body['variables']
        assert str(req.url)=='https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json'
        if q==PROBE:data=probe()
        elif q==PROTECTED:
            state.protected_calls+=1;row=copy.deepcopy(state.attributes)
            row['inventoryItem']['inventoryLevels']=connection(state.levels,v['levels'])
            row['product']['images']=connection(state.images,v['images'])
            data={'productVariant':row}
        elif q==CATALOG:data={'productVariants':{'nodes':[node(IDENTITY['variant_id']),node('gid://shopify/ProductVariant/2')],'pageInfo':{'hasNextPage':False,'endCursor':'end'}}}
        elif q==CATALOG_IDS:data={'productVariants':{'nodes':[{'id':IDENTITY['variant_id']},{'id':'gid://shopify/ProductVariant/2'}],'pageInfo':{'hasNextPage':False,'endCursor':'end'}}}
        elif q==READ:data={'productVariant':node(v['id'])}
        elif q==MUTATION:
            state.writes.append(body);assert v==PAYLOAD
            if state.mode!='lost':state.codes=[{'value':'P1','type':None}]
            if state.mode in {'timeout','lost'}:raise httpx.ReadTimeout('private upstream details')
            if state.mode=='crash':raise SystemExit('simulated process death')
            if state.mode=='typed':state.codes[0]['type']='UPC'
            if state.mode=='price':state.attributes['price']='601.00'
            if state.mode=='inventory':state.attributes['inventoryQuantity']=5
            if state.mode=='title':state.attributes['product']['title']='Changed elsewhere'
            data={'productVariantsBulkUpdate':{'product':{'id':IDENTITY['product_id']},'productVariants':[{'id':IDENTITY['variant_id']}],'userErrors':[]}}
        else:raise AssertionError('Unexpected query')
        return httpx.Response(200,json={'data':data},headers={'X-Shopify-API-Version':'2026-10'})
    http=httpx.Client(transport=httpx.MockTransport(handle));remote=P1Client(http_client=http)
    remote._token='fixture';remote._expires_at=10**12
    monkeypatch.setattr(pilot,'P1Client',lambda:remote)
    yield SimpleNamespace(engine=engine,sessions=sessions,uid=uid,rid=rid,remote=remote,state=state)
    http.close()
    assert contract.MUTATION_SEMANTICS_VERIFIED is False
    assert not settings.SHOPIFY_PRODUCT_WRITE_ENABLED and not settings.RETAIL_BARCODE_SYNC_ENABLED


def prepare(r):return pilot.prepare(r.engine,r.uid,editors_paused=True,risk_accepted=True,remote=r.remote)


def execute(r,prepared,**kw):
    return pilot.execute(r.engine,r.uid,remote=r.remote,**{'nonce':prepared['nonce'],'confirmation':pilot.CONFIRMATION,
        'editors_paused':True,'risk_accepted':True,**kw})


def test_preparation_cannot_mutate_and_one_exact_attempt_verifies(p1):
    r=p1;p=prepare(r)
    assert p['status']=='PILOT_PREPARED' and p['attempts']==0 and not r.state.writes
    result=execute(r,p)
    assert result['status']=='PILOT_VERIFIED' and result['attempts']==1
    assert result['after']['protected_unchanged'] and len(r.state.writes)==1
    assert r.state.writes[0]['variables']==PAYLOAD
    assert 'type' not in PAYLOAD['variants'][0]['barcodes'][0]
    with pytest.raises(HTTPException):execute(r,p)
    assert pilot.reconcile(r.engine,r.uid,r.remote)['status']=='PILOT_VERIFIED'
    assert len(r.state.writes)==1
    with r.sessions() as db:
        job,item=pilot.records(db)
        assert job.status==item.status=='PILOT_VERIFIED'
        assert db.query(RetailBarcodeEvent).filter_by(job_id=job.id).count()>=4
        row=db.get(RetailVariant,r.rid)
        assert row.local_barcode=='P1' and row.barcode is None and row.version==2
        assert db.get(RetailIdentifier,1).value=='P1'
        import retail_service as service
        assert 'VERIFIED_ON_SHOPIFY' in service.issues(row,service.audit_context(db))
        assert not db.query(RetailBarcodeReservation).count()


@pytest.mark.parametrize('field,value',[('local_barcode','P2'),('version',3),('sku','OTHER'),
    ('variant_id','gid://shopify/ProductVariant/2'),('product_id','gid://shopify/Product/2')])
def test_any_changed_identity_including_p2_cannot_be_prepared(p1,field,value):
    r=p1
    with r.sessions() as db:setattr(db.get(RetailVariant,r.rid),field,value);db.commit()
    with pytest.raises(HTTPException):prepare(r)
    assert not r.state.writes


def test_changed_allocation_after_prepare_cannot_execute(p1):
    r=p1;p=prepare(r)
    with r.sessions() as db:db.get(RetailVariant,r.rid).version=3;db.commit()
    with pytest.raises(HTTPException):execute(r,p)
    assert not r.state.writes


@pytest.mark.parametrize('job_status,item_status,sent',[('QUEUED','QUEUED',False),('RUNNING','CHECKING',False),('COMPLETE','UNKNOWN',True),('COMPLETE','CONFLICT',True)])
def test_existing_work_blocks_prepare_and_dispatch(p1,job_status,item_status,sent):
    r=p1;p=prepare(r)
    with r.sessions() as db:
        other=RetailVariant(shop='eb2b70',product_id='gid://shopify/Product/2',variant_id='gid://shopify/ProductVariant/2',
            title='Other product',variant_title='Other variant',sku='OTHER',local_barcode='P2',product_status='ACTIVE')
        db.add(other);db.flush()
        job=RetailBarcodeJob(shop='eb2b70',actor_id=r.uid,actor='admin',token='competing',status=job_status,api_version='2026-10',digest='x',expires_at=datetime.utcnow()+timedelta(minutes=5))
        db.add(job);db.flush();db.add(RetailBarcodeItem(job_id=job.id,variant_id=other.id,idempotency_key='competing',snapshot={'code':'P2'},before={},status=item_status,reason='fixture',sent_at=datetime.utcnow() if sent else None));db.commit()
    with pytest.raises(HTTPException):prepare(r)
    with pytest.raises(HTTPException):execute(r,p)
    assert not r.state.writes


@pytest.mark.parametrize('change',['supplier','secondary_collision','price','inventory','title'])
def test_fresh_preflight_rejects_new_remote_state(p1,change):
    r=p1;p=prepare(r)
    if change=='supplier':r.state.codes=[{'value':'KEEP','type':'EAN'}]
    elif change=='secondary_collision':r.state.other=[{'value':'SUPPLIER','type':'EAN'},{'value':'P1','type':None}]
    elif change=='price':r.state.attributes['price']='700.00'
    elif change=='inventory':r.state.attributes['inventoryQuantity']=9
    else:r.state.attributes['product']['title']='Another title'
    result=execute(r,p)
    assert result['status']=='PILOT_BLOCKED' and result['attempts']==0 and not r.state.writes


@pytest.mark.parametrize('mode,status',[('timeout','PILOT_VERIFIED'),('lost','PILOT_UNKNOWN'),('typed','PILOT_UNKNOWN'),
    ('price','PILOT_UNKNOWN'),('inventory','PILOT_UNKNOWN'),('title','PILOT_UNKNOWN')])
def test_one_attempt_errors_and_protected_readback_failures_never_resend(p1,mode,status):
    r=p1;p=prepare(r);r.state.mode=mode
    result=execute(r,p)
    assert result['status']==status and result['attempts']==1 and len(r.state.writes)==1
    with pytest.raises(HTTPException):execute(r,p)
    pilot.reconcile(r.engine,r.uid,r.remote)
    assert len(r.state.writes)==1


def test_durable_intent_survives_process_death_and_reconciliation_only_reads(p1):
    r=p1;p=prepare(r);r.state.mode='crash'
    with pytest.raises(SystemExit):execute(r,p)
    with r.sessions() as db:
        job,item=pilot.records(db);assert job.status=='PILOT_INTENT' and item.sent_at and item.attempts==1
    with pytest.raises(HTTPException):execute(r,p)
    assert pilot.reconcile(r.engine,r.uid,r.remote)['status']=='PILOT_VERIFIED'
    assert len(r.state.writes)==1


def test_two_concurrent_execute_requests_only_one_can_attempt(p1):
    r=p1;p=prepare(r)
    def run(_):
        try:return execute(r,p)['status']
        except HTTPException:return 'BLOCKED'
    with ThreadPoolExecutor(2) as workers:results=list(workers.map(run,[1,2]))
    assert sorted(results)==['BLOCKED','PILOT_VERIFIED'] and len(r.state.writes)==1


@pytest.mark.parametrize('change',['phrase','nonce','expired','digest','revoked'])
def test_explicit_approval_and_current_admin_are_required(p1,change):
    r=p1;p=prepare(r);kw={}
    if change=='phrase':kw['confirmation']=pilot.CONFIRMATION.replace('P1','P2')
    elif change=='nonce':kw['nonce']='0'*64
    else:
        with r.sessions() as db:
            job,item=pilot.records(db)
            if change=='expired':job.expires_at=datetime.utcnow()-timedelta(seconds=1)
            elif change=='digest':job.digest='tampered'
            else:db.get(User,r.uid).role='STAFF'
            db.commit()
    with pytest.raises(HTTPException):execute(r,p,**kw)
    assert not r.state.writes


def test_ordinary_worker_and_direct_write_remain_blocked(p1):
    r=p1;prepare(r)
    assert ordinary.dispatch_one(r.sessions,r.remote) is False
    with pytest.raises(RemoteError,match='MUTATION_SEMANTICS_UNVERIFIED'):r.remote.write_empty(dict(IDENTITY))
    with pytest.raises(RemoteError):r.remote._attempt_p1(object())
    assert not r.state.writes


def test_protected_connections_exhaust_all_levels_and_images(p1):
    r=p1
    r.state.levels=[{'id':str(i),'location':{'id':str(i)},'quantities':[{'name':name,'quantity':1} for name in ['available','on_hand','committed','incoming','reserved','damaged','safety_stock','quality_control']]} for i in range(102)]
    r.state.images=[{'id':str(i),'url':'https://cdn.shopify.com/image'} for i in range(103)]
    protected=r.remote.protected()
    assert len(protected['inventoryItem']['inventoryLevels'])==102 and len(protected['product']['images'])==103
    assert r.state.protected_calls==6 and not r.state.writes


def test_api_uses_existing_auth_csrf_rejects_targets_and_does_not_execute_on_prepare(p1):
    from retail_routes import build_router
    r=p1;app=FastAPI()
    def get_db():
        with r.sessions() as db:yield db
    def user():
        with r.sessions() as db:return db.get(User,r.uid)
    def csrf(req):
        if req.headers.get('X-CSRF-Token')!='fixture':raise HTTPException(403,'CSRF required')
    app.include_router(build_router(get_db,user,csrf,lambda _: 'fixture',None))
    c=TestClient(app);url='/crm/retail/api/barcode-p1-pilot'
    data={'editors_paused':True,'risk_accepted':True}
    assert c.post(url+'/prepare',json=data).status_code==403
    assert c.post(url+'/prepare',json={**data,'code':'P2'},headers={'X-CSRF-Token':'fixture'}).status_code==422
    response=c.post(url+'/prepare',json=data,headers={'X-CSRF-Token':'fixture'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='PILOT_PREPARED' and not r.state.writes
    assert response.headers['Cache-Control']=='no-store'
    assert c.post(url+'/execute',json={**data,'nonce':response.json()['nonce'],'confirmation':pilot.CONFIRMATION,'variant_id':'P2'},headers={'X-CSRF-Token':'fixture'}).status_code==422
    result=c.post(url+'/execute',json={**data,'nonce':response.json()['nonce'],'confirmation':pilot.CONFIRMATION},headers={'X-CSRF-Token':'fixture'})
    assert result.status_code==200,result.text
    assert result.json()['status']=='PILOT_VERIFIED' and len(r.state.writes)==1


def test_other_admin_and_delegated_nonadmin_cannot_use_approval(p1):
    r=p1;p=prepare(r)
    with r.sessions() as db:
        other=User(username='other-admin',password_hash='fixture',role='ADMIN',is_active=True)
        db.add(other);db.flush();other_id=other.id;db.commit()
    with pytest.raises(HTTPException):
        pilot.execute(r.engine,other_id,nonce=p['nonce'],confirmation=pilot.CONFIRMATION,editors_paused=True,risk_accepted=True,remote=r.remote)
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='STAFF';user.section_access=[ordinary.PERMISSION];db.commit()
    with pytest.raises(HTTPException):prepare(r)
    assert not r.state.writes


def test_protected_denial_missing_fields_and_cursor_fail_closed(p1):
    r=p1;original=r.remote.http.post
    def denied(url,*args,**kwargs):
        if kwargs['json']['query']==PROTECTED:
            return httpx.Response(200,json={'errors':[{'message':'private inventory access denied'}]},headers={'X-Shopify-API-Version':'2026-10'})
        return original(url,*args,**kwargs)
    r.remote.http.post=denied
    with pytest.raises(HTTPException) as error:prepare(r)
    assert error.value.detail=='P1_PREPARATION_REMOTE_VERIFICATION_FAILED'
    assert not r.state.writes


def test_old_nonce_is_invalidated_by_review_refresh(p1):
    r=p1;old=prepare(r);current=prepare(r)
    assert old['nonce']!=current['nonce']
    with pytest.raises(HTTPException):execute(r,old)
    assert execute(r,current)['status']=='PILOT_VERIFIED' and len(r.state.writes)==1


@pytest.mark.parametrize('failure',['missing_field','repeated_cursor','wrong_shop','incomplete_scan'])
def test_unverified_remote_contract_cannot_prepare(p1,failure):
    r=p1
    if failure=='wrong_shop':
        original=r.remote.capabilities
        r.remote.capabilities=lambda: {**original(),'shop_identity_verified':False}
    elif failure=='incomplete_scan':
        original=r.remote.inspect
        r.remote.inspect=lambda snap: {**original(snap),'complete':False}
    else:
        if failure=='repeated_cursor':r.state.images=[{'id':str(i),'url':'https://cdn.shopify.com/image'} for i in range(102)]
        original=r.remote.http.post
        def bad(url,*args,**kwargs):
            result=original(url,*args,**kwargs)
            if kwargs['json']['query']==PROTECTED:
                payload=result.json()
                if failure=='missing_field':del payload['data']['productVariant']['inventoryQuantity']
                else:payload['data']['productVariant']['product']['images']['pageInfo']={'hasNextPage':True,'endCursor':'100'}
                return httpx.Response(200,json=payload,headers=result.headers)
            return result
        r.remote.http.post=bad
    with pytest.raises(HTTPException):prepare(r)
    assert not r.state.writes


def test_token_refresh_cannot_outlive_the_final_target_deadline(p1):
    r=p1;p=prepare(r);attempt=r.remote._attempt_p1
    def expire_during_auth(permit):
        def token():permit.expires=-1;return 'fixture'
        r.remote.get_token=token
        return attempt(permit)
    r.remote._attempt_p1=expire_during_auth
    result=execute(r,p)
    assert result['status']=='PILOT_UNKNOWN' and result['attempts']==1 and not r.state.writes
    with pytest.raises(HTTPException):execute(r,p)
