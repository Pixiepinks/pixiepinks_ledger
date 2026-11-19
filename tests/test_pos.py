"""Disposable, mocked POS tests. No live Shopify operations or physical tenders."""
import copy
from datetime import datetime,timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv,LOCATION
from models import Account,User,SalesOrder,SalesPayment,JournalLine,Customer
from pos_models import *
from settings import settings
import pos_service as service
import pos_shopify
import pos_money as calc
import pos_receipts


class FakePOS:
    def __init__(self,r):self.r=r;self.orders={};self.calls=[];self.fail_after_create=False;self.fail_read=False;self.compatible=True;self.refunds=[]
    def capabilities(self):return {'checkout_ready':self.compatible,'refund_ready':self.compatible,'compatible':self.compatible,'version':'2026-10'}
    def inspect(self,line):
        stock=self.r.remote.observe(line)
        return {'price':'8500.00','taxable':True,**stock,'observed_at':0}
    def find(self,reference):return [copy.deepcopy(o) for o in self.orders.values() if o['sourceIdentifier']==reference]
    def customer(self,customer_id):return {'id':customer_id}
    def read(self,oid):
        if self.fail_read:raise pos_shopify.POSRemoteError('offline')
        return copy.deepcopy(self.orders.get(oid))
    def create(self,wire):
        self.calls.append(('CREATE',copy.deepcopy(wire)));w=wire['order'];oid='gid://shopify/Order/'+str(len(self.orders)+1);lines=[];ful=[];total=Decimal(0)
        for i,line in enumerate(w['lineItems']):
            lid='gid://shopify/LineItem/'+str(i+1);tax=sum((Decimal(t['priceSet']['shopMoney']['amount']) for t in line.get('taxLines',[])),Decimal(0));total+=Decimal(line['priceSet']['shopMoney']['amount'])*line['quantity']+tax
            lines.append({'id':lid,'quantity':line['quantity'],'refundableQuantity':line['quantity'],'variant':{'id':line['variantId']},'originalUnitPriceSet':line['priceSet'],'customAttributes':[{'key':p['name'],'value':p['value']} for p in line['properties']]});ful.append({'quantity':line['quantity'],'lineItem':{'id':lid}})
        bag=lambda amount:{'shopMoney':{'amount':calc.fmt(amount),'currencyCode':'LKR'}}
        self.orders[oid]={'id':oid,'name':'#POS1','customer':{'id':w['customer']['toAssociate']['id']} if w.get('customer') else None,'sourceIdentifier':w['sourceIdentifier'],'tags':w['tags'],'cancelledAt':None,'displayFinancialStatus':'PAID','displayFulfillmentStatus':'FULFILLED',
            'totalPriceSet':bag(total),'totalTaxSet':bag(sum((Decimal(t['priceSet']['shopMoney']['amount']) for l in w['lineItems'] for t in l.get('taxLines',[])),Decimal(0))),'totalDiscountsSet':bag(Decimal(0)),'totalReceivedSet':bag(total),'totalRefundedSet':bag(Decimal(0)),'lineItems':{'nodes':lines,'pageInfo':{'hasNextPage':False}},
            'transactions':[{'id':'gid://shopify/OrderTransaction/1','kind':'SALE','status':'SUCCESS','gateway':'manual','amountSet':bag(total)}],
            'fulfillments':[{'id':'gid://shopify/Fulfillment/1','status':'SUCCESS','location':{'id':w['fulfillment']['locationId']},'fulfillmentLineItems':{'nodes':ful,'pageInfo':{'hasNextPage':False}}}],
            'refunds':[]}
        if self.fail_after_create:raise TimeoutError('unknown remote outcome')
        return oid
    def refund(self,wire):
        self.calls.append(('REFUND',copy.deepcopy(wire)));w=wire['input'];order=self.orders[w['orderId']];amount=w['transactions'][0]['amount'];fid='gid://shopify/Refund/'+str(len(order['refunds'])+1)
        data={'id':fid,'note':w['note'],'refundLineItems':{'nodes':[{'lineItem':{'id':v['lineItemId']},'quantity':v['quantity'],'restockType':v['restockType'],'location':{'id':v['locationId']} if v.get('locationId') else None} for v in w['refundLineItems']],'pageInfo':{'hasNextPage':False}},'transactions':{'nodes':[{'id':'gid://shopify/OrderTransaction/2','kind':'REFUND','status':'SUCCESS','gateway':'manual','parentTransaction':{'id':w['transactions'][0]['parentId']},'amountSet':{'shopMoney':{'amount':amount,'currencyCode':'LKR'}}}],'pageInfo':{'hasNextPage':False}},'totalRefundedSet':{'shopMoney':{'amount':amount,'currencyCode':'LKR'}}};order['refunds'].append(data)
        old=Decimal(order['totalRefundedSet']['shopMoney']['amount']);order['totalRefundedSet']['shopMoney']['amount']=calc.fmt(old+Decimal(amount));order['displayFinancialStatus']='PARTIALLY_REFUNDED'
        for raw in w['refundLineItems']:
            line=next(l for l in order['lineItems']['nodes'] if l['id']==raw['lineItemId']);line['refundableQuantity']-=raw['quantity']
        return fid


