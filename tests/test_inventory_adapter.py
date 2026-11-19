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


def group(reference='urn:pixiepinks:inventory:fixture:doc',reason='correction',delta=2,name='on_hand'):
    return {'id':'gid://shopify/InventoryAdjustmentGroup/1','createdAt':'2026-11-19T10:00:00Z','reason':reason,'referenceDocumentUri':reference,
        'changes':[{'name':name,'delta':delta,'quantityAfterChange':12,'ledgerDocumentUri':None,'item':{'id':LINE['inventory_item_id']},'location':{'id':LINE['location_id']}}]}


@pytest.mark.parametrize('kind,reason,operation,code',[('COUNT',None,'inventorySetQuantities','correction'),('OPENING',None,'inventorySetQuantities','correction'),('RECEIPT',None,'inventoryAdjustQuantities','received'),('ADJUSTMENT','RETURN','inventoryAdjustQuantities','restock')])
def test_minimal_operation_and_field_idempotency(monkeypatch,kind,reason,operation,code):
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    reference='urn:pixiepinks:inventory:fixture:doc';key='durable-key-123456'
    c,calls=client(monkeypatch,[{operation:{'inventoryAdjustmentGroup':group(reason=code,name='on_hand' if kind in {'COUNT','OPENING'} else 'available'),'userErrors':[]}}])
    result=c.apply([{**LINE,'before':10,'after':12,'available_before':8}],reference,key,kind,reason)
    assert result['group_id']=='gid://shopify/InventoryAdjustmentGroup/1' and result['group']['id']==result['group_id']
    expected={'inventoryItemId':LINE['inventory_item_id'],'locationId':LINE['location_id']}
    absolute=kind in {'COUNT','OPENING'}
    expected.update({'quantity':12,'changeFromQuantity':10} if absolute else {'delta':2,'changeFromQuantity':8,'ledgerDocumentUri':'gid://pixiepinks/InventoryMovement/'+key+'-0'})
    assert calls[0]['variables']=={'input':{'name':'on_hand' if absolute else 'available','reason':code,'referenceDocumentUri':reference,'quantities' if absolute else 'changes':[expected]},'key':key}
    assert 'compareQuantity' not in calls[0]['query'] and ') @idempotent(key:$key)' in calls[0]['query']
    assert not any(field in calls[0]['query'] for field in ['sku','price','barcode','productVariantsBulkUpdate'])


def test_ambiguous_mutation_response_not_retried(monkeypatch):
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    c,calls=client(monkeypatch,[httpx.ReadTimeout('token')])
    with pytest.raises(InventoryError):c.apply([{**LINE,'before':10,'after':12,'available_before':8}],'urn:pixiepinks:inventory:fixture:doc','durable-key-123456','RECEIPT')
    assert len(calls)==1


def test_readonly_diagnostic_cannot_mutate(monkeypatch):
    from scripts.validate_inventory_readonly import ReadOnlyClient, run
    c=ReadOnlyClient()
    with pytest.raises(InventoryError,match='DIAGNOSTIC_MUTATION_FORBIDDEN'):c.apply([],None,None,None)
    for query in [adapter.SET,adapter.ADJUST]:
        with pytest.raises(InventoryError,match='DIAGNOSTIC_MUTATION_FORBIDDEN'):c.request(query,{},True)
    class Reader:
        def capabilities(self):return {'read_inventory':True}
        def locations(self):return []
    result=run(Reader());assert result['mode']=='READ_ONLY' and result['write_authorized'] is False and result['status']=='READ_ONLY_COMPLETE'


def test_missing_mutation_schema_remains_blocked(monkeypatch):
    c,calls=client(monkeypatch,[{'currentAppInstallation':{'accessScopes':[{'handle':'read_inventory'},{'handle':'write_inventory'}]}}])
    value=c.capabilities();assert value['mutation_shape_verified'] is False and value['source_contract_verified'] is adapter.MUTATION_CONTRACT_VERIFIED


def test_schema_shape_and_scopes_do_not_certify_semantics(monkeypatch):
    from inventory_contract_fixture import schema
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',False)
    c,_=client(monkeypatch,[schema()]);cap=c.capabilities()
    assert cap['mutation_shape_verified'] is True and cap['write_inventory'] is True
    assert cap['source_contract_verified'] is False and cap['native_transfers_verified'] is False


