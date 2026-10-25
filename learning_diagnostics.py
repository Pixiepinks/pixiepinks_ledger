"""Count-only, read-only diagnostics; no names, phone numbers, evidence or secrets."""
from collections import Counter
from datetime import datetime,timedelta
from sqlalchemy import func
from models import WhatsAppConversation,WhatsAppConversationMessage
from learning_models import LearningSource,LearningAnalysis
from learning_service import configuration,learning_enabled
from settings import settings


def environment():
    return {key:bool(getattr(settings,key)) for key in ('LEARNING_ENABLED','LEARNING_PUBLICATION_ENABLED',
        'LEARNING_RETRIEVAL_ENABLED','LEARNING_AUTOPUBLISH_ENABLED')} | {
        'openai_configured':bool(settings.OPENAI_API_KEY),'secret_configured':bool(settings.SECRET_KEY and settings.SECRET_KEY!='change-this-in-railway')}


def source_reason(source,conversation,config,now=None):
    if source.withdrawn_at:return 'SOURCE_WITHDRAWN'
    if not source.allowed or not source.consent_at or not (source.consent_reference or '').strip():return 'CONSENT_REQUIRED'
    if source.evaluation_only:return 'EVALUATION_ONLY'
    if conversation is None:return 'CONVERSATION_NOT_FOUND'
    if conversation.last_message_at and conversation.last_message_at>=(now or datetime.utcnow())-timedelta(hours=config['quiet_hours']):return 'QUIET_PERIOD'
    return None


def classify_message(message,staff):
    if message.direction=='inbound':
        if message.response_kind in {None,'customer'} and not message.media_id:return 'customer'
        return 'media'
    if message.direction!='outbound':return 'system'
    if message.response_kind in {'staff','staff_product','staff_audio','staff_image','staff_video','staff_document','staff_media'}:
        if message.sent_by not in staff:return 'unknown'
        if message.send_status not in {None,'sent','delivered','read'}:return 'undelivered'
        if message.response_kind=='staff' and not message.media_id:return 'staff'
        return 'staff_context' if message.response_kind=='staff_product' else 'media'
    if message.response_kind in {'ai','image_ai'} and not message.sent_by:return 'ai'
    if message.response_kind in {'fallback','handover','bicycle','bicycle_state','bicycle_product','image_bicycle','order_intent','product_reply','image_product','product_images','product_image','unsupported'} and not message.sent_by:return 'system'
    return 'unknown'


def source_metadata(db,source,window=None,config=None):
    config=config or configuration(db);conversation=db.get(WhatsAppConversation,source.conversation_id)
    latest=db.query(LearningAnalysis).filter_by(source_id=source.id).order_by(LearningAnalysis.id.desc()).first()
    counts={};scope_counts={}
    floor=datetime.utcnow()-timedelta(days=config['lookback_days'])
    if source.messages_from:floor=max(floor,source.messages_from)
    if conversation:
        # Aggregates only. No message text enters diagnostics or diagnostic logs.
        from models import User
        staff={name for name, in db.query(User.username).filter(User.role.in_(['ADMIN','SALES_MANAGER','SALES_AGENT','STAFF']))}
        rows=db.query(WhatsAppConversationMessage.direction,WhatsAppConversationMessage.response_kind,
            WhatsAppConversationMessage.sent_by,WhatsAppConversationMessage.send_status,
            WhatsAppConversationMessage.media_id.isnot(None).label('has_media'),func.count().label('amount')).filter(
            WhatsAppConversationMessage.phone_number==conversation.phone_number).group_by(
            WhatsAppConversationMessage.direction,WhatsAppConversationMessage.response_kind,WhatsAppConversationMessage.sent_by,
            WhatsAppConversationMessage.send_status,WhatsAppConversationMessage.media_id.isnot(None)).all()
        from types import SimpleNamespace
        result=Counter()
        for row in rows:
            origin=classify_message(SimpleNamespace(direction=row.direction,response_kind=row.response_kind,sent_by=row.sent_by,
                send_status=row.send_status,media_id='present' if row.has_media else None),staff)
            result[origin]+=row.amount
        counts=dict(result)
        scoped=db.query(func.count().filter(WhatsAppConversationMessage.created_at<floor),func.count().filter(WhatsAppConversationMessage.created_at>=floor)).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number).one()
        scope_counts={'excluded_by_time':int(scoped[0]),'within_time_scope':int(scoped[1])}
    return {'source_id':source.id,'conversation_id':source.conversation_id,'allowed':source.allowed,
        'consent_recorded':bool(source.consent_at and source.consent_reference),'withdrawn':bool(source.withdrawn_at),
        'evaluation_only':source.evaluation_only,'consent_at':source.consent_at.isoformat()+'Z' if source.consent_at else None,
        'messages_from':source.messages_from.isoformat()+'Z' if source.messages_from else None,
        'last_message_at':conversation.last_message_at.isoformat()+'Z' if conversation and conversation.last_message_at else None,
        'quiet_until':(conversation.last_message_at+timedelta(hours=config['quiet_hours'])).isoformat()+'Z' if conversation and conversation.last_message_at else None,
        'effective_scope_start':floor.isoformat()+'Z','scope_counts':scope_counts,
        'lookback_days':config['lookback_days'],'quiet_hours':config['quiet_hours'],
        'cursor':source.last_message_id,'analyzer_version':source.analyzer_version,'minimum_evidence':config['min_evidence'],
        'last_analysis_status':latest.status if latest else None,
        'last_analyzed_at':latest.completed_at.isoformat()+'Z' if latest and latest.status=='COMPLETE' and latest.completed_at else None,
        'origin_counts':counts,'window_counts':(window or {}).get('diagnostic_counts',{}),
        'reason':source_reason(source,conversation,config) or (window or {}).get('skip_reason') or ('ELIGIBLE_WINDOW' if (window or {}).get('eligible') else None)}


def diagnose(db,offset=0,limit=50):
    from learning_worker import collect
    config=configuration(db);sources=db.query(LearningSource).order_by(LearningSource.id).offset(offset).limit(min(limit,100)).all()
    return {'environment':environment(),'database_backend':db.bind.dialect.name,'effective_enabled':bool(learning_enabled(config)),
        'scope_policy':'Explicit whole-conversation authorization within lookback, optionally narrowed by messages_from. consent_at is authorization time, not an implicit lower bound.',
        'quiet_hours':config['quiet_hours'],'minimum_evidence':config['min_evidence'],
        'total_sources':db.query(LearningSource).count(),'offset':offset,
        'sources':[source_metadata(db,s,collect(db,s,config) if not source_reason(s,db.get(WhatsAppConversation,s.conversation_id),config) else None,config) for s in sources]}
