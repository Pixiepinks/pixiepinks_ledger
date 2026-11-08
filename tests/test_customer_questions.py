"""All transports mocked. No real customers, production database or AI calls."""
import json,hmac,hashlib,secrets
from datetime import datetime,timedelta
import pytest
from sqlalchemy import select
import main
import customer_questions as q
from models import CustomerQuestionConfig as Config,CustomerQuestionRun as Run,CustomerQuestionEvent as Event
from models import WhatsAppConversation as C,WhatsAppConversationMessage as M,GlobalAIReplyControl,MarketingConsent
from test_whatsapp_quick_messages import inbox

BASE='/crm/quick-messages/customer-questions/api'

def settings(client,csrf,**changes):
    p=client.get(BASE).json();p.pop('csrf_token');p.update(changes)
    return client.post(BASE,json=p,headers={'X-CSRF-Token':csrf})

def act(client,cid,csrf,action='start',version=None):
    return client.post(f'/crm/whatsapp/api/conversations/{cid}/customer-questions',json={'action':action,'version':version},headers={'X-CSRF-Token':csrf})

@pytest.fixture
def questions(inbox,monkeypatch):
    client,sessions,ids,sends,login,csrf=inbox;sessions.configure(autoflush=False)
    with sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    calls=[]
    monkeypatch.setattr(q,'send_buttons',lambda phone,body,buttons:calls.append((phone,body,buttons)) or 'wamid.questions.'+str(len(calls)))
    monkeypatch.setattr(main.settings,'META_APP_SECRET','question-fixture')
    return (*inbox,calls)

def reply(v,*,index=0,mid=None,sender=None,token=None,context=True,reply_type="button_reply"):
    client,sessions,ids,_,_,_,calls=v
    with sessions() as db:
        r=q.latest(db,ids[0]);phone=db.get(C,ids[0]).phone_number
        button=token or f'ppq:{r.token}:{r.step}:{index}';provider=r.provider_id
    message={'id':mid or 'wamid.reply.'+secrets.token_hex(8),'from':sender or phone,'type':'interactive',
        'timestamp':str(int(datetime.utcnow().timestamp())),
        'interactive':{'type':reply_type,reply_type:{'id':button,'title':'Untrusted title'}}}
    if context:message['context']={'id':provider}
    raw=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}).encode()
    return client.post('/webhook',content=raw,headers={'Content-Type':'application/json',
        'X-Hub-Signature-256':'sha256='+hmac.new(b'question-fixture',raw,hashlib.sha256).hexdigest()})

def enable(v):
    client,_,ids,_,_,csrf,_=v
    assert settings(client,csrf,enabled=True).status_code==200
    response=act(client,ids[0],csrf);assert response.status_code==200,response.text
    return response.json()['run']

def test_defaults_off_no_enrollment_permissions_csrf_and_validation(questions):
    client,sessions,ids,sends,login,csrf,calls=questions
    assert client.get(BASE).json()['enabled'] is False
    assert act(client,ids[0],csrf).status_code==409
    assert client.post(BASE,json={}).status_code==403
    for definition in [dict(q.DEFAULT,steps=[]),dict(q.DEFAULT,steps=[{'key':'age','body':'x','options':['a'*21]}]),dict(q.DEFAULT,steps=[{'key':'age','body':'x','options':['a']*4}])]:
        assert settings(client,csrf,definition=definition).status_code==422
    login('quick-staff')
    assert client.get(BASE).status_code==403
    assert act(client,ids[1],csrf).status_code in {403,404}
    with sessions() as db:
        assert db.query(Run).count()==0 and db.get(GlobalAIReplyControl,1).enabled is False
    assert not calls and not sends

def test_signed_sequence_durable_duplicates_and_preferences_not_consent(questions):
    v=questions;client,sessions,ids,sends,_,csrf,calls=v;enable(v)
    assert act(client,ids[0],csrf).status_code==409
    assert q.dispatch_one(sessions) and len(calls)==1
    assert calls[0][2][0]['title']=='Girl'
    with sessions() as db:
        old=q.latest(db,ids[0]);oldtoken=f'ppq:{old.token}:0:0';snapshot=old.definition
    assert settings(client,csrf,definition={**q.DEFAULT,'title':'Changed later'}).status_code==200
    assert reply(v,mid='wamid.same').status_code==200
    assert reply(v,mid='wamid.same').status_code==200
    with sessions() as db:
        run=q.latest(db,ids[0]);assert run.step==1 and run.answers=={'gender':'Girl'} and run.definition==snapshot
        assert db.query(Event).filter_by(action='ANSWER').count()==1
    assert q.dispatch_one(sessions) and len(calls)==2
    assert reply(v,token=oldtoken).status_code==200
    assert reply(v,index=2).status_code==200
    assert not q.dispatch_one(sessions)
    with sessions() as db:
        run=q.latest(db,ids[0]);assert run.status=='COMPLETE' and run.answers=={'gender':'Girl','age':'7+ years'}
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert not db.query(MarketingConsent).filter_by(status='OPTED_IN').count()
        assert db.query(M).filter_by(response_kind='customer_question',analytics_actor_kind='automatic').count()==2
        assert db.query(M).filter_by(response_kind='question_reply').count()==3
    assert not sends

