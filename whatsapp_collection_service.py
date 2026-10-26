"""Read-only collection verification and native WhatsApp product-list transport.

Retailer IDs are explicit operator mappings, verified against the linked Meta
catalog. No Shopify/Meta ID convention is assumed and no external paging URL
is ever requested. Nothing in this module changes either catalog.
"""
import json
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx

from settings import settings
from shopify_catalog_service import ShopifyCatalogClient, STOREFRONT_ROOT
from whatsapp_service import _message_id, is_shopify_image_url

MAX_ITEMS = 30
SLOTS = threading.BoundedSemaphore(4)
COLLECTION_QUERY = """
query InboxCollection($id: ID!, $after: String) {
  collection(id: $id) {
    id title handle
    products(first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id title handle status publishedAt featuredImage { url }
        variants(first: 100) {
          pageInfo { hasNextPage }
          nodes { id title price availableForSale inventoryQuantity
            inventoryItem { tracked } image { url } }
        }
      }
    }
  }
}
"""


class CollectionError(Exception):
    """Only fixed, credential-free error messages cross the HTTP boundary."""


def configuration():
    ids = [settings.META_WHATSAPP_BUSINESS_ACCOUNT_ID,
           settings.META_WHATSAPP_PHONE_NUMBER_ID, settings.META_WHATSAPP_CATALOG_ID]
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9]{1,40}", value) for value in ids):
        raise CollectionError("Configure the WhatsApp business account, phone number and catalog IDs before sending collections.")
    if not settings.META_WHATSAPP_ACCESS_TOKEN:
        raise CollectionError("WhatsApp credentials are not configured.")
    try:
        mapping = json.loads(settings.META_WHATSAPP_PRODUCT_MAPPING_JSON)
        valid = (isinstance(mapping, dict) and 0 < len(mapping) <= 10000
                 and all(isinstance(key, str) and re.fullmatch(r"gid://shopify/ProductVariant/[0-9]+", key)
                         and isinstance(value, str) and 0 < len(value) <= 200
                         and all(32 <= ord(char) < 127 for char in value)
                         for key, value in mapping.items())
                 and len(set(mapping.values())) == len(mapping))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise CollectionError("Configure unique verified Shopify variant to Meta retailer product ID mappings before sending collections.")
    return mapping


