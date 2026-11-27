from uuid import uuid4
from datetime import datetime,timedelta
from types import SimpleNamespace as N
import math,struct,io,wave,json,subprocess
import pytest
from fastapi import HTTPException
from models import User,WhatsAppConversation as C,WhatsAppConversationMessage as M,SalesOrder,SalesPayment,JournalEntry
from monara_models import MonaraJob as J,MonaraSend as S,MonaraEvent as E,MonaraPolicy as P
import monara_service as svc
import monara_audio as audio
from monara_sdk_worker import collect
from settings import settings
from test_whatsapp_quick_messages import inbox
from test_retail_postgres import pg


def sdk_python():
    from pathlib import Path
    python=Path(settings.MONARA_TTS_PYTHON) if settings.MONARA_TTS_PYTHON else Path(__file__).resolve().parents[1]/'.monara-venv/bin/python'
    if not python.exists():pytest.skip('Install the isolated SDK and set MONARA_TTS_PYTHON before SDK acceptance tests')
    return python

@pytest.fixture(scope='module')
def rendered():
    pcm=b''.join(struct.pack('<h',int(1800*math.sin(2*math.pi*440*n/24000))) for n in range(12000))
    return {'pcm':pcm,**audio.encode(pcm),'usage':{'prompt_token_count':10,'total_token_count':20}}
@pytest.fixture
def setup(inbox,monkeypatch,rendered):
    client,sessions,ids,sends,login,csrf=inbox
    monkeypatch.setattr(settings,'MONARA_TTS_ENABLED',True);monkeypatch.setattr(settings,'MONARA_TTS_SEND_ENABLED',True);monkeypatch.setattr(settings,'GEMINI_API_KEY','synthetic-never-used')
    monkeypatch.setattr(audio,'generate',lambda *args:rendered)
    with sessions() as db:
        admin=db.query(User).filter_by(role='ADMIN').one();c=db.get(C,ids[0]);c.assigned_to_user_id=admin.id
        p=db.get(P,1);p.data={**svc.DEFAULT,'generation':True,'sending':True,'monthly_budget':'100','reserve_per_request':'1'};db.commit();uid=admin.id
    return N(client=client,sessions=sessions,ids=ids,csrf=csrf,uid=uid,login=login,sends=sends,rendered=rendered)

def values(lang='si'):return {'transcript':svc.DISCLOSURES[lang]+'\nSynthetic customer message','language':lang,'style':'transcript_only'}
def create(v,payload=None,key=None):
    with v.sessions() as db:
        row=svc.create(db,db.get(User,v.uid),v.ids[0],payload or values(),key or str(uuid4()));jid=row.id;db.commit();return jid

def ready(v,payload=None):
    jid=create(v,payload);svc.process(v.sessions,jid,provider=lambda *args:v.rendered);return jid

def approve(v,jid):
    with v.sessions() as db:return {'approved':True,'previewed':True,'sha256':db.get(J,jid).sha256,'assignment_version':db.get(C,v.ids[0]).assignment_version}
def send(v,jid,body=None,key=None,upload=None,deliver=None):
    with v.sessions() as db:return svc.send(db,db.get(User,v.uid),v.ids[0],jid,body or approve(v,jid),key or str(uuid4()),upload or (lambda raw:'synthetic-media-id'),deliver or (lambda *a:('SENT','wamid.synthetic')))

def test_pcm_and_real_encoding(rendered):
    raw=audio.pcm_wav(rendered['pcm'])
    with wave.open(io.BytesIO(raw)) as w:assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.readframes(12000))==(1,2,24000,rendered['pcm'])
    from audio_service import validate_voice_audio
    info=validate_voice_audio(rendered['ogg']);assert abs(info['duration_seconds']-.5)<.05
    assert rendered['mp3'] and rendered['duration']==.5

@pytest.mark.parametrize('lang',['si','en','ta'])
def test_generation_unicode_and_profile(setup,lang):
    v=setup;jid=ready(v,values(lang))
    with v.sessions() as db:
        job=db.get(J,jid);assert job.transcript==values(lang)['transcript'];assert (job.model,job.voice,job.api_version)==('gemini-3.8-flash-tts','voice_xu8nahptj1p9','v1beta');assert job.status=='READY';assert job.mp3 and job.ogg
        assert db.query(SalesOrder).count()==db.query(SalesPayment).count()==db.query(JournalEntry).count()==0
    assert not v.sends

