"""Unattended runtime tests; Windows APIs/transport/printing are isolated mocks."""
import copy,json,multiprocessing,os,shutil,ssl,subprocess
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
from test_print_agent import setup
from windows_print_agent import runtime as r
from windows_print_agent.store import Store
from windows_print_agent.transport import AgentError


class WinEvents:
    WAIT_OBJECT_0=0;WAIT_ABANDONED=128
    def __init__(self,directory):self.directory=directory
    def CreateMutex(self,security,owner,name):
        import hashlib
        if os.name=='nt':
            import win32event
            return win32event.CreateMutex(None,False,name)
        return open(Path(self.directory)/hashlib.sha256(name.encode()).hexdigest(),'a+')
    def WaitForSingleObject(self,handle,timeout):
        if os.name=='nt':
            import win32event
            return win32event.WaitForSingleObject(handle,timeout)
        import fcntl
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);return 0
        except BlockingIOError:return 258
    def ReleaseMutex(self,handle):
        if os.name=='nt':
            import win32event
            return win32event.ReleaseMutex(handle)
        import fcntl
        fcntl.flock(handle,fcntl.LOCK_UN)
    def CloseHandle(self,handle):
        if os.name=='nt':
            import win32api
            return win32api.CloseHandle(handle)
        handle.close()


def contender(directory,root,queue,release):
    events=WinEvents(directory)
    try:
        with r.ProcessLock(root,identity='SID-User',events=events,api=events):
            queue.put('OWNED')
            import time
            deadline=time.monotonic()+10
            while not Path(release).exists() and time.monotonic()<deadline:time.sleep(.02)
    except r.AlreadyRunning:queue.put('BUSY')


def test_manual_scheduled_race_and_process_death_release(tmp_path):
    # Exercise real OS process locks behind a Windows API mock on Linux.
    ctx=multiprocessing.get_context('spawn' if os.name=='nt' else 'fork');queue=ctx.Queue();release=tmp_path/'release'
    owner=ctx.Process(target=contender,args=(tmp_path,tmp_path/'journal',queue,release));owner.start();assert queue.get(timeout=5)=='OWNED'
    scheduled=ctx.Process(target=contender,args=(tmp_path,tmp_path/'journal',queue,release));scheduled.start();assert queue.get(timeout=5)=='BUSY';scheduled.join(5)
    owner.terminate();owner.join(5)  # abrupt death: no stale ownership file workaround.
    replacement=ctx.Process(target=contender,args=(tmp_path,tmp_path/'journal',queue,release));replacement.start();assert queue.get(timeout=5)=='OWNED'
    release.write_text('stop');replacement.join(5);assert replacement.exitcode==scheduled.exitcode==0


def test_mutex_abandoned_and_handles_closed(tmp_path):
    calls=[]
    api=SimpleNamespace(WAIT_OBJECT_0=0,WAIT_ABANDONED=128,CreateMutex=lambda *a:55,WaitForSingleObject=lambda *a:128,ReleaseMutex=lambda h:calls.append(('release',h)),CloseHandle=lambda h:calls.append(('close',h)))
    with r.ProcessLock(tmp_path,identity='sid',events=api,api=api):pass
    assert calls==[('release',55),('close',55)]
    api.WaitForSingleObject=lambda *a:258
    with pytest.raises(r.AlreadyRunning):
        with r.ProcessLock(tmp_path,identity='sid',events=api,api=api):pass
    assert calls[-1]==('close',55)


def binding():
    return {'device_id':r.REGISTER1,'token':r.REGISTER1+'.DO_NOT_LOG_SECRET','server':r.ORIGIN,'public_key':'12'*32},{'device_id':r.REGISTER1,'normal_drawers_none':True,'media_and_cutter_tested':True,'fingerprints':{'POSPrinter POS-80':'verified'}}


