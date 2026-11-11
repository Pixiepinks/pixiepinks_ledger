"""Reusable saved-flow actions, using the existing durable send claims and checkout."""
from datetime import datetime
from decimal import Decimal
import secrets
from fastapi import HTTPException
from models import CustomerQuestionProductSend as Item, WhatsAppConversationMessage as Message, WhatsAppConversation as Conversation

TYPES = {'voice', 'details', 'checkout', 'text'}


def checkout_config(step):
    return step if step.get('type') == 'checkout' else step.get('service', {})


def quote(answer):
    return answer.get('order_quote') or answer['greasing_service']


def details_text(step):
    return step['body'].strip() + '\n\n' + '\n'.join(f"{row['label']}: {row['value']}" for row in step['rows'])


def validate(step):
    kind = step['type']
    allowed = {'voice': {'key','type','voice_id','revision','voice_title'},
               'details': {'key','type','body','rows'},
               'text': {'key','type','body'},
               'checkout': {'key','type','product_key','address_prompt','payment'}}[kind]
    required = allowed - ({'voice_title'} if kind == 'voice' else {'payment'} if kind == 'checkout' else set())
    if not required <= set(step) or set(step) - allowed:
        raise HTTPException(422, 'Provide the fields required for this flow step')
    if kind=='text':
        if not isinstance(step['body'],str) or not 1<=len(step['body'].strip())<=1024:raise HTTPException(422,'Question text must be 1–1024 characters')
    elif kind == 'voice':
        if any(type(step[k]) is not int or not 1 <= step[k] <= 2147483647 for k in ['voice_id','revision']):
            raise HTTPException(422, 'Select a ready Saved Voice and its revision')
        if 'voice_title' in step and (not isinstance(step['voice_title'],str) or len(step['voice_title'])>100):
            raise HTTPException(422, 'Invalid recording title')
    elif kind == 'details':
        rows = step['rows']
        if not isinstance(step['body'],str) or not 1 <= len(step['body'].strip()) <= 150:
            raise HTTPException(422, 'Product details heading must be 1–150 characters')
        if not isinstance(rows,list) or not 1 <= len(rows) <= 25:
            raise HTTPException(422, 'Add 1–25 product detail rows')
        for row in rows:
            if not isinstance(row,dict) or set(row) != {'label','value'} or any(not isinstance(row[k],str) or not 1 <= len(row[k].strip()) <= limit or any(ord(c)<32 for c in row[k]) for k,limit in [('label',80),('value',200)]):
                raise HTTPException(422, 'Each detail row needs a label (up to 80) and value (up to 200 characters)')
        if len(details_text(step)) > 4000:raise HTTPException(422, 'Product details message must be at most 4000 characters')
    else:
        if not isinstance(step['product_key'],str):raise HTTPException(422, 'Choose the product step for checkout')
        if not isinstance(step['address_prompt'],str) or not 1 <= len(step['address_prompt'].strip()) <= 500:
            raise HTTPException(422, 'Delivery address question must be 1–500 characters')
        if 'payment' in step:
            from question_payment_followup import validate as validate_payment
            validate_payment(step['payment'])


def advance(run):
    run.step += 1
    run.provider_id = None
    run.token = secrets.token_urlsafe(24)
    run.status = 'COMPLETE' if run.step == len(run.definition['steps']) else 'QUEUED'
    run.version += 1
    run.updated_at = datetime.utcnow()


def selected_card(db, run, step, card):
    """Use only price and identity retained on the card actually sent to the customer."""
    db.flush()
    product = step['products'][card.position]
    conversation=db.get(Conversation,run.conversation_id)
    message = db.query(Message).filter_by(whatsapp_message_id=card.provider_id,phone_number=conversation.phone_number,shopify_product_reference=product['product_id'],shopify_variant_reference=product['variant_id'],response_kind='question_product',direction='outbound').one_or_none()
    if message is None or message.product_price is None or message.product_currency != 'LKR' or not Decimal(message.product_price).is_finite() or message.product_price < 0:
        raise HTTPException(409, 'Checkout needs a sent product with a valid LKR price')
    return {'product_id':product['product_id'],'variant_id':product['variant_id'],'number':card.position+1,
            'title':message.product_title,'variant_title':message.variant_title,'price':f'{message.product_price:.2f}','currency':'LKR'}


