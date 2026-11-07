"""Private reporting endpoints; aggregation and drill-down share the same scope."""
from datetime import datetime
from fastapi import APIRouter,Depends,Query,Request,HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select,func
from sqlalchemy.exc import SQLAlchemyError
from models import User,CRMAnalyticsPhone as Phone,WhatsAppConversation as C
from staff_access import allowed
import crm_dashboard_service as service


class DashboardRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def safe(request):
            try:
                response=await original(request)
                response.headers['Cache-Control']='private, no-store'
                return response
            except HTTPException as exc:
                return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers={**(exc.headers or {}),'Cache-Control':'private, no-store'})
            except SQLAlchemyError:
                return JSONResponse({'detail':'Dashboard data is unavailable. Your previous display has been retained; retry shortly.'},status_code=503,headers={'Cache-Control':'private, no-store'})
        return safe


def build_router(get_db,require_user,templates,statuses,labels):
    router=APIRouter(route_class=DashboardRoute)

    @router.get('/crm/dashboard/api')
    def data(period:str='today',date_from:str='',date_to:str='',staff_id:int|None=Query(None,ge=1),country:str='',source:str='all',
             queue:str='needs_reply',staff_offset:int=Query(0,ge=0,le=100000),detail:str='',offset:int=Query(0,ge=0,le=100000),category:str='',actor_id:int|None=Query(None,ge=1),lead_status:str=Query('',max_length=100),
             db=Depends(get_db),user=Depends(require_user)):
        report=service.Report(db,user,service.window(period,date_from,date_to),staff_id,country,source)
        events=report.events() if report.whatsapp else None
        if detail:
            if detail=='queue':
                result=report.operational(queue,offset=offset,limit=50)
                if result['counts'][queue] is None:raise HTTPException(403,'This operational section is unavailable.')
                return {'detail':{'rows':result['items'],'total':result['counts'][queue],'offset':offset},'generated_at':service.stamp(datetime.utcnow())}
            return {'detail':{**report.details(detail,events,offset,category,actor_id,lead_status),'offset':offset},'generated_at':service.stamp(datetime.utcnow())}
        countries=[];staff=[]
        if report.whatsapp:
            rows=db.execute(select(Phone.country,Phone.country_name).where(report.visibility(Phone.raw_phone)).group_by(Phone.country,Phone.country_name).order_by(Phone.country_name)).all()
            countries=[{'value':r[0],'label':r[1]} for r in rows]
            staff_query=db.query(User).filter(User.role.in_(['ADMIN','STAFF','SALES_MANAGER','SALES_AGENT']))
            if user.role!='ADMIN':staff_query=staff_query.filter(User.id==user.id)
            staff=[{'id':u.id,'name':u.username,'active':u.is_active} for u in staff_query.order_by(User.username).limit(500)]
        return {'generated_at':service.stamp(datetime.utcnow()),'window':{'start':service.stamp(report.period.start),'end':service.stamp(report.period.end),'label':report.period.label},
            'filters':{'staff':staff,'countries':countries,'sources':service.SOURCES},'metrics':report.metrics(events) if events is not None else None,
            'categories':service.CATEGORIES,'staff_activity':report.staff(events,staff_offset) if events is not None else None,
            'attention':report.operational(queue),'pipeline':report.pipeline(statuses),'lead_labels':labels,
            'access':{'whatsapp':report.whatsapp,'leads':report.leads,'team':user.role=='ADMIN' and report.whatsapp}}
    return router
