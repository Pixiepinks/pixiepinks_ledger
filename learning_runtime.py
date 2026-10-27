"""Fail-open, bounded learned-data retrieval beneath existing bot authority."""
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import re
import threading

from settings import settings

logger=logging.getLogger(__name__)
_context=ContextVar('learning_conversation',default=None)
_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='learning-read')
_slots=threading.BoundedSemaphore(2)


@contextmanager
def reply_scope(conversation_id):
    token=_context.set(conversation_id)
    try:yield
    finally:_context.reset(token)


def _bounded(operation):
    if not _slots.acquire(blocking=False):return None
    def work():
        try:return operation()
        except Exception:
            logger.warning('Learning retrieval/telemetry unavailable; existing reply preserved')
            return None
        finally:_slots.release()
    try:future=_pool.submit(work)
    except RuntimeError:
        logger.warning("Learning executor unavailable; existing reply preserved")
        _slots.release();return None
    try:return future.result(timeout=0.15)
    except TimeoutError:return None


def retrieve(query,catalog=None):
    if not settings.LEARNING_RETRIEVAL_ENABLED:return None
    def lookup():
        from database import SessionLocal
        from sqlalchemy import text
        from models import BotKnowledgeEntry,BotConfiguration
        from learning_models import LearningKnowledgeVersion
        from learning_service import configuration,privacy_ready
        from learning_policy import prohibited,normalized
        with SessionLocal() as db:
            if db.bind.dialect.name=='postgresql':db.execute(text("SET LOCAL statement_timeout = '100ms'"))
            config=configuration(db)
            if not config['publication_enabled'] or not privacy_ready(config):return None
            rows=db.query(BotKnowledgeEntry,LearningKnowledgeVersion).join(LearningKnowledgeVersion,
                LearningKnowledgeVersion.knowledge_item_id==BotKnowledgeEntry.id).filter(
                BotKnowledgeEntry.enabled.is_(True),BotKnowledgeEntry.archived.is_(False)).order_by(LearningKnowledgeVersion.id.desc()).limit(200).all()
            bot=db.query(BotConfiguration).first();snapshot=bot.published_snapshot if bot else {}
            return select_examples(query, catalog, rows, snapshot)
    return _bounded(lookup)


def record(kind,versions=(),conversation_id=None):
    if not settings.LEARNING_RETRIEVAL_ENABLED:return
    cid=conversation_id if conversation_id is not None else _context.get()
    if cid is None:return
    def save():
        from database import SessionLocal
        from learning_models import LearningSource,LearningFeedback
        from learning_service import configuration,privacy_ready
        with SessionLocal() as db:
            resolved=cid
            if isinstance(cid,str):
                from models import WhatsAppConversation
                conversation=db.query(WhatsAppConversation).filter_by(phone_number=cid).first()
                if conversation is None:return
                resolved=conversation.id
            if not privacy_ready(configuration(db)):return
            if not db.query(LearningSource.id).filter_by(conversation_id=resolved,allowed=True,withdrawn_at=None).first():return
            for version in (versions or [None])[:4]:db.add(LearningFeedback(kind=kind,version_id=version,conversation_id=resolved))
            db.commit()
    _bounded(save)


def select_examples(query, catalog, rows, snapshot, *, force_ids=()):
    """Pure shared matcher; production never supplies force_ids."""
    from learning_policy import prohibited, normalized
    handles={p.get('handle') for p in (catalog or []) if isinstance(p,dict)}
    words=set(re.sub(r'[^\w\u0D80-\u0DFF]',' ',normalized(query)).split())
    words-={'a','an','the','is','are','i','you','me','to','for','and','my','it','this','that','please','can','do','of','in','ඔබට','මට','මම'}
    if words&{'hi','hello','hey','ayubowan','ආයුබෝවන්'}:words|={'greeting','hello'}
    if words&{'thanks','thank','sthuthi','ස්තුතියි'}:words|={'acknowledgement','thank'}
    selected=[];seen=set()
    for entry,version in rows:
        if entry.id in seen:continue
        seen.add(entry.id)
        if version.publication_status!='PUBLISHED' or prohibited(entry.content):continue
        if entry.topic=='product' and (entry.scope!='PRODUCT' or entry.scope_reference not in handles):continue
        if entry.scope=='PRODUCT' and entry.scope_reference not in handles:continue
        terms=set(re.sub(r'[^\w\u0D80-\u0DFF]',' ',normalized(entry.title+' '+entry.content+' '+(entry.tags or ''))).split())
        score=len(words&terms)
        if not score and entry.id not in force_ids:continue
        selected.append((score,version.source_type,entry,version))
    selected.sort(key=lambda row:(row[1]!='MANAGER',-row[0]))
    manager=[];automatic=[];versions=[];characters=0
    for _,source,entry,version in selected[:4]:
        value={'title':entry.title[:120],'content':entry.content[:700]}
        characters+=len(value['content'])
        if characters>2000:break
        (automatic if source=='AUTO' else manager).append(value);versions.append(version.id)
    if not versions:return None
    admin=[{'title':str(item.get('title',''))[:100],'content':str(item.get('content',''))[:500]}
           for bucket in ('policies','knowledge','faqs') for item in snapshot.get(bucket,[])
           if item.get('enabled',True) and any(word in normalized(str(item)) for word in words)]
    return {'administrator_policies':admin[:3],'approved_bicycle_and_delivery_rules':{'bicycle':snapshot.get('bicycle',{}),'delivery':snapshot.get('delivery',{})},
        'manager_reviewed_examples':manager,'automatic_communication_examples':automatic,'versions':versions}
