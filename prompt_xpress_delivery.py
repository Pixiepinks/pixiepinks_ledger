"""Explicit prepaid dispatch, durable staff tracking message, manual delivery completion."""
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from fastapi.routing import APIRoute
from models import SalesOrder,SalesFulfillment,SalesFulfillmentItem,SalesDeliveryDetail,WhatsAppManualSend as Send,WhatsAppConversationMessage as Message
from sales_fulfillment_service import create_full_fulfillment,transition,update_delivery,FulfillmentError
from sales_order_service import transition_order,SalesOrderValidationError
from sales_payment_service import verified_total
from quick_message_service import require_reply_window
from whatsapp_service import send_whatsapp_text
import customer_questions as questions
import conversation_access

TRACKING_URL='https://www.promptxpress.lk/TrackItem.aspx#'
COURIER='Prompt Xpress'


class Route(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:response=await original(request)
            except HTTPException as error:response=JSONResponse({'detail':error.detail},status_code=error.status_code)
            except SQLAlchemyError:response=JSONResponse({'detail':'Delivery action unavailable. Refresh and check the recorded state before retrying.'},status_code=503)
            response.headers['Cache-Control']='private, no-store';return response
        return handler


def tracking(value):
    if not isinstance(value,str) or not re.fullmatch(r'MC[0-9]{8}',value.strip().upper()):
        raise HTTPException(422,'Tracking number must be MC followed by exactly 8 digits, for example MC12345678')
    return value.strip().upper()


def feature(user,write=False):
    import main
    main._require_sales_orders_enabled();main._require_fulfillment_enabled()
    if not main.settings.SALES_DELIVERY_TRACKING_ENABLED:raise HTTPException(404,'Sales delivery tracking is disabled')
    if write:
        main._require_sales_permission(user,'fulfill_operate');main._require_sales_permission(user,'dispatch')


def load(db,user,oid,lock=False):
    order=db.query(SalesOrder).filter_by(id=oid).with_for_update().one_or_none() if lock else db.get(SalesOrder,oid)
    if not order or not order.crm_conversation_id:raise HTTPException(404,'A sales order linked to an Inbox conversation is required')
    conversation=conversation_access.require_owned(db,user,order.crm_conversation_id) if lock else conversation_access.require_visible(db,user,order.crm_conversation_id)
    if re.sub(r'\D','',order.whatsapp_phone)!=conversation.phone_number:raise HTTPException(409,'Order/customer phone changed. Review before sending tracking.')
    return order,conversation


def paid(db,order):
    if order.payment_status!='PAID' or order.grand_total<=0 or verified_total(db,order.id)<order.grand_total:
        raise HTTPException(409,'Verify the full customer payment before using prepaid dispatch')
    if order.order_status in {'CANCELLED','RETURNED'}:raise HTTPException(409,'This order cannot be dispatched')


def shipment(db,order):
    rows=db.query(SalesFulfillment).filter(SalesFulfillment.sales_order_id==order.id,SalesFulfillment.status.notin_(['CANCELLED','RETURNED'])).with_for_update().all()
    if len(rows)>1:raise HTTPException(409,'Use the existing fulfilment workflow for multiple shipments')
    row=rows[0] if rows else None
    if row:
        items=db.query(SalesFulfillmentItem).filter_by(fulfillment_id=row.id).all()
        if {item.sales_order_line_id:item.quantity for item in items}!={line.id:line.quantity for line in order.lines}:
            raise HTTPException(409,'Use the existing fulfilment workflow for a partial shipment')
    return row


def key(row,number):return 'prompt-xpress-'+str(row.id)+'-'+hashlib.sha256(number.encode()).hexdigest()[:32]


def snapshot(db,order,conversation):
    rows=db.query(SalesFulfillment).filter(SalesFulfillment.sales_order_id==order.id,SalesFulfillment.status.notin_(['CANCELLED','RETURNED'])).all()
    row=rows[0] if len(rows)==1 else None
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=row.id).one_or_none() if row else None
    number=detail.tracking_number if detail and detail.courier_name==COURIER else None
    attempt=db.query(Send).filter_by(idempotency_key=key(row,number),conversation_id=conversation.id).one_or_none() if number else None
    return {'order_id':order.id,'order_status':order.order_status,'payment_status':order.payment_status,
        'fulfillment':{'id':row.id,'status':row.status,'version':row.version} if row else None,
        'tracking_number':number,'tracking_url':TRACKING_URL,'message_status':attempt.status if attempt else 'NOT_SENT',
        'conversation_id':conversation.id,'version':row.version if row else 0,'multiple_shipments':len(rows)>1}


