"""Hardware tests are entirely mocked/disposable; no physical command or live order."""
import copy
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv,LOCATION
from test_pos import pos,checkout,cash,newcart
from models import User,SalesPayment
from pos_models import POSCheckout,POSRegister,POSSession
from hardware_models import PrintDevice,HardwareJob,HardwareEvent
import hardware_service as h
from settings import settings


@pytest.fixture
def hardware(pos,monkeypatch):
    r=pos
    for key in ['POS_LOCAL_PRINT_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED','RETAIL_HARDWARE_LABEL_PRINT_ENABLED']:monkeypatch.setattr(settings,key,True)
    with r.sessions() as db:
        u=db.get(User,r.uid);reg=db.get(POSSession,r.sid).register_id
        d,code=h.enroll(db,u,reg);r.did=d.id;r.token=h.pair(db,code);h.manage(db,u,d.id,'activate')
        h.configure(db,u,d.id,{**h.DEFAULT,'auto_receipts':True,'drawer_queue':h.DRAWER_QUEUE,'drawer_verified':True})
    ready(r)
    return r


def ready(r,mode='RECEIPT'):
    with r.sessions() as db:
        u=db.get(User,r.uid);d=h.device(db,r.did);h.request_mode(db,u,d.id,mode,True)
        h.heartbeat(db,d,{'version':d.version,'queues':[{'name':name,'state':'READY','jobs':0} for name in [h.RECEIPT_QUEUE,h.LABEL_QUEUE,h.DRAWER_QUEUE]],'queues_empty':True,'local_verified':True,'drawer_verified':True,'mode_ack':mode})


def take(r):
    with r.sessions() as db:return h.claim(db,h.authenticate(db,'Bearer '+r.token))


def result(r,j,status='SUBMITTED_TO_PRINTER'):
    with r.sessions() as db:return h.acknowledge(db,h.authenticate(db,'Bearer '+r.token),j['id'],j['claim'],status,123)


def finish_sale(r,plan=None):
    rid=checkout(r,plan);assert h.pos.dispatch_one(r.sessions,r.pos);return rid


@pytest.mark.parametrize('method,cash_component', [('CASH',True),('CARD_PAYMENT',False),('BANK_TRANSFER',False),('SPLIT',True)])
def test_complete_sale_receipt_and_conditional_drawer(hardware,method,cash_component):
    r=hardware
    plan=cash() if method=='CASH' else [{'method':'CARD_PAYMENT','amount':'8500.00','received':'8500.00','reference':'TERMINAL-1','confirmed':True}] if method=='CARD_PAYMENT' else [{'method':'BANK_TRANSFER','amount':'8500.00','received':'8500.00','reference':'BANK-1','confirmed':False}] if method=='BANK_TRANSFER' else cash('1000','1000')+[{'method':'CARD_PAYMENT','amount':'7500','received':'7500','reference':'T-2','confirmed':True}]
    rid=checkout(r,plan)
    if method=='BANK_TRANSFER':
        with r.sessions() as db:
            u=db.get(User,r.uid);p=db.query(SalesPayment).filter_by(payment_method='BANK_TRANSFER').one();h.pos.verify_bank(db,u,rid,p.id,uuid4().hex);h.pos.finalize(db,u,rid)
    h.tick(r.sessions);assert take(r) is None  # Still not authoritatively COMPLETE.
    h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions);h.tick(r.sessions)
    with r.sessions() as db:
        assert db.query(HardwareJob).filter_by(kind='RECEIPT').count()==1
        assert db.query(HardwareJob).filter_by(kind='DRAWER').count()==int(cash_component)
    j=take(r);assert j['kind']=='RECEIPT' and j['queue']==h.RECEIPT_QUEUE
    assert take(r) is None
    result(r,j)
    if cash_component:
        action=take(r);assert action['kind']=='DRAWER' and action['queue']==h.DRAWER_QUEUE;result(r,action)
    assert take(r) is None and len(r.pos.calls)==1 and not r.remote.mutations
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE'


