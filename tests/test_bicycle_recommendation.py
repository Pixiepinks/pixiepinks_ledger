import pytest

import main
from bicycle_recommendation import (
    available_bicycle_matches,
    detect_bicycle_preference,
    extract_child_age,
    recommended_bicycle_size,
)
from database import Base, SessionLocal, engine
from models import WhatsAppConversationMessage


def setup_function():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.query(WhatsAppConversationMessage).delete()
        db.commit()


def _reply(monkeypatch, text, searches=None, products=None):
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args) or True)
    monkeypatch.setattr(
        main, "search_products",
        lambda query: (searches.append(query) if searches is not None else None) or (products or []),
    )
    main._reply_to_text_message("94771111111", "current", text, "Customer")
    return sent[0][1]


@pytest.mark.parametrize("age,size", [(2, 12), (3, 12), (4, 16), (5, 16),
                                       (6, 20), (7, 20), (8, 24), (9, 24),
                                       (10, 24), (11, 26), (12, 26)])
def test_age_to_size_boundaries(age, size):
    assert recommended_bicycle_size(age) == size


def test_age_below_mapping_is_rejected():
    with pytest.raises(ValueError):
        recommended_bicycle_size(1)


@pytest.mark.parametrize("text,expected", [
    ("my son", "boy"), ("a boy", "boy"), ("මගේ පුතාට bike එකක්", "boy"),
    ("my daughter", "girl"), ("a girl", "girl"), ("දුවට බයිසිකලයක්", "girl"),
])
def test_preference_detection_english_sinhala_and_mixed(text, expected):
    assert detect_bicycle_preference(text) == expected


@pytest.mark.parametrize("text,gender,age", [
    ("I need a bicycle for my 6 year old daughter", "girl", 6),
    ("bicycle for a 7-year-old boy", "boy", 7),
])
def test_complete_details_are_extracted(text, gender, age):
    assert detect_bicycle_preference(text) == gender
    assert extract_child_age(text) == age


@pytest.mark.parametrize("customer_text,question", [
    ("I need a bicycle for my son", "How old is he?"),
    ("I need a bicycle for my daughter", "How old is she?"),
    ("මගේ පුතාට බයිසිකලයක් ඕන", "How old is he?"),
    ("දුවට bicycle එකක් ඕන", "How old is she?"),
])
def test_known_preference_skips_redundant_question(monkeypatch, customer_text, question):
    searches = []
    assert _reply(monkeypatch, customer_text, searches) == f"Great. {question}"
    assert searches == []


def test_pending_answers_use_persisted_history_then_query(monkeypatch):
    searches = []
    first = _reply(monkeypatch, "Bicycles", searches)
    assert "boy or a girl" in first
    main._store_conversation_message("94771111111", "inbound", "boy")
    second = _reply(monkeypatch, "boy", searches)
    assert second == "Great. How old is he?"
    main._store_conversation_message(
        "94771111111", "inbound", "7", whatsapp_message_id="current"
    )
    third = _reply(monkeypatch, "7", searches)
    assert "20-inch bicycle is usually a good starting point" in third
    assert searches == ["bicycle size 20"]


def test_complete_request_queries_and_preserves_live_values_with_max_three(monkeypatch):
    searches = []
    products = [{
        "title": f"Live Bike {number}", "product_type": "Bicycle", "tags": [],
        "url": f"https://shop/products/{number}",
        "variants": [{"title": "20 inch", "price": f"{37000 + number}.00",
                      "available": True, "selected_options": {"Size": "20 inch"}}],
    } for number in range(1, 5)]
    reply = _reply(monkeypatch, "I need a bicycle for my 7 year old boy", searches, products)
    assert searches == ["bicycle size 20"]
    assert "Live Bike 1" in reply and "Rs. 37,001" in reply
    assert "https://shop/products/1" in reply
    assert "Live Bike 3" in reply and "Live Bike 4" not in reply


@pytest.mark.parametrize("customer_text", ["Show me size 20 bicycles",
                                      "Show me girls size 16 bicycles"])
def test_direct_size_request_bypasses_guided_flow(monkeypatch, customer_text):
    searches = []
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: "catalog reply")
    assert _reply(monkeypatch, customer_text, searches) == "catalog reply"
    assert searches == [customer_text]


def test_available_matches_do_not_invent_gender_from_colour():
    products = [
        {"title": "Pink Comet", "tags": ["pink"], "variants": [{"available": True}]},
        {"title": "Boys Racer", "tags": [], "variants": [{"available": True}]},
    ]
    assert [p["title"] for p in available_bicycle_matches(products, "girl")] == ["Pink Comet"]


def test_fit_answer_is_cautious_and_does_not_query(monkeypatch):
    main._store_conversation_message(
        "94771111111", "outbound",
        "🚲 For a 7-year-old boy, a 20-inch bicycle is usually a good starting point.",
    )
    searches = []
    reply = _reply(monkeypatch, "Will this fit?", searches)
    assert "height and inseam" in reply and "cannot guarantee" in reply
    assert searches == []
