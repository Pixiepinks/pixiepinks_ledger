"""Railway cron entrypoint: python -m learning_worker tick. No web background jobs."""
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
import logging
import re
import time
from uuid import uuid4

from sqlalchemy import or_,func,text
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from models import (WhatsAppConversation,WhatsAppConversationMessage,WhatsAppOrderIntent,SalesOrder,Customer,User)
from learning_models import (LearningLease,LearningSource,LearningJob,LearningAnalysis,LearningCandidate,
    LearningEvidence,LearningFeedback,LearningReservation)
from learning_policy import ANALYZER_VERSION,sanitize,prohibited,INJECTION,content_hash,classify,similar
from learning_analyzer import analyze,reserve_tokens,AnalysisFailure
from learning_service import configuration,config_row,learning_enabled,enqueue,evaluate,can_auto,publish_candidate,prune,LearningError,audit
from learning_schedule import due_slots,day_start

logger=logging.getLogger(__name__)
LOCK_KEY=714206271
LEASE_SECONDS=120


@contextmanager
def worker_lease(engine,sessions):
    owner=str(uuid4());connection=None;held=False
    try:
        if engine.dialect.name=='postgresql':
            connection=engine.connect()
            if not connection.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK_KEY}).scalar():
                yield None;return
            held=True
        with sessions() as db:
            count=db.query(LearningLease).filter(LearningLease.id==1,LearningLease.expires_at<datetime.utcnow()).update(
                {'owner':owner,'expires_at':datetime.utcnow()+timedelta(seconds=LEASE_SECONDS),'last_seen_at':datetime.utcnow()},synchronize_session=False)
            db.commit()
        yield owner if count else None
    finally:
        if owner:
            try:
                with sessions() as db:
                    db.query(LearningLease).filter_by(id=1,owner=owner).update({'owner':None,'expires_at':datetime(1970,1,1)},synchronize_session=False);db.commit()
            except SQLAlchemyError:logger.error('Learning lease release unavailable')
        if connection is not None:
            try:
                if held:connection.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':LOCK_KEY})
            finally:connection.close()


def fence(db,owner):
    row=db.query(LearningLease).filter_by(id=1,owner=owner).one_or_none()
    if row is None or row.expires_at<datetime.utcnow():raise LearningError('Worker lease lost')
    row.expires_at=datetime.utcnow()+timedelta(seconds=LEASE_SECONDS);row.last_seen_at=datetime.utcnow()


def analysis_version(config):
    return ANALYZER_VERSION+':'+config['model']


def collect(db,source,config=None):
    config=config or configuration(db)
    conversation=db.get(WhatsAppConversation,source.conversation_id)
    if conversation is None:return None
    cursor=source.last_message_id if source.analyzer_version==analysis_version(config) else 0
    newer=db.query(WhatsAppConversationMessage).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number,
        WhatsAppConversationMessage.id>cursor,WhatsAppConversationMessage.created_at>=datetime.utcnow()-timedelta(days=config['lookback_days'])).order_by(WhatsAppConversationMessage.id).limit(8).all()
    if not newer:return None
    context=db.query(WhatsAppConversationMessage).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number,
        WhatsAppConversationMessage.id<=cursor).order_by(WhatsAppConversationMessage.id.desc()).limit(4).all()
    rows=list(reversed(context))+newer
    known=[conversation.phone_number,'+'+conversation.phone_number,conversation.customer_name]
    if conversation.customer_name:known+=conversation.customer_name.split()
    orders=db.query(SalesOrder).filter_by(crm_conversation_id=conversation.id).limit(20).all()
    for order in orders:
        known += [order.customer_name,order.delivery_contact_person,order.delivery_address,order.whatsapp_phone,
            order.alternative_phone,order.internal_order_number,order.shopify_order_name,order.shopify_order_id]
    intents=db.query(WhatsAppOrderIntent).filter_by(customer_whatsapp_phone=conversation.phone_number).limit(10).all()
    for intent in intents:known += [intent.contact_person_name,intent.delivery_address,intent.primary_phone,intent.alternative_phone]
    for customer in db.query(Customer).filter(Customer.phone.in_([conversation.phone_number,'+'+conversation.phone_number])).limit(10):known += [customer.name,customer.email,customer.phone]
    staff={name for name, in db.query(User.username).filter(User.role.in_(['ADMIN','SALES_MANAGER','SALES_AGENT','STAFF']))}
    known+=list(staff)
    excerpts=[];handles=[]
    for row in rows:
        if row.product_handle and row.sent_by in staff:handles.append(row.product_handle)
        role='staff' if row.direction=='outbound' and row.response_kind=='staff' and row.sent_by in staff else ('customer' if row.direction=='inbound' and row.response_kind not in {'customer_media','customer_audio'} else None)
        if role is None or INJECTION.search(row.message_text or ''):continue
        clean=sanitize(row.message_text,known)
        # A redacted turn is excluded, not relied upon as a safe training example.
        if not clean or '[' in clean:continue
        excerpts.append({'id':row.id,'role':role,'text':clean})
    eligible=any(row['role']=='staff' and row['id']>cursor for row in excerpts) and any(row['role']=='customer' for row in excerpts)
    outcome='ORDER_CONFIRMED' if any(row.order_status in {'CONFIRMED','FULFILLED'} for row in orders) else 'UNKNOWN'
    correction=db.query(LearningFeedback.id).filter_by(conversation_id=conversation.id,kind='STAFF_CORRECTION').first()
    return {'end':newer[-1].id,'eligible':eligible,'messages':excerpts,'known':known,'handles':handles,
            'outcome':outcome,'evidence_type':'STAFF_RECORDED_CORRECTION' if correction else 'HUMAN_RESPONSE'}


