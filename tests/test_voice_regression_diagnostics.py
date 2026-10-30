"""Regression investigation remains offline; all Meta access is mocked."""
from io import BytesIO
from pathlib import Path
import json
import os
import shutil
import stat
import subprocess
import wave

import av
import httpx
import pytest

import audio_service
import voice_compatibility
import voice_regression_diagnostics as offline
import voice_production_diagnostics as diagnostics
import voice_trace
import whatsapp_service
from models import VoicePlaybackTest, SavedVoice, GlobalAIReplyControl
from test_voice_playback_test import ab, BASE
from test_saved_voices import voices, wav
from test_whatsapp_quick_messages import inbox


def test_same_source_current_historical_ffmpeg_and_aac_decode_without_sends(tmp_path,monkeypatch):
    monkeypatch.setattr(whatsapp_service.httpx,'post',lambda *a,**kw:pytest.fail('Offline test contacted Meta'))
    raw=wav();directory=offline.private_workspace(tmp_path/'private')
    report=offline.compare(raw,directory,known_working=audio_service.normalize_voice_audio(raw)[0])
    assert report['source']==voice_trace.fingerprint(raw) and report['live_sends']==0
    for name,suffix,codec in [('current','.ogg','opus'),('historical_pre_22b05f8','.ogg','opus'),('aac_m4a_offline_only','.m4a','aac')]:
        output=(directory/(name+suffix)).read_bytes()
        value=report['variants'][name]
        assert value['bytes']==voice_trace.fingerprint(output)
        assert value['format']['codec']==codec and value['sent'] is False
        assert value['candidate_extension']==suffix
        if shutil.which('ffmpeg'):assert value['independent']['ffmpeg']['returncode']==0
    assert 'not a verified' in report['interpretation']
    assert report['owner_reported_working']['provenance'].startswith('Owner supplied')
    assert all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in directory.iterdir())
    assert stat.S_IMODE(directory.stat().st_mode)==0o700
    if shutil.which('ffmpeg'):
        assert report['variants']['ffmpeg_cli']['ogg_opus']['encoded_packet_duration_ms_unique']==[20.0]


def test_historical_profile_matches_committed_old_encoder_packet_content(tmp_path):
    # Reference reproduces encoding lines from 22b05f8^ (audio_service SHA-256
    # 55e70f71cbe8b7482484ccaba8a19dd0eec3623829ebedc9fce565e8b7a4a622).
    from av.audio.resampler import AudioResampler
    raw=wav();output=BytesIO()
    with av.open(BytesIO(raw),mode='r') as source:
        with av.open(output,mode='w',format='ogg') as target:
            stream=target.add_stream('libopus',rate=48000);stream.layout='mono'
            resampler=AudioResampler(format='fltp',layout='mono',rate=48000)
            for frame in source.decode(audio=0):
                for converted in resampler.resample(frame):
                    for packet in stream.encode(converted):target.mux(packet)
            for converted in resampler.resample(None):
                for packet in stream.encode(converted):target.mux(packet)
            for packet in stream.encode(None):target.mux(packet)
    candidate=offline.historical_profile(raw)
    def packets(encoded):
        with av.open(BytesIO(encoded)) as source:return [bytes(p) for p in source.demux(audio=0) if p.size]
    # OGG random serials/CRC can differ; encoded packet payloads must match.
    assert packets(candidate)==packets(output.getvalue())


def test_coded_packet_duration_distinguished_from_trimmed_demux_duration():
    raw,_=audio_service.normalize_voice_audio(wav())
    report=voice_compatibility.inspect(raw)
    assert report['encoded_packet_duration_ms_unique']==[20.0]
    assert 6.5 in report['packet_duration_ms_unique']
    assert report['granule_duration_seconds']==.5
    assert report['pages'][0]['flags']==2 and report['pages'][-1]['flags']==4


def test_cli_tools_optional_and_errors_do_not_expose_decoder_text(tmp_path,monkeypatch):
    raw=wav();monkeypatch.setattr(offline.shutil,'which',lambda name:None)
    report=offline.compare(raw,tmp_path)
    assert report['variants']['ffmpeg_cli']['available'] is False
    assert report['variants']['current']['independent']['ffmpeg']['available'] is False
    monkeypatch.setattr(offline.shutil,'which',lambda name:'/not-shown/'+name)
    monkeypatch.setattr(offline.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess([],1,b'',b'private-url token customer-name'))
    result=offline.independent_inspection(raw,tmp_path)
    assert result['ffmpeg']['stderr_present'] and 'private-url' not in json.dumps(result)


