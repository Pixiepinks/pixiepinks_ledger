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
from retail_barcode_protection import PROTECTED as NORMAL_PROTECTED
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
    assert contract.MUTATION_SEMANTICS_VERIFIED is True
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
        elif q in {PROTECTED,NORMAL_PROTECTED}:
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
    assert contract.MUTATION_SEMANTICS_VERIFIED is True
    assert not settings.SHOPIFY_PRODUCT_WRITE_ENABLED and not settings.RETAIL_BARCODE_SYNC_ENABLED


def test_pilot_preparation_execution_and_transport_permanently_retired(p1):
    r=p1
    for action in [lambda:pilot.prepare(r.engine,r.uid,editors_paused=True,risk_accepted=True),
                   lambda:pilot.execute(r.engine,r.uid,nonce='0'*64,confirmation=pilot.CONFIRMATION,editors_paused=True,risk_accepted=True)]:
        with pytest.raises(HTTPException) as error:action()
        assert error.value.status_code==410
    with pytest.raises(RemoteError,match='P1_PILOT_PERMANENTLY_RETIRED'):r.remote._attempt_p1(object())
    assert not r.state.writes


@pytest.mark.parametrize('configured_shop',['eb2b70','EB2B70'])
def test_normal_adapter_cannot_replay_p1_even_with_flags_enabled(p1,monkeypatch,configured_shop):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP',configured_shop)
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',True)
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
    with pytest.raises(RemoteError,match='COMPLETED_P1_PILOT_CANNOT_REPLAY'):p1.remote.write_empty(dict(IDENTITY))
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',False)
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',False)
    assert not p1.state.writes
