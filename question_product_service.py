"""One durable product-card claim per worker cycle; no batch replay."""
from datetime import datetime
from fastapi import HTTPException
from models import CustomerQuestionProductSend as Item,WhatsAppOutboundProductMessage as Reference,WhatsAppConversationMessage as Message
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
    from question_delivery_details import pending as delivery_pending,claim as claim_delivery
    if delivery_pending(run):return claim_delivery(db,run)
    from question_greasing_service import pending as service_pending,claim as claim_service
    if service_pending(run):return claim_service(db,run)
    items=db.query(Item).filter(Item.run_id==run.id,Item.step==run.step,Item.position>=0).order_by(Item.position).all()
    if any(item.status in {'SENDING','FAILED','UNCERTAIN'} for item in items):
        run.status='UNCERTAIN';run.version+=1;return False
    queued=next((item for item in items if item.status=='QUEUED'),None)
    if queued is not None:queued.status='SENDING';return True
    position=len(items)
    if position>=len(run.definition['steps'][run.step]['products']):
        from customer_questions import audit
        if run.definition['steps'][run.step].get('choose') and any(item.status=='SENT' for item in items):
            choice=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-1).one_or_none()
            if choice is None:db.add(Item(run_id=run.id,step=run.step,position=-1,status='SENDING'));return True
            if choice.status=='QUEUED':choice.status='SENDING';return True
            run.status='UNCERTAIN';run.version+=1;return False
        run.step=len(run.definition['steps']);run.status='COMPLETE';run.version+=1
        audit(db,run,'COMPLETE','System',note='No sent products available for a customer selection list.');return False
    db.add(Item(run_id=run.id,step=run.step,position=position,status='SENDING'))
    return True


def send_claimed(db,run,conversation):
    from customer_questions import audit
    from quick_message_service import require_reply_window
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,status='SENDING').one()
    step=run.definition['steps'][run.step]
    if item.position<=-4:
        from question_delivery_details import send_claimed as send_delivery
        send_delivery(db,run,conversation,item);return
    if item.position in {-2,-3}:
        from question_greasing_service import send_claimed as send_service
        send_service(db,run,conversation,item);return
    if item.position==-1:
        send_choice_list(db,run,conversation,item);return
    selection=step['products'][item.position]
    try:product=resolve_inbox_product(selection['product_id'],selection['variant_id'])
    except ShopifyCatalogError:
        product=None;item.status='FAILED';item.reason='Catalogue verification unavailable. Check sent cards before restarting.'
    card_caption=(f'{item.position+1}. ' if step.get('choose') else '')+caption(product) if product is not None else ''
    if item.status=='SENDING':
        if product is None or product.get('id')!=selection['product_id'] or product.get('variant_id')!=selection['variant_id'] or not available(product):
            item.status='SKIPPED';item.reason='Product is unavailable, unpublished, out of stock or has no safe image.'
        elif len(card_caption)>1024:
            item.status='SKIPPED';item.reason='Product caption exceeds the WhatsApp limit.'
        else:
            # Check again after catalogue IO; every card requires an open window.
            db.refresh(conversation)
            try:require_reply_window(db,conversation)
            except HTTPException:
                item.status='SKIPPED';item.reason='Customer-service window closed before sending.'
                run.status='WINDOW_CLOSED';run.version+=1
                audit(db,run,'PRODUCT_WINDOW_CLOSED','System',position=item.position+1);return
            try:outcome,provider=send_whatsapp_image(conversation.phone_number,product['image'],card_caption,return_outcome=True)
            except Exception:outcome,provider='UNKNOWN',None
            if outcome=='SENT' and isinstance(provider,str) and 0<len(provider)<=255:
                item.status='SENT';item.provider_id=provider
                item.choice_label=(f'{item.position+1}. '+' '.join(product['title'].split()))[:24] if step.get('choose') else None
                card=message(conversation.phone_number,product,provider,source='question_product');card.message_text=card_caption;db.add(card)
                if not db.query(Reference).filter_by(whatsapp_message_id=provider).first():
                    db.add(Reference(whatsapp_message_id=provider,customer_phone=conversation.phone_number,shopify_product_handle=product['handle'],product_title=product['title']))
                conversation.last_message_at=max(conversation.last_message_at or datetime.utcnow(),datetime.utcnow())
            else:
                item.status='FAILED' if outcome=='FAILED' else 'UNCERTAIN'
                item.reason='Meta rejected the product card.' if outcome=='FAILED' else 'Delivery is uncertain. Check the customer chat; no automatic retry.'
    audit(db,run,'PRODUCT_'+item.status,'System',position=item.position+1,product_id=selection['product_id'],provider_id=item.provider_id,reason=item.reason)
    run.updated_at=datetime.utcnow();run.version+=1
    if item.status in {'SENT','SKIPPED'}:
        if item.position+1==len(run.definition['steps'][run.step]['products']) and not step.get('choose'):
            run.step=len(run.definition['steps']);run.status='COMPLETE';audit(db,run,'COMPLETE','System',note='Selected products processed. Staff can continue; review any skipped cards.')
        else:run.status='QUEUED'
    else:run.status='UNCERTAIN'  # Fail closed: never automatically replay any card.



