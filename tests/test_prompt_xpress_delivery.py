"""Prepaid courier flow, manual Delivered, no live customer or courier calls."""
from datetime import datetime,timedelta
import pytest
import main
import customer_questions as q
import prompt_xpress_delivery as courier
from models import SalesOrder,SalesFulfillment,SalesDeliveryDetail,SalesFulfillmentEvent,SalesPayment,WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,WhatsAppManualSend as Send,SalesOrderStatusHistory,JournalEntry
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting,text
from test_question_payment_followup import paying
from test_question_payment_review import reviewing,prepare,verify


@pytest.fixture
def dispatching(reviewing,monkeypatch):
    oid=prepare(reviewing);assert verify(reviewing,oid).status_code==200
    v=reviewing[1];assert q.dispatch_one(v.sessions)
    monkeypatch.setattr(main.settings,'SALES_FULFILLMENT_ENABLED',True)
    monkeypatch.setattr(main.settings,'SALES_DELIVERY_TRACKING_ENABLED',True)
    sends=[]
    monkeypatch.setattr(courier,'send_whatsapp_text',lambda phone,body,**kw:sends.append((phone,body)) or 'wamid.prompt.tracking')
    return reviewing,v,f'/api/sales-orders/{oid}/prompt-xpress',oid,sends


def get(f):
    response=f[1].client.get(f[2]);assert response.status_code==200,response.text
    return response.json()


def action(f,name,**changes):
    values={'action':name,'version':get(f)['version'],**changes}
    return f[1].client.post(f[2],json=values,headers={'X-CSRF-Token':f[1].csrf})


def ready(f):
    result=action(f,'prepare');assert result.status_code==200,result.text
    return result.json()


def test_dispatch_sends_exact_prompt_link_once_then_manual_delivered(dispatching):
    f=dispatching;_,v,url,oid,sends=f
    with v.sessions() as db:journals=db.query(JournalEntry).count();paid=db.get(SalesOrder,oid).amount_paid
    ready(f)
    with v.sessions() as db:assert db.get(SalesOrder,oid).order_status=='PROCESSING'
    payload={'action':'dispatch','version':get(f)['version'],'tracking_number':'mc12345678'}
    response=v.client.post(url,json=payload,headers={'X-CSRF-Token':v.csrf});assert response.status_code==200,response.text
    assert response.json()['tracking_number']=='MC12345678' and response.json()['message_status']=='SENT'
    assert courier.TRACKING_URL in sends[0][1] and 'MC12345678' in sends[0][1] and len(sends)==1
    assert v.client.post(url,json=payload,headers={'X-CSRF-Token':v.csrf}).status_code==200 and len(sends)==1
    assert action(f,'send_tracking').status_code==200 and len(sends)==1
    with v.sessions() as db:
        order=db.get(SalesOrder,oid);assert order.order_status==order.fulfillment_status=='DISPATCHED'
        detail=db.query(SalesDeliveryDetail).one();assert detail.courier_name==courier.COURIER and detail.tracking_url==courier.TRACKING_URL
        message=db.query(Message).filter_by(response_kind='staff_tracking').one();assert message.analytics_category=='text' and message.sent_by=='quick-admin'
    assert action(f,'delivered').status_code==200 and action(f,'delivered').status_code==200
    with v.sessions() as db:
        order=db.get(SalesOrder,oid);ful=db.query(SalesFulfillment).one()
        assert order.order_status==order.fulfillment_status==ful.status=='DELIVERED'
        assert ful.delivered_at and db.query(SalesDeliveryDetail).one().delivery_date
        assert order.payment_status=='PAID' and order.amount_paid==paid and db.query(SalesPayment).count()==1
        assert db.query(JournalEntry).count()==journals
        assert db.query(SalesOrderStatusHistory).filter_by(new_status='DELIVERED').count()==1
    assert len(sends)==1


@pytest.mark.parametrize('number',['12345678','MC1234567','MC123456789','MCabcdefgh','MC１２３４５６７８','MC1234 5678',''])
def test_invalid_tracking_never_dispatches(dispatching,number):
    f=dispatching;ready(f)
    assert action(f,'dispatch',tracking_number=number).status_code==422
    assert get(f)['fulfillment']['status']=='READY_TO_DISPATCH' and not f[4]


def test_delivered_before_dispatch_and_stale_version_are_rejected(dispatching):
    f=dispatching;ready(f)
    assert action(f,'delivered').status_code==409
    assert action(f,'dispatch',tracking_number='MC12345678',version=0).status_code==409
    assert not f[4]


@pytest.mark.parametrize('failure',['timeout','crash'])
def test_uncertain_or_crashed_send_never_replays_or_loses_dispatch(dispatching,monkeypatch,failure):
    f=dispatching;ready(f)
    calls=[]
    def send(*a,**kw):
        calls.append(1)
        if failure=='crash':raise KeyboardInterrupt()
        return None
    monkeypatch.setattr(courier,'send_whatsapp_text',send)
    if failure=='crash':
        with pytest.raises(KeyboardInterrupt):action(f,'dispatch',tracking_number='MC12345678')
    else:assert action(f,'dispatch',tracking_number='MC12345678').status_code==200
    assert get(f)['order_status']=='DISPATCHED'
    assert action(f,'send_tracking').status_code==200 and len(calls)==1
    assert get(f)['message_status'] in {'PROCESSING','UNCERTAIN'}


