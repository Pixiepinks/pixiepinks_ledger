"""One identity, one durable intent, explicit authenticated admin approval.

No worker dispatch path, generic target arguments, flag override or ordinary
source-gate override. PostgreSQL shop/user/variant locks fence local writers
across independently committed intent records and the bounded remote call.
"""
from contextlib import contextmanager
from datetime import datetime,timedelta
import hashlib
import secrets
import time
from fastapi import HTTPException
from sqlalchemy import select,or_,text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from models import User
from retail_models import RetailShop,RetailVariant,RetailIdentifier,RetailBarcodeJob,RetailBarcodeItem,RetailBarcodeReservation
from settings import settings
import retail_service as service
import retail_barcode_sync as workflow
import retail_barcode_contract as contract
from retail_barcode_shopify import RemoteError
from retail_barcode_p1_remote import P1Client,IDENTITY,exact_identity,fingerprint

TOKEN=hashlib.sha256(b'pixiepinks-one-time-P1-47281619206447-v2').hexdigest()[:32]
KEY=hashlib.sha256(b'pixiepinks-one-time-P1-47281619206447-v2-attempt').hexdigest()
CONFIRMATION='WRITE P1 ONLY TO eb2b70.myshopify.com PRODUCT 9055242223919 VARIANT 47281619206447 SKU 1001200 ALLOCATION 1 VERSION 2'
_PERMIT_KEY=object()


class _Permit:
    def __init__(self,key,deadline):
        if key is not _PERMIT_KEY:raise RemoteError('P1_ATTEMPT_NOT_AUTHORIZED')
        self.used=False;self.expires=min(deadline,time.monotonic()+5)
    def live(self):return time.monotonic()<=self.expires
    def consume(self):
        if self.used or not self.live():return False
        self.used=True;return True


def guards():
    if contract.MUTATION_SEMANTICS_VERIFIED is not False or settings.SHOPIFY_PRODUCT_WRITE_ENABLED or settings.RETAIL_BARCODE_SYNC_ENABLED:
        raise HTTPException(409,'P1_GLOBAL_GUARDS_MUST_REMAIN_DISABLED')
    if service.shop()!=IDENTITY['shop']:raise HTTPException(409,'P1_SHOP_MISMATCH')


def authorize(user):
    workflow.authorize(user)
    if user.role!='ADMIN':raise HTTPException(403,'P1 requires an active administrator')


@contextmanager
def locked(engine,actor_id):
    if engine.dialect.name!='postgresql':raise HTTPException(409,'P1_REQUIRES_POSTGRESQL')
    sessions=sessionmaker(bind=engine,expire_on_commit=False)
    with sessions() as db:
        try:
            guards()
            if db.connection().get_isolation_level()!='READ COMMITTED':raise HTTPException(409,'P1_REQUIRES_READ_COMMITTED')
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Existing row only: no startup migration or first-use row insertion.
            if not db.get(RetailShop,IDENTITY['shop']):raise HTTPException(409,'P1_SHOP_RECORD_UNAVAILABLE')
            service.write_lock(db)
            user=db.execute(select(User).where(User.id==actor_id).with_for_update(read=True)).scalar_one_or_none()
            authorize(user)
            yield db,sessions,user
        except SQLAlchemyError:
            raise HTTPException(409,'P1_DATABASE_LOCK_OR_AUDIT_UNAVAILABLE') from None
        finally:db.rollback()  # Lock-holder itself never commits or mutates rows.


def local(db):
    ledger=db.execute(select(RetailIdentifier).where(RetailIdentifier.id==1).with_for_update(read=True)).scalar_one_or_none()
    row=db.execute(select(RetailVariant).where(RetailVariant.id==ledger.variant_id).with_for_update(read=True)).scalar_one_or_none() if ledger else None
    if not row or ledger.kind!='barcode' or ledger.shop!=IDENTITY['shop'] or ledger.value!='P1':raise HTTPException(409,'P1_ALLOCATION_CHANGED')
    snap=workflow.snapshot(row,ledger)
    if not exact_identity(snap) or workflow.local_reason(db,row):raise HTTPException(409,'P1_ALLOCATION_CHANGED_OR_INELIGIBLE')
    return snap