@pytest.mark.parametrize('flag',['MONARA_TTS_ENABLED','MONARA_TTS_SEND_ENABLED'])
def test_gates_default_off_and_runtime_blocks(setup,monkeypatch,flag):
    assert __import__('settings').Settings.model_fields[flag].default is False
    if flag=='MONARA_TTS_SEND_ENABLED':jid=ready(setup)
    monkeypatch.setattr(settings,flag,False)
    with pytest.raises(HTTPException) as error:
        send(setup,jid) if flag.endswith('SEND_ENABLED') else create(setup)
    assert error.value.status_code==403


def chunk(raw=b'\x00\x00',mime='audio/L16;codec=pcm;rate=24000'):
    return N(candidates=[N(finish_reason=None,content=N(parts=[N(inline_data=N(data=raw,mime_type=mime))]))],usage_metadata=N(total_token_count=7))
def test_all_streamed_parts_and_usage():
    import base64
    result=collect([chunk(b'\x01\x02'),chunk(b'\x03\x04')]);assert base64.b64decode(result['pcm'])==b'\x01\x02\x03\x04';assert result['usage']['total_token_count']==7
@pytest.mark.parametrize('stream',[[],[chunk(b'')],[chunk(mime='audio/mp3')],[chunk(b'x')],[N(candidates=[N(finish_reason='SAFETY',content=None)],usage_metadata=None)]])
def test_invalid_provider_stream(stream):
    with pytest.raises(ValueError):collect(stream)
def test_interrupted_and_bounded_stream():
    def interrupted():yield chunk();raise RuntimeError('SYNTHETIC SECRET MUST NOT LOG')
    with pytest.raises(RuntimeError):collect(interrupted())
    with pytest.raises(ValueError):collect([chunk(b'\0'*100)],max_bytes=50)

def test_duplicate_generation_and_provider_claim(setup):
    v=setup;key=str(uuid4());jid=create(v,key=key);assert create(v,key=key)==jid
    calls=[]
    def provider(*a):calls.append(a);return v.rendered
    svc.process(v.sessions,jid,provider);svc.process(v.sessions,jid,provider)
    assert len(calls)==1
    with v.sessions() as db:assert db.query(J).count()==1

def test_failed_provider_no_retry_and_redaction(setup,capsys):
    jid=create(setup)
    def fail(*args):raise RuntimeError('synthetic-secret-provider-body')
    svc.process(setup.sessions,jid,fail);svc.process(setup.sessions,jid,fail)
    with setup.sessions() as db:assert db.get(J,jid).failure=='PROVIDER_OR_ENCODING_FAILED_NO_RETRY'
    assert 'synthetic-secret' not in capsys.readouterr().out

@pytest.mark.parametrize('limit',['characters','daily','budget','concurrency'])
def test_usage_limits(setup,limit):
    with setup.sessions() as db:
        p=db.get(P,1);d={**p.data}
        if limit=='characters':d['monthly_characters']=1
        if limit=='daily':d['daily_requests_per_staff']=1
        if limit=='budget':d['monthly_budget']='1'
        p.data=d;db.commit()
    if limit in {'daily','budget'}:ready(setup)
    if limit=='concurrency':create(setup);create(setup)
    with pytest.raises(HTTPException) as e:create(setup)
    assert e.value.status_code==429

def test_private_access_and_explicit_library_share(setup):
    v=setup;jid=ready(v)
    with v.sessions() as db:
        user=db.get(User,v.uid)
        with pytest.raises(HTTPException):svc.audio(db,user,jid,v.ids[1],'ogg')
        agent=db.query(User).filter_by(username='quick-agent').one();p=db.get(P,1);p.data={**p.data,'staff_ids':[agent.id]};db.commit()
        with pytest.raises(HTTPException):svc.audio(db,agent,jid,v.ids[0],'ogg')
        with pytest.raises(HTTPException):svc.library_save(db,user,jid,v.ids[0],{'title':'Synthetic','category':'Thank you','approved':False})
        svc.library_save(db,user,jid,v.ids[0],{'title':'Synthetic','category':'Thank you','approved':True});db.commit()
        assert svc.audio(db,agent,jid,None,'mp3')==v.rendered['mp3']
        assert db.query(J).count()==1

def test_explicit_send_duplicate_fence_and_history(setup):
    v=setup;jid=ready(v);key=str(uuid4());calls=[];body=approve(v,jid)
    result=send(v,jid,body,key,deliver=lambda *a:calls.append(a) or ('SENT','wamid.synthetic'))
    assert result['status']=='SENT' and calls
    assert send(v,jid,body,key)['status']=='SENT'
    with pytest.raises(HTTPException):send(v,jid,body)
    with v.sessions() as db:
        assert db.query(S).count()==1;assert db.query(M).filter_by(direction='outbound').count()==1
        assert db.query(M).filter_by(direction='outbound').one().response_kind=='monara_voice'

