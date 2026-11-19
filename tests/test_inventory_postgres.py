from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from datetime import datetime
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
import pytest
from test_retail_postgres import pg
from test_retail_phase1 import node, Reader
from test_inventory_phase3 import FakeInventory, payload, enable_mock_dispatch
from inventory_models import InventoryDocument, InventoryEvent
from retail_models import RetailVariant
from models import Supplier, User
from settings import settings
import retail_sync
import inventory_service as service


def setup(pg,monkeypatch):
    _,sessions,uid=pg;monkeypatch.setattr(settings,'INVENTORY_ENABLED',True)
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261119_44');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        job=retail_sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';rid=job.id;db.commit()
    retail_sync.run_sync(sessions,rid,Reader([([node(1,barcode='P1')],False)]))
    with sessions() as db:
        supplier=Supplier(name='PG inventory supplier');db.add(supplier);db.commit();supplier_id=supplier.id
        v=db.query(RetailVariant).one();product={'id':v.id,'version':v.version}
    return SimpleNamespace(sessions=sessions,uid=uid,supplier=supplier_id,products=[product],remote=FakeInventory())


def test_inventory_postgres_migration_and_immutable_history(pg,monkeypatch):
    r=setup(pg,monkeypatch)
    with r.sessions() as db:
        user=db.get(User,r.uid);doc=service.create(db,user,payload(r),r.remote);docid=doc.id
        service.submit(db,user,docid,r.remote);service.approve(db,user,docid,doc.reference)
    for sql in ["UPDATE inventory_documents SET payload='{}' WHERE id=:id", "UPDATE inventory_documents SET baseline='[]' WHERE id=:id", "DELETE FROM inventory_events WHERE document_id=:id"]:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql),{'id':docid});db.commit()


def test_inventory_postgres_concurrent_dispatch_at_most_once(pg,monkeypatch):
    r=setup(pg,monkeypatch);enable_mock_dispatch(monkeypatch)
    with r.sessions() as db:
        user=db.get(User,r.uid);doc=service.create(db,user,payload(r),r.remote);docid=doc.id
        service.submit(db,user,docid,r.remote);service.approve(db,user,docid,doc.reference)
    barrier=Barrier(2)
    def run(_):barrier.wait();return service.dispatch_one(r.sessions,r.remote)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sum(results)==1 and len(r.remote.mutations)==1
    with r.sessions() as db:
        doc=db.get(InventoryDocument,docid);assert doc.status=='VERIFIED' and doc.attempts==1
        assert db.query(InventoryEvent).filter_by(document_id=docid,action='DISPATCH_INTENT').count()==1


def test_postgres_duplicate_receipt_request_is_one_document(pg,monkeypatch):
    r=setup(pg,monkeypatch);request=payload(r);barrier=Barrier(2)
    def create(_):
        barrier.wait()
        with r.sessions() as db:return service.create(db,db.get(User,r.uid),request,r.remote).id
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(create,range(2)))
    assert len(set(ids))==1
    with r.sessions() as db:
        assert db.query(InventoryDocument).count()==1
        assert db.query(InventoryEvent).filter_by(action='CREATED').count()==1
