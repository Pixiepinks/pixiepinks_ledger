"""Synthetic schema fixtures; not verified Shopify contract or product writes."""
import json
from types import SimpleNamespace
import httpx
import pytest
from scripts.export_shopify_barcode_contract import export_evidence,PROBE,RELATED,EvidenceReader,EvidenceError


def ref(name,kind='OBJECT'):return {'name':name,'kind':kind}
def field(name,type_name,kind='OBJECT'):return {'name':name,'type':ref(type_name,kind),'args':[]}
def config(**kw):return SimpleNamespace(**{'SHOPIFY_SHOP':'existing-fixture','SHOPIFY_CLIENT_ID':'SECRET_ID','SHOPIFY_CLIENT_SECRET':'SECRET_VALUE','SHOPIFY_API_VERSION':'2026-07',**kw})
def schema():
    return {'shop':{'myshopifyDomain':'existing-fixture.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'read_products'},{'handle':'write_products'}]},
        'variant':{'fields':[field('id','ID','SCALAR'),field('barcodes','SyntheticBarcodeCollection')]},
        'bulkInput':{'inputFields':[field('id','ID','SCALAR'),field('barcodes','BarcodeInput','INPUT_OBJECT')]},
        'barcodeInput':{'name':'BarcodeInput','kind':'INPUT_OBJECT','inputFields':[field('syntheticValue','String','SCALAR')]},
        'mutationType':{'mutationType':{'fields':[field('productVariantsBulkUpdate','SyntheticUpdatePayload')]}}}


class Client:
    base_url='https://existing-fixture.myshopify.com'
    def __init__(self,handler):self.calls=[];self.tokens=0;self.http=httpx.Client(transport=httpx.MockTransport(handler))
    def get_token(self):self.tokens+=1;return 'FAKE_TOKEN'


@pytest.fixture
def fixture():
    state={'data':schema(),'version':'2026-10','status':200};calls=[]
    def handle(req):
        value=json.loads(req.content);calls.append((str(req.url),value))
        assert value['query'] in {PROBE,RELATED} and value['query'].lstrip().startswith('query ')
        if value['query']==RELATED:
            name=value['variables']['name'];data={'type':state.get('types',{}).get(name,{'name':name,'kind':'OBJECT','fields':[field('value','String','SCALAR')]})}
        else:data=state['data']
        return httpx.Response(state['status'],headers={'X-Shopify-API-Version':state['version']},json={'data':data,**({'errors':state['errors']} if 'errors' in state else {})})
    client=Client(handle);yield client,state,calls;client.http.close()


def test_existing_client_token_and_store_reused_for_fixed_queries(fixture):
    client,state,calls=fixture;result=export_evidence(client,config())
    assert result['shop_identity_verified'] and result['served_api_version']=='2026-10'
    assert result['global_api_version']=='2026-07' and result['scope_handles_observed']==['read_products','write_products']
    assert client.tokens==len(calls)==3
    assert all(url=='https://existing-fixture.myshopify.com/admin/api/2026-10/graphql.json' for url,_ in calls)
    assert set(result['related_types'])=={'SyntheticBarcodeCollection','SyntheticUpdatePayload'}
    assert result['status']=='BLOCKED' and not result['contract_verified'] and result['pilot_variant'] is None
    assert not result['complete_barcode_state_verified'] and not result['catalog_collision_coverage_verified']
    assert 'SECRET' not in json.dumps(result) and 'FAKE_TOKEN' not in json.dumps(result)


@pytest.mark.parametrize('version,status,reason',[('2026-07',200,'SERVED_API_VERSION_UNVERIFIED'),('2026-10',429,'RATE_LIMITED_EVIDENCE_INCOMPLETE'),('2026-10',500,'READ_ONLY_REQUEST_FAILED')])
def test_partial_unavailable_or_fallback_never_certifies(fixture,version,status,reason):
    client,state,_=fixture;state.update(version=version,status=status);r=export_evidence(client,config())
    assert r['reason']==reason and not r['contract_verified']


def test_missing_connection_and_lab_do_not_make_requests(fixture):
    client,_,calls=fixture
    assert export_evidence(client,config(SHOPIFY_CLIENT_SECRET=None))['reason']=='RAILWAY_RUNTIME_CONNECTION_UNAVAILABLE'
    assert export_evidence(client,config(SHOPIFY_SHOP='pixiepinks-barcode-lab'))['reason']=='LAB_CONNECTION_NOT_PERMITTED'
    assert not calls and client.tokens==0


def test_wrong_identity_errors_and_unknown_metadata_are_sanitized(fixture):
    client,state,_=fixture;state['data']['shop']['myshopifyDomain']='other-fixture.myshopify.com'
    assert export_evidence(client,config())['reason']=='SHOP_IDENTITY_UNVERIFIED'
    state['errors']=[{'message':'SECRET_VALUE'}];result=export_evidence(client,config())
    assert result['reason']=='SCHEMA_ACCESS_UNVERIFIED' and 'SECRET_VALUE' not in str(result)


def test_no_caller_supplied_operation_or_mutation(fixture):
    client,_,calls=fixture
    with pytest.raises(EvidenceError,match='UNSUPPORTED_READ_DOCUMENT'):EvidenceReader(client).read('mutation { bad }',{})
    assert not calls


def test_missing_required_schema_retains_partial_evidence_without_empty_claim(fixture):
    client,state,_=fixture;state['data']['variant']['fields']=[]
    r=export_evidence(client,config())
    assert r['reason']=='REQUIRED_BARCODE_SCHEMA_UNAVAILABLE' and not r['complete_barcode_state_verified']


def test_related_type_cycles_are_bounded_without_assuming_barcode_shape(fixture):
    client,state,calls=fixture
    state['types']={'SyntheticBarcodeCollection':{'name':'SyntheticBarcodeCollection','kind':'OBJECT','fields':[field('self','SyntheticBarcodeCollection')]}}
    result=export_evidence(client,config())
    assert result['reason']=='SCHEMA_EVIDENCE_REQUIRES_REVIEW' and len(calls)==3
    assert not result['contract_verified']


def test_type_budget_failure_is_explicit_and_never_certifies_completeness(fixture):
    client,state,calls=fixture
    types={'SyntheticBarcodeCollection':{'name':'SyntheticBarcodeCollection','kind':'OBJECT','fields':[field('next','Chain0')]}}
    for i in range(14):types['Chain'+str(i)]={'name':'Chain'+str(i),'kind':'OBJECT','fields':[field('next','Chain'+str(i+1))]}
    state['types']=types
    result=export_evidence(client,config())
    assert result['reason']=='SCHEMA_TYPE_BUDGET_REACHED' and len(result['related_types'])==12
    assert not result['complete_barcode_state_verified']


def test_union_members_and_input_defaults_are_observed_without_inventing_semantics(fixture):
    client,state,_=fixture
    state['data']['barcodeInput']['inputFields'][0]['defaultValue']=None
    state['types']={'SyntheticBarcodeCollection':{'name':'SyntheticBarcodeCollection','kind':'UNION','possibleTypes':[ref('SyntheticMember')]}}
    result=export_evidence(client,config())
    assert 'SyntheticMember' in result['related_types']
    assert 'defaultValue' in result['schema_metadata']['barcode_input']['inputFields'][0]
    assert not result['contract_verified']


def test_unrelated_bulk_required_fields_are_visible_without_traversing_or_setting_them(fixture):
    client,state,calls=fixture
    state['data']['bulkInput']['inputFields'].append({'name':'requiredFixtureField','type':{'kind':'NON_NULL','name':None,'ofType':ref('SyntheticUnrelatedInput','INPUT_OBJECT')},'defaultValue':None})
    result=export_evidence(client,config())
    retained=result['schema_metadata']['bulk_input']['inputFields'][-1]
    assert retained['name']=='requiredFixtureField' and retained['type']['kind']=='NON_NULL'
    assert 'SyntheticUnrelatedInput' not in result['related_types'] and len(calls)==3
    assert result['status']=='BLOCKED' and not result['contract_verified']
