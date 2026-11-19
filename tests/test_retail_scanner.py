"""Offline scanner and targeted inventory tests. Never connects to Shopify."""
import copy
import json
import subprocess
from pathlib import Path
import httpx
import pytest
from models import User
from retail_models import RetailVariant, RetailPreview, RetailPrintJob, RetailIdentifier
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail, node, import_nodes, allocate, choose
import retail_inventory as inventory


def contract(scopes=('read_products','read_inventory','read_locations')):
    def typ(name):
        if name.startswith('['):return {'kind':'LIST','ofType':typ(name[1:-1])}
        return {'kind':'SCALAR','name':name}
    groups={'item':{'id':'ID','tracked':'Boolean','inventoryLevels':'InventoryLevelConnection'},'variant':{'id':'ID','product':'Product','inventoryItem':'InventoryItem'},'connection':{'nodes':'[InventoryLevel]','pageInfo':'PageInfo'},'level':{'id':'ID','quantities':'[InventoryQuantity]','location':'Location'},'quantity':{'name':'String','quantity':'Int'},'page':{'hasNextPage':'Boolean','endCursor':'String'},'location':{'id':'ID','name':'String','isActive':'Boolean'}}
    data={key:{'fields':[{'name':n,'type':typ(t),'args':[]} for n,t in values.items()]} for key,values in groups.items()}
    for group,name,args in [('item','inventoryLevels',{'first':'Int','after':'String'}),('level','quantities',{'names':'[String]'})]:
        next(f for f in data[group]['fields'] if f['name']==name)['args']=[{'name':n,'type':typ(t)} for n,t in args.items()]
    data.update(shop={'myshopifyDomain':'retail-fixture.myshopify.com'},currentAppInstallation={'accessScopes':[{'handle':s} for s in scopes]})
    return data