def test_held_unpaid_uncertain_no_receipt_or_drawer(hardware):
    r=hardware;cid,v=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);h.pos.edit_cart(db,u,cid,v,'hold',{},r.pos)
    rid=checkout(r);r.pos.fail_after_create=True;h.pos.dispatch_one(r.sessions,r.pos)
    h.tick(r.sessions)
    with r.sessions() as db:
        assert not db.query(HardwareJob).count()
        with pytest.raises(HTTPException):h.receipt_job(db,db.get(User,r.uid),r.did,rid,uuid4().hex)


def test_unknown_delivery_never_replayed_and_explicit_review(hardware):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions);j=take(r)
    with r.sessions() as db:
        row=db.get(HardwareJob,j['id']);row.delivered_at=h.now()-timedelta(minutes=3);db.commit()
    assert take(r) is None
    with r.sessions() as db:
        row=db.get(HardwareJob,j['id']);assert row.status=='UNKNOWN' and row.attempts==1
        with pytest.raises(HTTPException):h.review_job(db,db.get(User,r.uid),row.id,False,'Uncertain output',False)
        h.review_job(db,db.get(User,r.uid),row.id,False,'Agent stopped and both queues inspected',True)
        second=h.receipt_job(db,db.get(User,r.uid),r.did,rid,uuid4().hex);db.commit()
        from pypdf import PdfReader
        import io
        assert 'DUPLICATE' in PdfReader(io.BytesIO(second.pdf)).pages[0].extract_text()
        assert db.query(HardwareJob).filter_by(kind='DRAWER').count()==1
    # Original cash drawer action is not duplicated by a receipt retry.


def test_media_switch_holds_receipts_and_label_routing(hardware,monkeypatch):
    from retail_models import RetailPrintJob
    r=hardware;rid=finish_sale(r);h.tick(r.sessions);ready(r,'LABEL');assert take(r) is None
    raw=b'%PDF-1.7\nmock approved label artifact'
    monkeypatch.setattr(h.retail_labels,'read_pdf',lambda row:raw);monkeypatch.setattr(h.retail_labels,'ensure_reprint_safe',lambda *a:None)
    with r.sessions() as db:
        row=RetailPrintJob(shop=h.retail.shop(),actor_id=r.uid,status='GENERATED',template={'layout':'roll'},rows=[],total=3);db.add(row);db.commit()
        j=h.labels_job(db,db.get(User,r.uid),r.did,row.id,uuid4().hex);db.commit();assert j.pdf==raw
    label=take(r);assert label['kind']=='LABEL' and label['queue']==h.LABEL_QUEUE and label['media']=='LABEL';result(r,label)
    ready(r,'RECEIPT');assert take(r)['kind']=='RECEIPT'


def test_revocation_credentials_pairing_expiry_and_browser_auth(hardware):
    r=hardware
    assert r.client.get('/api/print-agent/config').status_code==401
    assert r.client.get('/api/print-agent/config',headers={'Authorization':'Bearer '+r.token,'Origin':'https://evil.example'}).status_code==403
    with r.sessions() as db:
        u=db.get(User,r.uid);code=h.manage(db,u,r.did,'rotate')
        with pytest.raises(HTTPException):h.authenticate(db,'Bearer '+r.token)
        fresh=h.pair(db,code)
        with pytest.raises(HTTPException):h.pair(db,code)
        h.manage(db,u,r.did,'activate');h.manage(db,u,r.did,'revoke')
        with pytest.raises(HTTPException):h.authenticate(db,'Bearer '+fresh)
        with pytest.raises(HTTPException):h.manage(db,u,r.did,'activate')


def test_cross_register_receipt_and_cross_device_artifact_rejected(hardware):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions);j=take(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);other=h.pos.register(db,u,'Other physical counter',LOCATION);d,code=h.enroll(db,u,other.id);token=h.pair(db,code);h.manage(db,u,d.id,'activate')
        with pytest.raises(HTTPException):h.receipt_job(db,u,d.id,rid,uuid4().hex)
        with pytest.raises(HTTPException):h.claimed(db,h.authenticate(db,'Bearer '+token),j['id'],j['claim'])


