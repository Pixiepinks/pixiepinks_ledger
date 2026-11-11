"""Staff-reviewed slip → existing audited SalesPayment → durable confirmation."""
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from models import CustomerQuestionRun as Run, CustomerQuestionProductSend as Item
from models import SalesOrder, SalesPayment, WhatsAppConversationMessage as Message, WhatsAppMessageAttachment as Attachment
import customer_questions as questions
from question_flow_actions import quote
import conversation_access
import staff_access
from sales_order_service import create_draft, transition_order, SalesOrderValidationError
from sales_payment_service import record_payment_claim, decide_payment, PaymentValidationError
from shopify_catalog_service import resolve_inbox_product, ShopifyCatalogError
from shopify_collection_service import available
from settings import settings
from whatsapp_service import send_whatsapp_text

NOTIFICATION_POSITION = -202


def eligible(user):
    import main
    permissions=staff_access.sales_permissions(user,main.SALES_ORDER_PERMISSIONS)
    return bool(staff_access.allowed(user,'sales_orders') and permissions & {'create','record_payment_claim','verify_payment'})


def feature(user,permission=None):
    import main
    main._require_sales_orders_enabled();main._require_payment_tracking_enabled()
    if not staff_access.allowed(user,'sales_orders'):raise HTTPException(403,'Sales Orders access is required')
    if permission:main._require_sales_permission(user,permission)


def selected(run):
    for index,step in enumerate(run.definition['steps']):
        answer=run.answers.get(step['key'])
        if isinstance(answer,dict) and answer.get('payment',{}).get('receipt_message_id'):
            return step['key'],index,answer
    raise HTTPException(409,'This run has no submitted payment slip to review')


def save(run,key,state):
    run.answers={**run.answers,key:{**run.answers[key],'payment':state}}
    run.version+=1;run.updated_at=datetime.utcnow()


def normalize(value):
    return ' '.join(str(value or '').split()).casefold()


def matching(order,conversation,answer):
    details=answer['delivery_details'];service=quote(answer)
    return (order.crm_conversation_id==conversation.id and re.sub(r'\D','',order.whatsapp_phone)==conversation.phone_number and
        order.currency=='LKR' and order.order_status not in {'CANCELLED','RETURNED'} and
        Decimal(order.grand_total)==Decimal(service['quoted_total']) and Decimal(order.service_total or 0)==Decimal(service['charge']) and
        Decimal(order.shipping_total)==0 and Decimal(order.tax_total)==0 and Decimal(order.discount_total)==0 and Decimal(order.subtotal)==Decimal(answer['price']) and
        normalize(order.customer_name)==normalize(details['name']) and normalize(order.delivery_address)==normalize(details['address']) and
        len(order.lines)==1 and order.lines[0].shopify_product_id==answer['product_id'] and
        order.lines[0].shopify_variant_id==answer['variant_id'] and Decimal(order.lines[0].quantity)==1 and
        Decimal(order.lines[0].unit_price)==Decimal(answer['price']) and Decimal(order.lines[0].line_total)==Decimal(answer['price']) and
        Decimal(order.lines[0].discount_amount)==0 and Decimal(order.lines[0].tax_amount)==0)


def require_slip(db,conversation,answer):
    state=answer['payment']
    if answer.get('delivery_details',{}).get('status')!='CONFIRMED' or state.get('status') not in {'SLIP_REVIEW','VERIFIED'}:
        raise HTTPException(409,'Customer confirmation and a submitted slip are required')
    attachment=db.query(Attachment).join(Message,Message.id==Attachment.message_id).filter(
        Attachment.id==state['receipt_attachment_id'],Message.id==state['receipt_message_id'],
        Message.phone_number==conversation.phone_number,Message.direction=='inbound').one_or_none()
    if attachment is None:raise HTTPException(409,'The submitted slip is unavailable in this conversation')


def load(db,user,cid,rid,lock=False):
    conversation=conversation_access.require_visible(db,user,cid)
    if lock and not conversation_access.permissions(db,user,conversation)['can_manage']:
        raise HTTPException(403,'Only the conversation owner or administrator can review payment')
    query=select(Run).where(Run.id==rid,Run.conversation_id==cid)
    run=db.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if run is None:raise HTTPException(404,'Customer question run not found')
    key,index,answer=selected(run);require_slip(db,conversation,answer)
    return conversation,run,key,index,answer