@pytest.mark.parametrize('fault',['USER','PATH','REGISTER2','SERVER','PIN','TOKEN','ATTESTATION'])
def test_register1_scope_fails_closed(fault):
    c,p=binding();user='User';install=r.INSTALL
    if fault=='USER':user='Administrator'
    if fault=='PATH':install=Path('other')
    if fault=='REGISTER2':c['device_id']='8fe7dcde-7502-40c4-897d-2a91e84f4512'
    if fault=='SERVER':c['server']='https://other.example'
    if fault=='PIN':c['public_key']='not hex'
    if fault=='TOKEN':c['token']='different.SECRET'
    if fault=='ATTESTATION':p['fingerprints']['4BARCODE 3B-365B']='other'
    with pytest.raises((AgentError,ValueError)):r.validate_binding(c,p,install,user)


def test_valid_scope_and_policy_media_not_automatically_assumed():
    c,p=binding();r.validate_binding(c,p,r.INSTALL,'User')
    cfg={'id':r.REGISTER1,'status':'ACTIVE','register_id':1,'media':'UNKNOWN','config':{'receipt_only':True,'receipt_profile':'80','receipt_queue':'POSPrinter POS-80','drawer_queue':'','drawer_verified':False,'auto_cash_movements':False}}
    r.validate_config(cfg,r.REGISTER1,True);assert cfg['media']=='UNKNOWN'
    for key,value in [('receipt_only',False),('receipt_profile','58'),('drawer_queue','other')]:
        changed=copy.deepcopy(cfg);changed['config'][key]=value
        with pytest.raises(AgentError):r.validate_config(changed,r.REGISTER1,True)
    cfg['register_id']=2
    with pytest.raises(AgentError):r.validate_config(cfg,r.REGISTER1,True)


def test_backoff_is_bounded_jittered_and_resets():
    b=r.Backoff(rng=lambda a,z:z);delays=[b.next() for _ in range(20)]
    assert delays[:3]==[6,12,24] and max(delays)==120
    b.reset();assert b.next()==6


@pytest.mark.parametrize('status',[502,503,504,429])
def test_gateway_retries_use_backoff_without_print_replay(tmp_path,status):
    engine,remote,backend,store,config,job,_=setup(tmp_path);events=[];waits=[];attempt=[0]
    original=remote.config
    def config_read():
        attempt[0]+=1
        if attempt[0]<3:raise httpx.HTTPStatusError('SECRET',request=httpx.Request('GET','https://example.test'),response=httpx.Response(status))
        return original()
    remote.config=config_read
    class Stop:
        def __init__(self):self.done=False
        def is_set(self):return self.done
        def wait(self,seconds):
            waits.append(seconds)
            if len(waits)==4:self.done=True
    monitor=SimpleNamespace(emit=lambda event,**kw:events.append((event,kw)))
    assert r.supervise(engine,monitor,Stop(),backoff=r.Backoff(rng=lambda a,z:z))==0
    assert waits==[6,12,5,5] and len(backend.calls)==remote.permits==1
    assert store.get(job['id'])[1]=='SUBMITTED_TO_PRINTER';store.close()


@pytest.mark.parametrize('exception',[httpx.ConnectError('secret'),httpx.ConnectTimeout('secret'),httpx.ReadTimeout('secret')])
def test_network_dns_and_resume_recover(exception):assert r.transient(exception)


@pytest.mark.parametrize('status',[400,401,403,409])
def test_security_authorization_errors_deliberate_stop_no_scheduler_loop(status):
    exc=httpx.HTTPStatusError('SECRET',request=httpx.Request('GET','https://example.test'),response=httpx.Response(status))
    engine=SimpleNamespace(cycle=lambda:(_ for _ in ()).throw(exc));events=[]
    stop=SimpleNamespace(is_set=lambda:False,wait=lambda _:pytest.fail('No retry permitted'))
    assert r.supervise(engine,SimpleNamespace(emit=lambda e,**kw:events.append((e,kw))),stop)==0
    assert events[-1][1]['error_category']=='SECURITY_OR_CONFIGURATION'


