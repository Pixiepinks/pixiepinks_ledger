"""Owner-only A/B tests. All provider requests mocked; no live messages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import main
import voice_playback_test as service
import voice_trace
import whatsapp_service
from models import VoicePlaybackTest, VoicePlaybackVariant, SavedVoice, GlobalAIReplyControl, WhatsAppConversation
from test_saved_voices import voices, wav
from test_whatsapp_quick_messages import inbox
from test_voice_production_trace import wire

BASE='/crm/bot-management/voice-diagnostics/ab'


@pytest.fixture
def ab(voices, monkeypatch):
    v=voices
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','private-business-id')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-secret-token')
    monkeypatch.setenv('VOICE_PLAYBACK_TEST_ENABLED','true')
    monkeypatch.setenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER','94770012301')
    with v.sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    v.row=v.create().json()['voice']
    def prepare(**kw):
        return v.client.post(BASE+'/prepare',data={'voice_id':v.row['id'],'revision':v.row['revision'],
            'owner_number':'94770012301','owner_confirm':'true','csrf_token':v.csrf,**kw})
    def latest():
        with v.sessions() as db:return db.query(VoicePlaybackTest).order_by(VoicePlaybackTest.created_at.desc()).first().id
    def run(run_id=None, **kw):
        return v.client.post(BASE+'/run',data={'run_id':run_id or latest(),'confirm_two':'true','csrf_token':v.csrf,**kw})
    v.prepare=prepare;v.run=run;v.latest=latest
    return v


def test_real_multipart_same_bytes_payload_ids_ai_off_and_replay(ab, monkeypatch):
    v=ab;uploaded,sent=wire(monkeypatch)
    assert v.prepare().status_code==200
    assert not uploaded and not sent
    run_id=v.latest()
    preview=v.client.get(BASE+'/'+run_id+'/audio').content
    assert v.run().status_code==200
    assert uploaded==[preview,preview]
    assert [x['audio'] for x in sent]==[{'id':'1234567'},{'id':'1234567','voice':True}]
    assert sent[0]['to']==sent[1]['to']=='94770012301'
    assert v.run().status_code==200 and len(sent)==2
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,run_id)
        assert run.status=='SUBMITTED' and run.confirmed_at
        for variant in db.query(VoicePlaybackVariant).all():
            assert variant.status=='SENT'
            assert variant.audio_trace['stages']['multipart_file']['sha256']==run.evidence['normalized']['sha256']
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.get(WhatsAppConversation,v.ids[0]).mode=='HUMAN'
    page=v.client.get(BASE,params={'run_id':run_id})
    assert '94770012301' not in page.text and 'private-secret-token' not in page.text


@pytest.mark.parametrize('failure', ['disabled','number','confirm','csrf','revision','invalid_audio','expired_window'])
def test_fail_closed_before_any_send(ab,monkeypatch,failure):
    v=ab;wire(monkeypatch)
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda *a:pytest.fail('No upload allowed'))
    if failure=='disabled':monkeypatch.delenv('VOICE_PLAYBACK_TEST_ENABLED')
    data={}
    if failure=='number':data['owner_number']='94770012302'
    if failure=='confirm':data['owner_confirm']='false'
    if failure=='csrf':data['csrf_token']='wrong'
    if failure=='revision':data['revision']=999
    if failure=='invalid_audio':
        with v.sessions() as db:
            row=db.get(SavedVoice,v.row['id'])
            from saved_voice_service import storage_path
            storage_path(row.storage_name).write_bytes(b'invalid')
    if failure=='expired_window':
        with v.sessions() as db:
            db.get(WhatsAppConversation,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    assert v.prepare(**data).status_code in {400,403,409,503}


@pytest.mark.parametrize('role', ['quick-staff','quick-manager','quick-denied'])
def test_unauthorized_all_actions(ab,role):
    v=ab;v.prepare();rid=v.latest();v.login(role)
    assert v.client.get(BASE).status_code==403
    assert v.client.get(BASE+'/'+rid+'/audio').status_code==403
    assert v.run(rid).status_code==403
    assert v.prepare().status_code==403


@pytest.mark.parametrize('change', ['confirmation','config','corrupt','expired'])
def test_review_gates(ab,monkeypatch,change):
    v=ab;v.prepare();rid=v.latest()
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda *a:pytest.fail('No upload allowed'))
    data={}
    if change=='confirmation':data['confirm_two']='false'
    if change=='config':monkeypatch.setenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER','94770012302')
    if change=='corrupt':
        with v.sessions() as db:
            from saved_voice_service import storage_path
            storage_path(db.get(VoicePlaybackTest,rid).storage_name).write_bytes(b'bad')
    if change=='expired':
        with v.sessions() as db:db.get(VoicePlaybackTest,rid).created_at=datetime.utcnow()-timedelta(minutes=16);db.commit()
    assert v.run(**data).status_code in {400,409,422}


@pytest.mark.parametrize('failure', ['upload','first_send','second_send','ambiguous'])
def test_partial_results_and_no_silent_retries(ab,monkeypatch,failure):
    v=ab;v.prepare();uploads=[];sends=[]
    def upload(raw):
        uploads.append(raw)
        return None if failure=='upload' else 'media.'+str(len(uploads))
    def send(*args,**kw):
        sends.append(kw)
        if failure=='first_send' or (failure=='second_send' and len(sends)==2):return 'FAILED',None
        if failure=='ambiguous':return 'UNKNOWN',None
        return 'SENT','wamid.'+str(len(sends))
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',upload)
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',send)
    assert v.run().status_code==200
    assert v.run().status_code==200
    assert len(sends)==(0 if failure=='upload' else 2 if failure=='second_send' else 1)
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,v.latest());assert run.status=='INCOMPLETE'
        variants=db.query(VoicePlaybackVariant).order_by(VoicePlaybackVariant.variant).all()
        if failure!='second_send':assert variants[1].status=='NOT_STARTED'


def test_concurrent_workers_only_one_claim(ab,monkeypatch):
    v=ab;v.prepare();rid=v.latest();sent=[];uploaded=[]
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploaded.append(raw) or 'media')
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:(sent.append(kw) or ('SENT','wamid.'+str(len(sent)))))
    with v.sessions() as db:uid=db.get(VoicePlaybackTest,rid).staff_id
    def worker(_):
        with v.sessions() as db:return service.execute(db,SimpleNamespace(id=uid,role='ADMIN'),rid,True).status
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(worker,range(2)))
    assert len(uploaded)==len(sent)==2


def test_playback_results_are_manual_and_classified(ab,monkeypatch):
    v=ab;wire(monkeypatch);v.prepare();rid=v.latest();v.run()
    with v.sessions() as db:assert db.get(VoicePlaybackTest,rid).playback is None
    response=v.client.post(BASE+'/results',data={'run_id':rid,'a':'plays','b':'fails','platform':'Android','description':'audio_unavailable','csrf_token':v.csrf})
    assert response.status_code==200 and 'voice-note-specific' in response.text
    with v.sessions() as db:
        run=db.get(VoicePlaybackTest,rid)
        assert run.playback=={'A':'plays','B':'fails','platform':'Android','error':'audio_unavailable'}
        assert run.results_by==run.staff_id and run.results_at
    assert v.client.post(BASE+'/results',data={'run_id':rid,'a':'invented','b':'plays','platform':'Android','csrf_token':v.csrf}).status_code==422


def test_meta_retrieval_explicit_and_get_is_read_only(ab,monkeypatch):
    v=ab;wire(monkeypatch);v.prepare();rid=v.latest();v.run();calls=[]
    import voice_production_diagnostics
    monkeypatch.setattr(voice_production_diagnostics,'retrieve_meta',lambda mid,fp:calls.append((mid,fp)) or {'downloaded':fp})
    assert v.client.get(BASE,params={'run_id':rid}).status_code==200 and not calls
    data={'run_id':rid,'csrf_token':v.csrf}
    assert v.client.post(BASE+'/meta',data=data).status_code==400 and not calls
    assert v.client.post(BASE+'/meta',data={**data,'owner_confirm':'true'}).status_code==200 and len(calls)==2


@pytest.mark.parametrize('a,b,expected',[('plays','fails','A:'),('fails','fails','B:'),('plays','plays','C:'),('fails','plays','D:')])
def test_classifications_are_guidance_only(a,b,expected):
    text=service.summary({'A':a,'B':b})
    assert text.startswith(expected) and 'not proof' in text


def test_delivery_webhook_updates_only_diagnostic_status_and_redacts_errors(ab,monkeypatch):
    v=ab;v.prepare()
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:'media.owner')
    count=[]
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:('SENT','wamid.ab.'+str(len(count.append(1) or count))))
    v.run()
    with v.sessions() as db:wid=db.query(VoicePlaybackVariant).filter_by(variant='B').one().whatsapp_message_id
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    payload={'entry':[{'changes':[{'value':{'statuses':[{'id':wid,'status':'failed','recipient_id':'private-phone',
        'errors':[{'code':131053,'message':'private-error'}]}]}}]}]}
    assert v.client.post('/webhook',json=payload).status_code==200
    with v.sessions() as db:
        variant=db.query(VoicePlaybackVariant).filter_by(variant='B').one()
        assert variant.delivery_status=='failed' and variant.delivery_error_code==131053
        assert db.get(GlobalAIReplyControl,1).enabled is False
    page=v.client.get(BASE,params={'run_id':v.latest()})
    assert 'private-phone' not in page.text and 'private-error' not in page.text


def test_snapshot_stays_identical_when_library_changes(ab,monkeypatch):
    v=ab;v.prepare();rid=v.latest();uploaded,sent=wire(monkeypatch)
    preview=v.client.get(BASE+'/'+rid+'/audio').content
    with v.sessions() as db:
        voice=db.get(SavedVoice,v.row['id']);voice.revision+=1;voice.active=False;db.commit()
    assert v.run().status_code==200 and uploaded==[preview,preview]


def test_offline_stream_evidence_is_advisory(ab):
    v=ab;v.prepare()
    with v.sessions() as db:
        stream=db.get(VoicePlaybackTest,v.latest()).evidence['audio']
        assert stream['opus_head_version']==1 and stream['channels']==1
        assert stream['opus_tags_present'] and stream['final_page_eos']
        assert stream['pre_skip_samples']>0 and stream['final_granule']>stream['pre_skip_samples']
        assert stream['warnings']==[] and 'Offline' in stream['interpretation']


def test_other_admin_cannot_replay_another_admin_review(ab,monkeypatch):
    v=ab;v.prepare();rid=v.latest()
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:pytest.fail('No upload'))
    with v.sessions() as db:
        with pytest.raises(HTTPException) as err:service.execute(db,SimpleNamespace(id=999999,role='ADMIN'),rid,True)
        assert err.value.status_code==404


def test_postgres_migration_and_cross_session_single_claim(tmp_path,monkeypatch):
    import os
    import hashlib
    from uuid import uuid4
    from pathlib import Path
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from alembic import command
    from alembic.config import Config
    import database
    from models import Base,User
    from audio_service import normalize_voice_audio
    from saved_voice_service import write_audio
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
    admin=create_engine(url)
    assert admin.url.database=='pixiepinks_test'
    schema='voice_ab_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:
        Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('voice_playback_')])
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261030_20');command.upgrade(config,'head');command.upgrade(config,'head')
        with engine.connect() as conn:assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261030_21'
        monkeypatch.setenv('VOICE_PLAYBACK_TEST_ENABLED','true');monkeypatch.setenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER','94770012301')
        monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','business.test')
        monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','test')
        monkeypatch.setattr(whatsapp_service.settings,'SAVED_VOICES_DIR',str(tmp_path/'pg-voices'))
        sessions=sessionmaker(bind=engine,expire_on_commit=False)
        raw,duration=normalize_voice_audio(wav(),'audio/wav')
        with sessions() as db:
            user=User(username='owner-ab-admin',password_hash='not-a-login',role='ADMIN');db.add(user)
            db.add(WhatsAppConversation(phone_number='94770012301',mode='HUMAN',last_customer_message_at=datetime.utcnow()))
            db.get(GlobalAIReplyControl,1).enabled=False
            db.get(GlobalAIReplyControl,1).generation=5
            voice=SavedVoice(title='Owner test',category='Test',transcript='Synthetic',language='singlish',active=True,revision=1,
                storage_name=write_audio(raw),sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),duration_seconds=duration,
                normalization_version=1,processing_status='READY',created_by='test',updated_by='test')
            db.add(voice);db.commit();uid=user.id
            rid=service.prepare(db,user,voice.id,1,'94770012301',True).id
        uploads=[];sends=[]
        monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'media.pg')
        monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',lambda *a,**kw:('SENT','wamid.pg.'+str(len(sends.append(kw) or sends))))
        def execute(_):
            with sessions() as db:service.execute(db,db.get(User,uid),rid,True)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(execute,range(4)))
        assert uploads==[raw,raw] and len(sends)==2
        with sessions() as db:
            assert db.get(VoicePlaybackTest,rid).status=='SUBMITTED'
            assert db.get(GlobalAIReplyControl,1).enabled is False
            assert db.query(VoicePlaybackVariant).count()==2
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_each_variant_uses_its_own_uploaded_media_id(ab,monkeypatch):
    v=ab;v.prepare();uploads=[];references=[]
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:uploads.append(raw) or 'media.'+str(len(uploads)))
    def send(recipient,media_id,**kw):
        references.append((media_id,kw['voice']))
        return 'SENT','wamid.'+str(len(references))
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_audio',send)
    v.run()
    assert references==[('media.1',False),('media.2',True)] and uploads[0]==uploads[1]


def test_disabled_default_page_and_no_send_on_get(ab,monkeypatch):
    v=ab;monkeypatch.delenv('VOICE_PLAYBACK_TEST_ENABLED')
    monkeypatch.setattr(whatsapp_service,'upload_whatsapp_audio',lambda raw:pytest.fail('No upload'))
    page=v.client.get(BASE)
    assert page.status_code==200 and 'Disabled.' in page.text
    assert 'Run A/B Test — Send Two Messages' not in page.text


def test_chromium_owner_review_requires_second_confirmation(ab,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=ab;uploads,sends=wire(monkeypatch)
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='owner.test':route.fulfill(status=200,body='');return
                response=v.client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:val for k,val in req.headers.items() if k in {'content-type','range'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,
                    headers={k:val for k,val in response.headers.items() if k not in {'set-cookie','content-length','content-encoding'}})
            page.route('**/*',serve)
            page.goto('https://owner.test'+BASE)
            page.locator('[name=revision]').fill(str(v.row['revision']))
            page.locator('[name=owner_number]').fill('94770012301')
            page.locator('[name=owner_confirm]').check()
            page.get_by_role('button',name='Prepare review — send nothing').click()
            page.get_by_role('button',name='Run A/B Test — Send Two Messages').wait_for()
            assert not uploads and not sends
            page.get_by_role('button',name='Run A/B Test — Send Two Messages').click()
            assert not uploads and not sends  # Browser required confirmation.
            page.locator('[name=confirm_two]').check()
            page.get_by_role('button',name='Run A/B Test — Send Two Messages').click()
            page.get_by_role('button',name='Save actual playback results — send nothing').wait_for()
            assert len(sends)==2
            page.locator('[name=a]').select_option('plays');page.locator('[name=b]').select_option('fails')
            page.get_by_role('button',name='Save actual playback results — send nothing').click()
            page.get_by_text('A: Investigate voice-note-specific',exact=False).wait_for()
            assert len(sends)==2 and not errors
        finally:browser.close()
