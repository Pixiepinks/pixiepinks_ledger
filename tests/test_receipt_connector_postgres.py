"""Disposable PostgreSQL encrypted replay fence and additive migration coverage."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from fastapi import HTTPException
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from test_retail_postgres import pg
from hardware_models import PrintConnectorNonce
from print_connector_service import consume_nonce
from windows_print_agent.envelope import Context


def test_replay_fence_is_durable_across_competing_workers(pg):
    engine,sessions,_=pg
    ctx=Context(str(uuid4()),b'a'*32,b'b'*32)
    barrier=Barrier(2)
    def consume(_):
        with sessions() as db:
            barrier.wait()
            try:consume_nonce(db,ctx);return 200
            except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(consume,range(2)))==[200,409]
    with sessions() as db:assert db.query(PrintConnectorNonce).count()==1
    # The fence remains committed even if subsequent application work rolls back.
    with sessions() as db:
        db.rollback()
        with pytest.raises(HTTPException) as error:consume_nonce(db,ctx)
        assert error.value.status_code==409


def test_additive_nonce_migration_preserves_existing_data(pg,monkeypatch):
    engine,sessions,uid=pg
    from models import User
    # File load avoids treating Alembic's installed package as a local namespace.
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('connector_migration',Path(__file__).resolve().parents[1]/'alembic/versions/20261121_49_print_connector.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    PrintConnectorNonce.__table__.drop(engine)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        migration.upgrade();migration.upgrade();migration.downgrade()
        assert 'print_connector_nonces' in inspect(conn).get_table_names()
    with sessions() as db:assert db.get(User,uid).username=='retail-pg-admin'
