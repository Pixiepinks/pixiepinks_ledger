from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
import main
from database import Base,SessionLocal,engine
from migrations import upgrade_database
from models import (SalesOrder,SalesOrderLine,SalesFulfillment,SalesFulfillmentItem,
 SalesDeliveryDetail,SalesFulfillmentEvent,ShopifyFulfillmentOperation,JournalEntry,SalesPayment,User)
from sales_fulfillment_service import create_full_fulfillment,transition,update_delivery,FulfillmentError
from shopify_fulfillment_service import create_preflight,execute,reconcile,ShopifyFulfillmentError
from utils_auth import hash_password

def setup_function():
 Base.metadata.create_all(engine);upgrade_database()
 with SessionLocal() as db:
  for model in (ShopifyFulfillmentOperation,SalesFulfillmentEvent,SalesDeliveryDetail,SalesFulfillmentItem,SalesFulfillment,SalesPayment,SalesOrderLine,SalesOrder):db.query(model).delete()
  for username,role in (("fulfill-admin","ADMIN"),("fulfill-agent","SALES_AGENT")):
   user=db.query(User).filter_by(username=username).one_or_none()
   if not user:db.add(User(username=username,password_hash=hash_password("password"),role=role))
   else:user.role=role
  db.commit()

def order(db,shopify=True):
 o=SalesOrder(internal_order_number="SO-20261024-F4D00001",idempotency_key="f4d",customer_name="Delivery Test",whatsapp_phone="94700000000",created_by="admin",order_status="CONFIRMED",payment_status="PAID",fulfillment_status="UNFULFILLED",subtotal=350,discount_total=0,shipping_total=0,tax_total=0,grand_total=350,amount_paid=350,currency="LKR",shopify_order_id="gid://shopify/Order/1803" if shopify else None,shopify_lifecycle_status="ORDER_LINKED" if shopify else "NOT_LINKED")
 db.add(o);db.flush();db.add(SalesOrderLine(sales_order_id=o.id,shopify_product_id="gid://shopify/Product/1",shopify_variant_id="gid://shopify/ProductVariant/2",product_name_snapshot="Pen",quantity=1,unit_price=350,discount_amount=0,tax_amount=0,line_total=350));db.commit();db.refresh(o);return o

def test_operational_state_machine_audit_and_isolation():
 with SessionLocal() as db:
  o=order(db);journals=db.query(JournalEntry).count();f=create_full_fulfillment(db,o.id,"staff","create")
  assert f.status=="UNFULFILLED" and len(db.query(SalesFulfillmentItem).filter_by(fulfillment_id=f.id).all())==1
  for i,status in enumerate(("PACKING","READY_TO_DISPATCH","DISPATCHED","IN_TRANSIT","OUT_FOR_DELIVERY","DELIVERED")):
   f=transition(db,f.id,status,"staff",f"step-{i}",expected_version=f.version)
  db.refresh(o);assert f.status=="DELIVERED" and o.payment_status=="PAID" and o.fulfillment_status=="UNFULFILLED"
  assert db.query(JournalEntry).count()==journals and db.query(SalesFulfillmentEvent).count()==7
  with pytest.raises(FulfillmentError):transition(db,f.id,"PACKING","admin","bad",reason="correction",admin=True)

def test_failure_reattempt_return_and_idempotency():
 with SessionLocal() as db:
  f=create_full_fulfillment(db,order(db).id,"staff","create")
  for i,s in enumerate(("PACKING","READY_TO_DISPATCH","DISPATCHED","DELIVERY_FAILED","OUT_FOR_DELIVERY","DELIVERY_FAILED","RETURN_TO_SENDER","RETURNED")):f=transition(db,f.id,s,"staff",f"s{i}")
  assert f.status=="RETURNED" and transition(db,f.id,"RETURNED","staff","s7").id==f.id

def test_tracking_url_validation_and_no_overfulfillment():
 with SessionLocal() as db:
  o=order(db);f=create_full_fulfillment(db,o.id,"staff","create")
  with pytest.raises(FulfillmentError):update_delivery(db,f.id,{"tracking_url":"javascript:alert(1)"},"staff","url")
  detail=update_delivery(db,f.id,{"delivery_method":"COURIER","courier_name":"Manual Courier","tracking_number":"ABC","tracking_url":"https://courier.example/ABC"},"staff","safe")
  assert detail.tracking_number=="ABC"
  with pytest.raises(FulfillmentError):create_full_fulfillment(db,o.id,"staff","second")

