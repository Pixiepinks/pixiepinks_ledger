"""Private frontend configuration and per-conversation question controls."""
import json
from fastapi import APIRouter,Depends,Request,HTTPException,Query
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
from models import CustomerQuestionFlow as Flow,CustomerQuestionConfig as Config,CustomerQuestionEvent as Event
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
    def settings(request:Request,flow_id:int|None=Query(None,ge=1,le=2147483647),db=Depends(get_db),user=Depends(require_user)):
        admin(user);c=questions.config(db)
        flow=db.get(Flow,flow_id if flow_id is not None else 1)
        if flow is None:raise HTTPException(404,'Flow not found')
        if flow_id is None:return {'enabled':c.enabled,'version':c.version,'definition':flow.definition,'csrf_token':csrf_token(request)}
        return {'enabled':c.enabled,'version':c.version,'definition':flow.definition,'flow':questions.flow_snapshot(flow),
            'flows':questions.library(db,True),'csrf_token':csrf_token(request)}

    @router.post('/crm/quick-messages/customer-questions/api')
    async def save(request:Request,db=Depends(get_db),user=Depends(require_user)):
        admin(user);p=await body(request)
        if set(p) not in ({'enabled','version','definition'},{'enabled','version','definition','flow_id'}) or type(p.get('enabled')) is not bool or type(p.get('version')) is not int:raise HTTPException(422,'Invalid question settings')
        from settings import settings
        if p['enabled'] and not settings.META_APP_SECRET:raise HTTPException(409,'Webhook signature verification must be configured before enabling customer questions')
        definition=questions.validate(p['definition']);questions.write_transaction(db);c=questions.config(db,True)
        if c.version!=p['version']:raise HTTPException(409,'Settings changed. Refresh before saving.')
        flow_id=p.get('flow_id',1)
        if type(flow_id) is not int or not 1<=flow_id<=2147483647:raise HTTPException(422,'Choose a saved flow')
        flow=db.get(Flow,flow_id)
        if flow is None or flow.archived:raise HTTPException(409,'Archived or missing flows cannot be edited')
        flow.definition=definition;flow.version+=1
        from datetime import datetime
        flow.updated_at=datetime.utcnow()
        # Conditional UPDATE also serializes concurrent SQLite editors.
        from sqlalchemy import update
        if db.execute(update(Config).where(Config.id==1,Config.version==p['version']).values(enabled=p['enabled'],definition=definition if flow_id==1 else c.definition,version=Config.version+1)).rowcount!=1:raise HTTPException(409,'Settings changed. Refresh before saving.')
        questions.audit(db,None,'CONFIGURE',user.username,enabled=p['enabled'],definition=definition,version=p['version']+1,flow_id=flow_id)
        db.commit();return {'ok':True,'version':p['version']+1}

    @router.post('/crm/quick-messages/customer-questions/flows')
    async def library_action(request:Request,db=Depends(get_db),user=Depends(require_user)):
        admin(user);p=await body(request)
        if set(p)-{'action','version','flow_id','title','enabled'} or type(p.get('version')) is not int:raise HTTPException(422,'Invalid flow action')
        questions.write_transaction(db);control=questions.config(db,True)
        if p['version']!=control.version:raise HTTPException(409,'Flow library changed. Refresh and review.')
        action=p.get('action');source=None
        if not isinstance(action,str):raise HTTPException(422,'Choose a flow action')
        if action in {'duplicate','archive','restore'}:
            if type(p.get('flow_id')) is not int or not 1<=p['flow_id']<=2147483647:raise HTTPException(422,'Choose a flow')
            source=db.get(Flow,p['flow_id'])
            if source is None:raise HTTPException(404,'Flow not found')
        if action=='toggle':
            from settings import settings
            if type(p.get('enabled')) is not bool:raise HTTPException(422,'Choose ON or OFF')
            if p['enabled'] and not settings.META_APP_SECRET:raise HTTPException(409,'Webhook signature verification must be configured before enabling customer questions')
            control.enabled=p['enabled'];control.version+=1
            questions.audit(db,None,'AUTOMATION_SWITCH',user.username,enabled=control.enabled)
            db.commit();return {'version':control.version}
        if action in {'create','duplicate'}:
            if db.query(Flow).count()>=100:raise HTTPException(409,'Flow library limit of 100 reached')
            title=p.get('title')
            if not isinstance(title,str) or not 1<=len(title.strip())<=100:raise HTTPException(422,'Flow title must be 1–100 characters')
            definition=json.loads(json.dumps(source.definition if source else questions.DEFAULT));definition['title']=title.strip()
            flow=Flow(definition=definition,created_by=user.username,archived=False,version=0);db.add(flow);db.flush()
        elif action in {'archive','restore'}:
            flow=source;flow.archived=action=='archive';flow.version+=1
        else:raise HTTPException(422,'Choose create, duplicate, archive or restore')
        from datetime import datetime
        flow.updated_at=datetime.utcnow();control.version+=1
        questions.audit(db,None,'FLOW_'+action.upper(),user.username,flow_id=flow.id,definition=flow.definition)
        db.commit();return {'flow_id':flow.id,'version':control.version}

    @router.get('/crm/whatsapp/api/conversations/{cid}/customer-questions')
    def current(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        c=conversation_access.require_visible(db,user,cid);cfg=questions.config(db);run=questions.latest(db,cid)
        events=[]
        if run:
            events=[{'action':e.action,'actor':e.actor,'at':e.created_at.isoformat()+'Z'} for e in db.query(Event).filter_by(run_id=run.id).order_by(Event.id.desc()).limit(30)]
        return {'enabled':cfg.enabled,'title':cfg.definition['title'],'flows':questions.library(db),'run':questions.snapshot(run),'history':events,
            'can_manage':conversation_access.permissions(db,user,c)['can_manage'],'csrf_token':csrf_token(request)}

    @router.post('/crm/whatsapp/api/conversations/{cid}/customer-questions')
    async def act(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        c=conversation_access.require_visible(db,user,cid)
        if not conversation_access.permissions(db,user,c)['can_manage']:raise HTTPException(403,'Only the conversation owner or administrator can manage questions')
        p=await body(request)
        if set(p)-{'action','version','flow_id','flow_version'}:raise HTTPException(422,'Invalid question action')
        # SQLite needs a real write transaction before read/compare/write operations.
        conn=db.connection()
        if conn.dialect.name=='sqlite' and not conn.connection.driver_connection.in_transaction:conn.exec_driver_sql('BEGIN IMMEDIATE')
        if 'flow_id' in p and (type(p.get('flow_version')) is not int):raise HTTPException(422,'Preview the selected flow before starting')
        run=questions.start(db,c,user,p.get('flow_id',1),p.get('flow_version')) if p.get('action')=='start' else questions.change(db,c,user,p.get('action'),p.get('version'))
        db.commit();return {'run':questions.snapshot(run)}
    return router