@pytest.mark.parametrize('kind',['corrupt','long','symlink','public_output'])
def test_invalid_or_unsafe_sources_rejected_before_experiment(tmp_path,kind):
    path=tmp_path/'owner.audio'
    path.write_bytes(wav())
    if kind=='corrupt':
        with pytest.raises(audio_service.VoiceAudioError):offline.compare(b'not audio',tmp_path)
    elif kind=='long':
        output=BytesIO()
        with wave.open(output,'wb') as target:
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(8000);target.writeframes(b'\0\0'*8000*31)
        with pytest.raises(ValueError):offline.compare(output.getvalue(),tmp_path)
    elif kind=='symlink':
        (tmp_path/'link').symlink_to(path)
        with pytest.raises(OSError):offline.read_private_audio(tmp_path/'link')
    else:
        public=tmp_path/'public';public.mkdir(mode=0o755);public.chmod(0o755)
        with pytest.raises(ValueError):offline.private_workspace(public)


def test_meta_exact_download_bytes_can_be_inspected_offline_without_upload(tmp_path,monkeypatch):
    raw,_=audio_service.normalize_voice_audio(wav());calls=[]
    monkeypatch.setattr(diagnostics.settings,'META_WHATSAPP_ACCESS_TOKEN','private-token')
    def handler(request):
        calls.append(request);assert request.method=='GET'
        if request.url.host=='graph.facebook.com':return httpx.Response(200,json={'url':'https://lookaside.fbsbx.com/file?private-signed-url','mime_type':'audio/ogg','file_size':len(raw),'sha256':voice_trace.fingerprint(raw)['sha256']})
        return httpx.Response(200,content=raw)
    def inspect(value):
        assert value==raw
        offline.write_private(tmp_path,'meta-hosted.audio',value)
        return offline.analyze(value,tmp_path)
    result=diagnostics.retrieve_meta('owner-media',voice_trace.fingerprint(raw),transport=httpx.MockTransport(handler),offline_inspector=inspect)
    assert len(calls)==2 and (tmp_path/'meta-hosted.audio').read_bytes()==raw
    assert result['expected_upload_matches'] and result['offline_inspection']['ogg_opus']['validation']['codec']=='opus'
    assert 'private-token' not in json.dumps(result) and 'private-signed-url' not in json.dumps(result)


def test_offline_admin_action_preserves_originals_ai_and_idempotency(ab,monkeypatch):
    v=ab;v.prepare();rid=v.latest()
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:pytest.fail('Offline action uploaded media'))
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:pytest.fail('Offline action sent message'))
    with v.sessions() as db:
        voice=db.get(SavedVoice,v.row['id']);before={p.name:p.read_bytes() for p in v.directory.iterdir()}
    assert v.client.get(BASE,params={'run_id':rid}).status_code==200
    data={'run_id':rid,'csrf_token':v.csrf,'owner_confirm':'true'}
    assert v.client.post(BASE+'/offline',data={**data,'owner_confirm':'false'}).status_code==400
    assert v.client.post(BASE+'/offline',data={**data,'csrf_token':'wrong'}).status_code==403
    response=v.client.post(BASE+'/offline',data=data)
    assert response.status_code==200 and 'offline_comparison' in response.text
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        assert run.status=='REVIEW' and run.confirmed_at is None
        assert run.evidence['offline_comparison']['live_sends']==0
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert before=={p.name:p.read_bytes() for p in v.directory.iterdir()}
    v.login('quick-staff')
    assert v.client.post(BASE+'/offline',data=data).status_code==403


def test_offline_action_disabled_or_changed_source_fails_closed(ab,monkeypatch):
    v=ab;v.prepare();rid=v.latest();data={'run_id':rid,'csrf_token':v.csrf,'owner_confirm':'true'}
    monkeypatch.setenv('VOICE_PLAYBACK_TEST_ENABLED','false')
    assert v.client.post(BASE+'/offline',data=data).status_code==409
    monkeypatch.setenv('VOICE_PLAYBACK_TEST_ENABLED','true')
    from saved_voice_service import storage_path
    with v.sessions() as db:storage_path(db.get(SavedVoice,v.row['id']).original_storage_name).write_bytes(b'changed')
    assert v.client.post(BASE+'/offline',data=data).status_code==409


def test_multipart_contract_filename_mime_and_actual_bytes_match(monkeypatch):
    from email.parser import BytesParser
    from email.policy import default
    raw,_=audio_service.normalize_voice_audio(wav());calls=[]
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-token')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','private-business')
    def post(url,**kw):
        calls.append(url)
        request=httpx.Request('POST',url,**{k:v for k,v in kw.items() if k in {'headers','data','files','json'}})
        parsed=BytesParser(policy=default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+request.read())
        parts={p.get_param('name',header='content-disposition'):p for p in parsed.iter_parts()}
        assert parts['file'].get_filename()=='voice-note.ogg'
        assert parts['file'].get_content_type()=='audio/ogg'
        assert parts['file'].get_payload(decode=True)==raw
        assert parts['type'].get_payload(decode=True)==b'audio/ogg'
        assert parts['messaging_product'].get_payload(decode=True)==b'whatsapp'
        return httpx.Response(200,request=request,json={'id':'returned-owner-media'})
    monkeypatch.setattr(whatsapp_service.httpx,'post',post)
    assert whatsapp_service.upload_whatsapp_audio(raw)=='returned-owner-media'
    assert len(calls)==1 and calls[0].endswith('/private-business/media')


