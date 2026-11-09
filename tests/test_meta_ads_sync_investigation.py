"""Reproduce preflight 409s and worker failures without any live provider calls."""
from datetime import datetime, timedelta
import json
import logging
import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError
from test_meta_ads import sessions, configured, FakeClient, insight
from meta_ads_client import configuration, configuration_status, account_id, enabled, Client, AdsError
from meta_ads_worker import request_sync, tick
from meta_ads_models import MetaAdsSync as Sync, MetaAdsDaily as Daily
from meta_ads_report import report


@pytest.mark.parametrize('value', ['true', 'TRUE', 'True', ' true ', '\ntrue\r\n'])
def test_runtime_flag_normalization_and_dynamic_reads(configured, monkeypatch, value):
    monkeypatch.setenv('META_ADS_SYNC_ENABLED', 'false'); assert not enabled()
    monkeypatch.setenv('META_ADS_SYNC_ENABLED', value); assert enabled()
    monkeypatch.setenv('META_ADS_SYNC_ENABLED', 'false'); assert not enabled()


@pytest.mark.parametrize('value', ['109645107', 'act_109645107', ' 109645107 ', ' act_109645107\n'])
def test_reported_account_id_is_valid(configured, monkeypatch, value):
    monkeypatch.setenv('META_ADS_ACCOUNT_ID', value)
    assert account_id() == 'act_109645107'
    assert configuration()[0] == 'act_109645107'


@pytest.mark.parametrize('key,value,code', [
    ('META_ADS_SYNC_ENABLED', 'false', 'SYNC_DISABLED'),
    ('META_ADS_SYNC_ENABLED', '"true"', 'INVALID_SYNC_ENABLED'),
    ('META_ADS_ACCOUNT_ID', '', 'MISSING_ACCOUNT_ID'),
    ('META_ADS_ACCOUNT_ID', '"109645107"', 'INVALID_ACCOUNT_ID'),
    ('META_ADS_ACCESS_TOKEN', '', 'MISSING_ACCESS_TOKEN'),
    ('META_ADS_ACCESS_TOKEN', '"test-secret"', 'INVALID_ACCESS_TOKEN_FORMAT'),
    ('META_ADS_ACCESS_TOKEN', 'test secret', 'INVALID_ACCESS_TOKEN_FORMAT'),
    ('META_ADS_GRAPH_VERSION', '"v26.0"', 'INVALID_GRAPH_VERSION'),
    ('META_ADS_LOOKBACK_DAYS', '0', 'INVALID_LOOKBACK_DAYS'),
])
def test_each_preflight_rejection_has_zero_api_requests(sessions,monkeypatch,key,value,code):
    monkeypatch.setenv(key,value)
    called=[]
    with sessions() as db:
        with pytest.raises(AdsError,match=code):request_sync(db)
        if account_id():
            row=db.get(Sync,account_id())
            assert row.error_code==code and row.diagnostics=={'stage':'PREFLIGHT','api_requests':0}
    assert tick(sessions,client_factory=lambda:called.append(True))==('DISABLED' if code=='SYNC_DISABLED' else 'ERROR')
    assert called==[]


def test_token_outer_whitespace_is_trimmed_but_never_exposed(configured,monkeypatch):
    monkeypatch.setenv('META_ADS_ACCESS_TOKEN',' test-secret-do-not-print\n')
    assert configuration()[2]=='test-secret-do-not-print'
    result=configuration_status(); assert result['token_present'] and result['configured']
    assert 'test-secret' not in json.dumps(result)


@pytest.mark.parametrize('age',[None,timedelta(minutes=31)])
def test_malformed_or_stale_running_state_can_be_reclaimed(sessions,age):
    with sessions() as db:
        db.add(Sync(account_id='act_123',status='RUNNING',started_at=datetime.utcnow()-age if age else None));db.commit()
    assert tick(sessions,client_factory=FakeClient)=='SUCCESS'
    with sessions() as db:assert db.query(Daily).count()==3


def test_active_running_state_does_not_start_a_second_client(sessions):
    with sessions() as db:
        db.add(Sync(account_id='act_123',status='RUNNING',started_at=datetime.utcnow()));db.commit()
        assert request_sync(db)=='RUNNING'
    called=[]; assert tick(sessions,client_factory=lambda:called.append(True))=='BUSY'; assert not called


def transport_handler(request):
    assert request.method=='GET' and request.url.host=='graph.facebook.com'
    if request.url.path.endswith('/permissions'):
        return httpx.Response(200,json={'data':[{'permission':'ads_read','status':'granted'}]})
    if request.url.path.endswith('/ads'):return httpx.Response(200,json={'data':[]})
    if request.url.path.endswith('/insights'):
        return httpx.Response(200,json={'data':[insight(request.url.params['level'])]})
    return httpx.Response(200,json={'id':'act_123','currency':'LKR','timezone_name':'Asia/Colombo'})


def factory(handler):return lambda:Client(transport=httpx.MockTransport(handler),sleep=lambda _:None)


