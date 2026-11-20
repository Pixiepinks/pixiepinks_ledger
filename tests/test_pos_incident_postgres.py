from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from fastapi import HTTPException
import pytest
from test_retail_postgres import pg
from test_pos_postgres import setup
from test_pos import checkout
from models import User,JournalEntry,JournalLine,SalesPayment
from pos_models import POSOperation,POSCheckout,POSPosting
import pos_service as s
from scripts.inspect_pos_sales_readonly import run,read_only_session


def test_real_postgres_posting_constraint_rollback_and_single_reconciliation(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);original=s.publish_sale
    def broken(db,sale,order):
        original(db,sale,order)
        db.execute(text("INSERT INTO journal_lines (entry_id,account_id,debit,credit) VALUES (NULL,NULL,1,0)"))
    monkeypatch.setattr(s,'publish_sale',broken);s.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        op=db.query(POSOperation).one();ref=db.get(POSCheckout,rid).reference
        assert op.outcome=={'remote_id':'gid://shopify/Order/1'}
        assert s.operation_view(db,op)['diagnostic']=={'stage':'LOCAL_POSTING','failure_code':'DATABASE_ERROR'}
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==db.query(JournalLine).count()==0
        assert db.query(SalesPayment).one().accounting_entry_id is None
    report=run(r.sessions,r.pos,[ref]);assert report['sales'][0]['remote']['readback_status']=='VERIFIED_AGAINST_FROZEN_SALE'
    assert not report['sales'][0]['local']['existing_posting']
    with r.sessions() as db:
        with pytest.raises(HTTPException):s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Synthetic still-broken PostgreSQL posting',r.pos)
        assert db.query(POSPosting).count()==db.query(JournalEntry).count()==0
        assert db.get(POSCheckout,rid).status=='REVIEW'
        assert db.query(POSOperation).one().outcome=={'remote_id':'gid://shopify/Order/1'}
    monkeypatch.setattr(s,'publish_sale',original)
    with r.sessions() as db:
        s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Isolated PostgreSQL evidence',r.pos)
        assert db.query(POSPosting).count()==1 and db.query(SalesPayment).count()==1
        with pytest.raises(HTTPException):s.reconcile(db,db.get(User,r.uid),db.query(POSOperation).one().id,'Duplicate recovery blocked',r.pos)
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_postgres_diagnostic_transaction_forbids_writes(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    with pytest.raises(DatabaseError):
        with read_only_session(r.sessions) as db:db.execute(text("UPDATE pos_sessions SET status='CLOSED'"))
    with r.sessions() as db:assert db.execute(text('SHOW transaction_read_only')).scalar()=='off'
