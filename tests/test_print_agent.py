"""Windows APIs mocked: no print queue, USB device or drawer is contacted."""
import copy,hashlib
from contextlib import contextmanager
from uuid import uuid4
import pytest
from windows_print_agent.store import Store
from windows_print_agent.engine import Engine
from windows_print_agent.transport import Transport,AgentError
from windows_print_agent.protocol import *

class Backend:
    def __init__(self):self.calls=[];self.offline=False;self.busy=False;self.error=None;self.changed=False;self.owner=False
    def fingerprint(self,name):return 'changed' if self.changed else 'verified-'+name
    def shares_port(self,names):return True
    def drawer_safe(self,name):return True
    def status(self,names):return [{'name':n,'state':'OFFLINE' if self.offline else 'READY','jobs':int(self.busy)} for n in names]
    def validate_pdf(self,raw,media):assert raw.startswith(b'%PDF-')
    @contextmanager
    def exclusive(self):
        assert not self.owner;self.owner=True
        try:yield
        finally:self.owner=False
    def submit(self,queue,raw,jid,media,fingerprint,progress=None):
        if progress:progress()
        assert self.owner;self.calls.append((queue,raw,jid,media))
        if self.error:raise self.error
        return 22

class Remote:
    def __init__(self,config,job):self.value=config;self.job=job;self.results=[];self.permits=0;self.fail_ack=False;self.fail_permit=False
    def config(self):return self.value
    def heartbeat(self,body):return self.value
    def claim(self):return self.job
    def pdf(self,job):return b'%PDF-1.7\nmock receipt'
    def permit(self,job):
        self.permits+=1
        if self.fail_permit:raise TimeoutError()
    def progress(self,job):pass
    def result(self,jid,claim,status,spool):
        if self.fail_ack:raise TimeoutError()
        self.results.append((jid,status,spool))


def setup(tmp_path,kind='RECEIPT'):
    did=str(uuid4());queue={'RECEIPT':RECEIPT_QUEUE,'LABEL':LABEL_QUEUE,'DRAWER':DRAWER_QUEUE}[kind];mode='LABEL' if kind=='LABEL' else 'RECEIPT'
    config={'id':did,'version':1,'status':'ACTIVE','media':mode,'mode_request':None,'config':{'drawer_queue':DRAWER_QUEUE,'drawer_verified':True,'a4_queue':''}}
    job={'id':str(uuid4()),'device_id':did,'version':1,'protocol':VERSION,'kind':kind,'queue':queue,'media':mode,'sha256':hashlib.sha256(b'').hexdigest() if kind=='DRAWER' else hashlib.sha256(b'%PDF-1.7\nmock receipt').hexdigest(),'size':0 if kind=='DRAWER' else len(b'%PDF-1.7\nmock receipt'),'claim':'one-time-claim-value'}
    proof={'device_id':did,'normal_drawers_none':True,'media_and_cutter_tested':True,'drawer_queue_isolated':True,'drawer_driver_tested':True,'drawer_no_replay_tested':True,'fingerprints':{n:'verified-'+n for n in [RECEIPT_QUEUE,LABEL_QUEUE,DRAWER_QUEUE]}}
    backend=Backend();remote=Remote(config,job);store=Store(tmp_path/'actions.sqlite');engine=Engine(remote,backend,store,proof,did)
    return engine,remote,backend,store,config,job,proof


@pytest.mark.parametrize('kind',['RECEIPT','LABEL','DRAWER'])
def test_queue_selection_and_duplicate_delivery(tmp_path,kind):
    engine,remote,backend,store,config,job,_=setup(tmp_path,kind)
    engine.execute(job,config,True);engine.execute(job,config,True);engine.flush_results()
    assert len(backend.calls)==1 and backend.calls[0][0]==job['queue']
    assert remote.permits==1 and remote.results[-1][1]=='SUBMITTED_TO_PRINTER'
    assert store.get(job['id'])[1]=='SUBMITTED_TO_PRINTER'
    if kind=='DRAWER':
        import io
        from pypdf import PdfReader
        text=PdfReader(io.BytesIO(backend.calls[0][1])).pages[0].extract_text()
        assert 'NOT A SALES RECEIPT' in text and job['id'] in text and 'TOTAL LKR' not in text
    store.close()


