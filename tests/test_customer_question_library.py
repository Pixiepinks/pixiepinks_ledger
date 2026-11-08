"""Saved flows, explicit selection, archive/snapshot safety; no real transports."""
import pytest
from models import CustomerQuestionFlow as Flow,CustomerQuestionRun as Run,GlobalAIReplyControl
from test_customer_questions import questions,inbox,settings,act,reply
import customer_questions as q

BASE='/crm/quick-messages/customer-questions'

def action(v,kind,**extra):
    version=v[0].get(BASE+'/api').json()['version']
    return v[0].post(BASE+'/flows',json={'action':kind,'version':version,**extra},headers={'X-CSRF-Token':v[5]})

def select_start(v,flow_id,version):
    return v[0].post(f'/crm/whatsapp/api/conversations/{v[2][0]}/customer-questions',
        json={'action':'start','flow_id':flow_id,'flow_version':version},headers={'X-CSRF-Token':v[5]})

def test_preserve_original_create_edit_duplicate_and_switch(questions):
    v=questions;client,sessions,_,sends,_,csrf,_=v
    first=client.get(BASE+'/api?flow_id=1').json();assert len(first['flows'])==1 and first['definition']==q.DEFAULT
    assert action(v,'create',title='Bicycle enquiry').status_code==200
    second=client.get(BASE+'/api?flow_id=2').json();assert second['definition']['title']=='Bicycle enquiry'
    original=q.DEFAULT
    payload={'flow_id':2,'version':second['version'],'enabled':False,'definition':{'title':'Bicycle enquiry','steps':[{'key':'budget','body':'Your budget?','options':['Low','High']}]}}
    assert client.post(BASE+'/api',json=payload,headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.get(BASE+'/api?flow_id=1').json()['definition']==original
    duplicate=action(v,'duplicate',flow_id=2,title='Scooter enquiry');assert duplicate.status_code==200
    copy=client.get(BASE+'/api?flow_id=3').json();assert copy['definition']['steps']==payload['definition']['steps']
    assert copy['definition']['title']=='Scooter enquiry'
    with sessions() as db:
        assert db.query(Run).count()==0 and db.get(GlobalAIReplyControl,1).enabled is False
    assert not sends


def test_explicit_selected_flow_snapshot_and_archiving_active_run(questions):
    v=questions
    assert settings(v[0],v[5],enabled=True).status_code==200
    result=action(v,'duplicate',flow_id=1,title='Gift packs');fid=result.json()['flow_id']
    version=v[0].get(BASE+f'/api?flow_id={fid}').json()['flow']['version']
    assert select_start(v,fid,version).status_code==200
    assert q.dispatch_one(v[1])
    assert action(v,'archive',flow_id=fid).status_code==200
    current=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}/customer-questions').json()
    assert fid not in [flow['id'] for flow in current['flows']] and current['run']['flow_id']==fid
    assert reply(v,index=1).status_code==200 and q.dispatch_one(v[1])
    assert reply(v,index=2).status_code==200
    with v[1]() as db:
        run=q.latest(db,v[2][0]);assert run.status=='COMPLETE' and run.definition['title']=='Gift packs'
    assert select_start(v,fid,version).status_code==409
    assert action(v,'restore',flow_id=fid).status_code==200
    fresh=v[0].get(BASE+f'/api?flow_id={fid}').json()['flow']['version']
    assert select_start(v,fid,fresh).status_code==200
    assert select_start(v,1,0).status_code==409


def test_stale_preview_conflict_permissions_and_global_off(questions):
    v=questions;assert settings(v[0],v[5],enabled=True).status_code==200
    fid=action(v,'create',title='Another flow').json()['flow_id']
    data=v[0].get(BASE+f'/api?flow_id={fid}').json()
    assert v[0].post(BASE+'/api',json={'flow_id':fid,'definition':data['definition'],'version':data['version'],'enabled':True},headers={'X-CSRF-Token':v[5]}).status_code==200
    assert select_start(v,fid,0).status_code==409
    assert select_start(v,999,0).status_code==409
    assert action(v,'toggle',enabled=False).status_code==200
    assert select_start(v,fid,1).status_code==409
    # Admin library is unavailable to staff, even for their owned conversation.
    v[4]('quick-staff')
    assert v[0].get(BASE+'/api?flow_id=1').status_code==403
    assert v[0].post(BASE+'/flows',json={}).status_code==403


def test_archive_all_flows_can_still_turn_automation_off(questions):
    v=questions;assert settings(v[0],v[5],enabled=True).status_code==200
    assert action(v,'archive',flow_id=1).status_code==200
    assert action(v,'toggle',enabled=False).status_code==200
    assert not v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}/customer-questions').json()['enabled']
    stale=v[0].post(BASE+'/flows',json={'action':'create','title':'Stale','version':0},headers={'X-CSRF-Token':v[5]})
    assert stale.status_code==409
    with v[1]() as db:assert db.query(Flow).count()==1
