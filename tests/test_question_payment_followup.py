"""Payment instructions with mocked transports and signed private slip handoff."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime,timedelta
import pytest
import main
import customer_questions as q
import question_payment_followup as payment
import question_greasing_service as voice_service
from models import CustomerQuestionProductSend as Item,CustomerQuestionEvent as Event,CustomerQuestionRun as Run
from models import WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,WhatsAppMessageAttachment as Attachment,GlobalAIReplyControl,SavedVoice,SalesPayment,SalesOrder
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting,begin,complete_fields,current,text
from test_customer_questions import settings,act,reply
from test_whatsapp_outbound_media import png,jpeg

BASE='/crm/quick-messages/customer-questions/payment-images'


@pytest.fixture
def paying(collecting,monkeypatch):
    g,_=collecting;v=g[0]
    uploaded=v.client.post(BASE,content=png(),headers={'Content-Type':'image/png','X-CSRF-Token':v.csrf})
    assert uploaded.status_code==200,uploaded.text
    recording=v.create(title='How to pay',transcript='Pay using the bank details, then send the slip.').json()['voice']
    config={'voice_id':recording['id'],'revision':recording['revision'],'image_ref':uploaded.json()['image_ref'],'caption':payment.DEFAULT_CAPTION}
    g[2]['steps'][0]['service']['payment']=config
    images=[];uploads=[]
    monkeypatch.setattr(voice_service,'send_whatsapp_audio',lambda *a,**kw:g[4].append((a,kw)) or ('SENT',f'wamid.payment.audio.{len(g[4])}'))
    monkeypatch.setattr(payment.media,'upload_media',lambda raw,data:uploads.append((raw,data)) or 'media.bank')
    monkeypatch.setattr(payment.media,'send_media',lambda phone,mid,data:images.append((phone,mid,data)) or ('SENT','wamid.payment.bank'))
    return collecting,config,images,uploads


def confirmed(paying):
    c,config,images,uploads=paying
    begin(c);complete_fields(c,phone2='skip')
    assert not images and not uploads
    assert reply(c[0][3],index=0).status_code==200
    with c[0][0].sessions() as db:
        run=q.latest(db,c[0][0].ids[0]);assert run.status=='QUEUED'
        assert run.answers['recommendations']['payment']['status']=='VOICE_QUEUED'
    return paying


def instructions(paying):
    c,config,images,uploads=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    assert len(c[0][4])==2 and not images
    assert q.dispatch_one(v.sessions) and len(images)==1
    assert not q.dispatch_one(v.sessions)
    return paying


def slip(paying,*,kind='image',mime=None,mid=None,sender=None,context=None,when=None):
    c,_,_,_=paying;v=c[0][0]
    with v.sessions() as db:phone=db.get(Conversation,v.ids[0]).phone_number
    message={'id':mid or 'wamid.slip.'+secrets.token_hex(8),'from':sender or phone,'type':kind,
        'timestamp':str(int((when or datetime.utcnow()).timestamp())),
        kind:{'id':'private-slip-media','mime_type':mime or ('image/jpeg' if kind=='image' else 'application/pdf')}}
    if kind=='document':message[kind]['filename']='deposit-slip.pdf'
    if context:message['context']={'id':context}
    raw=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}).encode()
    return v.client.post('/webhook',content=raw,headers={'Content-Type':'application/json',
        'X-Hub-Signature-256':'sha256='+hmac.new(b'question-fixture',raw,hashlib.sha256).hexdigest()})


def state(paying):
    v=paying[0][0][0]
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);return run.status,run.answers['recommendations']['payment']


@pytest.mark.parametrize('kind',['image','document'])
def test_confirm_voice_bank_image_then_slip_is_unverified(paying,kind,monkeypatch):
    instructions(paying);c,config,images,uploads=paying;v=c[0][0]
    assert state(paying)==('WAITING',{'status':'AWAITING_SLIP','instructions_sent_at':state(paying)[1]['instructions_sent_at']})
    assert uploads[0][0]==png() and images[0][2]['caption']==payment.DEFAULT_CAPTION
    def forbidden(*a,**kw):raise AssertionError('Bank/receipt must not enter AI or remote bank download')
    monkeypatch.setattr(main,'_enqueue_ai_reply',forbidden)
    monkeypatch.setattr(main,'download_whatsapp_attachment',forbidden)
    with v.sessions() as db:
        bank=db.query(Message).filter_by(response_kind='payment_image').one();bank_id=bank.id
        aid=db.query(Attachment).filter_by(message_id=bank_id).one().id
        voice=db.query(Message).filter_by(response_kind='payment_voice').one();voice_id=voice.id
    preview=v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{bank_id}/attachments/{aid}/media')
    assert preview.status_code==200 and preview.content==png()
    assert v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[1]}/messages/{bank_id}/attachments/{aid}/media').status_code==404
    assert v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{voice_id}/media').status_code==200
    assert text(c,'I paid').status_code==200 and state(paying)[0]=='WAITING'
    assert slip(paying,kind=kind,mid='wamid.slip.same').status_code==200
    assert slip(paying,kind=kind,mid='wamid.slip.same').status_code==200
    status,data=state(paying)
    assert status=='COMPLETE' and data['status']=='SLIP_REVIEW'
    assert not q.dispatch_one(v.sessions)
    with v.sessions() as db:
        assert db.query(Event).filter_by(action='PAYMENT_SLIP_SUBMITTED').count()==1
        assert db.get(Message,data['receipt_message_id']).direction=='inbound'
        assert db.get(Attachment,data['receipt_attachment_id']).message_id==data['receipt_message_id']
        assert db.query(SalesPayment).count()==0 and db.query(SalesOrder).count()==0
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.query(Message).filter_by(response_kind='payment_voice',analytics_actor_kind='automatic').count()==1


def test_upload_private_validation_limits_and_pinned_image(paying):
    c,config,_,_=paying;v=c[0][0]
    preview=v.client.get(BASE+'/'+config['image_ref']);assert preview.status_code==200 and preview.content==png()
    assert preview.headers['cache-control']=='private, no-store'
    assert v.client.post(BASE,content=png(),headers={'Content-Type':'image/png'}).status_code==403
    assert v.client.post(BASE,content=b'<svg/>',headers={'Content-Type':'image/svg+xml','X-CSRF-Token':v.csrf}).status_code==415
    assert v.client.post(BASE,content=b'bad',headers={'Content-Type':'image/png','X-CSRF-Token':v.csrf}).status_code==422
    assert v.client.post(BASE,content=b'x'*(payment.media.size_limit('image')+1),headers={'Content-Type':'image/png','X-CSRF-Token':v.csrf}).status_code==413
    replacement=v.client.post(BASE,content=jpeg(),headers={'Content-Type':'image/jpeg','X-CSRF-Token':v.csrf})
    assert replacement.status_code==200 and replacement.json()['image_ref']!=config['image_ref']
    assert v.client.get(BASE+'/'+config['image_ref']).content==png()
    assert v.client.get(BASE+'/bad').status_code==422
    v.login('quick-staff');token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert v.client.get(BASE+'/'+config['image_ref']).status_code==403
    assert v.client.post(BASE,content=png(),headers={'Content-Type':'image/png','X-CSRF-Token':token}).status_code==403


@pytest.mark.parametrize('change',[{'image_ref':'../../secret'},{'caption':''},{'revision':0}])
def test_invalid_payment_config_rejected(paying,change):
    c,config,_,_=paying;v=c[0][0];config.update(change)
    assert settings(v.client,v.csrf,definition=c[0][2]).status_code==422


def test_payment_requires_delivery_collection(paying):
    c,_,_,_=paying;v=c[0][0];c[0][2]['steps'][0]['service']['collect_delivery']=False
    assert settings(v.client,v.csrf,definition=c[0][2]).status_code==422


@pytest.mark.parametrize('failure',['voice','changed_voice','missing_image','image','crash'])
def test_failure_never_replays_or_sends_later_stage(paying,monkeypatch,failure):
    c,config,images,uploads=confirmed(paying);v=c[0][0]
    if failure=='changed_voice':
        with v.sessions() as db:db.get(SavedVoice,config['voice_id']).revision+=1;db.commit()
    elif failure=='missing_image':payment.image_path(config['image_ref']).unlink()
    elif failure=='voice':monkeypatch.setattr(voice_service,'send_whatsapp_audio',lambda *a,**kw:('UNKNOWN',None))
    elif failure=='crash':
        def crash(*a,**kw):raise KeyboardInterrupt()
        monkeypatch.setattr(voice_service,'send_whatsapp_audio',crash)
    if failure=='crash':
        with pytest.raises(KeyboardInterrupt):q.dispatch_one(v.sessions)
    else:assert q.dispatch_one(v.sessions)
    if failure=='image':
        monkeypatch.setattr(payment.media,'send_media',lambda *a:('OUTCOME_UNCERTAIN',None))
        assert q.dispatch_one(v.sessions)
    assert not q.dispatch_one(v.sessions) and not images
    assert state(paying)[0] in {'UNCERTAIN','SENDING'}


def test_resume_after_voice_only_sends_image_and_window_is_rechecked(paying):
    c,config,images,uploads=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    with v.sessions() as db:version=q.latest(db,v.ids[0]).version
    assert act(v.client,v.ids[0],v.csrf,'pause',version).status_code==200
    assert not q.dispatch_one(v.sessions)
    with v.sessions() as db:version=q.latest(db,v.ids[0]).version
    assert act(v.client,v.ids[0],v.csrf,'resume',version).status_code==200
    assert q.dispatch_one(v.sessions) and len(c[0][4])==2 and len(images)==1


@pytest.mark.parametrize('mode',['pause','off','old','wrong_sender','wrong_context','unsupported'])
def test_only_current_active_slip_is_linked(paying,mode):
    instructions(paying);c,config,images,uploads=paying;v=c[0][0]
    if mode=='pause':
        with v.sessions() as db:version=q.latest(db,v.ids[0]).version
        assert act(v.client,v.ids[0],v.csrf,'pause',version).status_code==200
    elif mode=='off':assert settings(v.client,v.csrf,enabled=False).status_code==200
    args={'when':datetime.utcnow()-timedelta(days=2)} if mode=='old' else {'sender':'94770012302'} if mode=='wrong_sender' else {'context':'wamid.unrelated'} if mode=='wrong_context' else {'kind':'document','mime':'text/plain'} if mode=='unsupported' else {}
    assert slip(paying,**args).status_code==200
    assert state(paying)[1]['status']=='AWAITING_SLIP'


def test_slip_receipt_and_run_transition_are_atomic(paying,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    instructions(paying);v=paying[0][0][0];original=payment.save
    def fail(*a):original(*a);raise SQLAlchemyError('fixture rollback')
    monkeypatch.setattr(payment,'save',fail)
    assert slip(paying,mid='wamid.slip.retry').status_code==503
    assert state(paying)[1]['status']=='AWAITING_SLIP'
    with v.sessions() as db:assert not db.query(Message).filter_by(whatsapp_message_id='wamid.slip.retry').count()
    monkeypatch.setattr(payment,'save',original)
    assert slip(paying,mid='wamid.slip.retry').status_code==200
    assert state(paying)[1]['status']=='SLIP_REVIEW'


def test_known_failed_voice_blocks_bank_image(paying):
    c,_,images,_=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    with v.sessions() as db:db.query(Message).filter_by(response_kind='payment_voice').one().send_status='failed';db.commit()
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions) and not images
    assert state(paying)[0]=='UNCERTAIN'


def test_image_send_crash_never_replays(paying,monkeypatch):
    c,_,images,uploads=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    def crash(*a):raise KeyboardInterrupt()
    monkeypatch.setattr(payment.media,'send_media',crash)
    with pytest.raises(KeyboardInterrupt):q.dispatch_one(v.sessions)
    assert len(uploads)==1 and not q.dispatch_one(v.sessions)
    assert state(paying)[0]=='SENDING'


def test_window_closed_between_stages_never_sends_bank_image(paying):
    c,_,images,uploads=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    with v.sessions() as db:db.get(Conversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert q.dispatch_one(v.sessions) and not images and not uploads
    assert state(paying)[0]=='WINDOW_CLOSED'


def test_missing_media_upload_id_blocks_image_send(paying,monkeypatch):
    c,_,images,_=confirmed(paying);v=c[0][0]
    assert q.dispatch_one(v.sessions)
    monkeypatch.setattr(payment.media,'upload_media',lambda *a:None)
    assert q.dispatch_one(v.sessions) and not images and not q.dispatch_one(v.sessions)
    assert state(paying)[0]=='UNCERTAIN'


def test_early_slip_does_not_complete_payment_before_instructions(paying):
    confirmed(paying);v=paying[0][0][0]
    assert slip(paying).status_code==200
    assert state(paying)[1]['status']=='VOICE_QUEUED'
    with v.sessions() as db:assert not db.query(Event).filter_by(action='PAYMENT_SLIP_SUBMITTED').count()