def claim(db,run):
    from customer_questions import audit
    step = run.definition['steps'][run.step]
    if run.step:
        previous=db.query(Item).filter_by(run_id=run.id,step=run.step-1,status='SENT').all()
        providers=[item.provider_id for item in previous if item.provider_id]
        if providers and db.query(Message).filter(Message.whatsapp_message_id.in_(providers),Message.send_status=='failed').first():
            run.status='UNCERTAIN';run.version+=1
            audit(db,run,'FLOW_ACTION_BLOCKED','System',reason='The previous step has a failed delivery. Staff review required.');return False
    if step['type'] == 'checkout':
        from question_delivery_details import begin, pending, claim as claim_delivery
        from question_payment_followup import pending as payment_pending, claim as claim_payment
        if payment_pending(run):return claim_payment(db,run)
        if pending(run):return claim_delivery(db,run)
        answer = run.answers.get(step['product_key'])
        if not isinstance(answer,dict) or not answer.get('price') or answer.get('currency') != 'LKR':
            run.status='UNCERTAIN';run.version+=1
            audit(db,run,'CHECKOUT_BLOCKED','System',reason='No selected, sent LKR product is available for this checkout.');return False
        run.answers={**run.answers,step['key']:{**answer,'order_quote':{'charge':'0.00','quoted_total':answer['price'],'currency':'LKR'}}}
        begin(run)
        return claim_delivery(db,run)
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-300).one_or_none()
    if item is None:db.add(Item(run_id=run.id,step=run.step,position=-300,status='SENDING'));return True
    if item.status=='QUEUED':item.status='SENDING';return True
    run.status='UNCERTAIN';run.version+=1;return False


def send_claimed(db,run,conversation):
    from customer_questions import audit
    step=run.definition['steps'][run.step]
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,status='SENDING').one()
    if step['type']=='checkout':
        from question_product_service import send_claimed as send_checkout
        send_checkout(db,run,conversation);return
    if step['type']=='voice':
        from question_greasing_service import send_explanation
        send_explanation(db,run,conversation,item,config=step,source='question_voice',event='FLOW_VOICE_SENT')
    else:
        from whatsapp_service import send_whatsapp_text
        body=step['body'] if step['type']=='text' else details_text(step)
        try:provider=send_whatsapp_text(conversation.phone_number,body,return_message_id=True)
        except Exception:provider=None
        if isinstance(provider,str) and 1<=len(provider)<=255:
            item.status='SENT';item.provider_id=provider
            db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=body,whatsapp_message_id=provider,response_kind='customer_question',send_status='sent',analytics_actor_kind='automatic'))
            conversation.last_message_at=max(conversation.last_message_at or datetime.utcnow(),datetime.utcnow())
        else:
            item.status='UNCERTAIN';item.reason='Product details delivery is uncertain. Check chat; no automatic retry.';run.status='UNCERTAIN';run.version+=1
        audit(db,run,'FLOW_DETAILS_'+item.status,'System',provider_id=item.provider_id)
    if item.status=='SENT':
        if step['type']=='text':
            run.status='WAITING';run.provider_id=item.provider_id;run.updated_at=datetime.utcnow();run.version+=1
        else:advance(run)


def receive_text(db,run,incoming,verified,enabled):
    """Consume only the active typed question; paused/OFF runs still own their replies."""
    from datetime import timedelta
    from customer_questions import audit
    if run.step>=len(run.definition['steps']) or run.definition['steps'][run.step].get('type')!='text':return False
    if not verified or not enabled or run.status!='WAITING':return True
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-300,status='SENT').one_or_none()
    value=(incoming.message_text or '').strip()
    if not item or item.provider_id!=run.provider_id or incoming.created_at<item.created_at.replace(microsecond=0) or incoming.created_at>datetime.utcnow()+timedelta(minutes=5) or (incoming.reply_to_whatsapp_message_id and incoming.reply_to_whatsapp_message_id!=run.provider_id):return True
    if not 1<=len(value)<=500 or any(ord(c)<32 and c not in '\n\r\t' for c in value):return True
    key=run.definition['steps'][run.step]['key'];run.answers={**run.answers,key:value}
    audit(db,run,'ANSWER','Customer',key=key,message_id=incoming.id)
    advance(run);return True
