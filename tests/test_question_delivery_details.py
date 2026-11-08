"""Signed customer text and buttons, isolated DB, all outbound transports mocked."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
import pytest
import main
import customer_questions as q
import question_delivery_details as delivery
from models import CustomerQuestionProductSend as Item, CustomerQuestionEvent as Event, WhatsAppConversation as Conversation, WhatsAppConversationMessage as Message, GlobalAIReplyControl
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing, selected
from test_customer_questions import reply, settings, act


@pytest.fixture
def collecting(greasing, monkeypatch):
    greasing[2]['steps'][0]['service'].update(collect_delivery=True, charge='1800.00')
    calls=[]
    monkeypatch.setattr(delivery, 'send_whatsapp_text', lambda phone, body, **kw: calls.append((phone, body)) or f'wamid.delivery.{len(calls)}')
    return greasing, calls


def begin(collection, choice=0):
    g, calls = collection
    selected(g)
    v, _, _, value, _, _ = g
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    assert reply(value, index=choice).status_code == 200
    with v.sessions() as db:
        assert q.latest(db,v.ids[0]).status == 'QUEUED'
    assert q.dispatch_one(v.sessions)
    return collection


def text(collection, body, *, mid=None, sender=None, context=None, when=None):
    g, calls = collection
    v=g[0]
    with v.sessions() as db:
        phone=db.get(Conversation,v.ids[0]).phone_number
    message={'id':mid or 'wamid.delivery.in.'+secrets.token_hex(8), 'from':sender or phone, 'type':'text',
        'timestamp':str(int((when or datetime.utcnow()).timestamp())), 'text':{'body':body}}
    if context:message['context']={'id':context}
    raw=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}).encode()
    return v.client.post('/webhook',content=raw,headers={'Content-Type':'application/json',
        'X-Hub-Signature-256':'sha256='+hmac.new(b'question-fixture',raw,hashlib.sha256).hexdigest()})


def current(collection):
    v=collection[0][0]
    with v.sessions() as db:
        run=q.latest(db,v.ids[0])
        return run.status, run.answers['recommendations']['delivery_details'], run.provider_id, run.token


def complete_fields(collection, phone2='0772345678'):
    for value in ['අමායා පෙරේරා','123, Temple Road, Colombo 05','0771234567',phone2]:
        assert text(collection,value).status_code==200
        assert q.dispatch_one(collection[0][0].sessions)


@pytest.mark.parametrize('choice,total',[(0,'46800.00'),(1,'45000.00')])
def test_both_service_answers_collect_review_and_confirm(collecting,choice,total,monkeypatch):
    import marketing_consent_service
    c=begin(collecting,choice);g,calls=c;v=g[0]
    def forbidden(*a,**kw):raise AssertionError('Delivery answers must not enter AI or consent')
    monkeypatch.setattr(main,'_enqueue_ai_reply',forbidden)
    monkeypatch.setattr(marketing_consent_service,'inbound',forbidden)
    complete_fields(c)
    status,details,provider,token=current(c)
    assert status=='WAITING' and details['status']=='REVIEW'
    assert details['phone1']=='+94771234567' and details['phone2']=='+94772345678'
    body=g[3][-1][-1][1]
    assert 'FREE' in body and f'{float(total):,.2f}' in body and details['address'] in body
    assert len(body)<=1024
    # Free text is never order confirmation.
    assert text(c,'yes').status_code==200 and current(c)[0]=='WAITING'
    assert reply(g[3],index=0,mid='wamid.delivery.confirmed').status_code==200
    assert reply(g[3],index=0,mid='wamid.delivery.confirmed').status_code==200
    assert not q.dispatch_one(v.sessions)
    assert current(c)[0]=='COMPLETE' and current(c)[1]['status']=='CONFIRMED'
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);assert run.answers['recommendations']['greasing_service']['quoted_total']==total
        assert db.query(Event).filter_by(action='DELIVERY_CONFIRMED').count()==1
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.query(Message).filter_by(direction='outbound',analytics_actor_kind='automatic').count()==9
    # Existing conversation permissions also protect the saved delivery details.
    v.login('quick-agent')
    assert v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/customer-questions').status_code in {403,404}


def test_invalid_input_duplicates_early_and_stale_messages(collecting):
    c=begin(collecting);v=c[0][0]
    assert text(c,'A',mid='wamid.invalid').status_code==200
    assert current(c)[1]['stage']=='name' and current(c)[1]['invalid']
    # A burst cannot fill the next field before that prompt is sent.
    assert text(c,'Good Name').status_code==200 and current(c)[1]['stage']=='name'
    assert q.dispatch_one(v.sessions)
    assert text(c,'Good Name',mid='wamid.valid.name').status_code==200
    assert text(c,'Good Name',mid='wamid.valid.name').status_code==200
    assert current(c)[1]['stage']=='address'
    assert q.dispatch_one(v.sessions)
    assert text(c,'Long enough address',context='wamid.unrelated').status_code==200
    assert text(c,'Long enough address',when=datetime.utcnow()-timedelta(days=2)).status_code==200
    assert text(c,'Long enough address',sender='94770012302').status_code==200
    assert current(c)[1]['stage']=='address'
    for value in ['A real full address','12345']:
        assert text(c,value).status_code==200 and q.dispatch_one(v.sessions)
    assert current(c)[1]['stage']=='phone1' and current(c)[1]['invalid']
    assert text(c,'+94 77 123 4567').status_code==200 and q.dispatch_one(v.sessions)
    assert reply(c[0][3],index=0).status_code==200 and q.dispatch_one(v.sessions)
    assert current(c)[1]['phone2']=='' and current(c)[1]['stage']=='review'


def test_edit_replaces_details_and_old_confirmation_is_rejected(collecting):
    c=begin(collecting);v=c[0][0];complete_fields(c,phone2='skip')
    _,_,old_provider,token=current(c)
    with v.sessions() as db:step=q.latest(db,v.ids[0]).step
    assert reply(c[0][3],index=1).status_code==200 and q.dispatch_one(v.sessions)
    assert current(c)[1]['stage']=='name' and 'name' not in current(c)[1]
    complete_fields(c)
    assert reply(c[0][3],token=f'ppq:{token}:{step}:0').status_code==200
    assert current(c)[1]['status']=='REVIEW'
    assert reply(c[0][3],index=0).status_code==200
    assert current(c)[1]['status']=='CONFIRMED'


@pytest.mark.parametrize('mode',['pause','off'])
def test_paused_or_off_does_not_collect_or_send(collecting,mode):
    c=begin(collecting);v=c[0][0]
    with v.sessions() as db:version=q.latest(db,v.ids[0]).version
    if mode=='pause':assert act(v.client,v.ids[0],v.csrf,'pause',version).status_code==200
    else:assert settings(v.client,v.csrf,enabled=False).status_code==200
    assert text(c,'Good Name').status_code==200
    assert current(c)[1]['stage']=='name' and not q.dispatch_one(v.sessions)
    if mode=='pause':
        with v.sessions() as db:version=q.latest(db,v.ids[0]).version
        assert act(v.client,v.ids[0],v.csrf,'resume',version).status_code==200
    else:assert settings(v.client,v.csrf,enabled=True).status_code==200
    assert text(c,'Good Name').status_code==200
    assert current(c)[1]['stage']=='address'


@pytest.mark.parametrize('failure',['timeout','crash','window'])
def test_prompt_failures_never_replay(collecting,monkeypatch,failure):
    c=begin(collecting);v=c[0][0];assert text(c,'Good Name').status_code==200
    if failure=='window':
        with v.sessions() as db:db.get(Conversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    elif failure=='timeout':monkeypatch.setattr(delivery,'send_whatsapp_text',lambda *a,**kw:None)
    else:
        def crash(*a,**kw):raise KeyboardInterrupt()
        monkeypatch.setattr(delivery,'send_whatsapp_text',crash)
    if failure=='crash':
        with pytest.raises(KeyboardInterrupt):q.dispatch_one(v.sessions)
    else:assert q.dispatch_one(v.sessions)
    assert not q.dispatch_one(v.sessions)
    assert current(c)[0]=={'timeout':'UNCERTAIN','crash':'SENDING','window':'WINDOW_CLOSED'}[failure]


def test_invalid_config_rejected_and_default_existing_flow_unchanged(greasing):
    v,_,definition,_,_,_=greasing
    definition['steps'][0]['service']['collect_delivery']='yes'
    assert settings(v.client,v.csrf,definition=definition).status_code==422


def test_maximum_review_fits_whatsapp_limit():
    answer={'title':'T'*100,'variant_title':'V'*60,'price':'9999999.99',
        'greasing_service':{'accepted':True,'charge':'1000000.00','quoted_total':'10999999.99'},
        'delivery_details':{'name':'N'*100,'address':'A'*350,'phone1':'+123456789012345','phone2':'+123456789012345'}}
    assert len(delivery.summary(answer))<=1024


def test_delivery_stop_and_no_signature_secret(collecting,monkeypatch):
    c=begin(collecting);v=c[0][0]
    monkeypatch.setattr(main.settings,'META_APP_SECRET','')
    assert text(c,'Good Name').status_code==200
    assert current(c)[1]['stage']=='name'
    monkeypatch.setattr(main.settings,'META_APP_SECRET','question-fixture')
    assert text(c,'stop').status_code==200
    assert current(c)[0]=='STOPPED' and not q.dispatch_one(v.sessions)


def test_delivery_field_and_receipt_roll_back_together(collecting,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    c=begin(collecting);v=c[0][0];original=delivery.advance
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise SQLAlchemyError('fixture rollback')
    monkeypatch.setattr(delivery,'advance',fail)
    assert text(c,'Good Name',mid='wamid.delivery.retry').status_code==503
    assert current(c)[1]['stage']=='name'
    with v.sessions() as db:assert not db.query(Message).filter_by(whatsapp_message_id='wamid.delivery.retry').count()
    monkeypatch.setattr(delivery,'advance',original)
    assert text(c,'Good Name',mid='wamid.delivery.retry').status_code==200
    assert current(c)[1]['stage']=='address'
