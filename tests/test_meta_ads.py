"""No live Meta calls; financial and attribution invariants use isolated SQLite."""
from datetime import datetime, timedelta, date
from decimal import Decimal
import json
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from models import (WhatsAppConversation as C, WhatsAppConversationMessage as M, WhatsAppMessageReferral as R,
                    SalesOrder as O, SalesPayment as P, Lead, CRMAnalyticsPhone as Phone)
from meta_ads_models import MetaAdsDaily as Daily, MetaAdsSync as Sync
from meta_ads_client import Client, AdsError, parse, MESSAGE_ACTION
from meta_ads_worker import tick
from meta_ads_report import report


@pytest.fixture
def configured(monkeypatch):
    for k,v in {'META_ADS_ACCOUNT_ID':'123','META_ADS_GRAPH_VERSION':'v26.0','META_ADS_ACCESS_TOKEN':'test-secret-do-not-print','META_ADS_SYNC_ENABLED':'true'}.items():monkeypatch.setenv(k,v)


@pytest.fixture
def sessions(tmp_path,configured):
    engine=create_engine(f'sqlite:///{tmp_path}/ads.db');Base.metadata.create_all(engine)
    result=sessionmaker(bind=engine,expire_on_commit=False)
    yield result
    engine.dispose()


def insight(level='ad',**changes):
    today=datetime.now().date().isoformat()
    return {'account_id':'123','account_currency':'LKR','campaign_id':'11','campaign_name':'Campaign',
        'adset_id':'22','adset_name':'Ad set','ad_id':'33','ad_name':'Bicycles','date_start':today,'date_stop':today,
        'spend':'100','impressions':'400','reach':'300','inline_link_clicks':'12',
        'actions':[{'action_type':MESSAGE_ACTION,'value':'4'}],**changes}


def client(handler):
    return Client(transport=httpx.MockTransport(handler),sleep=lambda _:None)


@pytest.mark.parametrize('code,expected',[(190,'TOKEN_EXPIRED_OR_INVALID'),(10,'ADS_PERMISSION_DENIED'),(200,'ADS_PERMISSION_DENIED'),(100,'PROVIDER_REQUEST_FAILED')])
def test_safe_provider_errors(configured,code,expected):
    c=client(lambda r:httpx.Response(400,json={'error':{'code':code,'message':'test-secret-do-not-print'}}))
    with pytest.raises(AdsError) as error:c.get('act_123')
    assert str(error.value)==expected and 'secret' not in str(error.value)
    c.close()


def test_rate_limits_and_pagination_do_not_follow_url(configured):
    calls=[]
    def handle(r):
        assert r.url.host=='graph.facebook.com' and r.method=='GET'
        assert r.headers['Authorization']=='Bearer test-secret-do-not-print'
        calls.append(str(r.url))
        if len(calls)==1:return httpx.Response(429,headers={'Retry-After':'1'},json={'error':{'code':4}})
        if 'after' not in r.url.params:return httpx.Response(200,json={'data':[insight()], 'paging':{'next':'https://evil.test/?access_token=leak','cursors':{'after':'next'}}})
        return httpx.Response(200,json={'data':[insight(ad_id='34')]})
    c=client(handle);assert len(list(c.insights('ad',date.today(),date.today())))==2;assert len(calls)==3;c.close()


def test_rate_limit_retry_exhaustion(configured):
    calls=[]
    c=client(lambda r:calls.append(r) or httpx.Response(429,json={'error':{'code':4}}))
    with pytest.raises(AdsError,match='RATE_LIMIT'):c.get('act_123')
    assert len(calls)==4;c.close()


def test_verification_requires_separate_ads_read(configured):
    c=client(lambda r:httpx.Response(200,json={'data':[{'permission':'whatsapp_business_messaging','status':'granted'}]}))
    with pytest.raises(AdsError,match='ADS_READ_REQUIRED'):c.verify()
    c.close()