def page(qty=7,more=False,cursor=None,tracked=True):
    return {'shop':{'myshopifyDomain':'retail-fixture.myshopify.com'},'productVariant':{'id':'gid://shopify/ProductVariant/1','product':{'id':'gid://shopify/Product/1','status':'ACTIVE'},'inventoryItem':{'id':'gid://shopify/InventoryItem/1','tracked':tracked,'inventoryLevels':{'nodes':[{'id':'level-'+str(cursor),'quantities':[{'name':'available','quantity':qty}],'location':{'id':'location-'+str(cursor),'name':'Shop','isActive':True}}],'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}}
SNAPSHOT={'shop':'retail-fixture','version':1,'variant_id':'gid://shopify/ProductVariant/1','product_id':'gid://shopify/Product/1','inventory_item_id':'gid://shopify/InventoryItem/1'}


def reader(monkeypatch,pages,scopes=('read_products','read_inventory','read_locations'),probe=None):
    for key,value in [('SHOPIFY_SHOP','retail-fixture'),('SHOPIFY_CLIENT_ID','offline'),('SHOPIFY_CLIENT_SECRET','offline'),('SHOPIFY_API_VERSION','2026-07')]:monkeypatch.setattr(inventory.settings,key,value)
    calls=[];iterator=iter(pages);clock=[0]
    def transport(req):
        payload=json.loads(req.content);calls.append(payload)
        assert req.url.path=='/admin/api/2026-10/graphql.json'
        assert payload['query'] in {inventory.PROBE,inventory.READ} and 'mutation' not in payload['query']
        data=(probe or contract(scopes)) if payload['query']==inventory.PROBE else next(iterator)
        if isinstance(data,httpx.Response):return data
        return httpx.Response(200,json={'data':data},headers={'X-Shopify-API-Version':'2026-10'})
    c=inventory.InventoryClient(httpx.Client(transport=httpx.MockTransport(transport)),clock=lambda:clock[0]);c._token='offline';c._expires_at=10**20
    return c,calls,clock


def test_complete_available_pagination_and_cache(monkeypatch):
    c,calls,clock=reader(monkeypatch,[page(7,True,'a'),page(-2,False,'b'),page(0)])
    value=c.lookup(SNAPSHOT);assert value['status']=='VERIFIED' and value['available']==5 and len(value['locations'])==2
    assert calls[2]['variables']['after']=='a' and 'names:["available"]' in inventory.READ and 'on_hand' not in inventory.READ
    clock[0]=10;assert c.lookup(SNAPSHOT)['cached'] is True and len(calls)==3
    clock[0]=21;assert c.lookup(SNAPSHOT)['available']==0 and len(calls)==4


@pytest.mark.parametrize('scopes,tracked,expected',[(['read_products'],True,'UNAVAILABLE'),(['read_products'],False,'UNTRACKED'),(['read_products','read_inventory'],True,'VERIFIED')])
def test_scope_tracking_and_location_states(monkeypatch,scopes,tracked,expected):
    c,calls,_=reader(monkeypatch,[page(0,tracked=tracked)],scopes)
    value=c.lookup(SNAPSHOT);assert value['status']==expected
    assert value['available']==(0 if expected=='VERIFIED' else None)
    assert calls[-1]['variables']['locations'] is False


@pytest.mark.parametrize('failure', ['cursor','duplicate','wrong_shop','wrong_variant','wrong_item','wrong_parent','inactive','quantity','page_info','api','rate','version','schema'])
def test_incomplete_or_unavailable_is_never_zero(monkeypatch,failure):
    p=page();pages=[p];probe=None
    if failure=='cursor':p['productVariant']['inventoryItem']['inventoryLevels']['pageInfo']={'hasNextPage':True,'endCursor':None}
    if failure=='duplicate':pages=[page(1,True,'same'),page(1,True,'same')]
    if failure=='wrong_shop':p['shop']['myshopifyDomain']='other.myshopify.com'
    if failure=='wrong_variant':p['productVariant']['id']='gid://shopify/ProductVariant/2'
    if failure=='wrong_item':p['productVariant']['inventoryItem']['id']='gid://shopify/InventoryItem/2'
    if failure=='wrong_parent':p['productVariant']['product']['id']='gid://shopify/Product/2'
    if failure=='inactive':p['productVariant']['product']['status']='ARCHIVED'
    if failure=='quantity':p['productVariant']['inventoryItem']['inventoryLevels']['nodes'][0]['quantities'][0]['name']='on_hand'
    if failure=='page_info':p['productVariant']['inventoryItem']['inventoryLevels']['pageInfo']={}
    if failure=='api':pages=[httpx.Response(200,json={'errors':[{'message':'private token'}]})]
    if failure=='rate':pages=[httpx.Response(429)]
    if failure=='version':pages=[httpx.Response(200,json={'data':p},headers={'X-Shopify-API-Version':'2027-01'})]
    if failure=='schema':probe=contract();probe['item']['fields']=[]
    c,_,_=reader(monkeypatch,pages,probe=probe);value=c.lookup(SNAPSHOT)
    assert value['status']=='UNAVAILABLE' and value['available'] is None and value['locations']==[] and 'private' not in str(value)


def test_scope_and_shop_fail_closed(monkeypatch):
    c,calls,_=reader(monkeypatch,[page()],scopes=[]);assert c.lookup(SNAPSHOT)['available'] is None
    c,calls,_=reader(monkeypatch,[page()]);assert c.lookup({**SNAPSHOT,'shop':'other'})['available'] is None and calls==[]
    with pytest.raises(inventory.InventoryError):c._read('mutation { bad }',{})


def test_barcode_exact_search_details_and_shop_isolation(retail):
    r=retail;import_nodes(r,[node(1,barcode='001234',sku='SKU-ONLY'),node(2,barcode='SHARED'),node(3,barcode='SHARED')])
    def scan(code):return r.client.get('/crm/retail/api/scan',params={'code':code})
    assert scan('001234').json()['total']==1 and scan('1234').json()['total']==0 and scan('SKU-ONLY').json()['total']==0
    assert scan('SHARED').json()['total']==2
    row=scan('001234').json()['rows'][0];url=f"/crm/retail/api/scanner/products/{row['id']}"
    assert r.client.get(url,params={'version':row['version']}).json()['row']['price']=='8500.00'
    assert r.client.get(url,params={'version':row['version']+1}).status_code==409
    assert r.client.get('/crm/retail/api/scanner/search',params={'mode':'sku','q':'SKU-ONLY'}).json()['total']==1
    assert r.client.get('/crm/retail/api/scanner/search',params={'mode':'name','q':'%'}).json()['total']==0
    with r.sessions() as db:v=db.get(RetailVariant,row['id']);v.shop='other';db.commit()
    assert scan('001234').json()['total']==0 and r.client.get(url,params={'version':row['version']}).status_code==404


def test_local_scan_label_handoff_and_version_guard(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1)]);allocate(r,choose(r,filters={}))
    row=r.client.get('/crm/retail/api/scan?code=P1').json()['rows'][0];url=f"scanner/products/{row['id']}"
    assert r.client.get('/crm/retail/scanner').status_code==200
    assert r.client.post('/crm/retail/api/'+url+'/labels',json={'version':row['version']}).status_code==403
    response=r.post(url+'/labels',{'version':row['version']});assert response.status_code==200,response.text
    with r.sessions() as db:
        assert db.query(RetailPrintJob).count()==0 and db.query(RetailIdentifier).count()==1
        assert db.query(RetailPreview).filter_by(token=response.json()['token'],kind='SELECTION').one().payload['ids']==[row['id']]
    assert r.post(url+'/labels',{'version':row['version']+1}).status_code==409
    def change(snapshot):
        with r.sessions() as db:v=db.get(RetailVariant,row['id']);v.version+=1;db.commit()
        return inventory.unavailable('OFFLINE')
    monkeypatch.setattr(inventory.client,'lookup',change)
    assert r.client.get('/crm/retail/api/'+url+'/inventory',params={'version':row['version']}).status_code==409


def test_scanner_permission_and_readonly_stock(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1,barcode='EXT')]);row=r.client.get('/crm/retail/api/scan?code=EXT').json()['rows'][0];base=f"/crm/retail/api/scanner/products/{row['id']}"
    monkeypatch.setattr(inventory.client,'lookup',lambda snapshot: inventory.unavailable('INVENTORY_SCOPE_UNAVAILABLE'))
    stock=r.client.get(base+'/inventory',params={'version':row['version']});assert stock.json()['available'] is None and stock.headers['cache-control']=='private, no-store'
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.products.view'];db.commit()
    assert r.client.get('/crm/retail/scanner').status_code==200
    assert r.post(f"scanner/products/{row['id']}/labels",{'version':row['version']}).status_code==403
    with r.sessions() as db:u=db.get(User,r.uid);u.section_access=[];db.commit()
    assert r.client.get(base,params={'version':row['version']}).status_code==403
    assert r.client.get(base+'/inventory',params={'version':row['version']}).status_code==403


