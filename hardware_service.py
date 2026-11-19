"""Hardware authorization is downstream of immutable verified financial outcomes."""
import hashlib,hmac,io,json,secrets,time
from datetime import datetime,timedelta
from decimal import Decimal
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from models import User
from pos_models import POSCheckout,POSRegister,POSSession,POSCashMovement
from hardware_models import PrintDevice,HardwareJob,HardwareEvent,HardwareRate
from retail_models import RetailPrintJob
from settings import settings
import pos_service as pos
import pos_receipts
import retail_service as retail
import retail_labels
from windows_print_agent.protocol import *

DEFAULT={'receipt_queue':RECEIPT_QUEUE,'label_queue':LABEL_QUEUE,'drawer_queue':'','a4_queue':'','receipt_profile':'80','auto_receipts':False,'auto_cash_movements':False,'drawer_verified':False}


def now():return datetime.utcnow()
def hashed(value):return hashlib.sha256(value.encode()).hexdigest()
def enabled():return settings.POS_LOCAL_PRINT_ENABLED or settings.RETAIL_HARDWARE_LABEL_PRINT_ENABLED or settings.POS_CASH_DRAWER_AUTOMATION_ENABLED

def require(user,action='view'):
    # Read/admin recovery stays available while dispatch gates are off. Actual I/O paths
    # independently enforce their gates, so disabling printing never requires re-enabling it to audit.
    pos.require(user,action)

def event(db,d,action,user=None,j=None,**detail):
    db.add(HardwareEvent(shop=d.shop,device_id=d.id,job_id=j.id if j else None,actor_id=user.id if user else None,action=action,detail=detail))

def device(db,did):
    pos.locked(db)
    d=db.query(PrintDevice).filter_by(shop=retail.shop(),id=did).with_for_update().one_or_none()
    if not d:raise HTTPException(404,'Device not found')
    return d

def validate_config(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise HTTPException(422,'Complete device configuration required')
    if value['receipt_queue']!=RECEIPT_QUEUE or value['label_queue']!=LABEL_QUEUE:raise HTTPException(422,'Receipt and barcode queues must use their distinct approved profiles')
    if value['drawer_queue'] not in {'',DRAWER_QUEUE} or value['receipt_profile'] not in {'58','80'}:raise HTTPException(422,'Unknown printer profile')
    for key in ['auto_receipts','auto_cash_movements','drawer_verified']:
        if type(value[key]) is not bool:raise HTTPException(422,'Explicit hardware policy required')
    a4=value['a4_queue']
    if not isinstance(a4,str) or len(a4)>150 or any(ord(c)<32 for c in a4) or a4 in {RECEIPT_QUEUE,LABEL_QUEUE,DRAWER_QUEUE}:raise HTTPException(422,'A4 requires a separate suitable printer queue')
    if value['drawer_verified'] and not value['drawer_queue']:raise HTTPException(422,'Verified isolated drawer queue required')
    return dict(value)

def enroll(db,user,register_id,name='PixiePinks Main Counter PC'):
    require(user,'admin');pos.locked(db)
    reg=db.query(POSRegister).filter_by(shop=retail.shop(),id=register_id).one_or_none()
    if not reg:raise HTTPException(422,'Choose an existing register and its actual Shopify location')
    name=pos.inventory.text(name,100,True)
    d=PrintDevice(id=str(uuid4()),shop=reg.shop,register_id=reg.id,location_id=reg.location_id,name=name,status='PAIRING',config=dict(DEFAULT));db.add(d);db.flush()
    code=pair_code(d);event(db,d,'ENROLLMENT_CREATED',user);db.commit();return d,code

def pair_code(d):
    code=secrets.token_urlsafe(18);d.pair_hash=hashed(code);d.pair_expires=now()+timedelta(minutes=5)
    return d.id+'.'+code

def configure(db,user,did,value):
    require(user,'admin');pos.locked(db);d=device(db,did)
    if d.status=='REVOKED':raise HTTPException(409,'Revoked devices cannot be reactivated')
    expire_deliveries(db,d)
    if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['DELIVERED_TO_AGENT','UNKNOWN'])).first():raise HTTPException(409,'Resolve pending/uncertain hardware actions before reconfiguration')
    d.config=validate_config(value);d.version+=1;d.media='UNKNOWN';d.mode_request=None;d.observation=None
    d.enabled_at=now();event(db,d,'CONFIGURATION_CHANGED',user,version=d.version,config=d.config);db.commit();return d

