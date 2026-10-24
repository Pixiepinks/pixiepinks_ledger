"""Audited operational fulfillment; never changes payment, accounting, or inventory."""
import json
import re
import secrets
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from models import (SalesOrder, SalesOrderLine, SalesFulfillment, SalesFulfillmentItem,
                    SalesDeliveryDetail, SalesFulfillmentEvent)

STATUSES = {"UNFULFILLED", "PACKING", "READY_TO_DISPATCH", "DISPATCHED", "IN_TRANSIT",
            "OUT_FOR_DELIVERY", "DELIVERED", "DELIVERY_FAILED", "RETURN_TO_SENDER", "RETURNED", "CANCELLED"}
TRANSITIONS = {
    "UNFULFILLED": {"PACKING", "CANCELLED"}, "PACKING": {"READY_TO_DISPATCH", "CANCELLED"},
    "READY_TO_DISPATCH": {"DISPATCHED", "CANCELLED"}, "DISPATCHED": {"IN_TRANSIT", "DELIVERED", "DELIVERY_FAILED"},
    "IN_TRANSIT": {"OUT_FOR_DELIVERY", "DELIVERED", "DELIVERY_FAILED"},
    "OUT_FOR_DELIVERY": {"DELIVERED", "DELIVERY_FAILED"},
    "DELIVERY_FAILED": {"OUT_FOR_DELIVERY", "RETURN_TO_SENDER"}, "RETURN_TO_SENDER": {"RETURNED"},
    "DELIVERED": set(), "RETURNED": set(), "CANCELLED": set(),
}
DELIVERY_METHODS = {"COURIER", "LOCAL_DELIVERY", "CUSTOMER_PICKUP", "OTHER"}


class FulfillmentError(ValueError):
    def __init__(self, message, status_code=409): super().__init__(message); self.status_code=status_code


def _safe_text(value, maximum):
    value = str(value or "").strip()
    if len(value) > maximum: raise FulfillmentError(f"Value must be at most {maximum} characters", 422)
    return value or None


def _safe_url(value):
    value = _safe_text(value, 1000)
    if not value: return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FulfillmentError("Tracking URL must be a safe HTTPS URL", 422)
    return value


def _event(db, fulfillment, previous, target, actor, key, reason=None, metadata=None):
    db.add(SalesFulfillmentEvent(fulfillment_id=fulfillment.id, previous_status=previous,
        new_status=target, actor=actor, reason=reason, metadata_json=json.dumps(metadata or {}, sort_keys=True), idempotency_key=key))


def create_full_fulfillment(db: Session, order_id: int, actor: str, idempotency_key: str,
                            assigned_staff=None) -> SalesFulfillment:
    if not idempotency_key or len(idempotency_key)>100: raise FulfillmentError("A valid idempotency key is required",422)
    prior = db.query(SalesFulfillmentEvent).filter_by(idempotency_key=idempotency_key).one_or_none()
    if prior: return db.get(SalesFulfillment, prior.fulfillment_id)
    order = db.query(SalesOrder).filter_by(id=order_id).with_for_update().one_or_none()
    if not order: raise FulfillmentError("Sales order not found",404)
    if order.order_status not in {"CONFIRMED", "AWAITING_PAYMENT"}: raise FulfillmentError("Order is not eligible for fulfillment")
    existing = db.query(SalesFulfillment).filter_by(sales_order_id=order_id).all()
    allocated = {line.id: Decimal("0") for line in order.lines}
    for fulfillment in existing:
        if fulfillment.status not in {"CANCELLED", "RETURNED"}:
            for item in db.query(SalesFulfillmentItem).filter_by(fulfillment_id=fulfillment.id): allocated[item.sales_order_line_id] += Decimal(str(item.quantity))
    remaining = [(line, Decimal(str(line.quantity))-allocated[line.id]) for line in order.lines]
    if not remaining or any(q <= 0 for _,q in remaining): raise FulfillmentError("No unallocated quantity remains")
    fulfillment=SalesFulfillment(sales_order_id=order.id,
        fulfillment_reference=f"FUL-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}", status="UNFULFILLED",
        created_by=actor, assigned_staff=_safe_text(assigned_staff,100))
    db.add(fulfillment); db.flush()
    for line, quantity in remaining:
        db.add(SalesFulfillmentItem(fulfillment_id=fulfillment.id, sales_order_line_id=line.id, quantity=quantity))
    db.add(SalesDeliveryDetail(fulfillment_id=fulfillment.id, delivery_method="COURIER",
        contact_snapshot=order.delivery_contact_person or order.customer_name,
        address_snapshot=", ".join(filter(None,[order.delivery_address,order.city,order.district,order.postal_code]))))
    _event(db,fulfillment,None,"UNFULFILLED",actor,idempotency_key); db.commit(); db.refresh(fulfillment); return fulfillment