def test_invalid_tls_never_treated_as_routine_network_failure():
    exc=httpx.ConnectError('secret');exc.__cause__=ssl.SSLCertVerificationError('secret')
    assert not r.transient(exc) and not r.transient(AgentError('secret'))


@pytest.mark.parametrize('condition',['OFFLINE','UNKNOWN','PAUSED'])
def test_printer_and_media_not_ready_never_claim_or_submit(tmp_path,condition):
    engine,remote,backend,store,config,job,_=setup(tmp_path);observations=[];engine.monitor=lambda e,**kw:observations.append((e,kw))
    if condition=='OFFLINE':backend.offline=True
    if condition=='UNKNOWN':config['media']='UNKNOWN'
    if condition=='PAUSED':config['config']['printing_paused']=True
    remote.claim=lambda:pytest.fail('Unready printer must not claim')
    engine.cycle();assert not backend.calls and not store.get(job['id'])
    assert observations[-1][1]['printer_ready'] is False;store.close()


def test_crash_intent_unknown_survives_restart_without_duplicate(tmp_path):
    engine,remote,backend,store,config,job,proof=setup(tmp_path)
    store.intent(job);store.close();store=Store(tmp_path/'actions.sqlite');engine.store=store
    assert store.get(job['id'])[1]=='UNKNOWN'
    engine.cycle();engine.cycle();assert not backend.calls and remote.permits==0
    assert remote.results[0][1]=='UNKNOWN';store.close()


def test_uncertain_physical_output_is_review_stop_and_never_resubmitted(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path);backend.error=TypeError('PRIVATE_RECEIPT_SECRET')
    monitor=SimpleNamespace(emit=lambda *a,**kw:None);stop=SimpleNamespace(is_set=lambda:False,wait=lambda _:pytest.fail('Unsafe physical retry'))
    assert r.supervise(engine,monitor,stop)==0
    assert store.get(job['id'])[1]=='UNKNOWN' and len(backend.calls)==1
    store.close();store=Store(tmp_path/'actions.sqlite');engine.store=store;backend.error=None;engine.cycle()
    assert len(backend.calls)==1 and remote.permits==1;store.close()


def test_structured_logging_redaction_rotation_and_readiness(tmp_path):
    m=r.Monitor(tmp_path,r.REGISTER1,'User');assert m.handler.maxBytes==1024*1024 and m.handler.backupCount==3
    m.handler.maxBytes=250
    secret='DO_NOT_LOG_TOKEN_CUSTOMER_PDF_PRIVATE_KEY'
    for _ in range(50):m.emit('READINESS',media='UNKNOWN',printer_ready=False,receipt=secret,error_category=secret)
    m.write(secret+'\nTraceback '+secret)
    m.write(json.dumps({'event':'connector_error','stage':secret,'exception_class':secret,'message':secret,'winerror':secret}))
    m.emit('CONNECTED');m.emit('READINESS',media='RECEIPT',printer_ready=True)
    assert m.state['connection']=='AUTHENTICATED' and m.state['last_successful_connection']
    m.emit('ERROR',error_category='NETWORK');assert not m.state['printer_ready']
    m.emit('STOP',error_category='STOP_REQUESTED',exit_code=0);m.close()
    assert len(list((tmp_path/'logs').glob('connector.jsonl*')))<=4
    assert secret not in ''.join(p.read_text() for p in tmp_path.rglob('*') if p.is_file())
    assert json.loads((tmp_path/'runtime-status.json').read_text())['process']=='STOPPED'


