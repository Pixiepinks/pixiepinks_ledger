"""Persisted quote references and conversation-scoped, token-free reply previews."""
import re
from urllib.parse import urlsplit
from collections import defaultdict

from models import WhatsAppConversationMessage,WhatsAppMessageAttachment


def extract_reply_id(message):
    context=message.get('context')
    value=context.get('id') if isinstance(context,dict) else None
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:=+/-]{1,255}',value) else None


def reply_previews(db,conversation,messages):
    references={message.reply_to_whatsapp_message_id for message in messages if message.reply_to_whatsapp_message_id}
    if not references:return {}
    candidates=defaultdict(list)
    for original in db.query(WhatsAppConversationMessage).filter(
        WhatsAppConversationMessage.phone_number==conversation.phone_number,
        WhatsAppConversationMessage.whatsapp_message_id.in_(references)).all():
        candidates[original.whatsapp_message_id].append(original)
    originals={provider_id:rows[0] for provider_id,rows in candidates.items() if len(rows)==1}
    attachments=defaultdict(list)
    if originals:
        for attachment in db.query(WhatsAppMessageAttachment).filter(
            WhatsAppMessageAttachment.message_id.in_([row.id for row in originals.values()])).order_by(WhatsAppMessageAttachment.id).all():
            attachments[attachment.message_id].append(attachment)
    result={}
    for message in messages:
        reference=message.reply_to_whatsapp_message_id
        if not reference:continue
        original=originals.get(reference)
        if original is None or original.id==message.id:
            result[message.id]={'available':False}
            continue
        author=('Customer' if original.direction=='inbound' else
                'Staff' if (original.response_kind or '').startswith('staff') else 'PixiePinks')
        kind='text';summary=original.message_text;preview=None
        media=attachments.get(original.id,[])
        if media:
            attachment=media[0];kind=attachment.media_type
            label={'image':'Photo','sticker':'Sticker','video':'Video','audio':'Voice message','document':'Document'}.get(kind,'Attachment')
            summary=attachment.caption or attachment.filename or label
            if kind in {'image','sticker'}:
                preview=f'/crm/whatsapp/api/conversations/{conversation.id}/messages/{original.id}/attachments/{attachment.id}/media'
        elif original.product_title:
            kind='product';summary=original.product_title
            try:
                image=urlsplit(original.product_image_url or '')
                if image.scheme=='https' and image.netloc=='cdn.shopify.com':preview=original.product_image_url
            except ValueError:pass
        elif original.media_id:
            kind='audio';summary='Voice message'
        result[message.id]={'available':True,'message_id':original.id,'author':author,'type':kind,
                            'text':(summary or '')[:300],'preview_url':preview}
    return result


def interactive_previews(db,conversation,messages):
    """Display retained consent button labels only; never disclose actionable tokens."""
    import json
    from models import MarketingConsentRequest as Request,MarketingConsentEvent as Event
    from marketing_consent_buttons import VERSION,LABELS
    ids={m.whatsapp_message_id for m in messages if m.direction=='outbound' and m.whatsapp_message_id}
    if not ids:return {}
    from sqlalchemy import cast,String
    rows=db.query(Request.whatsapp_message_id,Request.wording_version,Event.reason).join(
        Event,Event.dedup_key=='request-buttons:'+cast(Request.id,String)).filter(
        Request.conversation_id==conversation.id,Request.whatsapp_message_id.in_(ids),Event.consent_id==Request.consent_id,
        Event.event_type=='CONSENT_BUTTONS_PREPARED').all()
    result={}
    for provider,version,reason in rows:
        if not (version or '').startswith(VERSION):continue
        try:
            evidence=json.loads(reason)
            titles=[b['title'] for b in evidence['buttons']]
            if evidence.get('v')==1 and titles==list(LABELS.values()):result[provider]=titles
        except (ValueError,TypeError,KeyError):continue
    from models import CustomerQuestionRun as QuestionRun,CustomerQuestionEvent as QuestionEvent
    events=db.query(QuestionEvent.detail).join(QuestionRun,QuestionEvent.run_id==QuestionRun.id).filter(
        QuestionRun.conversation_id==conversation.id,QuestionEvent.action=='SENT').all()
    for (detail,) in events:
        if isinstance(detail,dict) and detail.get('provider_id') in ids:
            result[detail['provider_id']]=detail.get('options',[])
    return result
