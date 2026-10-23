"""One-way, read-only Shopify Order lifecycle synchronization.

This module links an existing Shopify Order to an existing Ledger Sales Order.
It never creates/completes Shopify resources or changes payment, fulfillment,
inventory, customer, or accounting state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import SalesOrder, SalesOrderStatusHistory, ShopifyWebhookEvent
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError

ORDER_GID = re.compile(r"^gid://shopify/Order/([0-9]+)$")
DRAFT_GID = re.compile(r"^gid://shopify/DraftOrder/([0-9]+)$")
LEDGER_NUMBER = re.compile(r"^SO-[0-9]{8}-[A-F0-9]{8}$")
LEDGER_TAG = re.compile(r"^ledger-(SO-[0-9]{8}-[A-F0-9]{8})$", re.I)

RECONCILE_QUERY = """
query LedgerDraftLifecycle($id: ID!) {
  draftOrder(id: $id) { id name order { id name createdAt } }
}
"""


class ShopifyLifecycleError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 409, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def verify_shopify_webhook(raw_body: bytes, supplied: str, secret: str | None) -> bool:
    if not secret or not supplied:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, supplied)


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _order_gid(payload: dict) -> str | None:
    value = payload.get("admin_graphql_api_id")
    if isinstance(value, str) and ORDER_GID.fullmatch(value):
        return value
    numeric = payload.get("id")
    if isinstance(numeric, int) or (isinstance(numeric, str) and numeric.isdigit()):
        return f"gid://shopify/Order/{numeric}"
    return None


def _ledger_references(payload: dict) -> set[str]:
    references: set[str] = set()
    tags = payload.get("tags")
    if isinstance(tags, str):
        for tag in tags.split(","):
            match = LEDGER_TAG.fullmatch(tag.strip())
            if match:
                references.add(match.group(1).upper())
    note = payload.get("note")
    if isinstance(note, str):
        match = re.search(r"PixiePinks Ledger order (SO-[0-9]{8}-[A-F0-9]{8})", note, re.I)
        if match:
            references.add(match.group(1).upper())
    for attribute in payload.get("note_attributes") or []:
        if (isinstance(attribute, dict)
                and attribute.get("name") == "PixiePinks Ledger Order"):
            value = str(attribute.get("value") or "").upper()
            if LEDGER_NUMBER.fullmatch(value):
                references.add(value)
    return references


def _advance_confirmed_order(db: Session, order: SalesOrder, actor: str) -> None:
    """Advance only through existing valid confirmation transitions."""
    steps = []
    if order.order_status == "DRAFT":
        steps.append(("DRAFT", "AWAITING_CUSTOMER_CONFIRMATION"))
        steps.append(("AWAITING_CUSTOMER_CONFIRMATION", "CONFIRMED"))
    elif order.order_status == "AWAITING_CUSTOMER_CONFIRMATION":
        steps.append((order.order_status, "CONFIRMED"))
    elif order.order_status in {"AWAITING_PAYMENT", "PAYMENT_VERIFICATION", "CANCELLED", "RETURNED"}:
        order.shopify_lifecycle_status = "RECONCILIATION_REQUIRED"
        order.shopify_lifecycle_error = (
            f"Shopify created an Order while Ledger status is {order.order_status}; staff review is required")
        return
    for previous, target in steps:
        db.add(SalesOrderStatusHistory(sales_order_id=order.id, previous_status=previous,
            new_status=target, changed_by=actor,
            reason="Shopify confirmed creation of the Order from the linked Draft Order"))
        order.order_status = target
    if steps:
        order.confirmed_by = actor


def _link_order(db: Session, order: SalesOrder, order_gid: str, order_name: str | None,
                created_at: datetime | None, actor: str) -> None:
    if not ORDER_GID.fullmatch(order_gid):
        raise ShopifyLifecycleError("Shopify returned an invalid Order reference", status_code=422)
    if order.shopify_order_id and order.shopify_order_id != order_gid:
        order.shopify_lifecycle_status = "RECONCILIATION_REQUIRED"
        order.shopify_lifecycle_error = "A different Shopify Order is already linked; staff review is required"
        raise ShopifyLifecycleError(order.shopify_lifecycle_error)
    now = datetime.utcnow()
    order.shopify_order_id = order_gid
    order.shopify_order_name = str(order_name or "")[:100] or None
    order.shopify_order_created_at = created_at
    order.shopify_order_linked_at = order.shopify_order_linked_at or now
    order.shopify_source_draft_order_id = order.shopify_draft_order_id
    order.shopify_lifecycle_synced_at = now
    order.shopify_lifecycle_status = "ORDER_LINKED"
    order.shopify_lifecycle_error = None
    _advance_confirmed_order(db, order, actor)


def process_orders_create(db: Session, payload: dict, *, webhook_id: str,
                          shop_domain: str, raw_body: bytes,
                          client: ShopifyCatalogClient | None = None) -> str:
    """Persist and idempotently process one authenticated orders/create delivery."""
    if not webhook_id or len(webhook_id) > 255:
        raise ShopifyLifecycleError("Shopify webhook delivery ID is missing", status_code=400)
    now = datetime.utcnow()
    claim_token = secrets.token_hex(32)
    event = (db.query(ShopifyWebhookEvent).filter_by(webhook_id=webhook_id)
             .with_for_update().one_or_none())
    if event and event.status in {"PROCESSED", "IGNORED", "RECONCILIATION_REQUIRED"}:
        return event.status
    if event and event.status == "PROCESSING" and event.claim_expires_at and event.claim_expires_at > now:
        raise ShopifyLifecycleError("Shopify webhook processing is already in progress",
                                    status_code=503, retryable=True)
    if event:
        event.status = "PROCESSING"
        event.attempts += 1
        event.claim_token = claim_token
        event.claim_expires_at = now + timedelta(seconds=30)
    else:
        event = ShopifyWebhookEvent(webhook_id=webhook_id, topic="orders/create",
            shop_domain=shop_domain[:255], payload_hash=hashlib.sha256(raw_body).hexdigest(),
            status="PROCESSING", attempts=1, claim_token=claim_token,
            claim_expires_at=now + timedelta(seconds=30))
        db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ShopifyLifecycleError("Shopify webhook processing is already in progress",
                                    status_code=503, retryable=True) from None

    order_gid = _order_gid(payload)
    references = _ledger_references(payload)
    candidates = []
    if order_gid:
        candidates.extend(db.query(SalesOrder).filter_by(shopify_order_id=order_gid).all())
    if references:
        candidates.extend(db.query(SalesOrder).filter(
            SalesOrder.internal_order_number.in_(references)).all())
    candidates = list({candidate.id: candidate for candidate in candidates}.values())
    if not order_gid:
        outcome = "IGNORED"; error = "Webhook has no valid Shopify Order reference"
    elif not candidates:
        outcome = "IGNORED"; error = "No Ledger Sales Order reference matched"
    elif len(candidates) != 1:
        outcome = "RECONCILIATION_REQUIRED"
        error = "Multiple Ledger Sales Orders matched the Shopify Order references"
    else:
        candidate_id = candidates[0].id
        candidate_draft_id = candidates[0].shopify_draft_order_id
        if references and candidates[0].internal_order_number not in references:
            outcome = "RECONCILIATION_REQUIRED"
            error = "Shopify Order references conflict with the linked Ledger order"
        elif not candidate_draft_id or not DRAFT_GID.fullmatch(candidate_draft_id):
            outcome = "RECONCILIATION_REQUIRED"
            error = "The matched Ledger order has no verified Shopify Draft reference"
        else:
            try:
                remote = (client or ShopifyCatalogClient()).graphql(
                    RECONCILE_QUERY, {"id": candidate_draft_id}).get("draftOrder")
            except ShopifyCatalogError:
                event = db.query(ShopifyWebhookEvent).filter_by(webhook_id=webhook_id).one()
                if event.claim_token == claim_token:
                    event.status = "FAILED_RETRYABLE"
                    event.error_summary = "Shopify Draft relationship verification is temporarily unavailable"
                    event.claim_token = None; event.claim_expires_at = None; db.commit()
                raise ShopifyLifecycleError("Shopify lifecycle verification is unavailable",
                                            status_code=503, retryable=True) from None
            remote_order = remote.get("order") if remote and remote.get("id") == candidate_draft_id else None
            if not remote_order or remote_order.get("id") != order_gid:
                outcome = "RECONCILIATION_REQUIRED"
                error = "The Shopify Order could not be verified against the linked Draft Order"
            else:
                outcome = "LINK"; error = None

    event = (db.query(ShopifyWebhookEvent).filter_by(webhook_id=webhook_id)
             .with_for_update().one())
    if event.claim_token != claim_token:
        raise ShopifyLifecycleError("Shopify webhook claim changed during processing",
                                    status_code=503, retryable=True)
    event.shopify_order_id = order_gid
    if candidates and len(candidates) == 1:
        event.sales_order_id = candidates[0].id
    if outcome == "LINK":
        order = (db.query(SalesOrder).filter(SalesOrder.id == candidate_id)
                 .with_for_update().one())
        try:
            _link_order(db, order, order_gid, payload.get("name"),
                        _parse_datetime(payload.get("created_at")), "shopify:webhook")
            outcome = ("RECONCILIATION_REQUIRED"
                       if order.shopify_lifecycle_status == "RECONCILIATION_REQUIRED" else "PROCESSED")
            error = order.shopify_lifecycle_error
        except ShopifyLifecycleError as exc:
            outcome = "RECONCILIATION_REQUIRED"; error = str(exc)[:500]
    event.status = outcome
    event.error_summary = error
    event.claim_token = None; event.claim_expires_at = None
    event.processed_at = datetime.utcnow()
    db.commit()
    return event.status


def reconcile_order(db: Session, order: SalesOrder, actor: str,
                    client: ShopifyCatalogClient | None = None) -> SalesOrder:
    """Read the linked Draft and recover its completed Shopify Order, if any."""
    if not order.shopify_draft_order_id or not DRAFT_GID.fullmatch(order.shopify_draft_order_id):
        raise ShopifyLifecycleError("This Ledger order has no valid Shopify Draft reference")
    client = client or ShopifyCatalogClient()
    try:
        draft = client.graphql(RECONCILE_QUERY, {"id": order.shopify_draft_order_id}).get("draftOrder")
    except ShopifyCatalogError:
        raise ShopifyLifecycleError("Shopify lifecycle reconciliation is unavailable",
                                    status_code=503, retryable=True) from None
    if not draft or draft.get("id") != order.shopify_draft_order_id:
        raise ShopifyLifecycleError("The linked Shopify Draft could not be verified")
    remote_order = draft.get("order")
    if not remote_order:
        order.shopify_lifecycle_status = "DRAFT_OPEN"
        order.shopify_lifecycle_error = None
        order.shopify_lifecycle_synced_at = datetime.utcnow(); db.commit(); db.refresh(order)
        return order
    _link_order(db, order, str(remote_order.get("id") or ""), remote_order.get("name"),
                _parse_datetime(remote_order.get("createdAt")), actor)
    db.add(ShopifyWebhookEvent(webhook_id=f"manual:{order.id}:{datetime.utcnow().isoformat()}",
        topic="manual/reconcile", shop_domain="configured-store", payload_hash="0" * 64,
        shopify_order_id=order.shopify_order_id, sales_order_id=order.id,
        status=("RECONCILIATION_REQUIRED" if order.shopify_lifecycle_error else "PROCESSED"),
        attempts=1, error_summary=order.shopify_lifecycle_error, processed_at=datetime.utcnow()))
    db.commit(); db.refresh(order)
    return order


def shopify_admin_order_url(order: SalesOrder, shop: str | None) -> str | None:
    if not order.shopify_order_id or not shop or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", shop):
        return None
    match = ORDER_GID.fullmatch(order.shopify_order_id)
    return (f"https://admin.shopify.com/store/{shop}/orders/{match.group(1)}" if match else None)
