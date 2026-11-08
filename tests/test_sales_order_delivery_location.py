"""Staff completion of paid-order location with money and customer flow preserved."""
from datetime import datetime,timedelta
import json
import pytest
import main
import sales_order_delivery_location as location
from models import SalesOrder,SalesPayment,SalesPaymentAuditEvent,SalesOrderStatusHistory,User,CustomerQuestionRun
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting
from test_question_payment_followup import paying
from test_question_payment_review import reviewing,prepare,verify


@pytest.fixture
def editing(reviewing):
    oid=prepare(reviewing);assert verify(reviewing,oid).status_code==200
    return reviewing,reviewing[1],oid,f'/api/sales-orders/{oid}/delivery-location'


def get(f):
    response=f[1].client.get(f[3]);assert response.status_code==200,response.text
    return response.json()


def save(f,**changes):
    payload={**get(f),'city':'Homagama','district':'Colombo','postal_code':'10200',**changes}
    return f[1].client.put(f[3],json=payload,headers={'X-CSRF-Token':f[1].csrf})


def test_complete_city_and_district_on_paid_confirmed_order(editing):
    f=editing;v=f[1]
    with v.sessions() as db:
        order=db.get(SalesOrder,f[2]);before=(order.grand_total,order.amount_paid,order.payment_status,order.order_status,order.service_total)
        answers=db.query(CustomerQuestionRun).one().answers;definition=db.query(CustomerQuestionRun).one().definition
        payment=db.query(SalesPayment).one();p_before=(payment.id,payment.amount,payment.verification_status,payment.verified_at)
        audits=db.query(SalesPaymentAuditEvent).count()
    payload={**get(f),'city':'Homagama','district':'Colombo','postal_code':'10200'}
    response=v.client.put(f[3],json=payload,headers={'X-CSRF-Token':v.csrf});assert response.status_code==200,response.text
    assert v.client.put(f[3],json=payload,headers={'X-CSRF-Token':v.csrf}).status_code==200
    with v.sessions() as db:
        order=db.get(SalesOrder,f[2]);assert (order.grand_total,order.amount_paid,order.payment_status,order.order_status,order.service_total)==before
        assert order.city=='Homagama' and order.district=='Colombo' and order.postal_code=='10200'
        payment=db.query(SalesPayment).one();assert (payment.id,payment.amount,payment.verification_status,payment.verified_at)==p_before
        assert db.query(SalesPaymentAuditEvent).count()==audits
        assert db.query(CustomerQuestionRun).one().answers==answers and db.query(CustomerQuestionRun).one().definition==definition
        events=[event for event in db.query(SalesOrderStatusHistory) if 'DELIVERY_LOCATION_UPDATED' in (event.reason or '')]
        assert len(events)==1 and events[0].changed_by=='quick-admin'
        assert json.loads(events[0].reason)['before']=={'city':'','district':'','postal_code':''}
        from shopify_draft_order_service import _validate_local_order
        _validate_local_order(order)


def test_optional_location_does_not_block_order(editing):
    response=save(editing,district='',postal_code='');assert response.status_code==200,response.text
    assert response.json()['district']==''
    with editing[1].sessions() as db:assert db.get(SalesOrder,editing[2]).payment_status=='PAID'


def test_stale_edit_and_financial_fields_are_rejected(editing):
    f=editing;old=get(f)['version'];assert save(f).status_code==200
    assert save(f,version=old,city='Other city').status_code==409
    assert save(f,grand_total='1.00').status_code==422
    assert get(f)['city']=='Homagama'


@pytest.mark.parametrize('change',[{'city':'a'*121},{'district':'Colombo\n'},{'postal_code':None}])
def test_invalid_location_is_rejected(editing,change):
    assert save(editing,**change).status_code==422
    assert get(editing)['city']==''


@pytest.mark.parametrize('closed',['DISPATCHED','DELIVERED','CANCELLED','RETURNED','LINKED','SYNCING'])
def test_locked_delivery_context_cannot_be_edited(editing,closed):
    f=editing;payload={**get(f),'city':'Homagama','district':'Colombo'}
    with f[1].sessions() as db:
        order=db.get(SalesOrder,f[2])
        if closed=='LINKED':order.shopify_draft_order_id='gid://shopify/DraftOrder/123'
        elif closed=='SYNCING':order.shopify_sync_status='SYNCING'
        else:order.order_status=closed
        db.commit()
    assert f[1].client.put(f[3],json=payload,headers={'X-CSRF-Token':f[1].csrf}).status_code==409
    assert 'id="edit-delivery-location"' not in f[1].client.get(f'/crm/sales-orders/{f[2]}').text


def test_permissions_csrf_and_feature_gate(editing,monkeypatch):
    f=editing;v=f[1];payload={**get(f),'city':'Homagama','district':'Colombo'}
    assert v.client.put(f[3],json=payload).status_code==403
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',False)
    assert v.client.put(f[3],json=payload,headers={'X-CSRF-Token':v.csrf}).status_code==404
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    v.login('quick-staff');token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert v.client.put(f[3],json=payload,headers={'X-CSRF-Token':token}).status_code==403
    v.login('quick-agent');token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert v.client.put(f[3],json=payload,headers={'X-CSRF-Token':token}).status_code==404


def test_location_and_audit_commit_atomically(editing,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    f=editing;original=location.SalesOrderStatusHistory
    def fail(*a,**kw):raise SQLAlchemyError('fixture audit failure')
    monkeypatch.setattr(location,'SalesOrderStatusHistory',fail)
    assert save(f).status_code==503 and get(f)['city']==''
    monkeypatch.setattr(location,'SalesOrderStatusHistory',original)
    assert save(f).status_code==200


def test_legacy_fulfillment_already_dispatched_locks_location(editing):
    from models import SalesFulfillment
    f=editing;payload={**get(f),'city':'Homagama','district':'Colombo'}
    with f[1].sessions() as db:
        db.add(SalesFulfillment(sales_order_id=f[2],fulfillment_reference='FUL-LEGACY-DISPATCHED',status='DISPATCHED',created_by='quick-admin'));db.commit()
    assert f[1].client.put(f[3],json=payload,headers={'X-CSRF-Token':f[1].csrf}).status_code==409
