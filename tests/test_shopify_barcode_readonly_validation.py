"""No semantic-gate overrides, write activation or live Shopify connections."""
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import pytest
from settings import settings
import retail_barcode_contract as contract
import retail_barcode_shopify as adapter
from test_shopify_barcode_pagination import remote, SNAP
from scripts import validate_shopify_barcode_readonly as diagnostic


@pytest.fixture
def scanned(remote):
    original,state=remote
    client=diagnostic.DiagnosticClient(http_client=original.http)
    client._token='fake';client._expires_at=10**12
    assert contract.MUTATION_SEMANTICS_VERIFIED is True
    assert not settings.SHOPIFY_PRODUCT_WRITE_ENABLED and not settings.RETAIL_BARCODE_SYNC_ENABLED
    yield client,state
    assert not state.mutations
    assert contract.MUTATION_SEMANTICS_VERIFIED is True
    assert not settings.SHOPIFY_PRODUCT_WRITE_ENABLED and not settings.RETAIL_BARCODE_SYNC_ENABLED


def test_all_accessible_scan_without_database_or_fabricated_candidate(scanned):
    client,state=scanned
    result=diagnostic.run_diagnostic(client=client,allocation_loader=lambda _:pytest.fail('Unexpected DB read'))
    assert result['status']=='READ_ONLY_SCAN_COMPLETE' and result['coverage_complete']
    assert result['catalog_membership_verified'] and not result['atomic_snapshot']
    assert result['concurrent_edit_risk'] and not result['write_authorized']
    assert result['candidate'] is None and not result['collision_check_for_candidate']
    assert result['metrics']['fully_examined_variants']==3
    assert result['metrics']['barcode_values_examined']==2
    assert result['scope_observations']=={'read_products':True,'write_products':True}
    assert not any(call['query']==adapter.READ for call in state.calls)


def test_all_secondary_pages_and_aggregate_metrics(scanned):
    client,state=scanned
    state.barcodes['2']=[{'value':str(i),'type':None} for i in range(203)]
    result=diagnostic.run_diagnostic(client=client)
    assert result['coverage_complete']
    assert result['metrics']['secondary_values_examined']==202
    assert result['metrics']['barcode_values_examined']==204
    assert result['metrics']['untyped_values']==204
    assert [c['variables']['after'] for c in state.calls if c['query']==adapter.READ]==[str(i) for i in range(1,203,20)]


@pytest.mark.parametrize('mode,reason',[
    ('cursor','INVALID_PAGINATION_CURSOR'),('membership','CATALOG_CHANGED_DURING_SCAN'),
    ('duplicate','DUPLICATE_REMOTE_BARCODE'),('legacy','LEGACY_COLLECTION_CONTRADICTION'),
    ('version','API_VERSION_FALLBACK_REJECTED'),('permissions','READ_PRODUCTS_SCOPE_UNVERIFIED')])
def test_partial_scan_never_certifies(scanned,mode,reason):
    client,state=scanned
    if mode=='cursor':state.bad_cursor=True;state.barcodes['2']=[{'value':str(i),'type':None} for i in range(203)]
    elif mode=='membership':state.change_ids=True
    elif mode=='duplicate':state.barcodes['2']=[{'value':'DUP','type':None}]*2
    elif mode=='legacy':state.legacy='private supplier code'
    elif mode=='version':state.version='2026-07'
    else:state.probe['currentAppInstallation']['accessScopes']=[]
    result=diagnostic.run_diagnostic(client=client)
    assert not result['coverage_complete'] and result['candidate'] is None
    assert result['reason']==reason
    assert 'private supplier' not in json.dumps(result)


def test_scan_deadline_fail_closed(scanned,monkeypatch):
    client,_=scanned;monkeypatch.setattr(adapter,'MAX_SCAN_SECONDS',-1)
    result=diagnostic.run_diagnostic(client=client)
    assert result['status']=='INCOMPLETE' and not result['coverage_complete']


def test_selected_existing_allocation_proposed_only_after_actual_inspect(scanned):
    client,state=scanned;snapshot={**SNAP,'sku':'1001'};calls=[]
    def load(ledger_id):calls.append(ledger_id);return copy.deepcopy(snapshot),'COMMITTED_ALLOCATION_READ_ONLY'
    result=diagnostic.run_diagnostic(1,client=client,allocation_loader=load)
    assert calls==[1,1] and result['collision_check_for_candidate']
    assert result['candidate']['code']=='P2' and result['candidate']['administrator_approval_required']
    assert not result['candidate']['write_authorized'] and result['metrics']['target_rechecks']==1


