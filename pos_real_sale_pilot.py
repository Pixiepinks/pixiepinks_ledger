"""Scoped real-sale authorization and downstream receipt reservation.

Never dispatches a Shopify mutation; all real tenders, CREATE/read-back and posting
remain in pos_service. A historical shop-wide REVIEW is never bypassed.
"""
import re
from datetime import timedelta
from fastapi import HTTPException
from models import User,SalesOrder
from pos_models import POSPilotScope,POSPilotAuthorization,POSCheckout,POSOperation,POSSession,POSRegister,POSAudit,POSPosting
from hardware_models import PrintDevice,HardwareJob
from settings import settings
import pos_service as pos
import hardware_service as hardware


def require(user):
    pos.require(user,'admin')
    if not settings.POS_REAL_SALE_PILOT_ENABLED:raise HTTPException(404,'Real-sale pilot disabled')


def domain_clear(db,exclude=None):
    q=db.query(POSOperation).filter(POSOperation.shop==hardware.retail.shop(),POSOperation.status.in_(['QUEUED','DISPATCHING','UNCERTAIN','REVIEW','CONFLICT']))
    if exclude:q=q.filter(POSOperation.checkout_id!=exclude)
    if q.first():raise HTTPException(409,'Shop financial queue requires reviewed recovery; pilot cannot bypass existing operations')
    # Include unpaid pending checkouts not yet represented by an operation.
    q=db.query(POSCheckout).filter(POSCheckout.shop==hardware.retail.shop(),POSCheckout.status.notin_(['COMPLETE','CANCELLED']))
    if exclude:q=q.filter(POSCheckout.id!=exclude)
    if q.first():raise HTTPException(409,'Resolve existing pending shop sales before the real-sale pilot')


def scope(db):return db.get(POSPilotScope,hardware.retail.shop())


def target(db,s,ready=False):
    d=hardware.device(db,s.device_id);c=d.config
    if (d.status!='ACTIVE' or d.active_register_id!=s.register_id or d.register_id!=s.register_id or
        d.location_id!=s.location_id or not c.get('receipt_only') or c.get('receipt_profile')!='80' or
        c.get('receipt_queue')!=hardware.RECEIPT_QUEUE or c.get('drawer_queue') or
        c.get('drawer_verified') or c.get('auto_cash_movements') or c.get('auto_receipts')):
        raise HTTPException(409,'Pilot requires its approved dedicated 80 mm device with general auto receipts and drawer OFF')
    if not settings.POS_LOCAL_PRINT_ENABLED or not settings.PRINT_CONNECTOR_ENCRYPTED_ENABLED or settings.POS_CASH_DRAWER_AUTOMATION_ENABLED:
        raise HTTPException(409,'Approved encrypted receipt transport and cash-drawer OFF required')
    if ready:
        if c.get('printing_paused') or not d.last_seen or d.last_seen<hardware.now()-timedelta(seconds=60) or d.media!='RECEIPT' or d.mode_request or not (d.observation or {}).get('local_verified') or not (d.observation or {}).get('queues_empty'):
            raise HTTPException(409,'Pilot connector must be connected, attested, empty, unpaused and on receipt media')
        if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN'])).first():
            raise HTTPException(409,'Inspect existing printer work; pilot cannot bypass hardware fences')
        if not db.query(HardwareJob).filter_by(device_id=d.id,device_version=d.version,kind='TEST',status='CONFIRMED').first():
            raise HTTPException(409,'Physically confirmed current-device-version test required')
    return d


def configure(db,user,device_id,notes,confirm):
    require(user);pos.locked(db)
    if confirm is not True:raise HTTPException(422,'Explicit pilot scope approval required')
    notes=pos.inventory.text(notes,1000,True)
    old=scope(db)
    if old:
        if old.device_id!=device_id:raise HTTPException(409,'Pilot scope is immutable; use a separately reviewed release for another target')
        return old
    d=hardware.device(db,device_id);r=db.get(POSRegister,d.register_id)
    s=POSPilotScope(shop=d.shop,register_id=r.id,device_id=d.id,location_id=r.location_id,actor_id=user.id,notes=notes)
    target(db,s,True);domain_clear(db)
    db.add(s);pos.audit(db,user,d.id,'REAL_PILOT_SCOPE_APPROVED',register_id=r.id,location=r.location_id,notes=notes)
    db.commit();return s


