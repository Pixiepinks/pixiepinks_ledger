"""Private analytics routes. Separate explicit financial-report access grant."""
import csv
import io
from fastapi import APIRouter, Depends, Request, Query, HTTPException, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from crm_dashboard_routes import DashboardRoute
from staff_access import managed_grant
from meta_ads_client import AdsError, account_id, configuration_status
from meta_ads_models import MetaAdsSync
import meta_ads_diagnostics as diagnostics
import meta_ads_report as reporting
import meta_ads_worker as worker


def can_view(user):
    return bool(user and getattr(user,'is_active',True) and (user.role=='ADMIN' or
        managed_grant(user,'ad_analytics')))


def csv_cell(value):
    text='' if value is None else str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text


def build_router(get_db, require_user, csrf_check, csrf_token, templates):
    router=APIRouter(route_class=DashboardRoute)
    def viewer(user=Depends(require_user)):
        if not can_view(user):raise HTTPException(403,'Advertising analytics access required')
        return user

    @router.get('/crm/ad-analytics')
    @router.get('/crm/ad-analytics/ads/{ad_id}')
    def page(request:Request, ad_id:str='', user=Depends(viewer)):
        return templates.TemplateResponse('meta_ads.html',{'request':request,'user':user,'page_title':'Ad Analytics',
            'csrf_token':csrf_token(request),'ads_admin':user.role=='ADMIN','detail_ad':ad_id},headers={'Cache-Control':'private, no-store'})

    @router.get('/crm/ad-analytics/api')
    def data(request:Request,date_from:str='',date_to:str='',campaign:str=Query('',max_length=64),adset:str=Query('',max_length=64),
             ad:str=Query('',max_length=64),interest:str=Query('',max_length=100),status:str=Query('',max_length=100),
             level:str='ad',sort:str='spend',descending:bool=True,offset:int=Query(0,ge=0,le=100000),
             db=Depends(get_db),user=Depends(viewer)):
        result=reporting.report(db,date_from,date_to,campaign,adset,ad,interest,status,level)
        if sort not in {'spend','leads','confirmed_orders','revenue','roas','name'}:raise HTTPException(422,'Invalid sort')
        result['rows'].sort(key=lambda r:(r[sort] is not None,r[sort] or 0),reverse=descending)
        result['total']=len(result['rows']);result['offset']=offset
        result['rows']=result['rows'][offset:offset+50]
        for row in result['rows']:
            for field in ('orders','inquiries'):
                row[field+'_total']=len(row[field]);row[field]=row[field][:100]
        result['unattributed_total']=len(result['unattributed']);result['unattributed']=result['unattributed'][:100]
        result['csrf_token']=csrf_token(request) if user.role=='ADMIN' else None
        if user.role=='ADMIN':
            config = configuration_status()
            result['integration']['configuration_errors'] = config['errors']
            result['integration']['error_hint'] = diagnostics.hint(result['integration']['error']) if result['integration']['error'] else None
            row = db.get(MetaAdsSync, account_id()) if account_id() else None
            result['integration']['diagnostics'] = diagnostics.safe(row.diagnostics) if row else {}
            result['integration']['requested_at'] = reporting.iso(row.requested_at) if row else None
        return result

    @router.get('/crm/ad-analytics/export')
    def export(date_from:str='',date_to:str='',campaign:str='',adset:str='',ad:str='',interest:str='',status:str='',level:str='ad',
               db=Depends(get_db),user=Depends(viewer)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can export advertising financial reports')
        result=reporting.report(db,date_from,date_to,campaign,adset,ad,interest,status,level)
        fields=['id','name','currency','spend','impressions','reach','meta_messaging','leads','confirmed_orders','verified_orders','revenue','cpl','cpo','conversion','roas']
        stream=io.StringIO();writer=csv.writer(stream)
        writer.writerow(['Reporting from',result['period']['from'],'through',result['period']['to'],'Timezone',result['period']['timezone']])
        writer.writerow(['Attribution',result['attribution']]);writer.writerow(['Last successful sync',result['integration']['last_success'],'Stale',result['integration']['stale']])
        writer.writerow(fields)
        for row in result['rows']:writer.writerow([csv_cell(row.get(f)) for f in fields])
        return Response(stream.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="pixiepinks-ad-analytics.csv"'})

    @router.get('/crm/ad-analytics/diagnostics')
    def diagnose(db=Depends(get_db),user=Depends(viewer)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can inspect sync diagnostics')
        import os
        import re
        config = configuration_status()
        row = db.get(MetaAdsSync, config['account_id']) if config['account_id'] else None
        evidence = diagnostics.safe(row.diagnostics) if row else {}
        revision = os.getenv('RAILWAY_GIT_COMMIT_SHA','')
        deployment = os.getenv('RAILWAY_DEPLOYMENT_ID','')
        return {'configuration':config,'configuration_hints':[diagnostics.hint(code) for code in config['errors']],
            'revision':revision if re.fullmatch(r'[a-f0-9]{40}',revision) else None,
            'deployment_id':deployment if re.fullmatch(r'[A-Za-z0-9-]{1,80}',deployment) else None,
            'sync':{'status':row.status if row else 'NEVER', 'error':row.error_code if row else None,
                'error_hint':diagnostics.hint(row.error_code) if row and row.error_code else None,
                'started_at':reporting.iso(row.started_at) if row else None,
                'requested_at':reporting.iso(row.requested_at) if row else None,
                'last_success':reporting.iso(row.last_success_at) if row else None,
                'active_lease':worker.active(row) if row else False,'evidence':evidence,
                'api_attempted':evidence['api_requests']>0 if 'api_requests' in evidence else None},
            'note':'Reads this running service configuration and stored evidence only. No Meta request is performed. In-flight request progress is logged; stored counts describe completed or rejected attempts.'}

    @router.post('/crm/ad-analytics/sync')
    def sync(request:Request,tasks:BackgroundTasks,db=Depends(get_db),user=Depends(viewer)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can synchronize advertising analytics')
        csrf_check(request)
        try:status=worker.request_sync(db)
        except AdsError as exc:
            code = diagnostics.error_code(exc.code)
            return JSONResponse({'detail':code,'hint':diagnostics.hint(code),'stage':'PREFLIGHT','api_requests':0},status_code=409)
        if status=='QUEUED':
            # Reuse the configured database engine, never request-scoped sessions in background work.
            from sqlalchemy.orm import sessionmaker
            tasks.add_task(worker.tick,sessionmaker(bind=db.get_bind()))
        return {'status':status}
    return router
