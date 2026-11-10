"""Representative signed webhook → existing flow worker; all provider sends mocked."""
from datetime import datetime,timedelta
import hashlib,hmac,json
import pytest
import main
import customer_questions as q
from models import (AdFlowControl,AdFlowRule,AdFlowExecution as Exec,AdFlowAudit,CustomerQuestionConfig as Config,
    CustomerQuestionFlow as Flow,CustomerQuestionRun as Run,WhatsAppConversation as C,WhatsAppConversationMessage as M,
    WhatsAppMessageReferral as Ref,ProcessedWhatsAppMessage as Receipt,GlobalAIReplyControl,User)
from test_ad_assignment import routing,AD,SECRET,put as assign_rule
from test_whatsapp_quick_messages import inbox
from test_whatsapp_referrals import payload


@pytest.fixture
def flows(routing,monkeypatch):
    with routing[1]() as db:db.get(Config,1).enabled=True;db.commit()
    sends=[]
    monkeypatch.setattr(q,'send_buttons',lambda phone,body,buttons,**kw:sends.append((phone,body,buttons)) or f'wamid.flow.{len(sends)}')
    return routing,sends


def rule(f,aid=AD,**changes):
    v=f[0]
    with v[1]() as db:flow=db.get(Flow,1);version=flow.version
    return v[0].put('/crm/ad-flows/rules/'+aid,json={'name':'Bicycle enquiry','flow_id':1,'flow_version':version,'policy':'FIRST',
        'cooldown_hours':24,'allow_assigned':False,'enabled':True,'version':0,**changes},headers={'X-CSRF-Token':v[5]})


def master(f,enabled=True):
    v=f[0]
    with v[1]() as db:version=db.get(AdFlowControl,1).version
    return v[0].post('/crm/ad-flows/control',json={'enabled':enabled,'version':version},headers={'X-CSRF-Token':v[5]})


def post(f,value,signed=True):
    raw=json.dumps(value).encode();headers={'Content-Type':'application/json'}
    if signed:headers['X-Hub-Signature-256']='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    return f[0][0].post('/webhook',content=raw,headers=headers)


def incoming(f,mid='wamid.ad-flow',aid=AD,phone='94770009999',source='ad',text='Hello',ctwa=False,ref=True):
    body=payload(mid=mid,body=text);m=body['entry'][0]['changes'][0]['value']['messages'][0]
    m['timestamp']=str(int(datetime.utcnow().timestamp()));m['from']=phone
    if ref:m['referral'].update(source_id=aid,source_type=source)
    else:m.pop('referral',None)
    if ref and not ctwa:m['referral'].pop('ctwa_clid',None)
    return post(f,body)


def answer(f,cid,index=0,title='untrusted'):
    with f[0][1]() as db:
        run=q.latest(db,cid);phone=db.get(C,cid).phone_number
        m={'id':'wamid.answer.'+str(run.step),'from':phone,'timestamp':str(int(datetime.utcnow().timestamp())),'type':'interactive',
           'context':{'id':run.provider_id},'interactive':{'type':'button_reply','button_reply':{'id':f'ppq:{run.token}:{run.step}:{index}','title':title}}}
    return post(f,{'entry':[{'changes':[{'field':'messages','value':{'messages':[m]}}]}]})


def test_signed_webhook_runs_existing_engine_to_completion_with_ai_off_and_no_ctwa(flows):
    f=flows;assert rule(f).status_code==200;assert master(f).status_code==200
    assert incoming(f).status_code==200
    with f[0][1]() as db:
        run=db.query(Run).one();cid=run.conversation_id
        assert run.flow_id==1 and run.status=='QUEUED' and db.query(Exec).one().run_id==run.id
        assert db.query(Ref).one().ctwa_clid is None and db.query(Receipt).one().ai_reply_generation is None
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert q.dispatch_one(f[0][1]);assert answer(f,cid).status_code==200
    assert q.dispatch_one(f[0][1]);assert answer(f,cid).status_code==200
    with f[0][1]() as db:
        run=db.query(Run).one();assert run.status=='COMPLETE' and len(run.answers)==2
        assert db.query(Exec).count()==1 and db.query(M).filter_by(direction='outbound').count()==2
    metrics=f[0][0].get('/crm/ad-flows/api').json()['rules'][0]
    assert metrics['accepted']==1 and metrics['failed']==0
    assert len(f[1])==2 and not f[0][3]


