"""Local loyalty contract tests: no live Shopify, inventory or financial writes."""
import copy,re
from datetime import datetime,timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from models import Customer,SalesOrder,SalesOrderLine,SalesPayment,User,JournalEntry
from retail_models import RetailVariant
from loyalty_models import *
from test_whatsapp_quick_messages import inbox
import loyalty_service as svc
import loyalty_events
import loyalty_reporting as reporting
import sales_order_service as orders

NOW=datetime.utcnow()
@pytest.fixture
def loyalty(inbox,monkeypatch):
    client,sessions,ids,sends,login,csrf=inbox
    import main
    monkeypatch.setattr(main.settings,'SHOPIFY_SHOP','loyalty-test');monkeypatch.setattr(main.settings,'RETAIL_ENABLED',True);monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    with sessions() as db:
        if not svc.config(db):db.add(LoyaltyConfig(id=1,enabled=False,version=1,value=svc.DEFAULTS))
        c=Customer(name='Existing buyer',phone='0771234567');db.add(c);db.flush();cid=c.id
        v=RetailVariant(shop='loyalty-test',product_id='gid://shopify/Product/1',variant_id='gid://shopify/ProductVariant/1',inventory_item_id='gid://shopify/InventoryItem/1',title='School bag',variant_title='Pink',price=Decimal('4500'),currency='LKR',product_status='ACTIVE',stale=False,missing=False,tracked=True,options=[],collections=[]);db.add(v);db.commit();rid=v.id
        admin=db.query(User).filter_by(username='quick-admin').one();uid=admin.id
    product={'id':'gid://shopify/Product/1','variant_id':'gid://shopify/ProductVariant/1','sku':'1001','title':'School bag','variant_title':'Pink','price':'4500','currency':'LKR','image':'https://cdn.shopify.com/test.jpg','url':'https://test.invalid/product'}
    monkeypatch.setattr(main,'resolve_inbox_product',lambda *args:copy.deepcopy(product))
    return SimpleNamespace(client=client,sessions=sessions,cid=cid,rid=rid,uid=uid,csrf=csrf,login=login,sends=sends,product=product)

def line(quantity='1',discount='0'):
    return {'shopify_product_id':'gid://shopify/Product/1','shopify_variant_id':'gid://shopify/ProductVariant/1','product_name':'School bag','variant_name':'Pink','unit_price':'4500','currency':'LKR','quantity':quantity,'discount_amount':discount,'tax_amount':'0'}
def payload(cid=None,**changes):return {'unidentified_reason':'Explicit authorized synthetic unidentified order' if cid is None else None,'customer_id':cid,'customer_name':'Existing buyer','whatsapp_phone':'94771234567','lines':[line()],**changes}
def source(v,paid=True,status='DELIVERED',refund='0',customer=True,key=None):
    with v.sessions() as db:
        o,_=orders.create_draft(db,payload(v.cid if customer else None), 'fixture',key or str(uuid4()))
        o.order_status=status;o.payment_status='PAID' if paid else 'UNPAID';o.amount_paid=4500 if paid else 0;o.shopify_total_refunded=Decimal(refund)
        if paid:db.add(SalesPayment(sales_order_id=o.id,payment_method='COD',amount=4500,currency='LKR',payment_date=NOW.date(),verification_status='VERIFIED'))
        db.commit();return o.id

def enabled(v,value=True):
    with v.sessions() as db:svc.config(db).enabled=value;db.commit()

def offer(v,**changes):
    data={'variant_ids':[v.rid],'method':'PERCENT','value':'10','cost':'2500','cost_reference':'Verified purchase bill A','cost_verified_at':NOW.isoformat(),'variable_cost':'100','starts_at':(NOW-timedelta(days=1)).isoformat(),'ends_at':(NOW+timedelta(days=3)).isoformat(),'enabled':True,**changes}
    with v.sessions() as db:rows=svc.save_rules(db,db.get(User,v.uid),data,True);db.commit();return rows[0]['rule']