@pytest.mark.parametrize('bad', ['sender','context','token','index'])
def test_replies_are_bound_to_sender_question_and_context(questions,bad):
    enable(questions);q.dispatch_one(questions[1])
    kwargs={'sender':'94770012302'} if bad=='sender' else {'context':False} if bad=='context' else {'token':'ppq:forged:0:0'} if bad=='token' else {'index':8}
    assert reply(questions,**kwargs).status_code==200
    with questions[1]() as db:assert q.latest(db,questions[2][0]).step==0

def test_pause_resume_stop_off_and_stale_actions(questions):
    v=questions;client,sessions,ids,_,_,csrf,calls=v;enable(v);q.dispatch_one(sessions)
    def current():return client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/customer-questions').json()['run']
    r=current();assert act(client,ids[0],csrf,'pause',r['version']).status_code==200
    assert reply(v).status_code==200
    assert current()['status']=='PAUSED'
    assert act(client,ids[0],csrf,'resume',r['version']).status_code==409
    assert act(client,ids[0],csrf,'resume',current()['version']).status_code==200
    assert not q.dispatch_one(sessions) and len(calls)==1
    assert settings(client,csrf,enabled=False).status_code==200
    assert reply(v).status_code==200 and current()['step']==0
    assert act(client,ids[0],csrf,'stop',current()['version']).status_code==200
    assert not q.dispatch_one(sessions)

def test_timeout_and_crashed_claim_are_never_retried(questions,monkeypatch):
    v=questions;enable(v);monkeypatch.setattr(q,'send_buttons',lambda *a:None)
    assert q.dispatch_one(v[1]);assert not q.dispatch_one(v[1])
    with v[1]() as db:
        run=q.latest(db,v[2][0]);assert run.status=='UNCERTAIN';run.status='SENDING';db.commit()
    assert not q.dispatch_one(v[1])

