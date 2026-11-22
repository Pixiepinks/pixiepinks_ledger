"""Process ownership and unattended supervision; never retries a physical action."""
import hashlib,json,logging,os,random,signal,ssl,sys,time
from datetime import datetime,timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import UUID
import httpx
from .transport import AgentError

REGISTER1='f18b74ce-cf1f-440f-973f-579640ff46e6'
INSTALL=Path(r'C:\PixiePinks\ReceiptConnector-190')
ORIGIN='https://pixiepinksledger-production.up.railway.app'

class AlreadyRunning(Exception):pass


def windows_identity():
    import win32api,win32security
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32security.TOKEN_QUERY)
    try:sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:token.Close()
    return win32api.GetUserName(),sid


class ProcessLock:
    """Named kernel mutex; process death releases ownership, never resets SQLite.

    Key on Windows SID and canonical journal directory (stronger than installation
    alone), so copies/manual/scheduled invocations sharing a journal cannot race.
    The existing printer mutex still coordinates old connector versions/devices.
    """
    def __init__(self,root,identity=None,events=None,api=None):
        if events is None:
            import win32event as events,win32api as api
        self.events=events;self.api=api
        identity=identity or windows_identity()[1]
        digest=hashlib.sha256((identity+'|'+os.path.normcase(str(Path(root).resolve()))).encode()).hexdigest()
        self.name='Global\\PixiePinks.ReceiptProcess.'+digest;self.handle=None
    def __enter__(self):
        self.handle=self.events.CreateMutex(None,False,self.name)
        try:
            result=self.events.WaitForSingleObject(self.handle,0)
            if result not in {self.events.WAIT_OBJECT_0,self.events.WAIT_ABANDONED}:raise AlreadyRunning()
        except BaseException:
            self.api.CloseHandle(self.handle);self.handle=None;raise
        return self
    def __exit__(self,*args):
        try:self.events.ReleaseMutex(self.handle)
        finally:self.api.CloseHandle(self.handle);self.handle=None


def timestamp():return datetime.now(timezone.utc).isoformat()


class Monitor:
    """Fixed structured fields only; no caller-supplied messages, paths or payloads."""
    EVENTS={'START','STOP','CONNECTED','READINESS','JOB','RECOVERY','ERROR','BACKOFF'}
    CATEGORIES={'NONE','NETWORK','HTTP_TRANSIENT','SECURITY_OR_CONFIGURATION','LOCAL_REVIEW','STOP_REQUESTED','UNEXPECTED_EXIT','INSTANCE_BUSY','LOGGING_UNAVAILABLE','READINESS_HOLD'}
    def __init__(self,root,device_id,user):
        self.root=Path(root);self.directory=self.root/'logs';self.directory.mkdir(parents=True,exist_ok=True)
        self.logger=logging.Logger('receipt-runtime-'+str(os.getpid()));self.logger.propagate=False
        self.handler=RotatingFileHandler(self.directory/'connector.jsonl',maxBytes=1024*1024,backupCount=3,encoding='utf-8')
        self.logger.addHandler(self.handler)
        self.state={'pid':os.getpid(),'windows_user':'User' if user.casefold()=='user' else 'OTHER','device_id':str(UUID(device_id)),
                    'started_at':timestamp(),'process':'RUNNING','connection':'NOT_VERIFIED','printer_ready':False,'media':'UNKNOWN','error_category':'NONE'}
        self.emit('START')
    def emit(self,event,**fields):
        if event not in self.EVENTS:return
        try:
            safe={}
            for key,value in fields.items():
                if key=='error_category' and value in self.CATEGORIES:safe[key]=value
                elif key=='media' and value in {'RECEIPT','LABEL','A4','UNKNOWN'}:safe[key]=value
                elif key=='printer_ready' and type(value) is bool:safe[key]=value
                elif key=='job_status' and value in {'DELIVERED','INTENT','PERMITTED','UNKNOWN','FAILED','SUBMITTED_TO_PRINTER','ACKNOWLEDGED'}:safe[key]=value
                elif key=='job_id':
                    try:safe[key]=str(UUID(value))
                    except (ValueError,TypeError,AttributeError):pass
                elif key in {'delay_seconds','http_status','exit_code'} and type(value) in {float,int} and 0<=value<=3600:safe[key]=value
            if event=='CONNECTED':self.state.update(connection='AUTHENTICATED',last_successful_connection=timestamp(),error_category='NONE')
            if event=='READINESS' and safe.get('printer_ready') is False:self.state['error_category']='READINESS_HOLD'
            if event=='STOP':self.state.update(process='STOPPED',printer_ready=False)
            if event=='ERROR':self.state.update(connection='REVIEW' if safe.get('error_category')=='SECURITY_OR_CONFIGURATION' else 'UNAVAILABLE',printer_ready=False)
            self.state.update({k:v for k,v in safe.items() if k in {'media','printer_ready','error_category'}})
            self.state['updated_at']=timestamp()
            value={'event':event,'timestamp':self.state['updated_at'],**safe}
            self.logger.info(json.dumps(value,separators=(',',':')))
            temporary=self.root/'runtime-status.tmp';temporary.write_text(json.dumps(self.state),encoding='utf-8');os.replace(temporary,self.root/'runtime-status.json')
        except Exception:pass  # Logging cannot change driver/journal results.
    def diagnostic(self,line):
        from .diagnostics import STAGES,CLASSES
        try:
            data=json.loads(line)
            if data.get('event')=='connector_error':
                value={'event':'connector_error','stage':data['stage'] if data.get('stage') in STAGES else 'unclassified',
                       'exception_class':data['exception_class'] if data.get('exception_class') in CLASSES else 'OtherException','timestamp':timestamp()}
                for key in ['windows_error_code','os_error_code','hresult','http_status']:
                    v=data.get(key)
                    if type(v) is int and -(2**31)<=v<=2**32-1:value[key]=v
                self.logger.info(json.dumps(value,separators=(',',':')))
            elif data.get('event')=='connector_recovery':
                count=data.get('recovered_intent_count')
                if type(count) is int and 0<count<1000000:self.logger.info(json.dumps({'event':'RECOVERY','recovered_intent_count':count,'timestamp':timestamp()}))
        except Exception:pass
    def write(self,text):
        # diagnostics._write emits one JSON record. Raw traceback/message chunks are dropped.
        for line in text.splitlines():self.diagnostic(line)
        return len(text)
    def flush(self):
        try:self.handler.flush()
        except Exception:pass
    def close(self):
        try:self.handler.close()
        except Exception:pass
        self.logger.removeHandler(self.handler)


