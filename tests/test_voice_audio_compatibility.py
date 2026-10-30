"""Real codec/container regression fixtures; all Meta requests are mocked."""
from io import BytesIO
from datetime import datetime, timedelta
import hashlib
import json
import logging
import struct

import av
from av.audio.resampler import AudioResampler
import pytest
from fastapi import HTTPException

import audio_service as audio
import saved_voice_service as service
import whatsapp_service
import main
from models import SavedVoice, SavedVoiceSend, WhatsAppConversationMessage, GlobalAIReplyControl
from test_audio_service import wav_recording
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox


def encoded(codec='aac', container='ipod', seconds=7.3):
    output=BytesIO()
    with av.open(BytesIO(wav_recording(seconds))) as source:
        with av.open(output,'w',format=container) as target:
            stream=target.add_stream(codec,rate=48000);stream.layout='mono';stream.bit_rate=64000
            resampler=AudioResampler(format='fltp' if codec=='aac' else 'flt',layout='mono',rate=48000)
            for frame in source.decode(audio=0):
                for value in resampler.resample(frame):
                    for packet in stream.encode(value):target.mux(packet)
            for value in resampler.resample(None):
                for packet in stream.encode(value):target.mux(packet)
            for packet in stream.encode(None):target.mux(packet)
    return output.getvalue()


@pytest.mark.parametrize('mime',['audio/mp4','audio/x-m4a','audio/aac','audio/ogg','application/octet-stream'])
def test_actual_m4a_aac_is_transcoded_not_relabelled(mime):
    source=encoded();assert source[4:8]==b'ftyp'
    normalized,duration=audio.normalize_voice_audio(source,mime)
    assert normalized != source and normalized.startswith(b'OggS')
    info=audio.validate_voice_audio(normalized)
    assert (info['container'],info['codec'],info['channels'],info['sample_rate'])==('ogg','opus',1,48000)
    assert duration == pytest.approx(7.3,abs=.1)
    with av.open(BytesIO(normalized)) as decoder:
        frames=list(decoder.decode(audio=0))
        assert sum(f.samples for f in frames)/48000 == pytest.approx(duration,abs=.02)
        assert max(abs(value[0]) for f in frames for value in struct.iter_unpack("<f", bytes(f.planes[0])[:f.samples*4]))>.01


@pytest.mark.parametrize('codec,container,mime',[('libopus','ogg','audio/ogg'),('libopus','webm','audio/webm'),('aac','adts','audio/aac')])
def test_supported_existing_ogg_browser_webm_and_raw_aac(codec,container,mime):
    result,duration=audio.normalize_voice_audio(encoded(codec,container,1),mime)
    assert audio.validate_voice_audio(result)['duration_seconds'] == pytest.approx(1,abs=.1)


@pytest.mark.parametrize('raw',[b'',b'OggS',b'not an audio file',b'#EXTM3U\nhttps://private/audio',b'RIFF'+b'\0'*20,b'\0'*4+b'ftyp'+b'\0'*24])
def test_empty_unsupported_or_corrupt_audio_is_rejected(raw):
    with pytest.raises(audio.VoiceAudioError):audio.normalize_voice_audio(raw,'audio/ogg')


def test_crc_truncation_and_eos_are_validated():
    raw,_=audio.normalize_voice_audio(wav_recording(),'audio/wav')
    for bad in (raw[:-1],raw[:70],raw[:40]+bytes([raw[40]^1])+raw[41:]):
        with pytest.raises(audio.VoiceAudioError):audio.validate_voice_audio(bad)


def test_encoder_failure_is_safe_and_not_ready(monkeypatch,voices):
    original=audio.av.open
    def fail(*args,**kwargs):
        if kwargs.get('mode')=='w':raise RuntimeError('private-path-must-not-be-shown')
        return original(*args,**kwargs)
    monkeypatch.setattr(audio.av,'open',fail)
    result=voices.create()
    assert result.status_code==422 and 'Conversion failed' in result.json()['detail']
    assert 'private-path' not in result.text
    with voices.sessions() as db:assert db.query(SavedVoice).count()==0


