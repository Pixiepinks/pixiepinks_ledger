import copy,json
from uuid import uuid4
import httpx,pytest
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from models import User,SalesOrder,JournalEntry,SalesPayment
from pos_models import POSCheckout,POSCart,POSRegister,POSSession
from settings import settings
import pos_service as service
from scripts import diagnose_pos_order_readonly as d


def transport(monkeypatch,handler):
    monkeypatch.setattr(settings,'SHOPIFY_SHOP','fixture')
    client=d.ReadOnlyTransport(http_client=httpx.Client(transport=httpx.MockTransport(handler)));client.get_token=lambda:'mock-token'
    return client


def reply(order=None,**extra):return {'data':{'shop':{'myshopifyDomain':'fixture.myshopify.com','currencyCode':'LKR'},'order':order},**extra}


def test_graphql_access_error_and_partial_data_are_sanitized_not_verified(monkeypatch):
    body=reply({'id':d.ORDER_ID,'customer':{'email':'private@example.com'}},errors=[{'message':'Access denied for customer; token=secret private@example.com','path':['order','customer'],'extensions':{'code':'ACCESS_DENIED','fieldName':'customer','typeName':'Order'}}])
    client=transport(monkeypatch,lambda request:httpx.Response(200,json=body,headers={'X-Shopify-API-Version':'2026-10'}))
    result=client.probe('full',d.ORDER_ID)
    assert result.report['status']=='GRAPHQL_ERROR' and result.order is None
    assert result.report['graphql_errors'][0]['path']==['order','customer']
    assert result.report['graphql_errors'][0]['code']=='ACCESS_DENIED'
    assert 'secret' not in json.dumps(result.report) and 'private@example.com' not in repr(result)


def test_high_cost_full_query_can_fail_while_minimal_read_works(monkeypatch):
    calls=[]
    def handler(request):
        query=json.loads(request.content)['query'];calls.append(query)
        body=reply({'id':d.ORDER_ID})
        if query==d.adapter.READ:body=reply(errors=[{'message':'Query cost exceeds maximum','extensions':{'code':'MAX_COST_EXCEEDED'}}],extensions={'cost':{'requestedQueryCost':2000,'actualQueryCost':None,'throttleStatus':{'currentlyAvailable':1000,'restoreRate':50}}})
        return httpx.Response(200,json=body,headers={'X-Shopify-API-Version':'2026-10'})
    client=transport(monkeypatch,handler)
    full=client.probe('full',d.ORDER_ID);small=client.probe('identity',d.ORDER_ID)
    assert full.report['status']=='GRAPHQL_ERROR' and full.report['cost']['requestedQueryCost']==2000
    assert small.report['status']=='READ_SUCCEEDED' and small.report['verification_authorized'] is False
    assert all(q.startswith('query ') for q in calls)


@pytest.mark.parametrize('status,version,body,expected',[
 (403,'2026-10',reply(),'HTTP_ERROR'),(429,'2026-10',reply(),'HTTP_ERROR'),
 (200,'2026-07',reply(),'API_VERSION_MISMATCH'),
 (200,'2026-10',{'data':None},'INVALID_DATA_SHAPE'),
 (200,'2026-10',reply(),'ORDER_NOT_RETURNED'),
 (200,'2026-10',reply({'id':'gid://shopify/Order/999'}),'ORDER_ID_MISMATCH'),
])
def test_transport_failures_distinguished_without_modifying_live_adapter(monkeypatch,status,version,body,expected):
    client=transport(monkeypatch,lambda request:httpx.Response(status,json=body,headers={'X-Shopify-API-Version':version}))
    result=client.probe('full',d.ORDER_ID)
    assert result.report['status']==expected and result.order is None


def test_no_generic_graphql_mutations_or_unbounded_groups(monkeypatch):
    calls=[];client=transport(monkeypatch,lambda request:calls.append(request))
    for method,args in [(client.graphql,('mutation { orderCreate { id } }',{})),(client.create,({},)),(client.refund,({},)),(client.probe,('unreviewed',d.ORDER_ID)),(client.probe,('full','not-an-id'))]:
        with pytest.raises(ValueError):method(*args)
    assert not calls