@pytest.mark.parametrize('mode',['unmapped','wrong','organic','post','invalid','missing','master_off','rule_off','engine_off'])
def test_no_accidental_trigger(flows,mode):
    f=flows
    if mode!='unmapped':rule(f,enabled=mode!='rule_off')
    if mode!='master_off':master(f)
    if mode=='engine_off':
        with f[0][1]() as db:db.get(Config,1).enabled=False;db.commit()
    response=incoming(f,aid='999' if mode=='wrong' else 'not-an-id' if mode=='invalid' else None if mode=='missing' else AD,source='post' if mode=='post' else 'ad',ref=mode!='organic',text='Bicycle enquiry' if mode=='organic' else 'Hello')
    assert response.status_code==200
    with f[0][1]() as db:assert db.query(Run).count()==0 and db.query(M).filter_by(direction='inbound').count()==1
    assert not f[1] and not f[0][3]


def test_duplicate_and_two_ad_enquiries_start_only_one_run(flows):
    f=flows;rule(f);master(f)
    assert incoming(f).status_code==200 and incoming(f).status_code==200
    assert incoming(f,mid='wamid.second').status_code==200
    with f[0][1]() as db:
        assert db.query(Run).count()==1 and db.query(Exec).filter_by(status='STARTED').count()==1
        assert db.query(Exec).count()==2 and db.query(Receipt).count()==2


@pytest.mark.parametrize('state',['archived','changed'])
def test_missing_or_changed_saved_flow_retains_inbound_and_records_failure(flows,state):
    f=flows;rule(f);master(f)
    with f[0][1]() as db:
        flow=db.get(Flow,1)
        if state=='archived':flow.archived=True
        else:flow.version+=1
        db.commit()
    assert incoming(f).status_code==200
    with f[0][1]() as db:
        e=db.query(Exec).one();assert e.status=='FAILED' and e.reason==('FLOW_UNAVAILABLE' if state=='archived' else 'FLOW_CHANGED')
        assert db.query(Run).count()==0 and db.query(Ref).count()==1


def test_first_policy_uses_crm_record_not_billing_window(flows):
    f=flows;rule(f);master(f)
    assert incoming(f,ref=False,mid='organic.first').status_code==200
    assert incoming(f,mid='later.ad').status_code==200
    with f[0][1]() as db:assert db.query(Exec).one().reason=='NOT_NEW' and db.query(Run).count()==0


def test_repeat_policy_active_flow_and_cooldown(flows):
    f=flows;rule(f,policy='REPEAT');master(f)
    incoming(f)
    with f[0][1]() as db:db.query(Run).one().status='COMPLETE';db.commit()
    incoming(f,mid='repeat.too-soon')
    with f[0][1]() as db:
        assert db.query(Exec).order_by(Exec.id.desc()).first().reason=='COOLDOWN'
        db.query(Exec).filter_by(status='STARTED').one().created_at-=timedelta(hours=25);db.commit()
    incoming(f,mid='repeat.allowed')
    with f[0][1]() as db:assert db.query(Run).count()==2
    incoming(f,mid='repeat.active')
    with f[0][1]() as db:assert db.query(Run).count()==2 and db.query(Exec).order_by(Exec.id.desc()).first().reason=='ACTIVE_FLOW'


def test_staff_assignment_requires_explicit_compatible_mapping(flows):
    f=flows;assign_rule(f[0]);rule(f);master(f);incoming(f)
    with f[0][1]() as db:assert db.query(Exec).one().reason=='STAFF_OWNED' and db.query(Run).count()==0
    assert rule(f,version=1,allow_assigned=True).status_code==200
    incoming(f,mid='assigned.allowed',phone='94770009998')
    with f[0][1]() as db:
        assert db.query(Run).count()==1
        assert db.query(C).filter_by(phone_number='94770009998').one().assigned_to_user_id==f[0][6]['quick-staff']


