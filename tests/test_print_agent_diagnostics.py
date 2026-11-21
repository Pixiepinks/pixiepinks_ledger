"""All dispatch, driver and crash failures are synthetic; no Windows I/O."""
import hashlib,json,sqlite3
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from test_print_agent import setup
from test_receipt_connector_gdi import printer,sample
from windows_print_agent import diagnostics as d
from windows_print_agent.store import Store
from windows_print_agent.protocol import RECEIPT_QUEUE

SECRET='DO_NOT_LOG_TOKEN_PAIRING_PRIVATE_KEY_CUSTOMER_PDF'


def records(capsys):
    output=capsys.readouterr()
    assert SECRET not in output.out+output.err
    return [json.loads(line) for line in output.err.splitlines() if line.startswith('{')]


def error():
    exc=OSError(SECRET);exc.winerror=5
    return exc


def test_allowlisted_output_nested_redaction_and_numeric_codes(capsys):
    exc=error();exc.hresult=-2147024891
    with pytest.raises(OSError) as caught:
        with d.step('job_execution'):
            with d.step('driver_submission'):
                with d.step('gdi_start_doc'):raise exc
    assert caught.value is exc
    rows=records(capsys)
    assert rows==[{'event':'connector_error','stage':'gdi_start_doc','exception_class':'OSError','windows_error_code':5,'hresult':-2147024891}]
    assert set(rows[0])=={'event','stage','exception_class','windows_error_code','hresult'}


def test_unknown_labels_classes_and_bad_code_attributes_cannot_leak(capsys):
    Untrusted=type(SECRET,(Exception,),{})
    exc=Untrusted(SECRET);exc.winerror=SECRET;exc.hresult=True;exc.errno=2**100
    d.failure(SECRET,exc)
    assert records(capsys)==[{'event':'connector_error','stage':'unclassified','exception_class':'OtherException'}]


def test_broken_diagnostic_sink_preserves_original_exception(monkeypatch):
    class Sink:
        def write(self,value):raise OSError('disk unavailable')
    exc=ValueError(SECRET)
    with monkeypatch.context() as patch:
        patch.setattr(d.sys,'stderr',Sink())
        with pytest.raises(ValueError) as caught:
            with d.step('pdf_render'):raise exc
    assert caught.value is exc


@pytest.mark.parametrize('point,expected',[
    ('permit','permit_response'),('progress','progress_rpc'),
    ('heartbeat','progress_heartbeat'),('submit','driver_submission'),
])
def test_dispatch_failure_stage_and_unknown_fence(tmp_path,monkeypatch,capsys,point,expected):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    target=backend if point=='submit' else remote
    def fail(*a,**kw):raise error()
    monkeypatch.setattr(target,point,fail)
    with pytest.raises(OSError):engine.execute(job,config,True)
    rows=records(capsys);assert rows[0]['stage']==expected
    assert store.get(job['id'])[1]=='UNKNOWN'
    assert store.db.execute('SELECT spool_id FROM jobs').fetchone()[0] is None
    permits=remote.permits;calls=len(backend.calls)
    engine.execute(job,config,True)
    assert remote.permits==permits and len(backend.calls)==calls
    store.close()


def test_permit_deadline_label_retains_five_second_safety_check(tmp_path,monkeypatch,capsys):
    import windows_print_agent.engine as module
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    ticks=iter([100.0,106.0])
    with monkeypatch.context() as patch:
        patch.setattr(module.time,'monotonic',lambda:next(ticks))
        with pytest.raises(Exception):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']=='permit_deadline'
    assert not backend.calls and store.get(job['id'])[1]=='UNKNOWN'
    store.close()


def gdi_engine(tmp_path,monkeypatch):
    engine,remote,backend,store,config,job,proof=setup(tmp_path)
    real,calls,info=printer(monkeypatch)
    fingerprint=real.fingerprint_info(info)
    original=backend.fingerprint
    backend.fingerprint=lambda name:fingerprint if name==RECEIPT_QUEUE else original(name)
    proof['fingerprints'][RECEIPT_QUEUE]=fingerprint
    raw=sample();remote.pdf=lambda job:raw
    job['sha256']=hashlib.sha256(raw).hexdigest();job['size']=len(raw)
    backend.submit=real.submit
    return engine,remote,real,calls,store,config,job


