"""Overview status reflects snapshot differences, without changing stored history."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from bot_configuration_service import (ensure_configuration, invalidate_cache, publish,
                                       restore_as_draft, save_draft)
from models import Base, BotAuditEvent, BotConfiguration, BotConfigurationVersion, User
from utils_auth import hash_password


@pytest.fixture
def overview(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'overview.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    invalidate_cache()
    with sessions() as db:
        ensure_configuration(db)
        db.add(User(username="overview-staff", password_hash=hash_password("password")))
        db.commit()
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username": "overview-staff", "password": "password"}).status_code == 200
    yield client, sessions
    invalidate_cache()
    engine.dispose()


def stored_state(sessions):
    with sessions() as db:
        return [
            [{column.name: deepcopy(getattr(row, column.name)) for column in model.__table__.columns}
             for row in db.query(model).order_by(model.id).all()]
            for model in (BotConfiguration, BotConfigurationVersion, BotAuditEvent)
        ]


def assert_overview(client, sessions, *, changed, saves, version):
    before = stored_state(sessions)
    for path in ("/crm/bot-management", "/crm/bot-management/overview"):
        response = client.get(path)
        assert response.status_code == 200
        status = "Unpublished changes" if changed else "No unpublished changes"
        assert f"<strong>{status}</strong>" in response.text
        assert f"{saves} total draft saves (includes published saves)" in response.text
        assert f"Version {version}" in response.text
    # GET requests must not reset counters, rewrite snapshots, or remove history.
    assert stored_state(sessions) == before


def test_initial_and_noop_saves_do_not_imply_unpublished_changes(overview):
    client, sessions = overview
    assert_overview(client, sessions, changed=False, saves=0, version=1)
    with sessions() as db:
        config = db.query(BotConfiguration).one()
        save_draft(db, config.published_snapshot, "staff", "No-op save")
    assert_overview(client, sessions, changed=False, saves=1, version=1)


def test_four_saves_and_two_publishes_show_version_three_with_no_pending_changes(overview):
    client, sessions = overview
    for version in (2, 3):
        for maximum in (1, version):
            with sessions() as db:
                snapshot = deepcopy(db.query(BotConfiguration).one().draft_snapshot)
                snapshot["global"]["maximum_products"] = maximum
                save_draft(db, snapshot, "staff", "Change product limit")
        assert_overview(client, sessions, changed=True, saves=(version - 1) * 2, version=version - 1)
        with sessions() as db:
            publish(db, "staff", "Publish approved limit")
        assert_overview(client, sessions, changed=False, saves=(version - 1) * 2, version=version)
    with sessions() as db:
        assert [row.version for row in db.query(BotConfigurationVersion).order_by(BotConfigurationVersion.version)] == [1, 2, 3]
        assert db.query(BotAuditEvent).filter_by(action="DRAFT_SAVED").count() == 4
        assert db.query(BotAuditEvent).filter_by(action="PUBLISHED").count() == 2


def test_restoring_history_and_reverting_draft_preserves_live_and_versions(overview):
    client, sessions = overview
    with sessions() as db:
        snapshot = deepcopy(db.query(BotConfiguration).one().draft_snapshot)
        snapshot["global"]["maximum_products"] = 1
        save_draft(db, snapshot, "staff", "Change product limit")
        publish(db, "staff", "Publish limit")
        versions_before = [(row.version, deepcopy(row.snapshot)) for row in db.query(BotConfigurationVersion).all()]
        live_before = deepcopy(db.query(BotConfiguration).one().published_snapshot)
        restore_as_draft(db, 1, "staff")
    assert_overview(client, sessions, changed=True, saves=2, version=2)
    with sessions() as db:
        config = db.query(BotConfiguration).one()
        assert config.published_snapshot == live_before
        assert [(row.version, row.snapshot) for row in db.query(BotConfigurationVersion).all()] == versions_before
        save_draft(db, config.published_snapshot, "staff", "Discard pending edits by saving live snapshot")
    assert_overview(client, sessions, changed=False, saves=3, version=2)
    with sessions() as db:
        restore_as_draft(db, 2, "staff")
    assert_overview(client, sessions, changed=False, saves=4, version=2)


def test_snapshot_key_order_is_not_a_pending_change(overview):
    client, sessions = overview
    with sessions() as db:
        snapshot = db.query(BotConfiguration).one().published_snapshot
        reordered = dict(reversed(list(snapshot.items())))
        save_draft(db, reordered, "staff", "Save identical keys in different order")
    assert_overview(client, sessions, changed=False, saves=1, version=1)
