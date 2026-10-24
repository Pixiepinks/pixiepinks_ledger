"""Versioned, deterministic business configuration for the store-wide assistant.

Shopify facts and security/payment guardrails intentionally do not live here.
"""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import threading
import time

from sqlalchemy.orm import Session

from models import BotAuditEvent, BotConfiguration, BotConfigurationVersion

SUPPORTED_SIZES = {12, 16, 20, 26}
HARD_GUARDRAILS = (
    "Shopify is authoritative for product existence, price and availability.",
    "Never invent bank details or confirm a payment automatically.",
    "HUMAN conversation mode always pauses AI automation.",
    "Customer data remains private and webhook idempotency remains enforced.",
)


def default_snapshot() -> dict:
    return {
        "global": {
            "instructions": "Be friendly, professional and concise. Reply in the customer's language.",
            "reply_in_customer_language": True, "ask_one_question": True,
            "moderate_emojis": True, "concise": True, "send_product_images": True,
            "maximum_products": 3,
        },
        "bicycle": {
            "age_rules": [
                {"from": 2, "to": 3, "size": 12, "enabled": True},
                {"from": 4, "to": 5, "size": 16, "enabled": True},
                {"from": 6, "to": 10, "size": 20, "enabled": True},
                {"from": 11, "to": None, "size": 26, "enabled": True},
            ],
            "service_charges": {"12": "2000.00", "16": "2000.00", "20": "2500.00", "26": "2500.00"},
            "free_islandwide_delivery": True,
            "collection_titles": {
                "boy:12": 'Size 12" Boys Bicycles', "boy:16": 'Size 16" Boys Bicycles',
                "boy:20": 'Size 20" Boys Bicycles', "boy:26": 'Size 26" Boys Bicycles',
                "girl:12": 'Size 12" Girls Bicycles', "girl:16": 'Size 16" Girls Bicycles',
                "girl:20": 'Size 20" Girls Bicycles', "girl:26": 'Size 26" Girls Bicycles',
            },
        },
        "delivery": {"minimum_working_days": 3, "maximum_working_days": 4,
                     "skip_weekends": True, "public_holidays_calculated": False},
        "knowledge": [], "policies": [], "faqs": [], "guided_questions": [], "guided_flows": [],
        "recommendation_rules": [],
    }


_cache_lock = threading.Lock()
_cache: tuple[float, int, dict] | None = None


def ensure_configuration(db: Session) -> BotConfiguration:
    config = db.query(BotConfiguration).first()
    if config:
        return config
    snapshot = default_snapshot()
    config = BotConfiguration(published_version=1, published_snapshot=deepcopy(snapshot),
                              draft_snapshot=deepcopy(snapshot), draft_revision=0)
    db.add(config)
    db.flush()
    db.add(BotConfigurationVersion(version=1, snapshot=deepcopy(snapshot),
                                   change_summary="Initial approved configuration", published_by="system"))
    db.commit()
    return config


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def get_snapshot(db: Session, draft: bool = False) -> tuple[dict, int]:
    global _cache
    config = ensure_configuration(db)
    if draft:
        return deepcopy(config.draft_snapshot), config.published_version
    now = time.monotonic()
    with _cache_lock:
        if _cache and _cache[0] > now and _cache[1] == config.published_version:
            return deepcopy(_cache[2]), config.published_version
        snapshot = deepcopy(config.published_snapshot or default_snapshot())
        _cache = (now + 30, config.published_version, snapshot)
        return deepcopy(snapshot), config.published_version