def test_first_second_order_and_disabled_global(loyalty):
    v=loyalty;offer(v);enabled(v)
    with v.sessions() as db:
        assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='0.00'
    first=source(v)
    with v.sessions() as db:
        q=svc.quote(db,v.cid,[line()]);assert q['loyalty_savings']=='450.00' and q['lines'][0]['loyalty_unit_price']=='4050.00'
        assert svc.quote(db,v.cid,[line()],exclude_order=first)['loyalty_savings']=='0.00'
    enabled(v,False)
    with v.sessions() as db:assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='0.00' and svc.history(db,v.cid)['count']==1

@pytest.mark.parametrize('state,paid,refund,expected',[('DRAFT',True,'0',0),('AWAITING_PAYMENT',False,'0',0),('DELIVERED',False,'0',0),('CANCELLED',True,'0',0),('RETURNED',True,'0',0),('DELIVERED',True,'4500',0),('DELIVERED',True,'1000',1)])
def test_qualifying_payment_cancellation_refunds(loyalty,state,paid,refund,expected):
    v=loyalty;source(v,paid,state,refund)
    with v.sessions() as db:
        h=svc.history(db,v.cid);assert h['count']==expected
        if expected:assert h['net']=='3500.00'

def test_test_data_cod_and_refund_retains_other_qualifying_order(loyalty):
    v=loyalty;test=source(v,key='test:synthetic');a=source(v);b=source(v)
    with v.sessions() as db:
        assert svc.history(db,v.cid)['count']==2
        db.get(SalesOrder,a).shopify_total_refunded=4500;db.commit()
        assert svc.history(db,v.cid)['count']==1
        m=svc.refresh_member(db,v.cid);assert m.status=='ACTIVE'
        db.get(SalesOrder,b).shopify_total_refunded=4500;db.commit();svc.refresh_member(db,v.cid);assert m.status=='PENDING'

@pytest.mark.parametrize('raw,expected',[('0771234567','+94771234567'),('+94771234567','+94771234567'),('94771234567','+94771234567'),('+442079460123','+442079460123'),('+14165550123','+14165550123'),('not a phone',None)])
def test_phone_normalization(raw,expected):assert svc.phone(raw)==expected

def test_ambiguous_identity_never_merges_and_suspension(loyalty):
    v=loyalty;source(v)
    with v.sessions() as db:
        other=Customer(name='Another distinct person',phone='+94771234567');db.add(other);db.commit()
        assert svc.member_summary(db,v.cid)['status']=='INELIGIBLE'
        m=svc.refresh_member(db,v.cid);assert m.status=='INELIGIBLE'
        user=db.get(User,v.uid);svc.change_member(db,user,v.cid,'VERIFY_IDENTITY','Independent identity checked in person');db.commit()
        assert svc.member_summary(db,v.cid)['status']=='ACTIVE' and db.query(Customer).count()==2
        svc.change_member(db,user,v.cid,'SUSPEND','Membership investigation');db.commit();assert svc.member_summary(db,v.cid)['status']=='SUSPENDED'
        svc.change_member(db,user,v.cid,'REINSTATE','Investigation resolved');db.commit();assert svc.member_summary(db,v.cid)['status']=='ACTIVE'

@pytest.mark.parametrize('method,value,price',[('PERCENT','10','4050.00'),('AMOUNT','450','4050.00'),('PRICE','4050','4050.00')])
def test_three_discount_methods_and_quantity_limit(loyalty,method,value,price):
    v=loyalty;source(v);offer(v,method=method,value=value,max_quantity='2');enabled(v)
    with v.sessions() as db:
        q=svc.quote(db,v.cid,[line('3')]);assert q['lines'][0]['loyalty_unit_price']==price and q['loyalty_savings']=='900.00' and q['merchandise_total']=='12600.00'

@pytest.mark.parametrize('change',[{'cost':None},{'cost':'4000'},{'variable_cost':'2000'},{'method':'PERCENT','value':'101'},{'method':'PRICE','value':'0'},{'method':'AMOUNT','value':'5000'},{'value':'-1'}])
def test_unsafe_rules_cannot_activate(loyalty,change):
    with pytest.raises(HTTPException):offer(loyalty,**change)