def transient(exc):
    cause=exc
    for _ in range(8):
        if isinstance(cause,ssl.SSLError):return False
        cause=getattr(cause,'__cause__',None)
        if cause is None:break
    if isinstance(exc,httpx.HTTPStatusError):return exc.response.status_code in {408,425,429,500,502,503,504}
    return isinstance(exc,(httpx.NetworkError,httpx.TimeoutException,TimeoutError))


class Backoff:
    def __init__(self,rng=random.uniform):self.failures=0;self.rng=rng
    def reset(self):self.failures=0
    def next(self):
        base=min(120,5*2**min(self.failures,5));self.failures+=1
        return min(120,self.rng(base*.8,base*1.2))


def validate_binding(credentials,proof,installation,user):
    from .protocol import RECEIPT_QUEUE
    if not isinstance(user,str) or user.casefold()!='user' or Path(installation).resolve()!=INSTALL.resolve():raise AgentError('Register 1 installation/account mismatch')
    if credentials.get('device_id')!=REGISTER1 or proof.get('device_id')!=REGISTER1 or credentials.get('server')!=ORIGIN:raise AgentError('Register 1 binding mismatch')
    pin=credentials.get('public_key')
    if not isinstance(pin,str) or len(pin)!=64 or len(bytes.fromhex(pin))!=32:raise AgentError('Pinned encryption key required')
    if not isinstance(credentials.get('token'),str) or credentials['token'].split('.',1)[0]!=REGISTER1:raise AgentError('Original credentials required')
    if proof.get('normal_drawers_none') is not True or proof.get('media_and_cutter_tested') is not True or set(proof.get('fingerprints',{}))!={RECEIPT_QUEUE}:raise AgentError('Existing receipt-only attestation required')


def validate_dependencies(importer=None):
    """Import-only preflight; no printer/DC, network or journal side effects."""
    if importer is None:
        from importlib import import_module
        importer=import_module
    for name in ('win32api','win32crypt','win32print','win32ui','win32gui','win32con','win32event','win32security','httpx','pypdfium2','PIL.ImageWin','cryptography.hazmat.primitives.asymmetric.x25519'):
        importer(name)


def validate_journal(path):
    """Inspect existing SQLite read-only; never bootstrap a missing/corrupt journal."""
    import sqlite3
    path=Path(path)
    if not path.is_file():raise AgentError('Existing journal required; no initialization permitted')
    db=None
    try:
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        db.execute('PRAGMA query_only=ON')
        columns={row[1] for row in db.execute('PRAGMA table_info(jobs)')}
        expected={'id','device_id','identity','claim','state','spool_id','acked'}
        if columns!=expected or db.execute('PRAGMA quick_check').fetchone()!=('ok',) or db.execute('PRAGMA journal_mode').fetchone()!=('wal',):raise AgentError('Existing journal schema/integrity requires review')
        if db.execute("SELECT COUNT(*) FROM jobs WHERE state IS NULL OR state NOT IN ('INTENT','UNKNOWN','FAILED','SUBMITTED_TO_PRINTER') OR acked IS NULL OR acked NOT IN (0,1)").fetchone()[0]:raise AgentError('Existing journal state requires review')
    except sqlite3.Error:raise AgentError('Existing journal unavailable; do not reset or replace it') from None
    finally:
        if db is not None:db.close()


