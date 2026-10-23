"""Explicit, transaction-aware Shopify payment synchronization.

The module never changes Ledger receipts, accounting, inventory, fulfillment, or customers.
Every potentially ambiguous write is terminally quarantined until read-only reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (SalesOrder, SalesPayment, ShopifyPaymentSyncAuditEvent,
                    ShopifyPaymentSyncOperation)
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError

MONEY = Decimal("0.01")
ORDER_GID = re.compile(r"^gid://shopify/Order/[0-9]+$")
TRANSACTION_GID = re.compile(r"^gid://shopify/OrderTransaction/[0-9]+$")
PREFLIGHT_TTL = timedelta(minutes=5)
CLAIM_TTL = timedelta(minutes=2)
BLOCKING_STATES = {"PREFLIGHT_READY", "PROCESSING", "SYNCED", "OUTCOME_UNCERTAIN", "RECONCILIATION_REQUIRED"}

SCOPES_QUERY = """query LedgerInstalledScopes { currentAppInstallation { accessScopes { handle } } }"""
ORDER_QUERY = """
query LedgerPaymentSyncPreflight($id: ID!) {
  order(id: $id) {
    id name displayFinancialStatus canMarkAsPaid cancelledAt updatedAt
    totalPriceSet { shopMoney { amount currencyCode } }
    totalReceivedSet { shopMoney { amount currencyCode } }
    totalOutstandingSet { shopMoney { amount currencyCode } }
    totalRefundedSet { shopMoney { amount currencyCode } }
    transactionsCount { count }
    transactions(first: 100) {
      id kind status gateway createdAt manuallyCapturable
      amountSet { shopMoney { amount currencyCode } }
      parentTransaction { id }
    }
  }
}
"""
MARK_PAID_MUTATION = """
mutation LedgerMarkOrderAsPaid($input: OrderMarkAsPaidInput!) {
  orderMarkAsPaid(input: $input) {
    order { id name displayFinancialStatus canMarkAsPaid updatedAt
      totalPriceSet { shopMoney { amount currencyCode } }
      totalReceivedSet { shopMoney { amount currencyCode } }
      totalOutstandingSet { shopMoney { amount currencyCode } }
      totalRefundedSet { shopMoney { amount currencyCode } }
      transactionsCount { count }
      transactions(first: 100) { id kind status gateway createdAt manuallyCapturable
        amountSet { shopMoney { amount currencyCode } } parentTransaction { id } }
    }
    userErrors { field message }
  }
}
"""


class ShopifyPaymentSyncError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message); self.status_code = status_code


def _money(value, label="Shopify amount") -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        raise ShopifyPaymentSyncError(f"{label} is invalid", status_code=502) from None
    if not result.is_finite() or result < 0:
        raise ShopifyPaymentSyncError(f"{label} is invalid", status_code=502)
    return result


def _shop_money(order: dict, field: str) -> tuple[Decimal, str]:
    money = ((order.get(field) or {}).get("shopMoney") or {})
    return _money(money.get("amount")), str(money.get("currencyCode") or "")


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _ledger_state(db: Session, order: SalesOrder) -> tuple[Decimal, str, int]:
    rows = db.query(SalesPayment).filter_by(sales_order_id=order.id).order_by(SalesPayment.id).all()
    verified = [p for p in rows if p.verification_status == "VERIFIED"]
    pending = sum(1 for p in rows if p.verification_status == "PENDING")
    total = sum((Decimal(str(p.amount)) for p in verified), Decimal("0")).quantize(MONEY)
    fingerprint = _fingerprint([[p.id, str(Decimal(str(p.amount)).quantize(MONEY)), p.currency,
                                 p.verified_at.isoformat() if p.verified_at else None] for p in verified])
    return total, fingerprint, pending


def _transactions(remote: dict) -> tuple[list[dict], str]:
    transactions = remote.get("transactions")
    if not isinstance(transactions, list):
        raise ShopifyPaymentSyncError("Shopify transaction history is incomplete", status_code=502)
    count_value = remote.get("transactionsCount") or {}
    try:
        count = int(count_value.get("count"))
    except (TypeError, ValueError):
        raise ShopifyPaymentSyncError("Shopify transaction history count is unavailable", status_code=502) from None
    if count != len(transactions):
        raise ShopifyPaymentSyncError("Shopify transaction history is incomplete")
    normalized = []
    for tx in transactions:
        if not isinstance(tx, dict) or not TRANSACTION_GID.fullmatch(str(tx.get("id") or "")):
            raise ShopifyPaymentSyncError("Shopify returned an invalid transaction history", status_code=502)
        amount, currency = _shop_money(tx, "amountSet")
        item = {"id": tx["id"], "kind": str(tx.get("kind") or ""),
                "status": str(tx.get("status") or ""), "amount": str(amount),
                "currency": currency, "gateway": str(tx.get("gateway") or "")[:100],
                "manuallyCapturable": bool(tx.get("manuallyCapturable")),
                "parent": (tx.get("parentTransaction") or {}).get("id")}
        normalized.append(item)
    normalized.sort(key=lambda item: item["id"])
    return normalized, _fingerprint(normalized)


def _assert_transaction_safe(transactions: list[dict], currency: str) -> None:
    known_kinds = {"AUTHORIZATION", "CAPTURE", "SALE", "VOID", "REFUND", "EMV_AUTHORIZATION", "CHANGE"}
    for tx in transactions:
        if tx["currency"] != currency:
            raise ShopifyPaymentSyncError("Shopify transaction currency is inconsistent")
        if tx["kind"] not in known_kinds or tx["status"] not in {"SUCCESS", "FAILURE", "ERROR", "PENDING", "AWAITING_RESPONSE"}:
            raise ShopifyPaymentSyncError("Shopify transaction state is not safely recognized")
        if tx["manuallyCapturable"] or tx["status"] in {"PENDING", "AWAITING_RESPONSE"}:
            raise ShopifyPaymentSyncError("Shopify has pending or capturable payment activity")
        if tx["status"] == "SUCCESS" and tx["kind"] in {"AUTHORIZATION", "EMV_AUTHORIZATION"}:
            raise ShopifyPaymentSyncError("Shopify has an authorization that this workflow must not capture")
        if tx["status"] == "SUCCESS" and tx["kind"] in {"CAPTURE", "SALE", "REFUND"}:
            raise ShopifyPaymentSyncError("Shopify already has successful payment activity")


def _read_remote(client: ShopifyCatalogClient, order_id: str) -> tuple[dict, list[dict], str]:
    try:
        remote = client.graphql(ORDER_QUERY, {"id": order_id}).get("order")
    except ShopifyCatalogError:
        raise ShopifyPaymentSyncError("Shopify payment preflight is temporarily unavailable", status_code=503) from None
    if not remote or remote.get("id") != order_id:
        raise ShopifyPaymentSyncError("The linked Shopify Order could not be verified")
    transactions, fingerprint = _transactions(remote)
    return remote, transactions, fingerprint


def _validate(db: Session, order: SalesOrder, remote: dict, transactions: list[dict]) -> tuple[Decimal, str]:
    if order.order_status != "CONFIRMED" or order.payment_status != "PAID":
        raise ShopifyPaymentSyncError("Ledger order must be confirmed and fully paid")
    if order.shopify_lifecycle_status != "ORDER_LINKED" or not ORDER_GID.fullmatch(order.shopify_order_id or ""):
        raise ShopifyPaymentSyncError("The Shopify Order linkage is not verified")
    verified, ledger_fp, pending = _ledger_state(db, order)
    total = _money(order.grand_total, "Ledger total")
    if total <= 0 or verified != total or _money(order.amount_paid, "Ledger paid amount") != total:
        raise ShopifyPaymentSyncError("Verified Ledger receipts do not exactly equal the order total")
    if pending:
        raise ShopifyPaymentSyncError("Pending Ledger payment claims must be resolved first")
    if remote.get("cancelledAt"):
        raise ShopifyPaymentSyncError("The Shopify Order is cancelled")
    shop_total, currency = _shop_money(remote, "totalPriceSet")
    received, received_currency = _shop_money(remote, "totalReceivedSet")
    outstanding, outstanding_currency = _shop_money(remote, "totalOutstandingSet")
    refunded, refunded_currency = _shop_money(remote, "totalRefundedSet")
    if len({currency, received_currency, outstanding_currency, refunded_currency, order.currency}) != 1:
        raise ShopifyPaymentSyncError("Shopify and Ledger currencies do not match")
    if str(remote.get("displayFinancialStatus") or "") not in {"PENDING", "AUTHORIZED"}:
        raise ShopifyPaymentSyncError("Shopify financial status is not eligible")
    if shop_total != total or outstanding != verified:
        raise ShopifyPaymentSyncError("Shopify and Ledger payment totals do not match")
    if received != 0 or refunded != 0:
        raise ShopifyPaymentSyncError("Shopify already has payment or refund activity")
    if not remote.get("canMarkAsPaid"):
        raise ShopifyPaymentSyncError("Shopify does not permit this order to be marked paid")
    _assert_transaction_safe(transactions, currency)
    return verified, ledger_fp


def installed_scopes(client: ShopifyCatalogClient | None = None) -> set[str]:
    try:
        data = (client or ShopifyCatalogClient()).graphql(SCOPES_QUERY, {})
    except ShopifyCatalogError:
        raise ShopifyPaymentSyncError("Shopify permission check is temporarily unavailable", status_code=503) from None
    scopes = ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])
    return {str(item.get("handle")) for item in scopes if isinstance(item, dict) and item.get("handle")}


def _audit(db: Session, operation: ShopifyPaymentSyncOperation, action: str, actor: str, detail=None):
    db.add(ShopifyPaymentSyncAuditEvent(operation_id=operation.id, action=action,
        actor=actor, detail=str(detail)[:500] if detail else None))


def create_preflight(db: Session, order_id: int, actor: str, idempotency_key: str,
                     client: ShopifyCatalogClient | None = None) -> ShopifyPaymentSyncOperation:
    if not idempotency_key or len(idempotency_key) > 100:
        raise ShopifyPaymentSyncError("A valid idempotency key is required", status_code=422)
    existing = db.query(ShopifyPaymentSyncOperation).filter_by(idempotency_key=idempotency_key).one_or_none()
    if existing:
        if existing.sales_order_id != order_id:
            raise ShopifyPaymentSyncError("Idempotency key was used for another order")
        return existing
    order = db.query(SalesOrder).filter_by(id=order_id).with_for_update().one_or_none()
    if not order: raise ShopifyPaymentSyncError("Sales order not found", status_code=404)
    active = db.query(ShopifyPaymentSyncOperation).filter_by(active_shopify_order_id=order.shopify_order_id).one_or_none()
    if active: return active
    api = client or ShopifyCatalogClient()
    if "write_orders" not in installed_scopes(api):
        raise ShopifyPaymentSyncError("Shopify write_orders permission is not installed", status_code=403)
    remote, transactions, tx_fp = _read_remote(api, order.shopify_order_id or "")
    amount, ledger_fp = _validate(db, order, remote, transactions)
    outstanding, _ = _shop_money(remote, "totalOutstandingSet")
    operation = ShopifyPaymentSyncOperation(operation_id=str(uuid.uuid4()), idempotency_key=idempotency_key,
        sales_order_id=order.id, shopify_order_id=order.shopify_order_id,
        active_shopify_order_id=order.shopify_order_id, mutation_name="orderMarkAsPaid",
        intended_amount=amount, currency=order.currency, ledger_payment_fingerprint=ledger_fp,
        preflight_financial_status=remote["displayFinancialStatus"],
        preflight_shopify_outstanding=outstanding, preflight_transaction_fingerprint=tx_fp,
        status="PREFLIGHT_READY", preflight_expires_at=datetime.utcnow() + PREFLIGHT_TTL)
    db.add(operation)
    try:
        db.flush(); _audit(db, operation, "PREFLIGHT_APPROVED", actor); db.commit(); db.refresh(operation)
    except IntegrityError:
        db.rollback()
        active = db.query(ShopifyPaymentSyncOperation).filter_by(active_shopify_order_id=order.shopify_order_id).one_or_none()
        if active: return active
        raise ShopifyPaymentSyncError("Another payment synchronization operation already exists") from None
    return operation


def execute_operation(db: Session, order_id: int, operation_id: str, actor: str, confirmation: str,
                      client: ShopifyCatalogClient | None = None) -> ShopifyPaymentSyncOperation:
    if confirmation != "MARK SHOPIFY ORDER PAID":
        raise ShopifyPaymentSyncError("Exact confirmation text is required", status_code=422)
    operation = db.query(ShopifyPaymentSyncOperation).filter_by(
        operation_id=operation_id, sales_order_id=order_id).with_for_update().one_or_none()
    if not operation: raise ShopifyPaymentSyncError("Payment synchronization operation not found", status_code=404)
    if operation.status == "SYNCED": return operation
    if operation.status != "PREFLIGHT_READY":
        raise ShopifyPaymentSyncError("This payment synchronization operation cannot be executed")
    if operation.preflight_expires_at <= datetime.utcnow():
        operation.status = "FAILED_SAFE"; operation.active_shopify_order_id = None
        operation.error_summary = "Preflight expired; run a new check"
        _audit(db, operation, "PREFLIGHT_EXPIRED", actor); db.commit()
        raise ShopifyPaymentSyncError(operation.error_summary)
    order = db.query(SalesOrder).filter_by(id=order_id).with_for_update().one()
    api = client or ShopifyCatalogClient()
    if "write_orders" not in installed_scopes(api):
        raise ShopifyPaymentSyncError("Shopify write_orders permission is not installed", status_code=403)
    remote, transactions, tx_fp = _read_remote(api, operation.shopify_order_id)
    amount, ledger_fp = _validate(db, order, remote, transactions)
    if (ledger_fp != operation.ledger_payment_fingerprint or tx_fp != operation.preflight_transaction_fingerprint
            or amount != Decimal(str(operation.intended_amount))):
        operation.status = "FAILED_SAFE"; operation.active_shopify_order_id = None
        operation.error_summary = "Payment or Shopify state changed after preflight"
        _audit(db, operation, "PREFLIGHT_INVALIDATED", actor); db.commit()
        raise ShopifyPaymentSyncError(operation.error_summary)
    claim = secrets.token_hex(32); now = datetime.utcnow()
    operation.status = "PROCESSING"; operation.claim_token = claim
    operation.claim_expires_at = now + CLAIM_TTL; operation.attempts += 1
    operation.approved_by = actor; operation.approved_at = now
    _audit(db, operation, "MUTATION_CLAIMED", actor); db.commit()
    try:
        result = api.graphql(MARK_PAID_MUTATION, {"input": {"id": operation.shopify_order_id}})
    except ShopifyCatalogError:
        current = db.query(ShopifyPaymentSyncOperation).filter_by(operation_id=operation_id).with_for_update().one()
        if current.claim_token == claim:
            current.status = "OUTCOME_UNCERTAIN"; current.error_summary = "Shopify response was not conclusive; read-only recovery is required"
            current.claim_token = None; current.claim_expires_at = None
            _audit(db, current, "OUTCOME_UNCERTAIN", actor); db.commit()
        raise ShopifyPaymentSyncError("Shopify outcome is uncertain; do not retry", status_code=503) from None
    payload = result.get("orderMarkAsPaid") or {}
    errors = payload.get("userErrors") or []
    if errors:
        message = "; ".join(str(e.get("message") or "Shopify rejected the operation") for e in errors[:3])[:500]
        current = db.query(ShopifyPaymentSyncOperation).filter_by(operation_id=operation_id).with_for_update().one()
        current.status = "FAILED_SAFE"; current.active_shopify_order_id = None
        current.error_summary = message; current.claim_token = None; current.claim_expires_at = None
        _audit(db, current, "SHOPIFY_REJECTED", actor, message); db.commit()
        raise ShopifyPaymentSyncError(message, status_code=422)
    return _readback_finalize(db, operation_id, claim, actor, api, transaction_before=tx_fp)


def _readback_finalize(db, operation_id, claim, actor, client, transaction_before):
    try:
        remote, transactions, tx_fp = _read_remote(client, db.query(ShopifyPaymentSyncOperation).filter_by(operation_id=operation_id).one().shopify_order_id)
    except ShopifyPaymentSyncError:
        current = db.query(ShopifyPaymentSyncOperation).filter_by(operation_id=operation_id).with_for_update().one()
        current.status = "OUTCOME_UNCERTAIN"; current.error_summary = "Shopify mutation returned but readback failed"
        current.claim_token = None; current.claim_expires_at = None
        _audit(db, current, "READBACK_FAILED", actor); db.commit()
        raise ShopifyPaymentSyncError("Shopify readback failed; do not retry", status_code=503) from None
    current = db.query(ShopifyPaymentSyncOperation).filter_by(operation_id=operation_id).with_for_update().one()
    if current.claim_token != claim: raise ShopifyPaymentSyncError("Payment synchronization claim changed", status_code=503)
    outstanding, currency = _shop_money(remote, "totalOutstandingSet")
    received, received_currency = _shop_money(remote, "totalReceivedSet")
    before_ids = set()  # the preflight fingerprint prevents changes; response IDs are compared via created successful set below
    successes = [t for t in transactions if t["status"] == "SUCCESS" and t["kind"] in {"CAPTURE", "SALE"}
                 and _money(t["amount"]) == Decimal(str(current.intended_amount)) and t["currency"] == current.currency]
    if (remote.get("displayFinancialStatus") == "PAID" and outstanding == 0
            and received == Decimal(str(current.intended_amount)) and currency == received_currency == current.currency
            and len(successes) == 1 and tx_fp != transaction_before):
        current.status = "SYNCED"; current.shopify_transaction_id = successes[0]["id"]
        current.completed_at = datetime.utcnow(); current.error_summary = None
    else:
        current.status = "RECONCILIATION_REQUIRED"
        current.error_summary = "Shopify payment readback did not prove exactly one matching transaction"
    current.claim_token = None; current.claim_expires_at = None
    _audit(db, current, "READBACK_COMPLETED", actor, current.status); db.commit(); db.refresh(current)
    return current


def recover_operation(db: Session, order_id: int, operation_id: str, actor: str,
                      client: ShopifyCatalogClient | None = None) -> ShopifyPaymentSyncOperation:
    operation = db.query(ShopifyPaymentSyncOperation).filter_by(
        operation_id=operation_id, sales_order_id=order_id).with_for_update().one_or_none()
    if not operation: raise ShopifyPaymentSyncError("Payment synchronization operation not found", status_code=404)
    if operation.status == "SYNCED": return operation
    if operation.status not in {"OUTCOME_UNCERTAIN", "RECONCILIATION_REQUIRED", "PROCESSING"}:
        raise ShopifyPaymentSyncError("This operation does not require recovery")
    if operation.status == "PROCESSING" and operation.claim_expires_at and operation.claim_expires_at > datetime.utcnow():
        raise ShopifyPaymentSyncError("Payment synchronization is still in progress", status_code=503)
    remote, transactions, _ = _read_remote(client or ShopifyCatalogClient(), operation.shopify_order_id)
    outstanding, currency = _shop_money(remote, "totalOutstandingSet")
    received, received_currency = _shop_money(remote, "totalReceivedSet")
    matches = [t for t in transactions if t["status"] == "SUCCESS" and t["kind"] in {"CAPTURE", "SALE"}
               and _money(t["amount"]) == Decimal(str(operation.intended_amount)) and t["currency"] == operation.currency]
    if (remote.get("displayFinancialStatus") == "PAID" and outstanding == 0
            and received == Decimal(str(operation.intended_amount)) and currency == received_currency == operation.currency
            and len(matches) == 1):
        operation.status = "SYNCED"; operation.shopify_transaction_id = matches[0]["id"]
        operation.completed_at = datetime.utcnow(); operation.error_summary = None
    else:
        operation.status = "RECONCILIATION_REQUIRED"
        operation.error_summary = "Read-only recovery could not prove exactly one matching Shopify transaction"
    operation.claim_token = None; operation.claim_expires_at = None
    _audit(db, operation, "RECOVERY_READBACK", actor, operation.status); db.commit(); db.refresh(operation)
    return operation