@pytest.mark.parametrize('change',['approval','preview','window','recipient','unknown','transport_failure'])
def test_send_rejections_and_uncertain_outcomes(setup,change):
    v=setup;jid=ready(v);body=approve(v,jid)
    if change=='approval':body['approved']=False
    if change=='preview':body['previewed']=False
    if change=='window':
        with v.sessions() as db:db.get(C,v.ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    if change=='recipient':
        def upload(raw):
            with v.sessions() as db:db.get(C,v.ids[0]).phone_number='94770099999';db.commit()
            return 'synthetic-media'
        result=send(v,jid,body,upload=upload);assert result['status']=='FAILED';return
    if change in {'unknown','transport_failure'}:
        def deliver(*a):
            if change=='transport_failure':raise RuntimeError('private failure')
            return 'UNKNOWN',None
        assert send(v,jid,body,deliver=deliver)['status']=='UNKNOWN_REVIEW'
        with pytest.raises(HTTPException):send(v,jid,body)
        return
    with pytest.raises(HTTPException):send(v,jid,body)

def test_no_hidden_spoken_disclosure(setup):
    p=values();p['transcript']='Exact transcript without disclosure';jid=ready(setup,p)
    assert send(setup,jid)['status']=='SENT'
    with setup.sessions() as db:
        assert db.get(J,jid).transcript==p['transcript']

def test_original_client_approval_remains_compatible(setup):
    jid=ready(setup);body=approve(setup,jid)
    body['heard_disclosure']=body.pop('previewed')
    key=str(uuid4())
    assert send(setup,jid,body,key)['status']=='SENT'
    assert send(setup,jid,body,key)['status']=='SENT'

@pytest.mark.parametrize('case',['missing','false','string','both','legacy_false'])
def test_preview_envelope_rejects_unapproved_requests(setup,case):
    jid=ready(setup);body=approve(setup,jid)
    if case=='missing':body.pop('previewed')
    if case=='false':body['previewed']=False
    if case=='string':body['previewed']='true'
    if case=='both':body['heard_disclosure']=True
    if case=='legacy_false':body['heard_disclosure']=False;body.pop('previewed')
    calls=[]
    with pytest.raises(HTTPException) as error:
        send(setup,jid,body,upload=lambda raw:calls.append(raw))
    assert error.value.status_code==422 and calls==[]
    with setup.sessions() as db:assert db.query(S).count()==0

def test_global_ai_off_does_not_block_manual_generation(setup):
    import global_ai_control
    assert not global_ai_control.in_automatic_job()
    from models import GlobalAIReplyControl
    with setup.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    jid=ready(setup)
    with setup.sessions() as db:assert db.get(J,jid).status=='READY'

def test_policy_admin_csrf_and_persistence(setup):
    v=setup;client=v.client
    assert client.get('/crm/monara/admin').status_code==200
    assert client.post('/crm/monara/admin/policy',json={}).status_code==403
    v.login('quick-agent');assert client.get('/crm/monara/admin').status_code==403
    assert client.get('/crm/monara/context/'+str(v.ids[1])).status_code==403


def test_retention_and_crash_recovery(setup):
    v=setup;jid=ready(v);other=create(v)
    with v.sessions() as db:
        db.get(J,jid).expires_at=datetime.utcnow()-timedelta(seconds=1);db.get(J,other).created_at=datetime.utcnow()-timedelta(minutes=10);db.get(J,other).status='GENERATING';db.commit()
        result=svc.maintain(db,db.get(User,v.uid));db.commit();assert result['expired']==1 and result['requires_review']==1
        assert db.get(J,jid).mp3 is None and db.get(J,jid).transcript==''
        assert db.get(J,other).failure=='INTERRUPTED_OUTCOME_REVIEW_NO_RETRY'

def test_http_generation_poll_and_media(setup):
    v=setup;client=v.client;headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':str(uuid4())}
    response=client.post(f'/crm/monara/conversations/{v.ids[0]}/generate',json=values(),headers=headers)
    assert response.status_code==200,response.text;jid=response.json()['id']
    assert client.post(f'/crm/monara/conversations/{v.ids[0]}/generate',json=values(),headers=headers).json()['id']==jid
    job=client.get(f'/crm/monara/jobs/{jid}?cid={v.ids[0]}').json();assert job['status']=='READY'
    media=client.get(f'/crm/monara/jobs/{jid}/audio/mp3?cid={v.ids[0]}');assert media.status_code==200 and media.headers['content-type'].startswith('audio/mpeg')
    assert 'no-store' in media.headers['cache-control']
    assert client.get(f'/crm/monara/jobs/{jid}?cid={v.ids[1]}').status_code==404

def test_send_key_payload_binding_and_sent_playback(setup):
    v=setup;jid=ready(v);p=approve(v,jid);key=str(uuid4());assert send(v,jid,p,key)['status']=='SENT'
    p['sha256']='different'
    with pytest.raises(HTTPException):send(v,jid,p,key)
    with v.sessions() as db:
        mid=db.query(S).one().message_id
        assert svc.playback(db,db.get(User,v.uid),v.ids[0],mid)==v.rendered['ogg']
        with pytest.raises(HTTPException):svc.playback(db,db.get(User,v.uid),v.ids[1],mid)

def test_real_sdk_configuration_without_api_calls():
    import pathlib
    python=sdk_python()
    out=subprocess.run([str(python),'monara_sdk_worker.py','--capabilities'],check=True,capture_output=True,timeout=15)
    d=json.loads(out.stdout);assert d['sdk']=='3.6.1' and d['custom_voice_supported'];assert d['config']['speech_config']['voice_config']=={'voice':'voice_xu8nahptj1p9'};assert d['config']['response_modalities']==['audio']


def test_postgresql_policy_reservation_concurrency_and_migration(pg,monkeypatch,rendered):
    from concurrent.futures import ThreadPoolExecutor
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from pathlib import Path
    import importlib.util
    engine,sessions,uid=pg
    path=Path('alembic/versions/20261127_62_monara.py');spec=importlib.util.spec_from_file_location('monara62',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(conn)));m.upgrade();m.upgrade()
    monkeypatch.setattr(settings,'MONARA_TTS_ENABLED',True);monkeypatch.setattr(settings,'GEMINI_API_KEY','synthetic')
    with sessions() as db:
        c=C(phone_number='94770012345',customer_name='Synthetic PG',assigned_to_user_id=uid,last_customer_message_at=datetime.utcnow());db.add(c);p=db.get(P,1);p.data={**svc.DEFAULT,'generation':True,'monthly_budget':'1','reserve_per_request':'1'};db.commit();cid=c.id
    def reserve(i):
        with sessions() as db:
            try:job=svc.create(db,db.get(User,uid),cid,values(),str(uuid4()));db.commit();return job.id
            except HTTPException as e:db.rollback();return e.status_code
    with ThreadPoolExecutor(2) as pool:out=list(pool.map(reserve,range(2)))
    assert sum(isinstance(v,str) for v in out)==1 and 429 in out
    with sessions() as db:
        assert db.query(J).count()==1
        with pytest.raises(DBAPIError):db.execute(text('DELETE FROM monara_events'));db.commit()
        db.rollback()
    with engine.begin() as conn:
        monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(conn)))
        with pytest.raises(RuntimeError,match='history exists'):m.downgrade()