def prepare(db,conversation,run,key,answer,user):
    state=dict(answer['payment'])
    if state.get('sales_order_id'):return db.get(SalesOrder,state['sales_order_id'])
    try:product=resolve_inbox_product(answer['product_id'],answer['variant_id'])
    except ShopifyCatalogError:raise HTTPException(503,'Product verification is unavailable. No order was created.') from None
    if not product or not available(product) or product.get('currency')!='LKR' or Decimal(str(product['price']))!=Decimal(answer['price']):
        raise HTTPException(409,'Product availability or price changed. Review with the customer before creating an order.')
    d=answer['delivery_details']
    payload={'crm_conversation_id':conversation.id,'customer_name':d['name'],'whatsapp_phone':conversation.phone_number,
        'alternative_phone':d['phone1'],'delivery_contact_person':d['name'],'delivery_address':d['address'],'city':d.get('city') or '',
        'customer_notes':f"Delivery phone 1: {d['phone1']}\nDelivery phone 2: {d.get('phone2') or 'Not provided'}\nQuestion run: {run.id}",
        'service_total':quote(answer)['charge'],'shipping_total':'0','tax_total':'0',
        'lines':[{'shopify_product_id':product['id'],'shopify_variant_id':product['variant_id'],'sku':product.get('sku'),
            'product_name':product['title'],'variant_name':product.get('variant_title'),'quantity':'1','unit_price':str(product['price']),
            'product_image_url':product.get('image'),'product_url':product.get('url')}]}
    order,_=create_draft(db,payload,user.username,f'question-order-{run.id}',commit=False)
    for target in ['AWAITING_CUSTOMER_CONFIRMATION','AWAITING_PAYMENT']:
        transition_order(db,order,target,user.username,f'Customer confirmed question run {run.id}',commit=False)
    state['sales_order_id']=order.id;save(run,key,state)
    questions.audit(db,run,'ORDER_PREPARED',user.username,sales_order_id=order.id)
    return order


def verify(db,conversation,run,key,index,answer,user,payload):
    if payload.get('bank_checked') is not True:raise HTTPException(422,'Confirm that you checked the actual bank transaction')
    if type(payload.get('sales_order_id')) is not int:raise HTTPException(422,'Select the matching sales order')
    raw_amount=payload.get('amount')
    if not isinstance(raw_amount,str) or not re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?',raw_amount):raise HTTPException(422,'Enter the actual received amount with at most two decimal places')
    try:received_date=date.fromisoformat(payload.get('payment_date',''))
    except (TypeError,ValueError):raise HTTPException(422,'Choose the bank transaction date') from None
    if received_date>datetime.now(ZoneInfo('Asia/Colombo')).date():raise HTTPException(422,'Bank transaction date cannot be in the future')
    fingerprint=hashlib.sha256(json.dumps({k:payload.get(k) for k in ['sales_order_id','amount','payment_date','payment_reference']},sort_keys=True).encode()).hexdigest()
    state=dict(answer['payment'])
    if state.get('verified_payment_id'):
        if state.get('verification_fingerprint')!=fingerprint:raise HTTPException(409,'This slip already has a different payment decision')
        return
    order=db.query(SalesOrder).filter_by(id=payload['sales_order_id']).with_for_update().one_or_none()
    if order is None or not matching(order,conversation,answer):raise HTTPException(409,'Choose an order matching this customer, product, delivery details and confirmed quote')
    if state.get('sales_order_id') not in {None,order.id}:raise HTTPException(409,'This slip is already linked to another order')
    claim={'payment_method':'BANK_TRANSFER','amount':raw_amount,'payment_date':received_date.isoformat(),
        'payment_reference':payload.get('payment_reference'),'notes':f'Question run {run.id}; receipt message {state["receipt_message_id"]}; actual bank transaction checked by {user.username}'}
    payment,_=record_payment_claim(db,order.id,claim,user.username,f'question-payment-claim-{run.id}',commit=False)
    payment,_=decide_payment(db,order.id,payment.id,approve=True,actor=user.username,idempotency_key=f'question-payment-verify-{run.id}',commit=False)
    if order.payment_status=='PAID':
        route={'DRAFT':'AWAITING_CUSTOMER_CONFIRMATION','AWAITING_CUSTOMER_CONFIRMATION':'AWAITING_PAYMENT',
            'AWAITING_PAYMENT':'PAYMENT_VERIFICATION','PAYMENT_VERIFICATION':'CONFIRMED'}
        while order.order_status in route:
            transition_order(db,order,route[order.order_status],user.username,f'Bank payment verified for question run {run.id}',commit=False)
    state.update(status='VERIFIED',sales_order_id=order.id,verified_payment_id=payment.id,verified_by=user.username,
        verified_at=datetime.utcnow().isoformat()+'Z',verification_fingerprint=fingerprint,confirmation_status='QUEUED')
    save(run,key,state);run.step=index;run.status='QUEUED';run.provider_id=None
    from quick_message_service import require_reply_window
    try:require_reply_window(db,conversation)
    except HTTPException:
        state['confirmation_status']='WINDOW_CLOSED';save(run,key,state);run.status='WINDOW_CLOSED'
    questions.audit(db,run,'BANK_PAYMENT_VERIFIED',user.username,sales_order_id=order.id,payment_id=payment.id)