def validate_snapshot(snapshot: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    rules = [r for r in snapshot.get("bicycle", {}).get("age_rules", []) if r.get("enabled")]
    normalized = []
    for index, rule in enumerate(rules, 1):
        start, end, size = rule.get("from"), rule.get("to"), rule.get("size")
        if not isinstance(start, int) or start < 2 or (end is not None and (not isinstance(end, int) or start > end)):
            errors.append(f"Bicycle age rule {index} has an invalid range.")
            continue
        if size not in SUPPORTED_SIZES:
            errors.append(f"Bicycle age rule {index} has an unsupported size.")
        normalized.append((start, end if end is not None else 999, index))
    normalized.sort()
    for previous, current in zip(normalized, normalized[1:]):
        if current[0] <= previous[1]:
            errors.append(f"Bicycle age rules {previous[2]} and {current[2]} overlap.")
        elif current[0] > previous[1] + 1:
            warnings.append(f"No bicycle recommendation covers ages {previous[1] + 1}–{current[0] - 1}.")
    for size, amount in snapshot.get("bicycle", {}).get("service_charges", {}).items():
        try:
            if int(size) not in SUPPORTED_SIZES or Decimal(str(amount)) < 0:
                raise ValueError
        except (ValueError, InvalidOperation):
            errors.append(f"Service charge for size {size} is invalid.")
    delivery = snapshot.get("delivery", {})
    minimum, maximum = delivery.get("minimum_working_days"), delivery.get("maximum_working_days")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum < 0 or minimum > maximum:
        errors.append("Delivery working-day range is invalid.")
    maximum_products = snapshot.get("global", {}).get("maximum_products")
    if not isinstance(maximum_products, int) or not 1 <= maximum_products <= 5:
        errors.append("Maximum recommended products must be between 1 and 5.")
    instruction = snapshot.get("global", {}).get("instructions", "")
    if not str(instruction).strip():
        errors.append("Global instructions cannot be empty.")
    if re.search(r"invent\s+(?:a\s+)?(?:price|stock|bank|payment)", str(instruction), re.I):
        errors.append("Global instructions conflict with locked system guardrails.")
    for bucket in ("knowledge", "policies", "faqs"):
        for entry in snapshot.get(bucket, []):
            scope = entry.get("scope", "GLOBAL")
            reference = str(entry.get("scope_reference") or "").strip()
            if scope not in {"GLOBAL", "COLLECTION", "PRODUCT"}:
                errors.append(f"{entry.get('title', 'Entry')} has an invalid scope.")
            elif scope == "GLOBAL" and reference:
                errors.append(f"{entry.get('title', 'Entry')} must not bind global content to Shopify.")
            elif scope != "GLOBAL" and not reference:
                errors.append(f"{entry.get('title', 'Entry')} requires a Shopify {scope.lower()} selection.")
    for flow in snapshot.get("guided_flows", []):
        keys = set()
        for question in flow.get("questions", []):
            key = str(question.get("key", ""))
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", key) or key in keys:
                errors.append(f"Guided question key '{key}' is invalid or duplicated.")
            keys.add(key)
            if question.get("answer_type") not in {"CHOICE", "NUMBER", "YES_NO", "TEXT"}:
                errors.append(f"Guided question '{key}' has an invalid answer type.")
        for rule in flow.get("rules", []):
            if rule.get("action", {}).get("type") not in {
                    "SET_ATTRIBUTE", "CHOOSE_COLLECTION", "PRODUCT_FILTER",
                    "SET_VALUE", "ASK_NEXT", "NO_MATCH"}:
                errors.append(f"Recommendation rule '{rule.get('name', '')}' has an unsafe action.")
    return errors, warnings


def normalize_answer(question: dict, value: str):
    """Normalize one answer using a closed set of deterministic types."""
    raw = str(value or "").strip()
    answer_type = question.get("answer_type")
    if not raw and question.get("required", True):
        raise ValueError("This question requires an answer.")
    if answer_type == "NUMBER":
        try:
            return Decimal(raw.replace(",", ""))
        except InvalidOperation:
            raise ValueError("Please enter a number.") from None
    if answer_type == "YES_NO":
        normalized = raw.casefold()
        if normalized in {"yes", "y", "true", "1", "ඔව්"}: return "YES"
        if normalized in {"no", "n", "false", "0", "නැහැ"}: return "NO"
        raise ValueError("Please answer yes or no.")
    if answer_type == "CHOICE":
        folded = raw.casefold()
        for choice in question.get("choices", []):
            label = str(choice.get("label", choice) if isinstance(choice, dict) else choice)
            aliases = choice.get("aliases", []) if isinstance(choice, dict) else []
            if folded == label.casefold() or folded in {str(item).casefold() for item in aliases}:
                return str(choice.get("value", label) if isinstance(choice, dict) else label).upper()
        raise ValueError("Please choose one of the approved answers.")
    return raw[:1000]


def evaluate_rule(rule: dict, answers: dict) -> bool:
    """Evaluate allow-listed conditions only; configurable content is never executed."""
    for condition in rule.get("conditions", []):
        actual, operator = answers.get(condition.get("key")), condition.get("operator")
        expected = condition.get("value")
        if operator == "EQUALS" and str(actual).casefold() != str(expected).casefold(): return False
        if operator == "BETWEEN":
            try:
                if not Decimal(str(expected)) <= Decimal(str(actual)) <= Decimal(str(condition["value_to"])): return False
            except (InvalidOperation, KeyError, TypeError): return False
        if operator == "CONTAINS" and str(expected).casefold() not in str(actual).casefold(): return False
        if operator not in {"EQUALS", "BETWEEN", "CONTAINS"}: return False
    return bool(rule.get("conditions"))


def relevant_entries(snapshot: dict, bucket: str, query: str = "", collection: str | None = None,
                     product: str | None = None) -> list[dict]:
    """Return only enabled, non-archived content in global-to-specific order."""
    words = set(re.findall(r"[\w-]+", query.casefold()))
    ranked = []
    for item in snapshot.get(bucket, []):
        if not item.get("enabled", True) or item.get("archived", False): continue
        scope, ref = item.get("scope", "GLOBAL"), item.get("scope_reference")
        if scope == "COLLECTION" and ref != collection: continue
        if scope == "PRODUCT" and ref != product: continue
        haystack = f"{item.get('title','')} {item.get('topic','')} {item.get('tags','')}".casefold()
        score = len(words & set(re.findall(r"[\w-]+", haystack)))
        if scope != "GLOBAL" and words and score == 0: continue
        ranked.append(({"GLOBAL": 0, "COLLECTION": 1, "PRODUCT": 2}[scope], -score, item))
    return [item for _, __, item in sorted(ranked, key=lambda row: (row[0], row[1]))][:20]


def save_draft(db: Session, snapshot: dict, actor: str, summary: str) -> BotConfiguration:
    config = ensure_configuration(db)
    config.draft_snapshot = deepcopy(snapshot)
    config.draft_revision += 1
    config.updated_at = datetime.utcnow()
    db.add(BotAuditEvent(action="DRAFT_SAVED", summary=summary, actor=actor))
    db.commit()
    return config


def publish(db: Session, actor: str, summary: str) -> BotConfiguration:
    config = ensure_configuration(db)
    errors, _ = validate_snapshot(config.draft_snapshot)
    if errors:
        raise ValueError(" ".join(errors))
    version = config.published_version + 1
    snapshot = deepcopy(config.draft_snapshot)
    config.published_snapshot = snapshot
    config.published_version = version
    config.published_at = datetime.utcnow()
    db.add(BotConfigurationVersion(version=version, snapshot=deepcopy(snapshot),
                                   change_summary=summary, published_by=actor))
    db.add(BotAuditEvent(action="PUBLISHED", summary=summary, actor=actor))
    db.commit()
    invalidate_cache()
    return config


def restore_as_draft(db: Session, version: int, actor: str) -> BotConfiguration:
    historical = db.query(BotConfigurationVersion).filter_by(version=version).one_or_none()
    if not historical:
        raise ValueError("Configuration version was not found.")
    return save_draft(db, historical.snapshot, actor, f"Restored version {version} as draft")


def recommended_size(db: Session, age: int, draft: bool = False) -> tuple[int, int]:
    snapshot, version = get_snapshot(db, draft)
    for rule in snapshot["bicycle"]["age_rules"]:
        if rule.get("enabled") and age >= rule["from"] and (rule.get("to") is None or age <= rule["to"]):
            return int(rule["size"]), version
    raise ValueError("No approved bicycle size rule covers this age.")


def service_charge(db: Session, size: int) -> Decimal | None:
    snapshot, _ = get_snapshot(db)
    value = snapshot["bicycle"]["service_charges"].get(str(size))
    return Decimal(value) if value is not None else None
