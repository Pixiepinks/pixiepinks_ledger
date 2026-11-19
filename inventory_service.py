"""Shared inventory document services for CRM and future POS.

Shopify owns balances. Local documents describe authorized business events only.
"""
import copy
import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select
from inventory_models import InventoryDocument, InventoryEvent, InventoryObservation, InventoryPolicy, InventoryPolicyEvent
from retail_models import RetailVariant
from models import User, Supplier
from settings import settings
import retail_service as retail
import inventory_shopify as remote
from retail_inventory import InventoryError

PERMISSIONS=('retail.inventory.view','retail.inventory.prepare','retail.inventory.manage')
KINDS={'RECEIPT','ADJUSTMENT','COUNT','OPENING','TRANSFER'}
REASONS={'DAMAGED','LOST','CORRECTION','RETURN','MANUAL'}
MAX_LINES=10


def require(user,permission='retail.inventory.view',admin=False):
    if not settings.INVENTORY_ENABLED:raise HTTPException(404,'Inventory Management is disabled')
    retail.require(user,permission)
    if admin and user.role!='ADMIN':raise HTTPException(403,'Administrator approval required')


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def event(db,doc,action,user=None,**detail):
    db.add(InventoryEvent(shop=doc.shop,document_id=doc.id,action=action,actor_id=user.id if user else None,detail=detail))


def get(db,document_id):
    doc=db.execute(select(InventoryDocument).where(InventoryDocument.id==document_id,InventoryDocument.shop==retail.shop()).with_for_update()).scalar_one_or_none()
    if doc is None:raise HTTPException(404,'Inventory document not found')
    return doc


def amount(value,minimum=0,maximum=1000000):
    if type(value) is not int or not minimum<=value<=maximum:raise HTTPException(422,f'Quantity must be an integer from {minimum} to {maximum}')
    return value


def text(value,maximum=500,required=False):
    if not isinstance(value,str) or len(value)>maximum or any(ord(c)<32 and c not in '\n\t' for c in value) or (required and not value.strip()):raise HTTPException(422,'Invalid text field')
    return value.strip()


def identity(db,variant_id,version,location):
    if type(variant_id) is not int or type(version) is not int:raise HTTPException(422,'Product and current version required')
    row=retail.scanner_row(db,variant_id,version)
    if row.missing or row.product_status!='ACTIVE' or row.tracked is not True or not remote.gid(row.inventory_item_id,'InventoryItem') or not remote.gid(location,'Location'):raise HTTPException(409,'Active tracked product and actual location required')
    return {'local_id':row.id,'version':row.version,'variant_id':row.variant_id,'product_id':row.product_id,
        'inventory_item_id':row.inventory_item_id,'location_id':location,'title':row.title,'variant_title':row.variant_title,'sku':row.sku,'barcode':retail.scan_value(row)}


def assert_identity(db,line):
    current=identity(db,line['local_id'],line['version'],line['location_id'])
    if any(current[k]!=line[k] for k in ('variant_id','product_id','inventory_item_id')):raise InventoryError('LOCAL_INVENTORY_IDENTITY_CHANGED')