def manage(db,user,did,action):
    require(user,'admin');pos.locked(db);d=device(db,did);code=None
    if d.status=='REVOKED' and action!='revoke':raise HTTPException(409,'Revoked devices require a new enrollment')
    if action=='activate':
        other=db.query(PrintDevice).filter_by(active_register_id=d.register_id).first()
        if other and other.id!=d.id:raise HTTPException(409,'Deactivate the register existing device first')
        if db.query(HardwareJob).filter(HardwareJob.register_id==d.register_id,HardwareJob.device_id!=d.id,HardwareJob.status.in_(['DELIVERED_TO_AGENT','UNKNOWN'])).first():raise HTTPException(409,'Review the previous device uncertain actions before replacing it')
        if d.status=='REVOKED' or not d.token_hash:raise HTTPException(409,'Pair an unrevoked device first')
        d.active_register_id=d.register_id;d.status='ACTIVE';d.enabled_at=now();d.media='UNKNOWN'
    elif action in {'deactivate','revoke','rotate'}:
        d.active_register_id=None;d.status='REVOKED' if action=='revoke' else 'PAIRING' if action=='rotate' else 'INACTIVE';d.version+=1;d.media='UNKNOWN';d.mode_request=None
        if action in {'revoke','rotate'}:d.token_hash=None;d.pair_hash=None;d.pair_expires=None
        if action=='rotate':code=pair_code(d)
        for j in db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT'])):
            j.status='UNKNOWN' if j.attempts else 'FAILED';j.result={'code':'DEVICE_'+action.upper()};event(db,d,'JOB_DEVICE_STOPPED',user,j)
    else:raise HTTPException(422,'Unknown device action')
    event(db,d,'DEVICE_'+action.upper(),user);db.commit();return code

def throttle(db,identity,limit=12):
    # Rate rows are independent of the financial/shop lock, including unauthenticated traffic.
    from crm_analytics_tracking import insert_for
    connection=db.connection()
    if connection.dialect.name=='sqlite' and not connection.connection.driver_connection.in_transaction:connection.exec_driver_sql('BEGIN IMMEDIATE')
    key=hashed(identity);bucket=int(time.time()//60)
    db.execute(insert_for(connection,HardwareRate.__table__).values(key=key,bucket=bucket,count=0).on_conflict_do_nothing(index_elements=['key']))
    r=db.execute(select(HardwareRate).where(HardwareRate.key==key).with_for_update()).scalar_one()
    if r.bucket!=bucket:r.bucket=bucket;r.count=0
    if r.count>=limit:db.commit();raise HTTPException(429,'Hardware request rate exceeded')
    r.count+=1;db.commit()


def pair(db,code):
    if not enabled():raise HTTPException(404,'Hardware disabled')
    pos.locked(db)
    try:did,secret=code.split('.',1);d=device(db,did)
    except (ValueError,AttributeError,HTTPException):raise HTTPException(401,'Pairing unavailable') from None
    if not d.pair_hash or not d.pair_expires or d.pair_expires<now() or not hmac.compare_digest(d.pair_hash,hashed(secret)) or d.status=='REVOKED':raise HTTPException(401,'Pairing unavailable')
    token=secrets.token_urlsafe(32);d.token_hash=hashed(token);d.pair_hash=None;d.pair_expires=None;d.status='INACTIVE';d.version+=1;d.media='UNKNOWN'
    event(db,d,'PAIRED');db.commit();return d.id+'.'+token

def authenticate(db,credential):
    if not enabled():raise HTTPException(404,'Hardware disabled')
    if not isinstance(credential,str) or not credential.startswith('Bearer ') or len(credential)>200:raise HTTPException(401,'Device authentication unavailable')
    try:
        did,secret=credential.removeprefix('Bearer ').split('.',1);d=device(db,did)
    except (AttributeError,ValueError,HTTPException):raise HTTPException(401,'Device authentication unavailable') from None
    if not d.token_hash or not hmac.compare_digest(d.token_hash,hashed(secret)) or d.status!='ACTIVE':raise HTTPException(401,'Device authentication unavailable')
    return d

def public_device(d):
    return {'id':d.id,'name':d.name,'register_id':d.register_id,'location':d.location_id,'status':d.status,'version':d.version,'config':d.config,'media':d.media,'mode_request':d.mode_request,
        'connected':bool(d.last_seen and d.last_seen>now()-timedelta(seconds=60)),'last_seen':d.last_seen.isoformat()+'Z' if d.last_seen else None,'observation':d.observation or {}}

def request_mode(db,user,did,mode,confirm):
    require(user,'sell');pos.locked(db);d=device(db,did)
    pos.session(db,user,db.query(POSSession).filter_by(active_register_id=d.register_id).one().id) if db.query(POSSession).filter_by(active_register_id=d.register_id).first() else pos.require(user,'admin')
    if mode not in MODES or confirm is not True:raise HTTPException(422,'Confirm installed physical media and cleared Windows queues')
    if mode=='A4' and not d.config['a4_queue']:raise HTTPException(409,'No verified A4 printer assigned')
    expire_deliveries(db,d)
    if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['DELIVERED_TO_AGENT','UNKNOWN'])).first():raise HTTPException(409,'Resolve active or uncertain actions before switching media')
    if d.last_seen and d.last_seen>now()-timedelta(seconds=60) and d.observation and not d.observation.get('queues_empty'):raise HTTPException(409,'Agent reports busy queues; inspect and wait before changing paper')
    d.media='UNKNOWN';d.mode_request=mode;event(db,d,'MEDIA_REQUESTED',user,mode=mode);db.commit()

