"""One customer registry for CRM and POS. No marketing or Shopify mutations."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import unicodedata,re
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from models import Customer,CustomerPhoneLock,CustomerRegistration
from marketing_identity import phone
import loyalty_service as loyalty


def name_key(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
def mobile(value):
    result=phone(value)
    if result and isinstance(value,str):
        digits=re.sub(r'\D','',value)
        if not value.strip().startswith(('+','00')) and len(digits)<=10 and not re.fullmatch(r'07\d{8}',digits):raise HTTPException(422,'Include the international country code; ambiguous national numbers are not inferred')
    if not result:raise HTTPException(422,'Enter a complete mobile with its country code, or a valid Sri Lankan local mobile')
    return result

def candidates(db,value):
    normalized=mobile(value)
    # Legacy contacts remain valid without imposing destructive phone uniqueness.
    query=db.query(Customer).filter(or_(Customer.normalized_mobile==normalized,Customer.normalized_mobile.is_(None),Customer.phone!=Customer.normalized_mobile))
    return [c for c in query.order_by(Customer.id) if phone(c.phone)==normalized]

def local_time(value):
    if not value:return None
    instant=loyalty.stamp(value) if isinstance(value,str) else value
    return instant.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo')).strftime('%Y-%m-%d %H:%M Sri Lanka')

def describe(db,c):
    s=loyalty.member_summary(db,c.id)
    return {'country':__import__('order_recipient').customer_country(c.phone),'id':c.id,'name':c.name,'phone':phone(c.phone),'source':c.registration_source,'registered_at':c.registered_at.isoformat()+'Z' if c.registered_at else None,'registered_local':local_time(c.registered_at),'last_purchase_local':local_time(s['last_purchase_at']),'membership_id':s['membership_id'],'membership_status':s['status'] if s['membership_id'] else ('IDENTITY_REVIEW' if s['status']=='INELIGIBLE' else 'PENDING_ACTIVATION' if s['qualifying_count'] else 'PENDING_FIRST_PURCHASE'),'qualifying_purchases':s['qualifying_count'],'net_paid':s['lifetime_net'],'last_purchase_at':s['last_purchase_at'],'pending_recalculation':s['recalculation_status']}

def search(db,q):
    if not isinstance(q,str) or not (1<=len(q.strip())<=100 and (len(q.strip())>=2 or q.strip().isdigit())):raise HTTPException(422,'Enter at least two search characters')
    normalized=phone(q);term=q.strip().casefold()
    if normalized:rows=candidates(db,q)
    else:
        member_ids={m.customer_id for m in db.query(loyalty.LoyaltyMember) if term in m.membership_id.casefold()}
        escaped=term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        numeric_id=int(term) if term.isdigit() and len(term)<=10 and int(term)<=2147483647 else -1
        rows=db.query(Customer).filter(or_(Customer.name.ilike('%'+escaped+'%',escape='\\') if len(term)>=2 else False,Customer.id==numeric_id,Customer.id.in_(member_ids or [-1]))).order_by(Customer.id).limit(21).all()
    return {'rows':[describe(db,c) for c in rows[:20]],'identity_conflict':bool(normalized and len(rows)>1),'has_more':len(rows)>20,'suggestions_only':True}

def lock_mobile(db,normalized):
    dialect=db.get_bind().dialect.name
    if dialect=='postgresql':from sqlalchemy.dialects.postgresql import insert
    elif dialect=='sqlite':from sqlalchemy.dialects.sqlite import insert
    else:raise HTTPException(503,'Customer registration locking unavailable')
    db.execute(insert(CustomerPhoneLock).values(phone=normalized).on_conflict_do_nothing(index_elements=['phone']))
    db.query(CustomerPhoneLock).filter_by(phone=normalized).with_for_update().one()

def register(db,user,name,raw_phone,source,key=None,email=None):
    if not isinstance(name,str) or not 2<=len(name.strip())<=255 or any(ord(c)<32 for c in name):raise HTTPException(422,'Customer name must contain 2–255 printable characters')
    normalized=mobile(raw_phone);name=name.strip();source=source if source in {'CRM','POS','CUSTOMER_MANAGEMENT'} else 'CRM'
    if key is None:key=str(uuid4())
    if not isinstance(key,str) or not 8<=len(key)<=100:raise HTTPException(422,'Valid registration request key required')
    fingerprint=loyalty.digest({'name':name_key(name),'phone':normalized,'source':source,'email':email or None,'actor':user.id})
    lock_mobile(db,normalized)
    prior=db.query(CustomerRegistration).filter_by(request_key=key).one_or_none()
    if prior:
        if prior.fingerprint!=fingerprint:raise HTTPException(409,'Registration key belongs to different details; review before retrying')
        return db.get(Customer,prior.customer_id),False
    matches=candidates(db,normalized)
    if matches or any(name_key(c.name)==name_key(name) for c in db.query(Customer)):
        loyalty.audit(db,'CUSTOMER_IDENTITY_CONFLICT','registration',user.username,None,{'source':source,'candidate_count':len(matches)},'Select an existing customer or request authorized identity review')
        raise HTTPException(409,'Existing or conflicting customer identity found. Search and explicitly select the correct Customer ID; do not register a duplicate.')
    if email is not None and (not isinstance(email,str) or len(email)>254):raise HTTPException(422,'Invalid optional email')
    try:
        with db.begin_nested():
            c=Customer(name=name,phone=normalized,normalized_mobile=normalized,email=email or None,registration_source=source,registered_at=datetime.utcnow());db.add(c);db.flush()
            db.add(CustomerRegistration(request_key=key,customer_id=c.id,fingerprint=fingerprint,source=source,actor_id=user.id));db.flush()
    except IntegrityError:raise HTTPException(409,'Concurrent customer registration detected; search existing profiles before retrying') from None
    loyalty.audit(db,'CUSTOMER_REGISTERED',c.id,user.username,None,{'customer_id':c.id,'source':source,'marketing_consent':False})
    return c,True

def association(db,cid,raw_phone=None):
    if type(cid) is not int or not 0<cid<=2147483647:raise HTTPException(422,'Select a valid internal Customer ID')
    c=db.query(Customer).filter_by(id=cid).with_for_update().one_or_none()
    if not c or not phone(c.phone):raise HTTPException(409,'Customer identity requires authorized review')
    if raw_phone and mobile(raw_phone)!=phone(c.phone):raise HTTPException(409,'Selected customer and purchaser mobile differ; review the identity')
    return c


def order_audit(db,order,actor,previous=None):
    from sqlalchemy import inspect
    if not inspect(db.connection()).has_table('loyalty_audits'):return
    loyalty.audit(db,'CUSTOMER_ORDER_ASSOCIATED' if order.customer_id else 'ORDER_UNIDENTIFIED',order.id,actor,{'customer_id':previous} if previous is not None else None,{'customer_id':order.customer_id,'unidentified_reason':order.unidentified_reason},order.unidentified_reason)


def edit_profile(db,user,cid,payload):
    if user.role!='ADMIN':raise HTTPException(403,'Administrator identity review required')
    if type(cid) is not int or not 0<cid<=2147483647:raise HTTPException(422,'Invalid Customer ID')
    if not isinstance(payload,dict) or set(payload)!={'name','phone','email','fingerprint'}:raise HTTPException(422,'Only reviewed identity fields accepted')
    name=payload['name'];email=payload['email']
    if not isinstance(name,str) or not 2<=len(name.strip())<=255 or any(ord(c)<32 for c in name):raise HTTPException(422,'Invalid customer name')
    if not isinstance(email,str) or len(email)>254:raise HTTPException(422,'Invalid optional email')
    normalized=mobile(payload['phone']);lock_mobile(db,normalized)
    c=db.query(Customer).filter_by(id=cid).populate_existing().with_for_update().one_or_none()
    if not c:raise HTTPException(404,'Customer not found')
    if payload['fingerprint']!=loyalty.fingerprint(c):raise HTTPException(409,'Customer identity changed; reload and review before saving')
    others=[v for v in db.query(Customer) if v.id!=cid and (phone(v.phone)==normalized or name_key(v.name)==name_key(name))]
    if others:raise HTTPException(409,'Conflicting identity exists; use authorized identity review rather than merging profiles')
    before={'name':c.name,'phone':c.phone,'email':c.email};after={'name':name.strip(),'phone':normalized,'email':email.strip() or None}
    if before!=after:
        c.name=after['name'];c.phone=after['phone'];c.email=after['email'];c.normalized_mobile=normalized
        loyalty.audit(db,'CUSTOMER_PROFILE_REVIEWED',cid,user.username,before,after,'Explicit administrator identity correction; commercial orders not rewritten')
    return c