def create(db,user,payload,client=None):
    require(user,'retail.inventory.prepare');client=client or remote.client
    if not isinstance(payload,dict) or set(payload)-{'client_key','kind','supplier_id','invoice','date','notes','reason','destination','lines','count_cutoff_ack'}:raise HTTPException(422,'Unknown inventory document fields')
    key=payload.get('client_key')
    if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',key):raise HTTPException(422,'A stable request key is required')
    retail.write_lock(db)
    old=db.query(InventoryDocument).filter_by(shop=retail.shop(),client_key=key).one_or_none()
    # Request hash is embedded alongside normalized input; retries must be exactly identical.
    request_hash=digest(payload)
    if old:
        if old.payload.get('request_hash')!=request_hash:raise HTTPException(409,'Request key already used for different data')
        return old
    kind=payload.get('kind');lines=payload.get('lines')
    if kind not in KINDS or not isinstance(lines,list) or not 1<=len(lines)<=MAX_LINES:raise HTTPException(422,'Choose a document type and 1–10 product lines')
    supplier=payload.get('supplier_id')
    if supplier is not None and (type(supplier) is not int or db.get(Supplier,supplier) is None):raise HTTPException(422,'Choose an existing supplier')
    if kind=='RECEIPT' and supplier is None:raise HTTPException(422,'Receiving supplier is required')
    if kind=='ADJUSTMENT' and payload.get('reason') not in REASONS:raise HTTPException(422,'Choose a stock adjustment reason')
    try:document_date=date.fromisoformat(payload.get('date',''))
    except (ValueError,TypeError):raise HTTPException(422,'Receiving/count date is required') from None
    if document_date>datetime.now(ZoneInfo('Asia/Colombo')).date():raise HTTPException(422,'Future document dates are not allowed')
    normalized={'prepared_by':user.username,'request_hash':request_hash,'supplier_id':supplier,'supplier_name':db.get(Supplier,supplier).name if supplier else None,
        'invoice':text(payload.get('invoice',''),120,required=kind=='RECEIPT'),'date':document_date.isoformat(),
        'notes':text(payload.get('notes',''),2000),'reason':payload.get('reason'),'lines':[]}
    seen=set()
    for raw in lines:
        if not isinstance(raw,dict) or set(raw)-{'variant_id','version','location_id','quantity','unit_cost'}:raise HTTPException(422,'Invalid inventory line fields')
        line=identity(db,raw.get('variant_id'),raw.get('version'),raw.get('location_id'))
        pair=(line['inventory_item_id'],line['location_id'])
        if pair in seen:raise HTTPException(422,'Duplicate item/location line')
        seen.add(pair)
        if kind not in {'COUNT','OPENING'}:
            line['quantity']=amount(raw.get('quantity'),-1000000 if kind=='ADJUSTMENT' else 1)
            if line['quantity']==0:raise HTTPException(422,'Adjustment quantity cannot be zero')
            if payload.get('reason') in {'DAMAGED','LOST'} and line['quantity']>0:raise HTTPException(422,'Damaged/lost quantities must be negative')
        else:
            if payload.get('count_cutoff_ack') is not True:raise HTTPException(422,'Confirm the physical count cutoff and movement-control procedure')
        if kind=='RECEIPT':
            try:
                cost=Decimal(str(raw.get('unit_cost')))
                if not cost.is_finite() or cost<0 or cost>Decimal('999999999.99') or cost.as_tuple().exponent < -2:raise ValueError()
            except (InvalidOperation,ValueError,TypeError):raise HTTPException(422,'Nonnegative purchase unit cost with at most two decimals required') from None
            line['unit_cost']=format(cost,'.2f');line['cost_currency']=settings.CURRENCY
        normalized['lines'].append(line)
    if kind=='TRANSFER':
        destination=payload.get('destination')
        if not remote.gid(destination,'Location') or any(l['location_id']==destination for l in normalized['lines']):raise HTTPException(422,'Choose a distinct destination location')
        normalized['destination']=destination
    business_key=digest({'supplier':supplier,'invoice':normalized['invoice'].casefold()}) if kind=='RECEIPT' else None
    if business_key and db.query(InventoryDocument).filter_by(shop=retail.shop(),business_key=business_key).first():raise HTTPException(409,'This supplier invoice already has a receiving document; review its existing outcome')
    doc=InventoryDocument(business_key=business_key,id=str(uuid4()),shop=retail.shop(),client_key=key,kind=kind,reference='INV-'+uuid4().hex[:16].upper(),
        payload=normalized,payload_hash=digest(normalized),contract_version=remote.CONTRACT_VERSION,actor_id=user.id,dispatch_key=str(uuid4()),status='DRAFT')
    db.add(doc);db.flush()
    if kind in {'COUNT','OPENING'}:
        # Start sheet now; every later correction compares against this immutable cutoff.
        doc.baseline=observe_lines(db,doc,client)
    event(db,doc,'CREATED',user,kind=kind,physical_balance='PROVISIONAL');db.commit();return doc


