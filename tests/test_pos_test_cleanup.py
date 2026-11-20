"""Synthetic-only test retirement; no production or Shopify mutation calls."""
from decimal import Decimal
import pytest
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout
from models import User,SalesOrder,SalesPayment,JournalEntry
from pos_models import POSCheckout,POSOperation,POSCashMovement,POSSession,POSAudit
import pos_service as service
import pos_test_cleanup as cleanup
from scripts.inspect_pos_sales_readonly import read_only_session


def setup(r,monkeypatch):
    first=checkout(r);r.pos.fail_read=True;service.dispatch_one(r.sessions,r.pos);second=checkout(r);r.pos.fail_read=False
    with r.sessions() as db:
        a=db.get(POSCheckout,first);b=db.get(POSCheckout,second)
        monkeypatch.setattr(cleanup,'A',a.reference);monkeypatch.setattr(cleanup,'B',b.reference)
        monkeypatch.setattr(cleanup,'SHOP',a.shop);monkeypatch.setattr(cleanup,'ORDER',db.query(POSOperation).filter_by(checkout_id=first).one().outcome['remote_id'])
        monkeypatch.setattr(cleanup,'AMOUNT',Decimal('8500')) # fixture product, production constant stays 350
    return first,second


def test_exact_production_scope():
    assert cleanup.SHOP=='eb2b70' and cleanup.A=='POS-851C4716B015DF1B0E79'
    assert cleanup.B=='POS-5AB126C311C38CDBD5FA' and cleanup.AMOUNT==350


def test_preview_no_writes_atomic_retirement_no_resend_or_cash_return(pos,monkeypatch):
    a,b=setup(pos,monkeypatch)
    with read_only_session(pos.sessions) as db:
        report=cleanup.plan(db,db.get(User,pos.uid),pos.pos)
        assert report['cash_correction_total']=='-17000.00'
        assert report['sessions'][0]['expected_cash_after']=='10000.00'
    with pos.sessions() as db:
        assert db.get(POSCheckout,a).status=='REVIEW' and db.get(POSCheckout,b).status=='QUEUED'
        result=cleanup.apply(db,db.get(User,pos.uid),pos.pos,report['plan_sha256'],cleanup.CONFIRM,'Owner confirmed both tenders were simulated test cash');db.commit()
        assert result['shopify_mutations']==0
        assert all(db.get(POSCheckout,i).status=='CANCELLED' for i in (a,b))
        assert all(p.verification_status=='REJECTED' for p in db.query(SalesPayment))
        assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==2
        assert service.drawer(db,db.get(POSSession,pos.sid))==Decimal('10000')
        assert db.query(POSCashMovement).count()==4
        assert db.query(POSAudit).filter_by(action='TEST_DATA_RETIRED').count()==2
        assert db.query(POSOperation).filter_by(checkout_id=a).one().outcome['remote_id']=='gid://shopify/Order/1'
        with pytest.raises(HTTPException):cleanup.apply(db,db.get(User,pos.uid),pos.pos,report['plan_sha256'],cleanup.CONFIRM,'Repeated retirement must never change cash again')
    assert service.dispatch_one(pos.sessions,pos.pos) is False
    assert len(pos.pos.calls)==1 and not pos.remote.mutations


@pytest.mark.parametrize('problem',['missing_b','attempted_b','dispatching','posting','read_failure','b_remote','stale_plan','no_confirmation','permissions','closed_session'])
def test_cleanup_fails_closed_without_partial_retirement(pos,monkeypatch,problem):
    a,b=setup(pos,monkeypatch)
    with pos.sessions() as db:report=cleanup.plan(db,db.get(User,pos.uid),pos.pos)
    with pos.sessions() as db:
        if problem=='missing_b':monkeypatch.setattr(cleanup,'B','POS-'+'F'*20)
        elif problem=='attempted_b':db.query(POSOperation).filter_by(checkout_id=b).one().attempts=1
        elif problem=='dispatching':db.query(POSOperation).filter_by(checkout_id=b).one().status='DISPATCHING'
        elif problem=='posting':db.add(JournalEntry(date=__import__('datetime').date.today(),memo='POS-sale:'+a))
        elif problem=='closed_session':db.get(POSSession,pos.sid).status='CLOSED'
        elif problem=='permissions':db.get(User,pos.uid).role='STAFF';db.get(User,pos.uid).section_access=[]
        db.commit()
    if problem=='read_failure':pos.pos.fail_read=True
    if problem=='b_remote':
        original=pos.pos.find;pos.pos.find=lambda ref:[{'id':'gid://shopify/Order/999'}] if ref==cleanup.B else original(ref)
    with pos.sessions() as db:
        with pytest.raises(Exception):
            cleanup.apply(db,db.get(User,pos.uid),pos.pos,'wrong' if problem=='stale_plan' else report['plan_sha256'],'wrong' if problem=='no_confirmation' else cleanup.CONFIRM,'Confirmed simulated test payments; preserve Shopify');db.commit()
        db.rollback()
        assert db.get(POSCheckout,a).status=='REVIEW' and db.get(POSCheckout,b).status=='QUEUED'
        assert db.query(POSCashMovement).count()==2
        assert db.query(POSAudit).filter_by(action='TEST_DATA_RETIRED').count()==0
    assert len(pos.pos.calls)==1