def quiet(db,own=None):
    # Include uncertain historical items even if their parent says COMPLETE.
    jobs=db.query(RetailBarcodeJob).filter(~RetailBarcodeJob.status.in_(['PREVIEW','COMPLETE','CANCELED','PILOT_PREPARED','PILOT_VERIFIED']))
    items=db.query(RetailBarcodeItem).filter(or_(RetailBarcodeItem.status.in_(['QUEUED','CHECKING','SENT','UNKNOWN','CONFLICT']),
        (RetailBarcodeItem.sent_at!=None)&(~RetailBarcodeItem.status.in_(['VERIFIED','PILOT_VERIFIED']))))
    reservations=db.query(RetailBarcodeReservation)
    if own is not None:
        jobs=jobs.filter(RetailBarcodeJob.id!=own);items=items.filter(RetailBarcodeItem.job_id!=own)
        reservations=reservations.join(RetailBarcodeItem).filter(RetailBarcodeItem.job_id!=own)
    if jobs.first() or items.first() or reservations.first():raise HTTPException(409,'P1_COMPETING_OR_UNCERTAIN_BARCODE_WORK')


def records(db):
    job=db.query(RetailBarcodeJob).filter_by(token=TOKEN).one_or_none()
    item=db.query(RetailBarcodeItem).filter_by(job_id=job.id).one() if job else None
    if job and (job.shop!=IDENTITY['shop'] or item.idempotency_key!=KEY or not exact_identity(item.snapshot)):
        raise HTTPException(409,'P1_DURABLE_IDENTITY_MISMATCH')
    return job,item


def digest(job,item):
    return fingerprint({'snapshot':item.snapshot,'before':item.before,'actor':job.actor_id,'expires':job.expires_at.isoformat()})


def public(job,item):
    return {'job_id':job.id,'status':job.status,'identity':dict(IDENTITY),'attempts':item.attempts,
        'reason':item.reason,'expires_at':job.expires_at.isoformat()+'Z',
        'ordinary_sync_enabled':False,'write_authorized':False,'confirmation':CONFIRMATION,
        'protected_before_sha256':fingerprint(item.before['protected']),
        'after':item.after,'verified_at':item.verified_at.isoformat()+'Z' if item.verified_at else None}


def remote_state(remote,snap,readback=False):
    cap=remote.capabilities()
    if not all(cap.get(k) is True for k in ['shop_identity_verified','read_supported','read_products_scope_observed','write_products_scope_observed']) or cap.get('version')!='2026-10':
        raise RemoteError('P1_SCOPE_SCHEMA_OR_SHOP_UNVERIFIED')
    state=remote.inspect(snap)
    if state.get('complete') is not True or state.get('collision_complete') is not True or state.get('write_scope_confirmed') is not True or state.get('version')!='2026-10' or state.get('variant_id')!=IDENTITY['variant_id'] or state.get('product_id')!=IDENTITY['product_id'] or state.get('status')!='ACTIVE':
        raise RemoteError('P1_COMPLETE_STATE_UNVERIFIED')
    expect=[{'value':'P1','type':None}] if readback else []
    collisions=[IDENTITY['variant_id']] if readback else []
    if state.get('barcodes')!=expect or state.get('codes')!=(['P1'] if readback else []) or state.get('collision_ids')!=collisions or state.get('legacy_barcode') not in ({None,'','P1'} if readback else {None,''}):
        raise RemoteError('P1_READBACK_MISMATCH' if readback else 'P1_TARGET_NONEMPTY_OR_COLLISION')
    return state


def final_target(remote,readback=False):
    value=remote._barcode_pages(IDENTITY['variant_id'],[0,time.monotonic()+15])
    if value.get('variant_id')!=IDENTITY['variant_id'] or value.get('product_id')!=IDENTITY['product_id'] or value.get('status')!='ACTIVE' or value.get('codes')!=(['P1'] if readback else []) or value.get('barcodes')!=([{'value':'P1','type':None}] if readback else []) or value.get('legacy_barcode') not in ({None,'','P1'} if readback else {None,''}):
        raise RemoteError('P1_FINAL_TARGET_CHANGED')


