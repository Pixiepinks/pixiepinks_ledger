"""Explicit Shopify Fulfillment Order synchronization with no blind retries."""
import hashlib, json, re, secrets, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from models import (SalesOrder, SalesOrderLine, SalesFulfillment, SalesFulfillmentItem,
                    SalesDeliveryDetail, ShopifyFulfillmentOperation)
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError

ORDER_GID=re.compile(r"^gid://shopify/Order/[0-9]+$")
FO_GID=re.compile(r"^gid://shopify/FulfillmentOrder/[0-9]+$")
FOLI_GID=re.compile(r"^gid://shopify/FulfillmentOrderLineItem/[0-9]+$")
FULFILLMENT_GID=re.compile(r"^gid://shopify/Fulfillment/[0-9]+$")
WRITE_SCOPES={"write_merchant_managed_fulfillment_orders","write_assigned_fulfillment_orders","write_third_party_fulfillment_orders"}
SCOPES_QUERY="query FulfillmentScopes { currentAppInstallation { accessScopes { handle } } }"
PREFLIGHT_QUERY="""query FulfillmentPreflight($id: ID!) { order(id:$id) { id name cancelledAt displayFinancialStatus
 fulfillmentOrders(first:50) { nodes { id status requestStatus assignedLocation { location { id name } }
  supportedActions { action } lineItems(first:100) { nodes { id remainingQuantity totalQuantity lineItem { id variant { id } } } } }
  pageInfo { hasNextPage } } fulfillments(first:50) { id status } } }"""
CREATE_MUTATION="""mutation CreateLedgerFulfillment($fulfillment: FulfillmentInput!) { fulfillmentCreate(fulfillment:$fulfillment) {
 fulfillment { id status createdAt trackingInfo { company number url } } userErrors { field message } } }"""

class ShopifyFulfillmentError(RuntimeError):
    def __init__(self,message,status_code=409): super().__init__(message); self.status_code=status_code