@pytest.fixture
def pos(inv,monkeypatch):
    r=inv;monkeypatch.setattr(settings,'POS_ENABLED',True);monkeypatch.setattr(settings,'POS_CHECKOUT_WRITE_ENABLED',True);monkeypatch.setattr(settings,'POS_REFUND_WRITE_ENABLED',True)
    r.pos=FakePOS(r);monkeypatch.setattr(pos_shopify,'client',r.pos)
    with r.sessions() as db:
        ids={}
        for index,key in enumerate(['sales','discount','tax','cash','card','bank','returns']):
            a=Account(code='POS'+str(index),name=key,type='ASSET' if key in {'cash','card','bank'} else 'INCOME');db.add(a);db.flush();ids[key]=a.id
        user=db.get(User,r.uid);service.configure(db,user,{**copy.deepcopy(service.DEFAULT),'tax_mode':'NONE','accounts':ids,'discount_limits':{'STAFF':'10.00'}})
        register=service.register(db,user,'Magammana West',LOCATION);s=service.open_register(db,user,register.id,'10000.00');r.sid=s.id
    return r


def newcart(r,quantity=1):
    with r.sessions() as db:
        u=db.get(User,r.uid);c=service.new_cart(db,u,r.sid)
        c=service.edit_cart(db,u,c.id,c.version,'add',{'id':r.products[0]['id'],'version':r.products[0]['version']},r.pos)
        if quantity!=1:c=service.edit_cart(db,u,c.id,c.version,'line',{'id':r.products[0]['id'],'quantity':quantity,'discount':service.ZERO},r.pos)
        return c.id,c.version


def cash(amount='8500.00',received='9000.00'):return [{'method':'CASH','amount':amount,'received':received,'reference':'','confirmed':True}]


def checkout(r,plan=None,quantity=1):
    cid,version=newcart(r,quantity)
    with r.sessions() as db:
        sale=service.checkout(db,db.get(User,r.uid),cid,version,uuid4().hex,plan or cash(),r.pos);return sale.id