def test_account_verification_and_statuses(configured):
    def handle(r):
        if r.url.path.endswith('/permissions'):return httpx.Response(200,json={'data':[{'permission':'ads_read','status':'granted'}]})
        if r.url.path.endswith('/ads'):return httpx.Response(200,json={'data':[{'id':'33','effective_status':'PAUSED','campaign':{'id':'11','effective_status':'ACTIVE'}}]})
        return httpx.Response(200,json={'id':'act_123','currency':'LKR','timezone_name':'Asia/Colombo'})
    c=client(handle);assert c.verify()['currency']=='LKR';assert c.delivery_statuses()[('ad','33')]=='PAUSED';c.close()


@pytest.mark.parametrize('changes,expected',[({'spend':None,'actions':None},None),({'spend':'NaN'},'INVALID_METRIC'),({'account_currency':'USD'},'CURRENCY_OR_ACCOUNT_MISMATCH'),({'reach':'-1'},'INVALID_METRIC')])
def test_parse_missing_currency_and_numbers(changes,expected):
    if expected:
        with pytest.raises(AdsError,match=expected):parse(insight(**changes),'ad','act_123','LKR',date.today(),date.today(),datetime.utcnow())
    else:
        value=parse(insight(**changes),'ad','act_123','LKR',date.today(),date.today(),datetime.utcnow())
        assert value['spend'] is None and value['messaging'] is None


class FakeClient:
    fail=False
    def verify(self):return {'currency':'LKR','timezone_name':'Asia/Colombo'}
    def delivery_statuses(self):return {('ad','33'):'ACTIVE'}
    def insights(self,level,start,end):
        if self.fail and level=='adset':raise AdsError('RATE_LIMIT')
        return [insight(level)]
    def close(self):pass


def test_sync_three_levels_idempotence_and_failed_refresh_retains_cache(sessions):
    assert tick(sessions,client_factory=FakeClient)=='SUCCESS'
    assert tick(sessions,force=True,client_factory=FakeClient)=='SUCCESS'
    with sessions() as db:
        assert db.query(Daily).count()==3
        assert {r.level for r in db.query(Daily)}=={'ad','adset','campaign'}
        success=db.get(Sync,'act_123').last_success_at
    class Failing(FakeClient):fail=True
    assert tick(sessions,force=True,client_factory=Failing)=='ERROR'
    with sessions() as db:
        assert db.query(Daily).count()==3 and db.get(Sync,'act_123').last_success_at==success
        assert db.get(Sync,'act_123').error_code=='RATE_LIMIT'
    assert tick(sessions,client_factory=FakeClient)=='IDLE'


def test_empty_success_removes_stale_rows_in_refreshed_dates(sessions):
    tick(sessions,client_factory=FakeClient)
    class Empty(FakeClient):
        def insights(self,*a):return []
    assert tick(sessions,force=True,client_factory=Empty)=='SUCCESS'
    with sessions() as db:assert db.query(Daily).count()==0


def seed(db):
    now=datetime.utcnow()-timedelta(minutes=5)
    db.add_all([Phone(raw_phone='94771234999',canonical_phone='+94771234999',country='LK',country_name='Sri Lanka'),
                Phone(raw_phone='+94771234999',canonical_phone='+94771234999',country='LK',country_name='Sri Lanka')])
    c=C(phone_number='94771234999',customer_name='Buyer',mode='HUMAN');db.add(c);db.flush()
    lead=Lead(lead_no='L-1',customer_name='Buyer',mobile=c.phone_number,status='INTERESTED',product_interest='Bicycles',whatsapp_conversation_id=c.id);db.add(lead);db.flush()
    for i,(aid,when,provider) in enumerate([('32',now-timedelta(days=3),'old'),('33',now-timedelta(days=1),'new'),('33',now-timedelta(days=1),'new')]):
        m=M(phone_number=c.phone_number,direction='inbound',message_text='Hello',whatsapp_message_id=provider,created_at=when);db.add(m);db.flush()
        db.add(R(message_id=m.id,source_id=aid,source_type='ad',headline='Bicycle'))
    o=O(internal_order_number='SO-1',idempotency_key='order1',crm_conversation_id=c.id,crm_lead_id=lead.id,customer_name='Buyer',whatsapp_phone='+94771234999',order_status='CONFIRMED',grand_total=1000,currency='LKR',created_by='admin',created_at=now)
    db.add(o);db.flush();db.add(P(sales_order_id=o.id,payment_method='BANK_TRANSFER',amount=1000,currency='LKR',payment_date=now.date(),verification_status='VERIFIED',verified_at=now,verified_by='admin'))
    db.commit();return c.id,o.id