def heartbeat(db,d,body):
    if set(body)!={'version','queues','queues_empty','local_verified','drawer_verified','mode_ack'} or type(body['queues_empty']) is not bool or type(body['local_verified']) is not bool or type(body['drawer_verified']) is not bool:raise HTTPException(422,'Invalid agent status')
    queues=body['queues']
    if not isinstance(queues,list) or len(queues)>100 or any(not isinstance(q,dict) or set(q)!={'name','state','jobs'} or not isinstance(q['name'],str) or len(q['name'])>150 or q['state'] not in {'READY','OFFLINE','ERROR','UNAVAILABLE'} or type(q['jobs']) is not int or not 0<=q['jobs']<=10000 for q in queues):raise HTTPException(422,'Invalid printer telemetry')
    required={d.config['receipt_queue'],d.config['label_queue']}|{n for n in [d.config['drawer_queue'],d.config['a4_queue']] if n}
    names=[q['name'] for q in queues]
    if len(names)!=len(set(names)) or not required<=set(names):raise HTTPException(422,'Complete unique configured queue telemetry required')
    computed_empty=all(q['jobs']==0 and q['state']=='READY' for q in queues)
    if body['queues_empty'] and not computed_empty:raise HTTPException(422,'Queue status contradicts empty assertion')
    d.last_seen=now();d.observation=body
    if body['version']==d.version and body['mode_ack']==d.mode_request and d.mode_request and body['queues_empty'] and body['local_verified']:
        d.media=d.mode_request;d.mode_request=None;event(db,d,'MEDIA_AGENT_ACKNOWLEDGED',mode=d.media)
    elif body['version']!=d.version or not body['local_verified']:d.media='UNKNOWN'
    db.commit();return public_device(d)

def expire_deliveries(db,d):
    for j in db.query(HardwareJob).filter_by(device_id=d.id,status='DELIVERED_TO_AGENT'):
        if j.delivered_at<now()-timedelta(minutes=2):j.status='UNKNOWN';j.result={'code':'AGENT_ACK_UNCERTAIN'};event(db,d,'DELIVERY_UNCERTAIN',j=j)

def profile_queue(d,kind,profile):
    if kind=='LABEL':return d.config['label_queue'],'LABEL'
    if kind=='DRAWER':return d.config['drawer_queue'],'RECEIPT'
    if profile=='A4':
        if not d.config['a4_queue']:raise HTTPException(409,'No suitable A4 printer configured; use browser PDF fallback')
        return d.config['a4_queue'],'A4'
    if profile not in {'58','80'}:raise HTTPException(422,'Invalid receipt profile')
    return d.config['receipt_queue'],'RECEIPT'

