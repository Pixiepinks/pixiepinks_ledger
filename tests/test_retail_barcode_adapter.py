"""Synthetic introspection/transport fixtures; NOT verified Shopify contracts.

These tests prove read-only version/identity guards and retained write blocking.
They do not stand in for official documentation or development-store validation.
"""
import json
import httpx
import pytest
from settings import settings
from retail_barcode_shopify import BarcodeClient,BARCODE_API_VERSION,PROBE,READ,RemoteError


def field(name,kind='SCALAR',type_name='String',args=None):
    return {'name':name,'type':{'kind':kind,'name':type_name},'args':args or []}


def evidence():
    return {'shop':{'myshopifyDomain':'adapter-fixture.myshopify.com'},
        'currentAppInstallation':{'accessScopes':[{'handle':'write_products'}]},
        'variant':{'name':'ProductVariant','kind':'OBJECT','fields':[field('id'),field('barcode'),field('barcodes','OBJECT','UnverifiedBarcodeCollection')]},
        'input':{'name':'ProductVariantsBulkInput','kind':'INPUT_OBJECT','inputFields':[field('id'),field('barcodes','LIST',None)]},
        'barcodeInput':{'name':'BarcodeInput','kind':'INPUT_OBJECT','inputFields':[field('unverifiedFixtureField')]},
        'mutationSchema':{'mutationType':{'name':'Mutation','fields':[field('productVariantsBulkUpdate','OBJECT','UnverifiedPayload',args=[field('allowPartialUpdates','SCALAR','Boolean')])]}}}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','adapter-fixture')
    monkeypatch.setattr(settings,'SHOPIFY_CLIENT_ID','fixture-id')
    monkeypatch.setattr(settings,'SHOPIFY_CLIENT_SECRET','fixture-secret')
    monkeypatch.setattr(settings,'SHOPIFY_API_VERSION','2026-07')
    calls=[];state={'data':evidence(),'version':'2026-10','status':200}
    def handle(request):
        body=json.loads(request.content);calls.append((str(request.url),body))
        assert body['query'] in {PROBE,READ} and not body['query'].lstrip().startswith('mutation')
        headers={'X-Shopify-API-Version':state['version']} if state['version'] else {}
        return httpx.Response(state['status'],json={'data':state['data'],**({'errors':state['errors']} if 'errors' in state else {})},headers=headers)
    http=httpx.Client(transport=httpx.MockTransport(handle));client=BarcodeClient(http_client=http)
    client._token='fixture-token';client._expires_at=10**12
    yield client,calls,state
    http.close()


def test_versioned_probe_does_not_change_global_version_or_enable_writes(adapter):
    client,calls,state=adapter;result=client.capabilities()
    assert result['requested_version']==BARCODE_API_VERSION=='2026-10' and result['version']=='2026-10'
    assert result['global_version']==settings.SHOPIFY_API_VERSION=='2026-07'
    assert calls[0][0].endswith('/admin/api/2026-10/graphql.json')
    assert result['scope'] and result['shop_identity_verified']
    assert result['multiple_barcode_field'] and result['multiple_barcode_input']
    assert result['barcode_input_observed'] and result['bulk_update_observed'] and result['allow_partial_updates_observed']
    assert len(result['schema_fingerprint'])==64
    assert not result['supported'] and not result['contract_verified']
    assert result['reason']=='MULTI_BARCODE_CONTRACT_UNVERIFIED'


@pytest.mark.parametrize('version,reason',[('', 'API_VERSION_UNVERIFIED'),('2026-07','API_VERSION_FALLBACK_REJECTED'),('2027-01','API_VERSION_FALLBACK_REJECTED')])
def test_missing_or_different_served_version_fails_closed(adapter,version,reason):
    client,calls,state=adapter;state['version']=version;result=client.capabilities()
    assert not result['supported'] and not result['scope'] and result['reason']==reason
    with pytest.raises(RemoteError,match=reason):client._read(READ,{'id':'gid://shopify/ProductVariant/1'})


@pytest.mark.parametrize('domain',[None,'other-shop.myshopify.com',{'malformed':True}])
def test_wrong_or_unknown_shop_never_confirms_scope(adapter,domain):
    client,_,state=adapter;state['data']['shop']['myshopifyDomain']=domain
    result=client.capabilities();assert not result['scope'] and result['reason']=='SHOP_IDENTITY_UNVERIFIED'


