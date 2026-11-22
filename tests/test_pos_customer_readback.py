"""Protected customer selection must not block unbound financial verification."""
import copy,json
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout,newcart,cash
from models import User,JournalEntry
from pos_models import POSCheckout,POSOperation,POSPosting
from settings import settings
import pos_shopify as adapter
import pos_service as service
from scripts.inspect_pos_sales_readonly import ReadOnlyClient
from scripts import diagnose_pos_order_readonly as diagnostic


def protected_client(monkeypatch,order,association=None,denied=True):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','fixture')
    client=adapter.POSShopify();client.get_token=lambda:'mock-never-live';calls=[]
    def handler(request):
        body=json.loads(request.content);q=body['query'];calls.append(q)
        shop={'myshopifyDomain':'fixture.myshopify.com','currencyCode':'LKR'}
        if 'customer {' in q:
            if denied:
                return httpx.Response(200,json={'data':{'shop':shop,'order':{'id':order['id'],'customer':None}},'errors':[{'message':'private content must not be exported','path':['order','customer'],'extensions':{'code':'ACCESS_DENIED'}}]},headers={'X-Shopify-API-Version':adapter.VERSION})
            value=association
        else:value=copy.deepcopy(order);value.pop('customer',None)
        return httpx.Response(200,json={'data':{'shop':shop,'order':value}},headers={'X-Shopify-API-Version':adapter.VERSION})
    client.http=httpx.Client(transport=httpx.MockTransport(handler))
    return client,calls


def frozen_order(r):
    rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:record=db.get(POSCheckout,rid);return SimpleNamespace(reference=record.reference,frozen=copy.deepcopy(record.frozen)),copy.deepcopy(r.pos.orders['gid://shopify/Order/1'])


def test_walkin_required_financial_read_succeeds_when_customer_access_denied(pos,monkeypatch):
    record,order=frozen_order(pos);client,calls=protected_client(monkeypatch,order)
    actual=service.read_order_for_verification(client,record,order['id'])
    assert 'customer' not in actual and service.verify_order(record,actual)==actual
    assert calls==[adapter.READ] and 'customer {' not in adapter.READ
    with pytest.raises(adapter.POSRemoteError):client.read_customer(order['id'])
    assert calls[-1]==adapter.ORDER_CUSTOMER


@pytest.mark.parametrize('state',['DENIED','MISSING','NULL','MISMATCH','WRONG_ORDER','MATCH'])
def test_bound_customer_requires_actual_order_association(pos,monkeypatch,state):
    record,order=frozen_order(pos);record.frozen['customer_shopify_id']='gid://shopify/Customer/42'
    assoc={'id':order['id'],'customer':{'id':'gid://shopify/Customer/42'}}
    if state=='MISSING':assoc.pop('customer')
    if state=='NULL':assoc['customer']=None
    if state=='MISMATCH':assoc['customer']={'id':'gid://shopify/Customer/99'}
    if state=='WRONG_ORDER':assoc['id']='gid://shopify/Order/999'
    client,calls=protected_client(monkeypatch,order,assoc,denied=state=='DENIED')
    if state=='MATCH':
        actual=service.read_order_for_verification(client,record,order['id']);service.verify_order(record,actual)
    else:
        with pytest.raises(adapter.POSRemoteError):
            actual=service.read_order_for_verification(client,record,order['id']);service.verify_order(record,actual)
    assert calls==[adapter.READ,adapter.ORDER_CUSTOMER]
    raw=copy.deepcopy(order);raw.pop('customer',None)
    with pytest.raises(adapter.POSRemoteError):service.verify_order(record,raw)


def test_required_financial_fields_not_relaxed(pos,monkeypatch):
    record,order=frozen_order(pos);order.pop('customer',None)
    for mutate in [lambda v:v.update(sourceIdentifier='different'),lambda v:v.update(displayFinancialStatus='PENDING'),lambda v:v['fulfillments'][0]['location'].update(id='gid://shopify/Location/999'),lambda v:v['lineItems']['nodes'][0].update(quantity=999),lambda v:v['totalTaxSet']['shopMoney'].update(amount='999')]:
        wrong=copy.deepcopy(order);mutate(wrong)
        with pytest.raises(adapter.POSRemoteError):service.verify_order(record,wrong)
    explicit=copy.deepcopy(order);explicit['customer']={'id':'gid://shopify/Customer/1'}
    with pytest.raises(adapter.POSRemoteError):service.verify_order(record,explicit)


def test_no_resend_reconciliation_uses_customer_free_finance_and_exact_id(pos,monkeypatch):
    r=pos;rid=checkout(r);r.pos.fail_read=True;service.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    original=r.pos.read
    def without_customer(oid):
        value=original(oid);value.pop('customer',None);return value
    r.pos.read=without_customer
    def forbidden(*a):raise AssertionError('No protected customer query for walk-in')
    r.pos.read_customer=forbidden
    with r.sessions() as db:
        op=db.query(POSOperation).one();u=db.get(User,r.uid)
        service.reconcile(db,u,op.id,'Explicit mocked existing order verification',r.pos)
        assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==1
        with pytest.raises(HTTPException):service.reconcile(db,u,op.id,'Never repeat completed accounting',r.pos)
    assert not service.dispatch_one(r.sessions,r.pos) and len(r.pos.calls)==1