def job_enabled(j):
    if j.kind=='LABEL':return settings.RETAIL_HARDWARE_LABEL_PRINT_ENABLED
    if j.kind=='DRAWER':return settings.POS_LOCAL_PRINT_ENABLED and settings.POS_CASH_DRAWER_AUTOMATION_ENABLED
    return settings.POS_LOCAL_PRINT_ENABLED

def add_job(db,d,kind,key,raw,user=None,checkout=None,label=None,parent=None,profile='80',reason='',automatic=False,session_id=None,event_time=None):
    prior=db.query(HardwareJob).filter_by(business_key=key).one_or_none()
    if prior:return prior
    if d.status!='ACTIVE':raise HTTPException(409,'Active paired device required')
    queue,mode=profile_queue(d,kind,profile)
    if len(raw)>MAX_PDF or (kind!='DRAWER' and not raw.startswith(b'%PDF-')):raise HTTPException(422,'Bounded valid PDF required')
    j=HardwareJob(id=str(uuid4()),shop=d.shop,device_id=d.id,register_id=d.register_id,location_id=d.location_id,device_version=d.version,business_key=key,kind=kind,queue=queue,media=mode,actor_id=user.id if user else None,
        session_id=checkout.session_id if checkout else session_id,checkout_id=checkout.id if checkout else None,label_id=label.id if label else None,parent_id=parent.id if parent else None,detail={'reason':reason,'profile':'RETAIL_LABEL_PDF' if kind=='LABEL' else profile,'label_template':label.template if label else None,'label_copies':label.total if label else None,'automatic':automatic,'physical_resource':'DBL_SHARED' if mode!='A4' else 'A4_EXTERNAL','protocol':VERSION},
        pdf=raw or None,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),status='QUEUED',expires_at=(min(checkout.completed_at,checkout.created_at)+timedelta(seconds=120)) if automatic and kind=='DRAWER' and checkout else (event_time or now())+timedelta(seconds=120) if kind=='DRAWER' else now()+timedelta(hours=24))
    if not job_enabled(j):raise HTTPException(409,'This hardware workflow is disabled')
    db.add(j);db.flush();event(db,d,'JOB_CREATED',user,j,kind=kind,reference=checkout.reference if checkout else str(label.id) if label else '',reason=reason);return j

def receipt_job(db,user,did,rid,key,profile='80',automatic=False):
    require(user);pos.locked(db);d=device(db,did);r=pos.sale(db,user,rid)
    if r.status!='COMPLETE':raise HTTPException(409,'Verified COMPLETE sale required')
    s=db.get(POSSession,r.session_id)
    if s.register_id!=d.register_id or r.shop!=d.shop or r.frozen['location']!=d.location_id:raise HTTPException(403,'Sale/register/location mismatch')
    if profile!='A4' and profile!=d.config['receipt_profile']:raise HTTPException(409,'Receipt width does not match operator-configured stock')
    business='receipt:auto:'+r.id if automatic else 'receipt:'+d.id+':'+key
    prior=db.query(HardwareJob).filter_by(business_key=business).one_or_none()
    if prior:
        if prior.checkout_id!=r.id or prior.detail['profile']!=profile or prior.device_id!=d.id:raise HTTPException(409,'Request key belongs to another receipt')
        return prior
    raw=pos_receipts.generate(db,user,r.id,profile,commit=False,duplicate=not automatic)
    j=add_job(db,d,'RECEIPT',business,raw,user,r,profile=profile,automatic=automatic)
    if automatic and any(t['method']=='CASH' and Decimal(t['amount'])>0 for t in r.frozen['tenders']):
        if settings.POS_CASH_DRAWER_AUTOMATION_ENABLED and d.config['drawer_verified'] and (d.observation or {}).get('drawer_verified'):
            add_job(db,d,'DRAWER','drawer:sale:'+r.id,b'',user,r,parent=j,reason='Verified cash component',automatic=True)
        else:event(db,d,'CASH_DRAWER_BLOCKED',user,j,reason='Drawer gate or verified isolated driver queue unavailable')
    return j