def prepare(db,order,user):
    row=shipment(db,order)
    if row is None:row=create_full_fulfillment(db,order.id,user.username,f'prompt-create-{order.id}',commit=False)
    for previous,target in [('UNFULFILLED','PACKING'),('PACKING','READY_TO_DISPATCH')]:
        if row.status==previous:transition(db,row.id,target,user.username,f'prompt-ready-{row.id}-{target}',expected_version=row.version,commit=False)
    if row.status!='READY_TO_DISPATCH':raise HTTPException(409,'This shipment is already dispatched or cannot be marked ready')
    if order.order_status=='CONFIRMED':transition_order(db,order,'PROCESSING',user.username,'Packed and ready for Prompt Xpress',commit=False)
    if order.order_status!='PROCESSING':raise HTTPException(409,'Confirm the order before preparing delivery')
    return row


def dispatch(db,order,user,number,version):
    row=shipment(db,order)
    if row is None:raise HTTPException(409,'Mark the packed order ready before dispatch')
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=row.id).one()
    if row.status=='DISPATCHED' and detail.tracking_number==number and detail.courier_name==COURIER:return row
    if row.version!=version:raise HTTPException(409,'Shipment changed. Refresh before dispatching')
    if row.status!='READY_TO_DISPATCH' or order.order_status!='PROCESSING':raise HTTPException(409,'Only a packed, ready shipment can be dispatched')
    update_delivery(db,row.id,{'delivery_method':'COURIER','courier_name':COURIER,'tracking_number':number,'tracking_url':TRACKING_URL,
        'dispatch_date':datetime.now(ZoneInfo('Asia/Colombo')).date().isoformat()},user.username,f'prompt-details-{row.id}-{number}',commit=False)
    transition(db,row.id,'DISPATCHED',user.username,f'prompt-dispatch-{row.id}',expected_version=row.version,commit=False)
    transition_order(db,order,'DISPATCHED',user.username,f'Prompt Xpress {number}',commit=False)
    order.fulfillment_status='DISPATCHED'
    return row


def claim_message(db,order,conversation,row,user):
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=row.id).one()
    number=tracking(detail.tracking_number)
    if detail.courier_name!=COURIER or row.status not in {'DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY'}:
        raise HTTPException(409,'A dispatched Prompt Xpress shipment is required')
    attempt=db.query(Send).filter_by(idempotency_key=key(row,number)).with_for_update().one_or_none()
    if attempt and attempt.conversation_id!=conversation.id:raise HTTPException(409,'Tracking message key belongs to another conversation')
    if attempt and attempt.status!='WINDOW_CLOSED':return attempt,False
    try:require_reply_window(db,conversation)
    except HTTPException:
        if not attempt:
            attempt=Send(idempotency_key=key(row,number),conversation_id=conversation.id,message_text='',status='WINDOW_CLOSED',sent_by=user.username);db.add(attempt)
        return attempt,False
    body=(f"ඔබගේ ඇණවුම Prompt Xpress වෙත භාර දී ඇත. 🚚💖\nඇණවුම් අංකය: {order.internal_order_number}\n"
        f"Tracking number: {number}\n\nTrack your delivery: {TRACKING_URL}\n\n"
        f"ඉහත ලින්ක් එක විවෘත කර {number} අංකය ඇතුළත් කර ඔබගේ බෙදාහැරීමේ තත්ත්වය බලන්න.")
    if not attempt:
        attempt=Send(idempotency_key=key(row,number),conversation_id=conversation.id,message_text=body,status='PROCESSING',sent_by=user.username);db.add(attempt)
    else:attempt.message_text=body;attempt.status='PROCESSING';attempt.sent_by=user.username
    db.flush();return attempt,True


