"""Bounded, explicitly approved barcode jobs with durable no-blind-resend recovery."""
import csv
import hashlib
import io
import json
import logging
import re
import secrets
from collections import Counter
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import select,and_
from models import User
from retail_models import *
from settings import settings
import retail_service as service
from retail_barcode_shopify import client,RemoteError
from retail_barcode_contract import RELEASE_ID

log=logging.getLogger(__name__)
PERMISSION='retail.barcodes.shopify_sync'
PENDING={'QUEUED','CHECKING','SENT'}
RELEASE=RELEASE_ID
RETIRE_PHRASE='RETIRE LEGACY BARCODE JOBS WITHOUT WRITING SHOPIFY'


def authorize(user):
    service.require(user,PERMISSION)


def flags():
    return settings.RETAIL_ENABLED and settings.SHOPIFY_PRODUCT_WRITE_ENABLED and settings.RETAIL_BARCODE_SYNC_ENABLED


def capability(remote=None):
    try:return (remote or client).capabilities()
    except Exception:return {'supported':False,'scope':False,'version':'','reason':'REMOTE_CAPABILITY_UNAVAILABLE'}


def event(db,job,action,item=None,**detail):
    db.add(RetailBarcodeEvent(shop=job.shop,job_id=job.id,item_id=item.id if item else None,
        actor=job.actor,action=action,detail=detail))
    log.info('retail_barcode_event job=%s item=%s action=%s',job.id,item.id if item else None,action)


def snapshot(row,ledger):
    return {'id':row.id,'version':row.version,'shop':row.shop,'product_id':row.product_id,
        'variant_id':row.variant_id,'title':row.title,'variant':row.variant_title,
        'sku':row.sku or '', 'code':row.local_barcode or '', 'ledger_id':ledger.id if ledger else None}


def local_reason(db,row,expected=None):
    if row is None or row.shop!=service.shop():return 'VARIANT_UNAVAILABLE'
    if not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',row.variant_id) or not re.fullmatch(r'gid://shopify/Product/[0-9]+',row.product_id):return 'INVALID_IDENTITY'
    ledger=db.query(RetailIdentifier).filter_by(shop=row.shop,variant_id=row.id,kind='barcode',value=row.local_barcode).one_or_none() if row.local_barcode else None
    if not ledger or not service.code_valid(row.local_barcode):return 'NO_COMMITTED_LOCAL_BARCODE'
    if expected is not None and snapshot(row,ledger)!=expected:return 'LOCAL_SNAPSHOT_CHANGED'
    if row.missing or row.product_status!='ACTIVE':return 'INACTIVE_OR_REMOVED'
    if row.stale or row.synced_at<datetime.utcnow()-timedelta(days=7):return 'STALE_IMPORT'
    if row.barcode:return 'IMPORTED_BARCODE_NONEMPTY'
    # Duplicate SKU is an independent review issue, never a barcode collision.
    ctx=service.audit_context(db)
    if row.sku and ctx['sku'][row.sku]>1 and ('sku',row.sku) not in ctx['shared']:return 'DUPLICATE_SKU_NEEDS_REVIEW'
    if db.query(RetailVariant.id).filter(RetailVariant.shop==row.shop,RetailVariant.id!=row.id,
        service.or_(RetailVariant.barcode==row.local_barcode,RetailVariant.local_barcode==row.local_barcode)).first():return 'DUPLICATE_BARCODE'
    return ''


def inspect(remote,snap):
    value=remote.inspect(snap)
    # Only bounded, typed fields can enter durable records; never persist API errors.
    if not isinstance(value,dict):raise RemoteError('INVALID_REMOTE_STATE')
    codes=value.get('codes')
    if not isinstance(codes,list) or len(codes)>10000 or any(not isinstance(c,str) or len(c)>500 for c in codes):raise RemoteError('INVALID_REMOTE_STATE')
    return {key:value.get(key) for key in ['complete','collision_complete','collision_ids','version','variant_id','product_id','status','codes','legacy_barcode','barcodes','readback_untyped_required','untyped_local_code']}