def labels_job(db,user,did,lid,key):
    retail.require(user,'retail.barcodes.print');pos.locked(db);d=device(db,did)
    if not settings.RETAIL_HARDWARE_LABEL_PRINT_ENABLED:raise HTTPException(409,'Local label printing disabled')
    row=db.query(RetailPrintJob).filter_by(shop=d.shop,id=lid).one_or_none()
    if not row or row.status!='GENERATED':raise HTTPException(409,'Generate and preview-confirm the existing label PDF first')
    if row.template.get('layout')!='roll':raise HTTPException(409,'Local DBL labels require roll stock; retain A4 browser fallback')
    retail_labels.ensure_reprint_safe(db,row);raw=retail_labels.read_pdf(row)
    prior=db.query(HardwareJob).filter_by(business_key='label:'+d.id+':'+key).first()
    if prior and (prior.label_id!=row.id or prior.sha256!=hashlib.sha256(raw).hexdigest()):raise HTTPException(409,'Key already used for different label document')
    j=add_job(db,d,'LABEL','label:'+d.id+':'+key,raw,user,label=row)
    retail.audit(db,user,'LOCAL_LABEL_QUEUED',job_id=row.id,hardware_job=j.id,device=d.id);return j

def manual_drawer(db,user,did,key,reason):
    require(user,'drawer.open');pos.locked(db);d=device(db,did)
    s=db.query(POSSession).filter_by(active_register_id=d.register_id,status='OPEN').one_or_none()
    if not s:raise HTTPException(409,'Open assigned register required')
    pos.session(db,user,s.id)
    if not d.config['drawer_verified'] or not (d.observation or {}).get('drawer_verified'):raise HTTPException(409,'Server and local isolated drawer verification required')
    reason=pos.inventory.text(reason,500,True)
    j=add_job(db,d,'DRAWER','drawer:manual:'+d.id+':'+key,b'',user,reason=reason,session_id=s.id)
    if j.detail['reason']!=reason:raise HTTPException(409,'Key already used for another reason')
    return j

def printer_test(db,user,did,key):
    require(user,'admin');pos.locked(db);d=device(db,did)
    from fpdf import FPDF
    pdf=FPDF(unit='mm',format=(int(d.config['receipt_profile']),60));pdf.set_margins(5,5,5);pdf.set_auto_page_break(False);pdf.add_page();pdf.set_font('Helvetica',size=10);pdf.multi_cell(0,5,'PixiePinks printer test\nNOT A SALES RECEIPT\nNormal queue: drawer must remain closed.')
    return add_job(db,d,'TEST','test:'+d.id+':'+key,bytes(pdf.output()),user,profile=d.config['receipt_profile'])

def public_job(j):return {'id':j.id,'device_id':j.device_id,'kind':j.kind,'status':j.status,'queue':j.queue,'media':j.media,'checkout_id':j.checkout_id,'label_id':j.label_id,'attempts':j.attempts,'created_at':j.created_at.isoformat()+'Z','detail':j.detail,'result':j.result}

def claim(db,d):
    pos.locked(db);expire_deliveries(db,d)
    if db.query(HardwareJob).filter(HardwareJob.shop==d.shop,HardwareJob.status.in_(['DELIVERED_TO_AGENT','UNKNOWN'])).first():db.commit();return None
    if not d.last_seen or d.last_seen<now()-timedelta(seconds=60) or d.media not in MODES or d.mode_request:db.commit();return None
    for j in db.query(HardwareJob).filter_by(device_id=d.id,status='QUEUED').order_by(HardwareJob.created_at).limit(50):
        if j.attempts:
            j.status='UNKNOWN';j.result={'code':'PERMANENT_ATTEMPT_FENCE'};event(db,d,'ATTEMPT_FENCE_BLOCKED',j=j);continue
        if j.expires_at<now() or j.device_version!=d.version:
            j.status='FAILED';j.result={'code':'EXPIRED_OR_CONFIGURATION_CHANGED'};event(db,d,'JOB_EXPIRED',j=j);continue
        if not source_valid(db,j):
            j.status='FAILED';j.result={'code':'SOURCE_NO_LONGER_ELIGIBLE'};event(db,d,'SOURCE_INELIGIBLE',j=j);continue
        if not job_enabled(j) or j.media!=d.media:continue
        try:authorize_actor(db,j)
        except HTTPException:j.status='FAILED';j.result={'code':'ACTOR_PERMISSION_REVOKED'};event(db,d,'JOB_PERMISSION_REVOKED',j=j);continue
        if j.kind=='DRAWER' and (not d.config['drawer_verified'] or not (d.observation or {}).get('drawer_verified')):continue
        if j.parent_id:
            p=db.get(HardwareJob,j.parent_id)
            if p.status not in {'SUBMITTED_TO_PRINTER','CONFIRMED'}:continue
        queues={q['name']:q for q in (d.observation or {}).get('queues',[])}
        if not (d.observation or {}).get('local_verified') or not (d.observation or {}).get('queues_empty') or queues.get(j.queue,{}).get('state')!='READY':continue
        secret=secrets.token_urlsafe(24);j.claim_hash=hashed(secret);j.attempts=1;j.status='DELIVERED_TO_AGENT';j.delivered_at=now();event(db,d,'DELIVERED_TO_AGENT',j=j);db.commit()
        return {**public_job(j),'version':j.device_version,'protocol':VERSION,'sha256':j.sha256,'size':j.size,'claim':secret}
    db.commit();return None

