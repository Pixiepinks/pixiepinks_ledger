"""Audited Sales Order payment claims and verification.

This module records operational receipt verification only. It never posts accounting,
changes inventory/fulfillment, writes Shopify, or sends customer notifications.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import SalesOrder, SalesPayment, SalesPaymentAuditEvent
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError

MONEY = Decimal("0.01")
PAYMENT_METHODS = {"BANK_TRANSFER", "CASH", "COD", "CARD_PAYMENT", "PAYMENT_LINK"}
PAYMENT_CATEGORIES = {
    "BANK_TRANSFER": "BANK_TRANSFER", "CASH": "CASH", "COD": "COD",
    "CARD_PAYMENT": "ONLINE_PAYMENT", "PAYMENT_LINK": "ONLINE_PAYMENT",
}
REFERENCE_REQUIRED = {"BANK_TRANSFER", "CARD_PAYMENT", "PAYMENT_LINK"}
PAYMENT_STATES = {"PENDING", "VERIFIED", "REJECTED"}
ORDER_GID = re.compile(r"^gid://shopify/Order/[0-9]+$")

SHOPIFY_PAYMENT_QUERY = """
query LedgerOrderPaymentStatus($id: ID!) {
  order(id: $id) {
    id name displayFinancialStatus updatedAt
    totalPriceSet { shopMoney { amount currencyCode } }
    totalReceivedSet { shopMoney { amount currencyCode } }
    totalOutstandingSet { shopMoney { amount currencyCode } }
    totalRefundedSet { shopMoney { amount currencyCode } }
  }
}
"""


class PaymentValidationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _money(value, field: str = "amount") -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise PaymentValidationError(f"{field} must be a valid amount") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999.99"):
        raise PaymentValidationError(f"{field} must be greater than zero and within the allowed range")
    return amount


def _parse_date(value) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PaymentValidationError("payment_date must be a valid ISO date") from None


def _text(value, maximum: int) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if len(result) > maximum:
        raise PaymentValidationError(f"Text value must be at most {maximum} characters")
    return result or None


def _reference(value) -> str | None:
    result = _text(value, 255)
    if result and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:/#-]*", result):
        raise PaymentValidationError("payment_reference contains unsupported characters")
    return result


def _deduplication_key(method: str, reference: str | None) -> str | None:
    if not reference:
        return None
    normalized = re.sub(r"\s+", "", reference).upper()
    return hashlib.sha256(f"{method}:{normalized}".encode()).hexdigest()


def _payload_hash(payload: dict) -> str:
    bounded = {key: payload.get(key) for key in
               ("payment_method", "amount", "payment_date", "payment_reference", "notes")}
    return hashlib.sha256(json.dumps(bounded, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def verified_total(db: Session, order_id: int) -> Decimal:
    value = db.query(func.coalesce(func.sum(SalesPayment.amount), 0)).filter(
        SalesPayment.sales_order_id == order_id,
        SalesPayment.verification_status == "VERIFIED").scalar()
    return Decimal(str(value or 0)).quantize(MONEY)


def _derive_summary(db: Session, order: SalesOrder) -> None:
    # Decisions and newly-created claims must be visible to aggregate queries even
    # when the application's Session factory has autoflush disabled.
    db.flush()
    paid = verified_total(db, order.id)
    pending = db.query(SalesPayment.id).filter_by(
        sales_order_id=order.id, verification_status="PENDING").first() is not None
    total = Decimal(str(order.grand_total)).quantize(MONEY)
    if paid > total:
        raise PaymentValidationError("Verified payments exceed the order total", status_code=409)
    order.amount_paid = paid
    if paid == total and total > 0:
        order.payment_status = "PAID"
    elif paid > 0:
        order.payment_status = "PARTIALLY_PAID"
    elif pending:
        order.payment_status = "PAYMENT_VERIFICATION"
    else:
        order.payment_status = "UNPAID"
    order.updated_at = datetime.utcnow()


def _audit(db: Session, payment: SalesPayment, action: str, previous: str | None,
           new: str, actor: str, idempotency_key: str, reason: str | None = None) -> None:
    db.add(SalesPaymentAuditEvent(
        sales_payment_id=payment.id, sales_order_id=payment.sales_order_id,
        action=action, previous_status=previous, new_status=new, actor=actor,
        reason=reason, idempotency_key=idempotency_key))


def record_payment_claim(db: Session, order_id: int, payload: dict, actor: str,
                         idempotency_key: str) -> tuple[SalesPayment, bool]:
    if not isinstance(payload, dict) or not idempotency_key or len(idempotency_key) > 100:
        raise PaymentValidationError("A valid idempotency key and payload are required")
    request_hash = _payload_hash(payload)
    existing = db.query(SalesPayment).filter_by(idempotency_key=idempotency_key).one_or_none()
    if existing:
        if existing.sales_order_id != order_id or existing.request_payload_hash != request_hash:
            raise PaymentValidationError("Idempotency key was already used for a different payment claim",
                                         status_code=409)
        return existing, False
    order = db.query(SalesOrder).filter_by(id=order_id).with_for_update().one_or_none()
    if not order:
        raise PaymentValidationError("Sales order not found", status_code=404)
    if order.order_status in {"CANCELLED", "RETURNED"}:
        raise PaymentValidationError("Payments cannot be recorded for this order", status_code=409)
    method = str(payload.get("payment_method") or "").upper()
    if method not in PAYMENT_METHODS:
        raise PaymentValidationError("Unsupported payment method")
    amount = _money(payload.get("amount"))
    reference = _reference(payload.get("payment_reference"))
    if method in REFERENCE_REQUIRED and not reference:
        raise PaymentValidationError(f"payment_reference is required for {method}")
    deduplication_key = _deduplication_key(method, reference)
    if deduplication_key and db.query(SalesPayment.id).filter_by(
            deduplication_key=deduplication_key).first():
        raise PaymentValidationError("This transaction reference has already been recorded",
                                     status_code=409)
    committed = verified_total(db, order.id)
    pending = db.query(func.coalesce(func.sum(SalesPayment.amount), 0)).filter(
        SalesPayment.sales_order_id == order.id,
        SalesPayment.verification_status == "PENDING").scalar()
    if committed + Decimal(str(pending or 0)) + amount > Decimal(str(order.grand_total)):
        raise PaymentValidationError("Payment claim exceeds the outstanding order balance",
                                     status_code=409)
    payment = SalesPayment(
        sales_order_id=order.id, payment_reference=reference,
        idempotency_key=idempotency_key, request_payload_hash=request_hash,
        deduplication_key=deduplication_key, payment_method=method, amount=amount,
        currency=order.currency, payment_date=_parse_date(payload.get("payment_date")),
        verification_status="PENDING", source="STAFF_RECORDED", claimed_by=actor,
        notes=_text(payload.get("notes"), 1000))
    db.add(payment)
    try:
        db.flush()
        _audit(db, payment, "CLAIM_RECORDED", None, "PENDING", actor,
               f"claim:{idempotency_key}")
        _derive_summary(db, order)
        db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback()
        existing = db.query(SalesPayment).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing and existing.sales_order_id == order_id and existing.request_payload_hash == request_hash:
            return existing, False
        raise PaymentValidationError("Payment claim conflicts with an existing transaction",
                                     status_code=409) from None
    return payment, True


def decide_payment(db: Session, order_id: int, payment_id: int, *, approve: bool,
                   actor: str, idempotency_key: str, reason: str | None = None) -> tuple[SalesPayment, bool]:
    if not idempotency_key or len(idempotency_key) > 100:
        raise PaymentValidationError("A valid idempotency key is required")
    audit_key = f"decision:{idempotency_key}"
    prior = db.query(SalesPaymentAuditEvent).filter_by(idempotency_key=audit_key).one_or_none()
    if prior:
        if prior.sales_payment_id != payment_id or prior.action != (
                "CLAIM_VERIFIED" if approve else "CLAIM_REJECTED"):
            raise PaymentValidationError("Idempotency key was already used for a different action",
                                         status_code=409)
        payment = db.get(SalesPayment, payment_id)
        return payment, False
    order = db.query(SalesOrder).filter_by(id=order_id).with_for_update().one_or_none()
    payment = db.query(SalesPayment).filter_by(
        id=payment_id, sales_order_id=order_id).with_for_update().one_or_none()
    if not order or not payment:
        raise PaymentValidationError("Payment claim not found", status_code=404)
    if payment.verification_status != "PENDING":
        raise PaymentValidationError("Only pending payment claims can be decided", status_code=409)
    now = datetime.utcnow()
    if approve:
        if verified_total(db, order.id) + Decimal(str(payment.amount)) > Decimal(str(order.grand_total)):
            raise PaymentValidationError("Verification would exceed the order total", status_code=409)
        payment.verification_status = "VERIFIED"
        payment.verified_by = actor; payment.verified_at = now
        action, target = "CLAIM_VERIFIED", "VERIFIED"
    else:
        rejection_reason = _text(reason, 500)
        if not rejection_reason:
            raise PaymentValidationError("A rejection reason is required")
        payment.verification_status = "REJECTED"
        payment.rejected_by = actor; payment.rejected_at = now
        payment.rejection_reason = rejection_reason
        action, target = "CLAIM_REJECTED", "REJECTED"
    payment.updated_at = now
    _audit(db, payment, action, "PENDING", target, actor, audit_key,
           payment.rejection_reason if not approve else None)
    _derive_summary(db, order)
    try:
        db.commit(); db.refresh(payment)
    except IntegrityError:
        db.rollback()
        prior = db.query(SalesPaymentAuditEvent).filter_by(idempotency_key=audit_key).one_or_none()
        if prior and prior.sales_payment_id == payment_id:
            return db.get(SalesPayment, payment_id), False
        raise PaymentValidationError("Payment decision conflicts with another request",
                                     status_code=409) from None
    return payment, True


def refresh_shopify_payment_status(db: Session, order: SalesOrder,
                                   client: ShopifyCatalogClient | None = None) -> SalesOrder:
    if not order.shopify_order_id or not ORDER_GID.fullmatch(order.shopify_order_id):
        raise PaymentValidationError("This order has no valid Shopify Order reference", status_code=409)
    try:
        remote = (client or ShopifyCatalogClient()).graphql(
            SHOPIFY_PAYMENT_QUERY, {"id": order.shopify_order_id}).get("order")
    except ShopifyCatalogError:
        raise PaymentValidationError("Shopify payment status is temporarily unavailable",
                                     status_code=503) from None
    if not remote or remote.get("id") != order.shopify_order_id:
        raise PaymentValidationError("The linked Shopify Order could not be verified", status_code=409)
    def shop_money(field):
        value = ((remote.get(field) or {}).get("shopMoney") or {})
        try:
            amount = Decimal(str(value.get("amount"))).quantize(MONEY)
        except (InvalidOperation, TypeError):
            raise PaymentValidationError("Shopify returned invalid payment totals", status_code=502) from None
        return amount, value.get("currencyCode")
    received, currency = shop_money("totalReceivedSet")
    outstanding, outstanding_currency = shop_money("totalOutstandingSet")
    refunded, refunded_currency = shop_money("totalRefundedSet")
    financial = str(remote.get("displayFinancialStatus") or "UNKNOWN")[:40]
    warnings = []
    if len({currency, outstanding_currency, refunded_currency, order.currency}) != 1:
        warnings.append("Shopify payment currency differs from Ledger")
    if financial == "PAID" and order.payment_status != "PAID":
        warnings.append("Shopify reports paid, but Ledger receipt verification is incomplete")
    if financial in {"REFUNDED", "PARTIALLY_REFUNDED", "VOIDED"}:
        warnings.append(f"Shopify reports {financial.replace('_', ' ').lower()}; staff review is required")
    if received != Decimal(str(order.amount_paid)).quantize(MONEY):
        warnings.append("Shopify received total differs from verified Ledger payments")
    order.shopify_financial_status = financial
    order.shopify_total_received = received
    order.shopify_total_outstanding = outstanding
    order.shopify_total_refunded = refunded
    order.shopify_payment_currency = currency
    order.shopify_payment_status_refreshed_at = datetime.utcnow()
    order.shopify_payment_reconciliation_status = "WARNING" if warnings else "MATCHED"
    order.shopify_payment_reconciliation_warning = "; ".join(warnings)[:500] or None
    db.commit(); db.refresh(order)
    return order
