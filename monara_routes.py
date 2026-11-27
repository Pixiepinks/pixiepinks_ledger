"""Explicit authenticated Monara controls; all audio is private, never public URLs."""
from fastapi import APIRouter,Request,Depends,BackgroundTasks,HTTPException,Query
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError,OperationalError
from sqlalchemy.orm import defer
from learning_routes import read_body
from monara_models import MonaraJob as J,MonaraSend as S,MonaraPolicy as P
from settings import settings
import monara_service as svc
import conversation_access as access
HEADERS={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}

def build_router(get_db,require_user,csrf,token,templates,sessions,upload,deliver):
    r=APIRouter()
    @r.get('/crm/monara')
    def page(request:Request,user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user)
        return templates.TemplateResponse('monara.html',{'request':request,'user':user,'page_title':'Monara AI Voice','csrf_token':token(request),'admin':user.role=='ADMIN','embedded':request.query_params.get('embedded')=='1','styles':svc.LABELS},headers=HEADERS)
    @r.get('/crm/monara/context/{cid}')
    def context(cid:int,request:Request,user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user);c=access.require_visible(db,user,cid);p=svc.policy(db)
        return {'id':c.id,'name':c.customer_name,'assignment_version':c.assignment_version,'staff_id':user.id,'csrf_token':token(request),'generation_enabled':settings.MONARA_TTS_ENABLED and p['generation'],'send_enabled':settings.MONARA_TTS_SEND_ENABLED and p['sending'],'max_characters':min(1000,settings.MONARA_TTS_MAX_CHARACTERS),'voice':'PixiePinks – Monara 2','model':settings.MONARA_TTS_MODEL,'style':p['default_style'],'styles':svc.LABELS,'style_notice':'Style instructions use separate SpeechMetadata; authored text is unchanged.','disclosures':svc.DISCLOSURES,'delivery_behavior':'Standard audio attachment; no native voice-note claim'}
    @r.post('/crm/monara/conversations/{cid}/generate')
    async def generate(cid:int,request:Request,tasks:BackgroundTasks,user=Depends(require_user),db=Depends(get_db)):
        csrf(request);values=await read_body(request)
        try:
            job=svc.create(db,user,cid,values,request.headers.get('Idempotency-Key',''));db.commit();tasks.add_task(svc.process,sessions(),job.id);return svc.public(job)
        except (IntegrityError,OperationalError):db.rollback();raise HTTPException(409,'Concurrent request; check the original request status without generating again') from None
    @r.get('/crm/monara/requests/{key}')
    def recover(key:str,cid:int,user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user);access.require_visible(db,user,cid)
        row=db.query(J).filter_by(request_key=key,staff_id=user.id,conversation_id=cid).one_or_none()
        if not row:raise HTTPException(404,'Request not recorded')
        return svc.public(row)
    @r.get('/crm/monara/jobs/{jid}')
    def job(jid:str,cid:int,user=Depends(require_user),db=Depends(get_db)):
        access.require_visible(db,user,cid);row=svc.scoped(db,user,jid,cid);return {**svc.public(row),'sha256':row.sha256,'usage':row.usage,'estimated_cost':row.estimated_cost,'reserved_cost':row.reserved_cost,'send':svc.send_status(db,v) if (v:=db.query(S).filter_by(job_id=jid,conversation_id=cid).first()) else {'status':'NOT_SENT'}}
    @r.get('/crm/monara/jobs/{jid}/audio/{kind}')
    def audio(jid:str,kind:str,request:Request,cid:int|None=None,user=Depends(require_user),db=Depends(get_db)):
        raw=svc.audio(db,user,jid,cid,kind)
        from saved_voice_routes import audio_response
        return audio_response(raw,'audio/mpeg' if kind=='mp3' else 'audio/ogg',request,filename=f'PixiePinks-Monara-AI.{kind}')
    @r.get('/crm/monara/library')
    def library(q:str=Query('',max_length=100),inactive:bool=False,offset:int=Query(0,ge=0,le=100000),user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user)
        query=db.query(J).options(defer(J.ogg),defer(J.mp3)).filter_by(shared=True,status='READY').filter(J.expires_at>svc.datetime.utcnow())
        if inactive:svc.authorize(db,user,admin=True)
        else:query=query.filter(J.active.is_(True))
        if q:query=query.filter(J.title.icontains(q,autoescape=True)|J.category.icontains(q,autoescape=True)|J.transcript.icontains(q,autoescape=True))
        return {'rows':[svc.public(v) for v in query.order_by(J.created_at.desc()).offset(offset).limit(25)],'total':query.count(),'limit':25}
    @r.post('/crm/monara/jobs/{jid}/library')
    async def save(jid:str,cid:int,request:Request,user=Depends(require_user),db=Depends(get_db)):
        csrf(request);values=await read_body(request);result=svc.library_save(db,user,jid,cid,values);db.commit();return result
    @r.post('/crm/monara/jobs/{jid}/send/{cid}')
    async def send(jid:str,cid:int,request:Request,user=Depends(require_user),db=Depends(get_db)):
        csrf(request);values=await read_body(request)
        from starlette.concurrency import run_in_threadpool
        from saved_voice_routes import SEND_SLOTS
        if not SEND_SLOTS.acquire(blocking=False):raise HTTPException(429,'Voice sending is busy; check the original request before retrying')
        try:return await run_in_threadpool(svc.send,db,user,cid,jid,values,request.headers.get('Idempotency-Key',''),upload,deliver)
        except (IntegrityError,OperationalError):db.rollback();raise HTTPException(409,'Original send intent already claimed; check status, never resend uncertain output') from None
        finally:SEND_SLOTS.release()
    @r.get('/crm/monara/admin')
    def admin(user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user,admin=True);row=db.get(P,1)
        return {'version':row.version,'policy':svc.policy(db),'usage':svc.summary(db,user),'runtime':{'generation_gate':settings.MONARA_TTS_ENABLED,'send_gate':settings.MONARA_TTS_SEND_ENABLED,'api_key_configured':bool(settings.GEMINI_API_KEY),'model':settings.MONARA_TTS_MODEL,'voice':settings.MONARA_TTS_VOICE_ID,'api_version':settings.MONARA_TTS_API_VERSION}}
    @r.post('/crm/monara/admin/policy')
    async def configure(request:Request,user=Depends(require_user),db=Depends(get_db)):
        csrf(request);result=svc.configure(db,user,await read_body(request));db.commit();return result
    @r.post('/crm/monara/admin/maintenance')
    def maintain(request:Request,user=Depends(require_user),db=Depends(get_db)):
        csrf(request);result=svc.maintain(db,user);db.commit();return result
    @r.post('/crm/monara/admin/archive/{jid}')
    async def archive(jid:str,request:Request,user=Depends(require_user),db=Depends(get_db)):
        svc.authorize(db,user,admin=True);csrf(request);p=await read_body(request)
        row=db.query(J).filter_by(id=jid,shared=True).with_for_update().one_or_none()
        if not row:raise HTTPException(404,'Library entry not found')
        if not isinstance(p,dict) or set(p)!={'active'} or type(p['active']) is not bool:raise HTTPException(422,'Explicit active state required')
        if p['active'] and (row.status!='READY' or row.expires_at<=svc.datetime.utcnow()):raise HTTPException(409,'Expired audio cannot be reactivated')
        row.active=p['active'];svc.event(db,user,'LIBRARY_ACTIVE_CHANGED',jid,active=row.active);db.commit();return {'active':row.active}
    return r
