"""Real DB immutability and transaction rollback for test-only retirement."""
import pytest
from test_retail_postgres import pg
from test_pos_postgres import setup as pg_setup
from test_pos_test_cleanup import setup as prepare_pair
from models import User,SalesPayment,JournalEntry
from pos_models import POSCheckout,POSCashMovement,POSSession
import pos_service as service
import pos_test_cleanup as cleanup
from scripts.inspect_pos_sales_readonly import read_only_session


def test_postgres_test_retirement_preserves_fences_and_cash(pg,monkeypatch):
    r=pg_setup(pg,monkeypatch);a,b=prepare_pair(r,monkeypatch)
    with read_only_session(r.sessions) as db:report=cleanup.plan(db,db.get(User,r.uid),r.pos)
    with r.sessions() as db:
        cleanup.apply(db,db.get(User,r.uid),r.pos,report['plan_sha256'],cleanup.CONFIRM,'Synthetic approved local test correction; no remote cleanup');db.commit()
    with r.sessions() as db:
        assert all(db.get(POSCheckout,i).status=='CANCELLED' for i in (a,b))
        assert db.query(POSCashMovement).count()==4 and db.query(SalesPayment).count()==2
        assert db.query(JournalEntry).count()==0
        assert service.drawer(db,db.get(POSSession,r.sid))==10000
    assert service.dispatch_one(r.sessions,r.pos) is False and len(r.pos.calls)==1


def test_postgres_worker_cannot_dispatch_b_between_pair_cancellation(pg,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    r=pg_setup(pg,monkeypatch);a,b=prepare_pair(r,monkeypatch)
    with r.sessions() as db:report=cleanup.plan(db,db.get(User,r.uid),r.pos)
    entered=Event();release=Event();worker_started=Event();original=r.pos.read
    def held_read(oid):
        entered.set();assert release.wait(10)
        return original(oid)
    r.pos.read=held_read
    def retire():
        with r.sessions() as db:
            cleanup.apply(db,db.get(User,r.uid),r.pos,report['plan_sha256'],cleanup.CONFIRM,'Synthetic race test approved simulated tenders');db.commit()
    def worker():
        worker_started.set();return service.dispatch_one(r.sessions,r.pos)
    with ThreadPoolExecutor(2) as pool:
        applying=pool.submit(retire)
        try:
            assert entered.wait(10)
            dispatching=pool.submit(worker);assert worker_started.wait(10)
        finally:release.set()
        applying.result(timeout=15)
        assert dispatching.result(timeout=15) is False
    assert len(r.pos.calls)==1
    with r.sessions() as db:assert all(db.get(POSCheckout,i).status=='CANCELLED' for i in (a,b))