def queue_confirmation(db,conversation,run,key,index,answer):
    state=dict(answer['payment'])
    if state.get('status')!='VERIFIED' or state.get('confirmation_status') not in {'WINDOW_CLOSED','QUEUED'}:
        raise HTTPException(409,'A sent or uncertain confirmation cannot be replayed')
    row=db.query(Item).filter_by(run_id=run.id,step=index,position=NOTIFICATION_POSITION).one_or_none()
    if row is not None and row.status!='QUEUED':raise HTTPException(409,'Check chat before sending another confirmation')
    from quick_message_service import require_reply_window
    require_reply_window(db,conversation)
    state['confirmation_status']='QUEUED';save(run,key,state);run.step=index;run.status='QUEUED';run.provider_id=None


def snapshot(db,conversation,run,answer,user):
    import main
    state=answer['payment'];bound=db.get(SalesOrder,state['sales_order_id']) if state.get('sales_order_id') else None
    rows=db.query(SalesOrder).filter_by(crm_conversation_id=conversation.id).order_by(SalesOrder.id.desc()).limit(100).all()
    def public(order):return {'id':order.id,'number':order.internal_order_number,'total':str(order.grand_total),'service_total':str(order.service_total),
        'amount_paid':str(order.amount_paid),'outstanding':str(max(Decimal('0'),Decimal(order.grand_total)-Decimal(order.amount_paid))),
        'payment_status':order.payment_status,'order_status':order.order_status,'url':f'/crm/sales-orders/{order.id}'}
    permissions=staff_access.sales_permissions(user,main.SALES_ORDER_PERMISSIONS)
    return {'run_id':run.id,'version':run.version,'payment':state,'order':public(bound) if bound else None,
        'orders':[public(row) for row in rows if matching(row,conversation,answer)],'quoted_total':quote(answer)['quoted_total'],
        'can_prepare':{'create','transition'}<=permissions,'can_verify':{'record_payment_claim','verify_payment','transition'}<=permissions,
        'automation_enabled':questions.config(db).enabled,'today':datetime.now(ZoneInfo('Asia/Colombo')).date().isoformat()}