@pytest.mark.parametrize('failure',['driver_timeout','spooler_failure','permit_timeout','unplugged'])
def test_unknown_outcomes_never_replay_after_restart(tmp_path,failure):
    engine,remote,backend,store,config,job,proof=setup(tmp_path,'DRAWER')
    if failure=='permit_timeout':remote.fail_permit=True
    else:backend.error=TimeoutError(failure) if failure=='driver_timeout' else OSError(failure)
    with pytest.raises(Exception):engine.execute(job,config,True)
    calls=len(backend.calls);store.close();new_store=Store(tmp_path/'actions.sqlite')
    assert new_store.get(job['id'])[1]=='UNKNOWN'
    engine=Engine(remote,backend,new_store,proof,config['id']);engine.execute(job,config,True)
    assert len(backend.calls)==calls and remote.permits==1
    engine.flush_results();assert remote.results[-1][1]=='UNKNOWN';new_store.close()


def test_crash_after_intent_and_ack_loss(tmp_path):
    engine,remote,backend,store,config,job,proof=setup(tmp_path)
    store.intent(job);store.close();new=Store(tmp_path/'actions.sqlite');engine=Engine(remote,backend,new,proof,config['id'])
    engine.execute(job,config,True);assert not backend.calls
    remote.fail_ack=True
    with pytest.raises(TimeoutError):engine.flush_results()
    remote.fail_ack=False;engine.flush_results();assert remote.results[-1][1]=='UNKNOWN';assert not backend.calls;new.close()


def test_ack_reconnect_only_resends_result_not_print(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    engine.execute(job,config,True);remote.fail_ack=True
    with pytest.raises(TimeoutError):engine.flush_results()
    remote.fail_ack=False;engine.cycle();assert len(backend.calls)==1 and remote.permits==1;store.close()


@pytest.mark.parametrize('change',['wrong_device','wrong_queue','wrong_mode','driver_changed','drawer_unverified'])
def test_local_security_and_configuration_gates(tmp_path,change):
    engine,remote,backend,store,config,job,_=setup(tmp_path,'DRAWER')
    if change=='wrong_device':job['device_id']=str(uuid4())
    if change=='wrong_queue':job['queue']=LABEL_QUEUE
    if change=='wrong_mode':job['media']='LABEL'
    if change=='driver_changed':backend.changed=True
    if change=='drawer_unverified':config['config']['drawer_verified']=False
    if change in {'driver_changed','drawer_unverified'}:
        engine.execute(job,config,True);assert store.get(job['id'])[1]=='FAILED'
    else:
        with pytest.raises(AgentError):engine.execute(job,config,False)
    assert not backend.calls;store.close()


def test_media_offline_busy_and_unverified_prevent_claim(tmp_path):
    engine,remote,backend,store,config,job,proof=setup(tmp_path)
    backend.offline=True;engine.cycle();assert not backend.calls
    backend.offline=False;backend.busy=True;engine.cycle();assert not backend.calls
    backend.busy=False;proof['normal_drawers_none']=False;engine.cycle();assert not backend.calls
    proof['normal_drawers_none']=True;config['media']='UNKNOWN';engine.cycle();assert not backend.calls
    config['media']='RECEIPT';engine.cycle();assert len(backend.calls)==1;store.close()


def test_shared_resource_prevents_conflicting_queue_submission(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    backend.busy=True;engine.execute(job,config,True);assert not backend.calls and store.get(job['id'])[1]=='FAILED';store.close()


def test_credential_rotation_retains_old_fences_without_blocking_new_device(tmp_path):
    engine,remote,backend,store,config,job,proof=setup(tmp_path)
    store.intent(job);store.finish(job['id'],'UNKNOWN')
    assert store.pending(str(uuid4()))==[] and store.pending(config['id'])
    store.close()


@pytest.mark.parametrize('server',['http://crm.example','https://user:secret@crm.example','https://crm.example/path','https://crm.example?x=1','https://crm.example:8443'])
def test_tls_origin_required(server):
    with pytest.raises(AgentError):Transport(server)


def test_drawer_driver_retention_or_unverified_replay_blocks_only_drawer(tmp_path):
    engine,remote,backend,store,config,job,proof=setup(tmp_path,'DRAWER')
    backend.drawer_safe=lambda name:False
    assert engine.verified(config)==(True,False)
    backend.drawer_safe=lambda name:True;proof['drawer_no_replay_tested']=False
    assert engine.verified(config)==(True,False)
    store.close()