def ingest(db,analysis,source,window,lessons,config,job):
    ids={row['id'] for row in window['messages']};staff_ids={row['id'] for row in window['messages'] if row['role']=='staff'}
    for lesson in lessons:
        refs=set(lesson.source_message_ids)
        if not refs.issubset(ids) or not refs&staff_ids:
            job.candidates_rejected+=1;continue
        raw=lesson.proposed_content
        content=sanitize(raw,window['known']);title=sanitize(lesson.title,window['known'])[:120]
        category,risk=classify(content,lesson.category)
        if content!=raw or title!=lesson.title:risk='PROHIBITED';category='sensitive'
        handle=lesson.product_handle if lesson.product_handle in window['handles'] else None
        fingerprint=content_hash(content,lesson.language,handle)
        candidate=db.query(LearningCandidate).filter_by(content_hash=fingerprint).one_or_none()
        if candidate is None:
            # Bounded lexical near-duplicate detection; no separate vector service.
            candidate=next((row for row in db.query(LearningCandidate).filter_by(language=lesson.language,product_handle=handle).order_by(LearningCandidate.id.desc()).limit(500)
                            if similar(content,row.proposed_content) and set(re.findall(r'\d+',content))==set(re.findall(r'\d+',row.proposed_content))),None)
        if candidate is None:
            candidate=LearningCandidate(category=category,title=title or 'Redacted suggestion',proposed_content=content,
                language=lesson.language,risk_level=risk,confidence_score=lesson.confidence,content_hash=fingerprint,
                product_handle=handle,product_category=sanitize(lesson.product_category)[:100],publication_status='PENDING_REVIEW',
                analysis_summary={'reason':sanitize(lesson.reason,window['known'])[:300],'intent':sanitize(lesson.intent,window['known'])[:120],
                    'strategy':sanitize(lesson.response_strategy,window['known'])[:200],'potential_correction':lesson.potential_correction})
            db.add(candidate);db.flush();job.candidates_generated+=1
        if not db.query(LearningEvidence).filter_by(candidate_id=candidate.id,analysis_id=analysis.id).first():
            excerpt=[row for row in window['messages'] if row['id'] in refs]
            db.add(LearningEvidence(candidate_id=candidate.id,analysis_id=analysis.id,source_id=source.id,
                source_message_references=sorted(refs),sanitized_excerpt=excerpt,evidence_type=window['evidence_type'],outcome_signal=window['outcome']))
        db.flush();evaluation=evaluate(db,candidate,config)
        if evaluation.failed_count or candidate.risk_level=='PROHIBITED':
            candidate.publication_status='REJECTED';job.candidates_rejected+=1
        elif candidate.publication_status!='PUBLISHED':
            if can_auto(db,candidate,evaluation,config):
                publish_candidate(db,candidate,None,automatic=True);job.candidates_auto_published+=1
            else:job.candidates_pending_review+=1


