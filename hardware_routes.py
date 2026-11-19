"""Session/CSRF protected staff controls; separate TLS bearer-only device API."""
import hashlib,hmac,json,re
from datetime import timedelta
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import JSONResponse,Response
from hardware_models import PrintDevice,HardwareJob,HardwareEvent
from pos_models import POSSession
from settings import settings
import hardware_service as s


def build_router(get_db,require_user,csrf):
    router=APIRouter()
    def out(value):return JSONResponse(value,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    async def body(request,keys,staff=True):
        if staff:csrf(request)
        raw=await request.body()
        if len(raw)>32000:raise HTTPException(413,'Hardware request too large')
        try:value=json.loads(raw)
        except (ValueError,TypeError):raise HTTPException(422,'JSON object required') from None
        if not isinstance(value,dict) or set(value)!=set(keys):raise HTTPException(422,'Unexpected hardware fields')
        if 'key' in value and (not isinstance(value['key'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',value['key'])):raise HTTPException(422,'Stable request key required')
        return value
    def scope(db,user,d):
        if user.role=='ADMIN' or 'pos.admin' in (user.section_access or []):return
        if not db.query(POSSession).filter_by(register_id=d.register_id,cashier_id=user.id,status='OPEN',shop=d.shop).first():raise HTTPException(403,'This device belongs to another register session')
    def native(request,db):
        if request.url.scheme!='https' or request.headers.get('origin'):raise HTTPException(403,'Native agent over TLS required')
        identity=hmac.new(settings.SECRET_KEY.encode(),('agent:'+str(request.client.host if request.client else '')).encode(),hashlib.sha256).hexdigest()
        s.throttle(db,identity,240)
        d=s.authenticate(db,request.headers.get('authorization',''));s.pos.locked(db);return d

    @router.get('/crm/pos/api/hardware')
    def overview(db=Depends(get_db),user=Depends(require_user)):
        s.require(user);rows=db.query(PrintDevice).filter_by(shop=s.retail.shop()).order_by(PrintDevice.created_at.desc()).limit(100).all()
        if user.role!='ADMIN' and 'pos.admin' not in (user.section_access or []):
            registers={r.register_id for r in db.query(POSSession).filter_by(shop=s.retail.shop(),cashier_id=user.id,status='OPEN')};rows=[d for d in rows if d.register_id in registers]
        ids=[d.id for d in rows]
        jobs=db.query(HardwareJob).filter(HardwareJob.device_id.in_(ids)).order_by(HardwareJob.created_at.desc()).limit(50).all()
        return out({'devices':[s.public_device(d) for d in rows],'jobs':[s.public_job(j) for j in jobs],'gates':{'receipts':settings.POS_LOCAL_PRINT_ENABLED,'drawer':settings.POS_CASH_DRAWER_AUTOMATION_ENABLED,'labels':settings.RETAIL_HARDWARE_LABEL_PRINT_ENABLED}})

    @router.post('/crm/pos/api/hardware/devices')
    async def enroll(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['register_id','name']);d,code=s.enroll(db,user,**p);return out({'device':s.public_device(d),'pairing_code':code,'expires_in':300})

    @router.post('/crm/pos/api/hardware/devices/{did}/config')
    async def configure(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['value']);return out(s.public_device(s.configure(db,user,did,p['value'])))

    @router.post('/crm/pos/api/hardware/devices/{did}/manage')
    async def manage(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['action']);code=s.manage(db,user,did,p['action']);return out({'ok':True,'pairing_code':code})

    @router.post('/crm/pos/api/hardware/devices/{did}/media')
    async def media(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['mode','confirm']);s.require(user,'sell');d=s.device(db,did);scope(db,user,d);s.request_mode(db,user,did,**p);return out({'requested':True})

    @router.post('/crm/pos/api/hardware/devices/{did}/receipt')
    async def receipt(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['rid','key','profile']);s.require(user);scope(db,user,s.device(db,did));j=s.receipt_job(db,user,did,**p);db.commit();return out(s.public_job(j))

    @router.post('/crm/pos/api/hardware/devices/{did}/drawer')
    async def drawer(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['key','reason']);j=s.manual_drawer(db,user,did,**p);db.commit();return out(s.public_job(j))

    @router.post('/crm/pos/api/hardware/devices/{did}/test')
    async def test(did:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['key']);j=s.printer_test(db,user,did,p['key']);db.commit();return out(s.public_job(j))

    @router.post('/crm/pos/api/hardware/jobs/{jid}/review')
    async def review(jid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['confirm','reason','agent_stopped']);s.require(user)
        j=db.query(HardwareJob).filter_by(shop=s.retail.shop(),id=jid).one_or_none()
        if not j:raise HTTPException(404,'Job not found')
        scope(db,user,s.device(db,j.device_id));return out(s.public_job(s.review_job(db,user,jid,**p)))

    @router.get('/crm/pos/api/hardware/devices/{did}/audit')
    def audit(did:str,offset:int=0,db=Depends(get_db),user=Depends(require_user)):
        s.require(user,'admin');s.device(db,did)
        if offset<0:raise HTTPException(422,'Invalid offset')
        rows=db.query(HardwareEvent).filter_by(shop=s.retail.shop(),device_id=did).order_by(HardwareEvent.id.desc()).offset(offset).limit(50)
        return out({'events':[{'id':e.id,'job_id':e.job_id,'actor':e.actor_id,'action':e.action,'detail':e.detail,'at':e.created_at.isoformat()+'Z'} for e in rows]})

    @router.get('/crm/retail/api/hardware/devices')
    def label_devices(db=Depends(get_db),user=Depends(require_user)):
        s.retail.require(user,'retail.barcodes.print')
        if not settings.RETAIL_HARDWARE_LABEL_PRINT_ENABLED:return out({'devices':[]})
        return out({'devices':[{'id':d.id,'name':d.name,'register_id':d.register_id,'media':d.media} for d in db.query(PrintDevice).filter_by(shop=s.retail.shop(),status='ACTIVE')]})

    @router.post('/crm/retail/api/hardware/labels')
    async def labels(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['did','lid','key']);j=s.labels_job(db,user,**p);db.commit();return out(s.public_job(j))

    @router.post('/api/print-agent/pair')
    async def pairing(request:Request,db=Depends(get_db)):
        if not s.enabled():raise HTTPException(404,'Hardware disabled')
        if request.url.scheme!='https' or request.headers.get('origin'):raise HTTPException(403,'Native TLS pairing required')
        p=await body(request,['code'],False)
        if not isinstance(p['code'],str) or len(p['code'])>150:raise HTTPException(422,'Pairing code required')
        identity=hmac.new(settings.SECRET_KEY.encode(),str(request.client.host if request.client else '').encode(),hashlib.sha256).hexdigest();s.throttle(db,'pair:'+identity)
        return out({'token':s.pair(db,p['code']),'protocol':s.VERSION})

    @router.get('/api/print-agent/config')
    def config(request:Request,db=Depends(get_db)):
        return out(s.public_device(native(request,db)))

    @router.post('/api/print-agent/heartbeat')
    async def heartbeat(request:Request,db=Depends(get_db)):
        d=native(request,db);p=await body(request,['version','queues','queues_empty','local_verified','drawer_verified','mode_ack'],False);return out(s.heartbeat(db,d,p))

    @router.post('/api/print-agent/claim')
    async def claim(request:Request,db=Depends(get_db)):
        d=native(request,db);await body(request,[],False);return out({'job':s.claim(db,d)})

    def permit(db,d,jid,token):
        j=s.claimed(db,d,jid,token)
        if j.status!='DELIVERED_TO_AGENT' or j.attempts!=1 or j.device_version!=d.version or j.expires_at<s.now() or not s.job_enabled(j) or j.media!=d.media or d.mode_request or not s.source_valid(db,j):raise HTTPException(409,'Hardware authorization expired; do not dispatch')
        s.authorize_actor(db,j)
        return j

    @router.get('/api/print-agent/jobs/{jid}/pdf')
    def pdf(jid:str,request:Request,db=Depends(get_db)):
        d=native(request,db);j=permit(db,d,jid,request.headers.get('x-job-claim',''))
        if not j.pdf or len(j.pdf)>s.MAX_PDF:raise HTTPException(409,'Artifact unavailable')
        return Response(j.pdf,media_type='application/pdf',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','X-Content-SHA256':j.sha256})

    @router.post('/api/print-agent/jobs/{jid}/permit')
    async def authorization(jid:str,request:Request,db=Depends(get_db)):
        d=native(request,db);p=await body(request,['claim'],False);j=permit(db,d,jid,p['claim'])
        if j.permitted_at:raise HTTPException(409,'Dispatch permit already consumed; never replay')
        j.permitted_at=s.now();s.event(db,d,'DISPATCH_PERMIT',j=j);db.commit();return out({'authorized':True,'valid_seconds':5})

    @router.post('/api/print-agent/jobs/{jid}/progress')
    async def progress(jid:str,request:Request,db=Depends(get_db)):
        d=native(request,db);p=await body(request,['claim'],False);j=permit(db,d,jid,p['claim'])
        if not j.permitted_at:raise HTTPException(409,'No existing dispatch permit')
        j.delivered_at=s.now();d.last_seen=s.now();db.commit();return out({'continue':True})

    @router.post('/api/print-agent/jobs/{jid}/result')
    async def result(jid:str,request:Request,db=Depends(get_db)):
        d=native(request,db);p=await body(request,['claim','status','spool_id'],False);return out(s.acknowledge(db,d,jid,p['claim'],p['status'],p['spool_id']))
    return router