def test_exception_audit_overlap_and_price_changes(loyalty):
    v=loyalty;source(v);r=offer(v,cost=None,exception_reason='Management approved unknown-cost clearance');enabled(v)
    with v.sessions() as db:
        q=svc.quote(db,v.cid,[line()]);assert q['loyalty_savings']=='450.00' and q['lines'][0]['profitability']['gross_profit'] is None
        assert db.query(LoyaltyAudit).filter_by(kind='PRICING_RULE_SAVED').count()==1
    with pytest.raises(HTTPException):offer(v)
    with v.sessions() as db:
        db.get(RetailVariant,v.rid).price=5000;db.commit()
        assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='0.00'

@pytest.mark.parametrize('discount,method,saving',[('0','LOYALTY','450.00'),('200','LOYALTY','450.00'),('900','PROMOTION','0.00'),('450','PROMOTION','0.00')])
def test_lower_promotion_without_stacking(loyalty,discount,method,saving):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        q=svc.quote(db,v.cid,[line(discount=discount)]);assert q['lines'][0]['pricing_method']==method and q['loyalty_savings']==saving

def test_boundary_and_bulk_rules(loyalty):
    v=loyalty;source(v);r=offer(v,starts_at=NOW.isoformat(),ends_at=(NOW+timedelta(hours=1)).isoformat());enabled(v)
    with v.sessions() as db:
        assert svc.quote(db,v.cid,[line()],now=NOW)['loyalty_savings']=='450.00'
        assert svc.quote(db,v.cid,[line()],now=NOW+timedelta(hours=1))['loyalty_savings']=='0.00'
        rule=db.get(LoyaltyRule,r['id']);rule.enabled=False;db.commit()
        other=RetailVariant(shop='loyalty-test',product_id='gid://shopify/Product/2',variant_id='gid://shopify/ProductVariant/2',title='Bottle',variant_title='Default',price=4500,currency='LKR',product_status='ACTIVE',missing=False,stale=False,options=[],collections=[]);db.add(other);db.commit()
        data={'variant_ids':[v.rid,other.id],'method':'PRICE','value':'4000','cost':'2000','cost_reference':'bill','cost_verified_at':NOW.isoformat(),'enabled':True}
        assert len(svc.save_rules(db,db.get(User,v.uid),data))==2;db.commit()

def test_crm_create_confirm_freeze_redemption_and_refund(loyalty):
    v=loyalty;source(v);offer(v);enabled(v);headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':'loyalty-crm-checkout'}
    response=v.client.post('/api/sales-orders',json=payload(v.cid),headers=headers)
    assert response.status_code==201,response.text
    order=response.json()['order'];oid=order['id'];assert order['grand_total']=='4050.00' and order['loyalty']['loyalty_savings']=='450.00'
    assert v.client.post('/api/sales-orders',json=payload(v.cid),headers=headers).json()['order']['id']==oid
    for state in ['AWAITING_CUSTOMER_CONFIRMATION','CONFIRMED']:
        result=v.client.post(f'/api/sales-orders/{oid}/transition',json={'status':state},headers=headers);assert result.status_code==200,result.text
    with v.sessions() as db:
        snapshot=copy.deepcopy(db.get(LoyaltyPricing,oid).value);assert db.get(LoyaltyPricing,oid).frozen
        assert db.query(LoyaltyRedemption).count()==1
        order=db.get(SalesOrder,oid);order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=4050;db.add(SalesPayment(sales_order_id=oid,payment_method='CASH',amount=4050,currency='LKR',payment_date=NOW.date(),verification_status='VERIFIED'));db.commit()
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        assert svc.member_summary(db,v.cid)['lifetime_savings']=='450.00'
        order=db.get(SalesOrder,oid);order.shopify_total_refunded=Decimal('2025');db.commit()
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        rows=reporting.redemption_rows(db);assert rows[0]['status']=='PARTIALLY_REVERSED' and rows[0]['net_discount']=='225.00'
        assert db.get(LoyaltyPricing,oid).value==snapshot
        before=db.query(LoyaltyRedemptionEvent).count();svc.sync_redemptions(db,db.get(SalesOrder,oid));db.commit();assert db.query(LoyaltyRedemptionEvent).count()==before
        assert db.query(JournalEntry).count()==0