def cart_hash(cart):return pos.digest({'session_id':cart.session_id,'version':cart.version,'payload':cart.payload,'quote':cart.quote})


def authorize(db,user,cid,version,key,notes,confirm):
    require(user);pos.locked(db)
    if confirm is not True:raise HTTPException(422,'Explicit genuine-sale pilot approval required before accepting payment')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',key):raise HTTPException(422,'Stable pilot approval key required')
    c=pos.get_cart(db,user,cid);s=scope(db)
    if not s:raise HTTPException(409,'Approve exact pilot shop/register/device first')
    old=db.get(POSPilotAuthorization,c.id)
    if old:
        if old.request_key!=key or old.cart_hash!=cart_hash(c):raise HTTPException(409,'Cart already has a different or stale pilot approval')
        return old
    if c.status!='DRAFT' or c.version!=version or not c.payload['lines']:raise HTTPException(409,'Current nonempty draft cart required')
    session=pos.session(db,user,c.session_id)
    if session.register_id!=s.register_id:raise HTTPException(403,'Cart outside approved pilot register')
    domain_clear(db);d=target(db,s,True)
    row=POSPilotAuthorization(cart_id=c.id,shop=s.shop,request_key=key,register_id=s.register_id,device_id=d.id,
        device_version=d.version,cart_hash=cart_hash(c),actor_id=user.id,notes=pos.inventory.text(notes,1000,True),expires_at=hardware.now()+timedelta(minutes=30))
    db.add(row);pos.audit(db,user,c.id,'REAL_PILOT_SALE_AUTHORIZED',device_id=d.id,version=d.version,cart_hash=row.cart_hash,notes=row.notes)
    db.commit();return row


def before_checkout(db,user,cart):
    row=db.get(POSPilotAuthorization,cart.id)
    if not row:return
    require(user)
    if row.actor_id!=user.id or row.cart_hash!=cart_hash(cart) or row.expires_at<hardware.now():raise HTTPException(409,'Pilot cart approval changed or expired; do not capture payment')
    domain_clear(db);s=scope(db);d=target(db,s,True)
    if d.version!=row.device_version:raise HTTPException(409,'Pilot device version changed')


def authorization(db,sale):return db.get(POSPilotAuthorization,sale.cart_id)


def dispatch_allowed(db,sale):
    a=authorization(db,sale)
    if not a:return True
    if not settings.POS_REAL_SALE_PILOT_ENABLED:return False
    # Revalidate original admin and scope, without weakening the normal queue fence.
    require(db.get(User,a.actor_id));s=scope(db);d=target(db,s)
    session=db.get(POSSession,sale.session_id)
    if sale.shop!=a.shop or not session or session.register_id!=a.register_id or sale.frozen['location']!=s.location_id:
        raise HTTPException(409,'Sale outside immutable pilot shop/register/location')
    return d.version==a.device_version


