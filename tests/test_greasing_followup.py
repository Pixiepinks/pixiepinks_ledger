"""Post-selection Saved Voice and explicit paid-service decision; all provider IO mocked."""
from decimal import Decimal
import pytest
import main
import customer_questions as q
import question_greasing_service as service
import question_product_service as products
from models import SavedVoice,CustomerQuestionProductSend as Item,WhatsAppConversationMessage as Message,GlobalAIReplyControl
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox
from test_product_choice_list import choosing
from test_shopify_collection_sends import product
from test_customer_questions import settings,act,reply


@pytest.fixture
def greasing(voices,monkeypatch):
    v=voices;v.sessions.configure(autoflush=False)
    row=v.create().json()['voice'];buttons=[];audio=[];uploads=[]
    monkeypatch.setattr(main.settings,'META_APP_SECRET','question-fixture')
    with v.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:product(1))
    monkeypatch.setattr(products,'send_whatsapp_image',lambda *a,**kw:('SENT','wamid.grease.card'))
    monkeypatch.setattr(q,'send_buttons',lambda phone,body,options,**kw:buttons.append((phone,body,options)) or f'wamid.grease.question.{len(buttons)}')
    monkeypatch.setattr(service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'media.grease')
    monkeypatch.setattr(service,'send_whatsapp_audio',lambda *a,**kw:audio.append((a,kw)) or ('SENT','wamid.grease.voice'))
    definition=choosing(1);definition['steps'][0]['service']={'voice_id':row['id'],'revision':row['revision'],'charge':'1000.00',
        'question':service.DEFAULT_QUESTION,'yes':service.DEFAULT_YES,'no':service.DEFAULT_NO}
    value=(v.client,v.sessions,v.ids,v.texts,v.login,v.csrf,buttons)
    return v,row,definition,value,audio,uploads


def selected(g):
    v,row,definition,value,audio,uploads=g
    response=settings(v.client,v.csrf,definition=definition,enabled=True);assert response.status_code==200,response.text
    response=act(v.client,v.ids[0],v.csrf);assert response.status_code==200,response.text
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    assert reply(value,index=0,reply_type='list_reply',mid='wamid.grease.selected').status_code==200
    return g


@pytest.mark.parametrize('choice,accepted,total',[(0,True,'46000.00'),(1,False,'45000.00')])
def test_voice_then_question_then_explicit_fee_decision(greasing,choice,accepted,total):
    v,row,definition,value,audio,uploads=selected(greasing)
    assert len(value[-1])==1 and not audio
    assert q.dispatch_one(v.sessions) and len(audio)==1 and len(value[-1])==1
    assert q.dispatch_one(v.sessions) and len(value[-1])==2
    assert '1,000.00' in value[-1][1][1]
    assert [o['title'] for o in value[-1][1][2]]==[service.DEFAULT_YES,service.DEFAULT_NO]
    assert reply(value,index=choice,reply_type='list_reply').status_code==200
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='WAITING'
    assert reply(value,index=choice,mid='wamid.grease.decision').status_code==200
    assert reply(value,index=choice,mid='wamid.grease.decision').status_code==200
    assert not q.dispatch_one(v.sessions)
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);decision=run.answers['recommendations']['greasing_service']
        assert decision['accepted'] is accepted and decision['quoted_total']==total
        assert decision['charge']==('1000.00' if accepted else '0.00')
        assert run.status=='COMPLETE' and db.get(GlobalAIReplyControl,1).enabled is False
        assert db.get(SavedVoice,row['id']).revision==row['revision']
        voice=db.query(Message).filter_by(response_kind='question_voice').one();mid=voice.id
        assert voice.analytics_actor_kind=='automatic' and voice.sent_by is None
    played=v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{mid}/media')
    assert played.status_code==200 and played.content==uploads[0]
    assert v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[1]}/messages/{mid}/media').status_code==404


def test_changed_recording_stops_before_voice_and_question(greasing):
    v,row,_,value,audio,uploads=selected(greasing)
    with v.sessions() as db:db.get(SavedVoice,row['id']).revision+=1;db.commit()
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions)
    assert not audio and not uploads and len(value[-1])==1
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'


