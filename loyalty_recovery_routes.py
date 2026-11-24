"""Permissioned local historical recovery, separated from commercial endpoints."""
from fastapi import APIRouter,Depends,Request,Query
from loyalty_models import LoyaltyRecoveryPlan,LoyaltyHistoryScan,LoyaltyMigration
import loyalty_service as svc
import loyalty_recovery as recovery
import loyalty_shopify_history as history

def build_router(get_db,require_user,payload,require,json_response,csrf_token,templates):
    router=APIRouter()
    def access(user,execute=False):
        require(user,'migration.preview');require(user,'members.manage')
        if execute:require(user,'migration.execute')
    @router.get('/crm/loyalty/api/recovery')
    def summary(db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.preview');return json_response(recovery.dashboard(db))
    @router.get('/crm/loyalty/api/recovery/orders')
    def orders(kind:str='LOCAL',offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        access(user);return json_response({'rows':recovery.order_rows(db,kind,offset),'offset':offset})
    @router.get('/crm/loyalty/api/recovery/orders/{kind}/{source_id}')
    def detail(kind:str,source_id:int,db=Depends(get_db),user=Depends(require_user)):
        access(user);s=recovery.source_snapshot(db,kind,source_id);return json_response({'source':s,'proposal':recovery.proposal(db,s)})
    @router.post('/crm/loyalty/api/recovery/plans')
    async def preview(request:Request,db=Depends(get_db),user=Depends(require_user)):
        access(user);v=await payload(request);job=recovery.preview(db,user,v.get('items'),request.headers.get('Idempotency-Key'));db.commit();return plan_data(job)
    def plan_data(job):
        return json_response({'id':job.id,'status':job.status,'digest':job.digest,'items':job.items,'results':job.results,'memberships_created':0,'commercial_writes':0})
    @router.get('/crm/loyalty/api/recovery/plans/{plan_id}')
    def plan_get(plan_id:str,db=Depends(get_db),user=Depends(require_user)):
        access(user);job=db.get(LoyaltyRecoveryPlan,plan_id)
        if not job:svc.fail('Recovery plan not found',404)
        return plan_data(job)
    @router.post('/crm/loyalty/api/recovery/plans/{plan_id}/{action}')
    async def plan_action(plan_id:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        access(user,True);v=await payload(request);recovery.require_off(db)
        job=db.query(LoyaltyRecoveryPlan).filter_by(id=plan_id).populate_existing().with_for_update().one_or_none()
        if not job:svc.fail('Recovery plan not found',404)
        if action=='approve':recovery.approve(db,user,job,v.get('digest'),v.get('reason'))
        elif action=='execute':
            from sqlalchemy.exc import IntegrityError
            try:recovery.execute(db,user,job,v.get('digest'))
            except IntegrityError:
                db.rollback();svc.fail('Concurrent customer or association change; inspect existing records and create a fresh preview',409)
        else:svc.fail('Unknown recovery action')
        db.commit();return plan_data(job)
    @router.post('/crm/loyalty/api/recovery/scans')
    async def scan_start(request:Request,db=Depends(get_db),user=Depends(require_user)):
        access(user,True);await payload(request);job=history.start(db,user);db.commit();return json_response(recovery.scan_summary(job))
    @router.get('/crm/loyalty/api/recovery/scans/{scan_id}')
    def scan_get(scan_id:str,db=Depends(get_db),user=Depends(require_user)):
        access(user);job=db.get(LoyaltyHistoryScan,scan_id)
        if not job or job.shop!=(history.settings.SHOPIFY_SHOP or '').lower():svc.fail('Scan not found in configured shop',404)
        return json_response(recovery.scan_summary(job))
    @router.post('/crm/loyalty/api/recovery/scans/{scan_id}/next')
    async def scan_next(scan_id:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        access(user,True);v=await payload(request);recovery.require_off(db)
        job=db.query(LoyaltyHistoryScan).filter_by(id=scan_id).populate_existing().with_for_update().one_or_none()
        if not job:svc.fail('Scan not found',404)
        history.page(db,user,job,v.get('expected_pages'));db.commit();return json_response(recovery.scan_summary(job))
    @router.get('/crm/loyalty/migration/runs/{job_id}')
    def migration_detail(job_id:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.preview');job=db.get(LoyaltyMigration,job_id)
        if not job:svc.fail('Migration not found',404)
        import staff_access
        return templates.TemplateResponse('loyalty_migration_detail.html',{'request':request,'user':user,'page_title':'Historical migration results','job':job,'csrf':csrf_token(request),'can_execute':staff_access.allowed(user,'loyalty.migration.execute')},headers={'Cache-Control':'private, no-store'})
    return router
