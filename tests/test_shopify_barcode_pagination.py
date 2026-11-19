"""Transport fixtures based on the supplied live 2026-10 schema.

Mutation semantics are NOT established by the schema. The release gate is only
monkeypatched in tests using httpx.MockTransport; no live mutation is permitted.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from settings import settings
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate
import retail_barcode_contract as contract
import retail_barcode_shopify as adapter
from retail_barcode_shopify import BarcodeClient,PROBE,READ,CATALOG,CATALOG_IDS,MUTATION,RemoteError,mutation_payload

SCHEMA=json.loads((Path(__file__).parent/'fixtures/shopify_barcode_2026_10_schema.json').read_text())
SNAP={'id':1,'version':1,'shop':'eb2b70','product_id':'gid://shopify/Product/10','variant_id':'gid://shopify/ProductVariant/1','code':'P1','ledger_id':1}


def probe():
    s=copy.deepcopy(SCHEMA['schema_metadata']);r=copy.deepcopy(SCHEMA['related_types'])
    return {'shop':{'myshopifyDomain':'eb2b70.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'read_products'},{'handle':'write_products'}]},
        'variant':{'fields':[s['variant_barcodes'],s['variant_legacy_barcode'],s['variant_id']]},
        'input':s['bulk_input'],'barcodeInput':s['barcode_input'],
        'mutationSchema':{'mutationType':{'fields':[s['bulk_update']]}},
        'barcodeConnection':r['ProductVariantBarcodeConnection'],'barcodeValue':r['ProductVariantBarcode'],
        'barcodePageInfo':r['PageInfo'],'barcodeType':r['BarcodeType']}


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','eb2b70');monkeypatch.setattr(settings,'SHOPIFY_CLIENT_ID','fixture');monkeypatch.setattr(settings,'SHOPIFY_CLIENT_SECRET','fixture')
    monkeypatch.setattr(adapter.time,'sleep',lambda _:None)
    state=SimpleNamespace(probe=probe(),calls=[],catalog=[['1','2'],['3']],barcodes={'1':[], '2':[{'value':'SUPPLIER','type':'EAN'}],'3':[{'value':'SECONDARY','type':None}]},
        mutations=[],mode=None,bad_cursor=False,change_ids=False,legacy=None,version='2026-10',catalog_rounds=0)
    def handle(req):
        body=json.loads(req.content);q=body['query'];v=body['variables'];state.calls.append(body)
        assert str(req.url)=='https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json'
        if q==PROBE:data=state.probe
        elif q in {CATALOG,CATALOG_IDS}:
            page=int(v['after'] or 0)
            if page==0:state.catalog_rounds+=1
            ids=list(state.catalog[page])
            if state.change_ids and state.catalog_rounds>1 and page==0:ids=ids+['99']
            rows=[]
            for i in ids:
                row={'id':'gid://shopify/ProductVariant/'+i}
                if q==CATALOG:
                    vals=state.barcodes.get(i,[])
                    row.update(barcode=state.legacy if i=='1' else None,product={'id':'gid://shopify/Product/10','status':'ACTIVE'},
                        barcodes={'nodes':vals[:1],'pageInfo':{'hasNextPage':len(vals)>1,'endCursor':'1'}})
                rows.append(row)
            data={'productVariants':{'nodes':rows,
                'pageInfo':{'hasNextPage':page+1<len(state.catalog),'endCursor':str(page+1)}}}
        elif q==READ:
            vid=v['id'].rsplit('/',1)[-1];page=int(v['after'] or 0);values=state.barcodes.get(vid,[])
            chunk=values[page:page+100]
            data={'productVariant':{'id':v['id'],'barcode':state.legacy if vid=='1' else None,'product':{'id':'gid://shopify/Product/10','status':'ACTIVE'},
                'barcodes':{'nodes':chunk,'pageInfo':{'hasNextPage':page+len(chunk)<len(values),'endCursor':'1' if state.bad_cursor else str(page+len(chunk))}}}}
        elif q==MUTATION:
            state.mutations.append(body)
            if state.mode=='timeout':
                state.barcodes['1']=[{'value':'P1','type':None}];raise httpx.ReadTimeout('private payload')
            if state.mode=='user_error':data={'productVariantsBulkUpdate':{'product':None,'productVariants':None,'userErrors':[{'code':'INVALID_INPUT','field':['variants']} ]}}
            else:
                state.barcodes['1']=[{'value':'P1','type':'UPC' if state.mode=='typed' else None}]
                data={'productVariantsBulkUpdate':{'product':{'id':SNAP['product_id']},'productVariants':[{'id':SNAP['variant_id']}],'userErrors':[]}}
        else:raise AssertionError('Unapproved document')
        return httpx.Response(200,json={'data':data},headers={'X-Shopify-API-Version':state.version})
    http=httpx.Client(transport=httpx.MockTransport(handle));client=BarcodeClient(http_client=http);client._token='fake';client._expires_at=10**12
    yield client,state
    http.close()


def test_actual_supplied_schema_is_readable_and_write_semantics_remain_unverified(remote):
    client,state=remote;assert contract.shape_verified(probe())
    cap=client.capabilities();assert cap['read_supported'] and not cap['supported'] and not cap['contract_verified']
    assert cap['reason']=='MUTATION_SEMANTICS_UNVERIFIED'
    assert {x['name'] for x in SCHEMA['related_types']['BarcodeType']['enumValues']}==contract.TYPES
    assert 'CUSTOM' not in contract.TYPES
    with pytest.raises(RemoteError,match='MUTATION_SEMANTICS_UNVERIFIED'):client.write_empty(SNAP)
    assert not state.mutations


def test_unknown_added_required_field_or_changed_null_type_blocks_contract():
    value=probe();value['barcodeInput']['inputFields'][1]['type']={'kind':'NON_NULL','ofType':{'kind':'ENUM','name':'BarcodeType'}}
    assert not contract.shape_verified(value)
    value=probe();value['input']['inputFields'].append({'name':'newRequired','type':{'kind':'NON_NULL','ofType':{'kind':'SCALAR','name':'String'}},'defaultValue':None})
    assert not contract.shape_verified(value)


def test_full_catalog_and_every_barcode_page_including_secondary_collision(remote):
    client,state=remote
    state.barcodes['2']=[{'value':str(i),'type':None} for i in range(100)]+[{'value':'P1','type':None}]
    result=client.inspect(SNAP)
    assert result['complete'] and result['collision_complete'] and result['codes']==[]
    assert result['collision_ids']==['gid://shopify/ProductVariant/2']
    reads=[c['variables'] for c in state.calls if c['query']==READ]
    assert {'id':'gid://shopify/ProductVariant/2','after':'1'} in reads
    assert len([c for c in state.calls if c['query'] in {CATALOG,CATALOG_IDS}])==4


def test_target_many_pages_are_complete_and_never_truncated(remote):
    client,state=remote;state.barcodes['1']=[{'value':str(i),'type':None} for i in range(201)]
    result=client.inspect(SNAP);assert result['complete'] and len(result['codes'])==201


@pytest.mark.parametrize('change,reason', [('cursor','INVALID_PAGINATION_CURSOR'),('catalog','CATALOG_CHANGED_DURING_SCAN'),('duplicate','DUPLICATE_REMOTE_BARCODE'),('legacy','LEGACY_COLLECTION_CONTRADICTION')])
def test_incomplete_or_ambiguous_states_never_claim_coverage(remote,change,reason):
    client,state=remote
    if change=='cursor':state.bad_cursor=True;state.barcodes['2']=[{'value':str(i),'type':None} for i in range(201)]
    elif change=='catalog':state.change_ids=True
    elif change=='duplicate':state.barcodes['2']=[{'value':'SAME','type':None}]*2
    elif change=='legacy':state.legacy='UNREPORTED_SECONDARY'
    with pytest.raises(RemoteError,match=reason):client.inspect(SNAP)
    assert not state.mutations


def test_minimal_payload_omits_regulated_type_and_all_other_product_fields():
    assert mutation_payload(SNAP)=={'productId':SNAP['product_id'],'variants':[{'id':SNAP['variant_id'],'barcodes':[{'value':'P1'}]}]}
    with pytest.raises(RemoteError):mutation_payload({**SNAP,'code':' arbitrary UPC '})


def enable_mock_semantics(monkeypatch):
    # Test-only semantic approval; production source remains False, with no env override.
    monkeypatch.setattr(contract,'MUTATION_SEMANTICS_VERIFIED',True)
    for key in ['RETAIL_ENABLED','SHOPIFY_PRODUCT_WRITE_ENABLED','RETAIL_BARCODE_SYNC_ENABLED']:monkeypatch.setattr(settings,key,True)


def test_mock_only_mutation_is_exact_and_acceptance_is_not_verification(remote,monkeypatch):
    client,state=remote;enable_mock_semantics(monkeypatch)
    assert client.write_empty(SNAP)=={'accepted':True}
    assert len(state.mutations)==1 and state.mutations[0]['variables']==mutation_payload(SNAP)
    result=client.inspect(SNAP);assert result['codes']==['P1'] and result['untyped_local_code']


@pytest.mark.parametrize('mode', ['timeout','user_error'])
def test_mutation_uncertainty_is_never_retried_and_requires_fresh_readback(remote,monkeypatch,mode):
    client,state=remote;enable_mock_semantics(monkeypatch);state.mode=mode
    with pytest.raises(RemoteError):client.write_empty(SNAP)
    assert len(state.mutations)==1
    result=client.inspect(SNAP)
    assert result['complete'] and result['codes']==(['P1'] if mode=='timeout' else [])


def test_existing_manufacturer_or_remote_collision_blocks_mock_mutation(remote,monkeypatch):
    client,state=remote;enable_mock_semantics(monkeypatch);state.barcodes['1']=[{'value':'MAKER','type':'EAN'}]
    with pytest.raises(RemoteError,match='REMOTE_NOT_EMPTY_OR_CODE_REUSED'):client.write_empty(SNAP)
    assert not state.mutations
    state.barcodes['1']=[];state.barcodes['3']=[{'value':'P1','type':None}]
    with pytest.raises(RemoteError,match='REMOTE_NOT_EMPTY_OR_CODE_REUSED'):client.write_empty(SNAP)
    assert not state.mutations


def test_typed_readback_cannot_be_verified(remote):
    import retail_barcode_sync as workflow
    client,state=remote;state.barcodes['1']=[{'value':'P1','type':'UPC'}]
    result=client.inspect(SNAP)
    assert workflow.remote_reason(result,SNAP,{'supported':True,'scope':True,'version':'2026-10'},readback=True)=='READBACK_TYPE_MISMATCH'


def test_unrelated_defaults_and_extra_barcode_fields_are_not_silently_accepted():
    value=probe();next(f for f in value['input']['inputFields'] if f['name']=='compareAtPrice')['defaultValue']='1.00'
    assert not contract.shape_verified(value)
    value=probe();value['barcodeInput']['inputFields'].append({'name':'extra','type':{'kind':'SCALAR','name':'Boolean'},'defaultValue':None})
    assert not contract.shape_verified(value)


@pytest.mark.parametrize('limit,reason',[('deadline','CATALOG_SCAN_DEADLINE_EXCEEDED'),('variants','CATALOG_VARIANT_BUDGET_EXCEEDED')])
def test_limits_fail_closed_never_return_partial_coverage(remote,monkeypatch,limit,reason):
    client,state=remote
    if limit=='deadline':monkeypatch.setattr(adapter,'MAX_SCAN_SECONDS',-1)
    else:monkeypatch.setattr(adapter,'MAX_CATALOG_VARIANTS',1)
    with pytest.raises(RemoteError,match=reason):client.inspect(SNAP)
    assert not state.mutations


@pytest.mark.parametrize('mode,status',[(None,'VERIFIED'),('timeout','VERIFIED'),('user_error','UNKNOWN'),('typed','CONFLICT')])
def test_worker_with_actual_schema_adapter_reconciles_once_preserves_local_fields(retail,remote,monkeypatch,mode,status):
    import retail_barcode_sync as workflow
    from retail_models import RetailBarcodeItem,RetailVariant
    client,state=remote;r=retail;enable_mock_semantics(monkeypatch)
    monkeypatch.setattr(workflow,'client',client)
    value=node(1,sku='ORIGINAL-SKU');value['product']['id']=SNAP['product_id']
    import_nodes(r,[value]);allocate(r,choose(r,ids=[1]))
    selection=choose(r,ids=[1]);p=r.post('barcode-sync/preview',{'selection':selection})
    assert p.status_code==200,p.text
    p=p.json();assert p['rows'][0]['status']=='ELIGIBLE',p
    response=r.post(f"barcode-sync/{p['id']}/approve",{'token':p['approval_token'],'confirmation':p['confirmation']})
    assert response.status_code==200,response.text
    state.mode=mode
    assert workflow.dispatch_one(r.sessions,client)
    assert len(state.mutations)==1 and not workflow.dispatch_one(r.sessions,client)
    with r.sessions() as db:
        item=db.query(RetailBarcodeItem).one();assert item.status==status,item.reason
        row=db.get(RetailVariant,1)
        assert row.local_barcode=='P1' and row.barcode is None and row.sku=='ORIGINAL-SKU' and str(row.price)=='8500.00'
        if status=='VERIFIED':assert item.verified_at is not None and item.after['untyped_local_code']
        else:assert item.verified_at is None


def test_long_retry_after_never_causes_unbounded_wait(remote):
    client,state=remote
    original=client.http
    def handle(req):return httpx.Response(429,headers={'Retry-After':'600'},json={})
    with httpx.Client(transport=httpx.MockTransport(handle)) as limited:
        client.http=limited
        with pytest.raises(RemoteError,match='RATE_LIMIT_RETRY_LATER'):client._read(CATALOG,{'after':None})
    client.http=original


def test_case_or_whitespace_alias_is_conservatively_a_collision(remote):
    client,state=remote;state.barcodes['3']=[{'value':' p1 ','type':None}]
    assert client.inspect(SNAP)['collision_ids']==['gid://shopify/ProductVariant/3']


def test_missing_type_is_not_treated_as_explicit_untyped_null(remote):
    client,state=remote;state.barcodes['2']=[{'value':'P1'}]
    with pytest.raises(RemoteError,match='INVALID_BARCODE_STATE'):client.inspect(SNAP)


def test_missing_terminal_page_metadata_never_claims_complete_catalog(remote,monkeypatch):
    client,state=remote;original=client._read
    def read(document,variables):
        data,version=original(document,variables)
        if document==CATALOG:data['productVariants']['pageInfo'].pop('endCursor')
        return data,version
    monkeypatch.setattr(client,'_read',read)
    with pytest.raises(RemoteError,match='INCOMPLETE_PAGE_INFO'):client.inspect(SNAP)