def test_absent_scope_and_missing_types_report_distinct_blockers(adapter):
    client,_,state=adapter;state['data']['currentAppInstallation']['accessScopes']=[]
    assert client.capabilities()['reason']=='WRITE_PRODUCTS_SCOPE_UNVERIFIED'
    state['data']=evidence();state['data']['variant']['fields']=[field('barcode')]
    assert client.capabilities()['reason']=='COMPLETE_BARCODE_STATE_UNAVAILABLE'
    state['data']=evidence();state['data']['input']['inputFields']=[field('barcode')]
    assert client.capabilities()['reason']=='BULK_BARCODE_SCHEMA_UNAVAILABLE'
    state['data']=evidence();state['data']['barcodeInput']=None
    assert client.capabilities()['reason']=='BARCODE_INPUT_SCHEMA_UNAVAILABLE'


@pytest.mark.parametrize('status,reason',[(401,'AUTHENTICATION_UNVERIFIED'),(429,'THROTTLED'),(500,'REMOTE_UNAVAILABLE')])
def test_http_failures_are_sanitized(adapter,status,reason):
    client,_,state=adapter;state['status']=status;state['errors']=[{'message':'DO_NOT_EXPOSE_SECRET'}]
    result=client.capabilities();assert result['reason']==reason and 'DO_NOT_EXPOSE' not in str(result)


def test_graphql_errors_and_malformed_schema_are_not_evidence(adapter):
    client,_,state=adapter;state['errors']=[{'message':'DO_NOT_EXPOSE_TOKEN'}]
    assert client.capabilities()['reason']=='SCHEMA_OR_ACCESS_UNVERIFIED'
    state.pop('errors');state['data']=[]
    result=client.capabilities();assert result['reason']=='SCHEMA_OR_ACCESS_UNVERIFIED' and not result['supported']


def test_legacy_empty_barcode_never_claims_complete_inspection_or_collision_check(adapter):
    client,calls,state=adapter;state['data']={'productVariant':{'id':'gid://shopify/ProductVariant/1','barcode':None,'product':{'id':'gid://shopify/Product/1','status':'ACTIVE'}}}
    with pytest.raises(RemoteError,match='COMPLETE_BARCODE_SCHEMA_UNVERIFIED'):
        client.inspect({'variant_id':'gid://shopify/ProductVariant/1','product_id':'gid://shopify/Product/1','code':'P1'})
    assert calls[0][0].endswith('/admin/api/2026-10/graphql.json')


def test_flags_and_introspection_never_allow_network_mutation(adapter,monkeypatch):
    client,calls,_=adapter;monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',True);monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
    assert not client.capabilities()['supported'];count=len(calls)
    with pytest.raises(RemoteError,match='MUTATION_SEMANTICS_UNVERIFIED'):
        client.write_empty({'variant_id':'gid://shopify/ProductVariant/1','code':'P1'})
    assert len(calls)==count
    with pytest.raises(RemoteError,match='UNSUPPORTED_DOCUMENT'):client._read('mutation { forbidden }',{})
    assert len(calls)==count


def test_unconfigured_adapter_makes_no_http_requests(adapter,monkeypatch):
    client,calls,_=adapter;monkeypatch.setattr(settings,'SHOPIFY_CLIENT_SECRET',None)
    assert client.capabilities()['reason']=='SHOPIFY_NOT_CONFIGURED' and not calls


def test_schema_fingerprint_detects_shape_change_without_exposing_raw_schema(adapter):
    client,_,state=adapter;first=client.capabilities();state['data']['barcodeInput']['inputFields'].append(field('anotherFixtureField'))
    second=client.capabilities();assert first['schema_fingerprint']!=second['schema_fingerprint']
    assert 'unverifiedFixtureField' not in str(second) and not second['supported']


def test_read_and_write_scope_observations_are_explicit_and_do_not_unlock_writes(adapter):
    client,_,state=adapter;result=client.capabilities()
    assert result['write_products_scope_observed'] and not result['read_products_scope_observed']
    state['data']['currentAppInstallation']['accessScopes'].append({'handle':'read_products'})
    result=client.capabilities()
    assert result['read_products_scope_observed'] and result['write_products_scope_observed']
    assert not result['supported'] and not result['contract_verified']