def observe_lines(db,doc,client):
    cap=client.capabilities()
    if not cap.get('read_inventory') or not cap.get('read_locations'):raise InventoryError('INVENTORY_READ_SCOPES_REQUIRED')
    output=[];deadline=time.monotonic()+20
    for line in doc.payload['lines']:
        if time.monotonic()>deadline:raise InventoryError('DOCUMENT_READ_BUDGET_EXCEEDED')
        assert_identity(db,line)
        observed=client.observe(line)
        if time.monotonic()>deadline:raise InventoryError('DOCUMENT_READ_BUDGET_EXCEEDED')
        output.append({**observed,'read_at':datetime.utcnow().isoformat()+'Z'})
    return output


def observe_count(db,user,document_id,line,quantity):
    require(user,'retail.inventory.prepare');retail.write_lock(db);doc=get(db,document_id)
    if doc.status!='DRAFT' or doc.kind not in {'COUNT','OPENING'}:raise HTTPException(409,'This count sheet is not editable')
    if type(line) is not int or not 0<=line<len(doc.payload['lines']):raise HTTPException(422,'Unknown count line')
    quantity=amount(quantity)
    db.add(InventoryObservation(document_id=doc.id,line=line,quantity=quantity,actor_id=user.id))
    event(db,doc,'COUNT_OBSERVED',user,line=line,quantity=quantity);db.commit();return doc


def submit(db,user,document_id,client=None):
    require(user,'retail.inventory.prepare');client=client or remote.client;retail.write_lock(db);doc=get(db,document_id)
    if doc.status!='DRAFT':raise HTTPException(409,'Only draft documents can be submitted')
    if digest(doc.payload)!=doc.payload_hash:raise HTTPException(409,'Document integrity failure')
    current=observe_lines(db,doc,client)
    if doc.kind=='TRANSFER':
        destinations={r['id'] for r in client.locations() if r.get('isActive') is True}
        if doc.payload['destination'] not in destinations:raise InventoryError('TRANSFER_DESTINATION_UNAVAILABLE')
    plan=[]
    if doc.kind in {'COUNT','OPENING'}:
        latest={}
        for obs in db.query(InventoryObservation).filter_by(document_id=doc.id).order_by(InventoryObservation.id):latest[obs.line]=obs.quantity
        if len(latest)!=len(doc.payload['lines']):raise HTTPException(409,'Record every count-sheet line')
        if any(any(current[i][k]!=doc.baseline[i][k] for k in ('on_hand','available')) for i in range(len(current))):raise HTTPException(409,'Stock moved during counting; restart the count with a new cutoff')
    else:doc.baseline=current
    for i,line in enumerate(doc.payload['lines']):
        before=doc.baseline[i]['on_hand']
        after=latest[i] if doc.kind in {'COUNT','OPENING'} else before+line['quantity']
        if doc.kind=='TRANSFER':
            if line['quantity']>max(0,current[i]['available']):raise HTTPException(409,'Insufficient available stock for transfer')
            after=before  # Native transfer unavailable: never simulate a two-leg set operation.
        if not 0<=after<=2147483647:raise HTTPException(422,'Resulting on-hand quantity is outside the supported range')
        plan.append({**line,'before':before,'after':after,'available_before':doc.baseline[i]['available']})
    doc.plan=plan;doc.status='SUBMITTED';event(db,doc,'SUBMITTED',user,plan=plan,baseline=doc.baseline);db.commit();return doc