def validate_config(config,device_id,strict=False):
    from .protocol import RECEIPT_QUEUE
    if config.get('id')!=device_id or config.get('status')!='ACTIVE':raise AgentError('Device authorization unavailable')
    if strict:
        c=config.get('config',{})
        if config.get('media') not in {'RECEIPT','UNKNOWN'} or device_id!=REGISTER1 or config.get('register_id')!=1 or not c.get('receipt_only') or c.get('receipt_profile')!='80' or c.get('receipt_queue')!=RECEIPT_QUEUE or c.get('auto_cash_movements') or c.get('drawer_queue') or c.get('drawer_verified'):raise AgentError('Register 1 receipt-only policy required')


def supervise(engine,monitor,stop,*,backoff=None):
    backoff=backoff or Backoff()
    while not stop.is_set():
        try:
            engine.cycle();backoff.reset();delay=5
        except Exception as exc:
            from .diagnostics import failure
            failure('poll_cycle',exc)
            retry=transient(exc);category='HTTP_TRANSIENT' if isinstance(exc,httpx.HTTPStatusError) and retry else 'NETWORK' if retry else 'SECURITY_OR_CONFIGURATION' if isinstance(exc,(AgentError,httpx.HTTPStatusError)) else 'LOCAL_REVIEW'
            monitor.emit('ERROR',error_category=category)
            if not retry:return 0  # deliberate review stop: Scheduler must NOT restart.
            delay=backoff.next();monitor.emit('BACKOFF',delay_seconds=delay)
        stop.wait(delay)  # interruptible; no work or journal reset during recovery.
    return 0


def run(root,loader,backend_factory,*,strict=False,installation=None):
    """Both manual and scheduled invocations own state through final cleanup."""
    try:
        with ProcessLock(root):
            return _run_owned(root,loader,backend_factory,strict=strict,installation=installation)
    except AlreadyRunning:return 0
    except Exception:return 0  # OS ownership unavailable: no journal or printer access.


def _run_owned(root,loader,backend_factory,*,strict=False,installation=None):
    import threading
    from .diagnostics import call
    from .engine import Engine
    from .store import Store
    from .transport import Transport,EncryptedTransport
    root=Path(root);monitor=None;store=None;transport=None;previous=sys.stderr;stop=threading.Event();handlers={}
    try:
        user=windows_identity()[0]
        if strict:
            monitor=Monitor(root,REGISTER1,user);sys.stderr=monitor
        credentials=call('credential_load',loader,root/'credentials.dpapi');proof=call('attestation_load',loader,root/'attestation.dpapi')
        if strict:
            call('startup_binding',validate_binding,credentials,proof,installation,user)
            call('startup_journal',validate_journal,root/'actions.sqlite')
        if monitor is None:monitor=Monitor(root,credentials['device_id'],user);sys.stderr=monitor
        for sig in [signal.SIGINT,signal.SIGTERM]+([signal.SIGBREAK] if hasattr(signal,'SIGBREAK') else []):
            handlers[sig]=signal.signal(sig,lambda *_:stop.set())
        request=root/'stop.request'
        if request.exists():stop.set()  # Only an explicit management start may clear this fence.
        original_wait=stop.wait
        def wait(seconds):
            until=time.monotonic()+seconds
            while not stop.is_set() and time.monotonic()<until:
                if request.exists():stop.set();break
                original_wait(min(.5,max(0,until-time.monotonic())))
            return stop.is_set()
        stop.wait=wait
        if stop.is_set():return 0
        transport=EncryptedTransport(credentials['server'],credentials['public_key'],credentials['token']) if credentials.get('public_key') else Transport(credentials['server'],credentials['token'])
        backend=backend_factory()
        # Existing physical mutex prevents overlap with older manual binaries too.
        with backend.exclusive():
            store=call('journal_open',Store,root/'actions.sqlite');engine=Engine(transport,backend,store,proof,credentials['device_id'])
            engine.monitor=monitor.emit
            if strict:engine.validate_runtime_config=lambda c:validate_config(c,credentials['device_id'],True)
            return supervise(engine,monitor,stop)
    except KeyboardInterrupt:return 0
    except AgentError:
        if monitor:monitor.emit('ERROR',error_category='SECURITY_OR_CONFIGURATION')
        return 0
    except Exception:
        if monitor:monitor.emit('ERROR',error_category='LOCAL_REVIEW')
        return 0
    finally:
        if store:
            try:call('journal_close',store.close)
            except Exception:
                if monitor:monitor.emit('ERROR',error_category='LOCAL_REVIEW')
        if transport:
            try:transport.http.close()
            except Exception:pass
        if monitor:monitor.emit('STOP',error_category='STOP_REQUESTED' if stop.is_set() else monitor.state['error_category'],exit_code=0);monitor.close()
        sys.stderr=previous
        for sig,handler in handlers.items():signal.signal(sig,handler)