def test_walkin_completed_authority_cash_change_and_accounting(pos):
    r=pos;rid=checkout(r)
    with r.sessions() as db:
        row=db.get(POSCheckout,rid);assert row.frozen['customer_name']=='Walk-in Customer'
        order=db.get(SalesOrder,row.sales_order_id);assert order.customer_id is None and order.whatsapp_phone==''
        assert row.frozen['tenders'][0]['change']=='500.00'
        assert db.query(POSPosting).count()==0
    assert service.dispatch_one(r.sessions,r.pos) is True
    with r.sessions() as db:
        row=db.get(POSCheckout,rid);order=db.get(SalesOrder,row.sales_order_id)
        assert row.status=='COMPLETE' and order.sales_channel=='POS' and order.shopify_order_id and order.payment_status=='PAID'
        assert db.query(POSPosting).count()==1
        assert sum((l.debit-l.credit for l in db.query(JournalLine)),Decimal(0))==0
        assert service.drawer(db,db.get(POSSession,r.sid))==Decimal('18500.00')
        assert db.query(Customer).count()==0
    assert service.dispatch_one(r.sessions,r.pos) is False and len(r.pos.calls)==1
    assert r.remote.mutations==[]


def test_duplicate_checkout_changed_tender_and_price_injection(pos):
    r=pos;cid,v=newcart(r);key=uuid4().hex
    with r.sessions() as db:
        u=db.get(User,r.uid);a=service.checkout(db,u,cid,v,key,cash(),r.pos)
        assert service.checkout(db,u,cid,v,key,cash(),r.pos).id==a.id
        with pytest.raises(HTTPException):service.checkout(db,u,cid,v,key,cash(received='10000.00'),r.pos)
    with r.sessions() as db:
        assert db.query(POSCheckout).count()==1 and db.query(POSCashMovement).count()==1
        u=db.get(User,r.uid)
        with pytest.raises(HTTPException):service.edit_cart(db,u,cid,v,'line',{'id':r.products[0]['id'],'quantity':1,'discount':service.ZERO,'unit_price':'1'},r.pos)


def test_held_cart_shared_lookup_and_permissions(pos):
    r=pos;cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);c=service.edit_cart(db,u,cid,v,'hold',{},r.pos);assert c.status=='HELD'
        c=service.edit_cart(db,u,cid,c.version,'resume',{},r.pos);assert c.status=='DRAFT'
    assert r.client.get('/crm/pos/api/products?barcode=SHARED').json()['total']==2
    assert r.client.get('/crm/pos/api/products?barcode=1001200').json()['total']==0
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['pos.view'];db.commit()
    assert r.client.get('/crm/pos').status_code==200
    response=r.client.post('/crm/pos/api/carts',json={'session_id':r.sid},headers={'X-CSRF-Token':r.csrf});assert response.status_code==403


def test_uncertain_create_never_retried_and_reconciles_once(pos):
    r=pos;rid=checkout(r);r.pos.fail_after_create=True
    assert service.dispatch_one(r.sessions,r.pos) is True
    assert service.dispatch_one(r.sessions,r.pos) is False and len(r.pos.calls)==1
    with r.sessions() as db:
        row=db.get(POSCheckout,rid);assert row.status=='UNCERTAIN';op=db.query(POSOperation).one()
        with pytest.raises(HTTPException):pos_receipts.generate(db,db.get(User,r.uid),rid)
        service.reconcile(db,db.get(User,r.uid),op.id,'Shopify order source, payment and store fulfillment checked',r.pos)
        assert row.status=='COMPLETE' and db.query(POSPosting).count()==1
    assert len(r.pos.calls)==1


def test_unverified_bank_split_no_order_until_verification_and_finalize(pos):
    r=pos;plan=cash('1000.00','1000.00')+[{'method':'BANK_TRANSFER','amount':'7500.00','received':'7500.00','reference':'BANK-123','confirmed':False}]
    rid=checkout(r,plan)
    assert service.dispatch_one(r.sessions,r.pos) is False and r.pos.calls==[]
    with r.sessions() as db:
        sale=db.get(POSCheckout,rid);assert sale.status=='AWAITING_PAYMENT';p=db.query(SalesPayment).filter_by(payment_method='BANK_TRANSFER').one()
        service.verify_bank(db,db.get(User,r.uid),rid,p.id,uuid4().hex);assert sale.status=='PAYMENT_READY'
        service.finalize(db,db.get(User,r.uid),rid)
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE'