def claimed(db,d,jid,claim_token):
    if not isinstance(claim_token,str) or not 10<=len(claim_token)<=100:raise HTTPException(403,'Job authorization unavailable')
    j=db.query(HardwareJob).filter_by(id=jid,device_id=d.id,shop=d.shop,register_id=d.register_id,location_id=d.location_id).one_or_none()
    if not j or not j.claim_hash or not hmac.compare_digest(j.claim_hash,hashed(claim_token)):raise HTTPException(403,'Job authorization unavailable')
    return j

def acknowledge(db,d,jid,secret,status,spool_id=None):
    if status not in {'SUBMITTED_TO_PRINTER','FAILED','UNKNOWN'} or (spool_id is not None and (type(spool_id) is not int or spool_id<0)):raise HTTPException(422,'Invalid outcome')
    pos.locked(db);j=claimed(db,d,jid,secret)
    if j.status in {'DELIVERED_TO_AGENT','UNKNOWN'}:
        j.status=status;j.result={'code':status,'spool_id':spool_id,'physical_output_verified':False};j.finished_at=now();event(db,d,'AGENT_'+status,j=j,spool_id=spool_id)
    elif j.status!=status:raise HTTPException(409,'Outcome already resolved; never resend hardware action')
    db.commit();return public_job(j)

def review_job(db,user,jid,confirm,reason,agent_stopped):
    require(user);pos.locked(db);j=db.query(HardwareJob).filter_by(shop=retail.shop(),id=jid).one_or_none()
    if not j:raise HTTPException(404,'Job not found')
    d=device(db,j.device_id);expire_deliveries(db,d)
    if j.kind=='LABEL':retail.require(user,'retail.barcodes.print')
    if j.kind=='DRAWER':pos.require(user,'drawer.open')
    if j.status not in {'UNKNOWN','SUBMITTED_TO_PRINTER','FAILED'}:raise HTTPException(409,'Only an existing outcome may be reviewed')
    if j.status=='UNKNOWN' and agent_stopped is not True:raise HTTPException(409,'Stop/review the agent and inspect both queues before resolving uncertainty')
    reason=pos.inventory.text(reason,500,True)
    if type(confirm) is not bool:raise HTTPException(422,'Explicit physical inspection required')
    j.status='CONFIRMED' if confirm else 'FAILED';j.finished_at=now();j.result={'code':'STAFF_CONFIRMED' if confirm else 'STAFF_REVIEWED_NOT_CONFIRMED','physical_output_verified':confirm,'reason':reason}
    if confirm and j.label_id:
        row=db.get(RetailPrintJob,j.label_id);row.printed_at=now();retail.audit(db,user,'USER_CONFIRMED_PRINTED',job_id=row.id,hardware_job=j.id)
    event(db,d,'STAFF_REVIEWED',user,j,confirmed=confirm,reason=reason,agent_stopped=agent_stopped);db.commit();return j

def authorize_actor(db,j):
    user=db.get(User,j.actor_id) if j.actor_id else None
    if j.kind=='LABEL':retail.require(user,'retail.barcodes.print')
    else:pos.require(user,'admin' if j.kind=='TEST' else 'drawer.open' if j.kind=='DRAWER' and not j.detail['automatic'] else 'view')