def test_storage_budget(setup,monkeypatch):
    monkeypatch.setattr(settings,'MONARA_TTS_STORAGE_MAX_BYTES',1)
    with pytest.raises(HTTPException) as e:create(setup)
    assert e.value.status_code==429 and 'storage budget' in e.value.detail


def test_download_range_and_library_scope(setup):
    v=setup;jid=ready(v)
    response=v.client.get(f'/crm/monara/jobs/{jid}/audio/mp3?cid={v.ids[0]}',headers={'Range':'bytes=0-99'})
    assert response.status_code==206 and len(response.content)==100
    assert 'Monara-AI.mp3' in response.headers['content-disposition']
    assert v.client.get(f'/crm/monara/jobs/{jid}/audio/mp3?cid={v.ids[0]}',headers={'Range':'bytes=99999999-'}).status_code==416


def test_empty_migration_rollback_and_same_generation_key(pg,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    import importlib.util
    engine,sessions,uid=pg;spec=importlib.util.spec_from_file_location('monara62',Path('alembic/versions/20261127_62_monara.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:
        monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(conn)));m.upgrade();m.downgrade();m.upgrade()
    monkeypatch.setattr(settings,'MONARA_TTS_ENABLED',True);monkeypatch.setattr(settings,'GEMINI_API_KEY','synthetic')
    with sessions() as db:
        c=C(phone_number='94770012346',customer_name='Synthetic PG',assigned_to_user_id=uid,last_customer_message_at=datetime.utcnow());db.add(c);db.get(P,1).data={**svc.DEFAULT,'generation':True,'monthly_budget':'10','reserve_per_request':'1'};db.commit();cid=c.id
    key=str(uuid4())
    def create_one(_):
        with sessions() as db:job=svc.create(db,db.get(User,uid),cid,values(),key);db.commit();return job.id
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(create_one,range(2)))
    assert ids[0]==ids[1]
    with sessions() as db:assert db.query(J).count()==1