def reserve(db,job,tokens,config):
    daily=db.query(func.coalesce(func.sum(LearningReservation.tokens),0),func.coalesce(func.sum(LearningReservation.cost),0)).filter(LearningReservation.created_at>=day_start()).one()
    cost=Decimal(str(tokens))*Decimal(str(config['usd_per_million_tokens']))/Decimal(1000000)
    if (job.reserved_tokens+tokens>config['max_tokens'] or int(daily[0])+tokens>config['daily_tokens']
        or Decimal(job.estimated_cost)+cost>Decimal(str(config['max_cost'])) or Decimal(daily[1])+cost>Decimal(str(config['daily_cost']))):
        raise LearningError('BUDGET_EXHAUSTED')
    db.add(LearningReservation(job_id=job.id,tokens=tokens,cost=cost))
    job.reserved_tokens+=tokens;job.estimated_cost=Decimal(job.estimated_cost)+cost


def run_job(sessions,owner,job_id,analyzer=analyze):
    with sessions() as db:
        fence(db,owner);job=db.get(LearningJob,job_id);job.status='RUNNING';job.started_at=job.started_at or datetime.utcnow();job.attempts+=1;db.commit()
    processed=0
    while True:
        with sessions() as db:
            fence(db,owner);config=configuration(db);job=db.get(LearningJob,job_id)
            if not learning_enabled(config):job.status='PAUSED';job.completed_at=datetime.utcnow();db.commit();return
            if processed>=config['max_conversations']:job.status='PARTIAL' if job.error_count else 'COMPLETED';job.completed_at=datetime.utcnow();db.commit();return
            source=db.query(LearningSource).join(WhatsAppConversation,LearningSource.conversation_id==WhatsAppConversation.id).filter(
                LearningSource.allowed.is_(True),LearningSource.withdrawn_at.is_(None),LearningSource.evaluation_only.is_(False),
                LearningSource.consent_at.isnot(None),LearningSource.consent_reference.isnot(None),LearningSource.id>job.checkpoint,
                WhatsAppConversation.last_message_at<datetime.utcnow()-timedelta(hours=config['quiet_hours'])).order_by(LearningSource.id).first()
            if source is None:job.status='PARTIAL' if job.error_count else 'COMPLETED';job.completed_at=datetime.utcnow();db.commit();return
            processed+=1
            source_id=source.id;window=collect(db,source,config)
            if not window:job.checkpoint=source.id;db.commit();continue
            analysis=db.query(LearningAnalysis).filter_by(source_id=source.id,end_message_id=window['end'],analyzer_version=analysis_version(config)).one_or_none()
            if analysis and analysis.status in {'COMPLETE','SKIPPED','FAILED'}:
                source.last_message_id=max(source.last_message_id,analysis.end_message_id);source.analyzer_version=analysis_version(config)
                job.checkpoint=source.id;db.commit();continue
            if analysis is None:
                analysis=LearningAnalysis(source_id=source.id,job_id=job.id,end_message_id=window['end'],analyzer_version=analysis_version(config),status='PROCESSING');db.add(analysis);db.flush()
            analysis.job_id=job.id
            analysis_id=analysis.id
            if not window['eligible']:
                analysis.status='SKIPPED';analysis.completed_at=datetime.utcnow();source.last_message_id=window['end'];source.analyzer_version=analysis_version(config)
                job.checkpoint=source.id;db.commit();continue
            # No raw data outside the transaction. Only this sanitized payload is sent.
            from learning_service import approved_entries
            known_entries,_=approved_entries(db)
            excerpt={'messages':window['messages'],'source_products':window['handles'],'outcome':window['outcome'],
                'approved_knowledge':[{'title':sanitize(item.get('title',''))[:120],'content':sanitize(item.get('content',''))[:500]} for item in known_entries[:8]]}
            db.commit()
        lessons=[];usage=0;failure=None
        for attempt in range(3):
            try:
                with sessions() as db:
                    fence(db,owner);job=db.get(LearningJob,job_id);config=configuration(db)
                    if not learning_enabled(config):raise LearningError('LEARNING_PAUSED')
                    if not db.get(LearningSource,source_id).allowed:raise LearningError('SOURCE_WITHDRAWN')
                    reserve(db,job,reserve_tokens(excerpt,config['model']),config);db.commit()
                lessons,usage=analyzer(excerpt,config['model']);failure=None;break
            except AnalysisFailure as error:
                failure=error.code
                if not error.retryable or attempt==2:break
                time.sleep(2**attempt)
            except LearningError as error:
                with sessions() as db:
                    fence(db,owner);job=db.get(LearningJob,job_id);job.status='BUDGET_LIMIT' if str(error)=='BUDGET_EXHAUSTED' else 'PAUSED';job.error_summary='BUDGET_OR_PRIVACY_GATE';job.completed_at=datetime.utcnow();db.commit()
                return
        with sessions() as db:
            fence(db,owner);job=db.get(LearningJob,job_id);source=db.get(LearningSource,source_id);analysis=db.get(LearningAnalysis,analysis_id)
            # Serialize publication with privacy/settings changes before candidate writes.
            config_row(db,True)
            db.refresh(source)  # A withdrawal may have committed while this lock was awaited.
            # Withdrawal or pause while the provider was running fences off all derived writes.
            config=configuration(db)
            if not source.allowed or source.withdrawn_at or not learning_enabled(config):
                analysis.status='SKIPPED';analysis.completed_at=datetime.utcnow();job.status='PAUSED';job.completed_at=datetime.utcnow();db.commit();return
            analysis.completed_at=datetime.utcnow()
            if failure:
                analysis.error_code=failure;job.error_count+=1;job.error_summary=failure
                if failure in {'provider_not_configured','provider_error'}:
                    analysis.status='PROCESSING';job.status='PAUSED_CONFIG';job.completed_at=datetime.utcnow();db.commit();return
                if failure=='provider_transient' and job.attempts<3:
                    analysis.status='PROCESSING';job.status='RETRY';job.next_retry_at=datetime.utcnow()+timedelta(minutes=2**job.attempts);db.commit();return
                analysis.status='FAILED'
            else:
                analysis.status='COMPLETE';job.token_usage+=usage
                ingest(db,analysis,source,window,lessons,config,job)
            source.last_message_id=window['end'];source.analyzer_version=analysis_version(config)
            job.checkpoint=source.id;job.conversations_processed+=1
            db.commit()


