"""Small, read-only Shopify Admin GraphQL catalogue client."""

import argparse
import json
import logging
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from settings import settings

logger = logging.getLogger(__name__)
TOKEN_MARGIN_SECONDS = 300
MAX_CANDIDATES = 30
MAX_COLLECTION_CANDIDATES = 20
STOREFRONT_ROOT = "https://www.pixiepinks.shop"
SEARCH_FIELDS = ("title", "product_type", "vendor", "tag")
PRODUCT_SYNONYMS = {
    "bicycle": ("bicycle", "bike"),
    "bike": ("bicycle", "bike"),
}


class ShopifyCatalogError(RuntimeError):
    """A safe error whose text never contains credentials or response bodies."""


class ShopifyCatalogClient:
    def __init__(self, http_client: Any = httpx, clock=time.time):
        self.http = http_client
        self.clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()

    def configured(self) -> bool:
        return all((settings.SHOPIFY_SHOP, settings.SHOPIFY_CLIENT_ID,
                    settings.SHOPIFY_CLIENT_SECRET, settings.SHOPIFY_API_VERSION))

    @property
    def base_url(self) -> str:
        shop = (settings.SHOPIFY_SHOP or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*", shop):
            raise ShopifyCatalogError("Shopify configuration is unavailable")
        return f"https://{shop}.myshopify.com"

    def clear_token(self) -> None:
        with self._token_lock:
            self._token = None
            self._expires_at = 0

    def get_token(self, force: bool = False) -> str:
        if not self.configured():
            raise ShopifyCatalogError("Shopify configuration is unavailable")
        with self._token_lock:
            now = self.clock()
            if not force and self._token and now < self._expires_at - TOKEN_MARGIN_SECONDS:
                return self._token
            try:
                response = self.http.post(
                    f"{self.base_url}/admin/oauth/access_token",
                    data={"grant_type": "client_credentials",
                          "client_id": settings.SHOPIFY_CLIENT_ID,
                          "client_secret": settings.SHOPIFY_CLIENT_SECRET},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
                response.raise_for_status()
                payload = response.json()
                token = payload.get("access_token")
                expires_in = int(payload.get("expires_in", 86399))
                if not isinstance(token, str) or not token:
                    raise ValueError("missing token")
            except Exception as exc:
                logger.warning("Shopify token request failed error_type=%s", type(exc).__name__)
                raise ShopifyCatalogError("Shopify authentication failed") from None
            self._token = token
            self._expires_at = now + max(0, expires_in)
            return token

    def graphql(self, query: str, variables: dict) -> dict:
        if not self.configured():
            raise ShopifyCatalogError("Shopify configuration is unavailable")
        endpoint = f"{self.base_url}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json"
        for attempt in range(2):
            token = self.get_token(force=attempt == 1)
            try:
                response = self.http.post(
                    endpoint, headers={"X-Shopify-Access-Token": token},
                    json={"query": query, "variables": variables},
                    timeout=httpx.Timeout(15.0, connect=5.0),
                )
            except httpx.RequestError as exc:
                logger.warning("Shopify GraphQL request failed error_type=%s", type(exc).__name__)
                raise ShopifyCatalogError("Shopify catalogue request failed") from None
            if response.status_code == 401 and attempt == 0:
                self.clear_token()
                continue
            if response.status_code == 401:
                raise ShopifyCatalogError("Shopify authentication failed")
            try:
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                logger.warning("Shopify GraphQL response failed status=%s error_type=%s",
                               response.status_code, type(exc).__name__)
                raise ShopifyCatalogError("Shopify catalogue request failed") from None
            if payload.get("errors"):
                logger.warning("Shopify GraphQL returned errors")
                raise ShopifyCatalogError("Shopify catalogue request failed")
            return payload.get("data") or {}
        raise ShopifyCatalogError("Shopify authentication failed")


PRODUCT_QUERY = """
query SearchProducts($first: Int!, $query: String!) {
  products(first: $first, query: $query, sortKey: RELEVANCE) {
    nodes { id title handle productType vendor tags status
      featuredImage { url }
      variants(first: 50) { nodes { id title sku price availableForSale
        inventoryQuantity inventoryItem { tracked }
        selectedOptions { name value }
      } }
    }
  }
}
"""

# Expected navigation labels are represented by stable handles rather than
# numeric IDs, and are kept in one place so a merchandising rename is simple.
BICYCLE_COLLECTIONS = {
    ("boy", 12): {"title": 'Boys Size 12"', "handle": "boys-size-12"},
    ("boy", 16): {"title": 'Boys Size 16"', "handle": "boys-size-16"},
    ("boy", 20): {"title": 'Boys Size 20"', "handle": "boys-size-20"},
    ("boy", 26): {"title": 'Boys Size 26"', "handle": "boys-size-26"},
    ("girl", 12): {"title": 'Girls Size 12"', "handle": "girls-size-12"},
    ("girl", 16): {"title": 'Girls Size 16"', "handle": "girls-size-16"},
    ("girl", 20): {"title": 'Girls Size 20"', "handle": "girls-size-20"},
    ("girl", 26): {"title": 'Girls Size 26"', "handle": "girls-size-26"},
}

COLLECTION_PRODUCTS_QUERY = """
query BicycleCollection($handle: String!, $first: Int!) {
  collectionByHandle(handle: $handle) {
    id title handle
    products(first: $first, sortKey: BEST_SELLING) {
      nodes { id title handle productType vendor tags status
        featuredImage { url }
        variants(first: 50) { nodes { id title sku price availableForSale
          inventoryQuantity inventoryItem { tracked }
          selectedOptions { name value }
        } }
      }
    }
  }
}
"""

DIAGNOSTIC_QUERY = """
query CatalogDiagnostic {
  products(first: 5) {
    nodes { title productType vendor handle }
  }
}
"""

BICYCLE_COLLECTION_DIAGNOSTIC_QUERY = """
query BicycleCollectionDiagnostic($first: Int!) {
  collections(first: $first) {
    nodes { title handle productsCount { count } }
  }
}
"""


def parse_filters(query: str) -> dict:
    text = query.casefold()
    number = r"(?:rs\.?|lkr|රු)?\s*([0-9][0-9,]*(?:\.\d+)?)"
    between = re.search(rf"(?:between|from)\s+{number}\s+(?:and|to|-)\s*{number}", text)
    maximum = re.search(rf"(?:under|below|less than|up to)\s*{number}", text)
    sinhala_maximum = re.search(rf"{number}\s*ට\s*අඩු", text)
    minimum = re.search(rf"(?:over|above|more than|at least)\s*{number}", text)
    # A bare age such as "12 years" must never be mistaken for an explicit wheel size.
    size = re.search(
        r"(?:\bsize\s*(12|16|20|24|26)(?:\s*(?:inch(?:es)?|\"|in))?(?=\s|$)|"
        r"\b(12|16|20|24|26)\s*(?:inch(?:es)?|\"|in)(?=\s|$))", text,
    )
    colours = ("pink", "blue", "red", "green", "black", "white", "yellow", "purple",
               "orange", "grey", "gray", "රෝස", "නිල්", "රතු", "කළු", "සුදු")
    colour = next((item for item in colours if re.search(
        rf"(?<!\w){re.escape(item)}(?!\w)", text)), None)

    def money(value: str | None) -> Decimal | None:
        try:
            return Decimal(value.replace(",", "")) if value else None
        except InvalidOperation:
            return None

    return {
        "min_price": money(between.group(1) if between else minimum.group(1) if minimum else None),
        "max_price": money(between.group(2) if between else maximum.group(1) if maximum
                           else sinhala_maximum.group(1) if sinhala_maximum else None),
        "size": next((group for group in size.groups() if group), None) if size else None,
        "colour": colour,
    }


def parse_search_intent(text: str) -> dict:
    """Separate product concepts from variant/price filters in customer language."""
    filters = parse_filters(text)
    cleaned = re.sub(r"[^\w\-]+", " ", text, flags=re.UNICODE)
    stop = {"do", "you", "have", "show", "me", "any", "is", "this", "the", "one",
            "what", "how", "much", "price", "available", "stock", "size", "under",
            "below", "less", "than", "between", "and", "rs", "lkr", "anything",
            "that", "second", "first", "third", "ones", "in", "inch", "inches",
            "another", "more", "cheaper", "please", "show",
            "තියෙනවද", "රු", "ට", "අඩු"}
    words = [word for word in cleaned.split() if word.casefold() not in stop and not re.fullmatch(r"[\d,]+", word)]
    singular = {"bicycles": "bicycle", "bikes": "bicycle", "bike": "bicycle",
                "bags": "bag",
                "toys": "toy", "chocolates": "chocolate"}
    colours = {str(filters["colour"])} if filters["colour"] else set()
    terms = []
    for word in words:
        normalized = singular.get(word.casefold(), word.casefold())
        if normalized in colours:
            continue
        if normalized not in terms:
            terms.append(normalized)
    return {"terms": terms[:4], "filters": filters}


def _shopify_search_query(text: str) -> str:
    """Build Shopify search grammar from parsed concepts, never raw prose."""
    concepts = parse_search_intent(text)["terms"]
    if not concepts:
        return "status:active"
    clauses = []
    for concept in concepts:
        aliases = PRODUCT_SYNONYMS.get(concept, (concept,))
        field_terms = [f"{field}:{alias}*" for alias in aliases for field in SEARCH_FIELDS]
        clauses.append("(" + " OR ".join(field_terms) + ")")
    return " AND ".join(clauses)


def _matches_product_terms(node: dict, terms: list[str]) -> bool:
    """Defensively confirm that each parsed concept occurs in a supported field."""
    haystack = " ".join([
        str(node.get("title", "")), str(node.get("productType", "")),
        str(node.get("vendor", "")), *(str(tag) for tag in node.get("tags", [])),
    ]).casefold()
    return all(any(alias in haystack for alias in PRODUCT_SYNONYMS.get(term, (term,)))
               for term in terms)


def _normalize_product(node: dict, filters: dict) -> dict | None:
    variants = []
    product_haystack = " ".join([str(node.get("title", "")),
                                  *(str(tag) for tag in node.get("tags", []))]).casefold()
    for raw in node.get("variants", {}).get("nodes", []):
        options = {item.get("name", ""): item.get("value", "") for item in raw.get("selectedOptions", [])}
        haystack = " ".join([str(raw.get("title", "")), *options.values()]).casefold()
        if filters["size"] and not re.search(rf"(?<!\d){re.escape(filters['size'])}(?!\d)", haystack):
            continue
        if filters["colour"] and filters["colour"] not in haystack and filters["colour"] not in product_haystack:
            continue
        try:
            price = Decimal(str(raw.get("price")))
        except (InvalidOperation, TypeError):
            continue
        if filters["min_price"] is not None and price < filters["min_price"]:
            continue
        if filters["max_price"] is not None and price > filters["max_price"]:
            continue
        tracked = (raw.get("inventoryItem") or {}).get("tracked")
        quantity = raw.get("inventoryQuantity") if tracked is True else None
        variants.append({"id": raw.get("id"), "title": raw.get("title"), "sku": raw.get("sku"),
                         "price": str(raw.get("price")), "currency": "LKR",
                         "available": bool(raw.get("availableForSale")),
                         "inventory_quantity": quantity,
                         "inventory_tracked": tracked, "selected_options": options})
    if not variants:
        return None
    handle = node.get("handle")
    return {"id": node.get("id"), "title": node.get("title"), "handle": handle,
            "product_type": node.get("productType"), "vendor": node.get("vendor"),
            "tags": node.get("tags") or [], "status": node.get("status"),
            "featured_image": (node.get("featuredImage") or {}).get("url"),
            "featured_image_verified": bool((node.get("featuredImage") or {}).get("url")),
            "url": f"{STOREFRONT_ROOT}/products/{handle}" if handle else None,
            "variants": variants}


client = ShopifyCatalogClient()


def search_products(query: str, limit: int = 5) -> list[dict]:
    """Search a bounded candidate set and filter its variants using live Shopify facts."""
    intent = parse_search_intent(query)
    filters = intent["filters"]
    data = client.graphql(PRODUCT_QUERY, {"first": MAX_CANDIDATES,
                                         "query": _shopify_search_query(query)})
    products = []
    for node in data.get("products", {}).get("nodes", []):
        if not _matches_product_terms(node, intent["terms"]):
            continue
        normalized = _normalize_product(node, filters)
        if normalized:
            products.append(normalized)
        if len(products) >= min(max(limit, 0), 5):
            break
    return products


def bicycle_collection(preference: str, wheel_size: int) -> dict | None:
    """Return the configured business collection; 24-inch is intentionally absent."""
    configured = BICYCLE_COLLECTIONS.get((preference, int(wheel_size)))
    return dict(configured) if configured else None


def search_bicycles_by_collection(
    preference: str, wheel_size: int, query: str = "", limit: int = 5
) -> dict:
    """Read a bounded collection and return only its member products.

    A missing collection never falls through to the opposite gender's catalogue.
    The returned collection identity is the identity Shopify actually resolved.
    """
    expected = bicycle_collection(preference, wheel_size)
    if expected is None:
        logger.info("Bicycle collection resolution requested_gender=%s requested_wheel_size=%s "
                    "expected_title=%s expected_handle=%s resolved=false resolved_title=%s "
                    "resolved_handle=%s product_count=0", preference, wheel_size, None, None,
                    None, None)
        return {"collection": None, "products": [], "reason": "unsupported_size"}
    data = client.graphql(COLLECTION_PRODUCTS_QUERY, {
        "handle": expected["handle"], "first": MAX_COLLECTION_CANDIDATES,
    })
    collection = data.get("collectionByHandle")
    if not collection or collection.get("handle") != expected["handle"]:
        logger.info("Bicycle collection resolution requested_gender=%s requested_wheel_size=%s "
                    "expected_title=%s expected_handle=%s resolved=false resolved_title=%s "
                    "resolved_handle=%s product_count=0", preference, wheel_size,
                    expected["title"], expected["handle"],
                    collection.get("title") if collection else None,
                    collection.get("handle") if collection else None)
        return {"collection": None, "products": [], "reason": "missing_collection"}

    # Membership is authoritative for gender and wheel size. Customer filters
    # (price/colour) are still verified against live variants.
    filters = parse_filters(query)
    filters["size"] = None
    terms = [term for term in parse_search_intent(query)["terms"]
             if term not in {"boy", "boys", "girl", "girls", "bicycle"}]
    products = []
    for node in collection.get("products", {}).get("nodes", []):
        if terms and not _matches_product_terms(node, terms):
            continue
        normalized = _normalize_product(node, filters)
        if normalized:
            normalized["bicycle_collection"] = {
                "id": collection.get("id"), "title": collection.get("title"),
                "handle": collection.get("handle"), "preference": preference,
                "wheel_size": wheel_size,
            }
            products.append(normalized)
        if len(products) >= min(max(limit, 0), 10):
            break
    logger.info("Bicycle collection resolution requested_gender=%s requested_wheel_size=%s "
                "expected_title=%s expected_handle=%s resolved=true resolved_title=%s "
                "resolved_handle=%s product_count=%s", preference, wheel_size,
                expected["title"], expected["handle"], collection.get("title"),
                collection.get("handle"), len(products))
    return {"collection": {"id": collection.get("id"), "title": collection.get("title"),
                           "handle": collection.get("handle")},
            "products": products, "reason": "ok"}


def diagnose_catalog_connectivity(catalog_client: ShopifyCatalogClient | None = None) -> dict:
    """Return and log only non-secret authentication and small catalogue facts."""
    diagnostic_client = catalog_client or client
    report = {"authentication": "failure", "graphql": "not_attempted",
              "product_count": 0, "products": []}
    try:
        diagnostic_client.get_token()
        report["authentication"] = "success"
    except ShopifyCatalogError:
        logger.info("Shopify diagnostic authentication=failure")
        return report
    try:
        data = diagnostic_client.graphql(DIAGNOSTIC_QUERY, {})
    except ShopifyCatalogError:
        report["graphql"] = "failure"
        logger.info("Shopify diagnostic authentication=success graphql=failure")
        return report
    report["graphql"] = "success"
    for node in data.get("products", {}).get("nodes", [])[:5]:
        report["products"].append({
            "title": node.get("title"), "product_type": node.get("productType"),
            "vendor": node.get("vendor"), "handle": node.get("handle"),
        })
    report["product_count"] = len(report["products"])
    logger.info("Shopify diagnostic authentication=success graphql=success products=%s details=%s",
                report["product_count"], report["products"])
    return report


def diagnose_bicycle_collections(catalog_client: ShopifyCatalogClient | None = None) -> list[dict]:
    """List relevant live collection identities without returning configuration or secrets."""
    diagnostic_client = catalog_client or client
    data = diagnostic_client.graphql(BICYCLE_COLLECTION_DIAGNOSTIC_QUERY, {"first": 100})
    collections = []
    for node in data.get("collections", {}).get("nodes", []):
        title = str(node.get("title") or "")
        if re.search(r"\b(?:boys?|girls?)\b.*\b(?:12|16|20|24|26)\b", title, re.I):
            collections.append({
                "title": title,
                "handle": node.get("handle"),
                "product_count": (node.get("productsCount") or {}).get("count"),
            })
    logger.info("Shopify bicycle collection diagnostic count=%s collections=%s",
                len(collections), collections)
    return collections


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run safe, read-only Shopify diagnostics")
    parser.add_argument("--bicycle-collections", action="store_true")
    args = parser.parse_args()
    if args.bicycle_collections:
        print(json.dumps(diagnose_bicycle_collections(), indent=2))
    else:
        print(json.dumps(diagnose_catalog_connectivity(), indent=2))
