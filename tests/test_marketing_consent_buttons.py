"""Consent buttons: all provider traffic mocked, isolated fixture databases."""
import hashlib
import hmac
import json
from datetime import datetime,timedelta
from uuid import uuid4
import pytest
import httpx
import main
import whatsapp_service
import marketing_consent_buttons as buttons
import marketing_consent_service as service
from models import MarketingConsent as Consent,MarketingConsentRequest as Request,MarketingConsentEvent as Event,WhatsAppConversation,WhatsAppConversationMessage as Message,ProcessedWhatsAppMessage,GlobalAIReplyControl
from test_marketing_consent import consent,send_request,incoming,action
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def button_consent(consent,monkeypatch):
    v=consent
    monkeypatch.setattr(main.settings,'META_APP_SECRET','fixture-meta-secret')
    v.button_sends=[]
    monkeypatch.setattr(main,'send_whatsapp_consent_buttons',lambda phone,body,items:v.button_sends.append((phone,body,items)) or 'wamid.button.'+str(len(v.button_sends)))
    return v


def prepare(v,language='si',**extra):
    return v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/marketing-consent/request',
        json={'key':str(uuid4()),'language':language,'format':'buttons',**extra},headers={'X-CSRF-Token':v.csrf})


def sent(v):
    response=prepare(v);assert response.status_code==200,response.text
    preview=response.json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        req=db.get(Request,preview['request_id']);provider=req.whatsapp_message_id
    return preview,provider


def reply(v,preview,provider,choice=0,*,sender=None,context=True,button_id=None,title=None,mid=None,secret='fixture-meta-secret',timestamp=None):
    if sender is None:
        with v.sessions() as db:sender=db.get(WhatsAppConversation,v.ids[0]).phone_number
    item=preview['buttons'][choice]
    message={'id':mid or 'wamid.reply.'+uuid4().hex,'from':sender,'type':'interactive',
        'timestamp':str(int((datetime.utcnow()+timedelta(seconds=2)).timestamp())) if timestamp is None else timestamp,
        'interactive':{'type':'button_reply','button_reply':{'id':button_id or item['id'],'title':title or item['title']}}}
    if context:message['context']={'id':provider if context is True else context}
    raw=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}).encode()
    signature='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return v.client.post('/webhook',content=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':signature})


@pytest.mark.parametrize('language',['si','en','ta'])
def test_previews_are_durable_versioned_and_never_sent_automatically(button_consent,language):
    v=button_consent;data=prepare(v,language).json()
    assert data['text']==buttons.BODY[language] and data['version']==buttons.VERSION+language
    assert [b['title'] for b in data['buttons']]==['Yes, I agree','No, thanks']
    assert len({b['id'] for b in data['buttons']})==2 and not v.button_sends and not v.sends
    with v.sessions() as db:
        req=db.get(Request,data['request_id']);assert buttons.configuration(db,req)==data['buttons']
        assert db.get(Consent,v.contact).status=='NOT_REQUESTED'
    retry=prepare(v,language,key=data['key']).json();assert retry['buttons']==data['buttons']
    assert send_request(v,data).status_code==200
    assert send_request(v,data).status_code==200
    assert len(v.button_sends)==1 and not v.sends


def test_signed_agreement_is_atomic_eligible_and_idempotent(button_consent):
    v=button_consent;preview,provider=sent(v);mid='wamid.button.agreement'
    assert reply(v,preview,provider,mid=mid).status_code==200
    assert reply(v,preview,provider,mid=mid).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_IN' and service.eligibility(db,row)['eligible']
        event=db.query(Event).filter_by(event_type='CONSENT_GRANTED').one()
        assert event.evidence_text=='Yes, I agree' and json.loads(event.reason)['button_id']==preview['buttons'][0]['id']
        assert event.wording==preview['text'] and event.wording_version==preview['version']
        assert db.query(ProcessedWhatsAppMessage).filter_by(message_id=mid).count()==1
        assert db.query(Message).filter_by(whatsapp_message_id=mid).one().response_kind=='customer_consent_button'
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert len(v.button_sends)==1 and not v.sends


def test_decline_and_later_withdrawal_always_suppress(button_consent):
    v=button_consent;preview,provider=sent(v)
    assert reply(v,preview,provider).status_code==200
    assert reply(v,preview,provider,choice=1).status_code==200
    assert reply(v,preview,provider).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_OUT' and not service.eligibility(db,row)['eligible']
    assert prepare(v).status_code==409


@pytest.mark.parametrize('case',['token','title','context','no_context','recipient','expired','identity','before_sent'])
def test_forged_stale_cross_recipient_and_incomplete_responses_do_not_grant(button_consent,case):
    v=button_consent;preview,provider=sent(v);kwargs={}
    if case=='token':kwargs['button_id']=preview['buttons'][0]['id'].replace(':yes',':forged')
    if case=='title':kwargs['title']='YES'
    if case=='context':kwargs['context']='wamid.unrelated'
    if case=='no_context':kwargs['context']=False
    if case=='recipient':
        with v.sessions() as db:kwargs['sender']=db.get(WhatsAppConversation,v.ids[1]).phone_number
    if case=='before_sent':kwargs['timestamp']=str(int((datetime.utcnow()-timedelta(days=1)).timestamp()))
    if case in {'expired','identity'}:
        with v.sessions() as db:
            if case=='expired':db.get(Request,preview['request_id']).expires_at=datetime.utcnow()-timedelta(seconds=1)
            else:db.get(Consent,v.contact).identity_conflict=True
            db.commit()
    assert reply(v,preview,provider,**kwargs).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='PENDING' and not service.eligibility(db,row)['eligible']
        if case in {'context','no_context','identity'}:assert row.needs_review
    assert not v.sends and len(v.button_sends)==1


