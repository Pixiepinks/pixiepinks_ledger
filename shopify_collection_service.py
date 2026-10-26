"""Bounded, read-only Shopify collection discovery for ordinary image sends."""
import hashlib
import json
import re
import threading
from time import monotonic
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from shopify_catalog_service import ShopifyCatalogClient, STOREFRONT_ROOT
from whatsapp_service import is_shopify_image_url

MAX_PRODUCTS = 20
SLOTS = threading.BoundedSemaphore(4)
COLLECTION_QUERY = """
query StaffCollectionProducts($id: ID!, $after: String) {
  collection(id: $id) {
    id title handle
    products(first: 5, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id title handle status publishedAt featuredImage { url }
        variants(first: 50) {
          pageInfo { hasNextPage }
          nodes { id title sku price availableForSale inventoryQuantity
            inventoryItem { tracked } image { url } }
        }
      }
    }
  }
}
"""


class CollectionError(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def valid_id(value, kind):
    return isinstance(value, str) and bool(re.fullmatch(r"gid://shopify/" + kind + r"/[0-9]{1,30}", value))


def safe_image(value):
    if not is_shopify_image_url(value):
        return False
    try:
        parsed = urlparse(value)
        return not parsed.username and not parsed.password and parsed.port in {None, 443}
    except ValueError:
        return False


def available(product):
    quantity = product.get("inventory_quantity")
    return bool(product.get("published") and product.get("available")
        and (not product.get("inventory_tracked") or (type(quantity) is int and quantity > 0))
        and safe_image(product.get("image")))


def snapshot(product):
    """Only reviewed customer-facing values; quantities are rechecked, not frozen."""
    return {**{key: product.get(key) for key in ["id", "variant_id", "handle", "title", "variant_title",
        "sku", "currency", "image", "url"]}, "price": str(Decimal(str(product["price"])).quantize(Decimal("0.01")))}


def load_collection(collection_id, catalog_client=None):
    client = catalog_client or ShopifyCatalogClient()
    products, cursor, seen = [], None, set()
    excluded = 0
    # Small nested pages avoid Shopify's per-query cost ceiling; do not request
    # 50 products * 50 variants * multiple nested objects in one query.
    deadline = monotonic() + 30
    for _ in range(40):
        if monotonic() >= deadline:
            raise CollectionError("Collection verification timed out. Try again or use a smaller collection.")
        data = client.graphql(COLLECTION_QUERY, {"id": collection_id, "after": cursor})
        if monotonic() >= deadline:
            raise CollectionError("Collection verification timed out. Try again or use a smaller collection.")
        collection = data.get("collection")
        if not isinstance(collection, dict) or collection.get("id") != collection_id:
            raise CollectionError("This collection is unavailable in Shopify.")
        title = collection.get("title")
        if not isinstance(title, str) or not title.strip():
            raise CollectionError("This collection has no valid title.")
        connection = collection.get("products") or {}
        for node in connection.get("nodes") or []:
            pid, handle = node.get("id"), node.get("handle")
            if not valid_id(pid, "Product") or pid in seen:
                continue
            seen.add(pid)
            if (node.get("status") != "ACTIVE" or not node.get("publishedAt")
                    or not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", handle)):
                excluded += 1
                continue
            variants = node.get("variants") or {}
            if (variants.get("pageInfo") or {}).get("hasNextPage"):
                raise CollectionError("A product has more than 50 variants. Use a smaller collection; no partial result was selected.")
            choices = []
            for variant in variants.get("nodes") or []:
                if not valid_id(variant.get("id"), "ProductVariant"):
                    continue
                try:
                    price = Decimal(str(variant.get("price"))).quantize(Decimal("0.01"))
                    if not price.is_finite() or price < 0:
                        continue
                except (InvalidOperation, ValueError, TypeError):
                    continue
                product = {"id": pid, "variant_id": variant["id"], "handle": handle,
                    "title": str(node.get("title") or "Product")[:255],
                    "variant_title": str(variant.get("title") or "")[:255], "sku": variant.get("sku") or "",
                    "price": price, "currency": "LKR", "published": True,
                    "available": bool(variant.get("availableForSale")),
                    "inventory_tracked": bool((variant.get("inventoryItem") or {}).get("tracked")),
                    "inventory_quantity": variant.get("inventoryQuantity"),
                    "image": (variant.get("image") or {}).get("url") or (node.get("featuredImage") or {}).get("url"),
                    "url": f"{STOREFRONT_ROOT}/products/{handle}"}
                if available(product):
                    choices.append(snapshot(product))
            if choices:
                products.append({"id": pid, "title": str(node.get("title") or "Product")[:255], "variants": choices})
            else:
                excluded += 1
        info = connection.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return {"collection": {"id": collection_id, "title": title[:255], "handle": collection.get("handle")},
                    "products": products, "excluded_products": excluded, "max_products": MAX_PRODUCTS}
        after = info.get("endCursor")
        if not isinstance(after, str) or not 0 < len(after) <= 1000 or after == cursor:
            raise CollectionError("Collection pagination could not be verified.")
        cursor = after
    raise CollectionError("This collection exceeds 200 products. Use a smaller collection; no partial result was selected.")
