"""Staff snippet validation, permissions and read-only selection queries."""
import re
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import or_,func
from models import WhatsAppQuickMessage,WhatsAppConversationMessage

MANAGER_ROLES={'ADMIN','SALES_MANAGER'}
STAFF_ROLES=MANAGER_ROLES|{'STAFF','SALES_AGENT'}
FIELDS={'title','shortcut','category','language','body','active','display_order'}


def authorize(user,manage=False):
    if user.role not in (MANAGER_ROLES if manage else STAFF_ROLES):
        raise HTTPException(403,'You are not authorized for this Quick Messages action')


def validate(payload):
    if not isinstance(payload,dict) or set(payload)!=FIELDS:
        raise HTTPException(422,'Provide title, shortcut, category, language, body, active and display_order only')
    values={}
    for key,limit in [('title',100),('shortcut',41),('category',80),('body',2000)]:
        value=payload[key]
        if not isinstance(value,str) or not 0<len(value.strip())<=limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
            raise HTTPException(422,f'Invalid {key}; maximum {limit} characters')
        if key!='body' and any(c in value for c in '\n\r\t'):
            raise HTTPException(422,f'Invalid {key}')
        values[key]=value.strip()
    values['shortcut']=values['shortcut'].removeprefix('/').lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',values['shortcut']):
        raise HTTPException(422,'Shortcut must use 1–40 letters, digits, hyphens or underscores and start with a letter')
    if not isinstance(payload['language'],str) or payload['language'] not in {'en','si'} or type(payload['active']) is not bool or type(payload['display_order']) is not int or not 0<=payload['display_order']<=10000:
        raise HTTPException(422,'Invalid language, active status or display order')
    return {**values,'language':payload['language'],'active':payload['active'],'display_order':payload['display_order']}


def serialize(row):
    return {**{key:getattr(row,key) for key in FIELDS},'id':row.id,'usage_count':row.usage_count,
            'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat(),
            'created_by':row.created_by,'updated_by':row.updated_by}


def listing(db,q='',category='',language='',status='',manage=False,offset=0):
    query=db.query(WhatsAppQuickMessage)
    if not manage:query=query.filter_by(active=True)
    elif status in {'active','inactive'}:query=query.filter_by(active=status=='active')
    if q:
        # Search treats wildcard characters literally, including ordinary URLs.
        pattern='%'+q.lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        query=query.filter(or_(*[func.lower(getattr(WhatsAppQuickMessage,key)).like(pattern,escape='\\') for key in ['title','shortcut','body','category']]))
    if category:query=query.filter_by(category=category)
    if language:query=query.filter_by(language=language)
    total=query.count()
    rows=query.order_by(WhatsAppQuickMessage.category,WhatsAppQuickMessage.display_order,WhatsAppQuickMessage.title,WhatsAppQuickMessage.id).offset(offset).limit(100).all()
    categories=db.query(WhatsAppQuickMessage.category).distinct()
    if not manage:categories=categories.filter(WhatsAppQuickMessage.active.is_(True))
    return {'messages':[serialize(row) for row in rows],'categories':[value for value, in categories.order_by(WhatsAppQuickMessage.category).limit(100)],'total':total,'offset':offset,'limit':100}


def require_reply_window(db,conversation):
    latest=conversation.last_customer_message_at
    # Legacy conversations may predate the timestamp column. Only an actual
    # inbound message can establish the customer-service window.
    if latest is None:
        latest=db.query(func.max(WhatsAppConversationMessage.created_at)).filter_by(phone_number=conversation.phone_number,direction='inbound').scalar()
    if latest is None or not timedelta(0)<=datetime.utcnow()-latest<timedelta(hours=24):
        raise HTTPException(409,'WhatsApp reply window is closed. A customer reply or approved Meta template is required. This Inbox does not send approved Meta templates.')