@pytest.mark.parametrize('code,expected',[(190,'TOKEN_EXPIRED_OR_INVALID'),(10,'ADS_PERMISSION_DENIED'),(100,'PROVIDER_REQUEST_FAILED')])
def test_numeric_provider_failure_persisted_without_secret_text(sessions,caplog,code,expected):
    caplog.set_level(logging.INFO,logger='meta_ads')
    def handler(r):return httpx.Response(400,json={'error':{'code':code,'error_subcode':33,'message':'test-secret-do-not-print'}})
    assert tick(sessions,client_factory=factory(handler))=='ERROR'
    with sessions() as db:
        row=db.get(Sync,'act_123');assert row.error_code==expected
        assert row.diagnostics=={'stage':'PERMISSIONS','api_requests':1,'http_status':400,'provider_code':code,'provider_subcode':33}
        assert row.last_success_at is None and db.query(Daily).count()==0
        assert 'test-secret' not in json.dumps(row.diagnostics)
    assert 'test-secret' not in caplog.text
    assert 'event=api_request' in caplog.text and 'event=failed' in caplog.text


def test_missing_permission_does_not_fetch_account_or_insights(sessions):
    calls=[]
    def handler(r):
        calls.append(r.url.path)
        return httpx.Response(200,json={'data':[{'permission':'whatsapp_business_messaging','status':'granted'}]})
    assert tick(sessions,client_factory=factory(handler))=='ERROR'
    assert len(calls)==1
    with sessions() as db:
        row=db.get(Sync,'act_123'); assert row.error_code=='ADS_READ_REQUIRED'
        assert row.diagnostics['stage']=='PERMISSIONS' and row.diagnostics['api_requests']==1


def test_successful_real_client_contract_persists_and_report_reflects_it(sessions):
    assert tick(sessions,client_factory=factory(transport_handler))=='SUCCESS'
    with sessions() as db:
        row=db.get(Sync,'act_123')
        assert row.currency=='LKR' and row.last_success_at and row.error_code is None
        assert row.diagnostics['api_requests']==6 and row.diagnostics['published_rows']==3
        data=report(db)
        assert data['integration']['status']=='SUCCESS' and data['integration']['last_success']
        assert data['summary']['currency']=='LKR' and data['summary']['spend']==100
        assert db.query(Daily).count()==3


def test_publication_failure_preserves_previous_valid_facts_and_records_phase(sessions,monkeypatch):
    assert tick(sessions,client_factory=factory(transport_handler))=='SUCCESS'
    with sessions() as db:success=db.get(Sync,'act_123').last_success_at
    def fail(*a,**kw):raise SQLAlchemyError('test-secret-do-not-print')
    monkeypatch.setattr(sessions.class_,'bulk_insert_mappings',fail)
    assert tick(sessions,force=True,client_factory=factory(transport_handler))=='ERROR'
    with sessions() as db:
        row=db.get(Sync,'act_123');assert row.last_success_at==success
        assert row.error_code=='SYNC_INTERRUPTED' and row.diagnostics['stage']=='PUBLISH'
        assert db.query(Daily).count()==3


def test_preflight_failure_does_not_destroy_previous_success(sessions,monkeypatch):
    tick(sessions,client_factory=factory(transport_handler))
    with sessions() as db:success=db.get(Sync,'act_123').last_success_at
    monkeypatch.setenv('META_ADS_ACCESS_TOKEN','')
    with sessions() as db:
        with pytest.raises(AdsError,match='MISSING_ACCESS_TOKEN'):request_sync(db)
        row=db.get(Sync,'act_123'); assert row.last_success_at==success and row.status=='CONFIG_ERROR'
        assert db.query(Daily).count()==3


def test_confirmed_production_symptoms_mean_disabled_preflight(sessions,monkeypatch):
    monkeypatch.setenv('META_ADS_ACCOUNT_ID','109645107')
    monkeypatch.setenv('META_ADS_SYNC_ENABLED','false')
    with sessions() as db:
        before=report(db)
        assert before['integration']['account_id']=='act_109645107'
        assert before['integration']['configured'] and not before['integration']['enabled']
        assert before['integration']['status']=='NEVER' and before['integration']['last_success'] is None
        assert before['integration']['currency'] is None
        with pytest.raises(AdsError,match='SYNC_DISABLED'):request_sync(db)
        row=db.get(Sync,'act_109645107');assert row.diagnostics['api_requests']==0


@pytest.mark.parametrize('held',[True,False])
def test_postgres_lock_busy_and_release_paths_do_not_fetch_api(configured,monkeypatch,held):
    from types import SimpleNamespace
    import meta_ads_worker as worker
    statements=[];closed=[];commits=[];calls=[]
    class Connection:
        def execute(self,statement):
            statements.append(str(statement));return SimpleNamespace(scalar=lambda:held)
        def close(self):closed.append(True)
    connection=Connection()
    engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),connect=lambda:connection)
    row=SimpleNamespace(status='NEVER',started_at=None,last_success_at=datetime.utcnow() if held else None,
                        requested_at=None,error_code=None,diagnostics=None)
    class DB:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get_bind(self):return engine
        def get(self,*args):return row
        def commit(self):commits.append(True)
    monkeypatch.setattr(worker,'state',lambda *a:row)
    assert tick(lambda:DB(),client_factory=lambda:calls.append(True))==('IDLE' if held else 'BUSY')
    assert closed==[True] and not calls
    if held:assert any('pg_advisory_unlock' in sql for sql in statements)
    else:assert row.error_code=='ADVISORY_LOCK_BUSY' and row.diagnostics['api_requests']==0
