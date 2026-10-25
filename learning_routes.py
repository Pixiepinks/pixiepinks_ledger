"""Authenticated Learning Center APIs. Expensive analysis is never executed here."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
import logging
import re
from zoneinfo import ZoneInfo

from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError,SQLAlchemyError

from models import User,BotKnowledgeEntry,WhatsAppConversation
from learning_models import (LearningCandidate,LearningEvidence,LearningEvaluation,LearningJob,LearningSource,
    LearningKnowledgeVersion,LearningAudit,LearningSettings,LearningAction,LearningFeedback,LearningLease,LearningAnalysis,LearningRegressionCase)
from learning_service import (LearningError,configuration,change_settings,review,source_change,enqueue,
    config_row,audit,disable_knowledge,privacy_ready)
from learning_schedule import next_run,day_start
from learning_diagnostics import environment
from settings import settings

logger=logging.getLogger(__name__)

class SafeLearningRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:
                response=await original(request)
                response.headers['Cache-Control']='private, no-store'
                response.headers['X-Content-Type-Options']='nosniff'
                return response
            except SQLAlchemyError:
                logger.error('Learning database action unavailable; original bot unaffected')
                return JSONResponse({'detail':'Learning service temporarily unavailable'},status_code=503,headers={'Cache-Control':'private, no-store'})
        return handler

MANAGERS={'ADMIN','SALES_MANAGER'}
STAFF=MANAGERS|{'STAFF','SALES_AGENT'}


def authorize(user,role='read'):
    allowed={'read':STAFF,'manage':MANAGERS,'admin':{'ADMIN'}}[role]
    if user.role not in allowed:raise HTTPException(403,'Not authorized for this learning action')


def public(row):
    result={column.name:getattr(row,column.name) for column in row.__table__.columns}
    for key,value in result.items():
        if isinstance(value,datetime):result[key]=value.replace(tzinfo=timezone.utc).isoformat()
        elif type(value).__name__=='Decimal':result[key]=float(value)
    if row.__tablename__=='learning_jobs':result.pop('diagnostics',None)
    return result


def job_summary(row):
    data=public(row);data.pop('request_key',None);diagnostics=row.diagnostics or {}
    data['summary']={key:diagnostics[key] for key in ['outcome','authorized_sources','total_sources','sources_evaluated','sources_skipped','conversations_selected','eligible_human_messages','eligible_customer_messages','excluded_messages','skip_reasons','duration_seconds','candidate_reasons'] if key in diagnostics}
    return data


async def read_body(request):
    data=bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data)+len(chunk)>16384:raise HTTPException(413,'Learning request is too large')
                data.extend(chunk)
        result=json.loads(data)
    except (ValueError,UnicodeError):raise HTTPException(400,'Invalid JSON') from None
    except TimeoutError:raise HTTPException(408,'Learning request timed out') from None
    if not isinstance(result,dict):raise HTTPException(422,'JSON object required')
    return result


def mutate(db,user,key,path,body,operation):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):raise HTTPException(400,'An Idempotency-Key is required')
    fingerprint=hashlib.sha256(json.dumps([path,body],sort_keys=True).encode()).hexdigest()
    row=db.get(LearningAction,key)
    if row:
        if row.actor_id!=user.id or row.fingerprint!=fingerprint:raise HTTPException(409,'Idempotency key belongs to another action')
        return row.result
    try:
        receipt=LearningAction(request_key=key,actor_id=user.id,fingerprint=fingerprint,result={});db.add(receipt);db.flush()
        result=operation();receipt.result=result;db.commit();return result
    except IntegrityError:
        db.rollback();row=db.get(LearningAction,key)
        if row and row.actor_id==user.id and row.fingerprint==fingerprint:return row.result
        raise HTTPException(409,'A conflicting learning action already exists') from None
    except (LearningError,ValueError) as error:
        db.rollback();raise HTTPException(409,str(error)) from None


def feedback_metrics(db,start,end):
    rows=db.query(LearningFeedback).filter(LearningFeedback.created_at>=start,LearningFeedback.created_at<end).all()
    ai={row.conversation_id for row in rows if row.kind=='AI_REPLY' and row.conversation_id is not None}
    corrected={row.conversation_id for row in rows if row.kind=='STAFF_CORRECTION'}&ai
    takeover={row.conversation_id for row in rows if row.kind=='HUMAN_TAKEOVER'}&ai
    return {'observed_ai_conversations':len(ai),'recorded_corrections':len(corrected),
        'correction_rate':len(corrected)/len(ai) if ai else None,'takeover_rate':len(takeover)/len(ai) if ai else None,
        'fallback_conversations':len({row.conversation_id for row in rows if row.kind=='AI_FALLBACK'}),
        'repeat_clarifications':sum(row.kind=='REPEAT_CLARIFICATION' for row in rows),
        'retrieved_version_uses':sum(row.kind=='AI_REPLY' for row in rows),
        'resolved_annotations':sum(row.kind=='RESOLVED' for row in rows),'unresolved_annotations':sum(row.kind=='UNRESOLVED' for row in rows)}


def build_router(get_db,require_user,require_csrf,csrf_token):
    router=APIRouter(prefix='/crm/bot-management/learning/api',route_class=SafeLearningRoute)
    @router.get('/dashboard')
    def dashboard(request:Request,response:Response,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store';config=configuration(db)
        today=day_start();now=datetime.utcnow();local=now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo'));week=today-timedelta(days=local.weekday())
        def analyzed(since):return db.query(func.count(func.distinct(LearningAnalysis.source_id))).filter(LearningAnalysis.status=='COMPLETE',LearningAnalysis.completed_at>=since).scalar()
        counts={status:count for status,count in db.query(LearningCandidate.publication_status,func.count()).group_by(LearningCandidate.publication_status)}
        costs=db.query(func.coalesce(func.sum(LearningJob.token_usage),0),func.coalesce(func.sum(LearningJob.estimated_cost),0)).one()
        last=db.query(LearningJob).filter_by(status='COMPLETED').order_by(LearningJob.completed_at.desc()).first();lease=db.get(LearningLease,1)
        return {'today_analyzed':analyzed(today),'week_analyzed':analyzed(week),'candidates':counts,
            'new_candidates':db.query(LearningCandidate).filter(LearningCandidate.created_at>=today).count(),
            'auto_published_today':db.query(LearningKnowledgeVersion).filter(LearningKnowledgeVersion.source_type=='AUTO',LearningKnowledgeVersion.created_at>=today).count(),
            'published_knowledge':db.query(BotKnowledgeEntry).join(LearningCandidate,LearningCandidate.knowledge_entry_id==BotKnowledgeEntry.id).filter(BotKnowledgeEntry.enabled.is_(True)).count(),
            'last_successful_run':job_summary(last) if last else None,'next_schedule_due':next_run(config) if config['enabled'] else None,
            'worker_last_seen':lease.last_seen_at.isoformat()+'Z' if lease and lease.last_seen_at else None,
            'latest_run':job_summary(db.query(LearningJob).order_by(LearningJob.created_at.desc()).first()) if db.query(LearningJob.id).first() else None,
            'web_environment':environment(),
            'schedule_verified_active':False,'actual_tokens':int(costs[0]),'reserved_cost_estimate':float(costs[1]),
            'errors':int(db.query(func.coalesce(func.sum(LearningJob.error_count),0)).scalar()),
            'observations':feedback_metrics(db,now-timedelta(days=7),now),'baseline':feedback_metrics(db,now-timedelta(days=14),now-timedelta(days=7)),
            'causation_note':'Observational, consented samples only; takeover is not proof of an AI error. Rates are not causal improvement estimates.',
            'settings':config,'environment_gates':{'learning':settings.LEARNING_ENABLED,'publication':settings.LEARNING_PUBLICATION_ENABLED,'automatic':settings.LEARNING_AUTOPUBLISH_ENABLED,'retrieval':settings.LEARNING_RETRIEVAL_ENABLED},
            'csrf_token':csrf_token(request),'role':user.role}

    @router.get('/candidates')
    def candidates(response:Response,q:str=Query('',max_length=100),status:str=Query('',max_length=24),language:str=Query('',pattern='^(en|si|mixed)?$'),category:str=Query('',max_length=24),
        product_category:str=Query('',max_length=100),conflicts:bool=False,date_from:datetime|None=None,date_to:datetime|None=None,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store';query=db.query(LearningCandidate)
        if q:query=query.filter(LearningCandidate.title.ilike('%'+q+'%'))
        if status=='AUTO_PUBLISHED':query=query.filter_by(auto_published=True,publication_status='PUBLISHED')
        elif status:query=query.filter_by(publication_status=status)
        if language:query=query.filter_by(language=language)
        if category:query=query.filter_by(category=category)
        if product_category:query=query.filter_by(product_category=product_category)
        if date_from:query=query.filter(LearningCandidate.created_at>=(date_from.astimezone(timezone.utc).replace(tzinfo=None) if date_from.tzinfo else date_from))
        if date_to:query=query.filter(LearningCandidate.created_at<(date_to.astimezone(timezone.utc).replace(tzinfo=None) if date_to.tzinfo else date_to))
        if conflicts:
            ids=[row.candidate_id for row in db.query(LearningEvaluation).filter(LearningEvaluation.factuality_result.is_(False)).limit(1000)]
            query=query.filter(LearningCandidate.id.in_(ids))
        return {'total':query.count(),'items':[public(row) for row in query.order_by(LearningCandidate.id.desc()).offset(offset).limit(50)]}

    @router.get('/candidates/{cid}')
    def candidate_detail(cid:int,response:Response,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store';candidate=db.get(LearningCandidate,cid)
        if not candidate:raise HTTPException(404,'Candidate not found')
        product=None
        if candidate.product_handle:
            from shopify_catalog_service import get_product_by_handle,ShopifyCatalogError
            try:
                found=get_product_by_handle(candidate.product_handle)
                if found:product={**{k:found.get(k) for k in ['handle','title','price','available','product_type','status']},'variants':[{key:variant.get(key) for key in ['title','sku','price','available']} for variant in found.get('variants',[])[:30]]}
            except ShopifyCatalogError:product={'unavailable':True}
        return {'candidate':public(candidate),'evidence':[public(row) for row in db.query(LearningEvidence).filter_by(candidate_id=cid).limit(50)],
            'evaluations':[public(row) for row in db.query(LearningEvaluation).filter_by(candidate_id=cid).order_by(LearningEvaluation.id.desc()).limit(20)],
            'versions':[public(row) for row in db.query(LearningKnowledgeVersion).filter_by(candidate_id=cid).order_by(LearningKnowledgeVersion.id.desc()).limit(50)],'current_shopify_product':product}

    @router.post('/candidates/{cid}/{action}')
    async def candidate_action(cid:int,action:str,request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');require_csrf(request);body=await read_body(request)
        if 'version_id' in body and (type(body['version_id']) is not int or body['version_id']<1):raise HTTPException(422,'Invalid version reference')
        if set(body)-{'content','title','verified','version_id'}:raise HTTPException(422,'Unknown review field')
        def operation():
            candidate=review(db,cid,action,user.id,body)
            return {'id':candidate.id,'status':candidate.publication_status}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)

    @router.get('/knowledge/{entry_id}/versions')
    def versions(entry_id:int,response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store'
        latest=db.query(LearningKnowledgeVersion).filter_by(knowledge_item_id=entry_id).order_by(LearningKnowledgeVersion.version_number.desc()).first()
        if not latest:raise HTTPException(404,'Learned knowledge not found')
        return {'items':[{**public(row),'effective_status':row.publication_status if row.id==latest.id else 'SUPERSEDED'}
            for row in db.query(LearningKnowledgeVersion).filter_by(knowledge_item_id=entry_id).order_by(LearningKnowledgeVersion.id.desc()).offset(offset).limit(50)]}

    @router.get('/candidates/{cid}/evaluations')
    def evaluations(cid:int,response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store'
        if not db.get(LearningCandidate,cid):raise HTTPException(404,'Candidate not found')
        return {'items':[public(row) for row in db.query(LearningEvaluation).filter_by(candidate_id=cid).order_by(LearningEvaluation.id.desc()).offset(offset).limit(50)]}

    @router.get('/settings')
    def get_settings(request:Request,response:Response,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store'
        return {'settings':configuration(db),'csrf_token':csrf_token(request)}

    @router.patch('/settings')
    async def update_settings(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'admin');require_csrf(request);body=await read_body(request)
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,lambda:{'settings':change_settings(db,body,user.id)})

    @router.get('/sources')
    def sources(response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');response.headers['Cache-Control']='private, no-store'
        return {'items':[public(row) for row in db.query(LearningSource).order_by(LearningSource.id.desc()).offset(offset).limit(50)]}

    @router.post('/sources')
    async def update_source(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'admin');require_csrf(request);body=await read_body(request)
        if set(body)-{'conversation_id','allowed','consent_reference','erase','legal_hold','evaluation_only','messages_from'} or type(body.get('conversation_id')) is not int or type(body.get('allowed')) is not bool:
            raise HTTPException(422,'A conversation ID and boolean eligibility are required')
        if any(type(body.get(key,False)) is not bool for key in ['erase','legal_hold','evaluation_only']) or not isinstance(body.get('consent_reference',''),str) or len(body.get('consent_reference',''))>200:raise HTTPException(422,'Invalid privacy settings')
        scope=None
        if body.get('messages_from') is not None and (not isinstance(body['messages_from'],str) or len(body['messages_from'])>64):raise HTTPException(422,'Invalid message scope timestamp')
        if body.get('messages_from'):
            try:
                scope=datetime.fromisoformat(body['messages_from'].replace('Z','+00:00'))
                if scope.tzinfo is None:raise ValueError()
                scope=scope.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError,AttributeError,TypeError):raise HTTPException(422,'Scope start must include a timezone') from None
        def operation():
            row=source_change(db,body['conversation_id'],body['allowed'],user.id,body.get('consent_reference',''),body.get('legal_hold',False),body.get('erase',False),body.get('evaluation_only',False),scope)
            return {'id':row.id,'allowed':row.allowed}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)

    @router.get('/regression-cases')
    def regression_cases(response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store'
        return {'items':[public(row) for row in db.query(LearningRegressionCase).order_by(LearningRegressionCase.id.desc()).offset(offset).limit(50)]}

    @router.post('/regression-cases')
    async def add_case(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');require_csrf(request);body=await read_body(request)
        if set(body)!={'source_id','message_ids','expected_behavior','language'} or type(body['source_id']) is not int or not isinstance(body['expected_behavior'],str) or body['expected_behavior'] not in {'SHOPIFY_AUTHORITY','SECURITY','WORKFLOW'} or not isinstance(body['language'],str) or body['language'] not in {'en','si','mixed'} or not isinstance(body['message_ids'],list) or not 1<=len(body['message_ids'])<=12 or not all(type(i) is int for i in body['message_ids']):
            raise HTTPException(422,'Invalid held-out evaluation case')
        def operation():
            config_row(db,True);source=db.get(LearningSource,body['source_id'])
            if not source or not source.allowed or not source.evaluation_only or source.withdrawn_at:raise LearningError('Eligible evaluation-only source required')
            from learning_worker import collect
            from learning_policy import content_hash
            window=collect(db,source)
            messages=[row for row in (window or {}).get('messages',[]) if row['id'] in body['message_ids']]
            if {row['id'] for row in messages}!=set(body['message_ids']):raise LearningError('Message references are outside the sanitized source window')
            question='\n'.join(row['text'] for row in messages if row['role']=='customer')[:800]
            if not question:raise LearningError('A sanitized customer question is required')
            row=LearningRegressionCase(source_id=source.id,source_message_references=body['message_ids'],sanitized_question=question,
                expected_behavior=body['expected_behavior'],language=body['language'],case_hash=content_hash(question,body['language'],body['expected_behavior']),approved_by=user.id)
            db.add(row);db.flush();audit(db,user.id,'REGRESSION_CASE_APPROVED','regression',row.id)
            return {'id':row.id}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)

    @router.post('/run')
    async def manual_run(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');require_csrf(request);body=await read_body(request)
        if body:raise HTTPException(422,'No run parameters are accepted')
        def operation():
            row=enqueue(db,'manual:'+request.headers.get('Idempotency-Key',''));return {'job_id':row.id,'status':row.status}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)

    @router.get('/history')
    def history(response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store'
        return {'jobs':[job_summary(row) for row in db.query(LearningJob).order_by(LearningJob.created_at.desc()).offset(offset).limit(50)],
            'audit':[public(row) for row in db.query(LearningAudit).order_by(LearningAudit.id.desc()).offset(offset).limit(50)]}

    @router.get('/jobs/{jid}')
    def job_status(jid:str,response:Response,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user);response.headers['Cache-Control']='private, no-store';row=db.get(LearningJob,jid)
        if row is None:raise HTTPException(404,'Job not found')
        return job_summary(row)

    @router.get('/jobs/{jid}/diagnostics')
    def job_diagnostics(jid:str,response:Response,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');response.headers['Cache-Control']='private, no-store'
        row=db.get(LearningJob,jid)
        if row is None:raise HTTPException(404,'Job not found')
        return {'job':job_summary(row),'diagnostics':row.diagnostics or {},'historical_available':bool(row.diagnostics)}

    @router.get('/diagnostics/sources')
    def source_diagnostics(response:Response,offset:int=Query(0,ge=0),db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'admin');response.headers['Cache-Control']='private, no-store'
        from learning_diagnostics import diagnose
        return diagnose(db,offset)

    @router.post('/feedback')
    async def feedback(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'manage');require_csrf(request);body=await read_body(request)
        if set(body)-{'kind','version_id','conversation_id'} or not isinstance(body.get('kind'),str) or body.get('kind') not in {'STAFF_CORRECTION','HUMAN_TAKEOVER','REPEAT_CLARIFICATION','RESOLVED','UNRESOLVED','REGRESSION'}:
            raise HTTPException(422,'Invalid feedback type')
        def operation():
            config_row(db,True);cid=body.get('conversation_id');vid=body.get('version_id')
            if type(cid) is not int or not db.query(LearningSource).filter_by(conversation_id=cid,allowed=True,withdrawn_at=None).first():raise LearningError('Eligible conversation required')
            if vid is not None and (type(vid) is not int or db.get(LearningKnowledgeVersion,vid) is None):raise LearningError('Version not found')
            db.add(LearningFeedback(kind=body['kind'],version_id=vid,conversation_id=cid,actor_id=user.id))
            if body['kind']=='REGRESSION':
                if vid is not None:
                    version=db.get(LearningKnowledgeVersion,vid)
                    db.get(LearningCandidate,version.candidate_id).rejection_reason='REGRESSION_FLAGGED'
                row=config_row(db);row.config={**configuration(db),'auto_paused':True};audit(db,user.id,'AUTO_PAUSED_REGRESSION','version',vid)
            return {'recorded':True}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)

    @router.post('/emergency-pause')
    async def emergency(request:Request,db=Depends(get_db),user:User=Depends(require_user)):
        authorize(user,'admin');require_csrf(request);body=await read_body(request)
        if set(body)-{'rollback_all'} or type(body.get('rollback_all',False)) is not bool:raise HTTPException(422,'Invalid emergency action')
        def operation():
            change_settings(db,{'auto_paused':True,'auto_publish':False},user.id)
            if body.get('rollback_all'):
                for candidate in db.query(LearningCandidate).filter(LearningCandidate.knowledge_entry_id.isnot(None)):
                    if db.get(BotKnowledgeEntry,candidate.knowledge_entry_id).enabled:disable_knowledge(db,candidate,user.id,'EMERGENCY_ROLLBACK')
            return {'paused':True}
        return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)
    return router