def test_refund_partial_payout_double_refund_and_no_manual_restock(pos):
    r=pos;rid=checkout(r,quantity=2,plan=cash('17000.00','17000.00'));service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        u=db.get(User,r.uid);ref=service.prepare_refund(db,u,rid,r.sid,uuid4().hex,[{'line':0,'quantity':1}],'CASH',True,'Item returned');fid=ref.id
    assert service.dispatch_one(r.sessions,r.pos) is True
    with r.sessions() as db:
        u=db.get(User,r.uid);ref=db.get(POSRefund,fid);assert ref.status=='AWAITING_PAYOUT'
        service.confirm_payout(db,u,fid,'Cash returned to customer');service.confirm_payout(db,u,fid,'Cash returned to customer')
        assert service.drawer(db,db.get(POSSession,r.sid))==Decimal('18500.00')
        assert db.query(POSPosting).count()==2
        with pytest.raises(HTTPException):service.prepare_refund(db,u,rid,r.sid,uuid4().hex,[{'line':0,'quantity':2}],'CASH',True,'over refund')
    assert r.remote.mutations==[] and len([c for c in r.pos.calls if c[0]=='REFUND'])==1


@pytest.mark.parametrize('profile,width',[('58',58),('80',80),('A4',210)])
def test_receipt_dimensions_reprints(pos,profile,width):
    from pypdf import PdfReader
    import io
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        u=db.get(User,r.uid);first=pos_receipts.generate(db,u,rid,profile);second=pos_receipts.generate(db,u,rid,profile)
        assert abs(float(PdfReader(io.BytesIO(first)).pages[0].mediabox.width)-width*72/25.4)<.1
        assert 'DUPLICATE' in PdfReader(io.BytesIO(second)).pages[0].extract_text()


def test_register_opening_exclusivity_closing_difference_and_cash_movement(pos):
    r=pos
    with r.sessions() as db:
        u=db.get(User,r.uid);s=db.get(POSSession,r.sid)
        assert service.open_register(db,u,s.register_id,'10000').id==r.sid
        service.cash_move(db,u,r.sid,'100','PAID_IN',uuid4().hex,'Additional float',service.cfg(db)[0]['accounts']['bank'])
        assert service.drawer(db,s)==Decimal('10100.00')
        service.cash_move(db,u,r.sid,'100','PAID_OUT',uuid4().hex,'Bank deposit',service.cfg(db)[0]['accounts']['bank'])
        service.close_register(db,u,r.sid,'10000.00','End of day counted')
        assert s.status=='CLOSED' and s.active_register_id is None


def test_immutable_snapshots_tenders_and_journals(pos):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    for statement in ["UPDATE pos_checkouts SET frozen='{}'", "UPDATE sales_orders SET grand_total=1 WHERE sales_channel='POS'", "UPDATE sales_order_lines SET unit_price=1", "UPDATE sales_payments SET amount=1 WHERE source='POS'", 'DELETE FROM pos_cash_movements','DELETE FROM pos_audits','UPDATE journal_lines SET debit=0']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(statement));db.commit()


def test_no_arbitrary_card_data_or_excess_tender():
    with pytest.raises(HTTPException):service.tenders([{'method':'CARD_PAYMENT','amount':'1','received':'1','reference':'4111111111111111','confirmed':True}],Decimal('1'))
    with pytest.raises(HTTPException):service.tenders(cash(),Decimal('1'))


def test_discount_cent_allocation_and_tax():
    q=calc.calculate([{'unit_price':'100.00','quantity':3,'discount':{'type':'FIXED','value':'1.00'},'variant_id':'x','taxable':True}],{'type':'FIXED','value':'1.00'},'EXCLUSIVE',Decimal('18'))
    assert q['discount_total']=='2.00' and q['tax_total']=='53.64' and q['grand_total']=='351.64'
    assert sum((Decimal(s['net_unit'])*s['quantity'] for s in q['segments']),Decimal(0))==Decimal('298.00')


