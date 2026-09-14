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
    monkeypatch.setattr(
        main, "search_bicycles_by_collection",
        lambda gender, size, query="", limit=5: (
            searches.append((gender, size, query)) if searches is not None else None
        ) or {"collection": {"title": f"{gender} {size}"}, "products": products or [],
              "reason": "ok"},
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


def test_exact_girls_bicycle_pending_age_retains_gender_and_uses_size_16(monkeypatch):
    searches = []
    first = _reply(monkeypatch, "do you have girls bicycles", searches)
    assert first == "Great. How old is she?"
    main._store_conversation_message(
        "94771111111", "inbound", "5 years", whatsapp_message_id="current"
    )
    second = _reply(monkeypatch, "5 years", searches)
    assert "5-year-old girl" in second
    assert "16-inch" in second
    assert "boy or a girl" not in second
    assert searches == [("girl", 16, "")]


def test_pending_boy_age_answer_retains_gender_and_uses_size_20(monkeypatch):
    searches = []
    assert _reply(monkeypatch, "do you have boys bicycles", searches) == "Great. How old is he?"
    main._store_conversation_message(
        "94771111111", "inbound", "7 years", whatsapp_message_id="current"
    )
    reply = _reply(monkeypatch, "7 years", searches)
    assert "boy or a girl" not in reply
    assert searches == [("boy", 20, "")]


def test_pending_age_accepts_sinhala_prefix():
    assert extract_child_age("අවුරුදු 5", standalone=True) == 5


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
    assert searches == [("boy", 20, "")]


def test_complete_request_queries_and_preserves_live_values_with_max_three(monkeypatch):
    searches = []
    sent = []
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: sent.append(args) or True)
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *args: False)
    products = [{
        "title": f"Live Bike {number}", "product_type": "Bicycle", "tags": [],
        "url": f"https://shop/products/{number}",
        "variants": [{"title": "20 inch", "price": f"{37000 + number}.00",
                      "available": True, "selected_options": {"Size": "20 inch"}}],
    } for number in range(1, 5)]
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda gender, size, query="", limit=5:
                        searches.append((gender, size, query)) or
                        {"collection": {"title": 'Boys Size 20"'}, "products": products})
    main._reply_to_text_message("94771111111", "current", "I need a bicycle for my 7 year old boy", "Customer")
    assert searches == [("boy", 20, "")]
    combined = "\n".join(body for _, body in sent)
    assert "Live Bike 1" in combined and "Rs. 37,001" in combined
    assert "https://shop/products/1" in combined
    assert "Live Bike 3" in combined and "Live Bike 4" not in combined


def test_production_style_twelve_year_old_uses_26_inch_search(monkeypatch):
    searches = []
    assert "boy or a girl" in _reply(monkeypatch, "bicycle", searches)
    main._store_conversation_message("94771111111", "inbound", "boy")
    assert "How old is he" in _reply(monkeypatch, "boy", searches)
    main._store_conversation_message("94771111111", "inbound", "12 years",
                                     whatsapp_message_id="current")
    reply = _reply(monkeypatch, "12 years", searches)
    assert "26-inch" in reply
    assert searches == [("boy", 26, "")]


def _completed_boy_26_history():
    main._store_conversation_message("94771111111", "inbound", "boy")
    main._store_conversation_message("94771111111", "inbound", "12 years")
    main._store_conversation_message(
        "94771111111", "outbound",
        "For a 12-year-old boy, a 26-inch bicycle is usually a good starting point. 🚲",
    )


def test_current_complete_request_replaces_stale_boy_26_state(monkeypatch):
    _completed_boy_26_history()
    searches = []
    reply = _reply(monkeypatch, "I want bicycle for 6 years girl", searches)
    assert "6-year-old girl" in reply and "20-inch" in reply
    assert searches == [("girl", 20, "")]
    assert all(search[:2] != ("girl", 26) for search in searches)


@pytest.mark.parametrize("text,expected", [
    ("5 year old girl", ("girl", 16, "")),
    ("I need a bicycle for my 7 year old son", ("boy", 20, "")),
    ("show me boys size 20 bicycles", ("boy", 20, "show me boys size 20 bicycles")),
])
def test_current_demographics_override_completed_history(monkeypatch, text, expected):
    _completed_boy_26_history()
    searches = []
    _reply(monkeypatch, text, searches)
    assert searches == [expected]


def test_explicit_size_wins_over_age_derived_size(monkeypatch):
    searches = []
    _reply(monkeypatch, "I need a size 20 bicycle for my 12 year old boy", searches)
    assert searches == [("boy", 20, "I need a size 20 bicycle for my 12 year old boy")]


def test_completed_result_then_generic_bicycle_request_starts_fresh(monkeypatch):
    _completed_boy_26_history()
    searches = []
    reply = _reply(monkeypatch, "Do you have bicycles?", searches)
    assert reply == "Yes, we do 🚲 Is the bicycle for a boy or a girl?"
    assert searches == []


def test_completed_result_then_greeting_ignores_stale_bicycle_state(monkeypatch):
    _completed_boy_26_history()
    searches = []
    contexts = []
    monkeypatch.setattr(
        main, "generate_customer_reply",
        lambda _text, _name, context, *args: contexts.append(context) or
        "Hi! How can I help you today?",
    )
    assert _reply(monkeypatch, "hi", searches) == "Hi! How can I help you today?"
    assert searches == []
    assert contexts == [[]]


