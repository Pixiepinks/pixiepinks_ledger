"""Safe Shopify Draft Order synchronization for commercial sales orders.

This module creates drafts only. It never completes orders, captures payment,
reserves inventory, creates fulfillments, or posts accounting entries.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from models import SalesOrder, ShopifySyncEvent
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError, resolve_inbox_product

CREATE_EVENT = "DRAFT_ORDER_CREATE"
REFRESH_EVENT = "DRAFT_ORDER_REFRESH"
ELIGIBLE_ORDER_STATUSES = {"DRAFT", "AWAITING_CUSTOMER_CONFIRMATION", "AWAITING_PAYMENT", "CONFIRMED"}
CLAIM_LEASE_SECONDS = 300
DRAFT_ORDER_GID = re.compile(r"^gid://shopify/DraftOrder/([0-9]+)$")


class ShopifyDraftSyncError(RuntimeError):
    """A safe operational error suitable for staff display."""

    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class ShopifyOutcomeUncertain(ShopifyDraftSyncError):
    pass


DRAFT_FIELDS = """
fragment LedgerDraft on DraftOrder {
  id name status updatedAt
  totalPriceSet { shopMoney { amount currencyCode } }
  totalTaxSet { shopMoney { amount currencyCode } }
  order { id name }
}
"""

CREATE_MUTATION = DRAFT_FIELDS + """
mutation CreateLedgerDraft($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { ...LedgerDraft }
    userErrors { field message }
  }
}
"""

GET_QUERY = DRAFT_FIELDS + """
query LedgerDraft($id: ID!) { draftOrder(id: $id) { ...LedgerDraft } }
"""

FIND_QUERY = DRAFT_FIELDS + """
query FindLedgerDraft($query: String!) {
  draftOrders(first: 10, query: $query) { nodes { ...LedgerDraft } }
}
"""


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ShopifyDraftSyncError("Shopify returned an invalid monetary value", status_code=502) from None


def _operation_key(order: SalesOrder) -> str:
    return f"shopify-draft-create:{order.id}"


def _tag(order: SalesOrder) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", order.internal_order_number)
    return f"ledger-{safe}"[:255]


def _payload_hash(order: SalesOrder) -> str:
    snapshot = {
        "order": order.internal_order_number,
        "total": str(order.grand_total),
        "currency": order.currency,
        "lines": [(line.shopify_variant_id, str(line.quantity), str(line.unit_price),
                   str(line.discount_amount)) for line in order.lines],
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    allowed = (
        "Shopify authentication failed", "Shopify configuration is unavailable",
        "Shopify catalogue request failed", "Shopify Draft Order creation was rejected",
        "Shopify permission is missing",
    )
    return next((message for message in allowed if message in text),
                "Shopify synchronization failed")


def _fixed_discount(discount: Decimal, title: str, reference: str) -> dict:
    """Return the 2026-07 DraftOrderAppliedDiscountInput fixed-amount shape."""
    if discount <= 0:
        raise ShopifyDraftSyncError("Discount amounts must be greater than zero")
    return {
        "title": title,
        "description": reference,
        "valueType": "FIXED_AMOUNT",
        # DraftOrderAppliedDiscountInput.value is GraphQL Float!, not a Money string.
        "value": float(discount),
    }


def _shopify_input(order: SalesOrder) -> dict:
    if order.order_status not in ELIGIBLE_ORDER_STATUSES:
        raise ShopifyDraftSyncError(f"An order in {order.order_status} cannot be synchronized")
    if not order.lines:
        raise ShopifyDraftSyncError("The order has no products")
    line_items = []
    line_discount_total = Decimal("0.00")
    has_taxable_product = False
    for line in order.lines:
        quantity = Decimal(str(line.quantity))
        if quantity != quantity.to_integral_value() or quantity <= 0:
            raise ShopifyDraftSyncError("Shopify Draft Orders require whole-number product quantities")
        try:
            current = resolve_inbox_product(line.shopify_product_id, line.shopify_variant_id)
        except ShopifyCatalogError as exc:
            raise ShopifyDraftSyncError(str(exc), status_code=503) from None
        if not current or not current.get("available"):
            raise ShopifyDraftSyncError(f"{line.product_name_snapshot} is no longer available")
        if _money(current.get("price")) != _money(line.unit_price):
            raise ShopifyDraftSyncError(
                f"The Shopify price for {line.product_name_snapshot} changed; update the Ledger draft first")
        if "taxable" not in current:
            raise ShopifyDraftSyncError(
                f"Shopify taxability for {line.product_name_snapshot} could not be verified")
        has_taxable_product = has_taxable_product or current["taxable"] is True
        # Shopify ignores DraftOrderLineItemInput.taxable when variantId is supplied. The
        # variant's live taxability and Shopify's configured tax engine are authoritative.
        item = {"variantId": line.shopify_variant_id, "quantity": int(quantity)}
        discount = _money(line.discount_amount)
        if discount:
            item["appliedDiscount"] = _fixed_discount(
                discount, "Ledger line discount", order.internal_order_number)
        line_discount_total += discount
        line_items.append(item)
    order_discount = _money(order.discount_total) - line_discount_total
    if order_discount < 0:
        raise ShopifyDraftSyncError("Ledger discount totals are inconsistent")
    if has_taxable_product and (not order.delivery_address or not order.city):
        raise ShopifyDraftSyncError(
            "A complete delivery address and city are required for Shopify tax calculation")
    if _money(order.tax_total) > 0 and not has_taxable_product:
        raise ShopifyDraftSyncError(
            "Ledger tax is nonzero, but Shopify reports every selected product as non-taxable")
    data = {
        "lineItems": line_items,
        "note": f"PixiePinks Ledger order {order.internal_order_number}",
        "tags": ["pixiepinks-ledger", _tag(order)],
        "customAttributes": [{"key": "PixiePinks Ledger Order", "value": order.internal_order_number}],
        "presentmentCurrencyCode": order.currency,
        "taxExempt": False,
        "phone": order.whatsapp_phone,
        "shippingAddress": {
            "firstName": order.delivery_contact_person or order.customer_name,
            "address1": order.delivery_address,
            "city": order.city, "province": order.district,
            "zip": order.postal_code, "countryCode": "LK", "phone": order.whatsapp_phone,
        },
    }
    data["shippingAddress"] = {key: value for key, value in data["shippingAddress"].items() if value}
    if order_discount:
        data["appliedDiscount"] = _fixed_discount(
            order_discount, "Ledger order discount", order.internal_order_number)
    if _money(order.shipping_total):
        data["shippingLine"] = {"title": "Delivery", "price": str(_money(order.shipping_total))}
    # Shopify calculates taxes from its configuration and the supplied address. The returned
    # total is always compared to Ledger, so a tax/configuration difference is never accepted silently.
    return data


def _find_existing(client: ShopifyCatalogClient, order: SalesOrder) -> list[dict]:
    data = client.graphql(FIND_QUERY, {"query": f"tag:{_tag(order)}"})
    return ((data.get("draftOrders") or {}).get("nodes") or [])


def _financial_status(order: SalesOrder, draft: dict) -> tuple[str, str | None]:
    money = (((draft.get("totalPriceSet") or {}).get("shopMoney")) or {})
    amount, currency = _money(money.get("amount")), money.get("currencyCode")
    tax_money = (((draft.get("totalTaxSet") or {}).get("shopMoney")) or {})
    tax, tax_currency = _money(tax_money.get("amount")), tax_money.get("currencyCode")
    differences = []
    if amount != _money(order.grand_total) or currency != order.currency:
        differences.append(
            f"Shopify total {currency or '?'} {amount} differs from Ledger "
            f"{order.currency} {_money(order.grand_total)}")
    if tax != _money(order.tax_total) or tax_currency != order.currency:
        differences.append(
            f"Shopify tax {tax_currency or '?'} {tax} differs from Ledger "
            f"{order.currency} {_money(order.tax_total)}")
    if differences:
        return "RECONCILIATION_REQUIRED", (
            "; ".join(differences))
    return "SYNCED", None


def _apply_remote(order: SalesOrder, event: ShopifySyncEvent, draft: dict) -> None:
    draft_id = draft.get("id")
    if not isinstance(draft_id, str) or not DRAFT_ORDER_GID.fullmatch(draft_id):
        raise ShopifyOutcomeUncertain(
            "Shopify returned an invalid Draft Order reference; manual reconciliation is required")
    status, warning = _financial_status(order, draft)
    order.shopify_draft_order_id = draft_id
    order.shopify_draft_order_name = draft.get("name")
    linked_order = draft.get("order") or {}
    if linked_order:
        order.shopify_order_id = linked_order.get("id")
        order.shopify_order_name = linked_order.get("name")
    order.shopify_sync_status = status
    order.shopify_sync_error = warning
    order.shopify_sync_updated_at = datetime.utcnow()
    event.external_reference = draft.get("id")
    event.status = status
    event.error_summary = warning
    event.processed_at = datetime.utcnow()
    event.claim_token = None
    event.claim_expires_at = None
    event.updated_at = datetime.utcnow()


def _claim_failed_event(db: Session, event_id: int, actor: str) -> str:
    """Atomically lease a known-safe failed operation without holding a transaction over HTTP."""
    now = datetime.utcnow()
    token = secrets.token_hex(32)
    claimed = db.query(ShopifySyncEvent).filter(
        ShopifySyncEvent.id == event_id,
        ShopifySyncEvent.status == "SYNC_FAILED",
        (ShopifySyncEvent.claim_token.is_(None)) |
        (ShopifySyncEvent.claim_expires_at < now),
    ).update({
        ShopifySyncEvent.status: "PROCESSING",
        ShopifySyncEvent.claim_token: token,
        ShopifySyncEvent.claim_expires_at: now + timedelta(seconds=CLAIM_LEASE_SECONDS),
        ShopifySyncEvent.attempts: ShopifySyncEvent.attempts + 1,
        ShopifySyncEvent.actor: actor,
        ShopifySyncEvent.error_summary: None,
        ShopifySyncEvent.updated_at: now,
    }, synchronize_session=False)
    db.commit()
    if claimed != 1:
        raise ShopifyDraftSyncError("Shopify Draft Order creation is already in progress")
    return token


def create_shopify_draft(db: Session, order: SalesOrder, actor: str,
                         client: ShopifyCatalogClient | None = None) -> tuple[SalesOrder, bool]:
    """Create at most one remote draft, reconciling by a deterministic Ledger tag first."""
    client = client or ShopifyCatalogClient()
    if order.shopify_draft_order_id:
        return order, False
    key = _operation_key(order)
    event = db.query(ShopifySyncEvent).filter_by(idempotency_key=key).one_or_none()
    if event and event.status in {"RECONCILIATION_REQUIRED", "UNCERTAIN"}:
        try:
            matches = _find_existing(client, order)
        except ShopifyCatalogError:
            raise ShopifyOutcomeUncertain(
                "The previous Shopify result is uncertain and could not be reconciled; do not retry yet") from None
        if len(matches) == 1:
            _apply_remote(order, event, matches[0]); db.commit(); db.refresh(order)
            return order, False
        raise ShopifyOutcomeUncertain(
            "The previous Shopify result remains uncertain and requires manual reconciliation")
    if event and event.status == "PROCESSING":
        # A concurrent worker owns the only durable operation key. It may already have written remotely.
        try:
            matches = _find_existing(client, order)
        except ShopifyCatalogError:
            matches = []
        if len(matches) == 1:
            _apply_remote(order, event, matches[0]); db.commit(); db.refresh(order)
            return order, False
        if event.claim_expires_at and event.claim_expires_at <= datetime.utcnow():
            event.status = order.shopify_sync_status = "RECONCILIATION_REQUIRED"
            event.error_summary = order.shopify_sync_error = (
                "The synchronization worker lease expired; reconcile Shopify before retrying")
            event.claim_token = None; event.claim_expires_at = None
            event.updated_at = order.shopify_sync_updated_at = datetime.utcnow(); db.commit()
            raise ShopifyOutcomeUncertain(
                "The previous Shopify attempt may have completed and requires manual reconciliation")
        raise ShopifyDraftSyncError("Shopify Draft Order creation is already in progress")
    if event is None:
        claim_token = secrets.token_hex(32)
        now = datetime.utcnow()
        event = ShopifySyncEvent(event_type=CREATE_EVENT, sales_order_id=order.id,
            direction="OUTBOUND", idempotency_key=key, payload_hash=_payload_hash(order),
            status="PROCESSING", attempts=1, actor=actor, claim_token=claim_token,
            claim_expires_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS), updated_at=now)
        db.add(event)
        order.shopify_sync_status = "SYNCING"; order.shopify_sync_error = None
        order.shopify_sync_updated_at = datetime.utcnow()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ShopifyDraftSyncError("Shopify Draft Order creation is already in progress") from None
    else:
        # A prior safe failure is reconciled before a retry and then atomically claimed.
        # Only the winner may issue a mutation; no transaction is held during Shopify I/O.
        try:
            matches = _find_existing(client, order)
        except ShopifyCatalogError:
            raise ShopifyDraftSyncError(
                "Shopify could not be reconciled before retry; no write was attempted", status_code=503) from None
        if len(matches) > 1:
            event.status = order.shopify_sync_status = "RECONCILIATION_REQUIRED"
            event.error_summary = order.shopify_sync_error = (
                "Multiple Shopify drafts match this Ledger order; manual reconciliation is required")
            event.updated_at = order.shopify_sync_updated_at = datetime.utcnow(); db.commit()
            raise ShopifyOutcomeUncertain(event.error_summary)
        if matches:
            _apply_remote(order, event, matches[0]); db.commit(); db.refresh(order)
            return order, False
        claim_token = _claim_failed_event(db, event.id, actor)
        event = db.query(ShopifySyncEvent).filter_by(id=event.id, claim_token=claim_token).one()
        order = db.get(SalesOrder, order.id)
        order.shopify_sync_status = "SYNCING"; order.shopify_sync_error = None
        order.shopify_sync_updated_at = datetime.utcnow(); db.commit()

    write_attempted = False
    try:
        matches = _find_existing(client, order)
        if len(matches) > 1:
            raise ShopifyOutcomeUncertain("Multiple Shopify drafts match this Ledger order; manual reconciliation is required")
        if matches:
            draft, created = matches[0], False
        else:
            write_attempted = True
            payload = client.graphql(CREATE_MUTATION, {"input": _shopify_input(order)})
            result = payload.get("draftOrderCreate") or {}
            errors = result.get("userErrors") or []
            if errors:
                message = " ".join(str(item.get("message", "")) for item in errors).casefold()
                if "access" in message or "permission" in message or "scope" in message:
                    raise ShopifyDraftSyncError("Shopify permission is missing; grant write_draft_orders")
                raise ShopifyDraftSyncError("Shopify Draft Order creation was rejected")
            draft, created = result.get("draftOrder"), True
            if not draft or not draft.get("id"):
                raise ShopifyOutcomeUncertain("Shopify did not return a Draft Order reference")
        _apply_remote(order, event, draft)
        try:
            db.commit(); db.refresh(order)
        except SQLAlchemyError:
            db.rollback()
            recovery = db.query(ShopifySyncEvent).filter_by(idempotency_key=key).one_or_none()
            linked = db.get(SalesOrder, order.id)
            if recovery and linked:
                recovery.status = "RECONCILIATION_REQUIRED"
                recovery.error_summary = "Shopify succeeded but Ledger persistence must be reconciled"
                recovery.claim_token = None
                recovery.claim_expires_at = None
                linked.shopify_sync_status = "RECONCILIATION_REQUIRED"
                linked.shopify_sync_error = recovery.error_summary
                linked.shopify_sync_updated_at = datetime.utcnow()
                try: db.commit()
                except SQLAlchemyError: db.rollback()
            raise ShopifyOutcomeUncertain(
                "Shopify may have created the draft, but Ledger persistence failed; reconcile before retrying") from None
        return order, created
    except ShopifyOutcomeUncertain as exc:
        event.status = order.shopify_sync_status = "RECONCILIATION_REQUIRED"
        event.error_summary = order.shopify_sync_error = str(exc)[:500]
        event.claim_token = None; event.claim_expires_at = None
        event.updated_at = order.shopify_sync_updated_at = datetime.utcnow(); db.commit()
        raise
    except ShopifyCatalogError as exc:
        # A transport/provider failure after the mutation starts can mean Shopify accepted it.
        # A failed read-only preflight is safe to retry later.
        message = _safe_error(exc)
        if not write_attempted or message in {
                "Shopify authentication failed", "Shopify configuration is unavailable"}:
            event.status = order.shopify_sync_status = "SYNC_FAILED"
        else:
            event.status = order.shopify_sync_status = "RECONCILIATION_REQUIRED"
        event.error_summary = order.shopify_sync_error = message
        event.claim_token = None; event.claim_expires_at = None
        event.updated_at = order.shopify_sync_updated_at = datetime.utcnow(); db.commit()
        if order.shopify_sync_status == "RECONCILIATION_REQUIRED":
            raise ShopifyOutcomeUncertain(
                "The Shopify result is uncertain; reconcile before retrying") from None
        raise ShopifyDraftSyncError(message, status_code=503) from None
    except ShopifyDraftSyncError as exc:
        event.status = order.shopify_sync_status = "SYNC_FAILED"
        event.error_summary = order.shopify_sync_error = str(exc)[:500]
        event.claim_token = None; event.claim_expires_at = None
        event.updated_at = order.shopify_sync_updated_at = datetime.utcnow(); db.commit()
        raise


def refresh_shopify_draft(db: Session, order: SalesOrder, actor: str,
                          client: ShopifyCatalogClient | None = None) -> SalesOrder:
    if not order.shopify_draft_order_id:
        raise ShopifyDraftSyncError("This Ledger order is not linked to a Shopify Draft Order")
    if not DRAFT_ORDER_GID.fullmatch(order.shopify_draft_order_id):
        raise ShopifyDraftSyncError(
            "The linked Shopify Draft Order reference is invalid and requires reconciliation")
    client = client or ShopifyCatalogClient()
    try:
        draft = client.graphql(GET_QUERY, {"id": order.shopify_draft_order_id}).get("draftOrder")
    except ShopifyCatalogError as exc:
        raise ShopifyDraftSyncError(_safe_error(exc), status_code=503) from None
    if not draft:
        order.shopify_sync_status = "RECONCILIATION_REQUIRED"
        order.shopify_sync_error = "The linked Shopify Draft Order could not be found"
    else:
        event = ShopifySyncEvent(event_type=REFRESH_EVENT, sales_order_id=order.id,
            direction="INBOUND", idempotency_key=f"shopify-draft-refresh:{order.id}:{datetime.utcnow().isoformat()}",
            payload_hash=hashlib.sha256(order.shopify_draft_order_id.encode()).hexdigest(),
            status="PROCESSING", attempts=1, actor=actor, updated_at=datetime.utcnow())
        db.add(event); _apply_remote(order, event, draft)
    order.shopify_sync_updated_at = datetime.utcnow(); db.commit(); db.refresh(order)
    return order


def shopify_admin_draft_url(order: SalesOrder, shop: str | None) -> str | None:
    if not order.shopify_draft_order_id or not shop or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", shop):
        return None
    match = DRAFT_ORDER_GID.fullmatch(order.shopify_draft_order_id)
    if not match:
        return None
    resource_id = match.group(1)
    return f"https://admin.shopify.com/store/{shop}/draft_orders/{resource_id}"
