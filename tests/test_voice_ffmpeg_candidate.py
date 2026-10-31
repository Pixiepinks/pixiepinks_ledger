"""Production candidate rollout stays OFF. All Meta sends in tests are mocked."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import timedelta
import json
import os
import struct
import wave

import av
import pytest

import audio_service
import main
import voice_diagnostic_tools as tools
import voice_ffmpeg_encoder as encoder
import voice_packet_analysis as bits
import voice_trace
import whatsapp_service
import voice_ffmpeg_owner_test as candidate
from models import VoicePlaybackTest,VoicePlaybackVariant,SavedVoice,GlobalAIReplyControl
from test_voice_encoding_experiment import experiment,wire
from test_voice_playback_test import ab,BASE
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox


def sample(seconds=.7,rate=16000):
    b=BytesIO()
    with wave.open(b,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(b'\0\0'*int(rate*seconds))
    return b.getvalue()


def real_tools():
    if not tools.runtime().get('verified'):pytest.skip('Optional pinned tool not installed')


def test_flag_off_uses_original_pyav_without_touching_ffmpeg(monkeypatch):
    monkeypatch.delenv('VOICE_FFMPEG_ENCODER_ENABLED',raising=False)
    monkeypatch.setattr(encoder,'normalize',lambda *a:pytest.fail('OFF called FFmpeg'))
    output,duration=audio_service.normalize_voice_audio(wav(),'audio/wav')
    assert duration==.5 and audio_service.validate_voice_audio(output)['codec']=='opus'


def test_on_uses_pinned_validated_ffmpeg_and_same_c_packet_content(monkeypatch):
    real_tools();monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(audio_service,'_normalize_pyav',lambda *a:pytest.fail('Silent PyAV fallback'))
    source=sample();output,duration=audio_service.normalize_voice_audio(source,'misleading/type')
    reference=tools.encode(source)
    comparison=bits.compare(bits.profile(reference),bits.profile(output))
    assert comparison['codec_packets_equal'] and duration==.7
    assert tools.production_decode(output)['passed']
    assert encoder.metrics()['success']>=1


@pytest.mark.parametrize('failure',['tool','encode','decode','structure'])
def test_no_fallback_on_any_candidate_failure(monkeypatch,failure):
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(audio_service,'_normalize_pyav',lambda *a:pytest.fail('Fallback'))
    if failure=='tool':monkeypatch.setattr(tools,'runtime',lambda:{'verified':False})
    else:
        real_tools()
        if failure=='encode':monkeypatch.setattr(tools,'production_encode',lambda raw:(_ for _ in ()).throw(ValueError('private-token-phone-url')))
        if failure=='decode':monkeypatch.setattr(tools,'production_decode',lambda raw:{'passed':False})
        if failure=='structure':monkeypatch.setattr(tools,'production_inspect',lambda raw:(_ for _ in ()).throw(ValueError('private-token-phone-url')))
    with pytest.raises(audio_service.VoiceAudioError) as error:audio_service.normalize_voice_audio(wav())
    assert 'private-token' not in str(error.value)
    assert encoder.metrics()['error']>=1


def test_owner_override_does_not_enable_global_or_other_threads(monkeypatch):
    monkeypatch.delenv('VOICE_FFMPEG_ENCODER_ENABLED',raising=False)
    with audio_service.owner_ffmpeg_candidate():
        assert audio_service.ffmpeg_encoder_enabled()
        with ThreadPoolExecutor(max_workers=1) as pool:assert pool.submit(audio_service.ffmpeg_encoder_enabled).result() is False
    assert not audio_service.ffmpeg_encoder_enabled() and 'VOICE_FFMPEG_ENCODER_ENABLED' not in os.environ
    with pytest.raises(RuntimeError):
        with audio_service.owner_ffmpeg_candidate():raise RuntimeError('test')
    assert not audio_service.ffmpeg_encoder_enabled()


def test_public_candidate_preserves_longer_than_diagnostic_limit(monkeypatch):
    real_tools();monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    output,duration=audio_service.normalize_voice_audio(sample(31,8000))
    assert duration==31 and tools.production_decode(output)['decoded_duration_seconds']==31
    with pytest.raises(audio_service.VoiceAudioError):audio_service.normalize_voice_audio(sample(601,8000))


def test_packet_analysis_isolates_one_sample_granule_without_claiming_cause():
    raw=tools.encode_pyav(wav(),'current');left=bits.profile(raw)
    changed=bytearray(raw)
    pos=0
    while pos<len(raw):
        segments=raw[pos+26];end=pos+27+segments+sum(raw[pos+27:pos+27+segments])
        if end==len(raw):
            value=struct.unpack_from('<Q',raw,pos+6)[0];struct.pack_into('<Q',changed,pos+6,value+1)
            changed[pos+22:pos+26]=b'\0'*4;crc=0
            for byte in changed[pos:end]:crc=((crc<<8)&0xffffffff)^audio_service._CRC[((crc>>24)^byte)&255]
            struct.pack_into('<I',changed,pos+22,crc)
        pos=end
    comparison=bits.compare(left,bits.profile(bytes(changed)))
    assert comparison['codec_packets_equal'] and comparison['final_granule_delta_samples']==1
    assert comparison['final_granule_delta_seconds']==1/48000 and 'no field' in comparison['conclusion']


@pytest.fixture
def owner_candidate(experiment,monkeypatch):
    v=experiment
    monkeypatch.delenv('VOICE_FFMPEG_ENCODER_ENABLED',raising=False)
    monkeypatch.setenv('VOICE_FFMPEG_OWNER_TEST_ENABLED','true')
    monkeypatch.setattr(tools,'runtime',lambda:{'available':True,'verified':True,'binary_sha256':tools.BINARY_SHA256})
    assert v.encoding_prepare().status_code==200
    reference=v.latest()
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,reference);run.status='SUBMITTED'
        control=db.query(VoicePlaybackVariant).filter_by(run_id=reference,variant='C').one();control.status='SENT'
        control.media_id='prior.reference.media';control.whatsapp_message_id='wamid.prior.reference';db.commit()
    data={'title':'Fresh owner sample','category':'Test','transcript':'Owner authorized','language':'singlish'}
    created=v.client.post('/crm/saved-voices/api',data=data,files={'audio':('fresh.wav',sample(),'audio/wav')},headers={'X-CSRF-Token':v.csrf})
    assert created.status_code==201,created.text
    fresh=created.json()['voice']
    def prepare(**kw):
        return v.client.post(BASE+'/candidate/prepare',data={'reference_id':reference,'voice_id':fresh['id'],'revision':fresh['revision'],
            'owner_number':'94770012301','owner_confirm':'true','mode':'native','csrf_token':v.csrf,**kw})
    def run(**kw):
        return v.client.post(BASE+'/candidate/run',data={'run_id':v.latest(),'confirm_one':'true','message_count':1,'csrf_token':v.csrf,**kw})
    v.reference=reference;v.fresh=fresh;v.candidate_prepare=prepare;v.candidate_run=run
    return v


def test_owner_prepare_calls_actual_public_path_and_send_native_once(owner_candidate,monkeypatch):
    v=owner_candidate;uploads,sends=wire(monkeypatch);calls=[]
    public=audio_service.normalize_voice_audio
    def wrapped(*a,**kw):calls.append(audio_service.ffmpeg_encoder_enabled());return public(*a,**kw)
    monkeypatch.setattr(audio_service,'normalize_voice_audio',wrapped)
    response=v.candidate_prepare();assert response.status_code==200,response.text
    assert calls==[True] and not uploads and not sends and not audio_service.ffmpeg_encoder_enabled()
    rid=v.latest()
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        assert run.evidence['kind']==candidate.KIND
        assert run.evidence['reference']['stored_playback_missing'] is True
        assert not run.evidence['global_production_flag_at_prepare']
        assert run.evidence['normalization_trace']['stages']['ffmpeg_normalization']['backend']=='pinned_ffmpeg'
    assert v.candidate_run().status_code==200
    assert v.candidate_run().status_code==200 and len(sends)==1
    assert sends[0]['audio']=={'id':'media.encoding.1','voice':True}
    with v.sessions() as db:
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.get(VoicePlaybackTest,v.reference).playback is None
    assert v.client.post(BASE+'/candidate/results',data={'run_id':rid,'result':'plays','platform':'Android','csrf_token':v.csrf}).status_code==200
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).playback['P']['file']==run.evidence['variants']['P']['bytes']


@pytest.mark.parametrize('gate',['owner','confirm','count','expired','role','flag','old_source'])
def test_owner_verification_fail_closed(owner_candidate,monkeypatch,gate):
    v=owner_candidate;uploads,sends=wire(monkeypatch)
    if gate=='old_source':
        assert v.candidate_prepare(voice_id=v.row['id'],revision=v.row['revision']).status_code==409
    elif gate=='owner':assert v.candidate_prepare(owner_number='94770012302').status_code==400
    elif gate=='flag':
        monkeypatch.delenv('VOICE_FFMPEG_OWNER_TEST_ENABLED');assert v.candidate_prepare().status_code==409
    else:
        assert v.candidate_prepare().status_code==200
        if gate=='confirm':assert v.candidate_run(confirm_one='false').status_code==400
        if gate=='count':assert v.candidate_run(message_count=3).status_code==400
        if gate=='expired':
            with v.sessions() as db:db.get(VoicePlaybackTest,v.latest()).created_at-=timedelta(minutes=16);db.commit()
            assert v.candidate_run().status_code==409
        if gate=='role':v.login('quick-staff');assert v.candidate_run().status_code==403
    assert not uploads and not sends


def test_ordinary_candidate_control_does_not_set_voice(owner_candidate,monkeypatch):
    v=owner_candidate;wire_uploads,sends=wire(monkeypatch)
    assert v.candidate_prepare(mode='ordinary').status_code==200
    assert v.candidate_run().status_code==200 and sends[0]['audio']=={'id':'media.encoding.1'}


def test_saved_voice_and_browser_flag_on_workflows_preserve_originals(voices,monkeypatch):
    real_tools();v=voices
    old=v.create().json()['voice']
    original_files={p.name:p.read_bytes() for p in v.directory.iterdir()}
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    fresh=v.create(title='FFmpeg new record')
    assert fresh.status_code==201,fresh.text
    assert all((v.directory/name).read_bytes()==raw for name,raw in original_files.items())
    monkeypatch.setattr(main,'send_whatsapp_audio',lambda *a,**kw:'wamid.browser.candidate')
    response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',files={'audio':('fresh.wav',wav(),'audio/wav')},
        headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':'production-candidate-browser-001'})
    assert response.status_code==200,response.text
    assert len(v.uploads)==1 and audio_service.validate_voice_audio(v.uploads[0])['codec']=='opus'


def test_flag_on_does_not_prepare_or_reencode_historical_records(voices,monkeypatch):
    import saved_voice_service
    from fastapi import HTTPException
    v=voices;row=v.create().json()['voice']
    before={p.name:p.read_bytes() for p in v.directory.iterdir()}
    with v.sessions() as db:
        voice=db.get(SavedVoice,row['id']);voice.normalization_version=0;voice.processing_status='PENDING';db.commit()
        monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
        with pytest.raises(HTTPException) as error:saved_voice_service.prepare(db,voice)
        assert error.value.status_code==409 and voice.revision==1
    assert before=={p.name:p.read_bytes() for p in v.directory.iterdir()}


def test_variant_c_rejects_nonseekable_m4a_without_fallback(monkeypatch):
    real_tools()
    from test_voice_audio_compatibility import encoded
    source=encoded(seconds=10.56)
    assert source[4:8]==b'ftyp' and len(source)>32768
    assert source.index(b'moov')>source.index(b'mdat')
    with pytest.raises(ValueError):tools.encode(source)  # Pinned pipe emits demux errors, even if exit status is zero
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    with pytest.raises(audio_service.VoiceAudioError):audio_service.normalize_voice_audio(source,'audio/mp4')


def test_production_timeout_is_explicit_and_never_falls_back(monkeypatch):
    import subprocess
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(tools,'runtime',lambda:{'verified':True})
    monkeypatch.setattr(tools,'production_source',lambda raw:{'decoded_duration_seconds':.5})
    monkeypatch.setattr(audio_service,'_normalize_pyav',lambda *a:pytest.fail('Fallback'))
    seen=[]
    def timeout(*a,**kw):seen.append(kw);raise subprocess.TimeoutExpired('candidate',120)
    monkeypatch.setattr(tools.subprocess,'run',timeout)
    with pytest.raises(audio_service.VoiceAudioError):audio_service.normalize_voice_audio(wav())
    assert seen[0]['timeout']==120 and set(seen[0]['env'])=={'PATH','LANG','LC_ALL'}


def test_readonly_experiment_evidence_does_not_send(experiment,monkeypatch):
    from voice_experiment_evidence import report
    v=experiment;v.encoding_prepare();rid=v.latest();uploads,sends=wire(monkeypatch)
    v.encoding_run();assert len(sends)==3
    with v.sessions() as db:
        result=report(db,rid)
        assert result['missing']==['No stored phone results'] and result['live_sends']==0
        assert all(row['immutable_matches_upload'] and row['payload_uses_recorded_media_id'] for row in result['variants'])
    assert len(sends)==3
    data={'run_id':rid,'owner_confirm':'true','csrf_token':v.csrf}
    assert v.client.post(BASE+'/encoding/evidence',data={**data,'owner_confirm':'false'}).status_code==400
    assert v.client.post(BASE+'/encoding/evidence',data=data).status_code==200 and len(sends)==3


def test_remux_is_offline_and_preserves_codec_packets():
    real_tools();source=tools.encode_pyav(wav(),'current')
    result=tools._run('remux',source);assert result.returncode==0
    comparison=bits.compare(bits.profile(source),bits.profile(result.stdout))
    assert comparison['codec_packets_equal'] and comparison['coded_durations_equal']
    assert all(p['crc_valid'] for p in bits.profile(result.stdout)['pages'])


def test_candidate_concurrent_claim_and_unknown_send_never_repeat(owner_candidate,monkeypatch):
    from types import SimpleNamespace
    v=owner_candidate;v.candidate_prepare();rid=v.latest();uploads=[];sends=[]
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'media.candidate')
    def send(*a,**kw):sends.append(kw);return 'UNKNOWN',None
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',send)
    with v.sessions() as db:uid=db.get(VoicePlaybackTest,rid).staff_id
    def execute(_):
        with v.sessions() as db:candidate.execute(db,type('Admin',(),{'id':uid,'role':'ADMIN'})(),rid,True,1)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(execute,range(3)))
    assert len(uploads)==len(sends)==1
    assert v.candidate_run().status_code==200 and len(sends)==1
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).status=='INCOMPLETE'


def test_candidate_meta_requires_confirmation_and_cleanup_preserves_library(owner_candidate,monkeypatch):
    import voice_production_diagnostics
    v=owner_candidate;before={p.name:p.read_bytes() for p in v.directory.iterdir()}
    v.candidate_prepare();rid=v.latest();wire(monkeypatch);v.candidate_run();calls=[]
    monkeypatch.setattr(voice_production_diagnostics,'retrieve_meta',lambda mid,expected:calls.append((mid,expected)) or {'downloaded':expected})
    data={'run_id':rid,'csrf_token':v.csrf}
    assert v.client.post(BASE+'/candidate/meta',data=data).status_code==400 and not calls
    assert v.client.post(BASE+'/candidate/meta',data={**data,'owner_confirm':'true'}).status_code==200 and len(calls)==1
    assert v.client.post(BASE+'/candidate/close',data={**data,'confirm_delete':'true'}).status_code==200
    assert before=={p.name:p.read_bytes() for p in v.directory.iterdir()}
    assert v.client.get(BASE+'/candidate/'+rid+'/audio').status_code==404


def test_chromium_fresh_public_candidate_requires_one_message_confirmation(owner_candidate,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=owner_candidate;uploads,sends=wire(monkeypatch)
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='candidate.test':route.fulfill(status=200,body='');return
                response=v.client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:value for k,value in req.headers.items() if k in {'content-type','range'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,
                    headers={k:value for k,value in response.headers.items() if k not in {'set-cookie','content-length','content-encoding'}})
            page.route('**/*',serve);page.goto('https://candidate.test'+BASE)
            form=page.locator('form[action$="/candidate/prepare"]')
            form.locator('[name=reference_id]').fill(v.reference)
            form.locator('[name=voice_id]').select_option(str(v.fresh['id']))
            form.locator('[name=revision]').fill(str(v.fresh['revision']))
            form.locator('[name=owner_number]').fill('94770012301');form.locator('[name=owner_confirm]').check()
            form.get_by_role('button').click();page.get_by_role('button',name='Send One Candidate Message').wait_for()
            assert not uploads and not sends
            page.get_by_role('button',name='Send One Candidate Message').click();assert not sends
            page.locator('[name=confirm_one]').check();page.get_by_role('button',name='Send One Candidate Message').click()
            page.get_by_role('button',name='Record candidate phone result — send nothing').wait_for()
            assert len(sends)==1 and sends[0]['audio']['voice'] is True
            page.locator('[name=result]').select_option('plays')
            page.get_by_role('button',name='Record candidate phone result — send nothing').click()
            assert len(sends)==1 and not errors
        finally:browser.close()
