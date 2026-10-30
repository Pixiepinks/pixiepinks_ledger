"""Evidence checks reproduce missing/mismatched boundaries, not a claimed phone fix."""
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import audio_service
import build_info
import main
import voice_production_diagnostics as diagnostics
import voice_trace
import whatsapp_service
from models import SavedVoiceSend, SavedVoice, WhatsAppManualSend, GlobalAIReplyControl
from test_saved_voices import voices, wav
from test_whatsapp_quick_messages import inbox


def wire(monkeypatch):
    uploaded=[];sent=[]
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-secret-token')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','private-business-id')
    monkeypatch.setattr(main,'upload_whatsapp_audio',whatsapp_service.upload_whatsapp_audio)
    monkeypatch.setattr(main,'send_whatsapp_audio',whatsapp_service.send_whatsapp_audio)
    def post(url,**kw):
        request=httpx.Request('POST',url,**{k:v for k,v in kw.items() if k in {'headers','data','files','json'}})
        if url.endswith('/media'):
            body=request.read()
            parsed=BytesParser(policy=default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+body)
            file=next(part for part in parsed.iter_parts() if part.get_param('name',header='content-disposition')=='file')
            uploaded.append(file.get_payload(decode=True))
            assert file.get_content_type()=='audio/ogg'
            payload={'id':'1234567'}
        else:
            sent.append(kw['json']);payload={'messages':[{'id':'wamid.owner.test'}]}
        return httpx.Response(200,request=request,json=payload)
    monkeypatch.setattr(whatsapp_service.httpx,'post',post)
    return uploaded,sent


def test_saved_attempt_proves_exact_validated_multipart_bytes_and_correct_media_id(voices,monkeypatch):
    v=voices;uploaded,sent=wire(monkeypatch)
    with v.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    row=v.create().json()['voice'];preview=v.client.get(f"/crm/saved-voices/api/{row['id']}/audio").content
    assert v.send(row).status_code==200
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one();report=diagnostics.attempt_report(db,'saved',attempt.id)
        assert report['analysis']['status']=='complete_matching_byte_evidence'
        assert report['analysis']['media_id_reference_matches'] is True
        trace=attempt.audio_trace
        assert trace['route']=='saved_voice' and trace['recording_id']==row['id']
        assert trace['returned_media_id']=='1234567' and trace['returned_whatsapp_message_id']=='wamid.owner.test'
        expected=voice_trace.fingerprint(preview)
        for stage in ['normalized_record','storage_read','upload_validated','multipart_file']:
            assert all(trace['stages'][stage][key]==value for key,value in expected.items())
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert uploaded==[preview] and sent[0]['audio']=={'id':'1234567','voice':True}
    assert v.send(row).status_code==200 and len(uploaded)==len(sent)==1


def test_browser_recorder_is_distinct_and_has_no_persistent_audio_storage(inbox,monkeypatch):
    client,sessions,ids,_,_,csrf=inbox;uploaded,sent=wire(monkeypatch)
    raw=wav()
    response=client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/send-audio',files={'audio':('voice.wav',raw,'audio/wav')},headers={'Idempotency-Key':'trace-browser-voice-001'})
    assert response.status_code==200,response.text
    with sessions() as db:
        attempt=db.query(WhatsAppManualSend).filter_by(message_text='Voice message').one()
        report=diagnostics.attempt_report(db,'browser',attempt.id)
        trace=attempt.audio_trace
        assert trace['stages']['browser_upload']==voice_trace.fingerprint(raw)
        assert trace['stages']['normalized_memory']==voice_trace.fingerprint(uploaded[0])
        assert trace['persistent_audio_storage']=='not_used_by_this_route'
        assert report['analysis']['status']=='complete_matching_byte_evidence'
        assert report['meta_media_id']=='1234567'
    assert len(sent)==1


