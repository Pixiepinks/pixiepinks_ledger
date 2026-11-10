"""Administrator mapping UI and safe dry runs; existing Inbox owns per-customer controls."""
from datetime import datetime
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from customer_question_routes import QuestionRoute
from models import User,AdFlowRule as Rule,AdFlowExecution as Execution,AdFlowAudit as Audit,CustomerQuestionFlow as Flow,CustomerQuestionRun as Run,WhatsAppConversation as Conversation,WhatsAppMessageReferral as Referral,WhatsAppConversationMessage as Message
from settings import settings
import ad_flow_service as service
import customer_questions as questions


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(route_class=QuestionRoute)
    def admin(user=Depends(require_user)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can configure ad-based flows')
        return user
    async def body(request):
        csrf(request)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>8192:raise HTTPException(413,'Mapping request is too large')
            raw.extend(chunk)
        try:
            import json
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError()
            return value
        except ValueError:raise HTTPException(422,'Provide mapping JSON') from None
    def serialized(db,r):
        flow=db.get(Flow,r.flow_id)
        counts=dict(db.query(Execution.status,func.count(Execution.id)).filter_by(rule_id=r.id).group_by(Execution.status).all())
        from models import CustomerQuestionEvent,CustomerQuestionProductSend
        accepted=(db.query(CustomerQuestionEvent.id).filter(CustomerQuestionEvent.run_id==Execution.run_id,CustomerQuestionEvent.action=='SENT').exists() | db.query(CustomerQuestionProductSend.id).filter(CustomerQuestionProductSend.run_id==Execution.run_id,CustomerQuestionProductSend.status=='SENT').exists())
        accepted_count=db.query(Execution.id).filter(Execution.rule_id==r.id,Execution.status=='STARTED',accepted).count()
        initial_failures=db.query(Execution).join(Run,Execution.run_id==Run.id).filter(Execution.rule_id==r.id,Run.status.in_(['UNCERTAIN','WINDOW_CLOSED']),~accepted).count()
        latest=db.query(Execution).filter_by(rule_id=r.id).order_by(Execution.id.desc()).first()
        run=db.get(Run,latest.run_id) if latest and latest.run_id else None
        return {'id':r.id,'ad_id':r.ad_id,'name':r.name,'flow_id':r.flow_id,'flow_title':flow.definition['title'] if flow else 'Unavailable',
                'flow_version':r.flow_version,'flow_ready':service.flow_error(db,r) is None,
                'policy':r.policy,'cooldown_hours':r.cooldown_hours,'allow_assigned':r.allow_assigned,'enabled':r.enabled,'version':r.version,
                'created_at':r.created_at.isoformat()+'Z','updated_at':r.updated_at.isoformat()+'Z','starts':counts.get('STARTED',0),'accepted':accepted_count,'failed':counts.get('FAILED',0)+initial_failures,
                'skipped':counts.get('SKIPPED',0),'last_triggered':(lambda at:at.isoformat()+'Z' if at else None)(db.query(func.max(Execution.created_at)).filter_by(rule_id=r.id,status='STARTED').scalar()),
                'status':run.status if run else latest.status if latest else 'NEVER','metadata':metadata(db,r.ad_id)}
    def metadata(db,aid):
        from meta_ads_models import MetaAdsDaily
        from meta_ads_client import account_id
        query=db.query(MetaAdsDaily).filter_by(level='ad',ad_id=aid)
        if account_id():query=query.filter_by(account_id=account_id())
        cached=query.order_by(MetaAdsDaily.synced_at.desc()).first()
        ref=db.query(Referral,Message).join(Message,Referral.message_id==Message.id).filter(Referral.source_id==aid,func.lower(Referral.source_type)=='ad').order_by(Message.created_at.desc()).first()
        cid=db.query(Conversation.id).filter_by(phone_number=ref[1].phone_number).scalar() if ref else None
        return {'name':cached.ad_name if cached else ref[0].headline if ref else None,'campaign':cached.campaign_name if cached else None,'adset':cached.adset_name if cached else None,
            'preview_url':f'/crm/whatsapp/api/conversations/{cid}/messages/{ref[1].id}/referrals/{ref[0].id}/preview' if ref and cid and (ref[0].image_url or ref[0].thumbnail_url) else None}

    @router.get('/crm/ad-flows')
    def page(request:Request,user=Depends(admin)):
        return templates.TemplateResponse('ad_flows.html',{'request':request,'user':user,'page_title':'Ad-Based Flows','csrf_token':csrf_token(request)})

    @router.get('/crm/ad-flows/api')
    def listing(request:Request,q:str=Query('',max_length=100),offset:int=Query(0,ge=0,le=100000),db=Depends(get_db),user=Depends(admin)):
        control=service.control(db);query=db.query(Rule).filter_by(deleted=False)
        if q:query=query.filter(Rule.ad_id.contains(q,autoescape=True)|Rule.name.contains(q,autoescape=True))
        ads=db.query(Referral.source_id,func.max(Referral.headline)).filter(func.lower(Referral.source_type)=='ad').group_by(Referral.source_id).limit(500).all()
        from ad_assignment_service import ad_id
        known={aid:name or aid for aid,name in ads if ad_id(aid)}
        for r in db.query(Rule).filter_by(deleted=False):known.setdefault(r.ad_id,r.name)
        from meta_ads_models import MetaAdsDaily
        for aid,name in db.query(MetaAdsDaily.ad_id,func.max(MetaAdsDaily.ad_name)).filter_by(level='ad').group_by(MetaAdsDaily.ad_id).limit(500):
            if ad_id(aid):known.setdefault(aid,name or aid)
        cfg=questions.config(db,optional=True)
        return {'enabled':control.enabled,'version':control.version,'engine_enabled':bool(cfg and cfg.enabled),'signed_webhooks':bool(settings.META_APP_SECRET),
            'rules':[serialized(db,r) for r in query.order_by(Rule.updated_at.desc()).offset(offset).limit(50)],'total':query.count(),
            'ads':[{'id':aid,'name':name} for aid,name in known.items()],
            'flows':[{'id':f.id,'title':f.definition['title'],'version':f.version,'eligible':service.flow_error(db,Rule(flow_id=f.id,flow_version=f.version)) is None} for f in db.query(Flow).filter_by(archived=False).order_by(Flow.id)],'csrf_token':csrf_token(request)}

    @router.post('/crm/ad-flows/control')
    async def switch(request:Request,db=Depends(get_db),user=Depends(admin)):
        p=await body(request)
        if set(p)!={'enabled','version'} or type(p['enabled']) is not bool or type(p['version']) is not int:raise HTTPException(422,'Provide enabled and current version')
        service.lock(db);control=service.control(db,True)
        if control.version!=p['version']:raise HTTPException(409,'Automation switch changed. Reload')
        if p['enabled'] and not settings.META_APP_SECRET:raise HTTPException(409,'Configure verified webhook signatures before enabling ad flows')
        control.enabled=p['enabled'];control.version+=1
        if not control.enabled:service.pause_mapped(db)
        service.event(db,user,'MASTER',enabled=control.enabled);db.commit();return {'enabled':control.enabled,'version':control.version}

    @router.put('/crm/ad-flows/rules/{aid}')
    async def save(aid:str,request:Request,db=Depends(get_db),user=Depends(admin)):
        p=await body(request)
        aid=aid.strip()
        import re
        required={'name','flow_id','flow_version','policy','cooldown_hours','allow_assigned','enabled','version'}
        if not re.fullmatch(r'[0-9]{1,40}',aid) or set(p)!=required:raise HTTPException(422,'Provide a numeric Ad ID and mapping fields')
        if not isinstance(p['name'],str) or not 1<=len(p['name'].strip())<=120 or not isinstance(p['policy'],str) or p['policy'] not in {'FIRST','REPEAT'} or type(p['cooldown_hours']) is not int or not 1<=p['cooldown_hours']<=8760 or type(p['allow_assigned']) is not bool or type(p['enabled']) is not bool or type(p['version']) is not int or type(p['flow_id']) is not int or type(p['flow_version']) is not int or not 1<=p['flow_id']<=2147483647 or not 0<=p['flow_version']<=2147483647 or not 0<=p['version']<=2147483647 or any(ord(c)<32 for c in p['name']):
            raise HTTPException(422,'Invalid mapping settings')
        service.lock(db);service.control(db,True)
        rule=db.query(Rule).filter_by(ad_id=aid).with_for_update().one_or_none()
        if (rule.version if rule and not rule.deleted else 0)!=p['version']:raise HTTPException(409,'Mapping changed. Reload before saving')
        flow=db.get(Flow,p['flow_id'])
        if not flow or flow.archived or flow.version!=p['flow_version']:raise HTTPException(409,'Choose a current saved flow')
        candidate=Rule(flow_id=flow.id,flow_version=flow.version)
        if p['enabled'] and service.flow_error(db,candidate):raise HTTPException(409,'Saved flow or media is unavailable. Review it in the flow builder')
        before={k:getattr(rule,k) for k in ('flow_id','flow_version','policy','cooldown_hours','allow_assigned','enabled','name')} if rule else None
        if rule is None:
            if db.query(Rule).count()>=500:raise HTTPException(409,'Mapping limit reached')
            rule=Rule(ad_id=aid,version=0);db.add(rule)
        rule.name=p['name'].strip();rule.flow_id=flow.id;rule.flow_version=flow.version;rule.policy=p['policy'];rule.cooldown_hours=p['cooldown_hours'];rule.allow_assigned=p['allow_assigned'];rule.enabled=p['enabled'];rule.deleted=False
        rule.updated_by=user.id;rule.version+=1;rule.updated_at=datetime.utcnow()
        try:
            db.flush()
            if not rule.enabled:service.pause_mapped(db,rule.id)
            service.event(db,user,'SAVE',rule,before=before,version=rule.version)
            db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'Mapping changed. Reload') from None
        return {'rule':serialized(db,rule)}

    @router.post('/crm/ad-flows/rules/{aid}/{action}')
    async def action(aid:str,action:str,request:Request,db=Depends(get_db),user=Depends(admin)):
        p=await body(request);rule=db.query(Rule).filter_by(ad_id=aid,deleted=False).one_or_none()
        if rule is None:raise HTTPException(404,'Mapping not found')
        if action=='test':
            if set(p)-{'conversation_id'}:raise HTTPException(422,'Choose a conversation or simulate a new one')
            cid=p.get('conversation_id')
            if cid is not None and (type(cid) is not int or not 1<=cid<=2147483647):raise HTTPException(422,'Invalid conversation')
            c=db.get(Conversation,cid) if cid is not None else Conversation(mode='AI',last_customer_message_at=datetime.utcnow(),ad_flow_opted_out=False)
            if c is None:raise HTTPException(404,'Conversation not found')
            reason=service.eligibility(db,c,rule,cid is None)
            return {'eligible':reason is None,'reason':reason,'detail':service.REASONS.get(reason,'Would queue this saved flow on a verified matching inbound ad message.'),'sends_messages':False}
        if action=='toggle':
            if set(p)!={'version','enabled'} or type(p['version']) is not int or type(p['enabled']) is not bool:raise HTTPException(422,'Provide enabled and current version')
            service.lock(db);rule=db.query(Rule).filter_by(id=rule.id).populate_existing().with_for_update().one()
            if rule.version!=p['version']:raise HTTPException(409,'Mapping changed. Reload')
            if p['enabled'] and service.flow_error(db,rule):raise HTTPException(409,'Review the saved flow and edit the mapping before enabling')
            rule.enabled=p['enabled'];rule.version+=1;rule.updated_at=datetime.utcnow();rule.updated_by=user.id
            if not rule.enabled:service.pause_mapped(db,rule.id)
            service.event(db,user,'ENABLE' if rule.enabled else 'DISABLE',rule,version=rule.version);db.commit();return {'enabled':rule.enabled}
        if action!='delete' or set(p)!={'version'} or type(p['version']) is not int:raise HTTPException(422,'Choose delete with the current version')
        service.lock(db);rule=db.query(Rule).filter_by(id=rule.id).populate_existing().with_for_update().one()
        if rule.version!=p['version']:raise HTTPException(409,'Mapping changed. Reload')
        rule.deleted=True;rule.enabled=False;rule.version+=1;service.pause_mapped(db,rule.id)
        service.event(db,user,'DELETE',rule,version=rule.version);db.commit();return {'deleted':True}

    @router.get('/crm/ad-flows/history/{aid}')
    def history(aid:str,db=Depends(get_db),user=Depends(admin)):
        rule=db.query(Rule).filter_by(ad_id=aid).one_or_none()
        if rule is None:raise HTTPException(404,'Mapping not found')
        events=[]
        for e in db.query(Execution).filter_by(rule_id=rule.id).order_by(Execution.id.desc()).limit(100):
            run=db.get(Run,e.run_id) if e.run_id else None
            events.append({'id':e.id,'conversation_id':e.conversation_id,'at':e.created_at.isoformat()+'Z','start_result':e.status,'status':run.status if run else e.status,'reason':e.reason,'detail':service.REASONS.get(e.reason,''),'run_id':e.run_id})
        return {'executions':events,'audit':[{'action':a.action,'actor_id':a.actor_id,'at':a.created_at.isoformat()+'Z'} for a in db.query(Audit).filter_by(rule_id=rule.id).order_by(Audit.id.desc()).limit(100)]}

    return router