def test_upload_saved_preview_and_meta_bytes_are_normalized_with_ai_off(voices):
    v=voices;source=encoded()
    with v.sessions() as db:
        control=db.get(GlobalAIReplyControl,1);control.enabled=False;db.commit()
    response=v.client.post('/crm/saved-voices/api',data={'title':'Woman recording','category':'Delivery','transcript':'Test words','language':'singlish'},
        files={'audio':('Voice 036.m4a',source,'audio/ogg')},headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==201,response.text
    row=response.json()['voice'];assert row['ready_to_send'] and row['processing_status']=='READY'
    preview=v.client.get(f"/crm/saved-voices/api/{row['id']}/audio")
    assert preview.status_code==200 and preview.headers['content-type']=='audio/ogg'
    audio.validate_voice_audio(preview.content)
    with v.sessions() as db:
        saved=db.get(SavedVoice,row['id'])
        assert service.storage_path(saved.original_storage_name).read_bytes()==source
        assert saved.audio_diagnostics['source']['codec']=='aac'
    assert v.send(row).status_code==200
    assert v.uploads==[preview.content] and len(v.sends)==1
    assert v.send(row).status_code==200 and len(v.sends)==1
    with v.sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False


def legacy(v, raw):
    name=service.write_audio(raw)
    with v.sessions() as db:
        row=SavedVoice(title='Legacy',category='General',transcript='Test words',language='singlish',
            storage_name=name,sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),duration_seconds=7.3,
            active=True,revision=1,created_by='admin',updated_by='admin')
        db.add(row);db.commit();return row.id,name


def test_legacy_mislabeled_bytes_are_prepared_with_original_retained_and_review_required(voices):
    v=voices;source=encoded();voice_id,name=legacy(v,source)
    old={'id':voice_id,'revision':1}
    assert v.send(old).status_code==409 and not v.sends and not v.uploads
    current=v.client.get('/crm/saved-voices/api').json()['voices'][0]
    assert current['ready_to_send'] and current['revision']==2
    assert service.storage_path(name).read_bytes()==source
    preview=v.client.get(f'/crm/saved-voices/api/{voice_id}/audio');audio.validate_voice_audio(preview.content)
    assert v.send(current,key='legacy-reviewed-key-0001').status_code==200 and len(v.sends)==1