def remote_reason(state,snap,cap,readback=False):
    if not cap.get('supported'):return 'UNSUPPORTED_BARCODE_SCHEMA'
    if not cap.get('scope'):return 'WRITE_PRODUCTS_SCOPE_UNVERIFIED'
    if state.get('complete') is not True or state.get('collision_complete') is not True:return 'AMBIGUOUS_REMOTE_BARCODES'
    if state.get('version')!=cap.get('version') or not state.get('version'):return 'API_VERSION_CHANGED'
    if state.get('variant_id')!=snap['variant_id'] or state.get('product_id')!=snap['product_id']:return 'REMOTE_IDENTITY_CHANGED'
    if state.get('status')!='ACTIVE':return 'REMOTE_INACTIVE_OR_REMOVED'
    collisions=state.get('collision_ids')
    if not isinstance(collisions,list) or any(not isinstance(v,str) for v in collisions):return 'REMOTE_COLLISIONS_UNVERIFIED'
    if any(v!=snap['variant_id'] for v in collisions):return 'REMOTE_BARCODE_REUSED'
    if readback:
        if state['codes']==[snap['code']] and (state.get('barcodes')!=[{'value':snap['code'],'type':None}] or state.get('untyped_local_code') is not True):return 'READBACK_TYPE_MISMATCH'
        return '' if state['codes']==[snap['code']] and state.get('legacy_barcode') in {None,'',snap['code']} else 'READBACK_MISMATCH'
    if state['codes'] or state.get('legacy_barcode') not in {None,''}:return 'REMOTE_BARCODE_NONEMPTY'
    return ''


def digest(job,items):
    value={'shop':job.shop,'actor':job.actor_id,'api':job.api_version,
        'release':job.release_version,'items':[{'snapshot':i.snapshot,'before':i.before,'key':i.idempotency_key} for i in items]}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def phrase(job,count):return f'WRITE {count} BARCODES TO EMPTY FIELDS IN {job.shop} BATCH {job.id}'


def items_for(db,job):return db.query(RetailBarcodeItem).filter_by(job_id=job.id).order_by(RetailBarcodeItem.id).all()


def get_job(db,user,rid):
    authorize(user)
    job=db.query(RetailBarcodeJob).filter_by(id=rid,shop=service.shop()).one_or_none()
    if not job:raise HTTPException(404,'Barcode sync batch not found')
    return job


def normal_local_reason(db,row,expected=None):
    if row and row.shop=='eb2b70' and (row.local_barcode=='P1' or row.variant_id=='gid://shopify/ProductVariant/47281619206447'):
        return 'COMPLETED_P1_PILOT_CANNOT_REPLAY'
    return local_reason(db,row,expected)


def historical_blockers(db):
    resolved_empty=and_(RetailBarcodeItem.status=='BLOCKED',RetailBarcodeItem.reason=='LEGACY_READBACK_RETIRED_NO_RESEND')
    unresolved=select(RetailBarcodeItem.id).where(RetailBarcodeItem.job_id==RetailBarcodeJob.id,~resolved_empty,
        service.or_(RetailBarcodeItem.status.in_(['SENT','UNKNOWN','CONFLICT','PILOT_UNKNOWN','PILOT_INTENT']),
            (RetailBarcodeItem.sent_at!=None)&(~RetailBarcodeItem.status.in_(['VERIFIED','PILOT_VERIFIED'])))).exists()
    jobs=db.query(RetailBarcodeJob.id).filter(RetailBarcodeJob.shop==service.shop(),RetailBarcodeJob.release_version==None,
        service.or_(~RetailBarcodeJob.status.in_(['COMPLETE','CANCELED','LEGACY_RETIRED','PILOT_VERIFIED']),unresolved)).count()
    return jobs+legacy_reservations(db).count()


def legacy_reservations(db):
    return db.query(RetailBarcodeReservation).join(RetailBarcodeItem).join(RetailBarcodeJob).filter(
        RetailBarcodeJob.shop==service.shop(),RetailBarcodeJob.release_version==None)


def legacy_resolved(item):
    return item.status in {'VERIFIED','PILOT_VERIFIED'} or (item.status=='BLOCKED' and item.reason=='LEGACY_READBACK_RETIRED_NO_RESEND')


