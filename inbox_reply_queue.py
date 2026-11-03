"""Derived reply turn: independent of unread flags, ownership and CRM lead stages."""
from datetime import datetime
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import aliased
from models import WhatsAppConversationMessage as Message

SUCCESS = ('sent', 'delivered', 'read')


def successful(message):
    # Older automatic sends retained a Meta ID but left send_status empty.
    return or_(func.lower(message.send_status).in_(SUCCESS),
               and_(message.send_status.is_(None), message.whatsapp_message_id.isnot(None),
                    message.whatsapp_message_id != ''))


def summary(db):
    """One row per phone; failed/unknown outgoing messages never clear a wait.

    Timestamp plus local ID breaks ties deterministically. NOT EXISTS preserves
    the oldest customer message since the last successful reply, including when
    an asynchronous failure invalidates what previously looked like a reply.
    """
    reply = aliased(Message)
    answered = db.query(reply.id).filter(
        reply.phone_number == Message.phone_number,
        reply.direction == 'outbound', successful(reply),
        or_(reply.created_at > Message.created_at,
            and_(reply.created_at == Message.created_at, reply.id > Message.id)),
    ).exists()
    pending = db.query(Message.phone_number.label('phone'),
                       func.min(Message.created_at).label('waiting_since')).filter(
        Message.direction == 'inbound', ~answered,
    ).group_by(Message.phone_number).subquery()
    outgoing = db.query(Message.phone_number.label('phone'), func.count(Message.id).label('replies')).filter(
        Message.direction == 'outbound', successful(Message),
    ).group_by(Message.phone_number).subquery()
    return pending, outgoing


def payload(waiting_since, replies, now=None):
    if waiting_since is None:
        return {'reply_queue': 'waiting_customer' if replies else 'no_activity',
                'waiting_since': None, 'waiting_seconds': 0}
    seconds = max(0, int(((now or datetime.utcnow()) - waiting_since).total_seconds()))
    return {'reply_queue': 'needs_reply', 'waiting_since': waiting_since.isoformat() + 'Z',
            'waiting_seconds': seconds}