def test_corrupt_legacy_is_failed_and_cannot_send(voices):
    v=voices;voice_id,name=legacy(v,b'invalid audio')
    response=v.client.post(f'/crm/saved-voices/api/{voice_id}/prepare',headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==422
    listed=v.client.get('/crm/saved-voices/api').json()['voices'][0]
    assert listed['processing_status']=='FAILED' and not listed['ready_to_send']
    assert v.send(listed).status_code==422 and not v.uploads and not v.sends
    assert service.storage_path(name).read_bytes()==b'invalid audio'


def test_processing_lease_blocks_send_and_expired_work_can_be_prepared(voices):
    v=voices;voice_id,_=legacy(v,encoded())
    with v.sessions() as db:
        row=db.get(SavedVoice,voice_id);row.processing_status='PROCESSING';row.normalization_started_at=datetime.utcnow();db.commit()
    endpoint=f'/crm/saved-voices/api/{voice_id}/prepare'
    assert v.client.post(endpoint,headers={'X-CSRF-Token':v.csrf}).status_code==409
    with v.sessions() as db:
        row=db.get(SavedVoice,voice_id);row.normalization_started_at=datetime.utcnow()-timedelta(minutes=3);db.commit()
    assert v.client.post(endpoint,headers={'X-CSRF-Token':v.csrf}).json()['voice']['ready_to_send']


def test_meta_upload_rejects_aac_disguised_as_ogg_before_network(monkeypatch):
    monkeypatch.setattr(whatsapp_service.httpx,'post',lambda *a,**k:pytest.fail('Unvalidated bytes reached Meta'))
    assert whatsapp_service.upload_whatsapp_audio(encoded()) is None


def test_delivery_failure_code_is_saved_without_private_error_text(voices,monkeypatch,caplog):
    v=voices;row=v.create().json()['voice'];assert v.send(row).status_code==200
    with v.sessions() as db:attempt=db.query(SavedVoiceSend).one();message_id=attempt.whatsapp_message_id
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    caplog.set_level(logging.INFO)
    data={'entry':[{'changes':[{'value':{'statuses':[{'id':message_id,'status':'failed','recipient_id':'private-phone',
        'errors':[{'code':131053,'message':'private-provider-message-with-token'}]}]}}]}]}
    assert v.client.post('/webhook',json=data).status_code==200
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one()
        assert attempt.delivery_status=='failed' and attempt.delivery_error_code==131053
    assert 'private-phone' not in caplog.text and 'private-provider-message' not in caplog.text


def test_failed_normalized_file_can_be_rebuilt_from_retained_source(voices):
    v=voices;row=v.create().json()['voice']
    with v.sessions() as db:
        saved=db.get(SavedVoice,row['id']);service.storage_path(saved.storage_name).write_bytes(b'corrupt')
    assert v.send(row).status_code==503
    prepared=v.client.post(f"/crm/saved-voices/api/{row['id']}/prepare",headers={'X-CSRF-Token':v.csrf})
    assert prepared.status_code==200 and prepared.json()['voice']['ready_to_send']
    normalized=v.client.get(f"/crm/saved-voices/api/{row['id']}/audio");audio.validate_voice_audio(normalized.content)
    assert not v.uploads and not v.sends


def test_postgresql_migration_and_concurrent_legacy_prepare(tmp_path,monkeypatch):
    import os
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from pathlib import Path
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from alembic import command
    from alembic.config import Config
    import database
    from models import Base
    from settings import settings
    url=os.environ.get('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Task-owned PostgreSQL not configured')
    admin=create_engine(url);assert admin.url.database=='pixiepinks_test'
    schema='voice_fix_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(settings,'SAVED_VOICES_DIR',str(tmp_path/'pg-voices'))
    try:
        Base.metadata.create_all(engine)
        source=encoded();name=service.write_audio(source)
        with sessions() as db:
            row=SavedVoice(title='Legacy',category='General',transcript='Words',language='singlish',storage_name=name,
                sha256=hashlib.sha256(source).hexdigest(),size_bytes=len(source),duration_seconds=7.3,created_by='admin',updated_by='admin')
            db.add(row);db.get(GlobalAIReplyControl,1).enabled=False;db.commit();voice_id=row.id
        new_columns=['audio_diagnostics','normalization_started_at','normalization_token','normalization_error','processing_status','normalization_version','original_storage_name']
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX ix_saved_voice_sends_whatsapp_message_id"))
            for field in new_columns:conn.execute(text(f'ALTER TABLE saved_voices DROP COLUMN {field}'))
            for field in ['delivery_status','delivery_error_code']:conn.execute(text(f'ALTER TABLE saved_voice_sends DROP COLUMN {field}'))
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261029_18');command.upgrade(config,'head');command.upgrade(config,'head')
        with sessions() as db:
            row=db.get(SavedVoice,voice_id);assert row.processing_status=='PENDING' and row.storage_name==name
            assert db.get(GlobalAIReplyControl,1).enabled is False
        original=service.normalize_voice_audio;entered=Event();release=Event();conversions=[]
        def convert(*args):
            conversions.append(True);entered.set();assert release.wait(timeout=10);return original(*args)
        monkeypatch.setattr(service,'normalize_voice_audio',convert)
        def prepare():
            with sessions() as db:
                try:service.prepare(db,db.get(SavedVoice,voice_id),retry=True);return 'ready'
                except HTTPException as exc:return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(prepare);assert entered.wait(timeout=10)
            second=pool.submit(prepare);assert second.result(timeout=5)==409
            release.set();assert first.result(timeout=10)=='ready'
        assert len(conversions)==1 and service.storage_path(name).read_bytes()==source
        with sessions() as db:
            row=db.get(SavedVoice,voice_id);assert row.revision==2 and row.processing_status=='READY'
            audio.validate_voice_audio(service.read_audio(row))
            assert db.get(GlobalAIReplyControl,1).enabled is False
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_fresh_media_upload_per_new_attempt_and_plain_audio_is_distinct(voices,monkeypatch):
    import httpx
    v=voices;row=v.create().json()['voice'];ids=[]
    monkeypatch.setattr(main,'upload_whatsapp_audio',lambda raw:f'media.{len(ids)+1}')
    monkeypatch.setattr(main,'send_whatsapp_audio',lambda phone,media,**kw:ids.append(media) or ('SENT',f'wamid.{len(ids)}'))
    assert v.send(row,key='voice-attempt-fresh-001').status_code==200
    assert v.send(row,key='voice-attempt-fresh-001').status_code==200
    assert v.send(row,key='voice-attempt-fresh-002').status_code==200
    assert ids==['media.1','media.2']
    payloads=[]
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','fake')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','fake')
    def post(url,**kw):
        payloads.append(kw['json'])
        return httpx.Response(200,request=httpx.Request('POST',url),json={'messages':[{'id':'wamid.fake'}]})
    monkeypatch.setattr(whatsapp_service.httpx,'post',post)
    whatsapp_service.send_whatsapp_audio('not-logged','media.voice',voice=True)
    whatsapp_service.send_whatsapp_audio('not-logged','media.ordinary',voice=False)
    assert payloads[0]['audio']=={'id':'media.voice','voice':True}
    assert payloads[1]['audio']=={'id':'media.ordinary'}
    assert all(p['type']=='audio' and 'text' not in p for p in payloads)