def test_takeover_stops_auto_run_and_master_off_pauses_future_sends(flows):
    f=flows;rule(f);master(f);incoming(f);assert q.dispatch_one(f[0][1])
    with f[0][1]() as db:run=db.query(Run).one();cid=run.conversation_id
    assert f[0][0].post(f'/crm/whatsapp/api/conversations/{cid}/takeover').status_code==200
    with f[0][1]() as db:assert db.query(Run).one().status=='STOPPED'
    assert not q.dispatch_one(f[0][1]) and len(f[1])==1
    incoming(f,mid='another.customer',phone='94770009998');assert master(f,False).status_code==200
    assert not q.dispatch_one(f[0][1])
    with f[0][1]() as db:assert db.query(Run).order_by(Run.id.desc()).first().status=='PAUSED'


def test_mapping_delete_pauses_and_keeps_history(flows):
    f=flows;rule(f);master(f);incoming(f)
    response=f[0][0].post('/crm/ad-flows/rules/'+AD+'/delete',json={'version':1},headers={'X-CSRF-Token':f[0][5]})
    assert response.status_code==200
    with f[0][1]() as db:assert db.query(Run).one().status=='PAUSED' and db.query(Exec).count()==1 and db.query(AdFlowRule).one().deleted
    assert not q.dispatch_one(f[0][1])


def test_customer_stop_blocks_repeats_until_explicit_customer_optin(flows):
    f=flows;rule(f,policy='REPEAT');master(f);incoming(f);incoming(f,ref=False,mid='stop',text='STOP')
    with f[0][1]() as db:
        c=db.query(C).filter_by(phone_number='94770009999').one();cid=c.id
        assert c.ad_flow_opted_out and db.query(Run).one().status=='STOPPED'
    incoming(f,mid='repeat.optout')
    with f[0][1]() as db:assert db.query(Exec).order_by(Exec.id.desc()).first().reason=='OPTED_OUT'
    assert incoming(f,ref=False,mid='customer.restart',text='START AD FLOWS').status_code==200
    with f[0][1]() as db:assert not db.get(C,cid).ad_flow_opted_out


def test_global_ai_on_cannot_compete_with_active_ad_flow(flows,monkeypatch):
    f=flows;rule(f);master(f)
    with f[0][1]() as db:db.get(GlobalAIReplyControl,1).enabled=True;db.commit()
    monkeypatch.setattr(main,'generate_customer_reply',lambda *a:pytest.fail('AI must not compete with mapped flow'))
    incoming(f);incoming(f,ref=False,mid='organic.during-flow')
    with f[0][1]() as db:
        assert db.get(GlobalAIReplyControl,1).enabled is True
        assert all(r.ai_reply_generation is None for r in db.query(Receipt))
    assert not f[0][3]


def test_test_rule_is_read_only_and_admin_controls_are_protected(flows):
    f=flows;rule(f);master(f)
    r=f[0][0].post('/crm/ad-flows/rules/'+AD+'/test',json={},headers={'X-CSRF-Token':f[0][5]});assert r.status_code==200 and r.json()['eligible']
    with f[0][1]() as db:assert db.query(Run).count()==0 and db.query(Exec).count()==0
    assert not f[1]
    assert f[0][0].post('/crm/ad-flows/control',json={'enabled':False,'version':1}).status_code==403
    f[0][4]('quick-manager');assert f[0][0].get('/crm/ad-flows/api').status_code==403
    assert rule(f,version=1).status_code==403


def test_concurrent_distinct_ad_messages_start_at_most_once(flows):
    from concurrent.futures import ThreadPoolExecutor
    f=flows;rule(f,policy='REPEAT');master(f)
    # Pre-existing AI-mode thread avoids testing the older thread-creation retry behavior.
    with f[0][1]() as db:db.add(C(phone_number='94770009999',mode='AI'));db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda mid:incoming(f,mid=mid),['concurrent.1','concurrent.2']))
    assert all(r.status_code==200 for r in results)
    with f[0][1]() as db:assert db.query(Run).count()==1 and db.query(Exec).filter_by(status='STARTED').count()==1


