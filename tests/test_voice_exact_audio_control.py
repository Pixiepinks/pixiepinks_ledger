from copy import deepcopy
from datetime import timedelta,datetime
import json

import pytest
import main
import audio_service
import voice_ffmpeg_owner_test as candidate
import voice_delivery_errors
import whatsapp_service
from models import VoicePlaybackTest,VoicePlaybackVariant,GlobalAIReplyControl,WhatsAppConversation
from saved_voice_service import _read_file,storage_path
from test_voice_ffmpeg_candidate import owner_candidate
from test_voice_encoding_experiment import experiment,wire
from test_voice_playback_test import ab,BASE
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def exact(owner_candidate,monkeypatch):
    v=owner_candidate
    assert v.candidate_prepare().status_code==200
    with v.sessions() as db:
        prior=db.get(VoicePlaybackTest,v.latest())
        evidence=deepcopy(prior.evidence)
        fp=evidence['variants']['P']['bytes'];monkeypatch.setattr(candidate,'FAILED_FINGERPRINT',fp)
        ref=VoicePlaybackTest(id=candidate.FAILED_REFERENCE,staff_id=prior.staff_id,voice_id=prior.voice_id,
            voice_revision=prior.voice_revision,storage_name=prior.storage_name,recipient_hash=prior.recipient_hash,
            status='SUBMITTED',evidence=evidence)
        db.add(ref);db.flush()
        stages={k:dict(fp) for k in ('immutable_snapshot','upload_validated','multipart_file')}
        stages['send_payload']={'audio':{'id':'failed.media','voice':True}}
        db.add(VoicePlaybackVariant(run_id=ref.id,variant='P',status='SENT',media_id='failed.media',
            delivery_status='failed',delivery_error_code=131053,audio_trace={'stages':stages}))
        db.commit()
        v.original=ref.evidence['variants']['P']['storage_name'];v.original_bytes=_read_file(v.original)
    monkeypatch.setenv('VOICE_EXACT_AUDIO_CONTROL_ENABLED','true')
    monkeypatch.setattr(audio_service,'normalize_voice_audio',lambda *a:pytest.fail('Must not re-encode'))
    def prepare(**kw):
        return v.client.post(BASE+'/candidate/exact/prepare',data={'reference_id':candidate.FAILED_REFERENCE,
            'owner_number':'94770012301','owner_confirm':'true','csrf_token':v.csrf,**kw})
    v.exact_prepare=prepare
    return v


def test_exact_bytes_review_one_send_duplicate_and_full_error_capture(exact,monkeypatch):
    v=exact;uploaded,sent=wire(monkeypatch)
    page=v.exact_prepare();assert page.status_code==200,page.text
    assert not uploaded and not sent and 'No re-encoding' in page.text
    rid=v.latest()
    assert v.exact_prepare().status_code==200 and v.latest()==rid
    preview=v.client.get(BASE+'/candidate/'+rid+'/audio')
    assert preview.content==v.original_bytes
    assert v.candidate_run(confirm_one='false').status_code==400
    assert v.candidate_run(message_count=2).status_code==400
    assert v.candidate_run().status_code==200
    assert v.candidate_run().status_code==200
    assert uploaded==[v.original_bytes]
    assert len(sent)==1 and sent[0]['audio']=={'id':'media.encoding.1'}
    assert sent[0]['biz_opaque_callback_data']=='exact-control:'+rid
    with v.sessions() as db:
        row=db.query(VoicePlaybackVariant).filter_by(run_id=rid).one();wid=row.whatsapp_message_id
        assert db.get(GlobalAIReplyControl,1).enabled is False
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    error={'code':131053,'title':'Media upload error','message':'Media upload error',
           'error_data':{'details':'Cannot process Opus stream','messaging_product':'whatsapp'},
           'href':'https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/'}
    for status in ('failed','delivered'):
        data={'entry':[{'changes':[{'value':{'statuses':[{'id':wid,'status':status,
            'errors':[error] if status=='failed' else [],'recipient_id':'private-recipient'}]}}]}]}
        assert v.client.post('/webhook',json=data).status_code==200
    with v.sessions() as db:
        trace=db.query(VoicePlaybackVariant).filter_by(run_id=rid).one().audio_trace
        assert trace['delivery_failure']['errors']==[error]
        assert len(trace['delivery_events'])==2 and 'private-recipient' not in json.dumps(trace)
    assert v.client.post(BASE+'/candidate/close',data={'run_id':rid,'confirm_delete':'true','csrf_token':v.csrf}).status_code==200
    assert _read_file(v.original)==v.original_bytes


