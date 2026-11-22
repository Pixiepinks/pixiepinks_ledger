"""Disposable PostgreSQL required-finance verification and no-resend posting."""
import pytest
from test_retail_postgres import pg
from test_pos_postgres import setup
from test_pos import checkout
from models import User,JournalEntry,SalesPayment
from pos_models import POSCheckout,POSOperation,POSPosting
from scripts.inspect_pos_sales_readonly import run
import pos_service as s


def test_walkin_financial_read_without_protected_customer_keeps_queue_until_approved_recovery(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);r.pos.fail_read=True;s.dispatch_one(r.sessions,r.pos);r.pos.fail_read=False
    original=r.pos.read
    def financial(oid):
        order=original(oid);order.pop('customer',None);return order
    def denied(oid):raise AssertionError('Optional protected field must never be queried')
    monkeypatch.setattr(r.pos,'read',financial);monkeypatch.setattr(r.pos,'read_customer',denied,raising=False)
    with r.sessions() as db:ref=db.get(POSCheckout,rid).reference
    for _ in range(3):checkout(r)
    assert not s.dispatch_one(r.sessions,r.pos)
    report=run(r.sessions,r.pos,[ref])
    assert report['sales'][0]['remote']['readback_status']=='VERIFIED_AGAINST_FROZEN_SALE'
    assert report['sales'][0]['remote']['customer_association_required'] is False
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='REVIEW' and db.query(POSPosting).count()==0
        assert db.query(POSOperation).filter_by(status='QUEUED',attempts=0).count()==3
        s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).filter_by(checkout_id=rid).one().id,'Separate approved mocked recovery',r.pos)
        assert db.get(POSCheckout,rid).status=='COMPLETE' and db.query(POSPosting).count()==db.query(JournalEntry).count()==1
        # Reconciliation itself never creates the next three Shopify orders.
        assert db.query(POSOperation).filter_by(status='QUEUED',attempts=0).count()==3
    assert len(r.pos.calls)==1
