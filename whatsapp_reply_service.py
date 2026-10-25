"""Persisted quote references and conversation-scoped, token-free reply previews."""
import re
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
        elif original.media_id:
            kind='audio';summary='Voice message'
        elif original.product_title:
            kind='product';summary=original.product_title
        result[message.id]={'available':True,'message_id':original.id,'author':author,'type':kind,
                            'text':(summary or '')[:300],'preview_url':preview}
    return result