@pytest.mark.parametrize('mode',['secondary_collision','supplier','local_changed','missing'])
def test_no_candidate_if_remote_or_local_state_not_eligible(scanned,mode):
    client,state=scanned;count=0
    if mode=='secondary_collision':state.barcodes['2'] += [{'value':'P2','type':None}]
    if mode=='supplier':state.barcodes['1']=[{'value':'KEEP','type':'EAN'}]
    def load(_):
        nonlocal count
        count+=1
        if mode=='missing':return None,'NO_UNAMBIGUOUS_COMMITTED_ALLOCATION'
        return {**SNAP,'version':2 if mode=='local_changed' and count>1 else 1},'READ_ONLY'
    result=diagnostic.run_diagnostic(1,client=client,allocation_loader=load)
    assert result['coverage_complete'] and result['candidate'] is None


@pytest.mark.parametrize('url,body',[
    ('https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json',{'query':adapter.MUTATION,'variables':{}}),
    ('https://other.myshopify.com/admin/api/2026-10/graphql.json',{'query':adapter.PROBE,'variables':{}}),
    ('https://eb2b70.myshopify.com/admin/api/2026-10/products.json',{}),
    ('https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json?secret=private',{'query':adapter.PROBE,'variables':{}})])
def test_transport_rejects_every_non_whitelisted_request_before_network(url,body):
    delegate=MagicMock();metrics=diagnostic.Metrics();transport=diagnostic.ReadOnlyTransport(delegate,metrics)
    with pytest.raises(adapter.RemoteError,match='READ_ONLY_TRANSPORT_REJECTED'):transport.post(url,json=body)
    delegate.post.assert_not_called()
    assert metrics.blocked_requests==1


def test_auth_reuses_existing_grant_without_printing_credentials():
    delegate=MagicMock();delegate.post.return_value=httpx.Response(200,json={'access_token':'secret'})
    metrics=diagnostic.Metrics();transport=diagnostic.ReadOnlyTransport(delegate,metrics)
    url='https://eb2b70.myshopify.com/admin/oauth/access_token'
    transport.post(url,data={'grant_type':'client_credentials','client_id':'private','client_secret':'secret'})
    assert metrics.auth_requests==1 and 'secret' not in json.dumps(metrics.report())
    with pytest.raises(adapter.RemoteError):transport.post(url,data={'grant_type':'client_credentials','scope':'write_products'})


def test_throttle_costs_and_errors_are_sanitized():
    delegate=MagicMock();delegate.post.return_value=httpx.Response(429,json={
        'errors':[{'message':'private customer info','extensions':{'code':'THROTTLED'}}],
        'extensions':{'cost':{'requestedQueryCost':10,'actualQueryCost':2,'throttleStatus':{'currentlyAvailable':25,'restoreRate':50}}}})
    metrics=diagnostic.Metrics();transport=diagnostic.ReadOnlyTransport(delegate,metrics)
    transport.post('https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json',json={'query':adapter.PROBE,'variables':{}})
    result=metrics.report()
    assert result['graphql_error_codes']=={'THROTTLED':1}
    assert result['minimum_available_cost']==25 and 'private' not in json.dumps(result)


@pytest.mark.parametrize('mode',['timeout','throttle','access'])
def test_actual_read_retries_are_bounded(scanned,mode):
    client,state=scanned;delegate=client.http.delegate;attempts=[]
    def fail(*args,**kwargs):
        attempts.append(1)
        if mode=='timeout':raise httpx.ReadTimeout('secret')
        if mode=='throttle':return httpx.Response(429,headers={'Retry-After':'0'},json={})
        return httpx.Response(200,json={'errors':[{'message':'secret','extensions':{'code':'ACCESS_DENIED'}}]})
    delegate.post=fail
    result=diagnostic.run_diagnostic(client=client)
    assert not result['coverage_complete'] and len(attempts)<=3
    assert 'secret' not in json.dumps(result)
    if mode=='timeout':assert result['metrics']['timeouts']==len(attempts)
    if mode=='throttle':assert result['metrics']['throttle_responses']==3


def test_existing_source_gate_blocks_even_diagnostic_write(scanned):
    client,_=scanned
    with pytest.raises(adapter.RemoteError,match='READ_ONLY_DIAGNOSTIC'):client.write_empty(SNAP)


def test_missing_connection_and_source_mismatch_never_access_network(monkeypatch):
    config=SimpleNamespace(SHOPIFY_PRODUCT_WRITE_ENABLED=False,RETAIL_BARCODE_SYNC_ENABLED=False,
        SHOPIFY_SHOP=None,SHOPIFY_CLIENT_ID=None,SHOPIFY_CLIENT_SECRET=None)
    assert diagnostic.run_diagnostic(config=config)['reason']=='RUNTIME_CONNECTION_UNAVAILABLE'
    monkeypatch.setattr(diagnostic,'EXPECTED_ADAPTER_SHA','different')
    assert diagnostic.run_diagnostic(config=config)['reason']=='DEPLOYED_SCANNER_VERSION_MISMATCH'


