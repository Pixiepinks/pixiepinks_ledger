import json,os,subprocess
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from monara_diagnostics import clean,diagnostic
from monara_sdk_worker import collect,WorkerFailure
import monara_audio as audio
import monara_service as svc
from monara_models import MonaraJob as J,MonaraEvent as E
from models import User
from test_monara import setup,inbox,rendered,create,sdk_python

def test_untrusted_details_are_strictly_allowlisted():
    d=clean({'stage':'secret-transcript','exception_class':'secret-key-class','reason':'private-provider-message','http_status':'401 token','exit_code':True,'api_key':'secret'})
    assert d=={'stage':'unknown','exception_class':'OtherError','reason':'UNCLASSIFIED'}
    assert 'secret' not in json.dumps(d)

def test_numeric_http_status_without_error_message():
    ClientError=type('ClientError',(Exception,),{})
    e=ClientError('SECRET_KEY / customer transcript');e.code=403
    d=diagnostic('provider_stream',e)
    assert d['http_status']==403 and d['exception_class']=='ClientError'
    assert 'SECRET' not in json.dumps(d)

@pytest.mark.parametrize('stage',['ogg_encoding','mp3_encoding'])
def test_encoding_error_stages(stage):
    d=diagnostic(stage,subprocess.CalledProcessError(1,['SECRET_ARG'],stderr=b'SECRET_STDERR'),'ENCODER_UNAVAILABLE')
    assert d['stage']==stage and 'SECRET' not in json.dumps(d)

def test_worker_error_survives_parent_without_raw_output(monkeypatch):
    failure={'error':'redacted','diagnostic':{'stage':'provider_stream','exception_class':'ClientError','http_status':403,'reason':'UNCLASSIFIED','response_body':'SECRET'}}
    monkeypatch.setattr(audio.subprocess,'run',lambda *a,**kw:N(returncode=1,stdout=json.dumps(failure).encode()))
    with pytest.raises(audio.MonaraAudioError) as e:audio.generate('PRIVATE_TRANSCRIPT','gemini-3.8-flash-tts','voice_xu8nahptj1p9','v1beta')
    assert e.value.diagnostic['stage']=='provider_stream' and e.value.diagnostic['http_status']==403
    assert e.value.diagnostic['exit_code']==1 and 'SECRET' not in json.dumps(e.value.diagnostic)

def test_worker_timeout_safe_diagnostic(monkeypatch):
    def fail(*a,**kw):raise subprocess.TimeoutExpired('SECRET_COMMAND',5,output=b'SECRET_AUDIO')
    monkeypatch.setattr(audio.subprocess,'run',fail)
    with pytest.raises(audio.MonaraAudioError) as e:audio.generate('PRIVATE_TRANSCRIPT','gemini-3.8-flash-tts','voice_xu8nahptj1p9','v1beta')
    assert e.value.diagnostic['stage']=='worker_launch' and e.value.diagnostic['exception_class']=='TimeoutExpired'
    assert 'SECRET' not in json.dumps(e.value.diagnostic)

def test_real_worker_rejects_invalid_profile_without_key_or_provider_call():
    python=sdk_python();payload={'model':'UNAPPROVED','transcript':'SECRET_TRANSCRIPT'}
    p=subprocess.run([str(python),'monara_sdk_worker.py'],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin'},timeout=15)
    assert p.returncode==1
    d=json.loads(p.stdout)['diagnostic'];assert d['stage']=='request_validation' and d['reason']=='UNAPPROVED_PROFILE'
    assert b'SECRET' not in p.stdout+p.stderr

def test_failed_job_retains_safe_diagnostic_and_never_retries(setup):
    v=setup;jid=create(v);calls=[]
    def fail(*args):
        calls.append(1)
        raise audio.MonaraAudioError('WORKER_FAILED',{'stage':'provider_stream','exception_class':'ClientError','http_status':429,'reason':'UNCLASSIFIED','body':'SECRET'})
    svc.process(v.sessions,jid,fail);svc.process(v.sessions,jid,fail)
    assert calls==[1]
    with v.sessions() as db:
        job=db.get(J,jid);assert job.failure=='PROVIDER_OR_ENCODING_FAILED_NO_RETRY'
        assert job.usage['diagnostic']['http_status']==429
        event=db.query(E).filter_by(job_id=jid,action='GENERATION_FAILED').one()
        assert event.detail['diagnostic']==job.usage['diagnostic']
        assert 'SECRET' not in json.dumps(event.detail)

def test_killed_worker_retains_numeric_exit_code(monkeypatch):
    monkeypatch.setattr(audio.subprocess,'run',lambda *a,**kw:N(returncode=-9,stdout=b''))
    with pytest.raises(audio.MonaraAudioError) as e:audio.generate('PRIVATE','gemini-3.8-flash-tts','voice_xu8nahptj1p9','v1beta')
    assert e.value.diagnostic['stage']=='worker_result' and e.value.diagnostic['exit_code']==-9
