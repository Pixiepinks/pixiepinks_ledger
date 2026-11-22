"""Daily register authorization and atomic receipt reservation; no hardware control changes."""
import hashlib
from datetime import timedelta
from uuid import uuid4
from fastapi import HTTPException
from models import User
from pos_models import POSDailyPolicy,POSDailySale,POSCheckout,POSOperation,POSPosting,POSSession,POSRegister,POSPilotAuthorization
from hardware_models import PrintDevice,HardwareJob
from settings import settings
import pos_service as pos
import hardware_service as h


def require_admin(user):
    pos.require(user,'admin')
    if not settings.POS_DAILY_OPERATIONS_ENABLED:raise HTTPException(404,'Daily POS release disabled')


def policy(db,rid):return db.query(POSDailyPolicy).filter_by(register_id=rid,shop=h.retail.shop()).one_or_none()


def target(db,p,ready=False):
    d=h.device(db,p.device_id);r=db.get(POSRegister,p.register_id);c=d.config
    if not r or r.shop!=p.shop or d.shop!=p.shop or d.location_id!=r.location_id or d.status!='ACTIVE' or d.active_register_id!=p.register_id or d.register_id!=p.register_id or d.version!=p.device_version or not c.get('receipt_only') or c.get('receipt_profile')!='80' or c.get('receipt_queue')!=h.RECEIPT_QUEUE or c.get('auto_receipts') or c.get('drawer_queue') or c.get('drawer_verified') or c.get('auto_cash_movements'):
        raise HTTPException(409,'Daily shop/register/location/device binding changed or unsupported hardware policy')
    if not settings.PRINT_CONNECTOR_ENCRYPTED_ENABLED or not settings.POS_LOCAL_PRINT_ENABLED or settings.POS_CASH_DRAWER_AUTOMATION_ENABLED:raise HTTPException(409,'Approved encrypted receipt printing with cash drawer OFF required')
    if ready:
        if c.get('printing_paused') or not d.last_seen or d.last_seen<h.now()-timedelta(seconds=60) or d.media!='RECEIPT' or d.mode_request or not (d.observation or {}).get('local_verified') or not (d.observation or {}).get('queues_empty'):raise HTTPException(409,'Daily connector must be current, attested, unpaused, empty and on receipt media')
        if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN'])).first():raise HTTPException(409,'Inspect unresolved hardware work before daily release/opening')
    return d


def financial_clear(db):
    if db.query(POSOperation).filter(POSOperation.shop==h.retail.shop(),POSOperation.status.in_(['REVIEW','UNCERTAIN','CONFLICT'])).first():raise HTTPException(409,'Resolve financial REVIEW/UNCERTAIN before new daily intake; never resend CREATE')


def configure(db,user,device_id,enabled,notes,confirm):
    pos.require(user,'admin');pos.locked(db)
    if enabled and not settings.POS_DAILY_OPERATIONS_ENABLED:raise HTTPException(409,'Daily POS feature flag is OFF')
    if confirm is not True or type(enabled) is not bool:raise HTTPException(422,'Explicit daily register release confirmation required')
    d=h.device(db,device_id);p=policy(db,d.register_id)
    if db.query(POSCheckout).join(POSSession,POSSession.id==POSCheckout.session_id).filter(POSSession.register_id==d.register_id,POSCheckout.status.notin_(['COMPLETE','CANCELLED'])).first():raise HTTPException(409,'Resolve pending financial work before changing daily authorization')
    if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN'])).first():raise HTTPException(409,'Pause/inspect outstanding hardware jobs before policy changes')
    if enabled:
        financial_clear(db)
        preview=POSDailyPolicy(shop=d.shop,register_id=d.register_id,device_id=d.id,device_version=d.version)
        target(db,preview,True)
        if not db.query(HardwareJob).filter_by(device_id=d.id,device_version=d.version,kind='TEST',status='CONFIRMED').first():raise HTTPException(409,'Current-version physical receipt acceptance required')
        cfg,_=pos.cfg(db)
        if cfg['tax_mode']=='UNCONFIGURED' or not cfg['accounts']:raise HTTPException(409,'Configure normal tax/accounts before daily release')
    notes=pos.inventory.text(notes,1000,True)
    if p is None:p=POSDailyPolicy(register_id=d.register_id,shop=d.shop,version=0);db.add(p)
    p.device_id=d.id;p.device_version=d.version;p.version+=1;p.enabled=int(enabled)
    p.actor_id=user.id;p.notes=notes;p.updated_at=h.now()
    pos.audit(db,user,d.id,'DAILY_REGISTER_POLICY',shop=d.shop,register_id=d.register_id,location=d.location_id,device_version=d.version,version=p.version,enabled=enabled,notes=notes)
    db.commit();return p


def opening(db,user,rid):
    p=policy(db,rid)
    if not p:return
    if not settings.POS_DAILY_OPERATIONS_ENABLED or not p.enabled:raise HTTPException(409,'Daily register not released')
    financial_clear(db);target(db,p,True)