def source_valid(db,j):
    if j.label_id:
        label=db.query(RetailPrintJob).filter_by(id=j.label_id,shop=j.shop,status='GENERATED').one_or_none()
        if not label:return False
        try:retail_labels.ensure_reprint_safe(db,label)
        except HTTPException:return False
    if j.checkout_id:
        sale=db.get(POSCheckout,j.checkout_id)
        if not sale or sale.status!='COMPLETE' or sale.shop!=j.shop or sale.frozen['location']!=j.location_id:return False
    if j.kind=='DRAWER':
        session=db.get(POSSession,j.session_id)
        if not session or session.status!='OPEN' or session.register_id!=j.register_id:return False
    return True


def tick(sessions):
    if not enabled():return
    with sessions() as db:
        pos.locked(db)
        for old in db.query(HardwareRate).filter(HardwareRate.bucket<int(time.time()//60)-1440).limit(100):db.delete(old)
        for d in db.query(PrintDevice).filter_by(shop=retail.shop(),status='ACTIVE'):
            expire_deliveries(db,d)
            for stale in db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status=='QUEUED',HardwareJob.expires_at<now()).limit(50):
                stale.status='FAILED';stale.result={'code':'EXPIRED_OR_CONFIGURATION_CHANGED'};event(db,d,'JOB_EXPIRED',j=stale)
            for j in db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.created_at<now()-timedelta(days=7),HardwareJob.pdf.isnot(None)).limit(20):j.pdf=None
            if not settings.POS_ENABLED or not settings.POS_LOCAL_PRINT_ENABLED or not d.enabled_at:continue
            cutoff=max(d.enabled_at,now()-timedelta(hours=24))
            if d.config['auto_receipts']:
                done=db.query(HardwareJob.checkout_id).filter_by(shop=d.shop,kind='RECEIPT').filter(HardwareJob.business_key.like('receipt:auto:%'))
                rows=db.query(POSCheckout).join(POSSession,POSSession.id==POSCheckout.session_id).filter(POSCheckout.shop==d.shop,POSSession.register_id==d.register_id,POSCheckout.status=='COMPLETE',POSCheckout.completed_at>=cutoff,~POSCheckout.id.in_(done.filter(HardwareJob.checkout_id.isnot(None)))).order_by(POSCheckout.completed_at).limit(2).all()
                for r in rows:
                    user=db.get(User,r.actor_id)
                    try:
                        with db.begin_nested():receipt_job(db,user,d.id,r.id,'auto',d.config['receipt_profile'],True);db.flush()
                    except Exception:
                        # Reserve the original business key once. Failure is downstream and never
                        # changes the COMPLETE sale, retries automatically, or floods audit on each tick.
                        j=HardwareJob(id=str(uuid4()),shop=d.shop,device_id=d.id,register_id=d.register_id,location_id=d.location_id,device_version=d.version,business_key='receipt:auto:'+r.id,kind='RECEIPT',queue=d.config['receipt_queue'],media='RECEIPT',actor_id=r.actor_id,session_id=r.session_id,checkout_id=r.id,detail={'automatic':True,'reason':'Original generation requires operator review','profile':d.config['receipt_profile']},sha256=hashlib.sha256(b'').hexdigest(),size=0,status='FAILED',expires_at=now()+timedelta(hours=24),result={'code':'RECEIPT_GENERATION_BLOCKED'})
                        db.add(j);db.flush();event(db,d,'AUTO_RECEIPT_BLOCKED',j=j,reference=r.reference)
            if settings.POS_CASH_DRAWER_AUTOMATION_ENABLED and d.config['auto_cash_movements'] and d.config['drawer_verified']:
                for m in db.query(POSCashMovement).join(POSSession,POSSession.id==POSCashMovement.session_id).filter(POSSession.register_id==d.register_id,POSCashMovement.kind.in_(['PAID_IN','PAID_OUT']),POSCashMovement.created_at>=max(cutoff,now()-timedelta(seconds=120))).limit(10):
                    add_job(db,d,'DRAWER','drawer:movement:'+str(m.id),b'',db.get(User,m.actor_id),reason='Approved cash movement '+m.reference,automatic=True,session_id=m.session_id,event_time=m.created_at)
        db.commit()