def prepare(engine,actor_id,*,editors_paused,risk_accepted,remote=None):
    if editors_paused is not True or risk_accepted is not True:raise HTTPException(422,'P1_PAUSE_AND_NONATOMIC_RISK_ATTESTATION_REQUIRED')
    remote=remote or P1Client()
    with locked(engine,actor_id) as (db,sessions,user):
        snap=local(db);job,item=records(db);quiet(db,job.id if job else None)
        if job and (job.status!='PILOT_PREPARED' or item.attempts or job.actor_id!=user.id):raise HTTPException(409,'P1_ALREADY_CONSUMED_OR_OTHER_APPROVER')
        try:
            protected=remote.protected();state=remote_state(remote,snap)
            if remote.protected()!=protected:raise RemoteError('P1_PROTECTED_CHANGED')
            final_target(remote)
        except Exception:raise HTTPException(409,'P1_PREPARATION_REMOTE_VERIFICATION_FAILED') from None
        if local(db)!=snap:raise HTTPException(409,'P1_ALLOCATION_CHANGED')
        nonce=secrets.token_hex(32)
        before={'protected':protected,'remote':state,'nonce_hash':hashlib.sha256(nonce.encode()).hexdigest(),
            'editors_paused':True,'risk_accepted':True}
        with sessions() as audit:
            j,i=records(audit)
            if not j:
                j=RetailBarcodeJob(shop=IDENTITY['shop'],actor_id=user.id,actor=user.username,token=TOKEN,status='PILOT_PREPARED',api_version='2026-10',digest='',expires_at=datetime.utcnow()+timedelta(minutes=10))
                audit.add(j);audit.flush()
                i=RetailBarcodeItem(job_id=j.id,variant_id=snap['id'],idempotency_key=KEY,snapshot=snap,before=before,status='PILOT_PREPARED',reason='P1_REVIEW_REQUIRED')
                audit.add(i)
            else:i.before=before;i.snapshot=snap;j.expires_at=datetime.utcnow()+timedelta(minutes=10)
            j.digest=digest(j,i);audit.flush();workflow.event(audit,j,'P1_PREPARED',i,protected_sha256=fingerprint(protected),identity=dict(IDENTITY),before=before,digest=j.digest)
            result=public(j,i);result['nonce']=nonce;audit.commit();return result


def mark(sessions,job_id,status,reason,*,intent=False,approved=False,after=None,actor=None):
    with sessions() as db:
        job=db.execute(select(RetailBarcodeJob).where(RetailBarcodeJob.id==job_id).with_for_update()).scalar_one()
        item=db.query(RetailBarcodeItem).filter_by(job_id=job.id).one()
        if intent and (item.attempts or item.sent_at):raise HTTPException(409,'P1_ATTEMPT_ALREADY_CONSUMED')
        job.status=item.status=status;item.reason=reason;item.checked_at=datetime.utcnow()
        if approved:
            job.approved_at=datetime.utcnow()
            db.add(RetailBarcodeReservation(shop=job.shop,variant_id=item.variant_id,value='P1',item_id=item.id))
        if intent:item.attempts=1;item.sent_at=datetime.utcnow()
        if after is not None:item.after=after
        elif status=='PILOT_UNKNOWN':item.after={'verification_complete':False}
        if status!='PILOT_VERIFIED':item.verified_at=None;job.completed_at=None
        if status=='PILOT_VERIFIED':
            item.verified_at=datetime.utcnow();job.completed_at=item.verified_at
            workflow.release(db,job,item)
        workflow.event(db,job,status,item,reason=reason,operator_id=actor,digest=job.digest,after=after,confirmation=CONFIRMATION if approved else None,
            editors_paused=True if approved else None,risk_accepted=True if approved else None)
        result=public(job,item);db.commit();return result