def retire_legacy(db,user,confirmation):
    authorize(user)
    if user.role!='ADMIN':raise HTTPException(403,'Administrator approval required')
    if confirmation!=RETIRE_PHRASE:raise HTTPException(422,'Type the exact legacy retirement confirmation')
    service.write_lock(db)
    jobs=db.query(RetailBarcodeJob).filter_by(shop=service.shop(),release_version=None).all()
    count=0
    for job in jobs:
        for checked in items_for(db,job):
            if legacy_resolved(checked) or (not checked.sent_at and checked.status=='BLOCKED'):
                reservation=db.get(RetailBarcodeReservation,(job.shop,checked.variant_id))
                if reservation and reservation.item_id==checked.id:
                    release(db,job,checked);event(db,job,'LEGACY_RESERVATION_RELEASED',checked,operator=user.username)
        if job.status in {'PILOT_VERIFIED','COMPLETE','CANCELED','LEGACY_RETIRED'} and not any(not legacy_resolved(i) and (i.status in {'SENT','UNKNOWN','CONFLICT','PILOT_UNKNOWN','PILOT_INTENT'} or i.sent_at) for i in items_for(db,job)):continue
        uncertain=False
        for item in items_for(db,job):
            if legacy_resolved(item):continue
            if item.sent_at or item.status in {'SENT','UNKNOWN','PILOT_UNKNOWN','PILOT_INTENT'}:
                uncertain=True  # Preserve attempt evidence/reservation; NEVER reset it.
            else:
                item.status='BLOCKED';item.reason='LEGACY_APPROVAL_RETIRED';release(db,job,item)
        old=job.status
        job.status='LEGACY_REVIEW' if uncertain else 'LEGACY_RETIRED'
        event(db,job,'LEGACY_RELEASE_RETIRED',before_status=old,operator=user.username,read_only_review=uncertain)
        count+=1
    return {'retired':count,'remaining_historical_blockers':historical_blockers(db),'shopify_write':False}


def preview(db,user,token,remote=None):
    # Deliberately no network calls in the request, including capability probes.
    authorize(user);service.write_lock(db)
    recent=db.query(RetailBarcodeJob).filter(RetailBarcodeJob.shop==service.shop(),RetailBarcodeJob.actor_id==user.id,
        RetailBarcodeJob.created_at>datetime.utcnow()-timedelta(minutes=1)).count()
    if recent>=5:raise HTTPException(429,'Wait one minute before creating another barcode preview')
    selected=service.preview_record(db,user,token,'SELECTION')
    if selected.payload.get('barcode_release')!=RELEASE:raise HTTPException(409,'Select products again for this barcode release')
    ids=selected.payload['ids']
    if not 1<=len(ids)<=min(5,settings.RETAIL_BARCODE_SYNC_BATCH_LIMIT):raise HTTPException(422,'Select 1–5 variants for a reviewed batch')
    snapshots=[]
    for rid in ids:
        row=db.query(RetailVariant).filter_by(id=rid,shop=service.shop()).one_or_none()
        if not row:raise HTTPException(409,'Selected catalog record is unavailable')
        ledger=db.query(RetailIdentifier).filter_by(shop=row.shop,variant_id=row.id,kind='barcode',value=row.local_barcode).one_or_none()
        snapshots.append(snapshot(row,ledger))
    job=RetailBarcodeJob(shop=service.shop(),actor_id=user.id,actor=user.username,token=secrets.token_hex(16),
        status='PREVIEW_PENDING',api_version='2026-10',digest='',release_version=RELEASE,preview_selection=snapshots,
        expires_at=datetime.utcnow()+timedelta(hours=1))
    db.add(job);db.flush();event(db,job,'BACKGROUND_PREVIEW_QUEUED',count=len(snapshots),release=RELEASE)
    return report(db,job)