def seed_history(r,shop,reference):
    with r.sessions() as db:
        base=db.query(POSCheckout).first()
        reg=POSRegister(shop=shop,name='Other historical register',location_id=base.frozen['location']);db.add(reg);db.flush()
        session=POSSession(id=str(uuid4()),shop=shop,register_id=reg.id,cashier_id=r.uid,opening_float=0,status='CLOSED');db.add(session);db.flush()
        order=SalesOrder(created_by=r.uid,sales_channel='POS',internal_order_number=reference,idempotency_key='fixture-'+uuid4().hex,customer_name='Synthetic history',whatsapp_phone='',currency='LKR',grand_total='350',subtotal='350',discount_total='0',tax_total='0',shipping_total='0');db.add(order);db.flush()
        cart=POSCart(id=str(uuid4()),shop=shop,session_id=session.id,actor_id=r.uid,status='CHECKOUT',payload=copy.deepcopy(base.frozen['cart']),quote=copy.deepcopy(base.frozen['quote']));db.add(cart);db.flush()
        row=POSCheckout(id=str(uuid4()),shop=shop,cart_id=cart.id,sales_order_id=order.id,reference=reference,request_key=uuid4().hex,actor_id=r.uid,session_id=session.id,frozen=copy.deepcopy(base.frozen),frozen_hash=base.frozen_hash,status='QUEUED');db.add(row);db.commit();return row.id


def setup(pos,monkeypatch):
    rid=checkout(pos);pos.pos.fail_read=True;service.dispatch_one(pos.sessions,pos.pos);pos.pos.fail_read=False
    with pos.sessions() as db:reference=db.get(POSCheckout,rid).reference
    monkeypatch.setattr(d,'SALE_A',reference);monkeypatch.setattr(d,'SALE_B','POS-'+'B'*20)
    class Client:
        def __init__(self):self.calls=[]
        def probe(self,group,order_id):
            self.calls.append(group)
            return d.ProbeResult({'group':group,'status':'READ_SUCCEEDED'},copy.deepcopy(pos.pos.orders[order_id]))
    return Client()


def test_cross_shop_lookup_is_not_a_missing_or_deleted_sale(pos,monkeypatch):
    client=setup(pos,monkeypatch);bid=seed_history(pos,'different-shop',d.SALE_B)
    result=d.run(pos.sessions,client,'gid://shopify/Order/1',checkout_id=bid)
    assert result['sale_b_lookup']['status']=='FOUND_IN_DIFFERENT_STORED_SHOP_CONTEXT'
    assert result['sale_b_lookup']['exact_matches'][0]['configured_shop_match'] is False
    assert result['browser_checkout_lookup']['checkout_id']==bid
    assert result['reads'][0]['canonical_verification']=='VERIFIED_AGAINST_FROZEN_SALE_A'
    assert result['target_matches_recorded_order'] and not result['write_authorized']
    with pos.sessions() as db:
        assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==1
    assert len(pos.pos.calls)==1 and not pos.remote.mutations


def test_absent_row_does_not_prove_another_shop_or_duplicate_authorization(pos,monkeypatch):
    client=setup(pos,monkeypatch);result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    assert result['sale_b_lookup']['status']=='NOT_FOUND_IN_THIS_DATABASE_SCHEMA'
    assert not result['sale_b_lookup']['exact_matches'] and not result['sale_b_lookup']['normalized_candidates']
    assert len(result['database_context_fingerprint'])==64
    assert not result['partial_groups_authorize_reconciliation']


def test_normalized_reference_candidate_is_reported_without_rewriting(pos,monkeypatch):
    client=setup(pos,monkeypatch);bid=seed_history(pos,service.retail.shop(),' '+d.SALE_B.lower()+' ')
    result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    assert result['sale_b_lookup']['status']=='CASE_OR_WHITESPACE_REFERENCE_CANDIDATE'
    with pos.sessions() as db:assert db.get(POSCheckout,bid).reference==' '+d.SALE_B.lower()+' '


def test_stop_group_expansion_after_throttling(pos,monkeypatch):
    setup(pos,monkeypatch)
    class Throttled:
        def __init__(self):self.calls=[]
        def probe(self,group,order_id):self.calls.append(group);return d.ProbeResult({'group':group,'http_status':429,'status':'HTTP_ERROR'})
    client=Throttled();result=d.run(pos.sessions,client,'gid://shopify/Order/1',('full','identity'))
    assert client.calls==['full'] and result['stopped_after_throttle']