@pytest.mark.parametrize('method,expected',[
    ('StartDoc','gdi_start_doc'),('StartPage','gdi_start_page'),
    ('EndPage','gdi_end_page'),('EndDoc','gdi_end_doc'),
    ('DeleteDC','gdi_release'),
])
def test_gdi_failures_before_and_after_startdoc_keep_unknown(tmp_path,monkeypatch,capsys,method,expected):
    engine,remote,real,calls,store,config,job=gdi_engine(tmp_path,monkeypatch)
    dc=real.ui.CreateDCFromHandle('mock-hdc');original=getattr(dc,method);failed=[False]
    def fail_once(*args):
        if not failed[0]:failed[0]=True;raise error()
        return original(*args)
    setattr(dc,method,fail_once);real.ui.CreateDCFromHandle=lambda handle:dc
    with pytest.raises(OSError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']==expected
    assert store.get(job['id'])[1]=='UNKNOWN'
    assert store.db.execute('SELECT spool_id FROM jobs').fetchone()[0] is None
    if method=='StartDoc':assert not calls
    else:assert any(c[0]=='StartDoc' for c in calls)
    if method=='DeleteDC':assert any(c[0]=='EndDoc' for c in calls)
    count=len(calls);engine.execute(job,config,True);assert len(calls)==count
    store.close()


@pytest.mark.parametrize('stage',[
    'driver_create_dc','driver_wrap_dc','driver_capabilities','driver_geometry',
    'driver_margins','pdf_render','pdf_image_conversion','gdi_draw','pdf_cleanup',
])
def test_driver_and_rendering_stages_are_distinguishable(tmp_path,monkeypatch,capsys,stage):
    engine,remote,real,calls,store,config,job=gdi_engine(tmp_path,monkeypatch)
    def fail(*args,**kw):raise error()
    if stage=='driver_create_dc':real.gui.CreateDC=fail
    elif stage=='driver_wrap_dc':real.ui.CreateDCFromHandle=fail
    elif stage in {'driver_capabilities','driver_geometry','driver_margins'}:
        dc=real.ui.CreateDCFromHandle('mock');get=dc.GetDeviceCaps
        target={'driver_capabilities':88,'driver_geometry':110,'driver_margins':8}[stage]
        dc.GetDeviceCaps=lambda key:fail() if key==target else get(key)
        real.ui.CreateDCFromHandle=lambda handle:dc
    elif stage=='gdi_draw':
        from PIL import ImageWin
        monkeypatch.setattr(ImageWin.Dib,'draw',fail)
    else:
        import pypdfium2
        if stage=='pdf_render':monkeypatch.setattr(pypdfium2.PdfPage,'render',fail)
        elif stage=='pdf_image_conversion':monkeypatch.setattr(pypdfium2.PdfBitmap,'to_pil',fail)
        else:
            original=pypdfium2.PdfPage.close;failed=[False]
            def close(page,*args,**kwargs):
                if any(c[0]=='EndDoc' for c in calls) and not failed[0]:failed[0]=True;raise error()
                return original(page,*args,**kwargs)
            monkeypatch.setattr(pypdfium2.PdfPage,'close',close)
    with pytest.raises(OSError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']==stage
    assert store.get(job['id'])[1]=='UNKNOWN'
    if stage=='pdf_cleanup':assert any(c[0]=='EndDoc' for c in calls)
    store.close()


def test_journal_submission_failure_after_spool_remains_unknown(tmp_path,monkeypatch,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    original=store.finish
    def finish(jid,state,spool_id=None):
        if state=='SUBMITTED_TO_PRINTER':raise sqlite3.OperationalError(SECRET)
        return original(jid,state,spool_id)
    monkeypatch.setattr(store,'finish',finish)
    with pytest.raises(sqlite3.OperationalError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']=='journal_submitted'
    assert len(backend.calls)==1 and store.get(job['id'])[1]=='UNKNOWN'
    assert store.db.execute('SELECT spool_id FROM jobs').fetchone()[0] is None
    store.close()


def test_crash_recovery_reports_count_without_replay_or_payload(tmp_path,monkeypatch,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    def crash(*a,**kw):raise KeyboardInterrupt()
    monkeypatch.setattr(backend,'submit',crash)
    with pytest.raises(KeyboardInterrupt):engine.execute(job,config,True)
    assert store.get(job['id'])[1]=='INTENT';store.close()
    recovered=Store(tmp_path/'actions.sqlite');engine.store=recovered
    assert records(capsys)==[{'event':'connector_recovery','stage':'journal_crash_recovery','recovered_intent_count':1}]
    assert recovered.get(job['id'])[1]=='UNKNOWN'
    engine.execute(job,config,True);assert remote.permits==1
    engine.flush_results();assert remote.results[-1][1:] == ('UNKNOWN',None)
    assert not records(capsys)
    recovered.close()
    final=Store(tmp_path/'actions.sqlite');assert not records(capsys);final.close()


def test_no_historical_diagnosis_added_for_existing_acked_unknown(tmp_path,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    store.intent(job);store.finish(job['id'],'UNKNOWN');store.acked(job['id']);store.close()
    engine.store=Store(tmp_path/'actions.sqlite')
    engine.execute(job,config,True);engine.flush_results()
    assert not backend.calls and not remote.results and remote.permits==0 and not records(capsys)
    engine.store.close()


def test_http_failure_reports_status_without_url_headers_or_body(capsys):
    request=httpx.Request('POST','https://invalid.test/'+SECRET,headers={'Authorization':SECRET})
    response=httpx.Response(403,request=request,text=SECRET)
    with pytest.raises(httpx.HTTPStatusError):
        with d.step('permit_response'):response.raise_for_status()
    assert records(capsys)==[{'event':'connector_error','stage':'permit_response','exception_class':'HTTPStatusError','http_status':403}]


def test_intent_failure_logs_stage_without_consuming_permit(tmp_path,monkeypatch,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    def fail(*args):raise sqlite3.OperationalError(SECRET)
    monkeypatch.setattr(store,'intent',fail)
    with pytest.raises(sqlite3.OperationalError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']=='journal_intent'
    assert store.get(job['id']) is None and remote.permits==0 and not backend.calls
    store.close()


def test_unknown_persistence_failure_keeps_existing_crash_recovery(tmp_path,monkeypatch,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    backend.error=error()
    def fail(*args):raise sqlite3.OperationalError(SECRET)
    monkeypatch.setattr(store,'finish',fail)
    with pytest.raises(sqlite3.OperationalError):engine.execute(job,config,True)
    assert [r['stage'] for r in records(capsys)]==['driver_submission','journal_unknown']
    assert store.get(job['id'])[1]=='INTENT';store.close()
    recovered=Store(tmp_path/'actions.sqlite')
    assert recovered.get(job['id'])[1]=='UNKNOWN' and records(capsys)[0]['stage']=='journal_crash_recovery'
    recovered.close()


def test_ack_journal_failure_does_not_reclassify_or_resubmit_print(tmp_path,monkeypatch,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    engine.execute(job,config,True)
    def fail(*args):raise sqlite3.OperationalError(SECRET)
    monkeypatch.setattr(store,'acked',fail)
    with pytest.raises(sqlite3.OperationalError):engine.flush_results()
    assert records(capsys)[0]['stage']=='journal_ack'
    assert store.get(job['id'])[1]=='SUBMITTED_TO_PRINTER' and len(backend.calls)==1
    engine.execute(job,config,True);assert len(backend.calls)==1
    store.close()


def test_printer_status_preserves_unavailable_fallback_and_logs_only_safe_fields(capsys):
    from windows_print_agent.windows import WindowsPrinters
    backend=object.__new__(WindowsPrinters)
    def fail(name):raise error()
    backend.info=fail
    assert backend.status([RECEIPT_QUEUE])==[{'name':RECEIPT_QUEUE,'state':'UNAVAILABLE','jobs':0,'conditions':['UNAVAILABLE']}]
    assert records(capsys)==[{'event':'connector_error','stage':'printer_status','exception_class':'OSError','windows_error_code':5}]


def test_mutex_denial_preserves_no_body_execution_and_handle_close(capsys):
    from windows_print_agent.windows import WindowsPrinters
    backend=object.__new__(WindowsPrinters);closed=[]
    backend.event=SimpleNamespace(CreateMutex=lambda *args:9,WaitForSingleObject=lambda *args:258,WAIT_OBJECT_0=0,WAIT_ABANDONED=128)
    backend.api=SimpleNamespace(CloseHandle=lambda handle:closed.append(handle))
    with pytest.raises(Exception):
        with backend.exclusive():raise AssertionError('must never execute')
    assert closed==[9] and records(capsys)[0]['stage']=='dispatch_lock'


def test_success_emits_no_diagnostics_and_separate_error_scopes_reset(tmp_path,capsys):
    engine,remote,backend,store,config,job,_=setup(tmp_path)
    engine.execute(job,config,True);engine.flush_results()
    assert not records(capsys) and store.get(job['id'])[1]=='SUBMITTED_TO_PRINTER'
    exc=ValueError(SECRET)
    for stage in ['pdf_render','driver_geometry']:
        with pytest.raises(ValueError):
            with d.step(stage):raise exc
    assert [r['stage'] for r in records(capsys)]==['pdf_render','driver_geometry']
    store.close()
