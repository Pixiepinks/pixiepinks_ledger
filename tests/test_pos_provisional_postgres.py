"""Concurrent provisional issuance must serialize to one original identity."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from test_retail_postgres import pg
from test_pos_postgres import setup
from test_pos import checkout
from test_pos_receipts_phase4g import text
from models import User,JournalEntry,SalesPayment
from pos_models import POSAudit,POSCheckout,POSOperation,POSCashMovement
import pos_provisional_receipts as provisional


def test_concurrent_provisional_issuance_is_one_original_and_one_reprint(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);barrier=Barrier(2)
    def issue(_):
        barrier.wait()
        with r.sessions() as db:return provisional.generate(db,db.get(User,r.uid),rid,True)
    with ThreadPoolExecutor(2) as pool:outputs=list(pool.map(issue,range(2)))
    assert sum('DUPLICATE / REPRINT' in text(raw) for raw in outputs)==1
    with r.sessions() as db:
        records=db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).all()
        assert len(records)==2 and {row.action for row in records}=={'PROVISIONAL_ISSUED','PROVISIONAL_REPRINTED'}
        assert len({row.detail['document_id'] for row in records})==1
        assert len({row.detail['first_issued_at'] for row in records})==1
        assert db.get(POSCheckout,rid).status=='QUEUED' and db.query(POSOperation).one().attempts==0
        assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==1 and db.query(POSCashMovement).count()==1
    assert not r.pos.calls and not r.remote.mutations