def test_keyboard_controller():
    result=subprocess.run(['node','--test','tests/retail_scanner.test.js'],cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('code',['', 'x'*101, 'P1\n', 'P1\x7f'])
def test_invalid_scans(retail,code):
    assert retail.client.get('/crm/retail/api/scan',params={'code':code}).status_code==422


def test_p2_and_inactive_missing_stale(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1),node(2)]);allocate(r,choose(r,filters={}))
    row=r.client.get('/crm/retail/api/scan?code=P2').json()['rows'][0]
    with r.sessions() as db:
        v=db.get(RetailVariant,row['id']);v.missing=True;v.product_status='ARCHIVED';v.stale=True;db.commit()
    row=r.client.get('/crm/retail/api/scan?code=P2').json()['rows'][0]
    assert row['missing'] and row['status']=='ARCHIVED' and 'STALE' in row['issues']
    def no_network(*a):raise AssertionError('Inactive stock must not query Shopify')
    monkeypatch.setattr(inventory.client,'lookup',no_network)
    base=f"scanner/products/{row['id']}"
    assert r.client.get('/crm/retail/api/'+base+'/inventory',params={'version':row['version']}).json()['available'] is None
    assert r.post(base+'/labels',{'version':row['version']}).status_code==409


def test_read_budget_page_cap_and_version_cache(monkeypatch):
    c,calls,clock=reader(monkeypatch,[page(1,True,'a'),page(1,True,'b')]);monkeypatch.setattr(inventory,'MAX_PAGES',1)
    assert c.lookup(SNAPSHOT)['reason']=='READ_BUDGET_EXCEEDED'
    monkeypatch.setattr(inventory,'MAX_PAGES',20)
    c,calls,clock=reader(monkeypatch,[page(3),page(9)])
    assert c.lookup(SNAPSHOT)['available']==3
    assert c.lookup({**SNAPSHOT,'version':2})['available']==9
    c,calls,clock=reader(monkeypatch,[page()]);original=c._read
    def slow(query,variables):
        result=original(query,variables)
        if query==inventory.READ:clock[0]+=inventory.MAX_SECONDS+1
        return result
    monkeypatch.setattr(c,'_read',slow)
    assert c.lookup(SNAPSHOT)['reason']=='READ_BUDGET_EXCEEDED'


def test_scanner_requires_authenticated_session(retail):
    r=retail;r.client.get('/logout')
    assert r.client.get('/crm/retail/api/scan?code=P1',follow_redirects=False).status_code in {302,303,401}
    assert r.client.get('/crm/retail/scanner',follow_redirects=False).status_code in {302,303,401}


from test_retail_postgres import pg


def test_scanner_postgres_exact_and_search(pg):
    from test_retail_phase1 import Reader
    import retail_sync as sync
    import retail_service as service
    _,sessions,uid=pg
    with sessions() as db:
        job=sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';job_id=job.id;db.commit()
    sync.run_sync(sessions,job_id,Reader([([node(1,barcode='000123',sku='SKU-ONLY'),node(2,barcode='SAME'),node(3,barcode='SAME')],False)]))
    with sessions() as db:
        assert service.scan_page(db,'000123')['total']==1
        assert service.scan_page(db,'123')['total']==0 and service.scan_page(db,'SKU-ONLY')['total']==0
        assert service.scan_page(db,'SAME')['total']==2
        assert service.scanner_search(db,'sku','SKU-ONLY')['total']==1
        assert service.scanner_search(db,'name','%')['total']==0