def approve(db,user,document_id,confirmation):
    require(user,admin=True);retail.write_lock(db);doc=get(db,document_id)
    if doc.status!='SUBMITTED':raise HTTPException(409,'Only submitted documents can be approved')
    if confirmation!=doc.reference:raise HTTPException(422,'Confirm the exact document reference')
    if doc.contract_version!=remote.CONTRACT_VERSION:raise HTTPException(409,'Document uses an older inventory contract; reject and prepare a newly reviewed document')
    if digest(doc.payload)!=doc.payload_hash or not doc.plan:raise HTTPException(409,'Document integrity failure')
    doc.approved_by=user.id;doc.approval_hash=digest({'payload':doc.payload_hash,'plan':doc.plan,'baseline':doc.baseline,'shop':doc.shop,'contract':doc.contract_version})
    doc.status='TRANSFER_PENDING' if doc.kind=='TRANSFER' else 'APPROVED'
    event(db,doc,'ADMIN_APPROVED',user,approval_hash=doc.approval_hash,reference=doc.reference,write_enabled=bool(settings.SHOPIFY_INVENTORY_WRITE_ENABLED and remote.MUTATION_CONTRACT_VERIFIED))
    db.commit();return doc


def reject(db,user,document_id,reason):
    require(user,admin=True);retail.write_lock(db);doc=get(db,document_id)
    if doc.status not in {'DRAFT','SUBMITTED','APPROVED','TRANSFER_PENDING'}:raise HTTPException(409,'Document cannot be rejected after dispatch')
    doc.status='REJECTED';event(db,doc,'REJECTED',user,reason=text(reason,500,True));db.commit();return doc


def transfer_confirmation(db,user,document_id,stage,confirmation):
    require(user,admin=True);retail.write_lock(db);doc=get(db,document_id)
    if doc.kind!='TRANSFER' or doc.status!='TRANSFER_PENDING' or stage not in {'DISPATCH','RECEIVE'} or confirmation!=doc.reference:raise HTTPException(409,'Approved pending transfer and exact confirmation required')
    actions={e.action for e in db.query(InventoryEvent).filter_by(document_id=doc.id)}
    if stage=='RECEIVE' and 'TRANSFER_DISPATCH_RECORDED' not in actions:raise HTTPException(409,'Record dispatch confirmation first')
    if 'TRANSFER_'+stage+'_RECORDED' not in actions:event(db,doc,'TRANSFER_'+stage+'_RECORDED',user,shopify_applied=False,native_api_verified=False)
    db.commit();return doc


def public(db,doc):
    events=db.query(InventoryEvent).filter_by(document_id=doc.id).order_by(InventoryEvent.id).all()
    observations=db.query(InventoryObservation).filter_by(document_id=doc.id).order_by(InventoryObservation.id).all()
    return {'id':doc.id,'reference':doc.reference,'kind':doc.kind,'status':doc.status,'payload':doc.payload,'baseline':doc.baseline,'plan':doc.plan,
        'contract_version':doc.contract_version,'actor_id':doc.actor_id,'approved_by':doc.approved_by,'attempts':doc.attempts,'remote_reference':doc.remote_reference,
        'completed_at':doc.completed_at.isoformat()+'Z' if doc.completed_at else None,
        'created_at':doc.created_at.isoformat()+'Z','dispatched_at':doc.dispatched_at.isoformat()+'Z' if doc.dispatched_at else None,
        'error':doc.error,'physical_balance':'VERIFIED_AT_COUNT_CUTOFF' if doc.kind=='OPENING' and doc.status=='VERIFIED' else 'PROVISIONAL',
        'events':[{'action':e.action,'actor_id':e.actor_id,'at':e.created_at.isoformat()+'Z','detail':e.detail} for e in events],
        'observations':[{'line':o.line,'quantity':o.quantity,'actor_id':o.actor_id,'at':o.created_at.isoformat()+'Z'} for o in observations]}


def blocked_job(db):
    return db.query(InventoryDocument).filter(InventoryDocument.shop==retail.shop(),InventoryDocument.status.in_(['DISPATCHING','UNCERTAIN','APPLIED_REVIEW'])).first()


