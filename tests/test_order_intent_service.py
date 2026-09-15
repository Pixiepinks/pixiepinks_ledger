from datetime import date
from decimal import Decimal

import pytest

from order_intent_service import (
    add_working_days, calculate_total, delivery_window, extract_sri_lankan_phones,
    get_bicycle_initial_service_charge, is_cancellation, normalize_sri_lankan_phone,
    service_decision,
)


@pytest.mark.parametrize("size,charge", [(12, "2000"), (16, "2000"),
                                          (20, "2500"), (26, "2500")])
def test_service_charge_mapping(size, charge):
    assert get_bicycle_initial_service_charge(size) == Decimal(charge)


def test_unknown_service_size_is_not_guessed():
    assert get_bicycle_initial_service_charge(24) is None


def test_decimal_total_and_free_delivery():
    assert calculate_total("16400.10", "2000", "0") == Decimal("18400.10")


@pytest.mark.parametrize("text,answer", [
    ("yes please", True), ("service eka ona", True), ("service එක ඕන", True),
    ("no thanks", False), ("service epa", False), ("service එපා", False),
])
def test_multilingual_service_decision(text, answer):
    assert service_decision(text) is answer


@pytest.mark.parametrize("raw", ["0712345678", "+94712345678", "94712345678"])
def test_phone_forms(raw):
    assert normalize_sri_lankan_phone(raw) == "+94712345678"


def test_two_distinct_phones_and_malformed_rejected():
    assert extract_sri_lankan_phones("0712345678 and 0777654321") == [
        "+94712345678", "+94777654321"]
    assert normalize_sri_lankan_phone("12345") is None
    assert extract_sri_lankan_phones("0712345678, +94712345678") == ["+94712345678"]


def test_weekend_is_skipped_for_delivery_window():
    friday = date(2026, 9, 18)
    assert add_working_days(friday, 3) == date(2026, 9, 23)
    assert delivery_window(friday) == (friday, date(2026, 9, 23), date(2026, 9, 24))


@pytest.mark.parametrize("text", ["cancel", "cancel order", "order eka epa", "ඇණවුම එපා"])
def test_cancellation_terms(text):
    assert is_cancellation(text)
