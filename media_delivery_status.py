"""Numeric-only asynchronous media delivery evidence; no upstream private text."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, case, and_
from database import Base


class MediaDeliveryStatus(Base):
    __tablename__ = 'media_delivery_statuses'
    message_id = Column(Integer, ForeignKey('whatsapp_conversation_messages.id'), primary_key=True)
    status = Column(String(16), nullable=False)
    error_code = Column(Integer)
    error_subcode = Column(Integer)
    event_at = Column(DateTime, nullable=False, default=datetime.utcnow)


MESSAGES = {
    131053: 'WhatsApp could not process the media. Try a newly recorded or converted clip.',
    131026: 'WhatsApp could not deliver to this recipient. Check recipient availability before another attempt.',
    131047: 'The WhatsApp customer-service window is closed. Ask the customer to message again.',
    131051: 'WhatsApp rejected the message type.',
    131052: 'WhatsApp could not retrieve the media.',
}


def number(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def failure(row):
    if not row or row.status != 'failed': return None
    return {'code': row.error_code, 'subcode': row.error_subcode,
        'message': MESSAGES.get(row.error_code, 'WhatsApp reported a delivery failure. Review the error before resending.')}


def record(db, provider_id, status, errors, when):
    from models import WhatsAppConversationMessage as Message
    from crm_analytics_tracking import insert_for
    message = db.query(Message).filter_by(whatsapp_message_id=provider_id, response_kind='staff_media', direction='outbound').one_or_none()
    if not message: return None
    error = errors[0] if isinstance(errors,list) and errors and isinstance(errors[0],dict) else {}
    code, subcode = (number(error.get('code')), number(error.get('error_subcode'))) if status == 'failed' else (None,None)
    statement = insert_for(db.connection(), MediaDeliveryStatus.__table__).values(message_id=message.id,
        status=status, error_code=code, error_subcode=subcode, event_at=when)
    rank = {'sent':1,'failed':2,'delivered':3,'read':4}
    current_rank = case(rank, value=MediaDeliveryStatus.status, else_=0)
    db.execute(statement.on_conflict_do_update(index_elements=['message_id'], set_={
        'status':status,'error_code':MediaDeliveryStatus.error_code if status == 'failed' and code is None else code,
        'error_subcode':MediaDeliveryStatus.error_subcode if status == 'failed' and subcode is None else subcode,'event_at':when},
        where=and_(MediaDeliveryStatus.event_at <= when, current_rank <= rank[status])))
    row = db.query(MediaDeliveryStatus).filter_by(message_id=message.id).populate_existing().one()
    message.send_status = row.status
    return row