def build_router(get_db,require_user,csrf,read_body,route_class):
    router=APIRouter(route_class=route_class)
    path='/crm/whatsapp/api/conversations/{cid}/customer-questions/{rid}/payment-review'
    @router.get(path)
    def current(cid:int,rid:int,db=Depends(get_db),user=Depends(require_user)):
        feature(user)
        if not eligible(user):raise HTTPException(403,'Order/payment access is required')
        c,run,key,index,answer=load(db,user,cid,rid)
        from fastapi.responses import JSONResponse
        return JSONResponse(snapshot(db,c,run,answer,user),headers={'Cache-Control':'private, no-store'})

    @router.post(path)
    async def act(cid:int,rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        feature(user);csrf(request);payload=await read_body(request)
        action=payload.get('action')
        if action not in {'prepare_order','verify','queue_confirmation'}:raise HTTPException(422,'Choose a payment review action')
        allowed={'action','version','sales_order_id','amount','payment_date','payment_reference','bank_checked'}
        if set(payload)-allowed:raise HTTPException(422,'Unexpected payment review fields')
        if action=='prepare_order':feature(user,'create');feature(user,'transition')
        else:
            for permission in ['record_payment_claim','verify_payment','transition']:feature(user,permission)
        questions.write_transaction(db)
        c,run,key,index,answer=load(db,user,cid,rid,True)
        if type(payload.get('version')) is not int:raise HTTPException(422,'Refresh the payment review first')
        if payload['version']!=run.version and not (action=='verify' and answer['payment'].get('verified_payment_id')):
            raise HTTPException(409,'Payment review changed. Refresh before acting')
        try:
            if action=='prepare_order':prepare(db,c,run,key,answer,user)
            elif action=='verify':verify(db,c,run,key,index,answer,user,payload)
            else:queue_confirmation(db,c,run,key,index,answer);questions.audit(db,run,'PAYMENT_CONFIRMATION_QUEUED',user.username)
            db.commit()
        except PaymentValidationError as error:db.rollback();raise HTTPException(error.status_code,str(error)) from None
        except SalesOrderValidationError as error:db.rollback();raise HTTPException(409,str(error)) from None
        from fastapi.responses import JSONResponse
        return JSONResponse(snapshot(db,c,run,selected(run)[2],user),headers={'Cache-Control':'private, no-store'})
    return router


def send_confirmation(db,run,conversation,item):
    from question_payment_followup import fail
    key,index,answer=selected(run);state=dict(answer['payment'])
    order=db.get(SalesOrder,state.get('sales_order_id'));payment=db.get(SalesPayment,state.get('verified_payment_id'))
    if not order or not payment or payment.sales_order_id!=order.id or payment.verification_status!='VERIFIED' or order.crm_conversation_id!=conversation.id or order.order_status in {'CANCELLED','RETURNED'}:
        fail(db,run,item,'Verified payment/order changed. Review before sending confirmation.');return
    outstanding=max(Decimal('0'),Decimal(order.grand_total)-Decimal(order.amount_paid))
    if order.payment_status=='PAID':
        body=(f"ඔබගේ ගෙවීම තහවුරු කර ඇත. ස්තුතියි! 💖\nඇණවුම් අංකය: {order.internal_order_number}\n"
            f"ලැබුණු මුදල: Rs. {Decimal(payment.amount):,.2f}\n"
            'ඔබගේ බයිසිකලය සූදානම් කර බෙදාහැරීමේ ඉදිරි විස්තර දැනුම් දෙන්නම්. 🚲🚚')
    else:
        body=(f"ඔබගේ Rs. {Decimal(payment.amount):,.2f} ගෙවීම තහවුරු කර ඇත. ස්තුතියි! 💖\n"
            f"ඇණවුම් අංකය: {order.internal_order_number}\nඉතිරි මුදල: Rs. {outstanding:,.2f}\n"
            'ඉතිරි ගෙවීම පිළිබඳ අපගේ කාර්ය මණ්ඩලය ඔබට සහාය වනු ඇත.')
    try:provider=send_whatsapp_text(conversation.phone_number,body,return_message_id=True)
    except Exception:provider=None
    if not isinstance(provider,str) or not 1<=len(provider)<=255:
        state['confirmation_status']='UNCERTAIN';save(run,key,state)
        fail(db,run,item,'Payment is verified, but confirmation delivery is uncertain. Check chat; no automatic retry.');return
    item.status='SENT';item.provider_id=provider
    state.update(confirmation_status='SENT',confirmation_provider_id=provider);save(run,key,state)
    run.status='COMPLETE';run.step=len(run.definition['steps']);run.provider_id=provider
    db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=body,
        whatsapp_message_id=provider,response_kind='staff_payment',send_status='sent',analytics_actor_kind='staff',analytics_category='text',sent_by=state['verified_by']))
    conversation.last_message_at=max(conversation.last_message_at or datetime.utcnow(),datetime.utcnow())
    questions.audit(db,run,'PAYMENT_CONFIRMATION_SENT',state['verified_by'],sales_order_id=order.id,payment_id=payment.id,provider_id=provider)


def window_closed(db,run):
    try:key,index,answer=selected(run)
    except HTTPException:return
    if answer['payment'].get('status')!='VERIFIED':return
    item=db.query(Item).filter_by(run_id=run.id,step=index,position=NOTIFICATION_POSITION,status='SENDING').one_or_none()
    if item:
        item.status='QUEUED';item.reason='Reply window closed before the confirmation send.'
    state=dict(answer['payment']);state['confirmation_status']='WINDOW_CLOSED';save(run,key,state)