def dispatch_one(sessions,client=None):
    """At most one attempt, durable intent before IO; uncertain jobs block the shop."""
    if not settings.RETAIL_ENABLED or not settings.INVENTORY_ENABLED:return False
    client=client or remote.client
    with sessions() as db:
        retail.write_lock(db)
        running=db.query(InventoryDocument).filter_by(shop=retail.shop(),status='DISPATCHING').first()
        if running and running.dispatched_at and running.dispatched_at<datetime.utcnow()-timedelta(minutes=2):
            running.status='UNCERTAIN';running.error='WORKER_OUTCOME_UNKNOWN';event(db,running,'DISPATCH_INTERRUPTED');db.commit();return True
        if blocked_job(db):return False
        if not settings.SHOPIFY_INVENTORY_WRITE_ENABLED or not remote.MUTATION_CONTRACT_VERIFIED:return False
        doc=db.query(InventoryDocument).filter_by(shop=retail.shop(),status='APPROVED').order_by(InventoryDocument.created_at).first()
        if not doc:return False
        try:
            approver=db.get(User,doc.approved_by);creator=db.get(User,doc.actor_id)
            require(approver,admin=True);require(creator,'retail.inventory.prepare')
            if doc.contract_version!=remote.CONTRACT_VERSION or doc.kind not in KINDS-{'TRANSFER'} or doc.attempts!=0 or digest(doc.payload)!=doc.payload_hash or doc.approval_hash!=digest({'payload':doc.payload_hash,'plan':doc.plan,'baseline':doc.baseline,'shop':doc.shop,'contract':doc.contract_version}):raise InventoryError('APPROVAL_INTEGRITY_FAILED')
            approval=db.query(InventoryEvent).filter_by(document_id=doc.id,action='ADMIN_APPROVED').one()
            if approval.created_at<datetime.utcnow()-timedelta(minutes=30):raise InventoryError('APPROVAL_EXPIRED')
            cap=client.capabilities()
            if not all(cap.get(k) for k in ('write_inventory','read_inventory','read_locations','mutation_shape_verified','source_contract_verified')):raise InventoryError('INVENTORY_MUTATION_CONTRACT_UNVERIFIED')
            current=observe_lines(db,doc,client)
            absolute=doc.kind in {'COUNT','OPENING'}
            if absolute and any(current[i]['on_hand']!=p['before'] or current[i]['available']!=p['available_before'] for i,p in enumerate(doc.plan)):raise InventoryError('CONCURRENT_STOCK_CHANGE')
            execution=copy.deepcopy(doc.plan)
            if not absolute:
                # Approval authorizes a delta; current balances may legitimately change.
                for i,p in enumerate(execution):
                    delta=doc.plan[i]['after']-doc.plan[i]['before']
                    p['before']=current[i]['on_hand'];p['after']=p['before']+delta
                    p['available_before']=current[i]['available']
                    if delta<0 and p['available_before']+delta<0:raise InventoryError('INSUFFICIENT_AVAILABLE_STOCK')
                    if not 0<=p['after']<=2147483647:raise InventoryError('INVALID_RESULTING_STOCK')
            if all(p['after']==p['before'] for p in doc.plan):
                doc.status='VERIFIED';doc.completed_at=datetime.utcnow();event(db,doc,'NO_CHANGE_VERIFIED',before=current,after=current);db.commit();return True
            plan=execution;kind=doc.kind;reason=doc.payload.get('reason');reference='urn:pixiepinks:inventory:'+doc.shop+':'+doc.id;key=doc.dispatch_key;doc_id=doc.id
            wire=remote.InventoryShopify.prepare(plan,reference,key,kind,reason)
            doc.status='DISPATCHING';doc.attempts=1;doc.dispatched_at=datetime.utcnow()
            event(db,doc,'DISPATCH_INTENT',key=key,reference=reference,plan=plan,before=current,contract_version=remote.CONTRACT_VERSION,operation='SET' if absolute else 'ADJUST',reason=wire['reason'],wire=wire,wire_sha256=digest(wire),quantity_name='on_hand' if absolute else 'available',ledger_prefix=f'gid://pixiepinks/InventoryMovement/{key}-');db.commit()
        except (InventoryError,HTTPException) as error:
            doc.status='CONFLICT';doc.error=error.reason if isinstance(error,InventoryError) else 'PERMISSION_OR_IDENTITY_CHANGED'
            event(db,doc,'PREFLIGHT_BLOCKED',reason=doc.error);db.commit();return True
    try:
        outcome=client.apply(plan,reference,key,kind,reason,prepared=wire)
        group=outcome['group_id']
    except Exception:
        with sessions() as db:
            retail.write_lock(db);doc=get(db,doc_id);doc.status='UNCERTAIN';doc.error='MUTATION_OUTCOME_UNKNOWN'
            event(db,doc,'MUTATION_UNCERTAIN',automatic_retry=False);db.commit()
        return True
    with sessions() as db:
        retail.write_lock(db);doc=get(db,doc_id);doc.remote_reference=group
        event(db,doc,'REMOTE_ACCEPTED',group=group,outcome=outcome)
        try:
            after=observe_lines(db,doc,client)
            matches=all(after[i]['on_hand']==p['after'] for i,p in enumerate(plan))
            doc.status='VERIFIED' if matches else 'APPLIED_REVIEW';doc.error=None if matches else 'READBACK_CHANGED'
            event(db,doc,'READBACK_VERIFIED' if matches else 'READBACK_REQUIRES_REVIEW',before=current,after=after,group=group,execution_plan=plan)
            if matches:doc.completed_at=datetime.utcnow()
        except Exception:
            doc.status='APPLIED_REVIEW';doc.error='READBACK_UNAVAILABLE';event(db,doc,'READBACK_UNAVAILABLE',group=group)
        db.commit()
    return True