def test_reply_window_closed_does_not_block_physical_dispatch(dispatching):
    f=dispatching;ready(f);v=f[1]
    with v.sessions() as db:db.get(Conversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert action(f,'dispatch',tracking_number='MC12345678').status_code==200
    assert get(f)['order_status']=='DISPATCHED' and get(f)['message_status']=='WINDOW_CLOSED' and not f[4]
    assert text(f[0][0][0],'Please send tracking').status_code==200
    assert action(f,'send_tracking').status_code==200 and len(f[4])==1


def test_permissions_csrf_flags_and_payment_guard(dispatching,monkeypatch):
    f=dispatching;v=f[1]
    assert v.client.post(f[2],json={'action':'prepare','version':0}).status_code==403
    monkeypatch.setattr(main.settings,'SALES_DELIVERY_TRACKING_ENABLED',False)
    assert v.client.get(f[2]).status_code==404
    monkeypatch.setattr(main.settings,'SALES_DELIVERY_TRACKING_ENABLED',True)
    with v.sessions() as db:db.get(SalesOrder,f[3]).payment_status='PARTIALLY_PAID';db.commit()
    assert action(f,'prepare').status_code==409
    v.login('quick-agent');assert v.client.get(f[2]).status_code==404
    with v.sessions() as db:assert not db.query(SalesFulfillment).count()


def test_dispatch_data_roll_back_together(dispatching,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    f=dispatching;ready(f);original=courier.claim_message
    def fail(*a,**kw):raise SQLAlchemyError('fixture rollback')
    monkeypatch.setattr(courier,'claim_message',fail)
    assert action(f,'dispatch',tracking_number='MC12345678').status_code==503
    assert get(f)['order_status']=='PROCESSING' and get(f)['fulfillment']['status']=='READY_TO_DISPATCH'
    assert not f[4]
    monkeypatch.setattr(courier,'claim_message',original)
    assert action(f,'dispatch',tracking_number='MC12345678').status_code==200


def test_generic_tracking_editor_validates_prompt_format_and_link(dispatching):
    from sales_fulfillment_service import update_delivery,FulfillmentError
    f=dispatching;ready(f)
    with f[1].sessions() as db:
        fid=db.query(SalesFulfillment).one().id
        with pytest.raises(FulfillmentError):update_delivery(db,fid,{'courier_name':'Prompt Xpress','tracking_number':'bad'},'quick-admin','invalid-prompt-tracking')
        db.rollback()
        detail=update_delivery(db,fid,{'courier_name':'PromptXpress','tracking_number':'mc12345678','tracking_url':'https://other.example/'},'quick-admin','valid-prompt-tracking')
        assert detail.tracking_number=='MC12345678' and detail.tracking_url==courier.TRACKING_URL and detail.courier_name==courier.COURIER


def test_fulfillment_action_key_cannot_be_reused_across_orders(dispatching):
    from sales_fulfillment_service import create_full_fulfillment,transition,FulfillmentError
    from models import SalesOrderLine
    f=dispatching;ready(f)
    with f[1].sessions() as db:
        first=db.query(SalesFulfillment).one()
        other=SalesOrder(internal_order_number='SO-OTHER-DELIVERY',idempotency_key='other-delivery',customer_name='Other',whatsapp_phone='94770000000',created_by='quick-admin',order_status='CONFIRMED',subtotal=1,grand_total=1)
        other.lines.append(SalesOrderLine(shopify_product_id='gid://shopify/Product/1',shopify_variant_id='gid://shopify/ProductVariant/1',product_name_snapshot='Test',quantity=1,unit_price=1,line_total=1))
        db.add(other);db.commit()
        second=create_full_fulfillment(db,other.id,'quick-admin','other-fulfillment-action')
        with pytest.raises(FulfillmentError):create_full_fulfillment(db,f[3],'quick-admin','other-fulfillment-action')
        transition(db,second.id,'PACKING','quick-admin','other-transition-action')
        with pytest.raises(FulfillmentError):transition(db,first.id,'DISPATCHED','quick-admin','other-transition-action')


def test_concurrent_dispatch_clicks_send_tracking_once(dispatching):
    from concurrent.futures import ThreadPoolExecutor
    f=dispatching;ready(f);v=f[1]
    payload={'action':'dispatch','version':get(f)['version'],'tracking_number':'MC12345678'}
    def submit(_):return v.client.post(f[2],json=payload,headers={'X-CSRF-Token':v.csrf})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert [result.status_code for result in results]==[200,200],[(result.status_code,result.text) for result in results]
    assert len(f[4])==1 and get(f)['message_status']=='SENT'
    with v.sessions() as db:assert db.query(Send).filter(Send.idempotency_key.like('prompt-xpress-%')).count()==1
