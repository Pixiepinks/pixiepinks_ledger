"""Shared operational registration; no production IDs or customer data."""
from datetime import datetime
from uuid import uuid4
from decimal import Decimal
import pytest
from fastapi import HTTPException
from models import Customer,User,SalesOrder,SalesPayment,JournalEntry,CustomerRegistration
from loyalty_models import LoyaltyMember,LoyaltyOrderIdentity
from test_loyalty import loyalty,inbox,source,payload
import customer_registration as registry
import loyalty_service as svc
import loyalty_events


def register(v,name='New buyer',phone='0779876543',source='CRM',key=None):
    with v.sessions() as db:
        c,created=registry.register(db,db.get(User,v.uid),name,phone,source,key or str(uuid4()));db.commit();return c.id,created

@pytest.mark.parametrize('raw,expected',[('0771234567','+94771234567'),('94771234567','+94771234567'),('+94771234567','+94771234567'),('+96550012345','+96550012345'),('+971501234567','+971501234567'),('442079460123','+442079460123')])
def test_normalized_shared_identity(raw,expected):assert registry.mobile(raw)==expected
@pytest.mark.parametrize('raw',['bad','771234567','1234','2025550123',None])
def test_invalid_or_ambiguous_number(raw):
    with pytest.raises(HTTPException):registry.mobile(raw)


def test_registration_idempotency_no_marketing_or_inquiry_membership(loyalty):
    v=loyalty;key=str(uuid4());cid,created=register(v,key=key);same,again=register(v,phone='+94779876543',key=key)
    assert cid==same and created and not again
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        c=db.get(Customer,cid);assert c.registration_source=='CRM' and c.normalized_mobile=='+94779876543'
        assert db.query(CustomerRegistration).count()==1 and db.query(LoyaltyMember).count()==0 and not svc.config(db).enabled
        from models import MarketingConsent
        assert not db.query(MarketingConsent).filter_by(customer_id=cid,status='OPTED_IN').first()
        with pytest.raises(HTTPException):registry.register(db,db.get(User,v.uid),'Conflicting name','0779876543','CRM',str(uuid4()))
        db.rollback();assert db.query(Customer).count()==2
    assert not v.sends


def test_existing_shared_phone_is_never_first_match_selection(loyalty):
    v=loyalty
    with v.sessions() as db:
        db.add(Customer(name='Another distinct person',phone='+94771234567'));db.commit()
        result=registry.search(db,'0771234567');assert result['identity_conflict'] and len(result['rows'])==2 and result['suggestions_only']
        assert all(r['membership_status']=='IDENTITY_REVIEW' for r in result['rows'])
        with pytest.raises(HTTPException):registry.register(db,db.get(User,v.uid),'Third identity','0771234567','CRM',str(uuid4()))
        db.rollback();assert db.query(Customer).count()==2


def test_crm_requires_link_or_documented_reason_and_revalidates(loyalty):
    v=loyalty;h={'X-CSRF-Token':v.csrf,'Idempotency-Key':str(uuid4())}
    p=payload();p.pop('unidentified_reason')
    assert v.client.post('/api/sales-orders',json=p,headers=h).status_code==422
    p['unidentified_reason']='Authorized existing manual business order without profile'
    r=v.client.post('/api/sales-orders',json=p,headers=h);assert r.status_code==201,r.text
    assert r.json()['order']['customer_id'] is None and r.json()['order']['unidentified_reason']
    p=payload(v.cid);p['whatsapp_phone']='96550012345';h['Idempotency-Key']=str(uuid4())
    assert v.client.post('/api/sales-orders',json=p,headers=h).status_code==409
    p=payload(v.cid);h['Idempotency-Key']=str(uuid4());r=v.client.post('/api/sales-orders',json=p,headers=h);assert r.status_code==201,r.text
    oid=r.json()['order']['id']
    assert v.client.post('/api/sales-orders/'+str(oid)+'/transition',json={'status':'AWAITING_CUSTOMER_CONFIRMATION'},headers=h).status_code==200
    with v.sessions() as db:db.get(Customer,v.cid).phone='+96550012345';db.commit()
    assert v.client.post('/api/sales-orders/'+str(oid)+'/transition',json={'status':'CONFIRMED'},headers=h).status_code==409


def test_first_completed_purchase_creates_one_member_while_discounts_off(loyalty):
    v=loyalty;oid=source(v,paid=False,status='DRAFT')
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:assert db.query(LoyaltyMember).count()==0 and not svc.config(db).enabled
    with v.sessions() as db:
        o=db.get(SalesOrder,oid);o.order_status='DELIVERED';o.payment_status='PAID';o.amount_paid=o.grand_total
        db.add(SalesPayment(sales_order_id=oid,payment_method='CASH',amount=o.grand_total,currency='LKR',payment_date=datetime.utcnow().date(),verification_status='VERIFIED'));db.commit()
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        m=db.query(LoyaltyMember).one();original=m.membership_id;assert m.customer_id==v.cid and m.status=='ACTIVE' and m.source=='CRM_COMPLETION'
        assert svc.member_summary(db,v.cid,exclude_order=oid)['qualifying_count']==0
        assert registry.search(db,'0771234567')['rows'][0]['membership_id']==original
        o=db.get(SalesOrder,oid);o.shopify_total_refunded=o.grand_total;db.commit()
    while loyalty_events.process_one(v.sessions):pass
    with v.sessions() as db:
        m=db.query(LoyaltyMember).one();assert m.membership_id==original and m.status=='PENDING' and not svc.config(db).enabled
        assert db.query(JournalEntry).count()==0