@pytest.mark.parametrize("text,term", [
    ("do you have chocolates?", "chocolate"),
    ("show me school bags", "school"),
])
def test_completed_result_then_new_category_uses_normal_catalog(monkeypatch, text, term):
    _completed_boy_26_history()
    searches = []
    contexts = []
    monkeypatch.setattr(
        main, "generate_customer_reply",
        lambda _text, _name, context, _products: contexts.append(context) or "catalog reply",
    )
    assert _reply(monkeypatch, text, searches) == "catalog reply"
    assert searches == [text]
    assert term in searches[0].casefold()
    assert contexts == [[]]


def test_rejected_old_size_begins_a_clean_bicycle_request(monkeypatch):
    _completed_boy_26_history()
    searches = []
    reply = _reply(monkeypatch, "no need 26 i need new bicycles", searches)
    assert reply == "Yes, we do 🚲 Is the bicycle for a boy or a girl?"
    assert searches == []


def test_explicit_new_size_discards_completed_size(monkeypatch):
    _completed_boy_26_history()
    searches = []
    _reply(monkeypatch, "boys size 20 bicycles", searches)
    assert searches == [("boy", 20, "boys size 20 bicycles")]


def test_handover_still_precedes_completed_bicycle_context(monkeypatch):
    _completed_boy_26_history()
    searches = []
    assert _reply(monkeypatch, "I want to speak to a person about bicycles", searches) == (
        main.HUMAN_HANDOVER_REPLY
    )
    assert searches == []


@pytest.mark.parametrize("text", ["show more", "cheaper ones", "any Lumala?"])
def test_result_followups_retain_completed_collection(monkeypatch, text):
    _completed_boy_26_history()
    searches = []
    _reply(monkeypatch, text, searches)
    assert searches == [("boy", 26, text)]


def test_new_bicycle_gender_does_not_reuse_completed_age(monkeypatch):
    _completed_boy_26_history()
    searches = []
    assert _reply(monkeypatch, "I want bicycle for girl", searches) == "Great. How old is she?"
    assert searches == []


def test_in_stock_products_are_preferred_and_out_of_stock_is_fallback():
    unavailable = {"title": "No Stock", "variants": [{"price": "1", "available": False}]}
    available = {"title": "In Stock", "variants": [{"price": "2", "available": True}]}
    assert [item["title"] for item in available_bicycle_matches(
        [unavailable, available], None
    )] == ["In Stock"]
    assert available_bicycle_matches([unavailable], None)[0]["title"] == "No Stock"


def test_direct_size_request_without_gender_uses_general_catalog(monkeypatch):
    searches = []
    monkeypatch.setattr(main, "generate_customer_reply", lambda *args: "catalog reply")
    assert _reply(monkeypatch, "Show me size 20 bicycles", searches) == "catalog reply"
    assert searches == ["Show me size 20 bicycles"]


def test_direct_gender_and_size_bypasses_age_question(monkeypatch):
    searches = []
    reply = _reply(monkeypatch, "Show me girls size 16 bicycles", searches)
    assert "How old" not in reply
    assert searches == [("girl", 16, "Show me girls size 16 bicycles")]


def test_more_after_direct_size_reuses_the_same_verified_collection(monkeypatch):
    searches = []
    _reply(monkeypatch, "show size 20 boys bicycles", searches)
    main._store_conversation_message("94771111111", "inbound", "more")
    _reply(monkeypatch, "more", searches)
    assert searches == [
        ("boy", 20, "show size 20 boys bicycles"),
        ("boy", 20, "more"),
    ]


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


def test_boy_regression_only_collection_products_reach_image_sender(monkeypatch):
    images = []
    product = {"title": "Boys Racer", "featured_image": "https://cdn.shopify.com/boy.jpg",
               "featured_image_verified": True, "url": "https://shop/boy",
               "variants": [{"title": "Default Title", "price": "49500", "available": True}]}
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda *args, **kwargs:
                        {"collection": {"title": 'Boys Size 26"'}, "products": [product]})
    monkeypatch.setattr(main, "send_whatsapp_text", lambda *args: True)
    monkeypatch.setattr(main, "send_whatsapp_image",
                        lambda _, url, caption: images.append((url, caption)) or True)
    main._reply_to_text_message("94772222222", "regression", "bike for my 12 year old boy", None)
    assert len(images) == 1 and images[0][0] == "https://cdn.shopify.com/boy.jpg"
    assert "Boys Racer" in images[0][1] and "Rs. 49,500" in images[0][1]


def test_unavailable_collection_does_not_send_an_image(monkeypatch):
    sent, images = [], []
    product = {"title": "No Stock", "featured_image": "https://cdn.shopify.com/no.jpg",
               "featured_image_verified": True,
               "variants": [{"price": "1", "available": False}]}
    monkeypatch.setattr(main, "search_bicycles_by_collection", lambda *args, **kwargs:
                        {"collection": {"title": 'Girls Size 26"'}, "products": [product]})
    monkeypatch.setattr(main, "send_whatsapp_text", lambda _, body: sent.append(body) or True)
    monkeypatch.setattr(main, "send_whatsapp_image", lambda *args: images.append(args) or True)
    main._reply_to_text_message("94773333333", "nostock", "girls size 26 bicycles", None)
    assert images == []
    assert "currently shown as unavailable" in sent[0]


def test_missing_24_collection_is_explicit_and_does_not_query(monkeypatch):
    monkeypatch.setattr(main, "search_bicycles_by_collection",
                        lambda *args: (_ for _ in ()).throw(AssertionError()))
    reply = _reply(monkeypatch, "bicycle for my 9 year old boy")
    assert "do not have a verified boy 24-inch collection" in reply
    assert "won't substitute" in reply