def test_stop_precedes_button_and_no_automatic_reply_when_ai_on(button_consent):
    v=button_consent;preview,provider=sent(v);incoming(v,'STOP')
    with v.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=True;db.commit()
    assert reply(v,preview,provider).status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_OUT'
    assert not v.sends and len(v.button_sends)==1


def test_signature_required_and_no_configuration_change(button_consent,monkeypatch):
    v=button_consent;preview,provider=sent(v)
    assert reply(v,preview,provider,secret='incorrect').status_code==403
    monkeypatch.setattr(main.settings,'META_APP_SECRET','')
    assert reply(v,preview,provider).status_code==503
    assert prepare(v).status_code==409
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING'


def test_uncertain_send_is_not_retried_or_sent_as_text(button_consent,monkeypatch):
    v=button_consent;preview=prepare(v).json();calls=[]
    monkeypatch.setattr(main,'send_whatsapp_consent_buttons',lambda *a:calls.append(a) or None)
    assert send_request(v,preview).status_code==502
    assert send_request(v,preview).status_code==409
    assert len(calls)==1 and not v.sends and prepare(v).status_code==409
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='NOT_REQUESTED'


def test_text_reply_still_requires_explicit_context(button_consent):
    v=button_consent;preview,provider=sent(v)
    incoming(v,'YES')
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING'
    incoming(v,'YES',reply=provider)
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_IN'


def test_transport_posts_correct_interactive_format_and_never_retries(monkeypatch):
    for name,value in [('META_WHATSAPP_ACCESS_TOKEN','fake-token'),('META_WHATSAPP_PHONE_NUMBER_ID','fake-id')]:monkeypatch.setattr(whatsapp_service.settings,name,value)
    requests=[]
    def post(url,**kwargs):
        requests.append(kwargs);return httpx.Response(200,json={'messages':[{'id':'wamid.accepted'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(whatsapp_service.httpx,'post',post)
    items=[{'id':'opaque-yes','title':'Yes, I agree'},{'id':'opaque-no','title':'No, thanks'}]
    assert whatsapp_service.send_whatsapp_consent_buttons('94770012301','PixiePinks WhatsApp promotions?',items)=='wamid.accepted'
    data=requests[0]['json'];assert data['type']=='interactive' and data['interactive']['type']=='button'
    assert data['interactive']['action']['buttons'][0]=={'type':'reply','reply':items[0]}
    assert whatsapp_service.send_whatsapp_consent_buttons('x','x'*1025,items) is None and len(requests)==1
    def failed(*a,**kw):requests.append(kw);raise httpx.ReadTimeout('fixture')
    monkeypatch.setattr(whatsapp_service.httpx,'post',failed)
    assert whatsapp_service.send_whatsapp_consent_buttons('94770012301','PixiePinks WhatsApp promotions?',items) is None and len(requests)==2


def test_window_csrf_and_invalid_format_prevent_sends(button_consent):
    v=button_consent
    assert v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/marketing-consent/request',json={'key':str(uuid4()),'language':'si','format':'buttons'}).status_code==403
    assert prepare(v,format={}).status_code==422
    with v.sessions() as db:db.get(WhatsAppConversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    assert prepare(v).status_code==409 and not v.button_sends and not v.sends


def test_optout_during_send_wins_and_no_button_after_expiry_still_withdraws(button_consent,monkeypatch):
    v=button_consent;preview=prepare(v).json()
    def send(*args):
        assert action(v,'opt_out',reason='Customer withdrew before delivery.').status_code==200
        return 'wamid.concurrent-button'
    monkeypatch.setattr(main,'send_whatsapp_consent_buttons',send)
    assert send_request(v,preview).status_code==200
    assert reply(v,preview,'wamid.concurrent-button').status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_OUT'


def test_decline_expired_but_authentic_request_suppresses(button_consent):
    v=button_consent;preview,provider=sent(v)
    with v.sessions() as db:db.get(Request,preview['request_id']).expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    assert reply(v,preview,provider,choice=1).status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_OUT'


def test_old_request_button_cannot_approve_new_request(button_consent):
    v=button_consent;old,old_provider=sent(v)
    assert action(v,'invalidate',reason='Request expired; review a new consent request.').status_code==200
    with v.sessions() as db:
        request=db.get(Request,old['request_id']);request.sent_at=datetime.utcnow()-timedelta(days=2);request.expires_at=datetime.utcnow()-timedelta(days=1);db.commit()
    new,new_provider=sent(v)
    assert old['buttons']!=new['buttons']
    assert reply(v,old,old_provider).status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING'
    assert reply(v,new,new_provider).status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_IN'


def test_second_precision_and_callback_before_acceptance_commit(button_consent):
    v=button_consent;preview,provider=sent(v);mid='wamid.racing-button'
    with v.sessions() as db:
        req=db.get(Request,preview['request_id']);sent_at=req.sent_at
        req.status='SENDING';req.whatsapp_message_id=None
        row=db.get(Consent,v.contact);row.status='NOT_REQUESTED';row.active_request_id=None;db.commit()
    timestamp=str(int(sent_at.timestamp()))
    assert reply(v,preview,provider,mid=mid,timestamp=timestamp).status_code==503
    with v.sessions() as db:
        assert not db.query(ProcessedWhatsAppMessage).filter_by(message_id=mid).count()
        req=db.get(Request,preview['request_id']);req.status='SENT';req.whatsapp_message_id=provider
        row=db.get(Consent,v.contact);row.status='PENDING';row.active_request_id=req.id;db.commit()
    assert reply(v,preview,provider,mid=mid,timestamp=timestamp).status_code==200
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_IN'
