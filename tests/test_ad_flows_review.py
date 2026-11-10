"""Final-review fault, restart and in-flight control checks; no real messaging."""
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from threading import Event
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
import pytest
import customer_questions as q
from test_ad_flows import flows,rule,master,incoming
from test_ad_assignment import routing,AD
from test_whatsapp_quick_messages import inbox
from models import CustomerQuestionRun as Run,AdFlowExecution as Execution,ProcessedWhatsAppMessage as Receipt,WhatsAppConversation as Conversation,GlobalAIReplyControl


@pytest.mark.parametrize('action',['takeover','disable'])
def test_control_waits_for_entered_send_then_blocks_later_steps(flows,monkeypatch,action):
    f=flows;rule(f);master(f);incoming(f)
    entered=Event();release=Event();calls=[]
    def send(*a,**kw):
        entered.set();assert release.wait(8);calls.append(True);return 'wamid.review-inflight'
    monkeypatch.setattr(q,'send_buttons',send)
    with f[0][1]() as db:cid=db.query(Run).one().conversation_id
    def control():
        return f[0][0].post(f'/crm/whatsapp/api/conversations/{cid}/takeover') if action=='takeover' else master(f,False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sending=pool.submit(q.dispatch_one,f[0][1]);assert entered.wait(5)
            changing=pool.submit(control)
            with pytest.raises(TimeoutError):changing.result(timeout=.15)
            release.set();assert sending.result(timeout=8)
            assert changing.result(timeout=8).status_code==200
    finally:release.set()
    assert calls==[True] and not q.dispatch_one(f[0][1])
    assert master(f,True).status_code==200
    assert incoming(f).status_code==200  # Same original receipt cannot restart anything.
    assert not q.dispatch_one(f[0][1])
    with f[0][1]() as db:
        assert db.query(Run).count()==1
        assert db.query(Run).one().status==('STOPPED' if action=='takeover' else 'PAUSED')
        assert db.get(GlobalAIReplyControl,1).enabled is False


def test_restart_keeps_waiting_and_uncertain_claims_without_replay(flows,monkeypatch):
    f=flows;rule(f);master(f);incoming(f)
    restarted=sessionmaker(bind=f[0][1].kw['bind'],expire_on_commit=False)
    assert q.dispatch_one(restarted) and len(f[1])==1
    assert not q.dispatch_one(restarted)  # WAITING survives restart; no second question without reply.
    with restarted() as db:db.query(Run).one().status='SENDING';db.commit()
    assert not q.dispatch_one(restarted)  # Simulated crash after claim is never blindly retried.
    assert incoming(f).status_code==200 and len(f[1])==1
    with restarted() as db:assert db.query(Run).count()==1 and db.query(Execution).count()==1


def test_failed_execution_insert_rolls_back_receipt_and_run_then_retry_is_safe(flows):
    f=flows;rule(f);master(f)
    def fail(*args):raise SQLAlchemyError('private failure details')
    event.listen(Execution,'before_insert',fail)
    try:response=incoming(f)
    finally:event.remove(Execution,'before_insert',fail)
    assert response.status_code==503 and 'private failure' not in response.text
    with f[0][1]() as db:assert db.query(Run).count()==0 and db.query(Receipt).count()==0
    assert incoming(f).status_code==200
    with f[0][1]() as db:assert db.query(Run).count()==1 and db.query(Execution).count()==1 and db.query(Receipt).count()==1


def test_switching_ads_cannot_replace_active_flow_or_bypass_customer_cooldown(flows):
    f=flows;rule(f,policy='REPEAT');rule(f,aid='999',policy='REPEAT');master(f);incoming(f)
    incoming(f,mid='other-ad.active',aid='999')
    with f[0][1]() as db:
        assert db.query(Run).count()==1 and db.query(Execution).order_by(Execution.id.desc()).first().reason=='ACTIVE_FLOW'
        db.query(Run).one().status='COMPLETE';db.commit()
    incoming(f,mid='other-ad.cooldown',aid='999')
    with f[0][1]() as db:
        assert db.query(Run).count()==1 and db.query(Execution).order_by(Execution.id.desc()).first().reason=='COOLDOWN'
        db.query(Execution).filter_by(status='STARTED').one().created_at-=timedelta(hours=25);db.commit()
    incoming(f,mid='other-ad.allowed',aid='999')
    with f[0][1]() as db:assert db.query(Run).count()==2


def test_window_closes_before_queued_send_without_auto_retry(flows,monkeypatch):
    f=flows;rule(f);master(f);incoming(f)
    import quick_message_service
    now=datetime.utcnow()
    class Later:
        @staticmethod
        def utcnow():return now+timedelta(hours=25)
    with monkeypatch.context() as patch:
        patch.setattr(quick_message_service,'datetime',Later)
        assert q.dispatch_one(f[0][1])
    with f[0][1]() as db:assert db.query(Run).one().status=='WINDOW_CLOSED'
    assert not f[1] and not q.dispatch_one(f[0][1])


def test_uncertain_provider_result_is_not_automatically_retried(flows,monkeypatch):
    f=flows;rule(f);master(f);incoming(f);calls=[]
    monkeypatch.setattr(q,'send_buttons',lambda *a,**k:calls.append(True) and None)
    assert q.dispatch_one(f[0][1]) and not q.dispatch_one(f[0][1])
    assert incoming(f).status_code==200 and not q.dispatch_one(f[0][1])
    with f[0][1]() as db:assert db.query(Run).one().status=='UNCERTAIN' and db.query(Execution).count()==1
    assert calls==[True]


@pytest.mark.parametrize('status',['SENDING','UNCERTAIN','WINDOW_CLOSED'])
def test_master_cycle_cannot_make_unreviewed_sends_resumable(flows,status):
    f=flows;rule(f);master(f);incoming(f)
    with f[0][1]() as db:
        run=db.query(Run).one();run.status=status;cid=run.conversation_id;db.commit()
    assert master(f,False).status_code==200 and master(f,True).status_code==200
    with f[0][1]() as db:run=db.query(Run).one();version=run.version
    response=f[0][0].post(f'/crm/whatsapp/api/conversations/{cid}/customer-questions',json={'action':'resume','version':version},headers={'X-CSRF-Token':f[0][5]})
    assert response.status_code==409
    assert not q.dispatch_one(f[0][1]) and not f[1]
    with f[0][1]() as db:assert db.query(Run).one().status==('UNCERTAIN' if status=='SENDING' else status)
