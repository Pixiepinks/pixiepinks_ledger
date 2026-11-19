from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from fastapi import HTTPException
from test_retail_postgres import pg
from test_pos_postgres import setup as pos_setup
from test_pos import checkout
from models import User
from pos_models import POSSession,POSCheckout
from hardware_models import PrintDevice,HardwareJob,HardwareEvent
from settings import settings
import hardware_service as h
from test_hardware import ready


def setup(pg,monkeypatch):
    r=pos_setup(pg,monkeypatch)
    for key in ['POS_LOCAL_PRINT_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED','RETAIL_HARDWARE_LABEL_PRINT_ENABLED']:monkeypatch.setattr(settings,key,True)
    with r.sessions() as db:
        u=db.get(User,r.uid);d,code=h.enroll(db,u,db.get(POSSession,r.sid).register_id);r.did=d.id;r.token=h.pair(db,code);h.manage(db,u,d.id,'activate');h.configure(db,u,d.id,{**h.DEFAULT,'auto_receipts':True,'drawer_queue':h.DRAWER_QUEUE,'drawer_verified':True})
    ready(r);return r


def test_competing_workers_and_device_claims_only_deliver_once(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos)
    barrier=Barrier(2)
    def tick(_):barrier.wait();h.tick(r.sessions)
    with ThreadPoolExecutor(2) as pool:list(pool.map(tick,range(2)))
    with r.sessions() as db:assert db.query(HardwareJob).count()==2
    barrier=Barrier(2)
    def claim(_):
        barrier.wait()
        with r.sessions() as db:return h.claim(db,h.authenticate(db,'Bearer '+r.token))
    with ThreadPoolExecutor(2) as pool:jobs=list(pool.map(claim,range(2)))
    assert sum(j is not None for j in jobs)==1
    with r.sessions() as db:
        assert db.get(POSCheckout,rid).status=='COMPLETE'
        assert db.query(HardwareJob).filter_by(status='DELIVERED_TO_AGENT',attempts=1).count()==1
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_postgres_hardware_intent_and_audit_are_immutable(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions)
    for sql in ["UPDATE hardware_jobs SET queue='wrong'",'UPDATE hardware_jobs SET attempts=2','DELETE FROM hardware_events','DELETE FROM hardware_jobs']:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql));db.commit()


def test_revocation_blocks_claim_and_marks_possibly_delivered_uncertain(pg,monkeypatch):
    r=setup(pg,monkeypatch);rid=checkout(r);h.pos.dispatch_one(r.sessions,r.pos);h.tick(r.sessions)
    with r.sessions() as db:
        j=h.claim(db,h.authenticate(db,'Bearer '+r.token));assert j
        h.manage(db,db.get(User,r.uid),r.did,'revoke')
    with r.sessions() as db:
        assert db.get(HardwareJob,j['id']).status=='UNKNOWN'
        with pytest.raises(HTTPException):h.authenticate(db,'Bearer '+r.token)
        assert db.get(POSCheckout,rid).status=='COMPLETE'