def test_manual_open_permission_and_idempotency(hardware):
    r=hardware;key=uuid4().hex
    with r.sessions() as db:
        u=db.get(User,r.uid);a=h.manual_drawer(db,u,r.did,key,'Supervisor change float');db.commit();b=h.manual_drawer(db,u,r.did,key,'Supervisor change float');assert a.id==b.id;db.commit()
        u.role='STAFF';u.section_access=['pos.view','pos.sell'];db.commit()
        with pytest.raises(HTTPException):h.manual_drawer(db,u,r.did,uuid4().hex,'Unauthorized')
    j=take(r);assert j is None  # Actor permission revoked after creation, checked again at delivery.


def test_offline_gate_and_frozen_audit(hardware,monkeypatch):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions)
    monkeypatch.setattr(settings,'POS_LOCAL_PRINT_ENABLED',False);assert take(r) is None
    monkeypatch.setattr(settings,'POS_LOCAL_PRINT_ENABLED',True)
    with r.sessions() as db:
        d=h.device(db,r.did);d.observation={**d.observation,'queues_empty':False};db.commit()
    assert take(r) is None
    for sql in ["UPDATE hardware_jobs SET kind='DRAWER'",'UPDATE hardware_jobs SET attempts=2','DELETE FROM hardware_events']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()


def test_permit_is_single_use_and_agent_has_no_financial_api(hardware):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions);j=take(r);headers={'Authorization':'Bearer '+r.token}
    path='/api/print-agent/jobs/'+j['id']+'/permit'
    assert r.client.post(path,json={'claim':j['claim']},headers=headers).status_code==200
    assert r.client.post(path,json={'claim':j['claim']},headers=headers).status_code==409
    response=r.client.get('/api/print-agent/jobs/'+j['id']+'/pdf',headers={**headers,'X-Job-Claim':j['claim']});assert response.status_code==200 and response.content.startswith(b'%PDF-')
    with r.sessions() as db:assert db.get(POSCheckout,rid).status=='COMPLETE'


def test_closed_session_blocks_drawer_and_width_mismatch_blocks_receipt(hardware):
    r=hardware;rid=finish_sale(r)
    with r.sessions() as db:
        u=db.get(User,r.uid)
        with pytest.raises(HTTPException):h.receipt_job(db,u,r.did,rid,uuid4().hex,'58')
    h.tick(r.sessions);j=take(r);result(r,j)
    with r.sessions() as db:
        u=db.get(User,r.uid);h.pos.close_register(db,u,r.sid,'18500.00','Day closed after complete sale')
    assert take(r) is None
    with r.sessions() as db:assert db.query(HardwareJob).filter_by(kind='DRAWER').one().status=='FAILED'


def test_cash_movement_control_is_independent_of_auto_receipts(hardware):
    r=hardware
    with r.sessions() as db:
        u=db.get(User,r.uid);d=h.device(db,r.did);h.configure(db,u,d.id,{**d.config,'auto_receipts':False,'auto_cash_movements':True})
        offset=h.pos.cfg(db)[0]['accounts']['bank'];h.pos.cash_move(db,u,r.sid,'100','PAID_IN',uuid4().hex,'Additional float',offset)
    ready(r);h.tick(r.sessions);h.tick(r.sessions)
    j=take(r);assert j['kind']=='DRAWER';result(r,j);assert take(r) is None


def test_no_automatic_historical_replay_or_fresh_drawer_after_timeout(hardware):
    r=hardware;rid=finish_sale(r)
    with r.sessions() as db:
        d=h.device(db,r.did);d.enabled_at=h.now()+timedelta(seconds=1);db.commit()
    h.tick(r.sessions)
    with r.sessions() as db:assert not db.query(HardwareJob).count()
    with r.sessions() as db:
        d=h.device(db,r.did);d.enabled_at=h.now()-timedelta(minutes=5)
        sale=db.get(POSCheckout,rid);sale.completed_at=h.now()-timedelta(minutes=3);sale.created_at=sale.completed_at;db.commit()
    h.tick(r.sessions);j=take(r);result(r,j);assert take(r) is None
    with r.sessions() as db:assert db.query(HardwareJob).filter_by(kind='DRAWER').one().result['code']=='EXPIRED_OR_CONFIGURATION_CHANGED'


