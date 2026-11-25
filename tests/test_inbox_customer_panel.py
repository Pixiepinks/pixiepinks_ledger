"""Read-only conversation projection, using canonical loyalty evidence only."""
from datetime import datetime
from pathlib import Path
from sqlalchemy import text
from alembic import command
from alembic.config import Config
import pytest
from models import Customer,User,WhatsAppConversation,SalesOrder,SalesPayment,JournalEntry
from loyalty_models import LoyaltyMember,LoyaltyAudit,LoyaltyConfig
from test_loyalty import loyalty,inbox,source,enabled
from test_retail_postgres import pg
import loyalty_events
import loyalty_service as service


def prepare(v,paid=True,completed=True,linked=True):
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).order_by(WhatsAppConversation.id).first();c.phone_number='94771234567';cid=c.id;db.commit()
    if linked:
        oid=source(v,paid=paid,status='DELIVERED' if completed else 'DRAFT')
        with v.sessions() as db:db.get(SalesOrder,oid).crm_conversation_id=cid;db.commit()
    while loyalty_events.process_one(v.sessions):pass
    return cid


def url(cid):return '/crm/whatsapp/api/conversations/'+str(cid)+'/customer-panel'


@pytest.mark.parametrize('state',['UNREGISTERED','SUGGESTED','REGISTERED','ACTIVE','SUSPENDED','INELIGIBLE','AMBIGUOUS'])
def test_authoritative_panel_states_do_not_use_recipient_or_order_total_as_membership(loyalty,state):
    v=loyalty;cid=prepare(v,paid=state not in {'REGISTERED'},completed=state not in {'REGISTERED'},linked=state not in {'UNREGISTERED','SUGGESTED','AMBIGUOUS'})
    with v.sessions() as db:
        if state=='UNREGISTERED':db.get(WhatsAppConversation,cid).phone_number='96550012345'
        if state=='AMBIGUOUS':db.add(Customer(name='Separate shared identity',phone='+94771234567'))
        if state=='SUSPENDED':db.query(LoyaltyMember).filter_by(customer_id=v.cid).one().status='SUSPENDED'
        if state=='INELIGIBLE':db.get(Customer,v.cid).phone=None
        db.commit()
    response=v.client.get(url(cid));assert response.status_code==200,response.text
    value=response.json();assert value['state']==state and response.headers['cache-control']=='private, no-store'
    if state=='ACTIVE':assert value['membership_id'] and value['lifetime_net']=='4500.00' and value['can_view_member']
    if state=='SUGGESTED':assert value['relationship']=='SUGGESTED' and value['membership_status']=='PENDING_FIRST_PURCHASE'
    assert not v.sends


def test_basic_staff_cannot_read_full_history_money_or_unauthorized_conversations(loyalty):
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:
        u=db.query(User).filter_by(username='quick-agent').one();u.section_access=['whatsapp','sales_orders'];db.get(WhatsAppConversation,cid).assigned_to_user_id=u.id;db.commit()
    v.login('quick-agent');response=v.client.get(url(cid));assert response.status_code==200,response.text
    data=response.json();assert not data['can_view_member'] and 'membership_url' not in data
    assert not {'history','candidates','lifetime_net','lifetime_savings','activated_at','profitability','cost'}&set(data)
    with v.sessions() as db:db.get(WhatsAppConversation,cid).assigned_to_user_id=None;db.commit()
    assert v.client.get(url(cid)).status_code==404
    with v.sessions() as db:
        denied=db.query(User).filter_by(username='quick-staff').one();denied.section_access=['whatsapp'];db.get(WhatsAppConversation,cid).assigned_to_user_id=denied.id;db.commit()
    v.login('quick-staff');assert v.client.get(url(cid)).status_code==403


def test_panel_get_is_readonly_preserves_programme_on_counts_unread_and_rules(loyalty):
    v=loyalty;cid=prepare(v);enabled(v,True)
    models=[Customer,SalesOrder,SalesPayment,JournalEntry,LoyaltyMember,LoyaltyAudit]
    with v.sessions() as db:
        counts=[db.query(m).count() for m in models];config=service.config(db);version=config.version;unread=db.get(WhatsAppConversation,cid).unread_count
    for _ in range(5):assert v.client.get(url(cid)).status_code==200
    with v.sessions() as db:
        assert [db.query(m).count() for m in models]==counts and service.config(db).enabled and service.config(db).version==version
        assert db.get(WhatsAppConversation,cid).unread_count==unread
    assert not v.sends


def test_lifetime_values_include_multiple_purchases_and_reviewed_association(loyalty):
    from loyalty_models import LoyaltyOrderIdentity
    v=loyalty;cid=prepare(v);other=source(v)
    with v.sessions() as db:
        order=db.query(SalesOrder).filter_by(crm_conversation_id=cid).one();order.customer_id=None
        db.add(LoyaltyOrderIdentity(order_id=order.id,customer_id=v.cid,actor_id=v.uid,reason='Reviewed synthetic attribution'));db.commit()
    data=v.client.get(url(cid)).json();assert data['relationship']=='CONFIRMED_ORDER' and data['customer_id']==v.cid
    assert data['lifetime_net']=='9000.00' and data['qualifying_count']==2


def test_repeated_startup_migration_keeps_programme_on(pg):
    engine,sessions,uid=pg
    with sessions() as db:db.add(LoyaltyConfig(id=1,enabled=True,version=7,value=service.DEFAULTS));db.commit()
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261123_54');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:assert db.get(LoyaltyConfig,1).enabled and db.get(LoyaltyConfig,1).version==7


def test_postgres_active_projection_is_valid_in_readonly_transaction(pg,monkeypatch):
    from settings import settings
    monkeypatch.setattr(settings,'SALES_ORDERS_ENABLED',True)
    import sales_order_service as orders
    from test_loyalty import payload
    engine,sessions,uid=pg
    with sessions() as db:
        db.add(LoyaltyConfig(id=1,enabled=True,version=3,value=service.DEFAULTS))
        customer=Customer(name='Read-only synthetic member',phone='0771234567');db.add(customer);db.flush()
        conversation=WhatsAppConversation(phone_number='94771234567',customer_name='Read-only synthetic member',mode='HUMAN');db.add(conversation);db.flush();cid=conversation.id
        order,_=orders.create_draft(db,payload(customer.id),'test','readonly-projection-order');order.crm_conversation_id=cid;order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
        db.add(SalesPayment(sales_order_id=order.id,payment_method='CASH',amount=order.grand_total,currency='LKR',verification_status='VERIFIED',payment_date=datetime.utcnow().date()));db.commit();service.refresh_member(db,customer.id);db.commit()
    import inbox_customer_panel as panel
    with sessions() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        value=panel.snapshot(db,db.get(User,uid),db.get(WhatsAppConversation,cid))
        assert value['state']=='ACTIVE' and value['lifetime_net']=='4500.00'
        assert db.execute(text('SHOW transaction_read_only')).scalar()=='on' and service.config(db).enabled
        db.rollback()
