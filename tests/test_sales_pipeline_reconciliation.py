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

@pytest.mark.parametrize('account',['quick-agent','quick-manager','quick-staff'])
@pytest.mark.parametrize('path',['/crm/sales-pipeline/reconciliation','/crm/sales-pipeline/reconciliation/api','/crm/sales-pipeline/reconciliation/api/1'])
def test_nonadmin_cannot_read(candidate,path,account):
    candidate[0].post('/login',data={'username':account,'password':'password'})
    assert candidate[0].get(path).status_code==403

@pytest.mark.parametrize('account',['quick-agent','quick-manager','quick-staff'])
@pytest.mark.parametrize('action',['preview','associate','investigate'])
def test_nonadmin_cannot_write(candidate,action,account):
    candidate[0].post('/login',data={'username':account,'password':'password'})
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

def add_candidate(db,uid,index,status='DELIVERED'):
    c=C(phone_number='947701'+str(index).zfill(5),customer_name='Synthetic Review '+str(index),assigned_to_user_id=uid);db.add(c);db.flush()
    l=Lead(lead_no='R-'+str(uuid4()),customer_name=c.customer_name,mobile=c.phone_number,status=status,whatsapp_conversation_id=c.id);db.add(l);db.flush()
    o=O(conversation_id=c.id,active_conversation_id=c.id,lead_id=l.id,data={'stage':'CONFIRMED','lead_status':status},version=1);db.add(o)
    order=SalesOrder(internal_order_number='REVIEW-'+uuid4().hex,idempotency_key=str(uuid4()),crm_conversation_id=c.id,crm_lead_id=l.id,customer_name=c.customer_name,whatsapp_phone=c.phone_number,created_by='test',order_status='DELIVERED',payment_status='PAID',shopify_sync_status='SYNCED',shopify_lifecycle_status='ORDER_LINKED',grand_total=350,currency='LKR');db.add(order);db.flush()
    return o.id,order.id

def test_bulk_success_and_atomic_conflict(candidate):
    v=candidate;client,sessions,ids,csrf,oid=v
    with sessions() as db:
        uid=db.query(User).filter_by(role='ADMIN').one().id;other,orderid=add_candidate(db,uid,99);db.commit()
    p=payload(v);d=client.get('/crm/sales-pipeline/reconciliation/api/'+str(other)).json();p['items'].append({'opportunity_id':other,'order_id':orderid,'version':d['version'],'digest':d['digest'],'reviewed':True})
    with sessions() as db:db.get(SalesOrder,orderid).grand_total=400;db.commit()
    assert post(v,p).status_code==409
    with sessions() as db:assert db.get(O,oid).order_id is None and db.get(O,other).order_id is None
    d=client.get('/crm/sales-pipeline/reconciliation/api/'+str(other)).json();p['items'][1].update(version=d['version'],digest=d['digest'])
    assert post(v,p).status_code==200
    assert client.get('/crm/sales-pipeline/reconciliation/api?tab=history').json()['total']==2


def test_admin_investigation_metadata_hidden_from_staff(candidate):
    v=candidate;d=detail(v)
    assert post(v,{'opportunity_id':v[4],'version':d['version'],'state':'Needs Investigation','note':'ADMIN PRIVATE SYNTHETIC NOTE'},action='investigate').status_code==200
    with v[1]() as db:
        o=db.get(O,v[4]);c=db.get(C,o.conversation_id);c.assigned_to_user_id=db.query(User).filter_by(username='quick-agent').one().id;db.commit()
    v[0].post('/login',data={'username':'quick-agent','password':'password'})
    assert 'ADMIN PRIVATE' not in v[0].get('/crm/sales-pipeline/api').text
    assert 'ADMIN PRIVATE' not in v[0].get('/crm/sales-pipeline/api/'+str(v[4])).text


def test_no_unrestricted_correction(candidate):
    p=payload(candidate);assert post(candidate,p).status_code==200
    with candidate[1]() as db:o=db.get(O,candidate[4]);version=o.version
    response=candidate[0].post('/crm/sales-pipeline/api/'+str(candidate[4]),json={'version':version,'stage':'INTERESTED','order_id':None},headers={'X-CSRF-Token':candidate[3],'Idempotency-Key':str(uuid4())})
    assert response.status_code==409