def test_mismatch_is_reported_instead_of_claiming_the_current_file_proves_old_upload():
    trace=voice_trace.new('saved_voice')
    for stage,data in [('normalized_record',b'one'),('storage_read',b'one'),('upload_validated',b'two'),('multipart_file',b'two')]:
        trace['stages'][stage]=voice_trace.fingerprint(data)
    report=diagnostics.comparisons(trace)
    assert report['first_observed_mismatch']=='storage_read -> upload_validated'
    assert report['phone_playback']=='not_observable_from_server'
    assert diagnostics.comparisons(None)['status']=='unobserved_before_instrumentation'
    assert diagnostics.comparisons(voice_trace.new('saved_voice'))['status']=='incomplete_evidence'
    trace['stages']={'upload_result':{'media_id':'one'},'send_payload':{'audio':{'id':'two'}}}
    assert diagnostics.comparisons(trace)['media_id_reference_matches'] is False


def test_historical_attempt_is_explicitly_unobserved_not_backfilled(voices):
    v=voices;row=v.create().json()['voice'];v.send(row)
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one();attempt.audio_trace=None;db.commit()
        report=diagnostics.attempt_report(db,'saved',attempt.id)
        assert report['analysis']['status']=='unobserved_before_instrumentation'
        assert report['current_stored_bytes']['sha256']
        assert report['current_bytes_are_historical_send_proof'] is False


def test_upload_error_preserves_trace_and_does_not_send(voices,monkeypatch):
    v=voices;wire(monkeypatch)
    def fail(url,**kw):
        assert url.endswith('/media')
        return httpx.Response(400,request=httpx.Request('POST',url),json={'error':{'code':131053,'message':'private-provider-message'}})
    monkeypatch.setattr(whatsapp_service.httpx,'post',fail)
    row=v.create().json()['voice'];response=v.send(row)
    assert response.json()['status']=='FAILED'
    with v.sessions() as db:
        trace=db.query(SavedVoiceSend).one().audio_trace
        assert trace['stages']['upload_http']['error_code']==131053
        assert 'send_payload' not in trace['stages']
        assert 'private-provider-message' not in json.dumps(trace)


def test_runtime_page_admin_only_no_cache_no_secrets_and_no_setting_writes(inbox,monkeypatch):
    client,sessions,ids,sends,login,csrf=inbox
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA','a'*40)
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-secret-token')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','private-business-id')
    with sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    page=client.get('/crm/bot-management/voice-diagnostics')
    assert page.status_code==200 and page.headers['cache-control']=='private, no-store'
    assert 'a'*40 in page.text and '20261030_20' in page.text
    assert 'private-secret-token' not in page.text and 'private-business-id' not in page.text and '94770012301' not in page.text
    assert 'normalization_version' in page.text and 'audio_self_check' in page.text
    with sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False
    login('quick-manager');assert client.get('/crm/bot-management/voice-diagnostics').status_code==403
    assert not sends


def test_unknown_and_mismatched_build_provenance_is_never_invented(tmp_path,monkeypatch):
    monkeypatch.setattr(build_info,'ROOT',tmp_path)
    monkeypatch.delenv('RAILWAY_GIT_COMMIT_SHA',raising=False)
    result=build_info.runtime();assert result['artifact_commit'] is None and result['platform_reported_commit'] is None
    (tmp_path/'build_revision.json').write_text(json.dumps({'commit':'a'*40,'file_sha256':{'audio_service.py':'wrong'}}))
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA','b'*40)
    result=build_info.runtime()
    assert result['commit_sources_agree'] is False and result['artifact_files_match'] is False
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA','private-not-a-sha')
    assert build_info.runtime()['platform_reported_commit'] is None


@pytest.mark.parametrize('changed',[False,True])
def test_readonly_meta_metadata_download_compares_actual_bytes_and_never_sends(monkeypatch,changed):
    good,_=audio_service.normalize_voice_audio(wav(),'audio/wav');other=good+b'changed' if changed else good
    monkeypatch.setattr(diagnostics.settings,'META_WHATSAPP_ACCESS_TOKEN','private-secret-token')
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.method=='GET'
        if request.url.host=='graph.facebook.com':
            return httpx.Response(200,json={'url':'https://lookaside.fbsbx.com/audio?signed=private-url',
                'mime_type':'audio/ogg','file_size':len(other),'sha256':base64.b64encode(hashlib.sha256(other).digest()).decode()})
        return httpx.Response(200,content=other)
    result=diagnostics.retrieve_meta('1234567',voice_trace.fingerprint(good),transport=httpx.MockTransport(handler))
    assert result['expected_upload_matches'] is (not changed)
    assert result['metadata_hash_matches'] is True
    assert result['ogg_opus_validation']['passed'] is (not changed)
    assert len(calls)==2 and 'private-secret-token' not in json.dumps(result) and 'private-url' not in json.dumps(result)


