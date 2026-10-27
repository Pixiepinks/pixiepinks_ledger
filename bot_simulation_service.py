"""Read-only DRAFT/LIVE AI preview for all retail categories, not just bicycles."""
import copy
import json
import re
import threading
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import ai_service
from bot_configuration_service import relevant_entries, validate_snapshot, simulation_preferences
from learning_policy import sanitize
from models import BotConfiguration
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError, get_product_by_handle, search_products

SLOTS = threading.BoundedSemaphore(2)
_lock = threading.Lock()
_attempts = {}
MEMBERSHIP_QUERY = """
query SimulatorProductCollections($ids: [ID!]!) {
  nodes(ids: $ids) { ... on Product {
    id handle collections(first: 20) { nodes { handle } pageInfo { hasNextPage } }
  } }
}
"""


class SimulationError(Exception):
    def __init__(self, message, status_code=409):
        super().__init__(message); self.status_code = status_code


class SimulationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["draft", "live"] = "draft"
    message: str = Field(min_length=1, max_length=2000)
    context_text: str = Field(default="", max_length=2000)
    catalog_mode: Literal["auto", "off"] = "auto"
    product_handle: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_sample(self):
        if not self.message.strip(): raise ValueError("Enter a sample question")
        if self.product_handle and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", self.product_handle):
            raise ValueError("Use a product handle, not a URL")
        return self


def admit(actor):
    now = monotonic()
    with _lock:
        for key in list(_attempts):
            _attempts[key] = [when for when in _attempts[key] if now - when < 3600]
            if not _attempts[key]: del _attempts[key]
        if len(_attempts.get(actor, [])) >= 20 or (actor not in _attempts and len(_attempts) >= 128):
            raise SimulationError("Simulator limit reached: 20 attempts per manager per hour per web process.", 429)
        _attempts.setdefault(actor, []).append(now)


def preferences(snapshot):
    try: return simulation_preferences(snapshot)
    except ValueError as error: raise SimulationError(str(error)) from None


def collection_memberships(snapshot, catalog, warnings):
    needed = any(item.get("scope") == "COLLECTION" and item.get("enabled", True) and not item.get("archived", False)
        for bucket in ["policies", "knowledge", "faqs"] for item in snapshot.get(bucket, []))
    if not needed or not catalog: return {}
    wanted = {item["id"]: item["handle"] for item in catalog if re.fullmatch(r"gid://shopify/Product/[0-9]+", str(item.get("id", "")))}
    if not wanted: return {}
    try:
        result = ShopifyCatalogClient().graphql(MEMBERSHIP_QUERY, {"ids": list(wanted)[:5]})
    except ShopifyCatalogError:
        warnings.append("Collection membership could not be verified; collection-scoped information was excluded.")
        return {}
    verified = {}
    for node in result.get("nodes") or []:
        if not isinstance(node, dict) or node.get("id") not in wanted or wanted[node["id"]] != node.get("handle"): continue
        connection = node.get("collections") or {}
        verified[node["handle"]] = {item["handle"] for item in connection.get("nodes", []) if isinstance(item, dict) and isinstance(item.get("handle"), str)}
        if (connection.get("pageInfo") or {}).get("hasNextPage"):
            warnings.append("Only the first 20 verified collection memberships per product were considered.")
    return verified


def context_payload(snapshot, query, catalog, memberships, warnings):
    selected, seen, size = [], set(), 0
    scopes = [(None, None)] + [(product.get("handle"), None) for product in catalog]
    scopes += [(handle, collection) for handle, collections in memberships.items() for collection in collections]
    for bucket in ["policies", "faqs", "knowledge"]:
        for product, collection in scopes:
            for entry in relevant_entries(snapshot, bucket, query, collection=collection, product=product):
                key = json.dumps([bucket, entry], sort_keys=True)
                if key in seen: continue
                seen.add(key)
                title, content = str(entry.get("title") or ""), str(entry.get("content") or "")
                if len(title) > 120 or len(content) > 1500 or len(selected) >= 8 or size + len(content) > 6000:
                    warnings.append("Some matching knowledge exceeded the safe preview context limit and was excluded.")
                    continue
                scope, reference = entry.get("scope", "GLOBAL"), entry.get("scope_reference")
                applies_to = ([reference] if scope == "PRODUCT" else
                    sorted(handle for handle, collections in memberships.items() if reference in collections) if scope == "COLLECTION" else [])
                selected.append({"title": title, "content": content, "scope": scope,
                    "scope_reference": reference, "applies_to_products": applies_to, "bucket": bucket})
                size += len(content)
    return {"administrator_policies": selected,
        "approved_bicycle_and_delivery_rules": {"bicycle": snapshot.get("bicycle", {}), "delivery": snapshot.get("delivery", {})},
        "manager_reviewed_examples": [], "automatic_communication_examples": [], "versions": []}