def test_meta_parameterized_ogg_mime_is_recorded_not_silently_hidden(monkeypatch):
    raw,_=audio_service.normalize_voice_audio(wav())
    monkeypatch.setattr(diagnostics.settings,'META_WHATSAPP_ACCESS_TOKEN','test-token')
    def handler(request):
        if request.url.host=='graph.facebook.com':return httpx.Response(200,json={'url':'https://lookaside.fbsbx.com/owner','mime_type':'audio/ogg; codecs=opus'})
        return httpx.Response(200,content=raw)
    report=diagnostics.retrieve_meta('owner-media',transport=httpx.MockTransport(handler))
    assert report['metadata']['mime_type']=='audio/ogg; codecs=opus'


def test_opus_tags_report_never_contains_comments_or_vendor_content():
    import struct
    private=b'private customer phone token'
    tags=b'OpusTags'+struct.pack('<I',len(private))+private+struct.pack('<I',1)+struct.pack('<I',len(private))+private
    report=voice_compatibility._tags(tags)
    assert report['structure_valid'] and report['comment_count']==1
    assert 'private' not in json.dumps(report)
    assert not voice_compatibility._tags(tags[:-1])['structure_valid']


def test_owner_reference_pair_reads_exact_media_and_never_substitutes_current_files(ab,tmp_path,monkeypatch):
    from models import SavedVoiceSend
    v=ab
    row=v.row
    v.send(row,key='offline-old-reference-01')
    v.send(row,key='offline-failed-reference-02')
    old=offline.historical_profile(wav());failed=audio_service.normalize_voice_audio(wav())[0]
    with v.sessions() as db:
        attempts=db.query(SavedVoiceSend).order_by(SavedVoiceSend.id).all()
        attempts[0].media_id='old-owner-media';attempts[0].audio_trace=None
        attempts[1].media_id='failed-owner-media';db.commit()
        ids=[a.id for a in attempts]
    calls=[]
    def retrieve(media_id,expected,**kw):
        calls.append(media_id)
        raw=old if media_id=='old-owner-media' else failed
        return {'downloaded':voice_trace.fingerprint(raw),'offline_inspection':kw['offline_inspector'](raw)}
    monkeypatch.setattr(diagnostics,'retrieve_meta',retrieve)
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda *a:pytest.fail('Pair uploaded media'))
    with v.sessions() as db:report=offline.compare_meta_attempts(db,'saved',ids[0],'saved',ids[1],tmp_path)
    assert calls==['old-owner-media','failed-owner-media'] and report['live_sends']==0
    assert (tmp_path/'working_reference'/'meta-hosted.audio').read_bytes()==old
    assert (tmp_path/'failed_reference'/'meta-hosted.audio').read_bytes()==failed
    assert report['stream_field_comparison']['pre_skip_samples']['equal'] is True
    assert 'Inbox playback is not Android' in report['interpretation']


def test_owner_pair_ui_rejects_customer_reference_before_meta_and_preserves_ai(ab,monkeypatch):
    from models import SavedVoiceSend
    v=ab;v.prepare();rid=v.latest();v.send(v.row,key='pair-own-allowed-reference');v.send(v.row,key='pair-customer-forbidden',cid=v.ids[1])
    with v.sessions() as db:ids=[row.id for row in db.query(SavedVoiceSend).order_by(SavedVoiceSend.id)]
    calls=[]
    monkeypatch.setattr(offline,'compare_meta_attempts',lambda *args:calls.append(args[1:5]) or {'live_sends':0})
    data={'run_id':rid,'working_route':'saved','working_attempt_id':ids[0],'failed_route':'saved','failed_attempt_id':ids[1],
        'owner_confirm':'true','csrf_token':v.csrf}
    assert v.client.post(BASE+'/pair',data=data).status_code==404 and not calls
    data['failed_attempt_id']=ids[0]
    assert v.client.post(BASE+'/pair',data={**data,'owner_confirm':'false'}).status_code==400 and not calls
    assert v.client.post(BASE+'/pair',data={**data,'csrf_token':'wrong'}).status_code==403 and not calls
    assert v.client.post(BASE+'/pair',data=data).status_code==200 and len(calls)==1
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        assert run.evidence['reference_pair']['live_sends']==0 and run.status=='REVIEW'
        assert db.get(GlobalAIReplyControl,1).enabled is False
    v.login('quick-staff')
    assert v.client.post(BASE+'/pair',data=data).status_code==403 and len(calls)==1


def test_unavailable_pair_media_keeps_unknown_fields_not_matching(tmp_path,monkeypatch):
    monkeypatch.setattr(offline,'analyze_meta_attempt',lambda *args:{'retrieval':{'error':'Media expired'}})
    report=offline.compare_meta_attempts(None,'saved',1,'saved',56,tmp_path)
    assert all(value['equal'] is None for value in report['stream_field_comparison'].values())