def reconcile(db,user,document_id,evidence,confirmation,client=None):
    """Read only. Never infers mutation success from a matching current quantity alone."""
    require(user,admin=True);retail.write_lock(db);doc=get(db,document_id);client=client or remote.client
    if doc.status not in {'UNCERTAIN','APPLIED_REVIEW'}:raise HTTPException(409,'Document does not require reconciliation')
    observed=observe_lines(db,doc,client)
    event(db,doc,'RECONCILIATION_OBSERVED',user,quantities=observed,evidence=text(evidence,2000,True))
    if confirmation!=doc.reference:raise HTTPException(422,'Confirm the exact document reference')
    # A human closes review with retained evidence; status never masquerades as VERIFIED.
    doc.status='RECONCILED_MANUALLY';doc.completed_at=datetime.utcnow()
    event(db,doc,'MANUALLY_RECONCILED',user,automatic_retry=False,verified_by_api=False);db.commit();return doc


def set_policy(db,user,payload):
    require(user,'retail.inventory.manage');retail.write_lock(db)
    if not isinstance(payload,dict) or set(payload)!={'variant_id','version','location_id','minimum','target','supplier_id'}:raise HTTPException(422,'Invalid reorder policy')
    line=identity(db,payload['variant_id'],payload['version'],payload['location_id']);minimum=amount(payload['minimum']);target=amount(payload['target'])
    if target<minimum:raise HTTPException(422,'Target must be at least minimum stock')
    supplier=payload['supplier_id']
    if supplier is not None and (type(supplier) is not int or not db.get(Supplier,supplier)):raise HTTPException(422,'Unknown supplier')
    row=db.query(InventoryPolicy).filter_by(shop=retail.shop(),variant_id=line['local_id'],location_id=line['location_id']).one_or_none()
    if row is None:row=InventoryPolicy(shop=retail.shop(),variant_id=line['local_id'],location_id=line['location_id']);db.add(row)
    before={k:getattr(row,k) for k in ('minimum','target','supplier_id')}
    row.minimum=minimum;row.target=target;row.supplier_id=supplier;row.updated_by=user.id;row.updated_at=datetime.utcnow()
    db.add(InventoryPolicyEvent(shop=retail.shop(),actor_id=user.id,detail={'before':before,'after':payload}));db.commit()