def test_last_touch_dedup_verified_revenue_and_formulas(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        seed(db);data=report(db);row=next(r for r in data['rows'] if r['id']=='33')
        assert row['leads']==1 and row['starts']==1 and row['inbound']==1
        assert row['confirmed_orders']==1 and row['verified_orders']==1 and row['revenue']==1000
        assert row['cpl']==100 and row['cpo']==100 and row['roas']==10 and row['conversion']==100
        assert data['summary']['leads']==1
        assert data['summary']['spend']==100 and data['summary']['roas']==10
        assert row['reach'] is None and row['meta_messaging']==4
        assert next(r for r in data['rows'] if r['id']=='32')['revenue']==0
        assert report(db,interest='Toys')['summary']['leads']==0
        assert report(db,status='NEW')['summary']['confirmed_orders']==0
        assert report(db,campaign='999')['rows']==[]
        assert report(db,level='campaign')['rows'][0]['confirmed_orders']==1


@pytest.mark.parametrize('change',[{'order_status':'CANCELLED'},{'order_status':'RETURNED'},{'shopify_total_refunded':Decimal(1)},{'shopify_financial_status':'PARTIALLY_REFUNDED'}])
def test_cancelled_returned_refunded_never_revenue(sessions,change):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        _,oid=seed(db);o=db.get(O,oid)
        for k,v in change.items():setattr(o,k,v)
        db.commit();row=next(r for r in report(db)['rows'] if r['id']=='33')
        assert row['revenue']==0 and row['confirmed_orders']==0


def test_lead_paid_not_evidence_and_currency_mismatch(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        seed(db);p=db.query(P).one();p.currency='USD';db.query(Lead).one().status='PAID';db.commit()
        row=next(r for r in report(db)['rows'] if r['id']=='33');assert row['revenue']==0 and row['roas']==0
        p.currency='LKR';db.query(O).one().currency='USD';db.commit()
        row=next(r for r in report(db)['rows'] if r['id']=='33');assert row['revenue']==0


def test_uncertain_order_link_and_expired_touch_are_unattributed(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        _,oid=seed(db);o=db.get(O,oid);o.crm_conversation_id=None;db.commit()
        assert report(db)['summary']['unattributed_orders']==1
        o.crm_conversation_id=db.query(C).one().id
        for m in db.query(M):m.created_at=datetime.utcnow()-timedelta(days=31)
        db.commit();assert report(db)['summary']['unattributed_orders']==1


def test_dates_zero_spend_missing_messaging_and_single_day_reach(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        for f in db.query(Daily):f.spend=0;f.messaging=None
        db.commit();today=datetime.now().date().isoformat();r=report(db,today,today)['rows'][0]
        assert r['roas'] is None and r['cpl'] is None and r['meta_messaging'] is None and r['reach']==300
        from fastapi import HTTPException
        with pytest.raises(HTTPException):report(db,'2026-99-99')


def test_manager_requires_explicit_new_grant():
    from types import SimpleNamespace
    from meta_ads_routes import can_view,csv_cell
    assert can_view(SimpleNamespace(role='ADMIN'))
    assert not can_view(SimpleNamespace(role='SALES_MANAGER',section_access=None))
    assert can_view(SimpleNamespace(role='STAFF',section_access=['ad_analytics']))
    assert can_view(SimpleNamespace(role='SALES_MANAGER',section_access=['ad_analytics']))
    assert csv_cell('=SUM(1)')=="'=SUM(1)"


def test_ambiguous_latest_ads_and_outside_window(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        cid,oid=seed(db);latest=db.query(M).filter_by(whatsapp_message_id='new').first()
        m=M(phone_number=latest.phone_number,direction='inbound',message_text='Another ad',whatsapp_message_id='other-ad',created_at=latest.created_at);db.add(m);db.flush()
        db.add(R(message_id=m.id,source_id='34',source_type='ad'));db.commit()
        data=report(db);assert data['summary']['unattributed_orders']==1
        assert data['summary']['leads']==1 and sum(r['leads'] for r in data['rows'])==3


def test_partial_unverified_payment_and_overpayment(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        seed(db);p=db.query(P).one();p.amount=500;db.commit()
        assert report(db,ad='33')['summary']['revenue']==0
        p.amount=1500;p.verification_status='PENDING';db.commit()
        assert report(db,ad='33')['summary']['revenue']==0
        p.verification_status='VERIFIED';db.commit()
        assert report(db,ad='33')['summary']['revenue']==1000


def test_cross_currency_revenue_separate_and_meta_currency_changes(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        seed(db);db.query(O).one().currency='USD';db.query(P).one().currency='USD';db.commit()
        row=report(db,ad='33')['rows'][0]
        assert row['revenue']==0 and row['foreign_revenue']=={'USD':1000}
        f=db.query(Daily).filter_by(level='ad').one();f.currency='USD';db.commit()
        data=report(db,ad='33');assert data['summary']['currency']=='USD' and data['summary']['revenue']==1000


def test_missing_token_keeps_private_cached_reporting(sessions,monkeypatch):
    tick(sessions,client_factory=FakeClient)
    monkeypatch.delenv('META_ADS_ACCESS_TOKEN')
    with sessions() as db:
        data=report(db);assert not data['integration']['configured']
        assert data['summary']['spend']==100


def test_stale_and_incomplete_coverage_are_explicit(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        state=db.get(Sync,'act_123');state.last_success_at=datetime.utcnow()-timedelta(days=2);db.commit()
        data=report(db,(date.today()-timedelta(days=60)).isoformat(),date.today().isoformat())
        assert data['integration']['stale'] and not data['integration']['coverage_complete']


def test_daily_scheduler_disabled_and_locked(sessions,monkeypatch):
    monkeypatch.setenv('META_ADS_SYNC_ENABLED','false');assert tick(sessions,client_factory=FakeClient)=='DISABLED'
    monkeypatch.setenv('META_ADS_SYNC_ENABLED','true')
    with sessions() as db:db.add(Sync(account_id='act_123',status='RUNNING',started_at=datetime.utcnow()));db.commit()
    assert tick(sessions,client_factory=FakeClient)=='BUSY'


def test_provider_cursor_loop_and_invalid_account(configured):
    c=client(lambda r:httpx.Response(200,json={'data':[], 'paging':{'next':'elsewhere','cursors':{'after':'same'}}}))
    with pytest.raises(AdsError,match='INVALID_PAGINATION'):list(c.insights('ad',date.today(),date.today()))
    c.close()
    c=client(lambda r:httpx.Response(200,json={'data':[{'permission':'ads_read','status':'granted'}]}))
    with pytest.raises(AdsError,match='ACCOUNT_VERIFICATION_FAILED'):c.verify()
    c.close()


def test_order_phone_formatting_reuses_existing_normalizer_without_cache_write(sessions):
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:
        seed(db);db.query(Phone).filter(Phone.raw_phone.like('+%')).delete(synchronize_session=False);db.commit()
        before=db.query(Phone).count()
        assert report(db,ad='33')['summary']['revenue']==1000
        assert db.query(Phone).count()==before


def test_claim_is_visible_during_fetch_and_concurrent_tick_is_busy(sessions):
    class Inspect(FakeClient):
        def verify(self):
            with sessions() as db:assert db.get(Sync,'act_123').status=='RUNNING'
            assert tick(sessions,force=True,client_factory=FakeClient)=='BUSY'
            return super().verify()
    assert tick(sessions,client_factory=Inspect)=='SUCCESS'