def test_window_rechecked_and_config_version_conflict(questions):
    v=questions;enable(v)
    with v[1]() as db:db.get(C,v[2][0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert q.dispatch_one(v[1]) and not v[-1]
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='WINDOW_CLOSED'
    assert settings(v[0],v[5],version=0).status_code==409


def test_question_replies_never_enter_consent_or_ai_and_rollback_failure(questions,monkeypatch):
    import marketing_consent_service
    from sqlalchemy.exc import SQLAlchemyError
    v=questions;enable(v);q.dispatch_one(v[1])
    def forbidden(*a,**kw):raise AssertionError('Question answers must not enter AI or consent processing')
    monkeypatch.setattr(marketing_consent_service,'inbound',forbidden)
    monkeypatch.setattr(main,'_enqueue_ai_reply',forbidden)
    original=q.inbound
    def fail(*a):raise SQLAlchemyError('fixture failure')
    monkeypatch.setattr(q,'inbound',fail)
    assert reply(v,mid='wamid.retry.atomic').status_code==503
    with v[1]() as db:assert db.query(M).filter_by(whatsapp_message_id='wamid.retry.atomic').count()==0
    monkeypatch.setattr(q,'inbound',original)
    assert reply(v,mid='wamid.retry.atomic').status_code==200
    assert q.dispatch_one(v[1])
    data=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}').json()
    buttons=[m['interactive_buttons'] for m in data['messages'] if m['source']=='customer_question']
    assert buttons==[['Girl','Boy','Any'],['Under 3 years','3–6 years','7+ years']]
    assert 'ppq:' not in json.dumps(data)


def test_transport_payload_and_ambiguous_response_never_retry(monkeypatch):
    import httpx
    from settings import settings as env
    monkeypatch.setattr(env,'META_WHATSAPP_ACCESS_TOKEN','fixture-token')
    monkeypatch.setattr(env,'META_WHATSAPP_PHONE_NUMBER_ID','fixture-phone')
    captured=[]
    def post(url,**kwargs):
        captured.append(kwargs['json'])
        return httpx.Response(200,json={'messages':[{'id':'wamid.mock'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    assert q.send_buttons('94770012301','Question?',[{'id':'ppq:token:0:0','title':'Girl'}])=='wamid.mock'
    assert captured[0]['interactive']['action']['buttons']==[{'type':'reply','reply':{'id':'ppq:token:0:0','title':'Girl'}}]
    def timeout(*a,**kw):captured.append('timeout');raise httpx.ReadTimeout('fixture')
    monkeypatch.setattr(httpx,'post',timeout)
    assert q.send_buttons('94770012301','Question?',[{'id':'ppq:token:0:0','title':'Girl'}]) is None
    assert captured.count('timeout')==1


def test_customer_stop_cancels_queued_questions(questions):
    v=questions;enable(v)
    assert main._store_conversation_message('94770012301','inbound','STOP',response_kind='customer')=='stored'
    assert not q.dispatch_one(v[1]) and not v[-1]
    with v[1]() as db:
        assert q.latest(db,v[2][0]).status=='STOPPED'
        assert db.query(Event).filter_by(action='STOP',actor='Customer').count()==1


def test_server_worker_advances_without_browser_and_stops_cleanly(questions,monkeypatch):
    from threading import Thread,Event as Signal
    v=questions;enable(v);sent=Signal();stop=Signal()
    def send(*args):sent.set();return 'wamid.worker.fixture'
    monkeypatch.setattr(q,'send_buttons',send)
    thread=Thread(target=q.worker,args=(v[1],stop),daemon=True);thread.start()
    try:assert sent.wait(5)
    finally:stop.set();thread.join(timeout=5)
    assert not thread.is_alive()
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='WAITING'


def test_list_selection_continues_sequence_and_rejects_wrong_format(questions,monkeypatch):
    v=questions;client,sessions,ids,_,_,csrf,calls=v
    steps=[{'key':'age','type':'list','list_button':'Choose age','body':'Child age?',
        'options':[f'{i} years' for i in range(1,11)]},q.DEFAULT['steps'][0]]
    assert settings(client,csrf,definition={'title':'Age first','steps':steps},enabled=True).status_code==200
    captured=[]
    def transport(phone,body,buttons,**kw):captured.append((buttons,kw));return 'wamid.list.'+str(len(captured))
    monkeypatch.setattr(q,'send_buttons',transport)
    assert act(client,ids[0],csrf).status_code==200 and q.dispatch_one(sessions)
    assert len(captured[0][0])==10 and captured[0][1]=={'list_button':'Choose age'}
    assert reply(v,index=9).status_code==200  # Button payload cannot answer a list question.
    with sessions() as db:assert q.latest(db,ids[0]).step==0
    assert reply(v,index=9,reply_type='list_reply',mid='wamid.list.answer').status_code==200
    assert reply(v,index=9,reply_type='list_reply',mid='wamid.list.answer').status_code==200
    with sessions() as db:
        run=q.latest(db,ids[0]);assert run.answers=={'age':'10 years'} and run.step==1
        assert db.query(Event).filter_by(action='ANSWER').count()==1
    assert q.dispatch_one(sessions)
    assert captured[1][1]=={}  # Next step retains legacy reply buttons.
    assert reply(v,reply_type='list_reply').status_code==200
    with sessions() as db:assert q.latest(db,ids[0]).step==1
    assert reply(v,index=1).status_code==200
    with sessions() as db:
        assert q.latest(db,ids[0]).answers=={'age':'10 years','gender':'Boy'}
        assert q.latest(db,ids[0]).status=='COMPLETE'
        assert db.get(GlobalAIReplyControl,1).enabled is False
    payload=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()
    assert any(len(m.get('interactive_buttons',[]))==10 for m in payload['messages'])
    assert 'ppq:' not in json.dumps(payload)


@pytest.mark.parametrize('step',[
    {'type':'list','options':['x']*11}, {'type':'list','options':['a'*25]},
    {'type':'list','list_button':'x'*21}, {'type':'list','list_button':''},
    {'type':'other'}, {'type':[]}, {'type':'buttons','options':['1','2','3','4']},
    {'type':'buttons','list_button':'Choose'}])
def test_list_validation_limits(questions,step):
    v=questions
    definition={'title':'Validation','steps':[{**q.DEFAULT['steps'][0],**step}]}
    assert settings(v[0],v[5],definition=definition).status_code==422


def test_list_transport_cloud_api_payload(monkeypatch):
    import httpx
    from settings import settings as env
    monkeypatch.setattr(env,'META_WHATSAPP_ACCESS_TOKEN','fixture-token')
    monkeypatch.setattr(env,'META_WHATSAPP_PHONE_NUMBER_ID','fixture-phone')
    sent=[]
    def post(url,**kwargs):sent.append(kwargs['json']);return httpx.Response(200,json={'messages':[{'id':'wamid.list.mock'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    rows=[{'id':f'ppq:token:0:{i}','title':f'Age {i}'} for i in range(10)]
    assert q.send_buttons('94770012301','Choose age',rows,list_button='Age options')=='wamid.list.mock'
    assert sent[0]['interactive']=={'type':'list','body':{'text':'Choose age'},'action':{
        'button':'Age options','sections':[{'rows':rows}]}}
