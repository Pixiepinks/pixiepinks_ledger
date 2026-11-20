"""Mocked incident/recovery tests. No live Shopify or production accounting writes."""
import copy,json
from datetime import datetime,timedelta
from uuid import uuid4
import httpx,pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from models import User,SalesPayment,JournalEntry,JournalLine
from pos_models import POSOperation,POSCheckout,POSPosting,POSAudit
import pos_service as s
import pos_shopify as adapter
from pos_failure_codes import failure_code,sanitized
from scripts.inspect_pos_sales_readonly import run,ReadOnlyClient,read_only_session


def diagnostic(db,operation):return s.operation_view(db,operation)['diagnostic']


def test_create_returned_then_readback_failure_blocks_second_sale_without_resend(pos):
    r=pos;first=checkout(r);r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos);second=checkout(r)
    with r.sessions() as db:
        sale=db.get(POSCheckout,first);op=db.query(POSOperation).filter_by(checkout_id=first).one();refs=[sale.reference,db.get(POSCheckout,second).reference]
        assert sale.status=='REVIEW' and op.error=='READBACK_OR_POSTING_UNAVAILABLE'
        assert op.outcome['remote_id']=='gid://shopify/Order/1' and op.attempts==1
        assert diagnostic(db,op)=={'stage':'SHOPIFY_READBACK','failure_code':'SHOPIFY_READBACK_UNAVAILABLE'}
        assert db.query(POSPosting).count()==0 and db.query(SalesPayment).count()==2
    assert s.dispatch_one(r.sessions,r.pos) is False and len(r.pos.calls)==1
    r.pos.fail_read=False
    report=run(r.sessions,r.pos,refs)
    assert report['sales'][0]['remote']['readback_status']=='VERIFIED_AGAINST_FROZEN_SALE'
    assert report['sales'][0]['blocks_shop_queue'] and not report['sales'][1]['blocks_shop_queue']
    assert report['sales'][0]['existing_reconciliation_search_condition_met']
    assert report['sales'][1]['remote']['search_match_count']==0
    with r.sessions() as db:
        assert db.query(POSPosting).count()==0 and db.get(POSCheckout,first).status=='REVIEW'
        assert db.get(POSCheckout,second).status=='QUEUED'
        # Reconciliation is explicitly authorized only against these local mocks.
        op=db.query(POSOperation).filter_by(checkout_id=first).one()
        s.reconcile(db,db.get(User,r.uid),op.id,'Synthetic verified remote order evidence',r.pos)
        assert db.query(POSPosting).count()==1
        with pytest.raises(HTTPException):s.reconcile(db,db.get(User,r.uid),op.id,'Do not reconcile twice',r.pos)
    assert len(r.pos.calls)==1
    assert s.dispatch_one(r.sessions,r.pos) is True and len(r.pos.calls)==2
    with r.sessions() as db:
        assert db.get(POSCheckout,second).status=='COMPLETE'
        assert db.query(POSPosting).count()==2 and db.query(SalesPayment).count()==2


@pytest.mark.parametrize('field,value,code',[
 ('fulfillments',[],'FULFILLMENT_UNAVAILABLE'),
 ('displayFinancialStatus','PENDING','ORDER_PAYMENT_OR_TOTAL_MISMATCH'),
 ('sourceIdentifier','wrong','ORDER_IDENTITY_MISMATCH'),
 ('lineItems',{'nodes':[],'pageInfo':{'hasNextPage':True}},'ORDER_LINES_INCOMPLETE'),
])
def test_strict_verification_failures_are_classified_not_relaxed(pos,field,value,code):
    r=pos;rid=checkout(r);original=r.pos.read
    def read(oid):order=original(oid);order[field]=copy.deepcopy(value);return order
    r.pos.read=read;s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        op=db.query(POSOperation).one();assert diagnostic(db,op)=={'stage':'ORDER_VERIFICATION','failure_code':code}
        assert db.get(POSCheckout,rid).status=='REVIEW' and db.query(POSPosting).count()==0
        with pytest.raises(HTTPException):s.reconcile(db,db.get(User,r.uid),op.id,'Mismatch must remain blocked',r.pos)
    assert len(r.pos.calls)==1


def test_partial_posting_failure_rolls_back_retains_order_and_recovers_once(pos,monkeypatch):
    r=pos;rid=checkout(r);original=s.publish_sale
    def broken(db,sale,observed):
        original(db,sale,observed)
        raise IntegrityError('sensitive SQL','do not export params',Exception('private data'))
    monkeypatch.setattr(s,'publish_sale',broken);s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        op=db.query(POSOperation).one()
        assert diagnostic(db,op)=={'stage':'LOCAL_POSTING','failure_code':'DATABASE_ERROR'}
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==db.query(JournalLine).count()==0
        assert db.query(SalesPayment).one().accounting_entry_id is None
        assert op.outcome=={'remote_id':'gid://shopify/Order/1'}
        assert 'sensitive SQL' not in json.dumps(s.public_sale(db,db.get(POSCheckout,rid)))
    with r.sessions() as db:
        with pytest.raises(HTTPException) as failure:s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Synthetic still-broken posting',r.pos)
        assert failure.value.status_code==409 and 'DATABASE_ERROR' in failure.value.detail
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==0
        assert db.get(POSCheckout,rid).status=='REVIEW'
    monkeypatch.setattr(s,'publish_sale',original)
    with r.sessions() as db:
        s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Synthetic local posting recovery',r.pos)
        assert db.query(POSPosting).count()==1 and db.query(SalesPayment).count()==1
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_stale_dispatch_updates_sale_badge_but_preserves_attempt_fence(pos):
    r=pos;rid=checkout(r)
    with r.sessions() as db:
        op=db.query(POSOperation).one();op.status='DISPATCHING';op.attempts=1;op.dispatched_at=datetime.utcnow()-timedelta(minutes=3);db.commit()
    assert s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='UNCERTAIN'
        assert db.query(POSOperation).one().attempts==1
    assert s.dispatch_one(r.sessions,r.pos) is False and not r.pos.calls


