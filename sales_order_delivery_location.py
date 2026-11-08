"""Staff completion of delivery location on pre-dispatch orders; no money edits."""
from datetime import datetime
import json
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from models import SalesOrder,SalesOrderStatusHistory,SalesFulfillment
import conversation_access
import customer_questions as questions

FIELDS={'city':120,'district':120,'postal_code':30}
EDITABLE={'DRAFT','AWAITING_CUSTOMER_CONFIRMATION','AWAITING_PAYMENT','PAYMENT_VERIFICATION','CONFIRMED','PROCESSING'}


def editable(order):
    return (order.order_status in EDITABLE and not order.shopify_draft_order_id and not order.shopify_order_id and
        order.shopify_sync_status not in {'SYNCING','RECONCILIATION_REQUIRED'})


def shipment_locked(db,order,lock=False):
    query=db.query(SalesFulfillment).filter_by(sales_order_id=order.id).populate_existing()
    rows=(query.with_for_update() if lock else query).all()
    return any(row.status not in {'UNFULFILLED','PACKING','READY_TO_DISPATCH','CANCELLED'} for row in rows)


def can_edit(db,user,order,permissions):
    if 'edit' not in permissions or not editable(order) or shipment_locked(db,order):return False
    if order.crm_conversation_id:
        try:c=conversation_access.require_visible(db,user,order.crm_conversation_id)
        except HTTPException:return False
        return conversation_access.permissions(db,user,c)['can_manage']
    return True


def version(order):return order.updated_at.isoformat(timespec='microseconds')+'Z'


def public(order):return {'version':version(order),**{key:getattr(order,key) or '' for key in FIELDS}}


def access(db,user,oid,write=False):
    import main
    main._require_sales_orders_enabled();main._require_sales_permission(user,'edit')
    query=db.query(SalesOrder).filter_by(id=oid).populate_existing()
    order=(query.with_for_update() if write else query).one_or_none()
    if order is None:raise HTTPException(404,'Sales order not found')
    if order.crm_conversation_id:
        c=conversation_access.require_visible(db,user,order.crm_conversation_id)
        if not conversation_access.permissions(db,user,c)['can_manage']:raise HTTPException(403,'Only the conversation owner or administrator can edit delivery location')
    if not editable(order) or shipment_locked(db,order,write):raise HTTPException(409,'Edit delivery location before Shopify sync or dispatch. This order is already linked, dispatching or closed.')
    return order


def build_router(get_db,require_user,csrf,route_class):
    router=APIRouter(route_class=route_class);path='/api/sales-orders/{oid}/delivery-location'
    @router.get(path)
    def current(oid:int,db=Depends(get_db),user=Depends(require_user)):
        return JSONResponse(public(access(db,user,oid)),headers={'Cache-Control':'private, no-store'})
    @router.put(path)
    async def save(oid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>4096:raise HTTPException(413,'Delivery location request is too large')
            raw.extend(chunk)
        try:payload=json.loads(raw)
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid delivery location request') from None
        if not isinstance(payload,dict) or set(payload)!={'version',*FIELDS} or not isinstance(payload['version'],str):
            raise HTTPException(422,'Provide the current city, district and postal code only')
        values={}
        for key,limit in FIELDS.items():
            value=payload[key]
            if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 or 127<=ord(c)<160 for c in value):
                raise HTTPException(422,f'{key.replace("_"," ").title()} must be text with at most {limit} characters')
            values[key]=value.strip()
        questions.write_transaction(db);order=access(db,user,oid,True)
        before={key:getattr(order,key) or '' for key in FIELDS}
        if before==values:return JSONResponse(public(order),headers={'Cache-Control':'private, no-store'})
        if payload['version']!=version(order):raise HTTPException(409,'Order changed. Refresh and review the delivery location before saving')
        for key,value in values.items():setattr(order,key,value or None)
        order.updated_at=datetime.utcnow()
        db.add(SalesOrderStatusHistory(sales_order_id=order.id,previous_status=order.order_status,new_status=order.order_status,
            changed_by=user.username,reason=json.dumps({'action':'DELIVERY_LOCATION_UPDATED','before':before,'after':values},ensure_ascii=False)))
        db.commit();db.refresh(order)
        return JSONResponse(public(order),headers={'Cache-Control':'private, no-store'})
    return router
