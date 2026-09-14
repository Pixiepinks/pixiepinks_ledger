"""Deterministic helpers for the guided WhatsApp bicycle conversation."""

import re
from decimal import Decimal, InvalidOperation


# Inclusive lower bounds make the overlapping ranges unambiguous and easy to edit.
BICYCLE_SIZE_BY_MINIMUM_AGE = ((11, 26), (8, 24), (6, 20), (4, 16), (2, 12))

BOY_TERMS = ("boy", "boys", "son", "පුතා")
GIRL_TERMS = ("girl", "girls", "daughter", "දුව")
BICYCLE_TERMS = ("bicycle", "bicycles", "bike", "bikes", "බයිසික")


def recommended_bicycle_size(age: int) -> int:
    """Return a wheel-size starting point; age alone is not a fit guarantee."""
    if isinstance(age, bool) or not isinstance(age, int) or age < 2:
        raise ValueError("bicycle sizing requires an age of at least 2")
    for minimum_age, size in BICYCLE_SIZE_BY_MINIMUM_AGE:
        if age >= minimum_age:
            return size
    raise ValueError("bicycle sizing requires an age of at least 2")


def detect_bicycle_preference(text: str) -> str | None:
    normalized = text.casefold()
    for preference, terms in (("boy", BOY_TERMS), ("girl", GIRL_TERMS)):
        if any(term in normalized if any(ord(c) > 127 for c in term)
               else re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)
               for term in terms):
            return preference
    return None


def extract_child_age(text: str, *, standalone: bool = False) -> int | None:
    normalized = text.casefold().strip()
    if standalone:
        match = re.fullmatch(r"(?:age\s*)?(\d{1,2})(?:\s*(?:years?|yrs?)(?:\s*old)?)?", normalized)
    else:
        match = re.search(
            r"(?<!\d)(\d{1,2})\s*(?:[- ]?years?[- ]?old|yrs?\s*old|වයස|අවුරුදු)",
            normalized,
        )
    if not match:
        return None
    age = int(match.group(1))
    return age if 2 <= age <= 99 else None


def is_bicycle_request(text: str) -> bool:
    normalized = text.casefold()
    return any(term in normalized for term in BICYCLE_TERMS)


def asks_about_fit(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(phrase in normalized for phrase in (
        "definitely the right size", "will this fit", "will it fit", "correct size",
        "හරියට ගැළප", "ගැලපෙනවද",
    ))


def infer_guided_state(context: list[dict]) -> tuple[str | None, int | None, str | None]:
    """Reconstruct the active flow solely from persisted conversation history."""
    pending = None
    preference = None
    age = None
    for item in context:
        message = str(item.get("message_text", ""))
        if item.get("direction") == "outbound":
            lowered = message.casefold()
            if "boy or a girl" in lowered or "boy or girl" in lowered:
                pending = "gender"
                preference, age = None, None
            elif "how old is" in lowered:
                pending = "age"
            elif "usually a good starting point" in lowered:
                pending = "results"
                preference = detect_bicycle_preference(message) or preference
                age = extract_child_age(message) or age
        elif pending == "gender":
            detected = detect_bicycle_preference(message)
            if detected:
                preference, pending = detected, "age"
        elif pending == "age":
            detected_age = extract_child_age(message, standalone=True)
            if detected_age is not None:
                age, pending = detected_age, "results"
    return preference, age, pending


def _product_gender(product: dict) -> str | None:
    metadata = " ".join([
        str(product.get("title", "")), str(product.get("product_type", "")),
        str(product.get("vendor", "")), *(str(tag) for tag in product.get("tags", [])),
        *(str(variant.get("title", "")) for variant in product.get("variants", [])),
        *(str(value) for variant in product.get("variants", [])
          for value in variant.get("selected_options", {}).values()),
    ])
    return detect_bicycle_preference(metadata)


def available_bicycle_matches(
    products: list[dict], preference: str | None, limit: int = 3, *, cheapest: bool = False
) -> list[dict]:
    """Keep live available variants and rank explicit preference ahead of neutral data."""
    matches = []
    for product in products:
        available = [variant for variant in product.get("variants", []) if variant.get("available")]
        if available:
            copy = dict(product)
            copy["variants"] = available
            gender = _product_gender(copy)
            if preference and gender and gender != preference:
                continue
            rank = 0 if preference and gender == preference else 1
            try:
                lowest_price = min(Decimal(str(item["price"])) for item in available)
            except (InvalidOperation, KeyError):
                lowest_price = Decimal("Infinity")
            matches.append((rank, lowest_price, copy))
    matches.sort(key=lambda item: (item[1], item[0]) if cheapest else (item[0], item[1]))
    return [product for _, _, product in matches[:limit]]


def format_bicycle_results(age: int, preference: str, size: int, products: list[dict]) -> str:
    intro = (f"🚲 For a {age}-year-old {preference}, a {size}-inch bicycle is usually "
             "a good starting point.")
    if not products:
        return (intro + "\n\nI couldn't find a matching available bicycle in our live catalogue "
                "right now. Would you like to try another size or ask our team?")
    lines = [intro, "", "Here are some available options based on size/catalog information:", ""]
    for index, product in enumerate(products[:3], 1):
        variant = product["variants"][0]
        try:
            price = f"{Decimal(str(variant['price'])):,.2f}".rstrip("0").rstrip(".")
        except (InvalidOperation, KeyError):
            price = str(variant.get("price", ""))
        variant_title = str(variant.get("title") or "").strip()
        title = str(product.get("title") or "Bicycle")
        label = f"{title} – {variant_title}" if variant_title and variant_title != "Default Title" else title
        lines.extend([f"{index}. {label}", f"💰 Rs. {price}", "✅ Available",
                      f"🔗 {product.get('url') or ''}", ""])
    lines.append("Would you like more options or to filter by colour/budget?")
    return "\n".join(lines).strip()