def test_customer_consent_existing_binding_and_no_profile_upsert(pos):
    r=pos
    with r.sessions() as db:
        u=db.get(User,r.uid)
        with pytest.raises(HTTPException):service.create_customer(db,u,'Customer','0771234567',False)
        customer=service.create_customer(db,u,'Customer','0771234567',True)
        with pytest.raises(HTTPException):service.create_customer(db,u,'Different name','+94771234567',True)
        service.bind_customer(db,u,customer.id,'gid://shopify/Customer/42',r.pos);customer_id=customer.id
    cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);c=db.get(POSCart,cid);payload={k:c.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']};payload['customer_id']=customer_id
        c=service.edit_cart(db,u,cid,v,'details',payload,r.pos);row=service.checkout(db,u,cid,c.version,uuid4().hex,cash(),r.pos);rid=row.id
    service.dispatch_one(r.sessions,r.pos)
    wire=r.pos.calls[0][1]['order'];assert wire['customer']=={'toAssociate':{'id':'gid://shopify/Customer/42'}}
    assert not any(k in wire for k in ['shippingAddress','billingAddress','email','phone','buyerAcceptsMarketing'])
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(Customer).count()==1


def test_unavailable_stock_rejects_and_config_change_requires_reconfirmation(pos):
    r=pos;cid,v=newcart(r)
    original=r.pos.inspect
    r.pos.inspect=lambda line:{**original(line),'available':None}
    with r.sessions() as db:
        with pytest.raises(HTTPException):service.checkout(db,db.get(User,r.uid),cid,v,uuid4().hex,cash(),r.pos)
        assert db.query(POSCheckout).count()==0
    r.pos.inspect=original
    with r.sessions() as db:
        u=db.get(User,r.uid);policy,_=service.cfg(db);service.configure(db,u,{**policy,'tax_mode':'EXCLUSIVE','tax_rate':'18.00','tax_title':'Configured tax'})
        with pytest.raises(HTTPException):service.checkout(db,u,cid,v,uuid4().hex,cash(),r.pos)
        assert db.get(POSCart,cid).version==v+1


def test_cashier_discount_limits_and_exact_supervisor_approval(pos):
    r=pos;cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);c=service.edit_cart(db,u,cid,v,'line',{'id':r.products[0]['id'],'quantity':1,'discount':{'type':'PERCENT','value':'20.00'}},r.pos)
        c.payload={**c.payload,'discount_reason':'Seasonal discount'};db.commit()
        u.role='STAFF';u.section_access=['pos.view','pos.sell','pos.discount'];db.commit()
        with pytest.raises(HTTPException):service.checkout(db,u,cid,c.version,uuid4().hex,cash('6800.00','7000.00'),r.pos)
        admin=User(username='pos-supervisor',password_hash='not-live',role='ADMIN',is_active=True);db.add(admin);db.commit()
        service.authorize_discount(db,admin,cid,c.version,'Approved exact sale discount')
        row=service.checkout(db,u,cid,c.version,uuid4().hex,cash('6800.00','7000.00'),r.pos);rid=row.id
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE'


def test_refund_unknown_response_recovers_without_resend_and_checks_restock(pos):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        f=service.prepare_refund(db,db.get(User,r.uid),rid,r.sid,uuid4().hex,[{'line':0,'quantity':1}],'CASH',True,'Returned');fid=f.id;opid=f.operation_id
    original=r.pos.refund
    def uncertain(wire):original(wire);raise TimeoutError('lost response')
    r.pos.refund=uncertain;service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        assert db.get(POSOperation,opid).status=='UNCERTAIN'
        service.reconcile(db,db.get(User,r.uid),opid,'Exact refund note, lines, restock and successful manual transaction verified',r.pos)
        assert db.get(POSRefund,fid).status=='AWAITING_PAYOUT'
    assert len(r.pos.calls)==2