def test_diagnostic_reads_recorded_id_even_when_order_search_is_not_ready(pos):
    r=pos;rid=checkout(r);r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    with r.sessions() as db:ref=db.get(POSCheckout,rid).reference
    r.pos.find=lambda reference:[]
    result=run(r.sessions,r.pos,[ref])['sales'][0]
    assert result['remote']['readback_status']=='VERIFIED_AGAINST_FROZEN_SALE'
    assert result['remote']['search_match_count']==0 and not result['existing_reconciliation_search_condition_met']
    with r.sessions() as db:
        with pytest.raises(HTTPException):s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'No blind resend',r.pos)
        assert db.query(POSPosting).count()==0
    assert len(r.pos.calls)==1


def test_diagnostic_queries_only_and_database_write_rejection(pos,monkeypatch):
    monkeypatch.setattr(adapter.settings,'SHOPIFY_SHOP','fixture');client=ReadOnlyClient();client.get_token=lambda:'mock-token'
    calls=[]
    def handler(request):
        query=json.loads(request.content)['query'];calls.append(query)
        assert query in {adapter.READ,adapter.FIND}
        return httpx.Response(200,headers={'X-Shopify-API-Version':'2026-10'},json={'data':{'shop':{'myshopifyDomain':'fixture.myshopify.com','currencyCode':'LKR'},'order':None,'orders':{'nodes':[],'pageInfo':{'hasNextPage':False}}}})
    client.http=httpx.Client(transport=httpx.MockTransport(handler))
    for q in [adapter.CREATE,adapter.REFUND,adapter.PROBE]:
        with pytest.raises(adapter.POSRemoteError):client.request(q,{})
    for method in [client.create,client.refund]:
        with pytest.raises(adapter.POSRemoteError):method({})
    assert not calls
    client.read('gid://shopify/Order/1');client.find('POS-'+'A'*20)
    assert calls==[adapter.READ,adapter.FIND]
    with pytest.raises(Exception):
        with read_only_session(pos.sessions) as db:db.execute(text("UPDATE pos_sessions SET status='CLOSED'"))
    # Connection query_only setting must be restored when returned to the pool.
    with pos.sessions() as db:assert db.execute(text('PRAGMA query_only')).scalar()==0


def test_failure_metadata_never_exports_exception_strings():
    assert failure_code('LOCAL_POSTING',RuntimeError('token=secret customer=private'))=='INTERNAL_FAILURE'
    assert sanitized({'stage':'LOCAL_POSTING','failure_code':'DATABASE_ERROR','exception':'secret'})=={'stage':'LOCAL_POSTING','failure_code':'DATABASE_ERROR'}
    assert sanitized({'stage':'unexpected secret','failure_code':'DATABASE_ERROR'}) is None


def test_two_350_cash_claims_explain_zero_completed_sales_and_700_cash(pos):
    from decimal import Decimal
    from retail_models import RetailVariant
    from pos_models import POSSession
    from test_pos import cash
    r=pos
    with r.sessions() as db:db.get(RetailVariant,r.products[0]['id']).price=Decimal('350');db.commit()
    inspect=r.pos.inspect
    r.pos.inspect=lambda line:{**inspect(line),'price':'350.00'}
    first=checkout(r,plan=cash('350.00','350.00'));r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos)
    second=checkout(r,plan=cash('350.00','350.00'))
    assert Decimal(r.client.get('/crm/pos/api/sales?format=summary').json()['totals']['grand_total'])==0
    with r.sessions() as db:
        session=db.get(POSSession,r.sid);expected_cash=s.drawer(db,session)
        assert expected_cash-Decimal(session.opening_float)==Decimal('700.00')
        assert db.query(SalesPayment).count()==2 and db.query(POSPosting).count()==0
    r.pos.fail_read=False
    with r.sessions() as db:s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).filter_by(checkout_id=first).one().id,'Synthetic 350-order verification',r.pos)
    assert s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert s.drawer(db,db.get(POSSession,r.sid))==expected_cash
        assert db.query(SalesPayment).count()==2 and db.query(POSPosting).count()==2
        assert db.get(POSCheckout,second).status=='COMPLETE'
    assert Decimal(r.client.get('/crm/pos/api/sales?format=summary').json()['totals']['grand_total'])==Decimal('700.00')
    assert len(r.pos.calls)==2 and not r.remote.mutations


def test_multiple_remote_orders_do_not_authorize_reconciliation_or_resend(pos):
    r=pos;rid=checkout(r);r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    with r.sessions() as db:ref=db.get(POSCheckout,rid).reference
    matches=r.pos.find(ref);r.pos.find=lambda reference:[*matches,{**matches[0],'id':'gid://shopify/Order/999'}]
    result=run(r.sessions,r.pos,[ref])['sales'][0]
    assert result['remote']['search_match_count']==2 and not result['existing_reconciliation_search_condition_met']
    with r.sessions() as db:
        with pytest.raises(HTTPException) as error:s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Ambiguous remote matches must block',r.pos)
        assert 'ORDER_MATCH_COUNT_NOT_ONE' in error.value.detail
        assert db.query(POSPosting).count()==0 and db.get(POSCheckout,rid).status=='REVIEW'
    assert len(r.pos.calls)==1 and not r.remote.mutations
