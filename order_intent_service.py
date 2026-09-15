"""Deterministic WhatsApp sales-intent, money, contact, and delivery rules."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from zoneinfo import ZoneInfo


class OrderIntentStatus(str, Enum):
    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    AWAITING_BICYCLE_GENDER = "AWAITING_BICYCLE_GENDER"
    AWAITING_BICYCLE_AGE = "AWAITING_BICYCLE_AGE"
    SHOWING_PRODUCTS = "SHOWING_PRODUCTS"
    AWAITING_VARIANT = "AWAITING_VARIANT"
    AWAITING_SERVICE_DECISION = "AWAITING_SERVICE_DECISION"
    COLLECTING_DELIVERY_DETAILS = "COLLECTING_DELIVERY_DETAILS"
    READY_FOR_PAYMENT_HANDOVER = "READY_FOR_PAYMENT_HANDOVER"
    HANDED_TO_TEAM = "HANDED_TO_TEAM"
    CANCELLED = "CANCELLED"


SERVICE_CHARGES = {12: Decimal("2000.00"), 16: Decimal("2000.00"),
                   20: Decimal("2500.00"), 26: Decimal("2500.00")}


def get_bicycle_initial_service_charge(size: int | str | None) -> Decimal | None:
    """Return the fixed charge, or None rather than guessing an unknown size."""
    try:
        return SERVICE_CHARGES.get(int(str(size).replace('"', "").strip()))
    except (TypeError, ValueError):
        return None


def calculate_total(product_price, service_charge=0, delivery_charge=0) -> Decimal:
    try:
        values = [Decimal(str(value)) for value in
                  (product_price, service_charge, delivery_charge)]
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid monetary amount") from exc
    if any(value < 0 for value in values):
        raise ValueError("monetary amounts cannot be negative")
    return sum(values, Decimal("0.00")).quantize(Decimal("0.01"))


YES_TERMS = ("yes", "yes please", "do it", "add service", "with service",
             "grease it", "service it", "service eka ona", "service එක ඕන", "ඔව්")
NO_TERMS = ("no", "no thanks", "without service", "don't need service",
            "do not need service", "service epa", "service එපා", "එපා")
CANCEL_TERMS = ("cancel", "cancel order", "don't order", "do not order",
                "i don't want it", "stop", "order eka epa", "ඇණවුම එපා")


def _phrase(text: str, terms: tuple[str, ...]) -> bool:
    value = " ".join(text.casefold().strip().split())
    return any(term in value if any(ord(c) > 127 for c in term)
               else re.search(rf"(?<!\w){re.escape(term)}(?!\w)", value) is not None
               for term in terms)


def service_decision(text: str) -> bool | None:
    if _phrase(text, NO_TERMS):
        return False
    if _phrase(text, YES_TERMS):
        return True
    return None


def is_cancellation(text: str) -> bool:
    return _phrase(text, CANCEL_TERMS)


def normalize_sri_lankan_phone(value: str) -> str | None:
    compact = re.sub(r"[\s()-]", "", value)
    if re.fullmatch(r"07\d{8}", compact):
        return "+94" + compact[1:]
    if re.fullmatch(r"(?:\+94|94)7\d{8}", compact):
        return "+" + compact.lstrip("+")
    return None


def extract_sri_lankan_phones(text: str) -> list[str]:
    found = []
    for raw in re.findall(r"(?<!\d)(?:\+?94[\s()-]*7|07)[\d\s()-]{8,14}(?!\d)", text):
        phone = normalize_sri_lankan_phone(raw)
        if phone and phone not in found:
            found.append(phone)
    return found


def add_working_days(start: date, count: int) -> date:
    if count < 0:
        raise ValueError("working-day count cannot be negative")
    result = start
    added = 0
    while added < count:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


def delivery_window(start: date | None = None) -> tuple[date, date, date]:
    payment_date = start or datetime.now(ZoneInfo("Asia/Colombo")).date()
    return payment_date, add_working_days(payment_date, 3), add_working_days(payment_date, 4)