def test_generation_failure_does_not_rollback_sale_or_repeat_forever(hardware,monkeypatch):
    r=hardware;rid=finish_sale(r)
    def failure(*a,**kw):raise OSError('mock font/storage failure')
    monkeypatch.setattr(h.pos_receipts,'generate',failure);h.tick(r.sessions);h.tick(r.sessions)
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE'
        assert db.query(HardwareJob).count()==1 and db.query(HardwareJob).one().status=='FAILED'
        assert db.query(HardwareEvent).filter_by(action='AUTO_RECEIPT_BLOCKED').count()==1


def test_revoke_cannot_rotate_back_into_active_and_unique_register(hardware):
    r=hardware
    with r.sessions() as db:
        u=db.get(User,r.uid);d=h.device(db,r.did);other,code=h.enroll(db,u,d.register_id);h.pair(db,code)
        with pytest.raises(HTTPException):h.manage(db,u,other.id,'activate')
        h.manage(db,u,other.id,'revoke')
        with pytest.raises(HTTPException):h.manage(db,u,other.id,'rotate')


def test_pairing_expiration_and_observation_consistency(hardware):
    r=hardware
    with r.sessions() as db:
        u=db.get(User,r.uid);d=h.device(db,r.did);other,code=h.enroll(db,u,d.register_id);other.pair_expires=h.now()-timedelta(seconds=1);db.commit()
        with pytest.raises(HTTPException):h.pair(db,code)
        body={**d.observation,'queues_empty':True,'queues':[{'name':h.RECEIPT_QUEUE,'state':'READY','jobs':1}]}
        with pytest.raises(HTTPException):h.heartbeat(db,d,body)


def test_recovery_and_audit_remain_available_with_all_gates_disabled(hardware,monkeypatch):
    r=hardware;rid=finish_sale(r);h.tick(r.sessions);j=take(r)
    with r.sessions() as db:
        row=db.get(HardwareJob,j['id']);row.delivered_at=h.now()-timedelta(minutes=3);db.commit()
    for key in ['POS_LOCAL_PRINT_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED','RETAIL_HARDWARE_LABEL_PRINT_ENABLED']:monkeypatch.setattr(settings,key,False)
    assert r.client.get('/crm/pos/api/hardware').status_code==200
    with r.sessions() as db:
        u=db.get(User,r.uid);h.review_job(db,u,j['id'],False,'Agent stopped; no automatic replay permitted',True)
        with pytest.raises(HTTPException):h.manual_drawer(db,u,r.did,uuid4().hex,'Gate disabled')
        assert db.get(POSCheckout,rid).status=='COMPLETE'


def test_late_financial_reconciliation_never_causes_delayed_drawer_kick(hardware):
    r=hardware;rid=finish_sale(r)
    with r.sessions() as db:
        sale=db.get(POSCheckout,rid);sale.created_at=h.now()-timedelta(hours=1);db.commit()
    h.tick(r.sessions);receipt=take(r);result(r,receipt);assert take(r) is None
    with r.sessions() as db:assert db.query(HardwareJob).filter_by(kind='DRAWER').one().status=='FAILED'


def test_repairing_cannot_bypass_uncertain_register_action(hardware):
    r=hardware;finish_sale(r);h.tick(r.sessions);j=take(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);old=h.device(db,r.did);reg=old.register_id;h.manage(db,u,r.did,'revoke')
        replacement,code=h.enroll(db,u,reg);h.pair(db,code)
        with pytest.raises(HTTPException):h.manage(db,u,replacement.id,'activate')
        h.review_job(db,u,j['id'],False,'Old agent stopped and physical output reviewed',True)
        h.manage(db,u,replacement.id,'activate')
