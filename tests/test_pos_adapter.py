"""Documented 2026-10 schema fixtures and fail-closed HTTP boundary checks."""
import copy,json
from pathlib import Path
import httpx,pytest
import pos_shopify as a
from settings import settings
from scripts import validate_pos_readonly as diagnostic


def node(name):
    if name.startswith('['):return {'kind':'LIST','ofType':node(name[1:-1]),'name':None}
    return {'kind':'SCALAR' if name in {'ID','String','Int','Boolean','Money'} else 'INPUT_OBJECT','name':name,'ofType':None}


def schema():
    return json.loads((Path(__file__).parent/'fixtures/pos_2026_10_contract.json').read_text())


def transport(monkeypatch,change=None,status=200,version='2026-10',errors=None):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','fixture');client=a.POSShopify();client.get_token=lambda:'mock-token-never-live';calls=[]
    def handler(request):
        raw=json.loads(request.content);calls.append(raw);q=raw['query']
        data=schema() if q==a.PROBE else {'shop':{'myshopifyDomain':'fixture.myshopify.com','currencyCode':'LKR'},'order':None,'orders':{'nodes':[],'pageInfo':{'hasNextPage':False}}}
        if change and q==a.PROBE:change(data)
        return httpx.Response(status,json={'data':data,**({'errors':errors} if errors else {})},headers={'X-Shopify-API-Version':version})
    client.http=httpx.Client(transport=httpx.MockTransport(handler));return client,calls


def test_supported_schema_and_real_read_queries(monkeypatch):
    client,calls=transport(monkeypatch);cap=client.capabilities()
    assert cap['compatible'] and cap['checkout_ready'] and cap['refund_ready']
    assert [v['query'] for v in calls]==[a.PROBE,a.READ,a.FIND]
    assert all(v['query'].startswith('query ') for v in calls)


@pytest.mark.parametrize('change',[
    lambda d:d['currentAppInstallation'].update(accessScopes=[{'handle':'read_orders'}]),
    lambda d:d['t0']['inputFields'].append({'name':'newRequired','type':{'kind':'NON_NULL','ofType':node('String')},'defaultValue':None}),
    lambda d:d['restockEnum'].update(enumValues=[]),
    lambda d:d['mutation'].update(fields=[]),
    lambda d:d['__schema'].update(directives=[]),
])
def test_contract_drift_and_scopes_block(monkeypatch,change):
    client,_=transport(monkeypatch,change);assert not client.capabilities()['checkout_ready']


@pytest.mark.parametrize('kwargs',[{'status':429},{'version':'2026-07'},{'errors':[{'message':'denied'}]}])
def test_api_errors_fail_closed_without_retry(monkeypatch,kwargs):
    client,calls=transport(monkeypatch,**kwargs)
    with pytest.raises(a.POSRemoteError):client.capabilities()
    assert len(calls)==1


def test_flags_and_readonly_diagnostic_block_both_mutations(monkeypatch):
    client,calls=transport(monkeypatch);monkeypatch.setattr(settings,'POS_CHECKOUT_WRITE_ENABLED',False);monkeypatch.setattr(settings,'POS_REFUND_WRITE_ENABLED',False)
    for query in [a.CREATE,a.REFUND]:
        with pytest.raises(a.POSRemoteError):client.request(query,{})
    assert not calls
    safe=diagnostic.ReadOnlyClient()
    for query in [a.CREATE,a.REFUND]:
        with pytest.raises(a.POSRemoteError):safe.request(query,{})
    for method in [safe.create,safe.refund]:
        with pytest.raises(a.POSRemoteError):method({})


def test_order_search_exhausts_pages_and_rejects_cursor_loops():
    client=a.POSShopify();calls=[]
    def request(q,v):
        calls.append(v);return {'orders':{'nodes':[{'sourceIdentifier':'exact','id':str(len(calls))}],'pageInfo':{'hasNextPage':len(calls)==1,'endCursor':'next'}}}
    client.request=request;assert len(client.find('exact'))==2 and calls[-1]['after']=='next'
    client.request=lambda q,v:{'orders':{'nodes':[],'pageInfo':{'hasNextPage':True,'endCursor':'same'}}}
    with pytest.raises(a.POSRemoteError):client.find('exact')


@pytest.mark.parametrize('permission',['view','sell','discount','hold','refund','void','register_open','register_close','reports','admin'])
def test_pos_action_grants_remain_explicit(monkeypatch,permission):
    from types import SimpleNamespace
    from fastapi import HTTPException
    import staff_access
    import pos_service
    monkeypatch.setattr(settings,'POS_ENABLED',True)
    user=SimpleNamespace(role='STAFF',is_active=True,section_access=['pos.'+permission])
    assert staff_access.allowed(user,'pos.'+permission)
    if permission!='view':
        with pytest.raises(HTTPException):pos_service.require(user,permission)
        user.section_access.append('pos.view')
    pos_service.require(user,permission)
    for other in pos_service.PERMISSIONS:
        if other in {permission,'view'}:continue
        assert not staff_access.allowed(user,'pos.'+other)
        with pytest.raises(HTTPException):pos_service.require(user,other)
    monkeypatch.setattr(settings,'POS_ENABLED',False)
    assert not staff_access.allowed(user,'pos.'+permission)
    with pytest.raises(HTTPException):pos_service.require(user,permission)


def test_diagnostic_queries_only_without_database_writes(monkeypatch):
    import inventory_shopify
    client,calls=transport(monkeypatch)
    monkeypatch.setattr(inventory_shopify.client,'locations',lambda:[])
    def no_db():raise AssertionError('No database access without a variant selection')
    monkeypatch.setattr(diagnostic,'SessionLocal',no_db)
    result=diagnostic.run(client)
    assert result['status']=='READ_ONLY_COMPLETE' and result['write_authorized'] is False
    assert all(raw['query'].startswith('query ') for raw in calls)