def test_failure_mid_apply_rolls_back_both_sales_and_cash(pos,monkeypatch):
    a,b=setup(pos,monkeypatch)
    with pos.sessions() as db:report=cleanup.plan(db,db.get(User,pos.uid),pos.pos)
    original=service.payments._audit;calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('synthetic failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(service.payments,'_audit',fail_second)
    with pos.sessions() as db:
        with pytest.raises(RuntimeError):cleanup.apply(db,db.get(User,pos.uid),pos.pos,report['plan_sha256'],cleanup.CONFIRM,'Owner confirms simulated tenders; rollback fixture')
        db.rollback()
    with pos.sessions() as db:
        assert [db.get(POSCheckout,i).status for i in (a,b)]==['REVIEW','QUEUED']
        assert db.query(POSCashMovement).count()==2 and db.query(POSAudit).filter_by(action='TEST_DATA_RETIRED').count()==0
        assert all(p.verification_status=='VERIFIED' for p in db.query(SalesPayment))


def test_normal_sale_after_retirement_completes_without_recreating_tests(pos,monkeypatch):
    a,b=setup(pos,monkeypatch)
    with pos.sessions() as db:
        report=cleanup.plan(db,db.get(User,pos.uid),pos.pos)
        cleanup.apply(db,db.get(User,pos.uid),pos.pos,report['plan_sha256'],cleanup.CONFIRM,'Both test tenders simulated; normal operation regression');db.commit()
    third=checkout(pos);assert service.dispatch_one(pos.sessions,pos.pos)
    with pos.sessions() as db:
        assert db.get(POSCheckout,third).status=='COMPLETE'
        assert all(db.get(POSCheckout,i).status=='CANCELLED' for i in (a,b))
        assert db.query(JournalEntry).count()==1 and db.query(SalesPayment).count()==3
    assert len(pos.pos.calls)==2 and not pos.remote.mutations


def test_existing_genuine_sale_and_accounting_untouched(pos,monkeypatch):
    genuine=checkout(pos);assert service.dispatch_one(pos.sessions,pos.pos)
    with pos.sessions() as db:
        r=db.get(POSCheckout,genuine);oid=r.sales_order_id
        order=db.get(SalesOrder,oid);payment=db.query(SalesPayment).filter_by(sales_order_id=oid).one()
        before=(order.order_status,order.payment_status,order.amount_paid,order.shopify_order_id,payment.verification_status,payment.accounting_entry_id)
        journal_id=payment.accounting_entry_id
        movement=db.query(POSCashMovement).filter_by(reference='poscash:'+genuine+':0').one()
        cash_before=(movement.id,movement.amount,movement.kind)
    setup(pos,monkeypatch)
    with pos.sessions() as db:
        report=cleanup.plan(db,db.get(User,pos.uid),pos.pos)
        cleanup.apply(db,db.get(User,pos.uid),pos.pos,report['plan_sha256'],cleanup.CONFIRM,'Only the two explicit test sales are approved for retirement');db.commit()
    with pos.sessions() as db:
        order=db.get(SalesOrder,oid);payment=db.query(SalesPayment).filter_by(sales_order_id=oid).one()
        assert before==(order.order_status,order.payment_status,order.amount_paid,order.shopify_order_id,payment.verification_status,payment.accounting_entry_id)
        assert db.get(POSCheckout,genuine).status=='COMPLETE'
        assert db.get(JournalEntry,journal_id) is not None and db.query(JournalEntry).count()==1
        movement=db.query(POSCashMovement).filter_by(reference='poscash:'+genuine+':0').one()
        assert cash_before==(movement.id,movement.amount,movement.kind)
        assert service.drawer(db,db.get(POSSession,pos.sid))==Decimal('18500')
    assert len(pos.pos.calls)==2 and not pos.remote.mutations


def test_cli_defaults_to_read_only_preview(pos,monkeypatch,capsys):
    import sys
    from scripts import retire_pos_test_sales as command
    setup(pos,monkeypatch)
    monkeypatch.setattr(command,'SessionLocal',pos.sessions)
    monkeypatch.setattr(command,'ReadOnlyClient',lambda:pos.pos)
    monkeypatch.setattr(sys,'argv',['retire_pos_test_sales','--admin-id',str(pos.uid)])
    def forbidden(*args,**kwargs):raise AssertionError('Default command must not apply')
    monkeypatch.setattr(cleanup,'apply',forbidden)
    assert command.main()==0
    import json
    assert json.loads(capsys.readouterr().out)['mode']=='READ_ONLY_DRY_RUN'
    with pos.sessions() as db:
        assert db.query(POSCashMovement).count()==2
        assert db.query(POSAudit).filter_by(action='TEST_DATA_RETIRED').count()==0