def test_chromium_legacy_status_normalized_preview_and_failed_selection(voices):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=voices;legacy(v,encoded());failed_id,_=legacy(v,b'bad audio')
    v.client.post(f'/crm/saved-voices/api/{failed_id}/prepare',headers={'X-CSRF-Token':v.csrf})
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='voice-codecs.test':route.fulfill(status=200,body='');return
                response=v.client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
                    headers={k:value for k,value in request.headers.items() if k in {'content-type','x-csrf-token','range'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k in {'content-type','content-range','accept-ranges','cache-control'}})
            page.route('**/*',serve)
            page.goto(f'https://voice-codecs.test/crm/whatsapp?conversation_id={v.ids[0]}')
            page.locator(f'[data-conversation-id="{v.ids[0]}"]').click();page.locator('#saved-voices-open').click()
            cards=page.locator('.voice-library-card')
            failed=cards.filter(has_text='Conversion failed');api.expect(failed.get_by_role('button',name='Choose recording')).to_be_disabled()
            pending=cards.filter(has_text='Needs validation')
            api.expect(pending.get_by_role('button',name='Choose recording')).to_be_disabled()
            pending.get_by_role('button',name='Prepare recording').click()
            ready=cards.filter(has_text='Ready to send');api.expect(ready).to_have_count(1)
            ready.get_by_role('button',name='Choose recording').click()
            player=page.locator('#saved-voice-selected-audio')
            player.evaluate('(audio) => { audio.load(); return audio.play(); }')
            page.wait_for_function('document.getElementById("saved-voice-selected-audio").currentTime > 0.05')
            assert player.evaluate('(audio) => audio.error === null && audio.duration > 7')
            assert not v.uploads and not v.sends and not errors
        finally:browser.close()