def test_sent_audio_uses_existing_inbox_view_permissions(setup):
    v=setup;jid=ready(v);send(v,jid)
    with v.sessions() as db:
        row=db.query(S).one();agent=db.query(User).filter_by(username='quick-agent').one();db.get(C,v.ids[0]).assigned_to_user_id=agent.id;db.commit()
        assert svc.playback(db,agent,v.ids[0],row.message_id)==v.rendered['ogg']
        with pytest.raises(HTTPException):svc.scoped(db,agent,jid,v.ids[0])


def test_postgresql_single_send_under_concurrency(pg,monkeypatch,rendered):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import IntegrityError
    from threading import Lock
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    import importlib.util
    engine,sessions,uid=pg;spec=importlib.util.spec_from_file_location('monara62',Path('alembic/versions/20261127_62_monara.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(conn)));m.upgrade()
    for k,v in [('MONARA_TTS_ENABLED',True),('MONARA_TTS_SEND_ENABLED',True),('GEMINI_API_KEY','synthetic')]:monkeypatch.setattr(settings,k,v)
    with sessions() as db:
        c=C(phone_number='94770012347',customer_name='Synthetic PG',assigned_to_user_id=uid,last_customer_message_at=datetime.utcnow());db.add(c);db.get(P,1).data={**svc.DEFAULT,'generation':True,'sending':True,'monthly_budget':'10','reserve_per_request':'1'};db.commit();cid=c.id
        j=svc.create(db,db.get(User,uid),cid,values(),str(uuid4()));jid=j.id;db.commit()
    svc.process(sessions,jid,provider=lambda *a:rendered)
    calls=[];lock=Lock()
    def deliver(*args):
        with lock:calls.append(args)
        return 'SENT','wamid.monara-pg'
    def dispatch(_):
        with sessions() as db:
            try:
                result=svc.send(db,db.get(User,uid),cid,jid,{'approved':True,'previewed':True,'sha256':db.get(J,jid).sha256,'assignment_version':0},str(uuid4()),lambda raw:'synthetic-media',deliver);return result['status']
            except (HTTPException,IntegrityError):db.rollback();return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(dispatch,range(2)))
    assert len(calls)==1 and results.count('SENT')==1 and results.count('CONFLICT')==1
    with sessions() as db:assert db.query(S).count()==1 and db.query(M).filter_by(direction='outbound').count()==1

def test_actual_whatsapp_adapter_uses_standard_audio_with_global_ai_off(setup,monkeypatch):
    import whatsapp_service,httpx
    from models import GlobalAIReplyControl
    v=setup;jid=ready(v);payloads=[]
    with v.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setattr(settings,'META_WHATSAPP_ACCESS_TOKEN','synthetic-token')
    monkeypatch.setattr(settings,'META_WHATSAPP_PHONE_NUMBER_ID','synthetic-phone-id')
    def fake_post(url,**kwargs):
        if url.endswith('/media'):result={'id':'synthetic-media'}
        else:payloads.append(kwargs['json']);result={'messages':[{'id':'wamid.mocked-adapter'}]}
        return httpx.Response(200,json=result,request=httpx.Request('POST',url))
    monkeypatch.setattr(whatsapp_service.httpx,'post',fake_post)
    r=v.client.post(f'/crm/monara/jobs/{jid}/send/{v.ids[0]}',json=approve(v,jid),headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':str(uuid4())})
    assert r.status_code==200,r.text
    assert r.json()['status']=='SENT';assert len(payloads)==1 and payloads[0]['type']=='audio' and payloads[0]['audio']=={'id':'synthetic-media'}

def test_library_archive_management(setup):
    v=setup;jid=ready(v)
    with v.sessions() as db:svc.library_save(db,db.get(User,v.uid),jid,v.ids[0],{'title':'Synthetic Library','category':'Welcome','approved':True});db.commit()
    path='/crm/monara/admin/archive/'+jid
    assert v.client.post(path,json={'active':False},headers={'X-CSRF-Token':v.csrf}).status_code==200
    assert v.client.get('/crm/monara/library').json()['total']==0
    assert v.client.get('/crm/monara/library?inactive=true').json()['total']==1
    assert v.client.post(path,json={'active':True},headers={'X-CSRF-Token':v.csrf}).status_code==200
    assert v.client.get('/crm/monara/library').json()['total']==1

@pytest.mark.parametrize('style',list(__import__('monara_styles').PROMPTS))
def test_structured_styles_preserve_exact_transcript(setup,style):
    from monara_styles import PROMPTS
    p=values();p['style']=style;v=setup;jid=create(v,p);seen=[]
    svc.process(v.sessions,jid,provider=lambda *args:seen.append(args) or v.rendered)
    assert seen[0][0]==p['transcript'] and seen[0][4]==PROMPTS[style] and seen[0][5]=='si'
    with v.sessions() as db:assert db.get(J,jid).transcript==p['transcript'] and db.get(J,jid).style_prompt==PROMPTS[style]


def test_actual_sdk_separates_nonspoken_style():
    import pathlib
    python=sdk_python()
    code="""from google.genai import types,models
import json
p=types.Part.from_text(text='## Transcript:\\nExact synthetic transcript')
p.speech_metadata=types.SpeechMetadata(style='Warm, friendly and relaxed')
print(json.dumps(models._Part_to_mldev(p.model_dump(exclude_none=True))))
"""
    p=subprocess.run([str(python),'-c',code],capture_output=True,check=True,timeout=15)
    d=json.loads(p.stdout);assert d['text']=='## Transcript:\nExact synthetic transcript';assert d['speechMetadata']=={'style':'Warm, friendly and relaxed'}

def test_readonly_diagnostic_without_app_startup(pg,monkeypatch,capsys):
    from pathlib import Path
    from sqlalchemy import text
    from alembic.config import Config
    from alembic import command
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util
    import scripts.validate_monara_readonly as check
    engine,sessions,uid=pg
    spec=importlib.util.spec_from_file_location('monara62',Path('alembic/versions/20261127_62_monara.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:monkeypatch.setattr(m,'op',Operations(MigrationContext.configure(conn)));m.upgrade()
    command.stamp(Config('alembic.ini'),'20261127_62')
    monkeypatch.setattr(check,'SessionLocal',sessions)
    monkeypatch.setattr(settings,'MONARA_TTS_PYTHON',str(sdk_python()))
    check.main();result=json.loads(capsys.readouterr().out)
    assert result['transaction_read_only'] and result['migration']=='20261127_62'
    assert result['sdk']['available'] and result['sdk']['structured_style_supported']
    assert result['provider_calls']==result['messages_sent']==0 and not result['database_mutations']

def test_existing_send_concurrency_guard_is_reused(setup):
    from saved_voice_routes import SEND_SLOTS
    v=setup;jid=ready(v);held=0
    try:
        while SEND_SLOTS.acquire(blocking=False):held+=1
        response=v.client.post(f'/crm/monara/jobs/{jid}/send/{v.ids[0]}',json=approve(v,jid),headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':str(uuid4())})
        assert response.status_code==429
        with v.sessions() as db:assert db.query(S).count()==0
    finally:
        for _ in range(held):SEND_SLOTS.release()

def test_policy_rejects_malformed_style_with_safe_error(setup):
    v=setup
    with v.sessions() as db:
        p={**svc.policy(db)};p['default_style']=[]
        with pytest.raises(HTTPException) as e:svc.configure(db,db.get(User,v.uid),{'version':db.get(P,1).version,'policy':p})
        assert e.value.status_code==422

def test_admin_policy_updates_are_versioned_and_audited(setup):
    v=setup
    with v.sessions() as db:
        user=db.get(User,v.uid);row=db.get(P,1);old=row.version;p=svc.policy(db);p['daily_requests_per_staff']=15
        result=svc.configure(db,user,{'version':old,'policy':p});db.commit();assert result['version']==old+1
        audit=db.query(E).filter_by(action='POLICY_CHANGED').one();assert audit.detail['previous']['daily_requests_per_staff']==20 and audit.detail['current']['daily_requests_per_staff']==15
        assert 'style_digest' in audit.detail and 'GEMINI_API_KEY' not in str(audit.detail)
        with pytest.raises(HTTPException):svc.configure(db,user,{'version':old,'policy':p})