@pytest.mark.parametrize('gate',['disabled','ai','encoder','owner','csrf','reference','corrupt','window','role'])
def test_exact_preparation_fails_closed(exact,monkeypatch,gate):
    v=exact;uploaded,sent=wire(monkeypatch);kw={}
    if gate=='disabled':monkeypatch.delenv('VOICE_EXACT_AUDIO_CONTROL_ENABLED')
    if gate=='encoder':monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    if gate=='owner':kw['owner_number']='94770000000'
    if gate=='csrf':kw['csrf_token']='bad'
    if gate=='reference':kw['reference_id']='different'
    if gate=='corrupt':storage_path(v.original).write_bytes(b'bad')
    if gate=='role':v.login('quick-staff')
    with v.sessions() as db:
        if gate=='ai':db.get(GlobalAIReplyControl,1).enabled=True
        if gate=='window':db.get(WhatsAppConversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2)
        db.commit()
    assert v.exact_prepare(**kw).status_code in {400,403,409}
    assert not uploaded and not sent


@pytest.mark.parametrize('gate',['ai','encoder','disabled','window'])
def test_exact_send_rechecks_operational_guards(exact,monkeypatch,gate):
    v=exact;uploaded,sent=wire(monkeypatch)
    assert v.exact_prepare().status_code==200
    if gate=='encoder':monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    if gate=='disabled':monkeypatch.delenv('VOICE_EXACT_AUDIO_CONTROL_ENABLED')
    with v.sessions() as db:
        if gate=='ai':db.get(GlobalAIReplyControl,1).enabled=True
        if gate=='window':db.get(WhatsAppConversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2)
        db.commit()
    assert v.candidate_run().status_code==409
    assert not uploaded and not sent


def test_error_evidence_redacts_credentials_and_numbers():
    result=voice_delivery_errors.errors([{'code':131053,'title':'Media upload error',
        'message':'Bearer private-token access_token=secret123 phone +94 77 123 4567',
        'error_data':{'details':'See https://private.test/signed?token=secret'},
        'href':'https://developers.facebook.com/docs/test?token=secret'}])
    text=json.dumps(result)
    assert 'private-token' not in text and 'secret123' not in text and '123 4567' not in text
    assert 'private.test' not in text and '?token=' not in text


def test_early_webhook_is_correlated_before_submission_returns(exact,monkeypatch):
    v=exact;wire(monkeypatch)
    assert v.exact_prepare().status_code==200
    rid=v.latest();original=whatsapp_service.send_whatsapp_audio
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    def submit(*args,**kwargs):
        result=original(*args,**kwargs)
        callback=kwargs['owner_callback']
        event={'id':result[1],'status':'failed','biz_opaque_callback_data':callback,
               'errors':[{'code':131053,'title':'Media upload error','error_data':{'details':'Cannot process media'}}]}
        assert v.client.post('/webhook',json={'entry':[{'changes':[{'value':{'statuses':[event]}}]}]}).status_code==200
        return result
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',submit)
    assert v.candidate_run().status_code==200
    with v.sessions() as db:
        row=db.query(VoicePlaybackVariant).filter_by(run_id=rid).one()
        assert row.delivery_status=='failed'
        assert row.audio_trace['delivery_failure']['errors'][0]['error_data']['details']=='Cannot process media'


def test_concurrent_claim_and_unknown_result_do_not_repeat(exact,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    v=exact;assert v.exact_prepare().status_code==200;rid=v.latest()
    uploads=[];sends=[]
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'one.media')
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:sends.append(kw) or ('UNKNOWN',None))
    with v.sessions() as db:uid=db.get(VoicePlaybackTest,rid).staff_id
    def execute(_):
        with v.sessions() as db:candidate.execute(db,SimpleNamespace(id=uid,role='ADMIN'),rid,True,1)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(execute,range(3)))
    assert uploads==[v.original_bytes] and len(sends)==1
    assert v.candidate_run().status_code==200 and len(sends)==1