def stock(db,user,rid,version,location,client=None):
    require(user);line=identity(db,rid,version,location);client=client or remote.client
    try:
        cap=client.capabilities()
        if not cap.get('read_inventory') or not cap.get('read_locations'):raise InventoryError('INVENTORY_READ_SCOPES_REQUIRED')
        quantities=client.observe(line);db.expire_all();assert_identity(db,line)
    except InventoryError as error:return {'status':'UNAVAILABLE','reason':error.reason,'available':None,'on_hand':None,'physical_balance':'PROVISIONAL'}
    policy=db.query(InventoryPolicy).filter_by(shop=retail.shop(),variant_id=rid,location_id=location).one_or_none()
    verified=db.query(InventoryDocument).filter_by(shop=retail.shop(),kind='OPENING',status='VERIFIED').order_by(InventoryDocument.completed_at.desc()).all()
    opening=next((d for d in verified if any(p['variant_id']==line['variant_id'] and p['location_id']==location for p in d.plan)),None)
    return {'status':'VERIFIED_LIVE','physical_balance':'OPENING_RECONCILED' if opening else 'PROVISIONAL',
        'opening_reference':opening.reference if opening else None,
        'opening_verified_at':opening.completed_at.isoformat()+'Z' if opening and opening.completed_at else None,
        'opening_approved_by':opening.approved_by if opening else None,**quantities,'read_at':datetime.utcnow().isoformat()+'Z',
        'minimum':policy.minimum if policy else None,'target':policy.target if policy else None,
        'low_stock':quantities['available']<policy.minimum if policy else None,'out_of_stock':quantities['available']<=0,
        'suggested_replenishment':max(0,policy.target-quantities['available']) if policy else None,
        'supplier_id':policy.supplier_id if policy else None}


def movement_report(db,user):
    require(user);result=[]
    docs=db.query(InventoryDocument).filter_by(shop=retail.shop()).order_by(InventoryDocument.created_at.desc()).limit(500)
    for doc in docs:
        events=db.query(InventoryEvent).filter_by(document_id=doc.id).order_by(InventoryEvent.id).all()
        intent=next((e.detail for e in reversed(events) if e.action=='DISPATCH_INTENT'),{})
        observed=next((e.detail.get('after',[]) for e in reversed(events) if e.action in {'READBACK_VERIFIED','READBACK_REQUIRES_REVIEW'}),[])
        for i,line in enumerate(doc.payload['lines']):
            approved=doc.plan[i] if doc.plan else {}
            p=intent.get('plan',doc.plan or [])[i] if doc.plan else {}
            result.append({'reference':doc.reference,'kind':doc.kind,'status':doc.status,'title':line['title'],'sku':line['sku'],
                'contract_version':doc.contract_version,'basis':'EXECUTION_INTENT' if intent else 'APPROVED_PLAN',
                'approved_before':approved.get('before'),'approved_after':approved.get('after'),
                'observed_on_hand':observed[i].get('on_hand') if len(observed)>i else None,
                'observed_available':observed[i].get('available') if len(observed)>i else None,
                'barcode':line['barcode'],'location':line['location_id'],'before':p.get('before'),'after':p.get('after'),
                'delta':p.get('after',0)-p.get('before',0) if p else None,'unit_cost':line.get('unit_cost'),
                'supplier':doc.payload.get('supplier_name'),'date':doc.payload['date'],'api_reference':doc.remote_reference})
    return result


def csv_report(rows):
    out=io.StringIO();writer=csv.writer(out)
    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            values=[str(v) if v is not None else '' for v in row.values()]
            writer.writerow(["'"+v if v.lstrip().startswith(('=','+','-','@')) else v for v in values])
    return out.getvalue()
