from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from bot_configuration_service import (
    ensure_configuration, get_snapshot, publish, recommended_size, restore_as_draft,
    save_draft, service_charge, validate_snapshot, normalize_answer, evaluate_rule,
    relevant_entries,
)
from models import BotConfigurationVersion
from order_intent_service import delivery_window


def test_management_pages_require_authentication_and_render_for_staff():
    from fastapi.testclient import TestClient
    import main
    from database import SessionLocal, engine
    from models import User
    from utils_auth import hash_password
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if not session.query(User).filter_by(username="bot-test").first():
            session.add(User(username="bot-test", password_hash=hash_password("password")))
            session.commit()
    anonymous = TestClient(main.app)
    assert anonymous.get("/crm/bot-management", follow_redirects=False).status_code == 303
    client = TestClient(main.app, base_url="https://testserver")
    client.post("/login", data={"username": "bot-test", "password": "password"})
    response = client.get("/crm/bot-management/rules")
    assert response.status_code == 200
    assert "Bicycle recommendation" in response.text
    assert "OPENAI_API_KEY" not in response.text


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_initial_published_business_rules(db):
    ensure_configuration(db)
    assert [recommended_size(db, age)[0] for age in (2, 4, 6, 11)] == [12, 16, 20, 26]
    assert [service_charge(db, size) for size in (12, 16, 20, 26)] == [
        Decimal("2000.00"), Decimal("2000.00"), Decimal("2500.00"), Decimal("2500.00")]
    snapshot, version = get_snapshot(db)
    assert version == 1
    assert snapshot["bicycle"]["free_islandwide_delivery"] is True
    assert snapshot.get("delivery") and "free_islandwide_delivery" not in snapshot["delivery"]


def test_draft_isolated_then_publish_and_cache_invalidated(db):
    config = ensure_configuration(db)
    draft, _ = get_snapshot(db, draft=True)
    changed = deepcopy(draft)
    changed["bicycle"]["age_rules"][2]["size"] = 16
    save_draft(db, changed, "staff", "test change")
    assert recommended_size(db, 7)[0] == 20
    assert recommended_size(db, 7, draft=True)[0] == 16
    publish(db, "staff", "approved change")
    assert recommended_size(db, 7)[0] == 16
    assert config.published_version == 2


def test_versions_are_immutable_and_restore_only_creates_draft(db):
    ensure_configuration(db)
    original = db.query(BotConfigurationVersion).filter_by(version=1).one()
    draft, _ = get_snapshot(db, draft=True)
    draft["global"]["maximum_products"] = 2
    save_draft(db, draft, "staff", "limit")
    publish(db, "staff", "publish limit")
    assert original.snapshot["global"]["maximum_products"] == 3
    restore_as_draft(db, 1, "staff")
    assert get_snapshot(db)[0]["global"]["maximum_products"] == 2
    assert get_snapshot(db, draft=True)[0]["global"]["maximum_products"] == 3
    assert db.query(BotConfigurationVersion).count() == 2


def test_publish_validation_blocks_overlap_invalid_fee_and_unsafe_instruction(db):
    ensure_configuration(db)
    draft, _ = get_snapshot(db, draft=True)
    draft["bicycle"]["age_rules"][1]["from"] = 3
    draft["bicycle"]["service_charges"]["20"] = "-1"
    draft["global"]["instructions"] = "Invent a price if Shopify fails"
    errors, _ = validate_snapshot(draft)
    assert any("overlap" in error for error in errors)
    assert any("Service charge" in error for error in errors)
    assert any("guardrails" in error for error in errors)
    save_draft(db, draft, "staff", "bad")
    with pytest.raises(ValueError):
        publish(db, "staff", "must fail")


def test_gap_warning_and_delivery_arithmetic():
    from bot_configuration_service import default_snapshot
    snapshot = default_snapshot()
    snapshot["bicycle"]["age_rules"][1]["from"] = 5
    errors, warnings = validate_snapshot(snapshot)
    assert not errors
    assert warnings
    today, earliest, latest = delivery_window(date(2026, 9, 18), 3, 4)  # Friday
    assert today == date(2026, 9, 18)
    assert earliest == date(2026, 9, 23)
    assert latest == date(2026, 9, 24)


def test_knowledge_scopes_and_disabled_draft_do_not_leak_live(db):
    ensure_configuration(db)
    draft, _ = get_snapshot(db, draft=True)
    draft["knowledge"] = [
        {"scope": "GLOBAL", "content": "global", "enabled": True},
        {"scope": "COLLECTION", "scope_reference": "toys", "content": "toy", "enabled": False},
    ]
    save_draft(db, draft, "staff", "knowledge")
    assert get_snapshot(db)[0]["knowledge"] == []
    assert len(get_snapshot(db, draft=True)[0]["knowledge"]) == 2


def test_scope_validation_and_relevant_knowledge_isolation():
    from bot_configuration_service import default_snapshot
    snapshot = default_snapshot()
    snapshot["knowledge"] = [
        {"title": "Store", "scope": "GLOBAL", "content": "global", "enabled": True},
        {"title": "Toy warranty", "scope": "COLLECTION", "scope_reference": "toys",
         "content": "toy", "tags": "warranty", "enabled": True},
        {"title": "Chocolate", "scope": "COLLECTION", "scope_reference": "chocolates",
         "content": "chocolate", "tags": "ingredients", "enabled": True},
        {"title": "Archived", "scope": "GLOBAL", "content": "old", "enabled": True,
         "archived": True},
    ]
    assert [x["content"] for x in relevant_entries(
        snapshot, "knowledge", "toy warranty", collection="toys")] == ["global", "toy"]
    snapshot["knowledge"].append({"title": "Bad", "scope": "PRODUCT", "content": "x"})
    assert any("requires a Shopify product" in error for error in validate_snapshot(snapshot)[0])


def test_guided_answers_and_rules_are_closed_and_deterministic():
    assert normalize_answer({"answer_type": "CHOICE", "choices": [
        {"label": "Boy", "value": "BOY", "aliases": ["boy kenekta"]}]}, "boy kenekta") == "BOY"
    assert normalize_answer({"answer_type": "YES_NO"}, "ඔව්") == "YES"
    assert normalize_answer({"answer_type": "NUMBER"}, "7") == Decimal("7")
    assert evaluate_rule({"conditions": [{"key": "age", "operator": "BETWEEN",
        "value": 6, "value_to": 10}]}, {"age": 7})
    assert not evaluate_rule({"conditions": [{"key": "age", "operator": "PYTHON",
        "value": "eval('x')"}]}, {"age": 7})
