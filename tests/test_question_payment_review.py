"""Actual bank verification is explicit staff work; provider sends are mocked."""
from datetime import datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
import main
import customer_questions as q
import question_payment_review as review
from models import SalesOrder,SalesPayment,SalesPaymentAuditEvent,SalesOrderStatusHistory,GlobalAIReplyControl,CustomerQuestionProductSend as Item,WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,User
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting,text
from test_question_payment_followup import paying,instructions,slip
from test_shopify_collection_sends import product
from test_customer_questions import settings


@pytest.fixture
def reviewing(paying,monkeypatch):
    instructions(paying);v=paying[0][0][0]
    assert slip(paying).status_code==200
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    monkeypatch.setattr(main.settings,'SALES_PAYMENT_TRACKING_ENABLED',True)
    monkeypatch.setattr(review,'resolve_inbox_product',lambda *a:product(1))
    calls=[]
    monkeypatch.setattr(review,'send_whatsapp_text',lambda phone,body,**kw:calls.append((phone,body)) or 'wamid.payment.verified')
    with v.sessions() as db:rid=q.latest(db,v.ids[0]).id
    url=f'/crm/whatsapp/api/conversations/{v.ids[0]}/customer-questions/{rid}/payment-review'
    return paying,v,url,calls


def get(fixture):
    result=fixture[1].client.get(fixture[2]);assert result.status_code==200,result.text
    return result.json()


def action(fixture,name,**values):
    data=get(fixture);v=fixture[1]
    return v.client.post(fixture[2],json={'action':name,'version':data['version'],**values},headers={'X-CSRF-Token':v.csrf})


def prepare(fixture):
    result=action(fixture,'prepare_order');assert result.status_code==200,result.text
    return result.json()['order']['id']


def verify_values(fixture,order_id,**changes):
    return {'action':'verify','version':get(fixture)['version'],'sales_order_id':order_id,'amount':'46800.00',
        'payment_date':get(fixture)['today'],'payment_reference':'BANK-TEST-001','bank_checked':True,**changes}


def verify(fixture,order_id,**changes):
    return fixture[1].client.post(fixture[2],json=verify_values(fixture,order_id,**changes),headers={'X-CSRF-Token':fixture[1].csrf})


def test_prepare_verify_actual_bank_and_confirm_once(reviewing):
    f=reviewing;_,v,_,calls=f
    assert get(f)['orders']==[] and not q.dispatch_one(v.sessions)
    oid=prepare(f)
    with v.sessions() as db:
        order=db.get(SalesOrder,oid)
        assert order.grand_total==Decimal('46800.00') and order.service_total==Decimal('1800.00')
        assert order.order_status=='AWAITING_PAYMENT' and order.payment_status=='UNPAID'
        assert len(order.lines)==1 and order.lines[0].unit_price==Decimal('45000.00')
        assert 'Delivery phone 1: +94771234567' in order.customer_notes
        assert not db.query(SalesPayment).count()
    assert verify(f,oid,bank_checked=False).status_code==422
    payload=verify_values(f,oid)
    first=v.client.post(f[2],json=payload,headers={'X-CSRF-Token':v.csrf});assert first.status_code==200,first.text
    second=v.client.post(f[2],json=payload,headers={'X-CSRF-Token':v.csrf});assert second.status_code==200,second.text
    assert not calls
    with v.sessions() as db:
        order=db.get(SalesOrder,oid);payment=db.query(SalesPayment).one()
        assert order.payment_status=='PAID' and order.amount_paid==Decimal('46800.00') and order.order_status=='CONFIRMED'
        assert payment.verification_status=='VERIFIED' and payment.verified_by=='quick-admin'
        assert db.query(SalesPaymentAuditEvent).count()==2
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions) and len(calls)==1
    assert 'ගෙවීම තහවුරු කර ඇත' in calls[0][1] and '46,800.00' in calls[0][1]
    assert v.client.post(f[2],json=payload,headers={'X-CSRF-Token':v.csrf}).status_code==200
    assert not q.dispatch_one(v.sessions)
    assert get(f)['payment']['confirmation_status']=='SENT'
    with v.sessions() as db:
        message=db.query(Message).filter_by(response_kind='staff_payment').one()
        assert message.sent_by=='quick-admin' and message.analytics_actor_kind=='staff' and message.analytics_category=='text'


