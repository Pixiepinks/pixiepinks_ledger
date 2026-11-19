"""Proposed inventory mutation contract fixtures are not evidence of live compatibility."""
import copy
import json
import httpx
import pytest
from settings import settings
from retail_inventory import InventoryError
import inventory_shopify as adapter

LINE={'variant_id':'gid://shopify/ProductVariant/1','product_id':'gid://shopify/Product/1',
      'inventory_item_id':'gid://shopify/InventoryItem/1','location_id':'gid://shopify/Location/1'}


def observation(available=8,on_hand=10):
    return {'inventoryItem':{'id':LINE['inventory_item_id'],'tracked':True,
        'variant':{'id':LINE['variant_id'],'product':{'id':LINE['product_id'],'status':'ACTIVE'}},
        'inventoryLevel':{'id':'gid://shopify/InventoryLevel/1','location':{'id':LINE['location_id'],'name':'Shop','isActive':True},
            'quantities':[{'name':'available','quantity':available},{'name':'on_hand','quantity':on_hand}]}}}


def client(monkeypatch,responses):
    for key,value in [('SHOPIFY_SHOP','fixture'),('SHOPIFY_CLIENT_ID','mock'),('SHOPIFY_CLIENT_SECRET','mock')]:monkeypatch.setattr(settings,key,value)
    calls=[];iterator=iter(responses)
    def transport(request):
        assert request.url.path=='/admin/api/2026-10/graphql.json'
        data=json.loads(request.content);calls.append(data);response=next(iterator)
        if isinstance(response,Exception):raise response
        if isinstance(response,httpx.Response):return response
        response={'shop':{'myshopifyDomain':'fixture.myshopify.com'},**response}
        return httpx.Response(200,json={'data':response},headers={'X-Shopify-API-Version':'2026-10'})
    c=adapter.InventoryShopify(http_client=httpx.Client(transport=httpx.MockTransport(transport)));c._token='mock';c._expires_at=10**20
    return c,calls


def test_both_quantities_are_distinct_and_zero_is_verified(monkeypatch):
    c,calls=client(monkeypatch,[observation(0,5)])
    data=c.observe(LINE);assert data['available']==0 and data['on_hand']==5
    assert calls[0]['variables']=={'id':LINE['inventory_item_id'],'location':LINE['location_id']}
    assert 'mutation' not in calls[0]['query']


@pytest.mark.parametrize('change',['item','variant','product','tracked','location','inactive','null_level','quantity','duplicate'])
def test_bad_state_never_becomes_quantity(monkeypatch,change):
    data=observation();item=data['inventoryItem']
    if change=='item':item['id']='other'
    if change=='variant':item['variant']['id']='other'
    if change=='product':item['variant']['product']['id']='other'
    if change=='tracked':item['tracked']=False
    if change=='location':item['inventoryLevel']['location']['id']='other'
    if change=='inactive':item['inventoryLevel']['location']['isActive']=False
    if change=='null_level':item['inventoryLevel']=None
    if change=='quantity':item['inventoryLevel']['quantities'][0]['quantity']=None
    if change=='duplicate':item['inventoryLevel']['quantities'][1]['name']='available'
    c,_=client(monkeypatch,[data])
    with pytest.raises(InventoryError):c.observe(LINE)