def transition(db: Session, fulfillment_id: int, target: str, actor: str, idempotency_key: str,
               reason=None, expected_version=None, admin=False) -> SalesFulfillment:
    if target not in STATUSES: raise FulfillmentError("Invalid fulfillment status",422)
    prior=db.query(SalesFulfillmentEvent).filter_by(idempotency_key=idempotency_key).one_or_none()
    if prior: return db.get(SalesFulfillment,prior.fulfillment_id)
    fulfillment=db.query(SalesFulfillment).filter_by(id=fulfillment_id).with_for_update().one_or_none()
    if not fulfillment: raise FulfillmentError("Fulfillment not found",404)
    if expected_version is not None and int(expected_version)!=fulfillment.version: raise FulfillmentError("Fulfillment changed; refresh and retry")
    previous=fulfillment.status
    if target not in TRANSITIONS[previous]:
        if not admin or previous in {"DELIVERED","RETURNED"} or not _safe_text(reason,500):
            raise FulfillmentError("Invalid fulfillment transition")
    now=datetime.utcnow(); fulfillment.status=target; fulfillment.version += 1
    if target=="PACKING": fulfillment.packing_started_at=now
    elif target=="READY_TO_DISPATCH": fulfillment.ready_at=now
    elif target=="DISPATCHED": fulfillment.dispatched_at=now
    elif target=="DELIVERED": fulfillment.delivered_at=now
    _event(db,fulfillment,previous,target,actor,idempotency_key,_safe_text(reason,500)); db.commit(); db.refresh(fulfillment); return fulfillment


def update_delivery(db: Session, fulfillment_id: int, payload: dict, actor: str, idempotency_key: str) -> SalesDeliveryDetail:
    prior=db.query(SalesFulfillmentEvent).filter_by(idempotency_key=idempotency_key).one_or_none()
    if prior: return db.query(SalesDeliveryDetail).filter_by(fulfillment_id=prior.fulfillment_id).one()
    fulfillment=db.query(SalesFulfillment).filter_by(id=fulfillment_id).with_for_update().one_or_none()
    if not fulfillment: raise FulfillmentError("Fulfillment not found",404)
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=fulfillment_id).one()
    method=str(payload.get("delivery_method") or detail.delivery_method).upper()
    if method not in DELIVERY_METHODS: raise FulfillmentError("Unsupported delivery method",422)
    detail.delivery_method=method; detail.courier_name=_safe_text(payload.get("courier_name"),120)
    detail.courier_reference=_safe_text(payload.get("courier_reference"),255); detail.tracking_number=_safe_text(payload.get("tracking_number"),255)
    detail.tracking_url=_safe_url(payload.get("tracking_url")); detail.delivery_remarks=_safe_text(payload.get("delivery_remarks"),1000)
    detail.proof_of_delivery_reference=_safe_text(payload.get("proof_of_delivery_reference"),500)
    for field in ("expected_delivery_date","dispatch_date","delivery_date"):
        value=payload.get(field)
        if value:
            try: setattr(detail,field,date.fromisoformat(str(value)))
            except ValueError: raise FulfillmentError(f"{field} must be an ISO date",422) from None
    fulfillment.version += 1; _event(db,fulfillment,fulfillment.status,fulfillment.status,actor,idempotency_key,
        metadata={"action":"DELIVERY_UPDATED"}); db.commit(); db.refresh(detail); return detail
