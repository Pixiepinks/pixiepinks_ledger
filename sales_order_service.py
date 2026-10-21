"""Local commercial sales-order operations. No Shopify, inventory, or GL side effects."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import SalesOrder, SalesOrderLine, SalesOrderStatusHistory

MONEY = Decimal("0.01")
QUANTITY = Decimal("0.001")
ORDER_STATUSES = {
    "DRAFT", "AWAITING_CUSTOMER_CONFIRMATION", "AWAITING_PAYMENT", "PAYMENT_VERIFICATION",
    "CONFIRMED", "PROCESSING", "DISPATCHED", "DELIVERED", "CANCELLED", "RETURNED",
}
TRANSITIONS = {
    "DRAFT": {"AWAITING_CUSTOMER_CONFIRMATION", "CANCELLED"},
    "AWAITING_CUSTOMER_CONFIRMATION": {"DRAFT", "AWAITING_PAYMENT", "CONFIRMED", "CANCELLED"},
    "AWAITING_PAYMENT": {"PAYMENT_VERIFICATION", "CANCELLED"},
    "PAYMENT_VERIFICATION": {"AWAITING_PAYMENT", "CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"DISPATCHED", "CANCELLED"},
    "DISPATCHED": {"DELIVERED", "RETURNED"},
    "DELIVERED": {"RETURNED"},
    "CANCELLED": set(), "RETURNED": set(),
}

class SalesOrderValidationError(ValueError):
    pass

class InvalidOrderTransition(SalesOrderValidationError):
    pass

@dataclass(frozen=True)
class CalculatedLine:
    quantity: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal

@dataclass(frozen=True)
class OrderTotals:
    lines: tuple[CalculatedLine, ...]
    subtotal: Decimal
    discount_total: Decimal
    shipping_total: Decimal
    tax_total: Decimal
    grand_total: Decimal


def _decimal(value: Any, field: str, *, quantum: Decimal = MONEY, minimum: Decimal = Decimal("0"),
             maximum: Decimal = Decimal("999999999999.99")) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise SalesOrderValidationError(f"{field} must be a valid decimal") from None
    if not result.is_finite() or result < minimum or result > maximum:
        raise SalesOrderValidationError(f"{field} is outside the allowed range")
    return result


def calculate_order(lines: list[dict], order_discount: Any = 0, shipping: Any = 0,
                    tax: Any = 0) -> OrderTotals:
    if not isinstance(lines, list) or not lines:
        raise SalesOrderValidationError("At least one order line is required")
    calculated = []
    subtotal = line_discounts = line_taxes = Decimal("0.00")
    for index, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise SalesOrderValidationError(f"Line {index + 1} is invalid")
        quantity = _decimal(raw.get("quantity"), f"Line {index + 1} quantity",
                            quantum=QUANTITY, minimum=QUANTITY,
                            maximum=Decimal("99999999999.999"))
        unit_price = _decimal(raw.get("unit_price"), f"Line {index + 1} unit price")
        discount = _decimal(raw.get("discount_amount", 0), f"Line {index + 1} discount")
        line_tax = _decimal(raw.get("tax_amount", 0), f"Line {index + 1} tax")
        gross = (quantity * unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
        if gross > Decimal("999999999999.99"):
            raise SalesOrderValidationError(f"Line {index + 1} value is outside the allowed range")
        if discount > gross:
            raise SalesOrderValidationError(f"Line {index + 1} discount exceeds its value")
        total = (gross - discount + line_tax).quantize(MONEY, rounding=ROUND_HALF_UP)
        calculated.append(CalculatedLine(quantity, unit_price, discount, line_tax, total))
        subtotal += gross; line_discounts += discount; line_taxes += line_tax
    order_discount_value = _decimal(order_discount, "Order discount")
    shipping_value = _decimal(shipping, "Shipping")
    order_tax = _decimal(tax, "Order tax")
    if order_discount_value > subtotal - line_discounts:
        raise SalesOrderValidationError("Order discount exceeds the discounted subtotal")
    discount_total = (line_discounts + order_discount_value).quantize(MONEY)
    tax_total = (line_taxes + order_tax).quantize(MONEY)
    grand_total = (subtotal - discount_total + shipping_value + tax_total).quantize(MONEY)
    if grand_total > Decimal("999999999999.99"):
        raise SalesOrderValidationError("Order total is outside the allowed range")
    return OrderTotals(tuple(calculated), subtotal.quantize(MONEY), discount_total,
                       shipping_value, tax_total, grand_total)


def _required_text(payload: dict, name: str, maximum: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise SalesOrderValidationError(f"{name} is required and must be at most {maximum} characters")
    return value.strip()


def _validate_phone(value: str) -> str:
    phone = re.sub(r"\D", "", value or "")
    if not 9 <= len(phone) <= 15:
        raise SalesOrderValidationError("whatsapp_phone is invalid")
    return phone


def _order_number() -> str:
    return f"SO-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}"


def create_draft(db: Session, payload: dict, actor: str, idempotency_key: str) -> tuple[SalesOrder, bool]:
    if not isinstance(payload, dict) or not idempotency_key or len(idempotency_key) > 100:
        raise SalesOrderValidationError("A valid payload and idempotency key are required")
    existing = db.query(SalesOrder).filter_by(idempotency_key=idempotency_key).one_or_none()
    if existing:
        return existing, False
    totals = calculate_order(payload.get("lines"), payload.get("order_discount", 0),
                             payload.get("shipping_total", 0), payload.get("tax_total", 0))
    order = SalesOrder(
        internal_order_number=_order_number(), idempotency_key=idempotency_key,
        crm_conversation_id=payload.get("crm_conversation_id"), crm_lead_id=payload.get("crm_lead_id"),
        customer_id=payload.get("customer_id"), customer_name=_required_text(payload, "customer_name", 255),
        whatsapp_phone=_validate_phone(payload.get("whatsapp_phone", "")),
        alternative_phone=str(payload.get("alternative_phone") or "").strip() or None,
        delivery_contact_person=str(payload.get("delivery_contact_person") or "").strip() or None,
        delivery_address=str(payload.get("delivery_address") or "").strip() or None,
        city=str(payload.get("city") or "").strip() or None,
        district=str(payload.get("district") or "").strip() or None,
        postal_code=str(payload.get("postal_code") or "").strip() or None,
        customer_notes=str(payload.get("customer_notes") or "").strip() or None,
        notes=str(payload.get("notes") or "").strip() or None,
        subtotal=totals.subtotal, discount_total=totals.discount_total,
        shipping_total=totals.shipping_total, tax_total=totals.tax_total,
        grand_total=totals.grand_total, amount_paid=Decimal("0.00"), created_by=actor,
    )
    for raw, calculated in zip(payload["lines"], totals.lines):
        order.lines.append(SalesOrderLine(
            inventory_item_id=raw.get("inventory_item_id"),
            shopify_product_id=_required_text(raw, "shopify_product_id", 255),
            shopify_variant_id=_required_text(raw, "shopify_variant_id", 255),
            sku=str(raw.get("sku") or "").strip() or None,
            product_name_snapshot=_required_text(raw, "product_name", 500),
            variant_name_snapshot=str(raw.get("variant_name") or "").strip() or None,
            quantity=calculated.quantity, unit_price=calculated.unit_price,
            discount_amount=calculated.discount_amount, tax_amount=calculated.tax_amount,
            line_total=calculated.line_total,
        ))
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(SalesOrder).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing:
            return existing, False
        raise
    db.refresh(order)
    return order, True


def update_draft(db: Session, order: SalesOrder, payload: dict, actor: str) -> SalesOrder:
    if order.order_status != "DRAFT":
        raise SalesOrderValidationError("Only draft orders can be edited")
    if not isinstance(payload, dict):
        raise SalesOrderValidationError("A valid payload is required")
    # Reuse creation validation without committing a second order.
    totals = calculate_order(payload.get("lines"), payload.get("order_discount", 0),
                             payload.get("shipping_total", 0), payload.get("tax_total", 0))
    order.customer_name = _required_text(payload, "customer_name", 255)
    order.whatsapp_phone = _validate_phone(payload.get("whatsapp_phone", ""))
    for field in ("alternative_phone", "delivery_contact_person", "delivery_address", "city",
                  "district", "postal_code", "customer_notes", "notes"):
        setattr(order, field, str(payload.get(field) or "").strip() or None)
    order.lines.clear()
    for raw, calculated in zip(payload["lines"], totals.lines):
        order.lines.append(SalesOrderLine(
            inventory_item_id=raw.get("inventory_item_id"),
            shopify_product_id=_required_text(raw, "shopify_product_id", 255),
            shopify_variant_id=_required_text(raw, "shopify_variant_id", 255),
            sku=str(raw.get("sku") or "").strip() or None,
            product_name_snapshot=_required_text(raw, "product_name", 500),
            variant_name_snapshot=str(raw.get("variant_name") or "").strip() or None,
            quantity=calculated.quantity, unit_price=calculated.unit_price,
            discount_amount=calculated.discount_amount, tax_amount=calculated.tax_amount,
            line_total=calculated.line_total))
    order.subtotal, order.discount_total = totals.subtotal, totals.discount_total
    order.shipping_total, order.tax_total, order.grand_total = totals.shipping_total, totals.tax_total, totals.grand_total
    order.updated_at = datetime.utcnow()
    db.commit(); db.refresh(order)
    return order


def transition_order(db: Session, order: SalesOrder, new_status: str, actor: str,
                     reason: str | None = None) -> SalesOrder:
    target = str(new_status or "").upper()
    if target not in ORDER_STATUSES or target not in TRANSITIONS.get(order.order_status, set()):
        raise InvalidOrderTransition(f"Cannot transition {order.order_status} to {target or 'empty status'}")
    previous = order.order_status
    order.order_status = target
    order.updated_at = datetime.utcnow()
    if target == "CONFIRMED": order.confirmed_by = actor
    db.add(SalesOrderStatusHistory(sales_order_id=order.id, previous_status=previous,
                                   new_status=target, changed_by=actor,
                                   reason=(reason or "").strip() or None))
    db.commit(); db.refresh(order)
    return order