@pytest.mark.parametrize('url',['https://evil.example/file','http://lookaside.fbsbx.com/file','https://lookaside.fbsbx.com.evil.example/file'])
def test_meta_signed_download_never_forwards_credentials_to_untrusted_host(monkeypatch,url):
    monkeypatch.setattr(diagnostics.settings,'META_WHATSAPP_ACCESS_TOKEN','private-token');calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,json={'url':url})
    result=diagnostics.retrieve_meta('12345',transport=httpx.MockTransport(handler))
    assert result['error']=='Meta returned an untrusted download URL' and len(calls)==1


def test_nullable_trace_migration_preserves_existing_data_and_global_off(tmp_path,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from models import Base
    import database
    engine=create_engine(f"sqlite:///{tmp_path/'migration.db'}");Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in ['saved_voice_sends','whatsapp_manual_sends']:conn.execute(text(f'ALTER TABLE {table} DROP COLUMN audio_trace'))
        conn.execute(text('UPDATE global_ai_reply_control SET enabled=false,generation=7 WHERE id=1'))
    monkeypatch.setattr(database,'engine',engine)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(config,'20261030_19');command.upgrade(config,'head');command.upgrade(config,'head')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT generation FROM global_ai_reply_control')).scalar()==7
        assert conn.execute(text('SELECT enabled FROM global_ai_reply_control')).scalar()==0
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261030_20'
    engine.dispose()


def test_local_message_lookup_does_not_confuse_message_and_attempt_ids(voices):
    v=voices;row=v.create().json()['voice'];v.send(row)
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one()
        found=diagnostics.lookup_saved_message(db,attempt.message_id,row['id'])
        assert found['attempt_id']==attempt.id and found['recording_id']==row['id']
        assert 'error' in diagnostics.lookup_saved_message(db,attempt.message_id,row['id']+100)
        message_id=attempt.message_id
    page=v.client.get(f"/crm/bot-management/voice-diagnostics?message_id={message_id}&recording_id={row['id']}")
    assert page.status_code==200 and 'Saved Voices' in page.text
    assert v.client.get('/crm/bot-management/voice-diagnostics?message_id=0').status_code==422


def test_corrupt_current_storage_is_reported_as_evidence_not_hidden(voices):
    v=voices;row=v.create().json()['voice'];v.send(row)
    with v.sessions() as db:
        import saved_voice_service
        voice=db.get(SavedVoice,row['id']);saved_voice_service.storage_path(voice.storage_name).write_bytes(b'changed')
        report=diagnostics.attempt_report(db,'saved',db.query(SavedVoiceSend).one().id)
        assert report['current_stored_bytes']==voice_trace.fingerprint(b'changed')
        assert report['current_stored_matches_record_metadata'] is False
        assert report['current_stored_audio']['passed'] is False


def test_owner_probe_works_without_trace_column_and_only_for_matching_media(voices,monkeypatch):
    import voice_owner_probe
    v=voices;row=v.create().json()['voice'];v.send(row)
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one();attempt.media_id='2062516331063243';db.commit()
        message_id=attempt.message_id
        raw=v.client.get(f"/crm/saved-voices/api/{row['id']}/audio").content
        monkeypatch.setattr(voice_owner_probe,'download_whatsapp_media',lambda mid:(raw,'audio/ogg'))
        report=voice_owner_probe.probe(db,message_id,row['id'],'2062516331063243',retrieve=True)
        assert report['current_file_matches_meta_download'] is True
        assert report['historical_upload_hash']=='not_recorded_in_schema_19'
        assert voice_owner_probe.probe(db,message_id,row['id'],'999',retrieve=True).get('error')
    engine=v.sessions.kw['bind']
    with engine.begin() as conn:conn.execute(text('ALTER TABLE saved_voice_sends DROP COLUMN audio_trace'))
    with v.sessions() as db:
        report=voice_owner_probe.probe(db,message_id,row['id'],'2062516331063243')
        assert report['attempt']['media_id']=='2062516331063243'


def test_voice_evidence_is_visible_under_uvicorn_defaults_without_enabling_root_info():
    import subprocess,sys
    root=Path(__file__).resolve().parents[1]
    code='''import logging, logging.config
import uvicorn.config
logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
assert not logging.getLogger("whatsapp_service").isEnabledFor(logging.INFO)
import voice_trace
assert not logging.getLogger().isEnabledFor(logging.INFO)
voice_trace.record("multipart_file", {"sha256":"safe-hash", "size_bytes":3})
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root,text=True,capture_output=True,check=True)
    assert 'VOICE_EVIDENCE INFO' in result.stderr and 'safe-hash' in result.stderr


def test_admin_owner_media_inspection_is_explicit_get_only_and_never_sends(voices,monkeypatch):
    v=voices;row=v.create().json()['voice'];v.send(row);calls=[]
    with v.sessions() as db:
        attempt_id=db.query(SavedVoiceSend).one().id
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    def inspect(media,expected):calls.append((media,expected));return {'downloaded':expected,'ogg_opus_validation':{'passed':True}}
    monkeypatch.setattr(diagnostics,'retrieve_meta',inspect)
    data={'route':'saved','attempt_id':attempt_id,'csrf_token':v.csrf,'owner_confirm':'on'}
    url='/crm/bot-management/voice-diagnostics/meta'
    assert v.client.post(url,data={**data,'csrf_token':'bad'}).status_code==403
    assert v.client.post(url,data={**data,'owner_confirm':'false'}).status_code==400
    assert not calls
    response=v.client.post(url,data=data)
    assert response.status_code==200 and len(calls)==1 and 'meta_retrieval' in response.text
    assert len(v.sends)==len(v.uploads)==1
    with v.sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False and db.query(SavedVoiceSend).count()==1
    v.login('quick-manager');assert v.client.post(url,data=data).status_code==403


def test_build_manifest_and_nixpacks_do_not_assume_a_push_was_deployed(tmp_path,monkeypatch):
    import tomllib
    monkeypatch.setattr(build_info,'ROOT',tmp_path);monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA','c'*40)
    for name in build_info.FILES:(tmp_path/name).write_text('synthetic source')
    marker=build_info.build_record();(tmp_path/'build_revision.json').write_text(json.dumps(marker))
    evidence=build_info.runtime()
    assert evidence['artifact_commit']=='c'*40 and evidence['commit_sources_agree'] is True and evidence['artifact_files_match'] is True
    (tmp_path/'main.py').write_text('changed after artifact creation')
    assert build_info.runtime()['artifact_files_match'] is False
    config=tomllib.loads((Path(__file__).resolve().parents[1]/'nixpacks.toml').read_text())
    assert 'gcc-unwrapped' in config['phases']['setup']['nixLibs']
    assert 'python build_info.py --write' in config['phases']['build']['cmds']


def test_runtime_page_reports_partial_or_old_schema_without_mutating_it(inbox):
    client,sessions,_,sends,_,_=inbox
    with sessions.kw['bind'].begin() as conn:conn.execute(text('ALTER TABLE saved_voice_sends DROP COLUMN audio_trace'))
    response=client.get('/crm/bot-management/voice-diagnostics?message_id=2896&recording_id=10')
    assert response.status_code==200 and 'Trace column' in response.text
    assert not sends


def test_meta_metadata_error_returns_numeric_codes_without_private_provider_text(monkeypatch):
    monkeypatch.setattr(diagnostics.settings,'META_WHATSAPP_ACCESS_TOKEN','private-token')
    result=diagnostics.retrieve_meta('12345',transport=httpx.MockTransport(lambda request:httpx.Response(400,
        json={'error':{'code':190,'error_subcode':463,'message':'private-token private-phone'}})))
    assert result['error_code']==190 and result['error_subcode']==463
    assert 'private-token' not in json.dumps(result) and 'private-phone' not in json.dumps(result)


def test_unavailable_hash_is_unknown_not_reported_as_a_mismatch():
    assert diagnostics.same_fingerprints({'available':False},voice_trace.fingerprint(b'valid')) is None
    assert diagnostics.same_fingerprints(None,voice_trace.fingerprint(b'valid')) is None
    assert diagnostics.expected_upload({'trace':{'stages':{'normalized_record':{'sha256':None,'size_bytes':None}}}}) is None
