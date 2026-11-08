"""Private frontend configuration and per-conversation question controls."""
import json
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError


class QuestionRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def safe(request):
            try:
                response=await original(request)
            except HTTPException as exc:
                response=JSONResponse({"detail":exc.detail},status_code=exc.status_code)
            except SQLAlchemyError:
                response=JSONResponse({"detail":"Customer questions are unavailable. Refresh before trying again; do not repeat an uncertain send."},status_code=503)
            response.headers["Cache-Control"]="private, no-store"
            return response
        return safe
from models import CustomerQuestionConfig as Config,CustomerQuestionEvent as Event
import customer_questions as questions
import conversation_access


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(route_class=QuestionRoute)
    def admin(user):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can configure Customer Question Automation')
    async def body(request):
        csrf(request);raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>32768:raise HTTPException(413,'Question settings are too large')
            raw.extend(chunk)
        try:
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError()
            return value
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid question settings') from None

    @router.get('/crm/quick-messages/customer-questions')
    def page(request:Request,user=Depends(require_user)):
        admin(user)
        return templates.TemplateResponse('customer_questions.html',{'request':request,'user':user,
            'page_title':'Customer Question Automation','csrf_token':csrf_token(request)})

    @router.get('/crm/quick-messages/customer-questions/api')
    def settings(request:Request,db=Depends(get_db),user=Depends(require_user)):
        admin(user);c=questions.config(db)
        return {'enabled':c.enabled,'version':c.version,'definition':c.definition,'csrf_token':csrf_token(request)}

    @router.post('/crm/quick-messages/customer-questions/api')
    async def save(request:Request,db=Depends(get_db),user=Depends(require_user)):
        admin(user);p=await body(request)
        if set(p)!={'enabled','version','definition'} or type(p.get('enabled')) is not bool or type(p.get('version')) is not int:raise HTTPException(422,'Invalid question settings')
        from settings import settings
        if p['enabled'] and not settings.META_APP_SECRET:raise HTTPException(409,'Webhook signature verification must be configured before enabling customer questions')
        definition=questions.validate(p['definition']);questions.write_transaction(db);c=questions.config(db,True)
        if c.version!=p['version']:raise HTTPException(409,'Settings changed. Refresh before saving.')
        # Conditional UPDATE also serializes concurrent SQLite editors.
        from sqlalchemy import update
        if db.execute(update(Config).where(Config.id==1,Config.version==p['version']).values(enabled=p['enabled'],definition=definition,version=Config.version+1)).rowcount!=1:raise HTTPException(409,'Settings changed. Refresh before saving.')
        questions.audit(db,None,'CONFIGURE',user.username,enabled=p['enabled'],definition=definition,version=p['version']+1)
        db.commit();return {'ok':True,'version':p['version']+1}

    @router.get('/crm/whatsapp/api/conversations/{cid}/customer-questions')
    def current(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        c=conversation_access.require_visible(db,user,cid);cfg=questions.config(db);run=questions.latest(db,cid)
        events=[]
        if run:
            events=[{'action':e.action,'actor':e.actor,'at':e.created_at.isoformat()+'Z'} for e in db.query(Event).filter_by(run_id=run.id).order_by(Event.id.desc()).limit(30)]
        return {'enabled':cfg.enabled,'title':cfg.definition['title'],'run':questions.snapshot(run),'history':events,
            'can_manage':conversation_access.permissions(db,user,c)['can_manage'],'csrf_token':csrf_token(request)}

    @router.post('/crm/whatsapp/api/conversations/{cid}/customer-questions')
    async def act(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        c=conversation_access.require_visible(db,user,cid)
        if not conversation_access.permissions(db,user,c)['can_manage']:raise HTTPException(403,'Only the conversation owner or administrator can manage questions')
        p=await body(request)
        if set(p)-{'action','version'}:raise HTTPException(422,'Invalid question action')
        # SQLite needs a real write transaction before read/compare/write operations.
        conn=db.connection()
        if conn.dialect.name=='sqlite' and not conn.connection.driver_connection.in_transaction:conn.exec_driver_sql('BEGIN IMMEDIATE')
        run=questions.start(db,c,user) if p.get('action')=='start' else questions.change(db,c,user,p.get('action'),p.get('version'))
        db.commit();return {'run':questions.snapshot(run)}
    return router
