"""Published language reaches live AI without exposing drafts or enabling learning."""
import copy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import ai_service
import database
from bot_configuration_service import default_snapshot
from models import BotConfiguration


@pytest.fixture
def preferences_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'preferences.db'}")
    BotConfiguration.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    published = default_snapshot()
    published["global"]["instructions"] = "Always reply in Sinhala, even for English questions."
    draft = copy.deepcopy(published)
    draft["global"]["instructions"] = "UNPUBLISHED DRAFT UNIQUE"
    with sessions() as db:
        db.add(BotConfiguration(published_version=1, draft_revision=1,
                               published_snapshot=published, draft_snapshot=draft))
        db.commit()
    calls = []
    monkeypatch.setattr(ai_service.settings, "OPENAI_API_KEY", "fake")
    monkeypatch.setattr(ai_service.settings, "LEARNING_RETRIEVAL_ENABLED", False)
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text="ආයුබෝවන්", usage=None)
    monkeypatch.setattr(ai_service, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    yield sessions, calls
    engine.dispose()


def test_live_and_live_preview_share_published_language_without_draft_leak(preferences_db):
    sessions, calls = preferences_db
    ai_service.generate_customer_reply("Do you have school bags?")
    prefs = ai_service.published_response_preferences()
    ai_service.generate_customer_reply("Do you have school bags?",
        test_context=ai_service.ReplyTestContext(business_preferences=prefs))
    assert calls[0]["instructions"] == calls[1]["instructions"]
    assert "Always reply in Sinhala" in calls[0]["instructions"]
    assert "UNPUBLISHED DRAFT UNIQUE" not in calls[0]["instructions"]
    with sessions() as db:
        config = db.query(BotConfiguration).one()
        snapshot = copy.deepcopy(config.published_snapshot)
        snapshot["global"]["instructions"] = "PUBLISHED UPDATED LANGUAGE"
        config.published_snapshot = snapshot
        db.commit()
    ai_service.generate_customer_reply("Hello")
    assert "PUBLISHED UPDATED LANGUAGE" in calls[2]["instructions"]


def test_draft_preview_never_reads_live_preferences(preferences_db, monkeypatch):
    _, calls = preferences_db
    monkeypatch.setattr(ai_service, "published_response_preferences", lambda: pytest.fail("Preview read live settings"))
    ai_service.generate_customer_reply("Hello", test_context=ai_service.ReplyTestContext(
        business_preferences={"instructions": "PREVIEW ONLY"}))
    assert "PREVIEW ONLY" in calls[0]["instructions"]


def test_configuration_failure_keeps_ai_available(preferences_db, monkeypatch):
    _, calls = preferences_db
    def fail():
        raise RuntimeError("private database error")
    monkeypatch.setattr(ai_service, "published_response_preferences", fail)
    assert ai_service.generate_customer_reply("Hello") == "ආයුබෝවන්"
    assert "private database error" not in calls[0]["instructions"]