def reconcile_locked(db,sessions,job,item,remote,actor):
    # No path from reconciliation to _attempt_p1, even when remote is empty.
    if item.attempts!=1 or not item.sent_at:raise HTTPException(409,'P1_NO_MUTATION_INTENT_TO_RECONCILE')
    try:
        if local(db)!=item.snapshot:raise RemoteError('P1_LOCAL_CHANGED')
        state=remote_state(remote,item.snapshot,readback=True)
        protected=remote.protected();final_target(remote,readback=True)
        if protected!=item.before['protected']:raise RemoteError('P1_PROTECTED_CHANGED')
        after={'remote':state,'protected_sha256':fingerprint(protected),'protected_unchanged':True}
        return mark(sessions,job.id,'PILOT_VERIFIED','P1_FULL_READBACK_AND_PROTECTED_MATCH',after=after,actor=actor)
    except Exception:
        return mark(sessions,job.id,'PILOT_UNKNOWN','P1_READBACK_OR_PROTECTED_UNCONFIRMED',actor=actor)


def execute(engine,actor_id,*,nonce,confirmation,editors_paused,risk_accepted,remote=None):
    if editors_paused is not True or risk_accepted is not True or confirmation!=CONFIRMATION or not isinstance(nonce,str) or len(nonce)!=64:
        raise HTTPException(422,'P1_EXACT_CONFIRMATION_REQUIRED')
    remote=remote or P1Client()
    with locked(engine,actor_id) as (db,sessions,user):
        job,item=records(db)
        if not job or job.actor_id!=user.id or job.status!='PILOT_PREPARED' or item.attempts or item.sent_at:
            raise HTTPException(409,'P1_NOT_AWAITING_THIS_ADMIN_APPROVAL')
        if job.expires_at<datetime.utcnow() or job.digest!=digest(job,item) or not secrets.compare_digest(item.before['nonce_hash'],hashlib.sha256(nonce.encode()).hexdigest()):
            raise HTTPException(409,'P1_APPROVAL_EXPIRED_OR_CHANGED')
        quiet(db,job.id)
        if local(db)!=item.snapshot:raise HTTPException(409,'P1_ALLOCATION_CHANGED')
        mark(sessions,job.id,'PILOT_APPROVED','P1_EXPLICIT_OPERATOR_APPROVAL',approved=True,actor=user.id)
        try:
            if remote.protected()!=item.before['protected']:raise RemoteError('P1_PROTECTED_CHANGED')
            remote_state(remote,item.snapshot)  # New complete collision scan, never earlier evidence.
            if remote.protected()!=item.before['protected']:raise RemoteError('P1_PROTECTED_CHANGED')
            final_target(remote);fresh_at=time.monotonic()
            guards();quiet(db,job.id)
            db.expire_all()
            if local(db)!=item.snapshot or job.expires_at<datetime.utcnow() or time.monotonic()-fresh_at>5:raise RemoteError('P1_APPROVAL_OR_ALLOCATION_CHANGED')
        except Exception:
            return mark(sessions,job.id,'PILOT_BLOCKED','P1_FRESH_PREFLIGHT_FAILED_NO_MUTATION',actor=user.id)
        # Separate connection commits intent while the shop/user/variant locks
        # remain held here. Any restart sees consumed intent and ONLY reconciles.
        mark(sessions,job.id,'PILOT_INTENT','P1_SINGLE_ATTEMPT_CONSUMED',intent=True,actor=user.id)
        try:
            db.execute(text('SELECT 1'));guards()
            if job.expires_at<datetime.utcnow() or time.monotonic()-fresh_at>5:raise RemoteError('P1_FINAL_CHECK_EXPIRED')
            remote._attempt_p1(_Permit(_PERMIT_KEY,fresh_at+5))
        except Exception:
            mark(sessions,job.id,'PILOT_UNKNOWN','P1_MUTATION_RESPONSE_UNCERTAIN',actor=user.id)
        db.expire_all();job,item=records(db)
        return reconcile_locked(db,sessions,job,item,remote,user.id)


def reconcile(engine,actor_id,remote=None):
    with locked(engine,actor_id) as (db,sessions,user):
        job,item=records(db)
        if not job:raise HTTPException(404,'P1_PILOT_NOT_PREPARED')
        quiet(db,job.id)
        return reconcile_locked(db,sessions,job,item,remote or P1Client(),user.id)


def status(db,user):
    guards();authorize(user);job,item=records(db)
    return public(job,item) if job else {'status':'NOT_PREPARED','identity':dict(IDENTITY),'write_authorized':False}