def test_mapping_toggle_disables_without_revalidating_archived_flow(flows):
    f=flows;rule(f);master(f);incoming(f)
    with f[0][1]() as db:db.get(Flow,1).archived=True;db.commit()
    response=f[0][0].post('/crm/ad-flows/rules/'+AD+'/toggle',json={'enabled':False,'version':1},headers={'X-CSRF-Token':f[0][5]})
    assert response.status_code==200
    with f[0][1]() as db:assert db.query(Run).one().status=='PAUSED'


def test_ai_send_boundary_rechecks_an_already_admitted_job(flows):
    import global_ai_control as control
    f=flows;rule(f);master(f)
    # Capture an earlier admitted message before the mapped flow starts.
    with f[0][1]() as db:
        state=db.get(GlobalAIReplyControl,1);state.enabled=True
        db.add(Receipt(message_id='older.job',sender_phone='94770009999',message_type='text',received_at=datetime.utcnow(),ai_reply_generation=state.generation));db.commit()
    job=control.message_job(f[0][1],'older.job','94770009999');assert job is not None
    incoming(f)
    token=control._job.set(job)
    try:
        with pytest.raises(control.AutoReplyBlocked):
            with control.send_boundary(require_job=True):pytest.fail('Provider send must be blocked')
    finally:control._job.reset(token)


def test_migration_defaults_off_and_preserves_conversation_and_runs(flows):
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from pathlib import Path
    from sqlalchemy import text
    f=flows
    from models import AdFlowControl,AdFlowRule,AdFlowExecution,AdFlowAudit
    with f[0][1]() as db:
        AdFlowAudit.__table__.drop(db.get_bind());AdFlowExecution.__table__.drop(db.get_bind());AdFlowRule.__table__.drop(db.get_bind());AdFlowControl.__table__.drop(db.get_bind())
        db.execute(text('ALTER TABLE whatsapp_conversations DROP COLUMN ad_flow_opted_out'));db.commit()
    cfg=AlembicConfig(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(cfg,'20261109_37');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with f[0][1]() as db:
        assert db.get(AdFlowControl,1).enabled is False and db.query(AdFlowRule).count()==0
        assert db.query(C).count()==2 and db.get(GlobalAIReplyControl,1).enabled is False


def test_concurrent_same_webhook_has_one_receipt_execution_and_run(flows):
    from concurrent.futures import ThreadPoolExecutor
    f=flows;rule(f);master(f)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:incoming(f),[1,2]))
    assert all(r.status_code==200 for r in results)
    with f[0][1]() as db:assert db.query(Run).count()==1 and db.query(Exec).count()==1 and db.query(Receipt).count()==1


def test_unknown_ai_step_is_rejected_without_enabling_generative_ai(flows):
    f=flows;rule(f);master(f)
    with f[0][1]() as db:
        db.get(Flow,1).definition={'title':'Invalid AI flow','steps':[{'key':'ai','type':'ai','body':'Generate text','options':['Go']}]};db.commit()
    assert incoming(f).status_code==200
    with f[0][1]() as db:
        assert db.query(Exec).one().reason=='INVALID_FLOW' and db.query(Run).count()==0
        assert db.get(GlobalAIReplyControl,1).enabled is False


def test_untrusted_interactive_title_is_not_a_stop_command(flows):
    f=flows;rule(f);master(f);incoming(f);q.dispatch_one(f[0][1])
    with f[0][1]() as db:cid=db.query(Run).one().conversation_id
    assert answer(f,cid,title='STOP').status_code==200
    with f[0][1]() as db:
        assert db.query(Run).one().status=='QUEUED' and not db.get(C,cid).ad_flow_opted_out
        assert db.query(Run).one().answers['gender']=='Girl'


def test_start_metrics_distinguish_queue_from_transport_acceptance(flows,monkeypatch):
    f=flows;rule(f);master(f);incoming(f)
    data=f[0][0].get('/crm/ad-flows/api').json()['rules'][0];assert data['starts']==1 and data['accepted']==0 and data['failed']==0
    monkeypatch.setattr(q,'send_buttons',lambda *a,**kw:None);q.dispatch_one(f[0][1])
    data=f[0][0].get('/crm/ad-flows/api').json()['rules'][0];assert data['accepted']==0 and data['failed']==1 and data['status']=='UNCERTAIN'
