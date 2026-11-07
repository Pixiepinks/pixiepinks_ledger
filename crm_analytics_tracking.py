"""Additive local reporting metadata. No transport, consent or AI decisions."""
import logging
from datetime import datetime
from sqlalchemy import event,select,or_,and_
from sqlalchemy.orm import object_session
from sqlalchemy.exc import SQLAlchemyError
from models import WhatsAppConversationMessage as Message,ProcessedWhatsAppMessage,WhatsAppConversation,Lead,User,WhatsAppManualSend,CRMAnalyticsPhone as Phone,CRMAnalyticsDelivery as Delivery
from marketing_identity import phone
from phone_country_service import phone_country

logger=logging.getLogger(__name__)
RANK={'sent':0,'failed':1,'delivered':2,'read':3}


def insert_for(connection,table):
    if connection.dialect.name=='postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


def identity(value):
    normalized=phone(value)
    country=phone_country(normalized) if normalized else None
    return {'raw_phone':value,'canonical_phone':normalized,'country':country['region'] if country else 'ZZ',
            'country_name':country['name'] if country else 'Unknown numbering region'}


def ensure_phone(connection,value):
    if not isinstance(value,str) or not value or len(value)>64:return
    if connection.execute(select(Phone.raw_phone).where(Phone.raw_phone==value)).first():return
    connection.execute(insert_for(connection,Phone.__table__).values(**identity(value)).on_conflict_do_nothing(index_elements=['raw_phone']))


def update_delivery(connection,provider,status,when):
    if not provider or status not in RANK:return
    statement=insert_for(connection,Delivery.__table__).values(provider_id=provider,status=status,status_at=when,rank=RANK[status])
    connection.execute(statement.on_conflict_do_update(index_elements=['provider_id'],set_={
        'status':statement.excluded.status,'status_at':statement.excluded.status_at,'rank':statement.excluded.rank},where=or_(
            Delivery.status_at<when,and_(Delivery.status_at==when,Delivery.rank<RANK[status]))))


def delivery(db,provider,status,when):
    # A reporting failure must not prevent the operational delivery callback.
    try:
        with db.connection().begin_nested():update_delivery(db.connection(),provider,status,when)
    except SQLAlchemyError:logger.warning('Dashboard delivery metadata unavailable; operational callback continues')


def track_phone(mapper,connection,target):
    value=getattr(target,'phone_number',None) or getattr(target,'sender_phone',None) or getattr(target,'mobile',None)
    try:
        with connection.begin_nested():ensure_phone(connection,value)
    except SQLAlchemyError:logger.warning('Dashboard identity metadata unavailable; operational write continues')


def snapshot(mapper,connection,target):
    if target.direction!='outbound':return
    if target.response_kind=='ai':target.analytics_actor_kind='ai';return
    if target.response_kind in {'unsupported','handover'}:target.analytics_actor_kind='system';return
    if target.response_kind in {'bicycle','bicycle_product','image_bicycle','product_images','product_image','image_product'}:target.analytics_actor_kind='automatic';return
    if target.sent_by:
        session=object_session(target);actor=session.info.get('inbox_user') if session else None
        cached=getattr(actor,'__dict__',{})
        actor_id=cached.get('id') if cached.get('username')==target.sent_by else None
        if actor_id is None:
            try:
                with connection.begin_nested():actor_id=connection.execute(select(User.id).where(User.username==target.sent_by,User.role.in_(['ADMIN','STAFF','SALES_AGENT','SALES_MANAGER']))).scalar()
            except SQLAlchemyError:logger.warning('Dashboard actor snapshot unavailable; operational write continues')
        if actor_id:
            target.analytics_actor_id=actor_id;target.analytics_actor_kind='staff'
    kinds={'staff_product':'products','staff_audio':'recorded_voice','staff_saved_voice':'saved_voices'}
    if target.response_kind in kinds:target.analytics_category=kinds[target.response_kind]


def inserted(mapper,connection,target):
    if target.direction!='outbound' or not target.whatsapp_message_id:return
    status=(target.send_status or '').lower()
    if status not in RANK:return
    try:
        with connection.begin_nested():update_delivery(connection,target.whatsapp_message_id,status,target.created_at or datetime.utcnow())
    except SQLAlchemyError:logger.warning('Dashboard delivery metadata unavailable; operational write continues')


for model in [Message,ProcessedWhatsAppMessage,WhatsAppConversation,Lead]:
    event.listen(model,'before_insert',track_phone)
event.listen(WhatsAppConversation,'before_update',track_phone)
event.listen(Lead,'before_update',track_phone)
event.listen(Message,'before_insert',snapshot)
event.listen(Message,'after_insert',inserted)


@event.listens_for(WhatsAppManualSend,'before_insert')
def snapshot_attempt(mapper,connection,target):
    if target.sent_by:
        try:
            with connection.begin_nested():target.analytics_actor_id=connection.execute(select(User.id).where(User.username==target.sent_by,User.role.in_(['ADMIN','STAFF','SALES_AGENT','SALES_MANAGER']))).scalar()
        except SQLAlchemyError:logger.warning('Dashboard attempt snapshot unavailable; operational write continues')
