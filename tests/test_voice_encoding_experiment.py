"""Owner-only same-source experiment. No real Meta or customer messages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
import hashlib
import json
import os
import subprocess

import httpx
import pytest

import audio_service
import main
import voice_diagnostic_tools as tools
import voice_encoding_experiment as encoding
import voice_compatibility
import voice_trace
import whatsapp_service
from models import VoicePlaybackTest,VoicePlaybackVariant,SavedVoice,GlobalAIReplyControl,WhatsAppConversation,BotAuditEvent
from saved_voice_service import _read_file,storage_path
from test_voice_playback_test import ab,BASE
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def experiment(ab,monkeypatch):
    v=ab
    monkeypatch.setenv('VOICE_ENCODING_TEST_ENABLED','true')
    # Mock independent work for security/concurrency cases; a separate test
    # below exercises the real pinned subprocess encoder/decoder end-to-end.
    monkeypatch.setattr(tools,'runtime',lambda:{'available':True,'verified':True,'binary_sha256':'test-pinned-executable','ffmpeg_version':'mock'})
    monkeypatch.setattr(tools,'encode',lambda raw:audio_service.normalize_voice_audio(raw)[0])
    monkeypatch.setattr(tools,'encode_pyav',lambda raw,profile:audio_service.normalize_voice_audio(raw)[0])
    monkeypatch.setattr(tools,'inspect_source',lambda raw:audio_service.source_audio_info(raw))
    monkeypatch.setattr(tools,'inspect_variant',voice_compatibility.inspect)
    monkeypatch.setattr(tools,'decode',lambda raw:{'passed':True,'decoded_duration_seconds':audio_service.validate_voice_audio(raw)['duration_seconds']})
    def prepare(**kw):
        return v.client.post(BASE+'/encoding/prepare',data={'voice_id':v.row['id'],'revision':v.row['revision'],
            'owner_number':'94770012301','owner_confirm':'true','csrf_token':v.csrf,**kw})
    def run(run_id=None,**kw):
        return v.client.post(BASE+'/encoding/run',data={'run_id':run_id or v.latest(),'confirm_three':'true','message_count':3,'csrf_token':v.csrf,**kw})
    v.encoding_prepare=prepare;v.encoding_run=run
    return v


def wire(monkeypatch):
    uploads=[];sends=[]
    def post(url,**kw):
        request=httpx.Request('POST',url,**{k:v for k,v in kw.items() if k in {'headers','data','files','json'}})
        if url.endswith('/media'):
            parsed=BytesParser(policy=default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+request.read())
            file=next(p for p in parsed.iter_parts() if p.get_param('name',header='content-disposition')=='file')
            assert file['Content-Type']=='audio/ogg' and file.get_filename()=='voice-note.ogg'
            uploads.append(file.get_payload(decode=True));payload={'id':'media.encoding.'+str(len(uploads))}
        else:
            sends.append(kw['json']);payload={'messages':[{'id':'wamid.encoding.'+str(len(sends))}]}
        return httpx.Response(200,request=request,json=payload)
    monkeypatch.setattr(whatsapp_service.httpx,'post',post)
    return uploads,sends


def test_one_immutable_source_measured_modes_identical_message_type_and_idempotency(experiment,monkeypatch):
    v=experiment;uploads,sends=wire(monkeypatch)
    before={p.name:p.read_bytes() for p in v.directory.iterdir()}
    response=v.encoding_prepare();assert response.status_code==200,response.text
    rid=v.latest();assert not uploads and not sends
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid);source=run.evidence['source']
        assert run.evidence['kind']==encoding.KIND and run.status=='REVIEW'
        for label in encoding.LABELS:
            value=run.evidence['variants'][label];raw=_read_file(value['storage_name'])
            assert value['source']==source and value['bytes']==voice_trace.fingerprint(raw)
            actual=voice_compatibility.inspect(raw)
            assert value['audio']['encoded_mode_counts']==actual['encoded_mode_counts']
            assert sum(actual['encoded_mode_counts'].values())==actual['packet_count']-2
            assert value['storage_name'] not in response.text
        assert run.storage_name not in response.text and db.get(GlobalAIReplyControl,1).enabled is False
    assert v.encoding_run().status_code==200
    assert v.encoding_run().status_code==200
    assert len(uploads)==len(sends)==3
    assert [p['audio'] for p in sends]==[{'id':'media.encoding.1'},{'id':'media.encoding.2'},{'id':'media.encoding.3'}]
    assert all(p['to']=='94770012301' and p['type']=='audio' for p in sends)
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid);assert run.status=='SUBMITTED' and run.playback is None
        keys=[]
        for variant in db.query(VoicePlaybackVariant).filter_by(run_id=rid).all():
            keys.append(variant.audio_trace['idempotency_key'])
            assert variant.audio_trace['stages']['multipart_file']['sha256']==run.evidence['variants'][variant.variant]['bytes']['sha256']
        assert len(set(keys))==3
        assert db.query(BotAuditEvent).filter(BotAuditEvent.action.like('VOICE_ENCODING_%')).count()>=3
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert all((v.directory/name).read_bytes()==raw for name,raw in before.items())


def test_real_pinned_tool_generates_and_independently_decodes_every_profile(ab,monkeypatch):
    if not tools.runtime().get('verified'):pytest.skip('Optional pinned diagnostic wheel not installed')
    v=ab;monkeypatch.setenv('VOICE_ENCODING_TEST_ENABLED','true')
    uploads,sends=wire(monkeypatch)
    response=v.client.post(BASE+'/encoding/prepare',data={'voice_id':v.row['id'],'revision':v.row['revision'],
        'owner_number':'94770012301','owner_confirm':'true','csrf_token':v.csrf})
    assert response.status_code==200,response.text
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,v.latest());assert run.status=='REVIEW'
        assert all(value['independent']['passed'] and value['eligible'] for value in run.evidence['variants'].values())
        assert run.evidence['independent_runtime']['binary_sha256']==tools.BINARY_SHA256
        for value in run.evidence['variants'].values():assert tools.decode(_read_file(value['storage_name']))['passed']
    assert not uploads and not sends


@pytest.mark.parametrize('change',['disabled','owner','confirm','csrf','revision','role'])
def test_prepare_authorization_failures_never_upload(experiment,monkeypatch,change):
    v=experiment;uploads,sends=wire(monkeypatch);kw={}
    if change=='disabled':monkeypatch.delenv('VOICE_ENCODING_TEST_ENABLED')
    if change=='owner':kw['owner_number']='94770012302'
    if change=='confirm':kw['owner_confirm']='false'
    if change=='csrf':kw['csrf_token']='invalid'
    if change=='revision':kw['revision']=999
    if change=='role':v.login('quick-staff')
    assert v.encoding_prepare(**kw).status_code in {400,403,409}
    assert not uploads and not sends


@pytest.mark.parametrize('change',['corrupt_later','expired','window','count','confirm','config','decoder','off'])
def test_all_live_gates_precede_first_upload(experiment,monkeypatch,change):
    v=experiment;v.encoding_prepare();uploads,sends=wire(monkeypatch);kw={}
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,v.latest())
        if change=='corrupt_later':storage_path(run.evidence['variants']['C']['storage_name']).write_bytes(b'invalid')
        if change=='expired':run.created_at=datetime.utcnow()-timedelta(minutes=16)
        if change=='window':db.get(WhatsAppConversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2)
        db.commit()
    if change=='count':kw['message_count']=2
    if change=='confirm':kw['confirm_three']='false'
    if change=='config':monkeypatch.setenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER','94770012302')
    if change=='decoder':monkeypatch.setattr(tools,'decode',lambda raw:{'passed':False})
    if change=='off':monkeypatch.setenv('VOICE_ENCODING_TEST_ENABLED','false')
    assert v.encoding_run(**kw).status_code in {400,409}
    assert not uploads and not sends


def test_missing_independent_decoder_blocks_all_three_and_aac(experiment,monkeypatch):
    v=experiment;monkeypatch.setattr(tools,'runtime',lambda:{'available':False,'verified':False})
    uploads,sends=wire(monkeypatch)
    response=v.encoding_prepare();assert response.status_code==200
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,v.latest());assert run.status=='OFFLINE_ONLY'
        assert not run.evidence['aac_control']['enabled']
    assert v.encoding_run().status_code==200 and not uploads and not sends
    assert 'Send Three Messages' not in response.text


def test_failed_conversion_is_audited_and_unsendable(experiment,monkeypatch):
    v=experiment;monkeypatch.setattr(tools,'encode',lambda raw:(_ for _ in ()).throw(ValueError('private-error')))
    uploads,sends=wire(monkeypatch);response=v.encoding_prepare()
    assert response.status_code==200 and 'private-error' not in response.text
    with v.sessions() as db:assert db.get(VoicePlaybackTest,v.latest()).status=='OFFLINE_ONLY'
    v.encoding_run();assert not uploads and not sends


@pytest.mark.parametrize('stage',['upload','first','second','unknown'])
def test_partial_failures_stop_and_never_retry(experiment,monkeypatch,stage):
    v=experiment;v.encoding_prepare();uploads=[];sends=[]
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or (None if stage=='upload' else 'media'))
    def send(*a,**kw):
        sends.append(kw)
        if stage=='unknown':return 'UNKNOWN',None
        if stage=='first' or stage=='second' and len(sends)==2:return 'FAILED',None
        return 'SENT','wamid.'+str(len(sends))
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',send)
    v.encoding_run();v.encoding_run()
    assert len(sends)==(0 if stage=='upload' else 2 if stage=='second' else 1)
    with v.sessions() as db:assert db.get(VoicePlaybackTest,v.latest()).status=='INCOMPLETE'


def test_concurrent_workers_and_old_ab_route_cannot_duplicate_or_bypass(experiment,monkeypatch):
    v=experiment;v.encoding_prepare();rid=v.latest();uploads,sends=wire(monkeypatch)
    assert v.client.post(BASE+'/run',data={'run_id':rid,'confirm_two':'true','csrf_token':v.csrf}).status_code==409
    with v.sessions() as db:uid=db.get(VoicePlaybackTest,rid).staff_id
    def submit(_):
        with v.sessions() as db:encoding.execute(db,SimpleNamespace(id=uid,role='ADMIN'),rid,True,3)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(submit,range(3)))
    assert len(uploads)==len(sends)==3


def test_manual_results_bind_ids_hashes_and_cleanup_preserves_originals(experiment,monkeypatch):
    v=experiment;originals={p.name:p.read_bytes() for p in v.directory.iterdir()}
    v.encoding_prepare();rid=v.latest();wire(monkeypatch);v.encoding_run()
    response=v.client.post(BASE+'/encoding/results',data={'run_id':rid,'a':'plays','b':'cannot_download','c':'cannot_share','platform':'Android','csrf_token':v.csrf})
    assert response.status_code==200 and 'download/share outcome' in response.text
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        for label,value in run.playback['variants'].items():
            assert value['file']==run.evidence['variants'][label]['bytes']
            assert value['media_id'] and value['whatsapp_message_id']
    response=v.client.post(BASE+'/encoding/close',data={'run_id':rid,'confirm_delete':'true','csrf_token':v.csrf})
    assert response.status_code==200
    assert {p.name:p.read_bytes() for p in v.directory.iterdir()}==originals
    assert v.client.get(BASE+'/encoding/'+rid+'/A/audio').status_code==404
    assert v.encoding_run().status_code==200
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).playback['variants']['B']['result']=='cannot_download'


def test_pinned_tool_integrity_timeout_and_no_inherited_secrets(monkeypatch):
    if not tools.runtime().get('verified'):pytest.skip('Optional pinned tool unavailable')
    monkeypatch.setenv('IMAGEIO_FFMPEG_EXE','/untrusted/arbitrary-binary')
    assert tools.runtime()['verified']
    original=tools.BINARY_SHA256;monkeypatch.setattr(tools,'BINARY_SHA256','0'*64)
    assert not tools.runtime()['verified']
    monkeypatch.setattr(tools,'BINARY_SHA256',original)
    seen=[]
    def child(*a,**kw):
        seen.append(kw)
        raise subprocess.TimeoutExpired('worker',25)
    monkeypatch.setattr(tools.subprocess,'run',child)
    assert tools.decode(b'bounded')['passed'] is False
    assert seen[0]['env']=={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}


def test_each_stage_and_phone_result_are_distinct_in_ui(experiment,monkeypatch):
    v=experiment;uploads,sends=wire(monkeypatch);response=v.encoding_prepare()
    assert response.status_code==200
    assert all(text in response.text for text in ['Meta acceptance','Independent decode','Phone: Not tested','TOC:'])
    rid=v.latest();assert v.client.get(BASE+'/encoding/'+rid+'/A/audio').status_code==200
    assert not uploads and not sends
    v.encoding_run()
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).playback is None


def test_delivery_and_meta_inspection_do_not_infer_phone_success(experiment,monkeypatch):
    v=experiment;v.encoding_prepare();rid=v.latest();wire(monkeypatch);v.encoding_run()
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    payload={'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.encoding.2','status':'delivered'}]}}]}]}
    assert v.client.post('/webhook',json=payload).status_code==200
    calls=[]
    import voice_production_diagnostics
    monkeypatch.setattr(voice_production_diagnostics,'retrieve_meta',lambda mid,expected:calls.append((mid,expected)) or {'downloaded':expected})
    data={'run_id':rid,'csrf_token':v.csrf,'owner_confirm':'false'}
    assert v.client.post(BASE+'/encoding/meta',data=data).status_code==400 and not calls
    assert v.client.post(BASE+'/encoding/meta',data={**data,'owner_confirm':'true'}).status_code==200
    assert len(calls)==3
    with v.sessions() as db:
        assert db.query(VoicePlaybackVariant).filter_by(run_id=rid,variant='B').one().delivery_status=='delivered'
        assert db.get(VoicePlaybackTest,rid).playback is None
        assert db.get(GlobalAIReplyControl,1).enabled is False


def test_close_running_and_staff_preview_are_blocked(experiment):
    v=experiment;v.encoding_prepare();rid=v.latest()
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid);run.status='RUNNING';db.commit();snapshot=run.evidence['variants']['A']['storage_name']
    assert v.client.post(BASE+'/encoding/close',data={'run_id':rid,'csrf_token':v.csrf,'confirm_delete':'true'}).status_code==409
    assert storage_path(snapshot).exists()
    v.login('quick-staff')
    assert v.client.get(BASE+'/encoding/'+rid+'/A/audio').status_code==403


def test_postgres_claim_and_audit_across_worker_sessions(tmp_path,monkeypatch):
    from pathlib import Path
    from uuid import uuid4
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from models import Base,User
    from saved_voice_service import write_audio
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
    admin=create_engine(url);assert admin.url.database=='pixiepinks_test'
    schema='encoding_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:
        Base.metadata.create_all(engine)
        sessions=sessionmaker(bind=engine,expire_on_commit=False)
        monkeypatch.setenv('VOICE_ENCODING_TEST_ENABLED','true');monkeypatch.setenv('VOICE_PLAYBACK_TEST_ENABLED','true')
        monkeypatch.setenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER','94770012301')
        monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','test-business')
        monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','test-token')
        monkeypatch.setattr(whatsapp_service.settings,'SAVED_VOICES_DIR',str(tmp_path/'private-encoding'))
        monkeypatch.setattr(tools,'runtime',lambda:{'verified':True,'binary_sha256':'test'})
        monkeypatch.setattr(tools,'encode',lambda raw:audio_service.normalize_voice_audio(raw)[0])
        monkeypatch.setattr(tools,'decode',lambda raw:{'passed':True,'decoded_duration_seconds':audio_service.validate_voice_audio(raw)['duration_seconds']})
        original=wav();raw,duration=audio_service.normalize_voice_audio(original)
        with sessions() as db:
            user=User(username='encoding-owner',password_hash='not-a-login',role='ADMIN');db.add(user)
            db.add(WhatsAppConversation(phone_number='94770012301',mode='HUMAN',last_customer_message_at=datetime.utcnow()))
            db.get(GlobalAIReplyControl,1).enabled=False
            voice=SavedVoice(title='Synthetic',category='Test',transcript='Synthetic',language='singlish',active=True,revision=1,
                storage_name=write_audio(raw),original_storage_name=write_audio(original,original=True),
                sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),duration_seconds=duration,
                normalization_version=1,processing_status='READY',created_by='test',updated_by='test')
            db.add(voice);db.commit();uid=user.id
            rid=encoding.prepare(db,user,voice.id,1,'94770012301',True).id
        uploads=[];sends=[]
        monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'media')
        monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:sends.append(kw) or ('SENT','wamid.'+str(len(sends))))
        def execute(_):
            with sessions() as db:encoding.execute(db,db.get(User,uid),rid,True,3)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(execute,range(4)))
        assert len(uploads)==len(sends)==3 and all(s['voice'] is False for s in sends)
        with sessions() as db:
            assert db.query(BotAuditEvent).filter_by(action='VOICE_ENCODING_AUTHORIZED_THREE_SENDS').count()==1
            assert db.get(VoicePlaybackTest,rid).status=='SUBMITTED' and db.get(GlobalAIReplyControl,1).enabled is False
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_expiration_between_upload_and_send_blocks_external_message(experiment,monkeypatch):
    v=experiment;v.encoding_prepare();uploads=[];sends=[]
    class Clock(datetime):
        offset=timedelta()
        @classmethod
        def utcnow(cls):return datetime.utcnow()+cls.offset
    monkeypatch.setattr(encoding,'datetime',Clock)
    def upload(raw):
        uploads.append(raw);Clock.offset=timedelta(minutes=16);return 'media.not-sent'
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',upload)
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:sends.append(kw))
    assert v.encoding_run().status_code==200 and len(uploads)==1 and not sends
    with v.sessions() as db:
        first=db.query(VoicePlaybackVariant).filter_by(run_id=v.latest(),variant='A').one()
        assert first.audio_trace['stages']['authorization']['reason']=='expired'


def test_chromium_same_source_review_and_manual_results(experiment,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=experiment;uploads,sends=wire(monkeypatch)
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='owner-encoding.test':route.fulfill(status=200,body='');return
                response=v.client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:val for k,val in req.headers.items() if k in {'content-type','range'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,
                    headers={k:val for k,val in response.headers.items() if k not in {'set-cookie','content-length','content-encoding'}})
            page.route('**/*',serve);page.goto('https://owner-encoding.test'+BASE)
            form=page.locator('form[action$="/encoding/prepare"]')
            form.locator('[name=revision]').fill(str(v.row['revision']))
            form.locator('[name=owner_number]').fill('94770012301');form.locator('[name=owner_confirm]').check()
            form.get_by_role('button').click()
            page.get_by_role('button',name='Run encoding test — Send Three Messages').wait_for()
            assert not uploads and not sends
            page.get_by_role('button',name='Run encoding test — Send Three Messages').click()
            assert not uploads and not sends
            page.locator('[name=confirm_three]').check()
            page.get_by_role('button',name='Run encoding test — Send Three Messages').click()
            page.get_by_role('button',name='Record actual phone results — send nothing').wait_for()
            assert len(sends)==3
            page.locator('[name=a]').select_option('plays');page.locator('[name=b]').select_option('cannot_play')
            page.locator('[name=c]').select_option('plays')
            page.get_by_role('button',name='Record actual phone results — send nothing').click()
            page.get_by_text('Historical works/current fails',exact=False).wait_for()
            assert len(sends)==3 and not errors
        finally:browser.close()


def test_variant_idempotency_survives_erroneous_parent_reset(experiment,monkeypatch):
    v=experiment;v.encoding_prepare();wire(monkeypatch);v.encoding_run()
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,v.latest());run.status='REVIEW';db.commit()
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:pytest.fail('Existing variant was retried'))
    assert v.encoding_run().status_code==409


def test_cleanup_failure_is_audited_and_retryable_without_sending(experiment,monkeypatch):
    from pathlib import Path
    v=experiment;v.encoding_prepare();rid=v.latest()
    original=Path.unlink
    def fail(path,*args,**kw):raise PermissionError('private storage detail')
    monkeypatch.setattr(Path,'unlink',fail)
    data={'run_id':rid,'confirm_delete':'true','csrf_token':v.csrf}
    response=v.client.post(BASE+'/encoding/close',data=data)
    assert response.status_code==503 and 'private storage detail' not in response.text
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        assert run.status=='CLOSED' and run.evidence['snapshots_deleted'] is False
        assert db.query(BotAuditEvent).filter_by(action='VOICE_ENCODING_SNAPSHOT_DELETE_FAILED').count()==1
    monkeypatch.setattr(Path,'unlink',original)
    assert v.client.post(BASE+'/encoding/close',data=data).status_code==200
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).evidence['snapshots_deleted'] is True


def test_pyav_codec_children_are_bounded_and_do_not_require_optional_ffmpeg():
    import io,wave
    # These operations use the already pinned PyAV; independent runtime is a
    # separate eligibility gate. No FFmpeg or Meta request is made by inspection.
    info=tools.inspect_source(wav())
    assert info['codec'].startswith('pcm_')
    for profile in ('historical','current'):
        raw=tools.encode_pyav(wav(),profile)
        assert tools.inspect_variant(raw)['validation']['codec']=='opus'
    source=io.BytesIO()
    with wave.open(source,'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(8000);target.writeframes(b'\0\0'*8000*31)
    with pytest.raises(ValueError):tools.inspect_source(source.getvalue())