def test_complete_location_pages_and_no_partial_results(monkeypatch):
    def page(i,more,cursor):return {'locations':{'nodes':[{'id':f'gid://shopify/Location/{i}','name':str(i),'isActive':True}],'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}
    c,calls=client(monkeypatch,[page(1,True,'a'),page(2,False,'b')]);assert len(c.locations())==2 and calls[1]['variables']['after']=='a'
    c,_=client(monkeypatch,[page(1,True,'a'),page(2,True,'a')])
    with pytest.raises(InventoryError):c.locations()


@pytest.mark.parametrize('response',[httpx.Response(429),httpx.Response(403),httpx.Response(200,json={'errors':[{'message':'secret'}]}),httpx.Response(200,json={'data':observation()},headers={'X-Shopify-API-Version':'2027-01'}),httpx.ReadTimeout('secret')])
def test_read_errors_are_sanitized(monkeypatch,response):
    c,_=client(monkeypatch,[response])
    with pytest.raises(InventoryError) as exc:c.observe(LINE)
    assert 'secret' not in str(exc.value)


def test_mocked_cas_only_mutation_and_idempotency_key(monkeypatch):
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    c,calls=client(monkeypatch,[{'inventorySetQuantities':{'inventoryAdjustmentGroup':{'id':'gid://shopify/InventoryAdjustmentGroup/1'},'userErrors':[]}}])
    result=c.set_quantities([{**LINE,'before':10,'after':12}],'urn:fixture:receipt','durable-key')
    assert result.endswith('/1')
    assert calls==[{'query':adapter.SET,'variables':{'input':{'name':'on_hand','reason':'correction','referenceDocumentUri':'urn:fixture:receipt',
        'quantities':[{'inventoryItemId':LINE['inventory_item_id'],'locationId':LINE['location_id'],'quantity':12,'compareQuantity':10}]},'key':'durable-key'}}]
    assert not any(field in calls[0]['query'] for field in ['sku','price','barcode','productVariantsBulkUpdate'])


def test_ambiguous_mutation_response_not_retried(monkeypatch):
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    c,calls=client(monkeypatch,[httpx.ReadTimeout('token')])
    with pytest.raises(InventoryError):c.set_quantities([{**LINE,'before':10,'after':12}],'urn:fixture','same-key')
    assert len(calls)==1


def test_readonly_diagnostic_cannot_mutate(monkeypatch):
    from scripts.validate_inventory_readonly import ReadOnlyClient, run
    c=ReadOnlyClient()
    with pytest.raises(InventoryError,match='DIAGNOSTIC_MUTATION_FORBIDDEN'):c.set_quantities([],None,None)
    with pytest.raises(InventoryError,match='DIAGNOSTIC_MUTATION_FORBIDDEN'):c.request(adapter.SET,{},True)
    class Reader:
        def capabilities(self):return {'read_inventory':True}
        def locations(self):return []
    result=run(Reader());assert result['mode']=='READ_ONLY' and result['write_authorized'] is False and result['status']=='READ_ONLY_COMPLETE'


def test_missing_mutation_schema_remains_blocked(monkeypatch):
    c,calls=client(monkeypatch,[{'currentAppInstallation':{'accessScopes':[{'handle':'read_inventory'},{'handle':'write_inventory'}]}}])
    value=c.capabilities();assert value['mutation_shape_verified'] is False and value['source_contract_verified'] is False


def test_proposed_schema_shape_and_scopes_do_not_certify_semantics(monkeypatch):
    def typ(name):
        return {'kind':'LIST','ofType':typ(name[1:-1])} if name.startswith('[') else {'kind':'SCALAR','name':name}
    def inputs(values):return {'inputFields':[{'name':n,'type':typ(t)} for n,t in values.items()]}
    data={'currentAppInstallation':{'accessScopes':[{'handle':v} for v in ['read_inventory','write_inventory','read_locations']]},
        'set':inputs({'name':'String','reason':'String','referenceDocumentUri':'String','quantities':'[InventoryQuantityInput]'}),
        'quantity':inputs({'inventoryItemId':'ID','locationId':'ID','quantity':'Int','compareQuantity':'Int'}),
        'mutation':{'fields':[{'name':'inventorySetQuantities','args':[{'name':'input','type':typ('InventorySetQuantitiesInput')}]}]},
        '__schema':{'directives':[{'name':'idempotent','args':[{'name':'key','type':typ('String')}]}]}}
    c,_=client(monkeypatch,[data]);cap=c.capabilities()
    assert cap['mutation_shape_verified'] is True and cap['write_inventory'] is True
    assert cap['source_contract_verified'] is False and cap['native_transfers_verified'] is False
