from uuid import uuid4
from copy import deepcopy
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from models import Lead,SalesOrder,SalesPayment,JournalEntry,User,WhatsAppConversation as C
from sales_pipeline_models import SalesOpportunity as O,SalesPipelineEvent as E
import sales_pipeline_reconciliation as r
import sales_pipeline as s
from test_sales_pipeline import fixture
from test_whatsapp_quick_messages import inbox
from test_retail_postgres import pg

@pytest.fixture
def candidate(fixture):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        o=db.get(O,oid);l=db.get(Lead,o.lead_id);l.status='DELIVERED';c=db.get(C,o.conversation_id)
        order=SalesOrder(internal_order_number='SYNTHETIC-REVIEW',idempotency_key=str(uuid4()),crm_conversation_id=c.id,crm_lead_id=l.id,customer_name='Synthetic Customer',whatsapp_phone=c.phone_number,created_by='test',order_status='DELIVERED',payment_status='PAID',shopify_sync_status='SYNCED',shopify_lifecycle_status='ORDER_LINKED',grand_total=350,currency='LKR');db.add(order);db.commit()
    return fixture

def detail(v):return v[0].get('/crm/sales-pipeline/reconciliation/api/'+str(v[4])).json()
def payload(v):
    d=detail(v);return {'items':[{'opportunity_id':d['opportunity_id'],'order_id':d['order']['id'],'version':d['version'],'digest':d['digest'],'reviewed':True}],'confirmed':True,'note':'Reviewed synthetic existing exact-conversation evidence'}
def post(v,p,key=None,action='associate'):
    return v[0].post('/crm/sales-pipeline/reconciliation/'+action,json=p,headers={'X-CSRF-Token':v[3],'Idempotency-Key':key or str(uuid4())})

def test_candidate_discovery_association_and_financial_isolation(candidate):
    v=candidate;client,sessions,ids,csrf,oid=v
    assert client.get('/crm/sales-pipeline/reconciliation').status_code==200
    d=detail(v);assert d['eligible'] and d['payment_warning'] and d['evidence']['exact_conversation']
    with sessions() as db:
        before=r.plain(db.query(SalesOrder).one());counts=[db.query(x).count() for x in (SalesOrder,SalesPayment,JournalEntry)]
    p=payload(v);key=str(uuid4());response=post(v,p,key);assert response.status_code==200,response.text
    assert post(v,p,key).json()['idempotent'] is True
    with sessions() as db:
        assert r.plain(db.query(SalesOrder).one())==before
        assert [db.query(x).count() for x in (SalesOrder,SalesPayment,JournalEntry)]==counts
        assert db.get(Lead,db.get(O,oid).lead_id).status=='DELIVERED'
        assert db.query(E).filter(E.key==('reconcile:'+key)).count()==1
    d=detail(v);assert d['category']=='history' and d['payment_warning']
    report=client.get('/crm/sales-pipeline/api').json();assert report['counts']['CONFIRMED']==1 and report['kpi']['open']==0
    assert post(v,p).status_code==409

@pytest.mark.parametrize('change',['version','order','phone','customer','lock','lead','ambiguous','already','data'])
def test_stale_and_conflicting_evidence(candidate,change):
    p=payload(candidate);client,sessions,ids,csrf,oid=candidate
    with sessions() as db:
        order=db.query(SalesOrder).one();o=db.get(O,oid)
        if change=='version':o.version+=1
        if change=='order':order.grand_total=400
        if change=='phone':order.whatsapp_phone='94770099999'
        if change=='customer':
            from models import Customer
            c=Customer(name='Different synthetic customer',phone='94770099998');db.add(c);db.flush();order.customer_id=c.id
        if change=='lock':order.shopify_lifecycle_status='RECONCILIATION_REQUIRED'
        if change=='lead':order.crm_lead_id=None;db.get(Lead,o.lead_id).status='INTERESTED'
        if change=='ambiguous':db.add(SalesOrder(internal_order_number='AMBIGUOUS',idempotency_key=str(uuid4()),crm_conversation_id=ids[0],customer_name='Synthetic',whatsapp_phone=order.whatsapp_phone,created_by='test',grand_total=350,order_status='CONFIRMED'))
        if change=='already':o.order_id=order.id;o.data={**o.data,'order_id':order.id}
        if change=='data':o.data={**o.data,'order_id':99999}
        db.commit()
    result=post(candidate,p);assert result.status_code in (404,409),result.text
    with sessions() as db:assert not any(e.current.get('action')=='ASSOCIATED' for e in db.query(E))