def test_registration_permissions_csrf_and_actor_bound_keys(loyalty):
    v=loyalty;data={'name':'Operational contact','phone':'+96550012345','channel':'CRM'};key=str(uuid4())
    assert v.client.post('/api/customer-registration/register',json=data).status_code==403
    r=v.client.post('/api/customer-registration/register',json=data,headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':key});assert r.status_code==200,r.text
    assert not r.json()['marketing_consent_granted']
    assert v.client.get('/api/customer-registration/search?q=96550012345&channel=CRM').json()['rows'][0]['id']==r.json()['customer']['id']
    v.login('quick-staff')
    assert v.client.get('/api/customer-registration/search?q=077&channel=CRM').status_code==403
    with v.sessions() as db:
        assert db.query(LoyaltyMember).count()==0 and db.query(JournalEntry).count()==0 and db.query(LoyaltyOrderIdentity).count()==0


def test_reviewed_profile_correction_cannot_rewrite_commercial_identity(loyalty):
    v=loyalty;oid=source(v);old_phone=None
    with v.sessions() as db:old_phone=db.get(SalesOrder,oid).whatsapp_phone
    profile=v.client.get('/api/customer-registration/profile/'+str(v.cid)+'?channel=CUSTOMER_MANAGEMENT').json()
    data={'name':'Reviewed contact name','phone':'+96550012345','email':'','fingerprint':profile['fingerprint']}
    r=v.client.post('/api/customer-registration/profile/'+str(v.cid)+'/edit',json=data,headers={'X-CSRF-Token':v.csrf});assert r.status_code==200,r.text
    assert v.client.post('/api/customer-registration/profile/'+str(v.cid)+'/edit',json=data,headers={'X-CSRF-Token':v.csrf}).status_code==409
    with v.sessions() as db:
        assert db.get(Customer,v.cid).phone=='+96550012345'
        o=db.get(SalesOrder,oid);assert o.customer_id==v.cid and o.whatsapp_phone==old_phone and o.grand_total==4500
        assert db.query(JournalEntry).count()==0
    v.login('quick-agent')
    assert v.client.post('/api/customer-registration/profile/'+str(v.cid)+'/edit',json=data,headers={'X-CSRF-Token':v.csrf}).status_code==403


def test_crm_completed_customer_is_recognized_by_pos_search(loyalty,monkeypatch):
    import main
    v=loyalty;source(v)
    while loyalty_events.process_one(v.sessions):pass
    monkeypatch.setattr(main.settings,'POS_ENABLED',True)
    r=v.client.get('/crm/pos/api/customers?q=0771234567');assert r.status_code==200,r.text
    row=r.json()['rows'][0];assert row['id']==v.cid and row['membership_status']=='ACTIVE' and row['qualifying_purchases']==1
    assert row['membership_id']==v.client.get('/api/customer-registration/profile/'+str(v.cid)+'?channel=CRM').json()['membership_id']


def test_readonly_customer_history_and_search_do_not_register_inquiries(loyalty):
    v=loyalty;source(v)
    with v.sessions() as db:before=db.query(Customer).count();members=db.query(LoyaltyMember).count()
    for _ in range(3):
        assert v.client.get('/api/customer-registration/search?q=0771234567&channel=CRM').status_code==200
        assert v.client.get('/api/customer-registration/profile/'+str(v.cid)+'/history').status_code==200
    with v.sessions() as db:assert db.query(Customer).count()==before and db.query(LoyaltyMember).count()==members
    assert not v.sends


@pytest.mark.parametrize('channel,grants',[('CRM',['whatsapp','sales_orders']),('POS',['whatsapp','pos.view','pos.sell'])])
def test_authorized_non_admin_can_register_without_management_or_export(loyalty,monkeypatch,channel,grants):
    import main
    v=loyalty;monkeypatch.setattr(main.settings,'POS_ENABLED',True)
    with v.sessions() as db:
        user=db.query(User).filter_by(username='quick-agent').one();user.section_access=grants;db.commit()
    v.login('quick-agent')
    import re
    csrf=re.search(r'data-assignment-csrf="([^"]+)"',v.client.get('/crm/whatsapp').text).group(1)
    data={'name':'Staff registered contact','phone':'+96550012345','channel':channel}
    assert v.client.post('/api/customer-registration/register',json=data,headers={'X-CSRF-Token':csrf}).status_code==422
    response=v.client.post('/api/customer-registration/register',json=data,headers={'X-CSRF-Token':csrf,'Idempotency-Key':str(uuid4())})
    assert response.status_code==200,response.text
    assert response.json()['customer']['source']==channel and not response.json()['marketing_consent_granted']
    assert v.client.get('/customers').status_code==403
    assert v.client.get('/crm/loyalty/reports').status_code==403
    with v.sessions() as db:assert db.query(LoyaltyMember).count()==0 and db.query(JournalEntry).count()==0