def _hash(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

def _utcnow():
    """Return server-side UTC in the naive form used by existing database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _is_retirable_expired_preflight(operation, now):
    """Only a never-executed preview may relinquish its active-operation slot."""
    return bool(
        operation.status == "PREFLIGHT_READY"
        and operation.preflight_expires_at <= now
        and operation.attempts == 0
        and operation.claim_token is None
        and operation.claim_expires_at is None
        and operation.approved_by is None
        and operation.approved_at is None
        and operation.shopify_fulfillment_id is None
    )

def _scopes(client):
    try: data=client.graphql(SCOPES_QUERY,{})
    except ShopifyCatalogError: raise ShopifyFulfillmentError("Shopify scope check is unavailable",503) from None
    return {x.get("handle") for x in ((data.get("currentAppInstallation") or {}).get("accessScopes") or []) if isinstance(x,dict)}

def _load(db,fulfillment_id,lock=False):
    query=db.query(SalesFulfillment).filter_by(id=fulfillment_id)
    fulfillment=(query.with_for_update() if lock else query).one_or_none()
    if not fulfillment: raise ShopifyFulfillmentError("Fulfillment not found",404)
    order=db.get(SalesOrder,fulfillment.sales_order_id)
    if not order or order.shopify_lifecycle_status!="ORDER_LINKED" or not ORDER_GID.fullmatch(order.shopify_order_id or ""):
        raise ShopifyFulfillmentError("Verified Shopify Order linkage is required")
    return fulfillment,order

def _remote(client,order_id):
    try: remote=client.graphql(PREFLIGHT_QUERY,{"id":order_id}).get("order")
    except ShopifyCatalogError: raise ShopifyFulfillmentError("Shopify fulfillment preflight is unavailable",503) from None
    if not remote or remote.get("id")!=order_id: raise ShopifyFulfillmentError("Shopify Order identity could not be verified")
    return remote

def _plan(db,fulfillment,order,remote):
    if remote.get("cancelledAt"): raise ShopifyFulfillmentError("Shopify Order is cancelled")
    connection=remote.get("fulfillmentOrders") or {}
    if (connection.get("pageInfo") or {}).get("hasNextPage"): raise ShopifyFulfillmentError("Shopify Fulfillment Order history is incomplete")
    nodes=connection.get("nodes") or []
    if not nodes: raise ShopifyFulfillmentError("No accessible Shopify Fulfillment Orders exist")
    items=db.query(SalesFulfillmentItem).filter_by(fulfillment_id=fulfillment.id).all()
    lines={line.id:line for line in db.query(SalesOrderLine).filter_by(sales_order_id=order.id)}
    by_variant={}
    for node in nodes:
        if not FO_GID.fullmatch(str(node.get("id") or "")): raise ShopifyFulfillmentError("Invalid Fulfillment Order reference")
        actions={a.get("action") for a in node.get("supportedActions") or []}
        if node.get("status") not in {"OPEN","IN_PROGRESS"} or "CREATE_FULFILLMENT" not in actions:
            continue
        location=((node.get("assignedLocation") or {}).get("location") or {}).get("id")
        for remote_item in ((node.get("lineItems") or {}).get("nodes") or []):
            variant=((remote_item.get("lineItem") or {}).get("variant") or {}).get("id")
            by_variant.setdefault(variant,[]).append((node,remote_item,location))
    plan=[]; locations=set()
    for item in items:
        line=lines.get(item.sales_order_line_id); quantity=Decimal(str(item.quantity))-Decimal(str(item.quantity_fulfilled))
        matches=by_variant.get(line.shopify_variant_id if line else None,[])
        capable=[match for match in matches if Decimal(str(match[1].get("remainingQuantity") or 0))>=quantity]
        if len(capable)!=1: raise ShopifyFulfillmentError("Shopify line mapping is missing or ambiguous")
        node,remote_item,location=capable[0]
        if not FOLI_GID.fullmatch(str(remote_item.get("id") or "")) or not location: raise ShopifyFulfillmentError("Shopify fulfillment line or location is invalid")
        if quantity<=0 or quantity != quantity.to_integral_value(): raise ShopifyFulfillmentError("Shopify fulfillment requires a positive whole-number quantity")
        item.shopify_fulfillment_order_id=node["id"]; item.shopify_fulfillment_order_line_item_id=remote_item["id"]
        locations.add(location); plan.append({"fulfillmentOrderId":node["id"],"lineItemId":remote_item["id"],"quantity":int(quantity)})
    if len(locations)!=1: raise ShopifyFulfillmentError("Fulfillment Orders span multiple Shopify locations")
    grouped={}
    for item in plan: grouped.setdefault(item["fulfillmentOrderId"],[]).append({"id":item["lineItemId"],"quantity":item["quantity"]})
    return [{"fulfillmentOrderId":key,"fulfillmentOrderLineItems":value} for key,value in sorted(grouped.items())]

def create_preflight(db:Session,fulfillment_id:int,actor:str,idempotency_key:str,client=None):
    if not idempotency_key: raise ShopifyFulfillmentError("A valid idempotency key is required",422)
    fulfillment,order=_load(db,fulfillment_id,True)
    existing=db.query(ShopifyFulfillmentOperation).filter_by(idempotency_key=idempotency_key).with_for_update().one_or_none()
    if existing:return existing
    if fulfillment.status not in {"READY_TO_DISPATCH","DISPATCHED"}: raise ShopifyFulfillmentError("Fulfillment must be ready to dispatch")
    now=_utcnow()
    active=db.query(ShopifyFulfillmentOperation).filter_by(active_fulfillment_id=fulfillment.id).with_for_update().one_or_none()
    if active:
        if not _is_retirable_expired_preflight(active,now): return active
        # Preserve the original snapshot and fingerprint as history. Only release
        # the uniqueness slot; this operation can never be executed afterwards.
        active.status="EXPIRED";active.active_fulfillment_id=None;active.completed_at=now
        active.error_summary="Preflight expired before approval or execution"
        db.flush()
    api=client or ShopifyCatalogClient(); scopes=_scopes(api)
    if not scopes.intersection(WRITE_SCOPES): raise ShopifyFulfillmentError("Required Shopify Fulfillment Order write scope is not installed",403)
    remote=_remote(api,order.shopify_order_id); plan=_plan(db,fulfillment,order,remote)
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=fulfillment.id).one()
    request={"lineItemsByFulfillmentOrder":plan,"notifyCustomer":False}
    tracking={k:v for k,v in {"company":detail.courier_name,"number":detail.tracking_number,"url":detail.tracking_url}.items() if v}
    if tracking:request["trackingInfo"]=tracking
    snapshot={"request":request,"existingFulfillments":sorted(str(x.get("id")) for x in remote.get("fulfillments") or [])}
    operation=ShopifyFulfillmentOperation(operation_id=str(uuid.uuid4()),idempotency_key=idempotency_key,sales_order_id=order.id,
        fulfillment_id=fulfillment.id,shopify_order_id=order.shopify_order_id,active_fulfillment_id=fulfillment.id,
        request_fingerprint=_hash(request),preflight_json=json.dumps(snapshot,sort_keys=True),status="PREFLIGHT_READY",
        preflight_expires_at=now+timedelta(minutes=5))
    db.add(operation);db.commit();db.refresh(operation);return operation

def execute(db:Session,fulfillment_id:int,operation_id:str,actor:str,confirmation:str,client=None):
    if confirmation!="CREATE SHOPIFY FULFILLMENT":raise ShopifyFulfillmentError("Exact confirmation text is required",422)
    op=db.query(ShopifyFulfillmentOperation).filter_by(operation_id=operation_id,fulfillment_id=fulfillment_id).with_for_update().one_or_none()
    if not op:raise ShopifyFulfillmentError("Operation not found",404)
    if op.status=="SYNCED":return op
    if op.status!="PREFLIGHT_READY" or op.preflight_expires_at<=_utcnow():raise ShopifyFulfillmentError("Fresh fulfillment preflight is required")
    fulfillment,order=_load(db,fulfillment_id,True);api=client or ShopifyCatalogClient()
    if not _scopes(api).intersection(WRITE_SCOPES):raise ShopifyFulfillmentError("Required Shopify scope is missing",403)
    remote=_remote(api,order.shopify_order_id);plan=_plan(db,fulfillment,order,remote)
    request=json.loads(op.preflight_json)["request"]
    if _hash(request)!=op.request_fingerprint or request["lineItemsByFulfillmentOrder"]!=plan:raise ShopifyFulfillmentError("Fulfillment state changed after preflight")
    claim=secrets.token_hex(32);op.status="PROCESSING";op.claim_token=claim;op.claim_expires_at=_utcnow()+timedelta(minutes=2)
    op.attempts+=1;op.approved_by=actor;op.approved_at=_utcnow();db.commit()
    try:payload=api.graphql(CREATE_MUTATION,{"fulfillment":request}).get("fulfillmentCreate") or {}
    except ShopifyCatalogError:
        op=db.query(ShopifyFulfillmentOperation).filter_by(operation_id=operation_id).with_for_update().one();op.status="RECONCILIATION_REQUIRED"
        op.error_summary="Shopify mutation outcome is uncertain; do not retry";op.claim_token=None;op.claim_expires_at=None;db.commit()
        raise ShopifyFulfillmentError(op.error_summary,503) from None
    errors=payload.get("userErrors") or []
    if errors:
        op=db.query(ShopifyFulfillmentOperation).filter_by(operation_id=operation_id).with_for_update().one();op.status="FAILED";op.active_fulfillment_id=None
        op.error_summary="; ".join(str(x.get("message") or "Shopify rejected fulfillment") for x in errors[:3])[:500];op.claim_token=None;db.commit()
        raise ShopifyFulfillmentError(op.error_summary,422)
    result=payload.get("fulfillment") or {}; remote_id=str(result.get("id") or "")
    try:
        readback=_remote(api,order.shopify_order_id)
        verified=next((item for item in readback.get("fulfillments") or [] if item.get("id")==remote_id),None)
    except ShopifyFulfillmentError:
        verified=None
    op=db.query(ShopifyFulfillmentOperation).filter_by(operation_id=operation_id).with_for_update().one()
    if op.claim_token!=claim or not FULFILLMENT_GID.fullmatch(remote_id) or not verified:
        op.status="RECONCILIATION_REQUIRED";op.error_summary="Shopify readback did not prove the created fulfillment"
    else:
        op.status="SYNCED";op.shopify_fulfillment_id=remote_id;op.completed_at=_utcnow();fulfillment.shopify_fulfillment_id=remote_id
        fulfillment.shopify_fulfillment_status=str(result.get("status") or "SUCCESS")[:40];fulfillment.shopify_sync_status="SYNCED";fulfillment.shopify_synced_at=_utcnow()
        for item in db.query(SalesFulfillmentItem).filter_by(fulfillment_id=fulfillment.id):item.quantity_fulfilled=item.quantity
    op.claim_token=None;op.claim_expires_at=None;db.commit();db.refresh(op);return op

def reconcile(db:Session,fulfillment_id:int,operation_id:str,actor:str,client=None):
    op=db.query(ShopifyFulfillmentOperation).filter_by(operation_id=operation_id,fulfillment_id=fulfillment_id).with_for_update().one_or_none()
    if not op:raise ShopifyFulfillmentError("Operation not found",404)
    if op.status=="SYNCED":return op
    if op.status not in {"RECONCILIATION_REQUIRED","PROCESSING"}:raise ShopifyFulfillmentError("Operation does not require reconciliation")
    if op.status=="PROCESSING" and op.claim_expires_at and op.claim_expires_at>_utcnow():raise ShopifyFulfillmentError("Operation is still in progress",503)
    fulfillment,order=_load(db,fulfillment_id,True);remote=_remote(client or ShopifyCatalogClient(),order.shopify_order_id)
    before=set(json.loads(op.preflight_json).get("existingFulfillments") or [])
    after={str(x.get("id")):x for x in remote.get("fulfillments") or [] if FULFILLMENT_GID.fullmatch(str(x.get("id") or ""))}
    created=[value for key,value in after.items() if key not in before]
    if len(created)==1:
        result=created[0];op.status="SYNCED";op.shopify_fulfillment_id=result["id"];op.completed_at=_utcnow();op.error_summary=None
        fulfillment.shopify_fulfillment_id=result["id"];fulfillment.shopify_fulfillment_status=str(result.get("status") or "SUCCESS")[:40]
        fulfillment.shopify_sync_status="SYNCED";fulfillment.shopify_synced_at=_utcnow()
        for item in db.query(SalesFulfillmentItem).filter_by(fulfillment_id=fulfillment.id):item.quantity_fulfilled=item.quantity
    else:
        op.status="RECONCILIATION_REQUIRED";op.error_summary="Read-only reconciliation could not identify exactly one new Shopify fulfillment"
    op.claim_token=None;op.claim_expires_at=None;db.commit();db.refresh(op);return op
