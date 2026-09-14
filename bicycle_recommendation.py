"""Deterministic helpers for the guided WhatsApp bicycle conversation."""

import re
from decimal import Decimal, InvalidOperation


# Inclusive lower bounds make the overlapping ranges unambiguous and easy to edit.
BICYCLE_SIZE_BY_MINIMUM_AGE = ((11, 26), (8, 24), (6, 20), (4, 16), (2, 12))

BOY_TERMS = ("boy", "boys", "son", "පුතා")
GIRL_TERMS = ("girl", "girls", "daughter", "දුව")
BICYCLE_TERMS = ("bicycle", "bicycles", "bike", "bikes", "බයිසික")
RESULT_FOLLOW_UP_TERMS = (
    "show more", "another one", "another", "more options", "cheaper", "under ",
    "below ", "any ", "colour", "color", "red", "blue", "pink", "black",
    "white", "lumala", "first one", "second one", "third one",
    "show boys ones", "show girls ones",
)

NEW_BICYCLE_REQUEST_TERMS = (
    "i need", "i want", "need another", "new bicycle", "new bike",
    "no need", "do you have", "show me",
)


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
        match = re.fullmatch(
            r"(?:(?:age|වයස|අවුරුදු)\s*)?(\d{1,2})"
            r"(?:\s*(?:years?|yrs?)(?:\s*old)?|\s*(?:වයස|අවුරුදු))?",
            normalized,
        )
    else:
        match = re.search(
            r"(?<!\d)(\d{1,2})\s*(?:[- ]?years?(?:[- ]?old)?|yrs?(?:\s*old)?|වයස|අවුරුදු)",
            normalized,
        )
    if not match:
        return None
    age = int(match.group(1))
    return age if 2 <= age <= 99 else None


def is_bicycle_request(text: str) -> bool:
    normalized = text.casefold()
    return any(term in normalized for term in BICYCLE_TERMS)


def is_generic_bicycle_request(text: str) -> bool:
    """Identify a fresh, detail-free bicycle enquiry rather than a result follow-up."""
    normalized = " ".join(text.casefold().strip().rstrip("?!. ").split())
    return normalized in {
        "bicycle", "bicycles", "bike", "bikes", "do you have bicycles",
        "do you have a bicycle", "do you have bikes", "i need a bicycle",
        "i need bicycle", "show me bicycles", "show me bikes",
    }


def is_bicycle_results_follow_up(text: str) -> bool:
    """Return true only for language that naturally modifies prior results."""
    normalized = " ".join(text.casefold().split())
    return normalized.strip("?!. ") == "more" or any(
        term in normalized for term in RESULT_FOLLOW_UP_TERMS
    )


def is_new_bicycle_request(text: str) -> bool:
    """Identify an explicit request whose filters must come only from this message."""
    if not is_bicycle_request(text):
        return False
    normalized = " ".join(text.casefold().split())
    return (
        is_generic_bicycle_request(text)
        or any(term in normalized for term in NEW_BICYCLE_REQUEST_TERMS)
        or detect_bicycle_preference(text) is not None
        or re.search(r"\bsize\s*(?:12|16|20|24|26)\b", normalized) is not None
        or extract_child_age(text) is not None
    )


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
            elif "how old is" in lowered or "child's age" in lowered:
                pending = "age"
                # The immediately pending question is authoritative.  Preserve
                # the demographic encoded by the assistant's pronoun even when
                # the original customer turn occurred before this history scan.
                if re.search(r"\b(?:she|her)\b", lowered):
                    preference = "girl"
                elif re.search(r"\b(?:he|him)\b", lowered):
                    preference = "boy"
            elif "usually a good starting point" in lowered:
                pending = "results"
                preference = detect_bicycle_preference(message) or preference
                age = extract_child_age(message) or age
            elif re.search(r"\b(?:boys|girls) size (?:12|16|20|24|26)\b", lowered):
                pending = "results"
                preference = detect_bicycle_preference(message) or preference
            elif pending == "results" and not (
                "🚲" in message or "bicycle" in lowered
                or "more options" in lowered or "filter by" in lowered
            ):
                preference, age, pending = None, None, None
        elif pending == "gender":
            detected = detect_bicycle_preference(message)
            if detected:
                preference, pending = detected, "age"
            else:
                preference, age, pending = None, None, None
        elif pending == "age":
            detected_age = extract_child_age(message, standalone=True)
            if detected_age is not None:
                age, pending = detected_age, "results"
            else:
                preference, age, pending = None, None, None
        elif pending == "results" and not (
            is_bicycle_request(message)
            or is_bicycle_results_follow_up(message)
            or asks_about_fit(message)
        ):
            # A category switch or ordinary chat ends the active result turn.
            preference, age, pending = None, None, None
        elif pending is None and is_bicycle_request(message):
            # Capture details from the request which caused the next assistant
            # question. This supports gender-neutral wording such as
            # "What is the child's age?" as well as he/she prompts.
            preference = detect_bicycle_preference(message)
            age = extract_child_age(message)
    return preference, age, pending