@pytest.mark.parametrize('path',['/crm/sales-pipeline/reconciliation','/crm/sales-pipeline/reconciliation/api','/crm/sales-pipeline/reconciliation/api/1'])
def test_nonadmin_cannot_read(candidate,path):
    candidate[0].post('/login',data={'username':'quick-agent','password':'password'})
    assert candidate[0].get(path).status_code==403

@pytest.mark.parametrize('action',['preview','associate','investigate'])
def test_nonadmin_cannot_write(candidate,action):
    candidate[0].post('/login',data={'username':'quick-agent','password':'password'})
    assert post(candidate,{},action=action).status_code==403

def test_csrf_and_confirmation(candidate):
    p=payload(candidate)
    assert candidate[0].post('/crm/sales-pipeline/reconciliation/associate',json=p).status_code==403
    p['confirmed']=False;assert post(candidate,p).status_code==422
    p['confirmed']=True;p['note']='';assert post(candidate,p).status_code==422

def test_paid_transition_is_explicit_and_preserves_payment(candidate):
    with candidate[1]() as db:db.query(Lead).one().status='PAID';db.commit()
    assert post(candidate,payload(candidate)).status_code==200
    with candidate[1]() as db:
        assert db.query(Lead).one().status=='ORDER_CONFIRMED'
        assert db.query(SalesOrder).one().order_status=='DELIVERED'
        assert db.query(SalesOrder).one().payment_status=='PAID'
        assert db.query(SalesPayment).count()==0
    assert detail(candidate)['payment_warning']

def test_missing_investigation_and_no_stage_change(fixture):
    with fixture[1]() as db:db.query(Lead).one().status='PAID';db.commit()
    d=detail(fixture);assert d['category']=='missing' and d['payment_warning']
    p={'opportunity_id':fixture[4],'version':d['version'],'state':'Awaiting Supporting Evidence','note':'Synthetic evidence pending'};key=str(uuid4())
    assert post(fixture,p,key,action='investigate').status_code==200
    assert post(fixture,p,key,action='investigate').status_code==200
    d=detail(fixture);assert d['stage']=='PAYMENT' and d['investigation']['state']==p['state']
    assert fixture[0].get('/crm/sales-pipeline/reconciliation/api?tab=payment').json()['total']==1

def test_bulk_rejects_duplicate_and_rolls_back(candidate):
    p=payload(candidate);p['items']*=2;assert post(candidate,p).status_code==409
    p=payload(candidate);other={**p['items'][0],'opportunity_id':999999,'order_id':999999};p['items'].append(other)
    assert post(candidate,p).status_code==404
    assert detail(candidate)['eligible']

def test_idempotency_payload_binding(candidate):
    p=payload(candidate);key=str(uuid4());assert post(candidate,p,key).status_code==200
    p['note']='Different approval';assert post(candidate,p,key).status_code==409

def test_search_pagination_filters(candidate):
    client=candidate[0]
    for tab,count in [('ready',1),('missing',0),('payment',1),('conflict',0),('history',0),('all',1)]:
        assert client.get('/crm/sales-pipeline/reconciliation/api',params={'tab':tab}).json()['total']==count
    assert client.get('/crm/sales-pipeline/reconciliation/api?q=does-not-exist').json()['total']==0
    assert client.get('/crm/sales-pipeline/reconciliation/api?page=0').status_code==422
