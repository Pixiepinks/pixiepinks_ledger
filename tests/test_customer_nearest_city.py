"""Nearest-town collection, optional skip and retained-run compatibility."""
import pytest
import customer_questions as q
import question_delivery_details as delivery
from models import SalesOrder
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting,begin,text,current,complete_fields
from test_question_payment_followup import paying
from test_question_payment_review import reviewing,prepare
from test_customer_questions import reply


def at_city(c):
    begin(c)
    for answer in ['Test Customer','123 Main Road, Gampaha']:
        assert text(c,answer).status_code==200 and q.dispatch_one(c[0][0].sessions)
    assert current(c)[1]['stage']=='city'
    assert c[0][3][-1][-1][1]==delivery.PROMPTS['city']


def test_nearest_city_is_asked_after_address_and_saved_before_phone(collecting):
    c=collecting;at_city(c);v=c[0][0]
    assert text(c,'12345').status_code==200 and current(c)[1]['stage']=='city'
    assert q.dispatch_one(v.sessions)
    assert text(c,'  ගම්පහ  ',mid='wamid.nearest.city').status_code==200
    assert text(c,'  ගම්පහ  ',mid='wamid.nearest.city').status_code==200
    details=current(c)[1];assert details['city']=='ගම්පහ' and details['stage']=='phone1' and 'phone1' not in details
    assert q.dispatch_one(v.sessions) and c[1][-1][1]==delivery.PROMPTS['phone1']
    for answer in ['0771234567','skip']:
        assert text(c,answer).status_code==200 and q.dispatch_one(v.sessions)
    assert 'නගරය: ගම්පහ' in c[0][3][-1][-1][1]


@pytest.mark.parametrize('how',['button','text'])
def test_city_can_be_skipped_without_stopping_order(collecting,how):
    c=collecting;at_city(c)
    response=reply(c[0][3],index=0) if how=='button' else text(c,'skip')
    assert response.status_code==200 and q.dispatch_one(c[0][0].sessions)
    assert current(c)[1]['city']=='' and current(c)[1]['stage']=='phone1'


def test_old_phone_stage_is_not_reinterpreted_as_city(collecting):
    c=collecting;at_city(c);v=c[0][0]
    assert text(c,'Gampaha').status_code==200 and q.dispatch_one(v.sessions)
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);key=run.definition['steps'][run.step]['key'];answer=dict(run.answers[key]);details=dict(answer['delivery_details'])
        details.pop('schema_version');details.pop('city');answer['delivery_details']=details;run.answers={**run.answers,key:answer};db.commit()
    assert text(c,'0771234567').status_code==200
    assert current(c)[1]['stage']=='phone2' and current(c)[1]['phone1']=='+94771234567' and 'city' not in current(c)[1]


def test_old_address_stage_adds_city_at_safe_boundary(collecting):
    c=begin(collecting);v=c[0][0]
    assert text(c,'Test Customer').status_code==200 and q.dispatch_one(v.sessions)
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);details=dict(delivery.selected(run)['delivery_details']);details.pop('schema_version');delivery.save(run,details);db.commit()
    assert text(c,'123 Main Road, Gampaha').status_code==200 and q.dispatch_one(v.sessions)
    assert current(c)[1]['stage']=='city' and current(c)[1]['schema_version']==2


def test_edit_details_collects_replacement_city(collecting):
    c=begin(collecting);v=c[0][0];complete_fields(c,city='Colombo')
    assert reply(c[0][3],index=1).status_code==200 and q.dispatch_one(v.sessions)
    assert 'city' not in current(c)[1]
    complete_fields(c,city='ගම්පහ')
    assert current(c)[1]['city']=='ගම්පහ'
    assert 'නගරය: ගම්පහ' in c[0][3][-1][-1][1]


def test_collected_city_is_copied_to_order_and_district_stays_staff_only(reviewing):
    oid=prepare(reviewing);v=reviewing[1]
    with v.sessions() as db:
        details=q.latest(db,v.ids[0]).answers['recommendations']['delivery_details']
        order=db.get(SalesOrder,oid)
        assert order.city==details['city']=='Colombo' and not order.district
    assert 'district' not in delivery.PROMPTS