def test_monitor_failure_cannot_change_physical_dispatch(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    engine.monitor=lambda *a,**kw:(_ for _ in ()).throw(OSError('private'))
    engine.cycle();engine.cycle();assert len(backend.calls)==1;store.close()


def test_scheduler_management_mock_suite():
    pwsh=Path(os.getenv('TEST_PWSH') or shutil.which('pwsh') or '/tmp/pixiepinks-pwsh/pwsh')
    if not pwsh.exists():pytest.skip('Set TEST_PWSH to PowerShell 7 for mocked management tests; no Windows production operations are required')
    env={**os.environ,'XDG_CACHE_HOME':'/tmp/pixiepinks-pwsh-cache','XDG_CONFIG_HOME':'/tmp/pixiepinks-pwsh-config','XDG_DATA_HOME':'/tmp/pixiepinks-pwsh-data'}
    result=subprocess.run([str(pwsh),'-NoProfile','-File',str(Path(__file__).parent/'windows/test_receipt_autostart.ps1')],capture_output=True,text=True,env=env)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'MOCK_SCHEDULER_TESTS_PASSED' in result.stdout


@pytest.mark.parametrize('headless',[False,True])
def test_complete_run_holds_lock_until_journal_cleanup_and_graceful_stop(tmp_path,monkeypatch,headless):
    from contextlib import contextmanager,nullcontext
    import windows_print_agent.engine as em
    import windows_print_agent.transport as tm
    import windows_print_agent.store as sm
    c,p=binding();original_store=Store(tmp_path/'actions.sqlite');original_store.close();original_bytes=(tmp_path/'actions.sqlite').read_bytes()
    monkeypatch.setattr(r,'INSTALL',tmp_path/'install');events=[]
    @contextmanager
    def lock(root):
        events.append('LOCK');yield;events.append('UNLOCK')
    class Journal:
        def __init__(self,path):events.append('JOURNAL_OPEN')
        def close(self):events.append('JOURNAL_CLOSE')
    class Engine:
        def __init__(self,*args):pass
        def cycle(self):
            self.monitor('CONNECTED');self.monitor('READINESS',media='RECEIPT',printer_ready=True)
            (tmp_path/'stop.request').write_text('STOP')
            events.append('CYCLE')
    monkeypatch.setattr(r,'ProcessLock',lock);monkeypatch.setattr(r,'windows_identity',lambda:('User','SID'))
    monkeypatch.setattr(em,'Engine',Engine);monkeypatch.setattr(sm,'Store',Journal)
    monkeypatch.setattr(tm,'EncryptedTransport',lambda *a:SimpleNamespace(http=SimpleNamespace(close=lambda:events.append('HTTP_CLOSE'))))
    loader=lambda path:c if path.name=='credentials.dpapi' else p
    with monkeypatch.context() as headless_patch:
        if headless:
            headless_patch.setattr(r.sys,'stdout',None);headless_patch.setattr(r.sys,'stderr',None)
        assert r.run(tmp_path,loader,lambda:SimpleNamespace(exclusive=nullcontext),strict=True,installation=r.INSTALL)==0
    assert events.index('JOURNAL_CLOSE')<events.index('UNLOCK') and events.count('CYCLE')==1
    state=json.loads((tmp_path/'runtime-status.json').read_text())
    assert state['process']=='STOPPED' and state['error_category']=='STOP_REQUESTED'
    assert (tmp_path/'actions.sqlite').read_bytes()==original_bytes
    # Persistent stop request prevents even journal initialization on an accidental relaunch.
    events.clear();r.run(tmp_path,loader,lambda:pytest.fail('Stop fence must prevent backend access'),strict=True,installation=r.INSTALL)
    assert events==['LOCK','UNLOCK']


def test_register2_or_missing_journal_run_cannot_open_store(tmp_path,monkeypatch):
    from contextlib import nullcontext
    import windows_print_agent.store as sm
    c,p=binding();monkeypatch.setattr(r,'ProcessLock',lambda root:nullcontext());monkeypatch.setattr(r,'windows_identity',lambda:('User','SID'));monkeypatch.setattr(r,'INSTALL',tmp_path/'install')
    monkeypatch.setattr(sm,'Store',lambda path:pytest.fail('No journal initialization permitted'))
    loader=lambda path:c if path.name=='credentials.dpapi' else p
    assert r.run(tmp_path,loader,lambda:pytest.fail('No backend permitted'),strict=True,installation=r.INSTALL)==0
    c['device_id']='8fe7dcde-7502-40c4-897d-2a91e84f4512'
    assert r.run(tmp_path,loader,lambda:pytest.fail('Register 2 untouched'),strict=True,installation=r.INSTALL)==0
    assert not (tmp_path/'actions.sqlite').exists()


def test_ack_network_failure_after_submission_recovers_without_reprint(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path);original=remote.result;attempts=[]
    def result(*args):
        attempts.append(1)
        if len(attempts)==1:raise httpx.ConnectError('DO_NOT_LOG_TOKEN')
        return original(*args)
    remote.result=result
    class Stop:
        def __init__(self):self.count=0
        def is_set(self):return self.count>=3
        def wait(self,seconds):self.count+=1
    assert r.supervise(engine,SimpleNamespace(emit=lambda *a,**kw:None),Stop(),backoff=r.Backoff(rng=lambda a,z:a))==0
    assert len(backend.calls)==remote.permits==1 and len(remote.results)==1
    assert store.db.execute('SELECT state,acked FROM jobs').fetchone()==('SUBMITTED_TO_PRINTER',1)
    store.close()


def test_sleep_resume_printer_availability_recovers_without_media_override(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path);backend.offline=True;seen=[]
    engine.monitor=lambda event,**kw:seen.append((event,kw))
    class Stop:
        def __init__(self):self.count=0
        def is_set(self):return self.count>=3
        def wait(self,seconds):self.count+=1;backend.offline=False
    r.supervise(engine,SimpleNamespace(emit=lambda *a,**kw:None),Stop())
    ready=[v['printer_ready'] for e,v in seen if e=='READINESS']
    assert ready[0] is False and ready[1] is True
    assert config['media']=='RECEIPT' and len(backend.calls)==remote.permits==1
    store.close()


@pytest.mark.parametrize('fault',['MISSING','CORRUPT','EMPTY','SCHEMA'])
def test_existing_journal_preflight_never_reinitializes(tmp_path,fault):
    import sqlite3
    path=tmp_path/'actions.sqlite'
    if fault=='CORRUPT':path.write_bytes(b'CORRUPT DO_NOT_LOG_SECRET')
    if fault=='EMPTY':path.touch()
    if fault=='SCHEMA':
        db=sqlite3.connect(path);db.execute('CREATE TABLE unrelated(id INTEGER)');db.close()
    before=path.read_bytes() if path.exists() else None
    with pytest.raises(AgentError):r.validate_journal(path)
    assert (path.read_bytes() if path.exists() else None)==before


def test_journal_preflight_preserves_pending_intent_until_owned_run(tmp_path):
    engine,remote,backend,store,config,job,_=setup(tmp_path);store.intent(job);store.close()
    path=tmp_path/'actions.sqlite';before=path.read_bytes();r.validate_journal(path)
    import sqlite3
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    assert db.execute('SELECT state,acked FROM jobs').fetchone()==('INTENT',0);db.close()
    assert path.read_bytes()==before


@pytest.mark.parametrize('missing',['NONE','pypdfium2','win32ui'])
def test_import_only_dependency_preflight(missing):
    imports=[]
    def importer(name):
        imports.append(name)
        if name==missing:raise ImportError('DO_NOT_LOG_PRIVATE_PATH')
    if missing=='NONE':
        r.validate_dependencies(importer)
        assert {'win32ui','pypdfium2','PIL.ImageWin','httpx','win32crypt'}<=set(imports)
    else:
        with pytest.raises(ImportError):r.validate_dependencies(importer)