def send_claimed(db,attempt_id):
    # Called only after the durable PROCESSING claim commits. A crash cannot replay it.
    questions.write_transaction(db)
    attempt=db.query(Send).filter_by(id=attempt_id).with_for_update().one()
    if attempt.status!='PROCESSING':return
    from models import WhatsAppConversation
    conversation=db.get(WhatsAppConversation,attempt.conversation_id)
    try:require_reply_window(db,conversation)
    except HTTPException:attempt.status='WINDOW_CLOSED';db.commit();return
    try:provider=send_whatsapp_text(conversation.phone_number,attempt.message_text,return_message_id=True)
    except Exception:provider=None
    if not isinstance(provider,str) or not 1<=len(provider)<=255:attempt.status='UNCERTAIN'
    else:
        attempt.status='SENT';attempt.whatsapp_message_id=provider
        db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=attempt.message_text,
            whatsapp_message_id=provider,response_kind='staff_tracking',send_status='sent',sent_by=attempt.sent_by,analytics_actor_kind='staff',analytics_category='text'))
        conversation.last_message_at=max(conversation.last_message_at or datetime.utcnow(),datetime.utcnow())
    db.commit()


def delivered(db,order,user,version):
    row=shipment(db,order)
    if row is None:raise HTTPException(409,'No shipment exists')
    if row.status=='DELIVERED' and order.order_status=='DELIVERED':return
    if row.version!=version:raise HTTPException(409,'Shipment changed. Refresh before marking delivered')
    if row.status not in {'DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY'} or order.order_status!='DISPATCHED':
        raise HTTPException(409,'Only a dispatched shipment can be marked delivered')
    transition(db,row.id,'DELIVERED',user.username,f'prompt-delivered-{row.id}',expected_version=row.version,commit=False)
    detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=row.id).one();detail.delivery_date=datetime.now(ZoneInfo('Asia/Colombo')).date()
    transition_order(db,order,'DELIVERED',user.username,'Delivery confirmed by staff',commit=False)
    order.fulfillment_status='DELIVERED'


def build_router(get_db,require_user,csrf):
    router=APIRouter(route_class=Route);path='/api/sales-orders/{oid}/prompt-xpress'
    @router.get(path)
    def current(oid:int,db=Depends(get_db),user=Depends(require_user)):
        feature(user);order,c=load(db,user,oid);return snapshot(db,order,c)
    @router.post(path)
    async def action(oid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        feature(user,True);csrf(request)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>4096:raise HTTPException(413,'Delivery request is too large')
            raw.extend(chunk)
        import json
        try:payload=json.loads(raw)
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid delivery request') from None
        if not isinstance(payload,dict) or set(payload)-{'action','version','tracking_number'}:raise HTTPException(422,'Invalid delivery request')
        action=payload.get('action')
        if action not in {'prepare','dispatch','send_tracking','delivered'}:raise HTTPException(422,'Choose a delivery action')
        if type(payload.get('version')) is not int:raise HTTPException(422,'Refresh the shipment before acting')
        questions.write_transaction(db);order,c=load(db,user,oid,True);paid(db,order);send=None;should_send=False
        try:
            row=shipment(db,order)
            if action=='prepare':
                if (row.version if row else 0)!=payload['version']:raise HTTPException(409,'Shipment changed. Refresh before preparing')
                prepare(db,order,user)
            elif action=='dispatch':
                row=dispatch(db,order,user,tracking(payload.get('tracking_number')),payload['version'])
                send,should_send=claim_message(db,order,c,row,user)
            elif action=='send_tracking':
                if row is None or row.version!=payload['version']:raise HTTPException(409,'Shipment changed. Refresh before sending tracking')
                send,should_send=claim_message(db,order,c,row,user)
            else:delivered(db,order,user,payload['version'])
            db.commit()
        except (FulfillmentError,SalesOrderValidationError) as error:db.rollback();raise HTTPException(getattr(error,'status_code',409),str(error)) from None
        if should_send:send_claimed(db,send.id)
        return snapshot(db,order,c)
    return router
