"""Administrator frontend for local ad-to-staff routing, independent of Meta sync."""
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from crm_dashboard_routes import DashboardRoute
from models import (AdAssignmentRule as Rule, AdAssignmentRuleAudit as RuleAudit,User,
                    ConversationAssignmentAudit as Assignment,WhatsAppMessageReferral as Referral)
from settings import settings
from whatsapp_referral_service import public_referral
import ad_assignment_service as service


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(route_class=DashboardRoute)
    def admin(user=Depends(require_user)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can manage ad assignments')
        return user

    def serialize(db,rule):
        staff=db.get(User,rule.staff_user_id)
        return {'ad_id':rule.ad_id,'label':rule.label,'staff_user_id':rule.staff_user_id,
            'staff_name':staff.username if staff else 'Unavailable staff','staff_eligible':service.eligible(staff),
            'enabled':rule.enabled,'version':rule.version,'updated_at':rule.updated_at.isoformat()+'Z',
            'assignments':db.query(Assignment).filter(Assignment.rule_id==rule.id).count()}

    @router.get('/crm/ad-assignments')
    def page(request:Request,user=Depends(admin)):
        return templates.TemplateResponse('ad_assignments.html',{'request':request,'user':user,'page_title':'Ad Assignments',
            'csrf_token':csrf_token(request)},headers={'Cache-Control':'private, no-store'})

    @router.get('/crm/ad-assignments/api')
    def listing(request:Request,q:str=Query('',max_length=100),offset:int=Query(0,ge=0,le=100000),db=Depends(get_db),user=Depends(admin)):
        query=db.query(Rule)
        if q:query=query.filter((Rule.ad_id.contains(q,autoescape=True)) | (Rule.label.contains(q,autoescape=True)))
        staff=db.query(User).order_by(User.username).all()
        ads=db.query(Referral.source_id,func.max(Referral.headline),func.count(Referral.id)).filter(
            func.lower(Referral.source_type)=='ad').group_by(Referral.source_id).order_by(func.max(Referral.id).desc()).limit(500).all()
        return {'rules':[serialize(db,r) for r in query.order_by(Rule.updated_at.desc(),Rule.id.desc()).offset(offset).limit(50)],
            'total':query.count(),'offset':offset,'staff':[{'id':u.id,'username':u.username,'eligible':service.eligible(u)} for u in staff],
            'ads':[{'id':aid,'headline':(public_referral({'headline':headline}) or {}).get('headline') or aid,'referrals':count}
                   for aid,headline,count in ads if service.ad_id(aid)],
            'secure_webhooks':bool(settings.META_APP_SECRET),'csrf_token':csrf_token(request)}

    @router.put('/crm/ad-assignments/api/rules/{aid}')
    async def save(aid:str,request:Request,db=Depends(get_db),user=Depends(admin)):
        csrf(request)
        try:body=await request.json()
        except ValueError:raise HTTPException(422,'Provide rule JSON') from None
        try:
            rule=service.save(db,user,aid,body);db.commit();db.refresh(rule)
        except IntegrityError:
            db.rollback();raise HTTPException(409,'Rule changed. Reload before saving') from None
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'The rule was not saved. Retry shortly') from None
        return {'rule':serialize(db,rule)}

    @router.get('/crm/ad-assignments/api/rules/{aid}/history')
    def history(aid:str,db=Depends(get_db),user=Depends(admin)):
        rule=db.query(Rule).filter_by(ad_id=aid).one_or_none()
        if rule is None:raise HTTPException(404,'Rule not found')
        users={u.id:u.username for u in db.query(User)}
        return {'history':[{'version':r.version,'actor':users.get(r.actor_id,'Administrator'),'at':r.created_at.isoformat()+'Z','changes':r.changes}
            for r in db.query(RuleAudit).filter_by(rule_id=rule.id).order_by(RuleAudit.version.desc()).limit(50)],
            'assignments':[{'conversation_id':a.conversation_id,'source':a.source,'staff':users.get(a.new_owner_id,'Staff'),
                'rule_version':a.rule_version,'at':a.created_at.isoformat()+'Z'} for a in db.query(Assignment).filter_by(rule_id=rule.id).order_by(Assignment.id.desc()).limit(50)]}

    @router.get('/crm/ad-assignments/api/rules/{aid}/preview')
    def preview(aid:str,db=Depends(get_db),user=Depends(admin)):
        rule=db.query(Rule).filter_by(ad_id=aid).one_or_none()
        if rule is None:raise HTTPException(404,'Rule not found')
        return {**service.preview(db,rule),'version':rule.version,'staff_name':serialize(db,rule)['staff_name']}

    @router.post('/crm/ad-assignments/api/rules/{aid}/apply-existing')
    async def apply(aid:str,request:Request,db=Depends(get_db),user=Depends(admin)):
        csrf(request)
        try:body=await request.json()
        except ValueError:raise HTTPException(422,'Provide preview JSON') from None
        try:result=service.apply_existing(db,user,aid,body);db.commit()
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'No assignments committed. Preview again and retry') from None
        return result
    return router