@pytest.mark.parametrize('same_key',[True,False])
def test_postgres_concurrency_audit_and_readonly(pg,monkeypatch,same_key):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import IntegrityError,DBAPIError
    engine,sessions,uid=pg;monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',True)
    # Install the actual deployed append-only audit triggers on this isolated schema.
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=next(Path('alembic/versions').glob('20261126_61*'));spec=importlib.util.spec_from_file_location('pipeline61',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)));migration.upgrade()
    with sessions() as db:
        db.info['pipeline_sync']=True
        oid,orderid=add_candidate(db,uid,500);db.commit();user=db.get(User,uid);d=r.evidence(db,user,oid)
        p={'items':[{'opportunity_id':oid,'order_id':orderid,'version':d['version'],'digest':d['digest'],'reviewed':True}],'confirmed':True,'note':'PostgreSQL concurrent review'}
    key=str(uuid4())
    def run(_):
        with sessions() as db:
            try:result=r.associate(db,db.get(User,uid),p,key if same_key or _==0 else key+'other');db.commit();return result
            except HTTPException as exc:db.rollback();return exc.status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
    assert sum(isinstance(x,dict) for x in results)==(2 if same_key else 1),results
    assert sum(x.get('idempotent',False) for x in results if isinstance(x,dict))==(1 if same_key else 0)
    if not same_key:assert 409 in results
    with sessions() as db:
        assert sum(e.current.get('action')=='ASSOCIATED' for e in db.query(E))==1
        eid=next(e.id for e in db.query(E) if e.current.get('action')=='BATCH_COMMITTED')
        with pytest.raises(DBAPIError):db.execute(text('UPDATE sales_pipeline_events SET actor_id=NULL WHERE id=:id'),{'id':eid});db.commit()
        db.rollback()
        db.execute(text('SET TRANSACTION READ ONLY'));before=r.readiness(db);assert before['unique_order_association'] and before['immutable_audit_ready']
        assert db.execute(text('SHOW transaction_read_only')).scalar()=='on';db.rollback()
        # Unique constraint rejects a second intent reusing the order, even outside the API.
        other=O(conversation_id=db.get(O,oid).conversation_id,lead_id=db.get(O,oid).lead_id,order_id=orderid,data={'order_id':orderid})
        db.add(other)
        with pytest.raises(IntegrityError):db.flush()
        db.rollback()

def test_write_allowlist_no_messages_payments_or_loyalty(candidate,inbox):
    from sqlalchemy import event
    client,sessions,*_=candidate;p=payload(candidate);writes=[]
    engine=sessions.kw['bind']
    def capture(conn,cursor,statement,parameters,context,many):
        if statement.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE'}:writes.append(statement.lower())
    event.listen(engine,'before_cursor_execute',capture)
    try:assert post(candidate,p).status_code==200
    finally:event.remove(engine,'before_cursor_execute',capture)
    allowed={'sales_opportunities','sales_pipeline_events','leads','sales_pipeline_reminders'}
    for statement in writes:
        parts=statement.split();table=parts[2] if parts[0] in {'insert','delete'} else parts[1]
        assert table.strip('"') in allowed,statement
    assert inbox[3]==[]


def test_history_survives_new_intent_and_staff_cannot_use_legacy(candidate):
    p=payload(candidate)
    assert candidate[0].post('/crm/sales-pipeline/api/'+str(candidate[4])+'/associate-order',json={'order_id':p['items'][0]['order_id'],'version':p['items'][0]['version']},headers={'X-CSRF-Token':candidate[3]}).status_code==409
    assert post(candidate,p).status_code==200
    with candidate[1]() as db:
        o=db.get(O,candidate[4]);o.active_conversation_id=None;db.commit()
    result=candidate[0].get('/crm/sales-pipeline/reconciliation/api?tab=history').json();assert result['total']==1
    assert not result['rows'][0]['eligible']


@pytest.mark.parametrize('field,value',[('order_id',True),('opportunity_id',-1),('version','1'),('reviewed',False),('digest',None)])
def test_manipulated_bulk_fields(candidate,field,value):
    p=payload(candidate);p['items'][0][field]=value
    assert post(candidate,p).status_code==422

def test_pipeline_gate_and_no_message_queries(candidate,monkeypatch):
    from sqlalchemy import event
    statements=[];engine=candidate[1].kw['bind']
    def capture(conn,cursor,statement,parameters,context,many):statements.append(statement.lower())
    event.listen(engine,'before_cursor_execute',capture)
    try:
        response=candidate[0].get('/crm/sales-pipeline/reconciliation/api?tab=all');assert response.status_code==200
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert not any('whatsapp_conversation_messages' in x for x in statements)
    monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',False)
    assert candidate[0].get('/crm/sales-pipeline/reconciliation').status_code==404
    assert candidate[0].get('/crm/sales-pipeline/reconciliation/api').status_code==404


def test_unauthed_cannot_access_candidate_data(candidate):
    from fastapi.testclient import TestClient
    import main
    client=TestClient(main.app,base_url='https://testserver')
    response=client.get('/crm/sales-pipeline/reconciliation/api',follow_redirects=False)
    assert response.status_code in (302,303,401,403)
    assert 'SYNTHETIC-REVIEW' not in response.text