@pytest.mark.parametrize('breakage',['legacy_compare','uri_scalar','idempotent_position','idempotent_key','unknown_required','missing_output','missing_adjust','wrong_payload'])
def test_contract_probe_rejects_incompatible_schema(monkeypatch,breakage):
    from inventory_contract_fixture import schema,typ
    data=schema()
    if breakage=='legacy_compare':data['quantity']['inputFields'][-1]['name']='compareQuantity'
    if breakage=='uri_scalar':data['set']['inputFields'][2]['type']=typ('URI')
    if breakage=='idempotent_position':data['__schema']['directives'][0]['locations']=['MUTATION']
    if breakage=='idempotent_key':data['__schema']['directives'][0]['args'][0]['type']=typ('ID!')
    if breakage=='unknown_required':data['change']['inputFields'].append({'name':'unknown','type':typ('Int!'),'defaultValue':None})
    if breakage=='missing_output':data['group']['fields']=[]
    if breakage=='missing_adjust':data['mutation']['fields'].pop()
    if breakage=='wrong_payload':data['setPayload']['fields'][0]['type']=typ('Product')
    c,_=client(monkeypatch,[data]);cap=c.capabilities()
    assert cap['mutation_shape_verified'] is False and cap['production_write_ready'] is False
    assert 'mutation_shape_verified' in cap['release_blockers']


@pytest.mark.parametrize('bad',['wrong_reference','wrong_reason','no_date','no_changes','wrong_delta','invented_gid','partial_change','user_error'])
def test_group_correlation_failures_are_uncertain_and_not_retried(monkeypatch,bad):
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    value=group(reason='received',name='available')
    if bad=='wrong_reference':value['referenceDocumentUri']='other'
    if bad=='wrong_reason':value['reason']='other'
    if bad=='no_date':value.pop('createdAt')
    if bad=='no_changes':value['changes']=[]
    if bad=='wrong_delta':value['changes'][0]['delta']=99
    if bad=='invented_gid':value={'id':'gid://shopify/InventoryAdjustmentGroup/1'}
    if bad=='partial_change':value['changes'][0].pop('quantityAfterChange')
    data={'inventoryAdjustmentGroup':value,'userErrors':[{'message':'private'}] if bad=='user_error' else []}
    c,calls=client(monkeypatch,[{'inventoryAdjustQuantities':data}])
    with pytest.raises(InventoryError):c.apply([{**LINE,'before':10,'after':12,'available_before':8}],'urn:pixiepinks:inventory:fixture:doc','durable-key-123456','RECEIPT')
    assert len(calls)==1


def test_diagnostic_scope_preservation_and_not_authorization():
    from scripts.validate_inventory_readonly import run
    class Reader:
        def capabilities(self):return {'installed_scope_handles':['read_inventory','read_locations','write_inventory'],'production_write_ready':True}
        def locations(self):return []
    value=run(Reader(),previous_scopes=['read_inventory','write_products'])
    assert value['write_authorized'] is False and value['production_write_ready'] is False
    assert value['preserved_scope_check']['missing_previous_scopes']==['write_products']


def test_reviewed_contract_still_requires_live_inventory_scope(monkeypatch):
    from inventory_contract_fixture import schema
    data=schema();data['currentAppInstallation']['accessScopes']=[{'handle':'read_inventory'},{'handle':'read_locations'}]
    c,_=client(monkeypatch,[data]);cap=c.capabilities()
    assert cap['source_contract_verified'] is True and cap['mutation_shape_verified'] is True
    assert cap['write_inventory'] is False and cap['production_write_ready'] is False
    assert cap['release_blockers']==['write_inventory']


def test_delta_cas_and_ledger_are_never_omitted():
    wire=adapter.InventoryShopify.prepare([{**LINE,'before':10,'after':12,'available_before':8}],
        'urn:pixiepinks:inventory:fixture:doc','durable-key-123456','RECEIPT')
    assert wire['variables']['input']['name']=='available'
    row=wire['variables']['input']['changes'][0]
    assert row['changeFromQuantity']==8 and row['delta']==2 and row['ledgerDocumentUri'].startswith('gid://pixiepinks/')
    with pytest.raises(InventoryError):adapter.InventoryShopify.prepare([{**LINE,'before':10,'after':12}],
        'urn:pixiepinks:inventory:fixture:doc','durable-key-123456','RECEIPT')


def test_actual_group_identity_and_item_mismatch_fail_closed(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    value=group(reason='received',name='available');value['changes'][0]['item']['id']='gid://shopify/InventoryItem/2'
    c,calls=client(monkeypatch,[{'inventoryAdjustQuantities':{'inventoryAdjustmentGroup':value,'userErrors':[]}}])
    with pytest.raises(InventoryError,match='INVENTORY_GROUP_ITEM_UNVERIFIED'):c.apply([{**LINE,'before':10,'after':12,'available_before':8}],
        'urn:pixiepinks:inventory:fixture:doc','durable-key-123456','RECEIPT')
    assert len(calls)==1