def test_allocation_reads_enforced_read_only_and_always_rollback(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','eb2b70')
    row={**SNAP,'sku':'1001','barcode':None,'product_status':'ACTIVE','missing':False,'stale':False,'synced_at':datetime.utcnow()}
    engine=MagicMock();engine.dialect.name='postgresql';conn=engine.connect.return_value.__enter__.return_value
    first=MagicMock();first.mappings.return_value.all.return_value=[row]
    second=MagicMock();second.scalar_one.return_value=0;conn.execute.side_effect=[first,second]
    snapshot,reason=diagnostic.load_allocation(1,engine)
    assert snapshot['code']=='P2' and reason=='COMMITTED_ALLOCATION_READ_ONLY'
    assert [c.args[0] for c in conn.exec_driver_sql.call_args_list]==['SET TRANSACTION READ ONLY',"SET LOCAL statement_timeout = '5000ms'"]
    assert all(str(c.args[0]).lstrip().startswith('SELECT') for c in conn.execute.call_args_list)
    conn.begin.return_value.rollback.assert_called_once();conn.commit.assert_not_called()


def test_allocation_query_failure_is_sanitized_and_rolled_back():
    engine=MagicMock();engine.dialect.name='postgresql';conn=engine.connect.return_value.__enter__.return_value
    conn.execute.side_effect=RuntimeError('password secret')
    assert diagnostic.load_allocation(1,engine)==(None,'CRM_READ_ONLY_QUERY_FAILED')
    conn.begin.return_value.rollback.assert_called_once()


def test_postgres_allocation_transaction_is_read_only_and_rollback_only(monkeypatch):
    import os
    from uuid import uuid4
    from sqlalchemy import create_engine,event,text
    from sqlalchemy.engine import make_url
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Disposable local TEST_POSTGRESQL_URL not configured')
    parsed=make_url(url)
    assert parsed.host in {'127.0.0.1','localhost'} and parsed.database=='pixiepinks_test'
    admin=create_engine(url);schema='readonly_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        with engine.begin() as conn:
            conn.execute(text('''CREATE TABLE retail_variants (id integer,version integer,shop text,product_id text,variant_id text,
                sku text,barcode text,local_barcode text,product_status text,missing boolean,stale boolean,synced_at timestamp)'''))
            conn.execute(text('CREATE TABLE retail_identifiers (id integer,value text,variant_id integer,shop text,kind text)'))
            conn.execute(text("""INSERT INTO retail_variants VALUES (1,1,'eb2b70','gid://shopify/Product/10','gid://shopify/ProductVariant/1',
                '1001',NULL,'P2','ACTIVE',false,false,CURRENT_TIMESTAMP)"""))
            conn.execute(text("INSERT INTO retail_identifiers VALUES (1,'P2',1,'eb2b70','barcode')"))
        observed=[];commits=[]
        @event.listens_for(engine,'before_cursor_execute')
        def verify_readonly(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().startswith('SELECT'):
                cursor.execute('SHOW transaction_read_only');observed.append(cursor.fetchone()[0])
        @event.listens_for(engine,'commit')
        def unexpected_commit(conn):commits.append(True)
        monkeypatch.setattr(settings,'SHOPIFY_SHOP','eb2b70')
        snapshot,reason=diagnostic.load_allocation(1,engine)
        assert reason=='COMMITTED_ALLOCATION_READ_ONLY' and snapshot['code']=='P2'
        assert observed==['on','on'] and commits==[]
        with engine.connect() as conn:
            # Inspect via a separate read-only transaction too.
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            assert conn.execute(text('SELECT count(*) FROM retail_identifiers')).scalar_one()==1
            with pytest.raises(Exception):conn.execute(text("UPDATE retail_variants SET local_barcode='CHANGED'"))
            conn.rollback()
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_bad_request_diagnostic_retains_safe_error_code_without_message(scanned):
    client,state=scanned;post=client.http.delegate.post
    def rejected_read(url,*args,**kwargs):
        if kwargs.get('json',{}).get('query')==adapter.READ:
            return httpx.Response(200,json={'errors':[{'message':'first cannot exceed 20',
                'extensions':{'code':'BAD_REQUEST'}}]},headers={'X-Shopify-API-Version':'2026-10'})
        return post(url,*args,**kwargs)
    client.http.delegate.post=rejected_read
    result=diagnostic.run_diagnostic(1,client=client,allocation_loader=lambda _: (dict(SNAP),'READ_ONLY'))
    assert result['status']=='INCOMPLETE' and not result['coverage_complete']
    assert result['candidate'] is None and result['metrics']['graphql_error_codes']=={'BAD_REQUEST':1}
    assert 'first cannot exceed' not in json.dumps(result) and not state.mutations