def test_partial_payment_does_not_claim_full_payment(reviewing):
    f=reviewing;oid=prepare(f)
    result=verify(f,oid,amount='40000.00');assert result.status_code==200,result.text
    with f[1].sessions() as db:
        order=db.get(SalesOrder,oid)
        assert order.payment_status=='PARTIALLY_PAID' and order.amount_paid==Decimal('40000.00')
        assert order.order_status=='AWAITING_PAYMENT'
    assert q.dispatch_one(f[1].sessions)
    assert '6,800.00' in f[3][0][1] and 'ඉතිරි මුදල' in f[3][0][1]


@pytest.mark.parametrize('change',[{'amount':'46800.01'},{'amount':'NaN'},{'amount':'1.001'},{'payment_reference':''},{'bank_checked':False},{'payment_date':'2099-01-01'}])
def test_invalid_bank_verification_changes_nothing(reviewing,change):
    oid=prepare(reviewing)
    assert verify(reviewing,oid,**change).status_code in {409,422}
    with reviewing[1].sessions() as db:
        assert not db.query(SalesPayment).count() and db.get(SalesOrder,oid).amount_paid==0
    assert not reviewing[3]


def test_payment_and_run_and_order_roll_back_atomically(reviewing,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    f=reviewing;oid=prepare(f);original=q.audit
    def fail(db,run,action,*args,**kw):
        if action=='BANK_PAYMENT_VERIFIED':raise SQLAlchemyError('fixture rollback')
        return original(db,run,action,*args,**kw)
    monkeypatch.setattr(q,'audit',fail)
    assert verify(f,oid).status_code==503
    with f[1].sessions() as db:
        assert not db.query(SalesPayment).count() and not db.query(SalesPaymentAuditEvent).count()
        order=db.get(SalesOrder,oid);assert order.payment_status=='UNPAID' and order.order_status=='AWAITING_PAYMENT'
    assert get(f)['payment']['status']=='SLIP_REVIEW'
    monkeypatch.setattr(q,'audit',original)
    assert verify(f,oid).status_code==200


@pytest.mark.parametrize('bad',['conversation','quote','service','product','cancelled'])
def test_wrong_order_cannot_receive_this_slip_payment(reviewing,bad):
    oid=prepare(reviewing);v=reviewing[1]
    with v.sessions() as db:
        order=db.get(SalesOrder,oid)
        if bad=='conversation':order.crm_conversation_id=v.ids[1]
        elif bad=='quote':order.grand_total+=1
        elif bad=='service':order.service_total=0
        elif bad=='product':order.lines[0].shopify_variant_id='gid://shopify/ProductVariant/999'
        else:order.order_status='CANCELLED'
        db.commit()
    assert verify(reviewing,oid).status_code==409
    with v.sessions() as db:assert not db.query(SalesPayment).count()


def test_reference_duplicate_is_not_counted_again(reviewing):
    from sales_payment_service import record_payment_claim
    oid=prepare(reviewing);v=reviewing[1]
    with v.sessions() as db:
        record_payment_claim(db,oid,{'payment_method':'BANK_TRANSFER','amount':'100.00','payment_date':get(reviewing)['today'],'payment_reference':'BANK-TEST-001'},'quick-admin','other-recorded-claim')
    assert verify(reviewing,oid,amount='100.00').status_code==409
    with v.sessions() as db:assert db.query(SalesPayment).count()==1 and db.get(SalesOrder,oid).amount_paid==0


def test_permission_feature_flags_and_csrf(reviewing,monkeypatch):
    f=reviewing;oid=prepare(f);v=f[1];payload=verify_values(f,oid)
    assert v.client.post(f[2],json=payload).status_code==403
    monkeypatch.setattr(main.settings,'SALES_PAYMENT_TRACKING_ENABLED',False)
    assert v.client.post(f[2],json=payload,headers={'X-CSRF-Token':v.csrf}).status_code==404
    monkeypatch.setattr(main.settings,'SALES_PAYMENT_TRACKING_ENABLED',True)
    v.login('quick-staff');token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert v.client.post(f[2],json=payload,headers={'X-CSRF-Token':token}).status_code==403
    v.login('quick-agent');token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert v.client.get(f[2]).status_code==404
    with v.sessions() as db:assert not db.query(SalesPayment).count()


def test_price_change_prevents_order_creation(reviewing,monkeypatch):
    p=product(1);p['price']=Decimal('50000.00')
    monkeypatch.setattr(review,'resolve_inbox_product',lambda *a:p)
    assert action(reviewing,'prepare_order').status_code==409
    with reviewing[1].sessions() as db:assert not db.query(SalesOrder).count()


@pytest.mark.parametrize('failure',['timeout','crash'])
def test_confirmation_failure_never_loses_payment_or_replays(reviewing,monkeypatch,failure):
    f=reviewing;oid=prepare(f);assert verify(f,oid).status_code==200
    if failure=='timeout':monkeypatch.setattr(review,'send_whatsapp_text',lambda *a,**kw:None)
    else:
        def crash(*a,**kw):raise KeyboardInterrupt()
        monkeypatch.setattr(review,'send_whatsapp_text',crash)
    if failure=='crash':
        with pytest.raises(KeyboardInterrupt):q.dispatch_one(f[1].sessions)
    else:assert q.dispatch_one(f[1].sessions)
    assert not q.dispatch_one(f[1].sessions)
    with f[1].sessions() as db:assert db.get(SalesOrder,oid).payment_status=='PAID' and db.query(SalesPayment).one().verification_status=='VERIFIED'
    assert action(f,'queue_confirmation').status_code==409


def test_reply_window_can_reopen_without_recording_payment_again(reviewing):
    f=reviewing;oid=prepare(f);v=f[1]
    with v.sessions() as db:db.get(Conversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert verify(f,oid).status_code==200
    assert get(f)['payment']['confirmation_status']=='WINDOW_CLOSED' and not q.dispatch_one(v.sessions)
    assert action(f,'queue_confirmation').status_code==409
    assert text(f[0][0],'Hello again').status_code==200
    assert action(f,'queue_confirmation').status_code==200 and q.dispatch_one(v.sessions)
    with v.sessions() as db:assert db.query(SalesPayment).count()==1


def test_automation_off_queues_notification_but_allows_staff_verification(reviewing):
    f=reviewing;oid=prepare(f);v=f[1]
    assert settings(v.client,v.csrf,enabled=False).status_code==200
    assert verify(f,oid).status_code==200 and not q.dispatch_one(v.sessions)
    assert settings(v.client,v.csrf,enabled=True).status_code==200
    assert q.dispatch_one(v.sessions) and len(f[3])==1


def test_window_closes_after_verification_before_send(reviewing):
    f=reviewing;oid=prepare(f);v=f[1];assert verify(f,oid).status_code==200
    with v.sessions() as db:db.get(Conversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert q.dispatch_one(v.sessions) and not f[3]
    assert get(f)['payment']['confirmation_status']=='WINDOW_CLOSED'
    with v.sessions() as db:assert db.query(Item).filter_by(position=-202).one().status=='QUEUED'
    assert text(f[0][0],'Hello again').status_code==200
    assert action(f,'queue_confirmation').status_code==200 and q.dispatch_one(v.sessions)
    with v.sessions() as db:assert db.query(SalesPayment).count()==1