def test_unknown_voice_never_retries_or_asks_question(greasing,monkeypatch):
    v,_,_,value,audio,uploads=selected(greasing)
    monkeypatch.setattr(service,'send_whatsapp_audio',lambda *a,**kw:audio.append(1) or ('UNKNOWN',None))
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions)
    assert len(audio)==len(uploads)==1 and len(value[-1])==1
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'


def test_variant_c_matches_existing_staff_encoder_without_mutating_library(greasing,monkeypatch):
    import voice_diagnostic_tools as tools
    import voice_packet_analysis as bits
    if not tools.runtime().get('verified'):pytest.skip('Pinned FFmpeg unavailable')
    v,row,_,_,audio,uploads=selected(greasing)
    before={path.name:path.read_bytes() for path in v.directory.iterdir()}
    with v.sessions() as db:source=(v.directory/db.get(SavedVoice,row['id']).storage_name).read_bytes()
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    assert q.dispatch_one(v.sessions)
    assert audio[0][1]=={'voice':False,'return_outcome':True}
    assert bits.compare(bits.profile(tools.encode(source)),bits.profile(uploads[0]))['codec_packets_equal']
    assert all((v.directory/name).read_bytes()==raw for name,raw in before.items())


@pytest.mark.parametrize('change',[{'charge':'-1'},{'charge':'NaN'},{'charge':'1.001'},{'voice_id':0},{'yes':'x'*21}])
def test_invalid_service_config_rejected(greasing,change):
    v,_,definition,_,_,_=greasing;definition['steps'][0]['service'].update(change)
    assert settings(v.client,v.csrf,definition=definition).status_code==422


def test_inactive_voice_rejected_when_saving(greasing):
    v,row,definition,_,_,_=greasing
    with v.sessions() as db:db.get(SavedVoice,row['id']).active=False;db.commit()
    assert settings(v.client,v.csrf,definition=definition).status_code==409


def test_pause_after_voice_does_not_replay_explanation(greasing):
    v,_,_,value,audio,_=selected(greasing);assert q.dispatch_one(v.sessions)
    with v.sessions() as db:version=q.latest(db,v.ids[0]).version
    assert act(v.client,v.ids[0],v.csrf,'pause',version).status_code==200 and not q.dispatch_one(v.sessions)
    with v.sessions() as db:version=q.latest(db,v.ids[0]).version
    assert act(v.client,v.ids[0],v.csrf,'resume',version).status_code==200
    assert q.dispatch_one(v.sessions) and len(audio)==1 and len(value[-1])==2


def test_lost_voice_ack_claim_is_not_replayed(greasing,monkeypatch):
    v,_,_,value,audio,_=selected(greasing)
    def crash(*a,**kw):audio.append(1);raise KeyboardInterrupt('fixture crash')
    monkeypatch.setattr(service,'send_whatsapp_audio',crash)
    with pytest.raises(KeyboardInterrupt):q.dispatch_one(v.sessions)
    assert not q.dispatch_one(v.sessions) and len(audio)==1 and len(value[-1])==1
    with v.sessions() as db:assert db.query(Item).filter_by(position=-2).one().status=='SENDING'


def test_saved_voice_section_grant_required_to_start(greasing):
    from models import User
    v,_,definition,_,_,_=greasing
    assert settings(v.client,v.csrf,definition=definition,enabled=True).status_code==200
    with v.sessions() as db:db.query(User).filter_by(username='quick-staff').one().section_access=['whatsapp'];db.commit()
    v.login('quick-staff')
    token=v.client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    assert act(v.client,v.ids[0],token).status_code==403


def test_unverified_product_price_cannot_queue_paid_followup(greasing):
    v,_,definition,value,audio,uploads=greasing
    assert settings(v.client,v.csrf,definition=definition,enabled=True).status_code==200
    assert act(v.client,v.ids[0],v.csrf).status_code==200
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    with v.sessions() as db:db.query(Message).filter_by(response_kind='question_product').one().product_currency='USD';db.commit()
    assert reply(value,index=0,reply_type='list_reply').status_code==200
    assert not q.dispatch_one(v.sessions) and not audio and not uploads
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'