@pytest.mark.parametrize('mode,expected',[('timeout','TRANSPORT_TIMEOUT'),('non_json','NON_JSON_RESPONSE'),('token','TOKEN_OR_CONFIGURATION_UNAVAILABLE')])
def test_operational_failures_never_export_exception_text(monkeypatch,mode,expected):
    def handler(request):
        if mode=='timeout':raise httpx.ReadTimeout('secret private payload',request=request)
        return httpx.Response(200,text='private upstream error',headers={'X-Shopify-API-Version':'2026-10'})
    client=transport(monkeypatch,handler)
    if mode=='token':
        def unavailable():raise RuntimeError('secret token')
        client.get_token=unavailable
    result=client.probe('full',d.ORDER_ID)
    assert result.report['status']==expected and result.order is None
    assert 'secret' not in json.dumps(result.report) and 'private' not in json.dumps(result.report)


def test_commercial_order_visible_without_a_checkout_is_not_safe_to_recreate(pos,monkeypatch):
    client=setup(pos,monkeypatch)
    with pos.sessions() as db:
        order=SalesOrder(created_by=pos.uid,sales_channel='POS',internal_order_number=d.SALE_B,
            idempotency_key='orphan-fixture-'+uuid4().hex,customer_name='Synthetic',whatsapp_phone='',
            currency='LKR',grand_total=350,subtotal=350,order_status='PAYMENT_VERIFICATION',payment_status='PAID')
        db.add(order);db.commit();oid=order.id
    result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    b=result['sale_b_lookup']
    assert b['status']=='NOT_FOUND_IN_THIS_DATABASE_SCHEMA'
    assert b['crm_sales_orders']['exact_match_count']==1
    row=b['crm_sales_orders']['records'][0]
    assert row['sales_order_id']==oid and row['payment_status']=='PAID'
    assert row['linked_checkout_count']==0 and not result['write_authorized']
    assert len(pos.pos.calls)==1


def test_commercial_order_link_traces_different_checkout_reference(pos,monkeypatch):
    client=setup(pos,monkeypatch)
    with pos.sessions() as db:
        row=db.query(POSCheckout).first();order=db.get(SalesOrder,row.sales_order_id)
        order.internal_order_number=d.SALE_B;db.commit()
    result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    b=result['sale_b_lookup']
    assert b['status']=='NOT_FOUND_IN_THIS_DATABASE_SCHEMA'
    linked=b['crm_sales_orders']['records'][0]['linked_checkouts']
    assert len(linked)==1 and linked[0]['reference']==d.SALE_A
    assert linked[0]['configured_shop_match'] and len(pos.pos.calls)==1


def test_paid_crm_stays_unlinked_until_full_pos_verification(pos,monkeypatch):
    client=setup(pos,monkeypatch)
    result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    commercial=result['sale_a_lookup']['crm_sales_orders']['records'][0]
    assert commercial['payment_status']=='PAID'
    assert commercial['order_status']=='PAYMENT_VERIFICATION'
    assert commercial['fulfillment_status']=='UNFULFILLED'
    assert commercial['shopify_order_id'] is None
    assert commercial['linked_checkout_count']==1
    assert commercial['linked_checkouts'][0]['status']=='REVIEW'
    assert result['reads'][0]['canonical_verification']=='VERIFIED_AGAINST_FROZEN_SALE_A'
    # Even a successful diagnostic does not publish or take another payment.
    with pos.sessions() as db:
        assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==1
    assert len(pos.pos.calls)==1


@pytest.mark.parametrize('change,expected',[
    ('location','FULFILLMENT_STATUS_OR_LOCATION_MISMATCH'),
    ('quantity','FULFILLMENT_QUANTITY_MISMATCH'),
    ('status','FULFILLMENT_STATUS_OR_LOCATION_MISMATCH'),
])
def test_fulfilled_badge_does_not_replace_frozen_fulfillment_checks(pos,monkeypatch,change,expected):
    client=setup(pos,monkeypatch);order=pos.pos.orders['gid://shopify/Order/1']
    order['displayFulfillmentStatus']='FULFILLED'
    fulfillment=order['fulfillments'][0]
    if change=='location':fulfillment['location']['id']='gid://shopify/Location/999'
    elif change=='quantity':fulfillment['fulfillmentLineItems']['nodes'][0]['quantity']+=1
    else:fulfillment['status']='FAILURE'
    result=d.run(pos.sessions,client,'gid://shopify/Order/1')
    assert result['reads'][0]['canonical_verification']=='NOT_VERIFIED'
    assert result['reads'][0]['failure_code']==expected
    assert not result['write_authorized'] and len(pos.pos.calls)==1
    with pos.sessions() as db:assert db.query(JournalEntry).count()==0