def tick(engine,sessions,analyzer=analyze):
    with worker_lease(engine,sessions) as owner:
        if not owner:return 'BUSY'
        with sessions() as db:
            fence(db,owner);config_row(db,True);prune(db);config=configuration(db)
            if not learning_enabled(config):db.commit();return 'DISABLED'
            pending=db.query(LearningJob).filter(LearningJob.status.in_(['QUEUED','RUNNING','RETRY']),or_(LearningJob.next_retry_at.is_(None),LearningJob.next_retry_at<=datetime.utcnow())).order_by(LearningJob.created_at).first()
            if pending is None:
                for _,slot in due_slots(config):
                    key='daily:'+slot+':'+analysis_version(config)
                    if not db.query(LearningJob.id).filter_by(request_key=key).first():
                        pending=enqueue(db,key,'DAILY');break
            job_id=pending.id if pending else None;db.commit()
        if job_id:
            try:run_job(sessions,owner,job_id,analyzer)
            except Exception:
                # No SDK/SQL exception strings: these can contain sensitive parameters.
                logger.error('Learning job interrupted; durable checkpoint retained')
                with sessions() as db:
                    fence(db,owner);job=db.get(LearningJob,job_id);job.status='RETRY' if job.attempts<3 else 'FAILED';job.error_count+=1;job.error_summary='WORKER_INTERRUPTED';job.next_retry_at=datetime.utcnow()+timedelta(minutes=2**min(job.attempts,5));db.commit()
                return 'RETRY'
            return 'PROCESSED'
        return 'IDLE'


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['tick']);parser.parse_args()
    from database import engine,SessionLocal
    try:
        result=tick(engine,SessionLocal);print('learning_worker status='+result)
    except Exception:
        logger.error('Learning worker unavailable; verify additive migrations and database health')
        raise SystemExit(2) from None


if __name__=='__main__':main()