def remote(existing=None,status="OPEN",actions=True,remaining=1,cancelled=None):
 return {"id":"gid://shopify/Order/1803","name":"#1803","cancelledAt":cancelled,"displayFinancialStatus":"PAID",
  "fulfillmentOrders":{"nodes":[{"id":"gid://shopify/FulfillmentOrder/10","status":status,"requestStatus":"UNSUBMITTED","assignedLocation":{"location":{"id":"gid://shopify/Location/1","name":"Main"}},"supportedActions":[{"action":"CREATE_FULFILLMENT"}] if actions else [],"lineItems":{"nodes":[{"id":"gid://shopify/FulfillmentOrderLineItem/11","remainingQuantity":remaining,"totalQuantity":1,"lineItem":{"id":"gid://shopify/LineItem/5","variant":{"id":"gid://shopify/ProductVariant/2"}}}]}}],"pageInfo":{"hasNextPage":False}},"fulfillments":existing or []}

class API:
 def __init__(self,before=None,after=None,scopes=True,timeout=False):self.before=before or remote();self.after=after or remote(existing=[{"id":"gid://shopify/Fulfillment/99","status":"SUCCESS"}]);self.scopes=scopes;self.timeout=timeout;self.mutations=0
 def graphql(self,q,v):
  if "currentAppInstallation" in q:return {"currentAppInstallation":{"accessScopes":[{"handle":"write_merchant_managed_fulfillment_orders"}] if self.scopes else []}}
  if "fulfillmentCreate" in q:
   self.mutations+=1
   if self.timeout:
    from shopify_catalog_service import ShopifyCatalogError
    raise ShopifyCatalogError("timeout")
   return {"fulfillmentCreate":{"fulfillment":{"id":"gid://shopify/Fulfillment/99","status":"SUCCESS","createdAt":"2026-10-24T00:00:00Z","trackingInfo":{}},"userErrors":[]}}
  return {"order":self.after if self.mutations and self.after else self.before}

def ready(db):
 f=create_full_fulfillment(db,order(db).id,"staff","create");f=transition(db,f.id,"PACKING","staff","pack");return transition(db,f.id,"READY_TO_DISPATCH","staff","ready")

def test_shopify_preflight_mutation_and_duplicate_click():
 api=API()
 with SessionLocal() as db:
  f=ready(db);op=create_preflight(db,f.id,"admin","pre",api);assert api.mutations==0 and op.status=="PREFLIGHT_READY"
  op=execute(db,f.id,op.operation_id,"admin","CREATE SHOPIFY FULFILLMENT",api);assert op.status=="SYNCED" and api.mutations==1
  assert execute(db,f.id,op.operation_id,"admin","CREATE SHOPIFY FULFILLMENT",api).status=="SYNCED" and api.mutations==1

@pytest.mark.parametrize("api,message",[(API(scopes=False),"scope"),(API(before=remote(cancelled="now")),"cancelled"),(API(before=remote(actions=False)),"mapping"),(API(before=remote(remaining=0)),"mapping")])
def test_shopify_preflight_blocks_unsafe_cases(api,message):
 with SessionLocal() as db:
  f=ready(db)
  with pytest.raises(ShopifyFulfillmentError,match=message):create_preflight(db,f.id,"admin","pre",api)
  assert api.mutations==0

def test_timeout_requires_read_only_reconciliation_without_retry():
 api=API(timeout=True)
 with SessionLocal() as db:
  f=ready(db);op=create_preflight(db,f.id,"admin","pre",api)
  with pytest.raises(ShopifyFulfillmentError,match="uncertain"):execute(db,f.id,op.operation_id,"admin","CREATE SHOPIFY FULFILLMENT",api)
  assert api.mutations==1
  api.timeout=False;api.after=remote(existing=[{"id":"gid://shopify/Fulfillment/99","status":"SUCCESS"}])
  op=reconcile(db,f.id,op.operation_id,"admin",api);assert op.status=="SYNCED" and api.mutations==1

def test_api_feature_flags_permissions_and_csrf(monkeypatch):
 with SessionLocal() as db:oid=order(db).id
 client=TestClient(main.app,base_url="https://testserver");client.post("/login",data={"username":"fulfill-agent","password":"password","next":"/"})
 monkeypatch.setattr(main.settings,"SALES_ORDERS_ENABLED",True)
 assert client.post(f"/api/sales-orders/{oid}/fulfillments").status_code==404
 monkeypatch.setattr(main.settings,"SALES_FULFILLMENT_ENABLED",True)
 assert client.post(f"/api/sales-orders/{oid}/fulfillments",headers={"Idempotency-Key":"x"}).status_code==403
 token=client.get("/api/sales-orders/csrf").json()["csrf_token"]
 assert client.post(f"/api/sales-orders/{oid}/fulfillments",headers={"Idempotency-Key":"x","X-CSRF-Token":token}).status_code==403