def reserve_receipt(db,sale,order):
    """Called inside publish_sale's transaction: financial commit + one job atomically.
    Printer/render problems reserve a FAILED job; they never undo verified money.
    """
    a=authorization(db,sale)
    if not a:return
    key='receipt:auto:'+sale.id
    if db.query(HardwareJob).filter_by(business_key=key).first():return
    user=db.get(User,a.actor_id);d=db.get(PrintDevice,a.device_id)
    try:
        with db.begin_nested():
            if not dispatch_allowed(db,sale):raise HTTPException(409,'Pilot release gate closed')
            # Fresh heartbeat/physical availability is not a condition for financial
            # completion: queue durably and let normal printer claims fence delivery.
            hardware.receipt_job(db,user,d.id,sale.id,'pilot','80',automatic=True,pilot_cart_id=a.cart_id)
            db.flush()
    except Exception:
        from uuid import uuid4
        import hashlib
        job=HardwareJob(id=str(uuid4()),shop=sale.shop,device_id=a.device_id,register_id=a.register_id,
            location_id=sale.frozen['location'],device_version=a.device_version,business_key=key,
            kind='RECEIPT',queue=hardware.RECEIPT_QUEUE,media='RECEIPT',actor_id=a.actor_id,
            session_id=sale.session_id,checkout_id=sale.id,status='FAILED',attempts=0,
            detail={'automatic':True,'profile':'80','pilot_cart_id':a.cart_id},
            sha256=hashlib.sha256(b'').hexdigest(),size=0,expires_at=hardware.now()+timedelta(hours=24),
            result={'code':'PILOT_RECEIPT_REQUIRES_REVIEW'})
        db.add(job);db.flush();hardware.event(db,d,'PILOT_RECEIPT_BLOCKED',user,j=job)
    else:
        job=db.query(HardwareJob).filter_by(business_key=key).one()
        hardware.event(db,d,'PILOT_RECEIPT_QUEUED',user,j=job)


def valid_job(db,job):
    a=db.get(POSPilotAuthorization,job.detail.get('pilot_cart_id'));sale=db.get(POSCheckout,job.checkout_id) if job.checkout_id else None
    op=db.query(POSOperation).filter_by(checkout_id=sale.id,kind='CREATE',status='VERIFIED').one_or_none() if sale else None
    posting=db.query(POSPosting).filter_by(reference='POS-sale:'+sale.id).one_or_none() if sale else None
    order=db.get(SalesOrder,sale.sales_order_id) if sale else None
    return bool(a and sale and op and posting and order and order.shopify_order_id==(op.outcome or {}).get('remote_id') and a.cart_id==sale.cart_id and a.shop==job.shop and
        a.register_id==job.register_id and a.device_id==job.device_id and a.device_version==job.device_version and
        job.kind=='RECEIPT' and job.business_key=='receipt:auto:'+sale.id)


def sale_status(db,sale):
    a=authorization(db,sale)
    if not a:return None
    op=db.query(POSOperation).filter_by(checkout_id=sale.id,kind='CREATE').one_or_none()
    phase={'AWAITING_PAYMENT':'PAYMENT_PENDING','PAYMENT_READY':'PAYMENT_VERIFIED','QUEUED':'SHOPIFY_CREATE_PENDING','COMPLETE':'COMPLETE','CANCELLED':'FAILED'}.get(sale.status,'REVIEW')
    if sale.status in {'REVIEW','UNCERTAIN'} and op and (op.outcome or {}).get('remote_id'):
        last=db.query(POSAudit).filter_by(reference=op.id,action='READBACK_REVIEW').order_by(POSAudit.id.desc()).first()
        phase='ACCOUNTING_PENDING' if last and (last.detail or {}).get('stage') in {'LOCAL_POSTING','LOCAL_FLUSH'} else 'SHOPIFY_READBACK_PENDING'
    job=db.query(HardwareJob).filter_by(business_key='receipt:auto:'+sale.id).one_or_none()
    d=db.get(PrintDevice,a.device_id)
    posting=db.query(POSPosting).filter_by(reference='POS-sale:'+sale.id).one_or_none()
    connected=bool(d.last_seen and d.last_seen>hardware.now()-timedelta(seconds=60))
    observation=d.observation or {}
    queue=next((q for q in observation.get('queues',[]) if q.get('name')==hardware.RECEIPT_QUEUE),{})
    printer_ready=bool(connected and d.status=='ACTIVE' and d.media=='RECEIPT' and not d.mode_request and observation.get('local_verified') and queue.get('state')=='READY' and queue.get('jobs')==0)
    return {'phase':phase,'device_id':a.device_id,'accounting_verified':bool(sale.status=='COMPLETE' and posting),
        'shopify_verified':bool(op and op.status=='VERIFIED'),'receipt':hardware.public_job(job) if job else None,
        'printer_paused':bool(d.config.get('printing_paused')),'printer_connected':connected,'printer_ready':printer_ready,
        'physical_output_verified':bool(job and (job.result or {}).get('physical_output_verified'))}