def test_migration_preview_approval_idempotency_and_source_change(loyalty):
    v=loyalty;source(v)
    with v.sessions() as db:
        user=db.get(User,v.uid);job=svc.preview_migration(db,user,{},'historical-migration-1');db.commit();jid=job.id;approval=job.digest
        assert job.counts['eligible_customers']==1 and not svc.config(db).enabled
        assert svc.preview_migration(db,user,{},'historical-migration-1').id==jid
        with pytest.raises(HTTPException):svc.execute_migration_batch(db,user,jid,approval)
        svc.approve_migration(db,user,jid,approval,'Reviewed local history and identity');db.commit()
        svc.execute_migration_batch(db,user,jid,approval);db.commit()
        assert job.status=='COMPLETE' and db.query(LoyaltyMember).count()==1 and not svc.config(db).enabled
        svc.execute_migration_batch(db,user,jid,approval);db.commit();assert db.query(LoyaltyMember).count()==1


def test_permissions_and_cost_redaction(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    v.login('quick-staff');csrf=re.search(r'data-assignment-csrf="([^"]+)"',v.client.get('/crm/whatsapp').text).group(1)
    assert v.client.get('/crm/loyalty/dashboard').status_code==403
    assert v.client.post('/crm/loyalty/api/rules',json={},headers={'X-CSRF-Token':csrf}).status_code==403
    v.login('quick-agent');csrf=v.client.get('/api/sales-orders/csrf').json()['csrf_token']
    quote=v.client.post('/api/sales-orders/loyalty/quote',json=payload(v.cid),headers={'X-CSRF-Token':csrf});assert quote.status_code==200,quote.text
    assert quote.json()['loyalty_savings']=='450.00' and 'cost' not in quote.json()['lines'][0] and 'gross_profit' not in quote.text
    assert v.client.post('/api/sales-orders/loyalty/quote',json=payload(v.cid)).status_code==403
    assert v.client.get('/crm/loyalty/api/reports/profitability?format=csv').status_code==403


def test_rule_version_history_and_live_profit_revalidation(loyalty):
    v=loyalty;source(v);r=offer(v);enabled(v)
    with v.sessions() as db:
        rule=db.get(LoyaltyRule,r['id']);rule.cost=Decimal('4000');db.commit()
        q=svc.quote(db,v.cid,[line()]);assert q['loyalty_savings']=='0.00' and q['lines'][0]['reason']=='PROFITABILITY_REVIEW_REQUIRED'
    updated=offer(v,id=r['id'],version=1,variant_ids=[v.rid],cost='2000')
    with v.sessions() as db:
        assert updated['version']==2 and db.query(LoyaltyRuleVersion).count()==2
        assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='450.00'


def test_order_promotion_allocation_tax_shipping_and_no_stacking(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        q=svc.quote(db,v.cid,[line(),line()],order_discount='1000')
        assert q['loyalty_savings']=='0.00' and sum(Decimal(l['total_line_discount']) for l in q['lines'])==1000
    response=v.client.post('/api/sales-orders/loyalty/quote',json=payload(v.cid,shipping_total='200',tax_total='50',service_total='25'),headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==200 and response.json()['totals']['grand_total']=='4325.00'


def test_compare_at_public_promotion_requires_unstacked_fixed_price(loyalty):
    v=loyalty;source(v)
    with v.sessions() as db:db.get(RetailVariant,v.rid).compare_at_price=5000;db.commit()
    with pytest.raises(HTTPException):offer(v)
    offer(v,method='PRICE',value='4000');enabled(v)
    with v.sessions() as db:
        assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='500.00'
        q=svc.quote(db,v.cid,[line(discount='700')]);assert q['loyalty_savings']=='0.00' and q['merchandise_total']=='3800.00'


def test_no_other_variants_or_channels_or_stale_prices(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        other={**line(),'shopify_variant_id':'gid://shopify/ProductVariant/999'}
        assert svc.quote(db,v.cid,[other])['loyalty_savings']=='0.00'
        assert svc.quote(db,v.cid,[line()],channel='POS')['loyalty_savings']=='0.00'
        row=db.get(RetailVariant,v.rid);row.stale=True;db.commit();assert svc.quote(db,v.cid,[line()])['loyalty_savings']=='0.00'


def test_confirmation_revalidates_and_draft_customer_change(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':'eligibility-change-before-confirm'}
    response=v.client.post('/api/sales-orders',json=payload(v.cid),headers=headers);oid=response.json()['order']['id']
    v.client.post(f'/api/sales-orders/{oid}/transition',json={'status':'AWAITING_CUSTOMER_CONFIRMATION'},headers=headers)
    enabled(v,False)
    result=v.client.post(f'/api/sales-orders/{oid}/transition',json={'status':'CONFIRMED'},headers=headers)
    assert result.status_code==409
    with v.sessions() as db:assert db.query(LoyaltyRedemption).count()==0 and not db.get(LoyaltyPricing,oid).frozen


def test_migration_missing_identity_never_auto_links_phone(loyalty):
    v=loyalty;oid=source(v,customer=False)
    with v.sessions() as db:
        user=db.get(User,v.uid);job=svc.preview_migration(db,user,{},'migration-missing-customer');db.commit()
        assert job.counts['missing_customer_details']==1 and job.counts['new_proposed']==0 and db.query(LoyaltyMember).count()==0
        review=db.query(LoyaltyIdentityReview).filter_by(order_id=oid).one();assert review.candidates==[v.cid]
        svc.resolve_order_identity(db,user,review.id,v.cid,'Checked original customer and order evidence');db.commit()
        assert db.get(SalesOrder,oid).customer_id is None
        assert svc.member_summary(db,v.cid)['qualifying_count']==1 and db.query(Customer).count()==1
        assert db.query(LoyaltyAudit).filter_by(kind='IDENTITY_RECONCILED').count()==1


def test_migration_changed_source_is_review_and_approval_expires(loyalty):
    v=loyalty;oid=source(v)
    with v.sessions() as db:
        user=db.get(User,v.uid);job=svc.preview_migration(db,user,{},'migration-source-changed');db.commit()
        svc.approve_migration(db,user,job.id,job.digest,'Preview checked by management');db.commit()
        db.get(SalesOrder,oid).shopify_total_refunded=4500;db.commit()
        svc.execute_migration_batch(db,user,job.id,job.digest);db.commit()
        assert db.query(LoyaltyMigrationRow).filter_by(migration_id=job.id).one().status=='REVIEW'
        assert db.query(LoyaltyMember).count()==0 and not svc.config(db).enabled
        job2=svc.preview_migration(db,user,{},'migration-approval-expiry');db.commit();svc.approve_migration(db,user,job2.id,job2.digest,'Reviewed another preview');db.commit()
        job2.approved_at=NOW-timedelta(hours=1);db.commit()
        with pytest.raises(HTTPException):svc.execute_migration_batch(db,user,job2.id,job2.digest)


def test_settings_permissions_and_disabled_default(loyalty):
    v=loyalty
    with v.sessions() as db:assert svc.config(db).enabled is False and svc.config(db).value['minimum_margin']=='20.00'
    response=v.client.post('/crm/loyalty/api/settings',json={'version':1,'enabled':True,'reason':'Management acceptance completed'},headers={'X-CSRF-Token':v.csrf});assert response.status_code==200
    assert v.client.post('/crm/loyalty/api/settings',json={'version':1,'reason':'Stale update'},headers={'X-CSRF-Token':v.csrf}).status_code==409
    with v.sessions() as db:
        user=db.query(User).filter_by(username='quick-staff').one();user.section_access=['loyalty.settings.manage'];db.commit()
    v.login('quick-staff');page=v.client.get('/crm/loyalty/settings');assert page.status_code==200
    token=re.search(r'data-csrf="([^"]+)"',page.text).group(1)
    assert v.client.post('/crm/loyalty/api/settings',json={'version':2,'enabled':False,'reason':'Cannot activate by settings alone'},headers={'X-CSRF-Token':token}).status_code==403


def test_duplicate_customer_membership_and_shopify_identity(loyalty):
    from sqlalchemy.exc import IntegrityError
    v=loyalty;source(v)
    with v.sessions() as db:
        user=db.get(User,v.uid);svc.refresh_member(db,v.cid);db.commit();svc.refresh_member(db,v.cid);db.commit()
        assert db.query(LoyaltyMember).count()==1
        svc.change_member(db,user,v.cid,'LINK_SHOPIFY_CUSTOMER','Verified historical Shopify customer link',shopify_customer_id='gid://shopify/Customer/123');db.commit()
        other=Customer(name='Other customer identity',phone='+14165550123');db.add(other);db.commit()
        with pytest.raises(HTTPException):svc.change_member(db,user,other.id,'LINK_SHOPIFY_CUSTOMER','Attempt duplicate link',shopify_customer_id='gid://shopify/Customer/123')
        db.rollback();db.add(LoyaltyMember(customer_id=v.cid,status='ACTIVE'))
        with pytest.raises(IntegrityError):db.commit()
        db.rollback()


def test_bulk_failure_is_atomic_and_preview_has_no_writes(loyalty):
    v=loyalty;data={'variant_ids':[v.rid,999999],'method':'PRICE','value':'4000','cost':'2000','cost_reference':'Verified bill','cost_verified_at':NOW.isoformat(),'enabled':True}
    response=v.client.post('/crm/loyalty/api/rules',json=data,headers={'X-CSRF-Token':v.csrf});assert response.status_code==422
    with v.sessions() as db:assert db.query(LoyaltyRule).count()==0 and db.query(LoyaltyRuleVersion).count()==0
    data['variant_ids']=[v.rid]
    response=v.client.post('/crm/loyalty/api/rules/preview',json=data,headers={'X-CSRF-Token':v.csrf});assert response.status_code==200
    with v.sessions() as db:assert db.query(LoyaltyRule).count()==0 and db.query(LoyaltyAudit).count()==0


def test_dashboard_and_csv_actual_values_and_export_auth(loyalty):
    from loyalty_routes import csv_response
    v=loyalty;source(v)
    with v.sessions() as db:
        svc.refresh_member(db,v.cid);db.commit();stats=reporting.metrics(db)
        assert stats['total_members']==1 and stats['active_members']==1 and stats['net_loyalty_sales']=='0' and stats['loyalty_orders']==0
    export=v.client.get('/crm/loyalty/api/reports/growth?format=csv');assert export.status_code==200 and 'PP-L-' in export.text
    assert csv_response([{'name':'=DANGEROUS()'}],'test').body.decode().find("'=DANGEROUS()")>=0


def test_immutable_pricing_and_audit_history(loyalty):
    from sqlalchemy.exc import IntegrityError,DBAPIError
    v=loyalty;source(v);r=offer(v)
    with v.sessions() as db:
        version=db.query(LoyaltyRuleVersion).first();version.value={'tampered':True}
        with pytest.raises(DBAPIError):db.commit()
        db.rollback();audit=db.query(LoyaltyAudit).first();db.delete(audit)
        with pytest.raises(DBAPIError):db.commit()
        db.rollback()


def test_quantity_limit_is_per_order_not_per_duplicate_line(loyalty):
    v=loyalty;source(v);offer(v,max_quantity='2');enabled(v)
    with v.sessions() as db:
        quote=svc.quote(db,v.cid,[line('2'),line('2')])
        assert quote['loyalty_savings']=='900.00'
        assert quote['lines'][1]['reason']=='ORDER_QUANTITY_LIMIT_REACHED'


def test_client_cannot_forge_loyalty_or_price_or_cost(loyalty):
    v=loyalty;offer(v);enabled(v)
    forged=payload(v.cid,_loyalty={'loyalty_savings':'4500','membership_status':'ACTIVE'},_loyalty_managed=True,_loyalty_base_lines=[{**line(),'unit_price':'0.01'}])
    forged['lines'][0]['unit_price']='0.01';forged['lines'][0]['cost']='0'
    response=v.client.post('/api/sales-orders',json=forged,headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':'forged-loyalty-first-purchase'})
    assert response.status_code==201,response.text
    assert response.json()['order']['grand_total']=='4500.00' and response.json()['order']['loyalty'] is None
    with v.sessions() as db:assert db.query(LoyaltyPricing).count()==0 and db.query(LoyaltyRedemption).count()==0


def test_management_test_exclusion_does_not_modify_commercial_order(loyalty):
    v=loyalty;oid=source(v)
    response=v.client.post('/crm/loyalty/api/order-exclusions',json={'order_id':oid,'reason':'Verified noncommercial test transaction'},headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==200
    with v.sessions() as db:
        assert svc.history(db,v.cid)['count']==0
        order=db.get(SalesOrder,oid);assert order.order_status=='DELIVERED' and order.amount_paid==4500 and order.grand_total==4500
        assert db.query(JournalEntry).count()==0 and db.query(LoyaltyOrderExclusion).count()==1


def test_readonly_coverage_probe_has_no_customer_or_mutation_fields(loyalty):
    from scripts.validate_loyalty_history_readonly import PROBE,remote
    assert 'mutation' not in PROBE and 'customer' not in PROBE and 'lineItems' not in PROBE
    class Client:
        def graphql(self,q,v):
            assert q==PROBE and v=={}
            return {'shop':{'myshopifyDomain':'loyalty-test.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'}]},'orders':{'nodes':[{'createdAt':'2026-10-01T00:00:00Z'}]}}
    result=remote(Client());assert result['domain_matches'] and result['read_orders'] and not result['read_all_orders'] and not result['complete_historical_coverage_verified']


def test_finalization_checks_saved_totals_and_full_snapshot(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        prepared=svc.prepare_order(db,payload(v.cid,shipping_total='200',tax_total='50'),actor='fixture')
        order,_=orders.create_draft(db,prepared,'fixture','loyalty-total-integrity')
        saved=db.get(LoyaltyPricing,order.id);assert saved.value['totals']['grand_total']=='4300.00'
        order.grand_total=Decimal('1');db.commit()
        with pytest.raises(HTTPException):svc.freeze_order(db,order,[line()],'fixture')
        db.rollback();assert not db.get(LoyaltyPricing,order.id).frozen


def test_existing_crm_confirmation_service_cannot_bypass_freeze(loyalty,monkeypatch):
    import shopify_catalog_service
    v=loyalty;source(v);offer(v);enabled(v)
    monkeypatch.setattr(shopify_catalog_service,'resolve_inbox_product',lambda *args:v.product)
    with v.sessions() as db:
        prepared=svc.prepare_order(db,payload(v.cid),actor='fixture');order,_=orders.create_draft(db,prepared,'fixture','shared-confirmation-service')
        orders.transition_order(db,order,'AWAITING_CUSTOMER_CONFIRMATION','fixture')
        orders.transition_order(db,order,'CONFIRMED','fixture')
        assert db.get(LoyaltyPricing,order.id).frozen and db.query(LoyaltyRedemption).count()==1


def test_legacy_staff_management_does_not_auto_grant_new_loyalty_permissions(loyalty):
    v=loyalty;html=v.client.get('/admin/staff').text
    with v.sessions() as db:staff_id=db.query(User.id).filter_by(username='quick-staff').scalar()
    fragment=html.split(f'data-staff-id="{staff_id}"',1)[1].split('</article>',1)[0]
    choices=re.findall(r'<input[^>]+name="sections"[^>]+value="loyalty\.[^"]+"[^>]*>',fragment)
    assert choices and all('checked' not in c for c in choices)

@pytest.mark.parametrize('tamper',['missing_live_line','product_id','variant_id'])
def test_confirmation_rejects_incomplete_or_changed_product_identity(loyalty,tamper):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        prepared=svc.prepare_order(db,payload(v.cid),actor='fixture')
        order,_=orders.create_draft(db,prepared,'fixture','identity-check-'+tamper)
        verified=[line()]
        if tamper=='missing_live_line':verified=[]
        elif tamper=='product_id':order.lines[0].shopify_product_id='gid://shopify/Product/999'
        else:order.lines[0].shopify_variant_id='gid://shopify/ProductVariant/999'
        db.commit()
        with pytest.raises(HTTPException):svc.freeze_order(db,order,verified,'fixture')
        db.rollback();assert db.query(LoyaltyRedemption).count()==0 and not db.get(LoyaltyPricing,order.id).frozen


def test_refund_reporting_matches_ledger_half_up_rounding(loyalty):
    v=loyalty;source(v);offer(v,method='AMOUNT',value='0.01');enabled(v)
    with v.sessions() as db:
        prepared=svc.prepare_order(db,payload(v.cid,shipping_total='0.01'),actor='fixture');order,_=orders.create_draft(db,prepared,'fixture','rounding-half-cent')
        svc.freeze_order(db,order,[line()],'fixture')
        order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
        db.add(SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=order.grand_total,currency='LKR',payment_date=NOW.date(),verification_status='VERIFIED'))
        db.commit();svc.sync_redemptions(db,order);db.commit()
        # Exactly half a .01 discount rounds up to .01 in the immutable ledger.
        order.shopify_total_refunded=Decimal('2250');db.commit();svc.sync_redemptions(db,order);db.commit()
        row=reporting.redemption_rows(db)[0]
        assert row['status']=='REVERSED' and row['net_discount']=='0.00'


def test_independent_loyalty_view_and_action_grants(loyalty):
    v=loyalty
    permissions={'dashboard':{'dashboard'},'members.view':{'members'},'members.manage':set(),'products.manage':{'products'},'exceptions.approve':set(),'redemptions.view':{'redemptions','reports'},'reports.profit':set(),'reports.export':set(),'migration.preview':{'migration'},'migration.execute':set(),'settings.manage':{'settings'},'programme.enable':set()}
    v.login('quick-staff')
    for grant,pages in permissions.items():
        with v.sessions() as db:
            db.query(User).filter_by(username='quick-staff').one().section_access=['loyalty.'+grant];db.commit()
        for page in ('dashboard','members','products','redemptions','reports','migration','settings'):
            response=v.client.get('/crm/loyalty/'+page)
            assert response.status_code==(200 if page in pages else 403),(grant,page,response.status_code)
    with v.sessions() as db:
        db.query(User).filter_by(username='quick-staff').one().section_access=['loyalty.exceptions.approve','loyalty.migration.execute','loyalty.programme.enable'];db.commit()
    csrf=v.client.get('/api/sales-orders/csrf')
    # Action approval does not itself grant the associated management write path.
    for path in ('rules','settings','migrations'):
        response=v.client.post('/crm/loyalty/api/'+path,json={},headers={'X-CSRF-Token':v.csrf})
        assert response.status_code==403,(path,response.text)


def test_revoked_payment_evidence_keeps_immutable_savings_under_review(loyalty):
    v=loyalty;source(v);offer(v);enabled(v)
    with v.sessions() as db:
        prepared=svc.prepare_order(db,payload(v.cid),actor='fixture');order,_=orders.create_draft(db,prepared,'fixture','evidence-review')
        svc.freeze_order(db,order,[line()],'fixture')
        order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
        payment=SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=order.grand_total,currency='LKR',payment_date=NOW.date(),verification_status='VERIFIED');db.add(payment);db.commit()
        svc.sync_redemptions(db,order);db.commit();before=db.query(LoyaltyRedemptionEvent).count()
        payment.verification_status='REJECTED';db.commit()
        assert reporting.redemption_rows(db)[0]['status']=='NEEDS_REVIEW'
        assert reporting.metrics(db)['net_loyalty_sales']=='0'
        with pytest.raises(HTTPException):svc.sync_redemptions(db,order)
        db.rollback();assert db.query(LoyaltyRedemptionEvent).count()==before
        assert db.query(JournalEntry).count()==0
