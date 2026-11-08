"""One durable product-card claim per worker cycle; no batch replay."""
from datetime import datetime
from fastapi import HTTPException
from models import CustomerQuestionProductSend as Item,WhatsAppOutboundProductMessage as Reference
from shopify_collection_service import valid_id,available
from shopify_catalog_service import resolve_inbox_product,ShopifyCatalogError
from whatsapp_service import send_whatsapp_image
from product_card_service import caption,message


def validate_selection(items):
    if not isinstance(items,list) or not 1<=len(items)<=20:raise HTTPException(422,'Select 1–20 products per product step')
    seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'product_id','variant_id','label'}:raise HTTPException(422,'Select products from the catalogue')
        if not valid_id(item['product_id'],'Product') or not valid_id(item['variant_id'],'ProductVariant'):raise HTTPException(422,'Invalid selected product or variant')
        if not isinstance(item['label'],str) or not 1<=len(item['label'])<=200 or any(ord(c)<32 for c in item['label']):raise HTTPException(422,'Invalid selected product label')
        pair=(item['product_id'],item['variant_id'])
        if pair in seen:raise HTTPException(422,'The same product variant is selected twice')
        seen.add(pair)


def claim(db,run):
    items=db.query(Item).filter_by(run_id=run.id,step=run.step).order_by(Item.position).all()
    if any(item.status in {'SENDING','FAILED','UNCERTAIN'} for item in items):
        run.status='UNCERTAIN';run.version+=1;return False
    queued=next((item for item in items if item.status=='QUEUED'),None)
    if queued is not None:queued.status='SENDING';return True
    position=len(items)
    if position>=len(run.definition['steps'][run.step]['products']):return False
    db.add(Item(run_id=run.id,step=run.step,position=position,status='SENDING'))
    return True


def send_claimed(db,run,conversation):
    from customer_questions import audit
    from quick_message_service import require_reply_window
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,status='SENDING').one()
    selection=run.definition['steps'][run.step]['products'][item.position]
    try:product=resolve_inbox_product(selection['product_id'],selection['variant_id'])
    except ShopifyCatalogError:
        product=None;item.status='FAILED';item.reason='Catalogue verification unavailable. Check sent cards before restarting.'
    if item.status=='SENDING':
        if product is None or product.get('id')!=selection['product_id'] or product.get('variant_id')!=selection['variant_id'] or not available(product):
            item.status='SKIPPED';item.reason='Product is unavailable, unpublished, out of stock or has no safe image.'
        elif len(caption(product))>1024:
            item.status='SKIPPED';item.reason='Product caption exceeds the WhatsApp limit.'
        else:
            # Check again after catalogue IO; every card requires an open window.
            db.refresh(conversation)
            try:require_reply_window(db,conversation)
            except HTTPException:
                item.status='SKIPPED';item.reason='Customer-service window closed before sending.'
                run.status='WINDOW_CLOSED';run.version+=1
                audit(db,run,'PRODUCT_WINDOW_CLOSED','System',position=item.position+1);return
            try:outcome,provider=send_whatsapp_image(conversation.phone_number,product['image'],caption(product),return_outcome=True)
            except Exception:outcome,provider='UNKNOWN',None
            if outcome=='SENT' and isinstance(provider,str) and 0<len(provider)<=255:
                item.status='SENT';item.provider_id=provider
                db.add(message(conversation.phone_number,product,provider,source='question_product'))
                if not db.query(Reference).filter_by(whatsapp_message_id=provider).first():
                    db.add(Reference(whatsapp_message_id=provider,customer_phone=conversation.phone_number,shopify_product_handle=product['handle'],product_title=product['title']))
                conversation.last_message_at=max(conversation.last_message_at or datetime.utcnow(),datetime.utcnow())
            else:
                item.status='FAILED' if outcome=='FAILED' else 'UNCERTAIN'
                item.reason='Meta rejected the product card.' if outcome=='FAILED' else 'Delivery is uncertain. Check the customer chat; no automatic retry.'
    audit(db,run,'PRODUCT_'+item.status,'System',position=item.position+1,product_id=selection['product_id'],provider_id=item.provider_id,reason=item.reason)
    run.updated_at=datetime.utcnow();run.version+=1
    if item.status in {'SENT','SKIPPED'}:
        if item.position+1==len(run.definition['steps'][run.step]['products']):
            run.step=len(run.definition['steps']);run.status='COMPLETE';audit(db,run,'COMPLETE','System',note='Selected products processed. Staff can continue; review any skipped cards.')
        else:run.status='QUEUED'
    else:run.status='UNCERTAIN'  # Fail closed: never automatically replay any card.
