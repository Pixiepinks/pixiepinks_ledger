"""Derived reply turn: independent of unread flags, ownership and CRM lead stages."""
from datetime import datetime
from sqlalchemy import and_, or_, func, union_all, select, case
from sqlalchemy.orm import aliased
from models import WhatsAppConversationMessage as Message,WhatsAppConversation as Conversation,ConversationWorkState as Work

SUCCESS = ('sent', 'delivered', 'read')


def successful(message):
    # Older automatic sends retained a Meta ID but left send_status empty.
    return or_(func.lower(message.send_status).in_(SUCCESS),
               and_(message.send_status.is_(None), message.whatsapp_message_id.isnot(None),
                    message.whatsapp_message_id != ''))


def raw_summary(db,operational=False):
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
    pending_query = db.query(Message.phone_number.label('phone'),
                       func.min(Message.created_at).label('waiting_since')).filter(
        Message.direction == 'inbound', ~answered,
    )
    if operational:
        pending_query=pending_query.outerjoin(Conversation,Conversation.phone_number==Message.phone_number).outerjoin(Work,Work.conversation_id==Conversation.id).filter(
            or_(Work.conversation_id.is_(None),and_(Message.id>Work.ack_inbound_id,or_(Work.review_cutoff_at.is_(None),Message.created_at>=Work.review_cutoff_at))))
    pending=pending_query.group_by(Message.phone_number).subquery()
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


def summary(db):
    """Authoritative operational wait: derived turns plus explicit staff overrides."""
    pending,outgoing=raw_summary(db,operational=True)
    inherited=select(pending.c.phone,pending.c.waiting_since).select_from(pending).outerjoin(
        Conversation,Conversation.phone_number==pending.c.phone).outerjoin(Work,Work.conversation_id==Conversation.id).where(
        or_(Work.status.is_(None),Work.status=='NEEDS_REPLY'))
    replied=select(Message.id).where(Message.phone_number==Conversation.phone_number,Message.direction=='outbound',
        successful(Message),Message.id>Work.baseline_id,Message.created_at>=Work.updated_at).exists()
    forced=select(Conversation.phone_number.label('phone'),case((Work.updated_by.is_(None),Work.inbound_at),else_=Work.updated_at).label('waiting_since')).select_from(Conversation).join(
        Work,Work.conversation_id==Conversation.id).where(Work.status=='NEEDS_REPLY',Work.force_reply.is_(True),~replied)
    combined=union_all(inherited,forced).subquery()
    return select(combined.c.phone,func.min(combined.c.waiting_since).label('waiting_since')).group_by(combined.c.phone).subquery(),outgoing