class MetaCatalog:
    def __init__(self):
        version = settings.META_GRAPH_API_VERSION
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
            raise CollectionError("Meta API version configuration is invalid.")
        self.base = f"https://graph.facebook.com/{version}"
        self.deadline = time.monotonic() + 30

    def get(self, path, params):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionError("Meta catalog verification timed out. Refresh the preview.")
        try:
            response = httpx.get(f"{self.base}/{path}", params=params,
                headers={"Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}"},
                timeout=min(8, remaining), follow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ValueError()
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise CollectionError("Meta catalog verification is unavailable. Check catalog linkage and catalog-management permissions.") from None

    def rows(self, path, fields):
        cursor = None
        for _ in range(8):
            params = {"fields": fields, "limit": 250}
            if cursor:
                params["after"] = cursor
            data = self.get(path, params)
            rows = data.get("data")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise CollectionError("Meta catalog returned invalid records.")
            yield from rows
            paging = data.get("paging") or {}
            if not paging.get("next"):
                return
            new_cursor = (paging.get("cursors") or {}).get("after")
            if not isinstance(new_cursor, str) or not 0 < len(new_cursor) <= 1000 or new_cursor == cursor:
                raise CollectionError("Meta catalog pagination could not be verified.")
            cursor = new_cursor
        raise CollectionError("Meta catalog verification exceeds its safe scan limit. Use a smaller catalog or reviewed mapping integration.")

    def available_retailers(self, wanted):
        waba = settings.META_WHATSAPP_BUSINESS_ACCOUNT_ID
        phone = settings.META_WHATSAPP_PHONE_NUMBER_ID
        catalog = settings.META_WHATSAPP_CATALOG_ID
        if not any(row.get("id") == phone for row in self.rows(f"{waba}/phone_numbers", "id")):
            raise CollectionError("The sending phone number does not belong to the configured WhatsApp business account.")
        if not any(row.get("id") == catalog for row in self.rows(f"{waba}/product_catalogs", "id")):
            raise CollectionError("The configured Meta catalog is not linked to this WhatsApp business account.")
        matched = set()
        for row in self.rows(f"{catalog}/products", "retailer_id,availability"):
            retailer = row.get("retailer_id")
            if isinstance(retailer, str) and retailer in wanted and row.get("availability") == "in stock":
                matched.add(retailer)
            # Do not stop on unavailable entries: duplicate/ambiguous mappings are
            # disallowed in configuration and Meta owns retailer ID uniqueness.
            if matched == wanted:
                break
        return matched


def preview(collection_id):
    mapping = configuration()
    client = ShopifyCatalogClient()
    products, collection, cursor = [], None, None
    skipped = {"unavailable": 0, "unmapped": 0, "meta_unavailable": 0}
    for _ in range(4):
        data = client.graphql(COLLECTION_QUERY, {"id": collection_id, "after": cursor})
        node = data.get("collection")
        if not isinstance(node, dict) or node.get("id") != collection_id:
            raise CollectionError("This Shopify collection is unavailable.")
        collection = {key: node.get(key) for key in ("id", "title", "handle")}
        if not isinstance(collection["title"], str) or not collection["title"]:
            raise CollectionError("This Shopify collection has no valid title.")
        connection = node.get("products") or {}
        for product in connection.get("nodes") or []:
            if product.get("status") != "ACTIVE" or not product.get("publishedAt"):
                skipped["unavailable"] += 1
                continue
            variants = product.get("variants") or {}
            if (variants.get("pageInfo") or {}).get("hasNextPage"):
                raise CollectionError("A product has more than 100 variants. Use a smaller collection; no partial collection was sent.")
            for variant in variants.get("nodes") or []:
                tracked = bool((variant.get("inventoryItem") or {}).get("tracked"))
                quantity = variant.get("inventoryQuantity")
                if (not variant.get("availableForSale") or
                        (tracked and (type(quantity) is not int or quantity <= 0))):
                    skipped["unavailable"] += 1
                    continue
                retailer = mapping.get(variant.get("id"))
                if not retailer:
                    skipped["unmapped"] += 1
                    continue
                try:
                    price = Decimal(str(variant.get("price"))).quantize(Decimal("0.01"))
                    if not price.is_finite() or price < 0:
                        raise ValueError()
                except (InvalidOperation, ValueError):
                    skipped["unavailable"] += 1
                    continue
                image = (variant.get("image") or {}).get("url") or (product.get("featuredImage") or {}).get("url")
                handle = product.get("handle")
                if not isinstance(handle, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", handle):
                    skipped["unavailable"] += 1
                    continue
                products.append({"product_id": product["id"], "variant_id": variant["id"],
                    "title": str(product.get("title") or "Product")[:255],
                    "variant_title": str(variant.get("title") or "")[:255], "price": str(price),
                    "image": image if safe_image(image) else None,
                    "url": f"{STOREFRONT_ROOT}/products/{handle}", "retailer_id": retailer})
        info = connection.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        next_cursor = info.get("endCursor")
        if not isinstance(next_cursor, str) or next_cursor == cursor or len(next_cursor) > 1000:
            raise CollectionError("Shopify collection pagination could not be verified.")
        cursor = next_cursor
    else:
        raise CollectionError("This collection exceeds 200 products. Use a smaller collection; no partial collection was sent.")
    if len(products) > 200:
        raise CollectionError("This collection exceeds 200 available mapped variants. Use a smaller collection.")
    available = MetaCatalog().available_retailers({product["retailer_id"] for product in products})
    verified = [product for product in products if product["retailer_id"] in available]
    skipped["meta_unavailable"] = len(products) - len(verified)
    return {"collection": collection, "items": verified, "excluded": skipped}


def safe_image(value):
    if not is_shopify_image_url(value):
        return False
    try:
        parsed = urlparse(value)
        return not parsed.username and not parsed.password and parsed.port in {None, 443}
    except ValueError:
        return False


def send_product_list(recipient, title, items):
    """Return (SENT|FAILED|UNKNOWN, wamid). Never retry a network-uncertain send."""
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient,
        "type": "interactive", "interactive": {"type": "product_list",
            "header": {"type": "text", "text": title[:60]},
            "body": {"text": "Browse these available products. Prices and availability are shown in our WhatsApp catalog."},
            "action": {"catalog_id": settings.META_WHATSAPP_CATALOG_ID,
                "sections": [{"title": "Available products", "product_items": [
                    {"product_retailer_id": item["retailer_id"]} for item in items]}]}}}
    try:
        result = httpx.post(f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{settings.META_WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}"},
            json=payload, timeout=15, follow_redirects=False)
        if 400 <= result.status_code < 500 and result.status_code not in {408, 429}:
            return "FAILED", None
        if result.status_code == 200 and (message_id := _message_id(result)):
            return "SENT", message_id
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return "UNKNOWN", None