def simulate(db, actor, values, needs_catalog):
    config = db.query(BotConfiguration).first()
    if not config: raise SimulationError("Initialize Bot Management configuration before running a preview.")
    snapshot = copy.deepcopy(config.draft_snapshot if values.mode == "draft" else config.published_snapshot)
    errors, _ = validate_snapshot(snapshot)
    if errors: raise SimulationError("Selected configuration is invalid: " + " ".join(errors[:5]))
    prefs = preferences(snapshot)
    if not settings.OPENAI_API_KEY: raise SimulationError("AI provider is not configured. No simulation request was sent.")
    if not SLOTS.acquire(blocking=False): raise SimulationError("Simulator is busy. Try again shortly.", 429)
    try:
        admit(actor)
        message, context_text = sanitize(values.message), sanitize(values.context_text)
        context = [{"direction": "inbound", "message_text": "Synthetic context, not verified business facts: " + context_text}] if context_text else []
        warnings, catalog, catalog_status = [], None, "NOT_REQUESTED"
        if prefs.get("automatic_product_attachments") and prefs.get("send_product_images"):
            from product_recommendation_service import recommend
            exact = None
            if values.product_handle:
                try:
                    exact = get_product_by_handle(values.product_handle) or {}
                    if exact.get("handle") != values.product_handle:
                        exact = {}
                except ShopifyCatalogError:
                    exact = {}
                    warnings.append("The exact product could not be verified. No attachments were selected.")
            result = recommend(snapshot, message, context, catalog_enabled=values.catalog_mode != "off",
                               exact_product=exact)
            cards = []
            for product in result.products:
                variant = next(v for v in product["variants"] if v["id"] == product["selected_variant_id"])
                cards.append({"title": product["title"], "handle": product["handle"],
                    "product_type": product.get("product_type"), "price": variant["price"],
                    "currency": variant.get("currency", "LKR"),
                    "image": variant.get("image") or product.get("featured_image")})
            return {"reply": result.reply if result.status == "GENERATED" else None,
                "status": result.status, "tokens": result.tokens, "model": settings.OPENAI_MODEL,
                "products": cards, "warnings": warnings,
                "trace": {"engine": "PRODUCT_RECOMMENDATION_PREVIEW", "mode": values.mode.upper(),
                    "stage": result.stage, "published_version": config.published_version,
                    "global_instructions_supplied": True, "verified_products": len(cards),
                    "input_redacted": message != values.message or context_text != values.context_text,
                    "live_learning_retrieval_unchanged": settings.LEARNING_RETRIEVAL_ENABLED,
                    "learning_examples_supplied": False, "customer_records_loaded": False, "messages_sent": 0},
                "notice": "Up to two billable AI calls: requirement discovery and verified product selection. "
                    "These cards are previews only; no WhatsApp attachments were sent. Live delivery rechecks "
                    "stock, current price, AI mode and the 24-hour service window before each attachment. "
                    "Existing bicycle and order workflows keep their own routing."}
        if values.product_handle:
            try:
                found = get_product_by_handle(values.product_handle)
            except ShopifyCatalogError:
                found = None; catalog_status = "UNAVAILABLE"
                warnings.append("Shopify was unavailable. No price, stock or product identity was verified.")
            if catalog_status != "UNAVAILABLE":
                catalog = [found] if found and found.get("handle") == values.product_handle else []
                catalog_status = "VERIFIED_HANDLE" if catalog else "NO_MATCH"
        elif values.catalog_mode == "auto" and needs_catalog(message, context):
            try:
                catalog = search_products(message, limit=prefs["maximum_products"])
                catalog_status = "SEARCH_RESULTS" if catalog else "NO_MATCH"
            except ShopifyCatalogError:
                catalog_status = "UNAVAILABLE"
                warnings.append("Shopify was unavailable. No price, stock or product identity was verified.")
        elif values.catalog_mode == "off": catalog_status = "DISABLED_FOR_TEST"
        verified = (catalog or [])[:prefs["maximum_products"]]
        memberships = collection_memberships(snapshot, verified, warnings)
        payload = context_payload(snapshot, message, verified, memberships, warnings)
        test = ai_service.ReplyTestContext(learned_payload=payload, business_preferences=prefs)
        reply = ai_service.generate_customer_reply(message, recent_context=context, catalog_results=catalog, test_context=test)
        cards = []
        for product in verified:
            handle = product.get("handle")
            if not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", handle): continue
            cards.append({"title": product.get("title"), "handle": handle, "product_type": product.get("product_type")})
        return {"reply": reply if test.status == "GENERATED" else None, "status": test.status,
            "tokens": test.total_tokens, "model": settings.OPENAI_MODEL, "products": cards,
            "warnings": list(dict.fromkeys(warnings)), "trace": {"engine": "AI_PREVIEW", "mode": values.mode.upper(),
                "published_version": config.published_version, "draft_revision": config.draft_revision if values.mode == "draft" else None,
                "global_instructions_supplied": True, "catalog_status": catalog_status, "verified_products": len(verified),
                "context_entries": [{key: item[key] for key in ["title", "scope", "scope_reference", "applies_to_products", "bucket"]} for item in payload["administrator_policies"]],
                "input_redacted": message != values.message or context_text != values.context_text,
                "live_learning_retrieval_unchanged": settings.LEARNING_RETRIEVAL_ENABLED,
                "learning_examples_supplied": False, "customer_records_loaded": False, "messages_sent": 0},
            "notice": "One billable AI request per run. This previews AI generation with the selected configuration; it is not the complete WhatsApp router, image vision or an executed order/payment/handover workflow. Shopify search results do not prove which product a quoted image or advertisement meant. No customer messages, business records or settings are changed. Live customer reply-language routing is not changed by this simulator."}
    finally: SLOTS.release()