def infer_bicycle_result_size(context: list[dict]) -> int | None:
    """Recover an explicit wheel size from the most recent result heading."""
    for item in reversed(context):
        if item.get("direction") != "outbound":
            continue
        message = str(item.get("message_text", ""))
        match = re.search(r"\b(?:boys|girls)\s+size\s+(12|16|20|26)(?:\s*\"|\b)",
                          message, re.I)
        if match:
            return int(match.group(1))
        if "usually a good starting point" in message.casefold():
            return None
    return None


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
    """Rank live variants, returning unavailable ones only when no stock matches exist."""
    matches = []
    for product in products:
        available = [variant for variant in product.get("variants", []) if variant.get("available")]
        variants = available or list(product.get("variants", []))
        if variants:
            copy = dict(product)
            copy["variants"] = variants
            gender = _product_gender(copy)
            if preference and gender and gender != preference:
                continue
            rank = 0 if preference and gender == preference else 1
            try:
                lowest_price = min(Decimal(str(item["price"])) for item in variants)
            except (InvalidOperation, KeyError):
                lowest_price = Decimal("Infinity")
            matches.append((not bool(available), rank, lowest_price, copy))
    matches.sort(key=lambda item: (item[0], item[2], item[1]) if cheapest
                 else (item[0], item[1], item[2]))
    if any(not unavailable for unavailable, *_ in matches):
        matches = [item for item in matches if not item[0]]
    return [product for *_, product in matches[:limit]]


def has_available_variant(products: list[dict]) -> bool:
    return any(variant.get("available") for product in products
               for variant in product.get("variants", []))


def format_lkr_price(value: object) -> str:
    """Format a Shopify LKR numeric string without changing its value."""
    try:
        amount = f"{Decimal(str(value)):,.2f}".rstrip("0").rstrip(".")
    except (InvalidOperation, TypeError):
        amount = str(value or "")
    return f"Rs. {amount}"


def format_bicycle_product(product: dict) -> str:
    """Build the short caption/text fallback solely from normalized Shopify facts."""
    variant = product["variants"][0]
    variant_title = str(variant.get("title") or "").strip()
    title = str(product.get("title") or "Bicycle")
    label = f"{title} – {variant_title}" if variant_title and variant_title != "Default Title" else title
    availability = "✅ Available" if variant.get("available") else "❌ Currently unavailable"
    return "\n\n".join((f"🚲 {label}", f"💰 {format_lkr_price(variant.get('price'))}",
                         availability, f"🔗 {product.get('url') or ''}"))


def format_bicycle_results(age: int, preference: str, size: int, products: list[dict]) -> str:
    intro = (f"🚲 For a {age}-year-old {preference}, a {size}-inch bicycle is usually "
             "a good starting point.")
    if not products:
        return (intro + "\n\nI couldn't find a matching available bicycle in our live catalogue "
                "right now. Would you like to try another size or ask our team?")
    lines = [intro, "", "Here are some available options based on size/catalog information:", ""]
    for index, product in enumerate(products[:3], 1):
        variant = product["variants"][0]
        price = format_lkr_price(variant.get("price"))
        variant_title = str(variant.get("title") or "").strip()
        title = str(product.get("title") or "Bicycle")
        label = f"{title} – {variant_title}" if variant_title and variant_title != "Default Title" else title
        availability = "✅ Available" if variant.get("available") else "❌ Currently unavailable"
        lines.extend([f"{index}. {label}", f"💰 {price}", availability,
                      f"🔗 {product.get('url') or ''}", ""])
    lines.append("Would you like more options or to filter by colour/budget?")
    return "\n".join(lines).strip()
