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
from learning_diagnostics import environment,source_reason,classify_message,source_metadata
from collections import Counter
import json

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
    floor=datetime.utcnow()-timedelta(days=config['lookback_days'])
    if source.messages_from:floor=max(floor,source.messages_from)
    newer=db.query(WhatsAppConversationMessage).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number,
        WhatsAppConversationMessage.id>cursor,WhatsAppConversationMessage.created_at>=floor).order_by(WhatsAppConversationMessage.id).limit(8).all()
    if not newer:
        outside=db.query(WhatsAppConversationMessage.id).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number,
            WhatsAppConversationMessage.id>cursor,WhatsAppConversationMessage.created_at<floor).first()
        return {'end':cursor,'eligible':False,'no_new':True,'messages':[],
            'skip_reason':('CONSENT_SCOPE_EXCLUDED' if source.messages_from and floor==source.messages_from else 'LOOKBACK_EXCLUDED') if outside else 'NO_NEW_MESSAGES','diagnostic_counts':{}}
    context=db.query(WhatsAppConversationMessage).filter(WhatsAppConversationMessage.phone_number==conversation.phone_number,
        WhatsAppConversationMessage.id<=cursor,WhatsAppConversationMessage.created_at>=floor).order_by(WhatsAppConversationMessage.id.desc()).limit(4).all()
    # Media/AI traffic must not evict the actual customer context preceding a new
    # staff reply. Keep one genuine inbound text within the same consent scope.
    previous_customer=db.query(WhatsAppConversationMessage).filter(
        WhatsAppConversationMessage.phone_number==conversation.phone_number,WhatsAppConversationMessage.id<=cursor,
        WhatsAppConversationMessage.created_at>=floor,WhatsAppConversationMessage.direction=='inbound',
        or_(WhatsAppConversationMessage.response_kind.is_(None),WhatsAppConversationMessage.response_kind=='customer'),
        WhatsAppConversationMessage.media_id.is_(None)).order_by(WhatsAppConversationMessage.id.desc()).first()
    if previous_customer and previous_customer.id not in {row.id for row in context}:context=context[:3]+[previous_customer]
    rows=sorted(context+newer,key=lambda row:row.id)
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
    excerpts=[];handles=[];counts=Counter();reasons=Counter()
    for row in rows:
        origin=classify_message(row,staff);counts[origin]+=1
        if origin=='staff_context' and row.product_handle:handles.append(row.product_handle)
        role=origin if origin in {'staff','customer'} else None
        if role is None:
            reasons['MESSAGE_ORIGIN_UNKNOWN' if origin=='unknown' else 'NON_TEXT_OR_AUTOMATED']+=1;continue
        if INJECTION.search(row.message_text or ''):reasons['UNSAFE_CONTENT']+=1;continue
        clean=sanitize(row.message_text,known)
        if not clean or '[' in clean:reasons['PRIVACY_EXCLUDED']+=1;continue
        excerpts.append({'id':row.id,'role':role,'text':clean})
    human=sum(row['role']=='staff' and row['id']>cursor for row in excerpts)
    customers=sum(row['role']=='customer' for row in excerpts)
    eligible=bool(human and customers)
    skip_reason=None if eligible else ('NO_HUMAN_STAFF_MESSAGES' if not human else 'NO_CUSTOMER_TEXT')
    counts.update({'eligible_human_messages':human,'eligible_customer_messages':customers,'excluded_messages':sum(reasons.values())});counts.update(reasons)
    outcome='ORDER_CONFIRMED' if any(row.order_status in {'CONFIRMED','FULFILLED'} for row in orders) else 'UNKNOWN'
    correction=db.query(LearningFeedback.id).filter_by(conversation_id=conversation.id,kind='STAFF_CORRECTION').first()
    return {'end':newer[-1].id,'eligible':eligible,'messages':excerpts,'known':known,'handles':handles,
            'skip_reason':skip_reason,'diagnostic_counts':dict(counts),'message_exclusions':dict(reasons),
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
        if candidate.evidence_count<config['min_evidence']:
            values=dict(job.diagnostics or {});reasons=dict(values.get('candidate_reasons',{}))
            reasons['INSUFFICIENT_EVIDENCE']=reasons.get('INSUFFICIENT_EVIDENCE',0)+1;values['candidate_reasons']=reasons;job.diagnostics=values
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


def record_reason(job,reason,source=None,window=None,db=None,config=None):
    value=dict(job.diagnostics or {});reasons=dict(value.get('skip_reasons',{}))
    if reason:reasons[reason]=reasons.get(reason,0)+1
    value['skip_reasons']=reasons
    if source is not None:
        skipped=set(value.get('skipped_source_ids',[]))
        if reason:skipped.add(source.id)
        else:skipped.discard(source.id)
        value['skipped_source_ids']=sorted(skipped);value['sources_skipped']=len(skipped)
    if source is not None:
        records=list(value.get('sources',[]));record=source_metadata(db,source,window,config)
        record['reason']=reason or 'PROCESSED';records.append(record);value['sources']=records[-200:]
    job.diagnostics=value


def finish(job,limit=None):
    job.status='PARTIAL' if job.error_count or limit else 'COMPLETED';job.completed_at=datetime.utcnow()
    values=dict(job.diagnostics or {});values['outcome']=('PARTIAL_FAILURE' if job.conversations_processed else 'EXTRACTION_FAILED') if job.error_count else ('PROCESSED' if job.conversations_processed else 'NO_ELIGIBLE_CONVERSATIONS')
    if limit:values['outcome']=limit
    values['duration_seconds']=max(0,(job.completed_at-job.started_at).total_seconds()) if job.started_at else 0
    if not values.get('authorized_sources'):values['skip_reasons']={**values.get('skip_reasons',{}),'NO_AUTHORIZED_SOURCES':1}
    job.diagnostics=values


def run_job(sessions,owner,job_id,analyzer=analyze):
    with sessions() as db:
        fence(db,owner);job=db.get(LearningJob,job_id);job.status='RUNNING';job.started_at=job.started_at or datetime.utcnow();job.attempts+=1
        values=dict(job.diagnostics or {});values['worker_environment']=environment()
        values['authorized_sources']=db.query(LearningSource).filter(LearningSource.allowed.is_(True),LearningSource.withdrawn_at.is_(None),LearningSource.consent_at.isnot(None),LearningSource.consent_reference.isnot(None),func.length(func.trim(LearningSource.consent_reference))>0).count()
        values['total_sources']=db.query(LearningSource).count();job.diagnostics=values
        queued=values.get('queued_environment')
        if queued and queued!=environment():record_reason(job,'CONFIGURATION_MISMATCH')
        db.commit()
    processed=0;scanned_windows=0
    while True:
        with sessions() as db:
            fence(db,owner);config=configuration(db);job=db.get(LearningJob,job_id)
            if not learning_enabled(config):job.status='PAUSED';job.completed_at=datetime.utcnow();record_reason(job,'LEARNING_DISABLED');db.commit();return
            if processed>=config['max_conversations']:finish(job);db.commit();return
            if job.diagnostics.get('sources_evaluated',0)>=500:
                record_reason(job,'SOURCE_SCAN_LIMIT');finish(job,'SOURCE_SCAN_LIMIT');db.commit();return
            source=db.query(LearningSource).filter(LearningSource.id>job.checkpoint).order_by(LearningSource.id).first()
            if source is None:finish(job);db.commit();return
            source_id=source.id
            values=dict(job.diagnostics or {});seen=list(values.get('evaluated_source_ids',[]))
            if source.id not in seen:seen.append(source.id)
            values['evaluated_source_ids']=seen;values['sources_evaluated']=len(seen);job.diagnostics=values
            reason=source_reason(source,db.get(WhatsAppConversation,source.conversation_id),config)
            if reason:
                record_reason(job,reason,source,db=db,config=config);job.checkpoint=source.id
                if values['sources_evaluated']>=500:record_reason(job,'SOURCE_SCAN_LIMIT');finish(job,'SOURCE_SCAN_LIMIT');db.commit();return
                db.commit();continue
            window=collect(db,source,config)
            if not window or window.get('no_new'):
                record_reason(job,(window or {}).get('skip_reason','CONVERSATION_NOT_FOUND'),source,window,db,config)
                job.checkpoint=source.id;db.commit();continue
            scanned_windows+=1
            if scanned_windows>config['max_conversations']*8:
                record_reason(job,'WINDOW_SCAN_LIMIT');finish(job,'WINDOW_SCAN_LIMIT');db.commit();return
            values=dict(job.diagnostics or {});selected_ids=set(values.get('selected_source_ids',[]));selected_ids.add(source.id)
            values['selected_source_ids']=sorted(selected_ids);values['conversations_selected']=len(selected_ids)
            for role,key in [('staff','eligible_human_messages'),('customer','eligible_customer_messages')]:
                ids=set(values.get(key+'_ids',[]));ids.update(row['id'] for row in window['messages'] if row['role']==role)
                values[key+'_ids']=sorted(ids);values[key]=len(ids)
            values['excluded_messages']=values.get('excluded_messages',0)+window['diagnostic_counts'].get('excluded_messages',0)
            job.diagnostics=values
            analysis=db.query(LearningAnalysis).filter_by(source_id=source.id,end_message_id=window['end'],analyzer_version=analysis_version(config)).one_or_none()
            if analysis and analysis.status in {'COMPLETE','SKIPPED'}:
                source.last_message_id=max(source.last_message_id,analysis.end_message_id);source.analyzer_version=analysis_version(config)
                record_reason(job,'ALREADY_PROCESSED',source,window,db,config)
                job.checkpoint=source.id;db.commit();continue
            if analysis is None:
                analysis=LearningAnalysis(source_id=source.id,job_id=job.id,end_message_id=window['end'],analyzer_version=analysis_version(config),status='PROCESSING');db.add(analysis);db.flush()
            analysis.job_id=job.id;analysis.status='PROCESSING'
            analysis_id=analysis.id
            if not window['eligible']:
                analysis.status='SKIPPED';analysis.completed_at=datetime.utcnow();source.last_message_id=window['end'];source.analyzer_version=analysis_version(config)
                record_reason(job,window['skip_reason'],source,window,db,config)
                # Continue scanning this source's bounded windows: non-human/media
                # prefixes must not hide a later genuine staff/customer exchange.
                db.commit();continue
            processed+=1
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
                analysis.status='PROCESSING' if failure in {'provider_not_configured','provider_error'} or (failure=='provider_transient' and job.attempts<3) else 'FAILED'
                record_reason(job,'EXTRACTION_FAILED',source,window,db,config)
                if failure in {'provider_not_configured','provider_error'}:
                    analysis.status='PROCESSING';job.status='PAUSED_CONFIG';job.completed_at=datetime.utcnow();db.commit();return
                if failure=='provider_transient' and job.attempts<3:
                    analysis.status='PROCESSING';job.status='RETRY';job.next_retry_at=datetime.utcnow()+timedelta(minutes=2**job.attempts);db.commit();return
                analysis.status='FAILED'
            else:
                analysis.status='COMPLETE';job.token_usage+=usage
                ingest(db,analysis,source,window,lessons,config,job)
            if not failure:
                source.last_message_id=window['end'];source.analyzer_version=analysis_version(config)
                job.conversations_processed+=1;record_reason(job,None,source,window,db,config)
            job.checkpoint=source.id
            db.commit()


def tick(engine,sessions,analyzer=analyze):
    with worker_lease(engine,sessions) as owner:
        if not owner:return 'BUSY'
        with sessions() as db:
            fence(db,owner);config_row(db,True);prune(db);config=configuration(db)
            if not learning_enabled(config):
                for queued in db.query(LearningJob).filter(LearningJob.status.in_(['QUEUED','RUNNING','RETRY'])).limit(100):
                    queued.status='PAUSED_CONFIG';queued.completed_at=datetime.utcnow()
                    values=dict(queued.diagnostics or {});values['worker_environment']=environment();values['outcome']='LEARNING_DISABLED';queued.diagnostics=values
                    record_reason(queued,'LEARNING_DISABLED')
                    if values.get('queued_environment') and values['queued_environment']!=environment():record_reason(queued,'CONFIGURATION_MISMATCH')
                db.commit();return 'DISABLED'
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
                    fence(db,owner);job=db.get(LearningJob,job_id);job.status='RETRY' if job.attempts<3 else 'FAILED';job.error_count+=1;job.error_summary='WORKER_INTERRUPTED';record_reason(job,'WORKER_INTERRUPTED');job.next_retry_at=datetime.utcnow()+timedelta(minutes=2**min(job.attempts,5));db.commit()
                return 'RETRY'
            return 'PROCESSED'
        return 'IDLE'


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['tick','diagnose']);parser.add_argument('--offset',type=int,default=0);args=parser.parse_args()
    from database import engine,SessionLocal
    try:
        if args.command=='diagnose':
            from learning_diagnostics import diagnose
            with SessionLocal() as db:print(json.dumps(diagnose(db,max(0,args.offset)),ensure_ascii=False));db.rollback()
            return
        result=tick(engine,SessionLocal);print('learning_worker status='+result)
    except Exception:
        logger.error('Learning worker unavailable; verify additive migrations and database health')
        raise SystemExit(2) from None


if __name__=='__main__':main()