def before_checkout(db,user,cart):
    if db.get(POSPilotAuthorization,cart.id):return  # Preserve immutable per-cart pilot.
    ss=db.get(POSSession,cart.session_id);p=policy(db,ss.register_id)
    if not p:return
    if not settings.POS_DAILY_OPERATIONS_ENABLED or not p.enabled:raise HTTPException(409,'Daily intake disabled')
    financial_clear(db);d=target(db,p)
    if d.config.get('printing_paused'):raise HTTPException(409,'Printing paused: resolve incident before accepting the next daily payment')
    if db.query(HardwareJob).filter_by(device_id=d.id,status='UNKNOWN').first():raise HTTPException(409,'Supervisor must review uncertain receipt before new daily intake')


def capture(db,user,sale):
    if db.get(POSPilotAuthorization,sale.cart_id):return
    ss=db.get(POSSession,sale.session_id);p=policy(db,ss.register_id)
    if not p:return
    db.add(POSDailySale(checkout_id=sale.id,shop=sale.shop,register_id=p.register_id,policy_version=p.version,
        device_id=p.device_id,device_version=p.device_version,actor_id=user.id))
    pos.audit(db,user,sale.reference,'DAILY_CHECKOUT_BOUND',register_id=p.register_id,policy_version=p.version,device_id=p.device_id,device_version=p.device_version)
    db.flush()


def dispatch_allowed(db,sale):
    b=db.get(POSDailySale,sale.id)
    if not b:return True
    p=policy(db,b.register_id)
    if not settings.POS_DAILY_OPERATIONS_ENABLED or not p or not p.enabled:return False
    ss=db.get(POSSession,sale.session_id)
    if p.version!=b.policy_version or p.device_id!=b.device_id or p.device_version!=b.device_version or sale.shop!=b.shop or not ss or ss.register_id!=b.register_id or sale.frozen['location']!=db.get(POSRegister,b.register_id).location_id:raise HTTPException(409,'Daily sale authorization changed')
    target(db,p);return True


def reserve_receipt(db,sale):
    b=db.get(POSDailySale,sale.id)
    if not b:return
    key='receipt:auto:'+sale.id
    if db.query(HardwareJob).filter_by(business_key=key).first():return
    u=db.get(User,b.actor_id);d=db.get(PrintDevice,b.device_id)
    try:
        with db.begin_nested():
            if not dispatch_allowed(db,sale):raise HTTPException(409,'Daily release off')
            h.receipt_job(db,u,d.id,sale.id,'daily','80',automatic=True,daily_checkout_id=sale.id);db.flush()
    except Exception:
        j=HardwareJob(id=str(uuid4()),shop=sale.shop,device_id=b.device_id,register_id=b.register_id,location_id=sale.frozen['location'],device_version=b.device_version,
            business_key=key,kind='RECEIPT',queue=h.RECEIPT_QUEUE,media='RECEIPT',actor_id=b.actor_id,session_id=sale.session_id,checkout_id=sale.id,
            detail={'automatic':True,'profile':'80','daily_checkout_id':sale.id},sha256=hashlib.sha256(b'').hexdigest(),size=0,status='FAILED',attempts=0,
            expires_at=h.now()+timedelta(hours=24),result={'code':'DAILY_RECEIPT_REQUIRES_REVIEW'})
        db.add(j);db.flush();h.event(db,d,'DAILY_RECEIPT_BLOCKED',u,j=j)
    else:h.event(db,d,'DAILY_RECEIPT_QUEUED',u,j=db.query(HardwareJob).filter_by(business_key=key).one())


def valid_receipt(db,j):
    b=db.get(POSDailySale,j.detail.get('daily_checkout_id'));s=db.get(POSCheckout,j.checkout_id) if j.checkout_id else None
    return bool(b and s and b.checkout_id==s.id and b.device_id==j.device_id and b.device_version==j.device_version and b.register_id==j.register_id and b.shop==j.shop and
        j.kind=='RECEIPT' and s.status=='COMPLETE' and db.query(POSOperation).filter_by(checkout_id=s.id,kind='CREATE',status='VERIFIED').first() and db.query(POSPosting).filter_by(reference='POS-sale:'+s.id).first() and j.business_key=='receipt:auto:'+s.id)


def sale_status(db,sale):
    b=db.get(POSDailySale,sale.id)
    if not b:return None
    op=db.query(POSOperation).filter_by(checkout_id=sale.id,kind='CREATE').one_or_none();j=db.query(HardwareJob).filter_by(business_key='receipt:auto:'+sale.id).one_or_none();d=db.get(PrintDevice,b.device_id)
    obs=d.observation or {};q=next((q for q in obs.get('queues',[]) if q.get('name')==h.RECEIPT_QUEUE),{})
    connected=bool(d.last_seen and d.last_seen>h.now()-timedelta(seconds=60))
    return {'shopify':'VERIFIED' if op and op.status=='VERIFIED' else op.status if op else 'PENDING',
        'accounting':'COMPLETE' if sale.status=='COMPLETE' and db.query(POSPosting).filter_by(reference='POS-sale:'+sale.id).first() else 'PENDING',
        'receipt':h.public_job(j) if j else None,'printer_paused':bool(d.config.get('printing_paused')),
        'printer_ready':bool(connected and d.media=='RECEIPT' and obs.get('local_verified') and q.get('state')=='READY' and q.get('jobs')==0),
        'device_id':b.device_id,'physical_output_verified':bool(j and (j.result or {}).get('physical_output_verified'))}