def test_diagnostic_customer_optional_denial_does_not_invalidate_finance(pos,monkeypatch):
    r=pos;rid=checkout(r);r.pos.fail_read=True;service.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    with r.sessions() as db:record=db.get(POSCheckout,rid);ref=record.reference
    monkeypatch.setattr(diagnostic,'SALE_A',ref)
    order=copy.deepcopy(r.pos.orders['gid://shopify/Order/1']);order.pop('customer',None)
    class Probe:
        def probe(self,group,oid):
            return SimpleNamespace(order=order if group=='full' else None,report={'group':group,'status':'READ_SUCCEEDED' if group=='full' else 'GRAPHQL_ERROR',**({'graphql_errors':[{'code':'ACCESS_DENIED','path':['order','customer']}]} if group=='customer' else {})})
    report=diagnostic.run(r.sessions,Probe(),'gid://shopify/Order/1',('full','customer'))
    assert report['reads'][0]['canonical_verification']=='VERIFIED_AGAINST_FROZEN_SALE_A'
    assert report['reads'][1]['verification_role']=='OPTIONAL_PROTECTED_CUSTOMER'
    assert report['customer_association_required'] is False and report['financial_query_includes_customer'] is False
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='REVIEW' and db.query(JournalEntry).count()==0
    assert len(r.pos.calls)==1


def bound_checkout(r):
    with r.sessions() as db:
        u=db.get(User,r.uid);customer=service.create_customer(db,u,'Fixture linked customer','0771234567',True)
        service.bind_customer(db,u,customer.id,'gid://shopify/Customer/42',r.pos);customer_id=customer.id
    cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);c=service.get_cart(db,u,cid)
        details={k:c.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']};details['customer_id']=customer_id
        c=service.edit_cart(db,u,cid,v,'details',details)
        return service.checkout(db,u,cid,c.version,uuid4().hex,cash(),r.pos).id


def test_bound_checkout_denial_retains_review_and_requires_real_association(pos,monkeypatch):
    from pos_models import POSAudit
    r=pos;rid=bound_checkout(r);original=r.pos.read
    def without(oid):
        order=original(oid);order.pop('customer',None);return order
    def denied(oid):raise adapter.POSRemoteError('protected fields denied')
    monkeypatch.setattr(r.pos,'read',without);monkeypatch.setattr(r.pos,'read_customer',denied,raising=False)
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='REVIEW' and db.query(POSPosting).count()==0
        audit=db.query(POSAudit).filter_by(action='READBACK_REVIEW').order_by(POSAudit.id.desc()).first()
        assert audit.detail['failure_code']=='CUSTOMER_ASSOCIATION_UNAVAILABLE'
        op=db.query(POSOperation).one()
        with pytest.raises(HTTPException):service.reconcile(db,db.get(User,r.uid),op.id,'Denied protected association cannot be skipped',r.pos)
    monkeypatch.setattr(r.pos,'read_customer',lambda oid:{'id':oid,'customer':{'id':'gid://shopify/Customer/42'}})
    with r.sessions() as db:
        service.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Verified actual linked association',r.pos)
        assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==1
    assert len(r.pos.calls)==1


def test_diagnostic_bound_customer_access_is_required_and_readonly(pos,monkeypatch):
    r=pos;rid=bound_checkout(r);r.pos.fail_read=True;service.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    with r.sessions() as db:ref=db.get(POSCheckout,rid).reference
    monkeypatch.setattr(diagnostic,'SALE_A',ref)
    order=copy.deepcopy(r.pos.orders['gid://shopify/Order/1']);order.pop('customer',None);calls=[]
    class Probe:
        def probe(self,group,oid):
            calls.append(group)
            return SimpleNamespace(order=order if group=='full' else None,report={'group':group,'status':'READ_SUCCEEDED' if group=='full' else 'GRAPHQL_ERROR',**({'graphql_errors':[{'code':'ACCESS_DENIED','path':['order','customer']}]} if group=='customer' else {})})
    out=diagnostic.run(r.sessions,Probe(),'gid://shopify/Order/1',('full','customer'))
    assert out['customer_association_required'] is True
    assert out['reads'][0]['canonical_verification']=='NOT_VERIFIED'
    assert out['reads'][0]['failure_code']=='CUSTOMER_ASSOCIATION_UNAVAILABLE'
    assert out['reads'][1]['verification_role']=='REQUIRED_ASSOCIATION'
    assert calls==['full','customer']
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='REVIEW' and db.query(POSPosting).count()==0


def test_readonly_client_allows_separate_association_but_no_mutation(pos,monkeypatch):
    record,order=frozen_order(pos);client,calls=protected_client(monkeypatch,order)
    readonly=ReadOnlyClient();readonly.http=client.http;readonly.get_token=client.get_token
    with pytest.raises(adapter.POSRemoteError):readonly.read_customer(order['id'])
    assert calls==[adapter.ORDER_CUSTOMER]
    with pytest.raises(adapter.POSRemoteError):readonly.create({})
    assert calls==[adapter.ORDER_CUSTOMER]