def check_preview_one(sessions,remote=None):
    remote=remote or client
    if not settings.RETAIL_ENABLED:return False
    with sessions() as db:
        service.write_lock(db);now=datetime.utcnow()
        job=db.execute(select(RetailBarcodeJob).where(RetailBarcodeJob.shop==service.shop(),
            RetailBarcodeJob.release_version==RELEASE,RetailBarcodeJob.status.in_(['PREVIEW_PENDING','PREVIEW_CHECKING']),
            service.or_(RetailBarcodeJob.preview_lease==None,RetailBarcodeJob.preview_lease<now))
            .order_by(RetailBarcodeJob.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if not job:return False
        if job.expires_at<now:
            job.status='CANCELED';event(db,job,'BACKGROUND_PREVIEW_EXPIRED');db.commit();return True
        index=len(items_for(db,job));snap=job.preview_selection[index];jid=job.id
        claim=secrets.token_hex(16);job.preview_claim=claim;job.preview_lease=now+timedelta(minutes=5);job.status='PREVIEW_CHECKING'
        reason=normal_local_reason(db,db.get(RetailVariant,snap['id']),snap);db.commit()
    before={};cap={};checked=None
    if not reason:
        try:
            cap=capability(remote)
            if not cap.get('supported') or not cap.get('scope') or cap.get('version')!='2026-10':reason='SCOPE_OR_SCHEMA_UNVERIFIED'
            else:
                protected=remote.protected(snap);before=inspect(remote,snap);checked=datetime.utcnow();reason=remote_reason(before,snap,cap)
                if remote.protected(snap)!=protected:reason='PROTECTED_ATTRIBUTES_CHANGED'
                before['protected']=protected
        except Exception:reason='REMOTE_STATE_UNAVAILABLE'
    with sessions() as db:
        service.write_lock(db);job=db.get(RetailBarcodeJob,jid)
        if job.preview_claim!=claim or job.status!='PREVIEW_CHECKING' or job.preview_lease<datetime.utcnow():return True
        current=normal_local_reason(db,db.get(RetailVariant,snap['id']),snap)
        if current:reason=current
        owner=db.get(User,job.actor_id)
        try:authorize(owner)
        except HTTPException:reason='APPROVER_ACCESS_REVOKED'
        if not reason and db.get(RetailBarcodeReservation,(job.shop,snap['id'])):reason='ALREADY_RESERVED'
        item=RetailBarcodeItem(job_id=job.id,variant_id=snap['id'],idempotency_key=hashlib.sha256((job.token+str(snap['id'])).encode()).hexdigest(),
            snapshot=snap,before=before,status='BLOCKED' if reason else 'ELIGIBLE',reason=reason or 'EMPTY_REMOTE_CONFIRMED',checked_at=checked)
        db.add(item);db.flush();job.preview_capability=cap or job.preview_capability;job.preview_claim=None;job.preview_lease=None
        items=items_for(db,job)
        if len(items)==len(job.preview_selection):
            job.status='PREVIEW';job.expires_at=datetime.utcnow()+timedelta(minutes=10);job.digest=digest(job,items)
        else:job.status='PREVIEW_PENDING'
        event(db,job,'BACKGROUND_ROW_CHECKED',item,checked=len(items),total=len(job.preview_selection));db.commit()
    return True


def approve(db,user,rid,token,confirmation,remote=None):
    authorize(user);service.write_lock(db);job=get_job(db,user,rid)
    if user.role!='ADMIN':raise HTTPException(403,'Administrator approval required')
    if job.release_version!=RELEASE:raise HTTPException(409,'Historical approval is fenced; create a fresh selection and preview')
    if historical_blockers(db):raise HTTPException(409,'Review and retire historical barcode work before activation')
    if job.actor_id!=user.id or not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token) or not secrets.compare_digest(job.token,token):raise HTTPException(403,'Approval belongs to the reviewing staff member')
    items=items_for(db,job)
    if job.status!='PREVIEW':
        if job.approved_at:return report(db,job)  # Idempotent repeat, never dispatch twice.
        raise HTTPException(409,'Batch is no longer awaiting approval')
    if job.expires_at<datetime.utcnow():raise HTTPException(409,'Preview expired; select and review again')
    if job.digest!=digest(job,items):raise HTTPException(409,'Reviewed snapshot changed')
    eligible=[i for i in items if i.status=='ELIGIBLE']
    if not eligible:raise HTTPException(409,'No eligible barcode assignments in this batch')
    if confirmation!=phrase(job,len(eligible)):raise HTTPException(422,'Type the exact batch confirmation')
    if not flags():raise HTTPException(409,'Both Shopify product writes and retail barcode sync must be enabled')
    cap=capability(remote) if remote is not None else (job.preview_capability or {})
    if not cap.get('supported') or not cap.get('scope') or cap.get('version')!=job.api_version:raise HTTPException(409,'Shopify scope / complete barcode schema is unverified or changed')
    if db.query(RetailBarcodeJob).filter(RetailBarcodeJob.shop==job.shop,RetailBarcodeJob.status.in_(['QUEUED','RUNNING'])).count()>=10:raise HTTPException(429,'Shop queue is full')
    for item in eligible:
        row=db.get(RetailVariant,item.variant_id);reason=normal_local_reason(db,row,item.snapshot)
        if reason:raise HTTPException(409,reason+'; preview again')
        if db.get(RetailBarcodeReservation,(job.shop,item.variant_id)):raise HTTPException(409,'Variant is already reserved by another batch')
        if db.query(RetailBarcodeReservation).filter_by(shop=job.shop,value=item.snapshot['code']).first():raise HTTPException(409,'Barcode already reserved')
        db.add(RetailBarcodeReservation(shop=job.shop,variant_id=item.variant_id,value=item.snapshot['code'],item_id=item.id))
        item.status='QUEUED';event(db,job,'ITEM_QUEUED',item)
    job.status='QUEUED';job.approved_at=datetime.utcnow();job.expires_at=datetime.utcnow()+timedelta(minutes=30);event(db,job,'BATCH_APPROVED',count=len(eligible),digest=job.digest)
    db.flush();return report(db,job,cap)


def cancel(db,user,rid):
    authorize(user);service.write_lock(db);job=get_job(db,user,rid)
    if job.status=='CANCELED':return report(db,job)
    items=items_for(db,job)
    if job.status not in {'PREVIEW','PREVIEW_PENDING','PREVIEW_CHECKING','QUEUED','RUNNING'} or any(i.status in {'CHECKING','SENT'} or i.sent_at for i in items):raise HTTPException(409,'Dispatch began; reconcile outcomes instead of canceling or rolling back')
    for item in items:
        if item.status in {'ELIGIBLE','QUEUED'}:item.status='BLOCKED';item.reason='CANCELED_BEFORE_DISPATCH';release(db,job,item)
    job.status='CANCELED';job.canceled_at=datetime.utcnow();job.completed_at=job.canceled_at;event(db,job,'BATCH_CANCELED')
    return report(db,job)


def release(db,job,item):
    reservation=db.get(RetailBarcodeReservation,(job.shop,item.variant_id))
    if reservation and reservation.item_id==item.id:db.delete(reservation)


def report(db,job,cap=None,viewer=None):
    items=items_for(db,job);counts=dict(Counter(i.status for i in items));cap=cap or job.preview_capability or {'supported':False,'scope':False}
    owner=db.get(User,job.actor_id)
    return {'id':job.id,'shop':job.shop,'actor':job.actor,'status':job.status,'api_version':job.api_version,
        'created_at':job.created_at.isoformat()+'Z','expires_at':job.expires_at.isoformat()+'Z',
        'counts':counts,'release_version':job.release_version,'historical_fenced':job.release_version!=RELEASE,
        'progress':{'checked':len(items),'total':len(job.preview_selection or items)},'confirmation':phrase(job,counts.get('ELIGIBLE',0)),
        'approval_token':job.token if job.status=='PREVIEW' else None,
        'can_submit':(viewer is None or (viewer.id==job.actor_id and viewer.role=='ADMIN')) and bool(owner and owner.is_active and owner.role=='ADMIN') and job.release_version==RELEASE and job.status=='PREVIEW' and not historical_blockers(db) and job.expires_at>=datetime.utcnow() and bool(counts.get('ELIGIBLE')) and flags() and cap.get('supported',False) and cap.get('scope',False),
        'rows':[{'id':i.id,'snapshot':i.snapshot,'before':i.before,'after':i.after,'status':i.status,'reason':i.reason,
            'attempts':i.attempts,'sent_at':i.sent_at.isoformat()+'Z' if i.sent_at else None,'checked_at':i.checked_at.isoformat()+'Z' if i.checked_at else None,
            'verified_at':i.verified_at.isoformat()+'Z' if i.verified_at else None} for i in items]}


def overview(db,user,offset=0):
    authorize(user);latest=db.query(RetailBarcodeJob).filter_by(shop=service.shop(),release_version=RELEASE).order_by(RetailBarcodeJob.id.desc()).first()
    cap=(latest.preview_capability if latest else None) or {'supported':False,'scope':False,'reason':'BACKGROUND_SCHEMA_CHECK_REQUIRED'}
    jobs=db.query(RetailBarcodeJob).filter_by(shop=service.shop())
    states=dict(db.query(RetailBarcodeItem.status,service.func.count()).join(RetailBarcodeJob).filter(RetailBarcodeJob.shop==service.shop()).group_by(RetailBarcodeItem.status).all())
    last=db.query(service.func.max(RetailBarcodeItem.checked_at)).join(RetailBarcodeJob).filter(RetailBarcodeJob.shop==service.shop()).scalar()
    return {'enabled':flags(),'is_admin':user.role=='ADMIN','release':RELEASE,'historical_blockers':historical_blockers(db),'legacy_reservations':legacy_reservations(db).count(),'retire_confirmation':RETIRE_PHRASE,'capability':cap,'limit':settings.RETAIL_BARCODE_SYNC_BATCH_LIMIT,'total':jobs.count(),
        'states':states,'approved_local':db.query(RetailIdentifier).filter_by(shop=service.shop(),kind='barcode').count(),
        'last_check':last.isoformat()+'Z' if last else None,
        'jobs':[report(db,j,viewer=user) for j in jobs.order_by(RetailBarcodeJob.id.desc()).offset(offset).limit(20)]}


def csv_report(db,user,rid):
    job=get_job(db,user,rid);out=io.StringIO();writer=csv.writer(out)
    def safe(v):
        value=str(v if v is not None else '')
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value
    writer.writerow(['Batch','Product','Variant','Shopify variant','SKU','Local code','Remote before','Remote after','Status','Reason','Attempts','Last check'])
    for i in items_for(db,job):
        s=i.snapshot;writer.writerow([safe(v) for v in [job.id,s['title'],s['variant'],s['variant_id'],s['sku'],s['code'],json.dumps(i.before),json.dumps(i.after),i.status,i.reason,i.attempts,i.checked_at]])
    return out.getvalue()


def finish(db,job):
    items=items_for(db,job)
    if not any(i.status in PENDING for i in items):job.status='COMPLETE';job.completed_at=datetime.utcnow()


def verify(db,job,item,remote,cap):
    state=inspect(remote,item.snapshot);item.after=state;item.checked_at=datetime.utcnow()
    reason=remote_reason(state,item.snapshot,cap,readback=True)
    if not reason and job.release_version==RELEASE:
        state['protected']=remote.protected(item.snapshot)
        state['protected_unchanged']=state['protected']==item.before.get('protected')
        if not state['protected_unchanged']:reason='PROTECTED_ATTRIBUTES_CHANGED'
    if job.release_version!=RELEASE:
        state['protected_comparison']='LEGACY_BASELINE_UNAVAILABLE'
        if state.get('complete') is True and state.get('collision_complete') is True and state.get('variant_id')==item.snapshot['variant_id'] and state.get('product_id')==item.snapshot['product_id'] and state.get('version')==cap.get('version') and reason in {'READBACK_MISMATCH','READBACK_TYPE_MISMATCH','REMOTE_BARCODE_REUSED','REMOTE_INACTIVE_OR_REMOVED'}:
            item.status='BLOCKED';item.reason='LEGACY_READBACK_RETIRED_NO_RESEND';state['legacy_readback_reason']=reason;release(db,job,item)
            event(db,job,'LEGACY_READ_ONLY_RECONCILED',item,reason=reason);return
    if not reason:
        item.status='VERIFIED';item.reason='FRESH_READBACK_CONFIRMED';item.verified_at=datetime.utcnow();release(db,job,item)
    elif reason=='READBACK_MISMATCH' and not state['codes']:
        item.status='UNKNOWN';item.reason='WRITE_OUTCOME_UNCONFIRMED'  # Keep reservation, never resend.
    else:item.status='CONFLICT';item.reason=reason
    event(db,job,'READBACK_'+item.status,item,reason=item.reason,after=state)


def dispatch_one(sessions,remote=None):
    remote=remote or client
    if not settings.RETAIL_ENABLED:return False
    # A short shop lock serializes claims; the same lock fences imports/allocations.
    with sessions() as db:
        service.write_lock(db);now=datetime.utcnow()
        candidate=db.execute(select(RetailBarcodeItem).join(RetailBarcodeJob).where(
            RetailBarcodeJob.shop==service.shop(),RetailBarcodeJob.status.in_(['QUEUED','RUNNING']),
            RetailBarcodeItem.status.in_(list(PENDING)),RetailBarcodeItem.next_attempt_at<=now,
            service.or_(RetailBarcodeJob.release_version==RELEASE,RetailBarcodeItem.sent_at!=None),
            service.or_(flags(),RetailBarcodeItem.sent_at!=None),
            service.or_(RetailBarcodeItem.lease_until==None,RetailBarcodeItem.lease_until<now))
            .order_by(RetailBarcodeItem.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if candidate is None:db.commit();return False
        job=db.get(RetailBarcodeJob,candidate.job_id);recovery=bool(candidate.sent_at)
        if not flags() and not recovery:db.commit();return False
        candidate.status='CHECKING';candidate.attempts+=1;candidate.lease_until=now+timedelta(minutes=5)
        candidate.claim_token=secrets.token_hex(16);claim=candidate.claim_token
        job.status='RUNNING';rid=candidate.id;event(db,job,'CLAIMED_FOR_RECONCILIATION' if recovery else 'CHECKING',candidate);db.commit()
    try:
        with sessions() as db:
            # Keep this transaction lock across fresh checks and the bounded call.
            # A SENT marker is durably committed separately before the network boundary.
            service.write_lock(db);item=db.get(RetailBarcodeItem,rid);job=db.get(RetailBarcodeJob,item.job_id)
            if item.claim_token!=claim:return True
            cap=capability(remote)
            if recovery:
                verify(db,job,item,remote,cap);item.lease_until=None;finish(db,job);db.commit();return True
            owner=db.execute(select(User).where(User.id==job.actor_id).with_for_update()).scalar_one_or_none()
            try:
                authorize(owner)
                if owner.role!='ADMIN':owner=None
            except HTTPException:owner=None
            reason=normal_local_reason(db,db.get(RetailVariant,item.variant_id),item.snapshot)
            if job.release_version!=RELEASE or historical_blockers(db):reason='RELEASE_ACTIVATION_FENCED'
            elif not flags():reason='WRITES_DISABLED'
            elif not owner:reason='APPROVER_ACCESS_REVOKED'
            elif job.expires_at<datetime.utcnow():reason='APPROVAL_EXPIRED'
            elif cap.get('version')!=job.api_version:reason='API_VERSION_CHANGED'
            elif not cap.get('supported') or not cap.get('scope'):reason='SCOPE_OR_SCHEMA_UNVERIFIED'
            reservation=db.get(RetailBarcodeReservation,(job.shop,item.variant_id))
            if not reservation or reservation.item_id!=item.id:reason='RESERVATION_LOST'
            if not reason:
                state=inspect(remote,item.snapshot);item.checked_at=datetime.utcnow()
                reason=remote_reason(state,item.snapshot,cap)
                if not reason and remote.protected(item.snapshot)!=item.before.get('protected'):reason='PROTECTED_ATTRIBUTES_CHANGED'
            if reason:
                item.status='CONFLICT';item.reason=reason;item.lease_until=None;release(db,job,item);event(db,job,'PREFLIGHT_BLOCKED',item,reason=reason);finish(db,job);db.commit();return True
            # Durable intent before network: after this point recovery ONLY reads.
            item.status='SENT';item.sent_at=datetime.utcnow();event(db,job,'DISPATCH_INTENT',item,idempotency_key=item.idempotency_key);db.commit()
        with sessions() as db:
            service.write_lock(db);item=db.get(RetailBarcodeItem,rid);job=db.get(RetailBarcodeJob,item.job_id)
            if item.claim_token!=claim or item.status!='SENT' or item.lease_until<datetime.utcnow():return True
            # Imports or allocations between transactions invalidate dispatch.
            reason=normal_local_reason(db,db.get(RetailVariant,item.variant_id),item.snapshot)
            owner=db.execute(select(User).where(User.id==job.actor_id).with_for_update()).scalar_one_or_none()
            try:
                authorize(owner)
                if owner.role!='ADMIN':owner=None
            except HTTPException:owner=None
            cap=capability(remote)
            if job.release_version!=RELEASE or historical_blockers(db):reason='RELEASE_ACTIVATION_FENCED'
            if not flags() or not owner:reason='WRITES_OR_APPROVER_DISABLED'
            if job.expires_at<datetime.utcnow():reason='APPROVAL_EXPIRED'
            if cap.get('version')!=job.api_version:reason='API_VERSION_CHANGED'
            if not reason:reason=remote_reason(inspect(remote,item.snapshot),item.snapshot,cap)
            if not reason and remote.protected(item.snapshot)!=item.before.get('protected'):reason='PROTECTED_ATTRIBUTES_CHANGED'
            if reason:
                item.status='CONFLICT';item.reason=reason;event(db,job,'FINAL_PREFLIGHT_BLOCKED',item,reason=reason)
            else:
                try:remote.write_empty(item.snapshot,protected_before=item.before.get('protected'),not_after=min(job.expires_at,item.lease_until))
                except Exception:event(db,job,'MUTATION_RESPONSE_UNCERTAIN',item)
                # Acceptance, errors and timeouts all require fresh read-back.
                verify(db,job,item,remote,cap)
            item.lease_until=None;finish(db,job);db.commit()
        return True
    except Exception:
        with sessions() as db:
            service.write_lock(db);item=db.get(RetailBarcodeItem,rid);job=db.get(RetailBarcodeJob,item.job_id)
            if item.claim_token!=claim:return True
            item.lease_until=None
            if item.sent_at:
                item.status='UNKNOWN';item.reason='READBACK_UNAVAILABLE';event(db,job,'UNKNOWN',item)
            elif item.attempts<3:
                item.status='QUEUED';item.reason='RETRY_READ_ONLY';item.next_attempt_at=datetime.utcnow()+timedelta(seconds=30*2**(item.attempts-1));event(db,job,'READ_RETRY_SCHEDULED',item)
            else:
                item.status='FAILED';item.reason='PREFLIGHT_UNAVAILABLE';release(db,job,item);event(db,job,'FAILED_BEFORE_WRITE',item)
            finish(db,job);db.commit()
        return True


def reconcile(db,user,rid):
    authorize(user);service.write_lock(db);job=get_job(db,user,rid);found=False
    for item in items_for(db,job):
        if (item.sent_at and item.status in {'UNKNOWN','CONFLICT','SENT'}) or (job.release_version!=RELEASE and item.status in {'UNKNOWN','CONFLICT','SENT','CHECKING','PILOT_UNKNOWN','PILOT_INTENT'}):
            if not item.sent_at:item.sent_at=datetime.utcnow()  # Conservative uncertainty marker; read only forever.
            item.status='SENT';item.next_attempt_at=datetime.utcnow();item.lease_until=None;found=True
    if not found:raise HTTPException(409,'No uncertain dispatched items to reconcile')
    job.status='RUNNING';job.completed_at=None;event(db,job,'READ_ONLY_RECONCILIATION_REQUESTED')
    return report(db,job)