def test_source_flags_independent_and_csrf_required(pos,monkeypatch):
    r=pos;monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',False);monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',False)
    rid=checkout(r);monkeypatch.setattr(settings,'POS_CHECKOUT_WRITE_ENABLED',False)
    assert service.dispatch_one(r.sessions,r.pos) is False and not r.pos.calls
    assert r.client.post('/crm/pos/api/carts',json={'session_id':r.sid}).status_code==403
    monkeypatch.setattr(settings,'POS_CHECKOUT_WRITE_ENABLED',True);assert service.dispatch_one(r.sessions,r.pos)
    assert r.remote.mutations==[]


def test_sales_reports_only_pos_and_bounded_csv(pos):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    result=r.client.get('/crm/pos/api/sales?format=summary');assert result.status_code==200,result.text
    assert result.json()['totals']['grand_total']=='8500.00'
    assert result.json()['tenders']['CASH']=='8500.00'
    assert r.client.get('/crm/pos/api/sales?format=csv').text.count('POS-')==1
    assert r.client.get('/crm/pos/api/sales?start=2026-01-01&end=2026-12-31').status_code==422


def test_duplicate_pos_webhook_ignored_and_legacy_workflows_blocked(pos):
    import shopify_order_lifecycle_service as lifecycle
    import sales_order_service as orders
    import shopify_payment_sync_service as sync
    import sales_fulfillment_service as fulfillment
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        row=db.get(POSCheckout,rid);order=db.get(SalesOrder,row.sales_order_id)
        payload={'admin_graphql_api_id':order.shopify_order_id,'name':order.shopify_order_name}
        for _ in range(2):lifecycle.process_orders_create(db,payload,webhook_id='pos-duplicate-webhook',shop_domain=row.shop,raw_body=b'fixture')
        assert db.query(SalesOrder).count()==1 and db.query(POSPosting).count()==1
        with pytest.raises(sync.ShopifyPaymentSyncError):sync.create_preflight(db,order.id,'admin',uuid4().hex)
        with pytest.raises(fulfillment.FulfillmentError):fulfillment.create_full_fulfillment(db,order.id,'admin',uuid4().hex)
        with pytest.raises(orders.SalesOrderValidationError):orders.update_draft(db,order,{},'admin')


def test_unsent_void_returns_tender_without_deleting_history(pos):
    r=pos;rid=checkout(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);service.cancel_checkout(db,u,rid,'Confirmed all accepted cash returned before remote dispatch')
        service.cancel_checkout(db,u,rid,'Confirmed all accepted cash returned before remote dispatch')
        row=db.get(POSCheckout,rid);order=db.get(SalesOrder,row.sales_order_id)
        assert row.status=='CANCELLED' and order.payment_status=='UNPAID' and order.amount_paid==0
        assert db.query(SalesPayment).one().verification_status=='REJECTED'
        assert service.drawer(db,db.get(POSSession,r.sid))==Decimal('10000')
        assert db.query(POSCashMovement).count()==2 and not db.query(POSPosting).count()
    assert service.dispatch_one(r.sessions,r.pos) is False and not r.pos.calls


def test_invoice_preserves_supplied_business_details(pos):
    import io
    from pypdf import PdfReader
    r=pos;cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);c=db.get(POSCart,cid)
        payload={k:c.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']}
        payload['invoice_details']='Acme Ltd / customer invoice reference INV-123'
        c=service.edit_cart(db,u,cid,v,'details',payload,r.pos)
        rid=service.checkout(db,u,cid,c.version,uuid4().hex,cash(),r.pos).id
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        pdf=pos_receipts.generate(db,db.get(User,r.uid),rid,'A4')
    assert 'Acme Ltd / customer invoice reference INV-123' in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