def send_choice_list(db,run,conversation,item):
    from customer_questions import audit,send_buttons
    cards=db.query(Item).filter(Item.run_id==run.id,Item.step==run.step,Item.position>=0,Item.status=='SENT').order_by(Item.position).all()
    if not cards or len(cards)>10 or any(not card.choice_label for card in cards):
        item.status='FAILED';item.reason='Product list could not be verified.';run.status='UNCERTAIN';run.version+=1;return
    step=run.definition['steps'][run.step];body=step.get('choice_body','Which product do you like?')
    options=[{'id':f'ppq:{run.token}:{run.step}:{card.position}','title':card.choice_label} for card in cards]
    provider=send_buttons(conversation.phone_number,body,options,list_button=step.get('choice_button','Choose a product'))
    item.status='SENT' if provider else 'UNCERTAIN';item.provider_id=provider
    run.status='WAITING' if provider else 'UNCERTAIN';run.provider_id=provider;run.updated_at=datetime.utcnow();run.version+=1
    if provider:
        db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=body,whatsapp_message_id=provider,
            response_kind='customer_question',send_status='sent',analytics_actor_kind='automatic'))
        conversation.last_message_at=max(conversation.last_message_at or run.updated_at,run.updated_at)
    else:item.reason='Product selection list delivery is uncertain. Check chat; no automatic retry.'
    audit(db,run,'SENT' if provider else 'UNCERTAIN','System',format='product_list',options=[row['title'] for row in options],provider_id=provider)


def receive_choice(db,run,incoming,position,phone):
    from customer_questions import audit
    step=run.definition['steps'][run.step]
    if not step.get('choose') or incoming.response_kind!='question_list' or not 0<=position<len(step['products']):return
    card=db.query(Item).filter_by(run_id=run.id,step=run.step,position=position,status='SENT').one_or_none()
    choice=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-1,status='SENT').one_or_none()
    if card is None or choice is None or choice.provider_id!=run.provider_id:return
    sent=db.query(Message).filter_by(phone_number=phone,whatsapp_message_id=card.provider_id,response_kind='question_product').one_or_none()
    if sent is None:return
    product=step['products'][position]
    if sent.shopify_product_reference!=product['product_id'] or sent.shopify_variant_reference!=product['variant_id']:return
    selected={'product_id':product['product_id'],'variant_id':product['variant_id'],'number':position+1,
              'title':sent.product_title,'variant_title':sent.variant_title}
    if step.get('service'):
        from decimal import Decimal
        if sent.product_price is None or not Decimal(sent.product_price).is_finite() or sent.product_price<0 or sent.product_currency!='LKR':
            run.answers={**run.answers,step['key']:selected};run.status='UNCERTAIN';run.version+=1
            audit(db,run,'SERVICE_QUOTE_REVIEW','System',reason='The selected card has no valid LKR price. Staff review is required.');return
        selected.update(price=f'{sent.product_price:.2f}',currency=sent.product_currency)
    run.answers={**run.answers,step['key']:selected}
    if step.get('service'):
        import secrets
        run.status='QUEUED';run.provider_id=None;run.token=secrets.token_urlsafe(24)
    else:run.step=len(run.definition['steps']);run.status='COMPLETE'
    run.updated_at=datetime.utcnow();run.version+=1
    audit(db,run,'PRODUCT_SELECTED','Customer',**selected,inbound_id=incoming.id)
    if run.status=='COMPLETE':audit(db,run,'COMPLETE','System',note='Customer selected a product. Staff can continue.')
