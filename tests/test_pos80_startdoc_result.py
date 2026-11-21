"""pywin32 311 PyCDC.StartDoc returns None; all Windows I/O is mocked."""
from uuid import uuid4
import pytest
from test_receipt_connector_gdi import printer,sample
from test_print_agent_diagnostics import gdi_engine,records
from test_receipt_connector import connector
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos
from hardware_models import HardwareJob,HardwareEvent
from models import User,JournalEntry,SalesPayment
from settings import settings
from windows_print_agent.store import Store
from windows_print_agent.protocol import RECEIPT_QUEUE
import hardware_service as h


def startdoc_returns(real,result):
    dc=real.ui.CreateDCFromHandle('mock-hdc');original=dc.StartDoc
    def start(title):
        original(title)
        return result
    dc.StartDoc=start;real.ui.CreateDCFromHandle=lambda handle:dc
    return dc


@pytest.mark.parametrize('result',[None,77])
def test_startdoc_binding_result_after_successful_enddoc(monkeypatch,result):
    backend,calls,info=printer(monkeypatch);startdoc_returns(backend,result)
    assert backend.submit(RECEIPT_QUEUE,sample(),'synthetic-only','RECEIPT',backend.fingerprint_info(info)) is result
    assert [c[0] for c in calls]==['StartDoc','StartPage','draw','EndPage','EndDoc']
    assert info['pDevMode'].Fields==0


def test_none_result_persists_success_and_restart_never_reprints(tmp_path,monkeypatch,capsys):
    engine,remote,real,calls,store,config,job=gdi_engine(tmp_path,monkeypatch)
    startdoc_returns(real,None)
    engine.execute(job,config,True);engine.flush_results()
    assert store.db.execute('SELECT state,spool_id,acked FROM jobs').fetchone()==('SUBMITTED_TO_PRINTER',None,1)
    assert remote.results[-1][1:]==('SUBMITTED_TO_PRINTER',None)
    assert not records(capsys)
    store.close();engine.store=Store(tmp_path/'actions.sqlite')
    count=len(calls);engine.execute(job,config,True);engine.flush_results()
    assert len(calls)==count and remote.permits==1
    engine.store.close()


@pytest.mark.parametrize('method',['EndDoc','DeleteDC'])
def test_none_binding_does_not_mask_after_print_failure_or_replay(tmp_path,monkeypatch,capsys,method):
    engine,remote,real,calls,store,config,job=gdi_engine(tmp_path,monkeypatch)
    dc=startdoc_returns(real,None);original=getattr(dc,method);failed=[False]
    def fail_once():
        if not failed[0]:failed[0]=True;raise OSError('SYNTHETIC PRIVATE ERROR MUST NOT BE LOGGED')
        return original()
    setattr(dc,method,fail_once)
    with pytest.raises(OSError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']==('gdi_end_doc' if method=='EndDoc' else 'gdi_release')
    assert store.db.execute('SELECT state,spool_id FROM jobs').fetchone()==('UNKNOWN',None)
    count=len(calls);engine.execute(job,config,True)
    assert len(calls)==count and remote.permits==1
    store.close()


def test_unexpected_binding_result_is_classified_and_remains_unknown(tmp_path,monkeypatch,capsys):
    engine,remote,real,calls,store,config,job=gdi_engine(tmp_path,monkeypatch)
    startdoc_returns(real,object())
    with pytest.raises(TypeError):engine.execute(job,config,True)
    assert records(capsys)[0]['stage']=='gdi_submission_result'
    assert any(c[0]=='EndDoc' for c in calls) and store.get(job['id'])[1]=='UNKNOWN'
    count=len(calls);engine.execute(job,config,True);assert len(calls)==count
    store.close()


def test_none_spool_id_acknowledges_via_existing_encrypted_protocol(connector,monkeypatch):
    r=connector;monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    real,calls,info=printer(monkeypatch);startdoc_returns(real,None)
    fingerprint=real.fingerprint_info(info)
    r.backend.fingerprint=lambda name:fingerprint
    r.engine.attestation['fingerprints'][RECEIPT_QUEUE]=fingerprint
    r.backend.submit=real.submit
    with r.sessions() as db:
        j=h.printer_test(db,db.get(User,r.uid),r.did,uuid4().hex);db.commit();jid=j.id
    r.engine.cycle();r.engine.cycle()
    assert [c[0] for c in calls].count('StartDoc')==1
    assert r.store.db.execute('SELECT state,spool_id,acked FROM jobs').fetchone()==('SUBMITTED_TO_PRINTER',None,1)
    with r.sessions() as db:
        j=db.get(HardwareJob,jid)
        assert j.status=='SUBMITTED_TO_PRINTER' and j.result['spool_id'] is None
        assert j.result['physical_output_verified'] is False
        assert not db.get(h.PrintDevice,r.did).config['auto_receipts']
        assert db.query(HardwareEvent).filter_by(job_id=jid,action='AGENT_SUBMITTED_TO_PRINTER').count()==1
        assert db.query(HardwareEvent).filter_by(job_id=jid,action='AGENT_UNKNOWN').count()==0
        assert db.query(HardwareJob).count()==1
        assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==0
