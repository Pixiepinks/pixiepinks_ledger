"""Reviewed identity recovery. Never edits commercial orders or posts money."""
from datetime import datetime,timedelta
from decimal import Decimal
from uuid import uuid4
import copy,re,unicodedata
from sqlalchemy import func
from models import Customer,SalesOrder,SalesPayment,WhatsAppConversation
from pos_models import POSCheckout
from loyalty_models import (LoyaltyOrderIdentity,LoyaltyRecoveryPlan,LoyaltyHistoricalOrder,LoyaltyHistoryScan)
import loyalty_service as svc
from settings import settings

MAX_AGE=timedelta(hours=24)

def require_off(db,lock=True):
    c=svc.config(db,lock)
    if not c or c.enabled:svc.fail('Historical recovery requires the loyalty programme OFF',409)

def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).casefold().split())

def valid_name(value):
    name=str(value or '').strip()
    return bool(2<=len(name)<=255 and normalized_name(name) not in {'walk-in customer','walk in','unknown','customer','test','test customer'} and not any(ord(c)<32 for c in name))

def local_snapshot(db,order):
    conversation=db.get(WhatsAppConversation,order.crm_conversation_id) if order.crm_conversation_id else None
    pos=db.query(POSCheckout).filter_by(sales_order_id=order.id).one_or_none()
    sources=[{'source':'SALES_ORDER','name':order.customer_name,'phone':svc.phone(order.whatsapp_phone)}]
    if conversation:sources.append({'source':'WHATSAPP_CONVERSATION','name':conversation.customer_name,'phone':svc.phone(conversation.phone_number)})
    if pos:sources.append({'source':'POS_FROZEN','name':pos.frozen.get('customer_name'),'phone':svc.phone(pos.frozen.get('customer_phone'))})
    # Never mine message contents or infer a customer association from staff names.
    primary=sources[0];usable=[s for s in sources if valid_name(s['name']) and s['phone']]
    choice=primary if primary in usable else usable[0] if usable else primary
    conflict=any(s['phone'] and choice['phone'] and s['phone']!=choice['phone'] or valid_name(s['name']) and valid_name(choice['name']) and normalized_name(s['name'])!=normalized_name(choice['name']) for s in sources)
    financial,error=svc.financial(db,order)
    local_paid=db.query(func.coalesce(func.sum(SalesPayment.amount),0)).filter_by(sales_order_id=order.id,verification_status='VERIFIED',currency='LKR').scalar()
    remote_paid=bool(order.shopify_payment_currency=='LKR' and order.shopify_payment_status_refreshed_at and order.shopify_total_received is not None and order.shopify_total_outstanding==0 and order.shopify_total_received>=order.grand_total and order.shopify_financial_status in {'PAID','PARTIALLY_REFUNDED','REFUNDED'})
    completion_verified=bool(pos and pos.status=='COMPLETE') if order.sales_channel=='POS' else order.order_status=='DELIVERED'
    payment_verified=bool(order.grand_total and order.grand_total>0 and order.currency=='LKR' and order.payment_status in {'PAID','PARTIALLY_REFUNDED','REFUNDED'} and (local_paid>=order.grand_total or remote_paid or pos and pos.status=='COMPLETE' and order.amount_paid==order.grand_total))
    return {'payment_verified':payment_verified,'completion_verified':completion_verified,'kind':'LOCAL','id':order.id,'reference':order.internal_order_number,'customer_id':svc.order_customer(db,order),'name':choice['name'],'phone':choice['phone'],'sources':sources,'identity_conflict':conflict,'financial':financial,'financial_error':error,'channel':order.sales_channel or 'CRM','order_gid':order.shopify_order_id,'status':order.order_status,'payment_status':order.payment_status,'updated_at':order.updated_at.isoformat()}

def remote_snapshot(db,row):
    eligible,error=remote_financial(db,row)
    return {'payment_verified':bool(eligible),'completion_verified':row.evidence.get('displayFulfillmentStatus')=='FULFILLED','kind':'SHOPIFY','id':row.id,'reference':row.evidence.get('name',row.order_gid),'customer_id':row.customer_id if row.approved_fingerprint==row.fingerprint else None,'name':row.identity.get('name'),'phone':svc.phone(row.identity.get('phone')),'sources':[{'source':'SHOPIFY_CUSTOMER','name':row.identity.get('name'),'phone':svc.phone(row.identity.get('phone'))}],'identity_conflict':False,'financial':eligible,'financial_error':error,'channel':'SHOPIFY_HISTORY','order_gid':row.order_gid,'source_fingerprint':row.fingerprint}

def source_snapshot(db,kind,source_id):
    if type(source_id) is not int:svc.fail('Numeric source ID required')
    if kind=='LOCAL':
        order=db.get(SalesOrder,source_id)
        if not order:svc.fail('Historical order not found',404)
        return local_snapshot(db,order)
    if kind=='SHOPIFY':
        row=db.get(LoyaltyHistoricalOrder,source_id)
        if not row or row.shop!=(settings.SHOPIFY_SHOP or '').lower():svc.fail('Historical evidence not found in configured shop',404)
        return remote_snapshot(db,row)
    svc.fail('Choose LOCAL or SHOPIFY history')

def proposal(db,snapshot):
    phone=snapshot['phone'];name=snapshot['name']
    matches=[c for c in db.query(Customer).order_by(Customer.id) if phone and svc.phone(c.phone)==phone]
    exact=[c for c in matches if normalized_name(c.name)==normalized_name(name)]
    candidates=[{'id':c.id,'name':c.name,'name_matches':c in exact} for c in matches]
    usable=bool(phone and valid_name(name) and not snapshot['identity_conflict'])
    unambiguous=usable and len(matches)==1 and len(exact)==1
    return {'candidates':candidates,'unambiguous':unambiguous,'suggested_customer_id':exact[0].id if unambiguous else None,'can_create':usable and snapshot['financial_error']!='TEST_ORDER' and not matches,'recoverable_information':usable,'requires_identity_review':not unambiguous,'phone_mask':('…'+phone[-3:]) if phone else None,'has_name':valid_name(name),'fingerprint':svc.digest(snapshot)}

def dashboard(db):
    rows=[local_snapshot(db,o) for o in db.query(SalesOrder).order_by(SalesOrder.id)]
    remote=db.query(LoyaltyHistoricalOrder).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower()).all()
    rows += [remote_snapshot(db,r) for r in remote]
    channels={};counts={'total_orders':len(rows),'linked_customers':0,'missing_customer_links':0,'recoverable_information':0,'identity_review':0,'eligible_customers':0,'missing_payment_evidence':0,'missing_completion_evidence':0,'duplicate_order_candidates':0}
    customers=set();seen=set()
    for row in rows:
        channels[row['channel']]=channels.get(row['channel'],0)+1
        p=proposal(db,row);linked=bool(row['customer_id']);counts['linked_customers']+=linked;counts['missing_customer_links']+=not linked
        counts['recoverable_information']+=bool(not linked and p['recoverable_information']);counts['identity_review']+=bool(not linked and p['requires_identity_review'])
        counts['missing_payment_evidence']+=not row['payment_verified']
        counts['missing_completion_evidence']+=not row['completion_verified']
        key=row['order_gid'] or f"local:{row['id']}"
        counts['duplicate_order_candidates']+=key in seen;seen.add(key)
        if linked and row['financial'] and Decimal(row['financial']['net'])>=svc.money(svc.rules_config(db)['minimum_purchase']):customers.add(row['customer_id'])
    counts['eligible_customers']=sum(svc.member_summary(db,c)['status']=='ACTIVE' for c in customers)
    scan=db.query(LoyaltyHistoryScan).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower()).order_by(LoyaltyHistoryScan.started_at.desc()).first()
    return {'counts':counts,'channels':channels,'coverage':scan_summary(scan) if scan else {'status':'NOT_CHECKED','complete_shopify_history':False},'recent_recovery_plans':[{'id':p.id,'status':p.status,'orders':len(p.items),'created_at':p.created_at.isoformat()} for p in db.query(LoyaltyRecoveryPlan).order_by(LoyaltyRecoveryPlan.created_at.desc()).limit(30)],'diagnostic':'Names/phones do not establish authoritative Customer IDs. Recovery never rewrites commercial orders.'}

def order_rows(db,kind='LOCAL',offset=0):
    if kind=='LOCAL':rows=[local_snapshot(db,o) for o in db.query(SalesOrder).order_by(SalesOrder.id).offset(offset).limit(50)]
    elif kind=='SHOPIFY':rows=[remote_snapshot(db,r) for r in db.query(LoyaltyHistoricalOrder).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower()).order_by(LoyaltyHistoricalOrder.id).offset(offset).limit(50)]
    else:svc.fail('Choose LOCAL or SHOPIFY history')
    return [{k:v for k,v in r.items() if k not in {'name','phone','sources'}}|proposal(db,r) for r in rows]

def preview(db,user,items,key):
    require_off(db)
    if not isinstance(key,str) or not 8<=len(key)<=100:svc.fail('Unique request key required')
    if not isinstance(items,list) or not 1<=len(items)<=100:svc.fail('Review 1–100 orders per recovery plan')
    requested=[];seen=set()
    for item in items:
        if not isinstance(item,dict):svc.fail('Invalid recovery selection')
        kind=item.get('kind');sid=item.get('id');action=item.get('action','ASSOCIATE');cid=item.get('customer_id')
        if not isinstance(kind,str) or kind not in {'LOCAL','SHOPIFY'} or type(sid) is not int:svc.fail('Choose a valid source and numeric order ID')
        if (kind,sid) in seen:svc.fail('Duplicate source order in selection')
        seen.add((kind,sid));s=source_snapshot(db,kind,sid);p=proposal(db,s)
        if s['customer_id']:svc.fail('Order already has an authoritative customer; recovery cannot reassign it',409)
        if action=='CREATE':
            if not p['can_create']:svc.fail('Customer creation requires consistent source name/mobile and no existing identity collision',409)
            if len(items)!=1:svc.fail('New customer creation requires individual review')
            target={'create_name':s['name'].strip(),'create_phone':s['phone']}
        elif action=='ASSOCIATE':
            if type(cid) is not int or not db.get(Customer,cid):svc.fail('Select an existing internal Customer ID')
            if len(items)>1 and (not p['unambiguous'] or p['suggested_customer_id']!=cid):svc.fail('Bulk recovery permits only unambiguous name-and-phone matches; review other orders individually',409)
            target={'customer_id':cid,'target_fingerprint':svc.fingerprint(db.get(Customer,cid))}
        else:svc.fail('Choose ASSOCIATE or CREATE')
        requested.append({'shop':(settings.SHOPIFY_SHOP or '').lower(),'kind':kind,'id':sid,'action':action,'source_fingerprint':p['fingerprint'],'candidate_fingerprint':svc.digest(p['candidates']),'evidence':s,'unambiguous':p['unambiguous'],**target})
    prior=db.query(LoyaltyRecoveryPlan).filter_by(request_key=key).one_or_none()
    if prior:
        if prior.items!=svc.serial(requested):svc.fail('Request key reused with changed recovery evidence',409)
        return prior
    job=LoyaltyRecoveryPlan(id=str(uuid4()),request_key=key,digest=svc.digest(requested),items=svc.serial(requested),actor_id=user.id,status='PREVIEW')
    db.add(job);svc.audit(db,'RECOVERY_PREVIEW',job.id,user.username,None,{'digest':job.digest,'orders':len(items),'commercial_writes':0});return job

def approve(db,user,job,digest,why):
    require_off(db)
    if job.status!='PREVIEW' or job.digest!=digest or svc.digest(job.items)!=digest:svc.fail('Reviewed recovery digest required',409)
    job.status='APPROVED';job.approved_by=user.id;job.approved_at=datetime.utcnow();job.approval_reason=svc.reason(why)
    svc.audit(db,'RECOVERY_APPROVED',job.id,user.username,None,{'digest':digest},job.approval_reason)

def execute(db,user,job,digest):
    require_off(db)
    if job.digest!=digest or svc.digest(job.items)!=digest:svc.fail('Recovery digest mismatch',409)
    if job.status=='COMPLETE':return job
    if job.status!='APPROVED' or not job.approved_at or job.approved_at<datetime.utcnow()-timedelta(minutes=15):svc.fail('Fresh explicit recovery approval required',409)
    # Freeze source identity rows and existing targets against concurrent edits.
    for item in sorted(job.items,key=lambda i:(i['kind'],i['id'])):
        model=SalesOrder if item['kind']=='LOCAL' else LoyaltyHistoricalOrder
        db.query(model).filter_by(id=item['id']).populate_existing().with_for_update().one_or_none()
    target_ids=sorted({i['customer_id'] for i in job.items if i['action']=='ASSOCIATE'})
    if target_ids:db.query(Customer).filter(Customer.id.in_(target_ids)).order_by(Customer.id).populate_existing().with_for_update().all()
    from customer_registration import lock_mobile
    for item in job.items:
        if item['action']=='CREATE':lock_mobile(db,item['create_phone'])
    # Verify every source and target before creating any customer/mapping.
    for item in job.items:
        if item.get('shop')!=(settings.SHOPIFY_SHOP or '').lower():svc.fail('Recovery shop identity changed; review a new preview',409)
        s=source_snapshot(db,item['kind'],item['id'])
        if svc.digest(s)!=item['source_fingerprint']:svc.fail('Recovery evidence changed; generate a fresh preview',409)
        if svc.digest(proposal(db,s)['candidates'])!=item['candidate_fingerprint']:svc.fail('Identity candidates changed; review a fresh preview',409)
        if item['action']=='ASSOCIATE' and (not db.get(Customer,item['customer_id']) or svc.fingerprint(db.get(Customer,item['customer_id']))!=item['target_fingerprint']):svc.fail('Target customer changed; review a new preview',409)
        if item['action']=='CREATE' and not proposal(db,s)['can_create']:svc.fail('Customer identity collision; review existing records',409)
    prior_processing=db.info.get('loyalty_processing');db.info['loyalty_processing']=True
    try:
        results=[]
        for item in job.items:
            if item['action']=='CREATE':
                customer=Customer(name=item['create_name'],phone=item['create_phone'],normalized_mobile=item['create_phone'],registration_source='HISTORICAL_REVIEW',registered_at=datetime.utcnow());db.add(customer);db.flush();cid=customer.id
            else:cid=item['customer_id']
            if item['kind']=='LOCAL':
                db.add(LoyaltyOrderIdentity(order_id=item['id'],customer_id=cid,actor_id=user.id,reason=job.approval_reason))
                review=db.query(svc.LoyaltyIdentityReview).filter_by(order_id=item['id'],status='OPEN').all()
                for row in review:row.status='RESOLVED';row.resolution={'customer_id':cid,'plan_id':job.id,'actor_id':user.id,'reason':job.approval_reason}
            else:
                row=db.get(LoyaltyHistoricalOrder,item['id']);row.customer_id=cid;row.approved_fingerprint=row.fingerprint
            results.append({'kind':item['kind'],'id':item['id'],'customer_id':cid,'created':item['action']=='CREATE'})
        job.status='COMPLETE';job.results=results
        svc.audit(db,'RECOVERY_EXECUTED',job.id,user.username,None,{'digest':digest,'associations':results,'memberships_created':0,'commercial_writes':0},job.approval_reason);db.flush()
    finally:
        if prior_processing is None:db.info.pop('loyalty_processing',None)
        else:db.info['loyalty_processing']=prior_processing
    return job


def scan_summary(scan):
    return {'id':scan.id,'status':scan.status,'pages':scan.pages,'orders_seen':scan.orders_seen,'started_at':scan.started_at.isoformat(),'completed_at':scan.completed_at.isoformat() if scan.completed_at else None,**scan.coverage}

def remote_financial(db,row):
    e=row.evidence;scan=db.get(LoyaltyHistoryScan,row.scan_id)
    if row.shop!=(settings.SHOPIFY_SHOP or '').lower():return None,'SHOP_MISMATCH'
    if db.query(SalesOrder.id).filter_by(shopify_order_id=row.order_gid).first():return None,'DUPLICATE_LOCAL_CRM'
    if not scan or scan.status!='PAGINATION_EXHAUSTED':return None,'INCOMPLETE_SCAN'
    if row.fetched_at<datetime.utcnow()-MAX_AGE:return None,'STALE_SHOPIFY_EVIDENCE'
    if e.get('test'):return None,'TEST_ORDER'
    if e.get('cancelledAt'):return None,'CANCELLED_OR_RETURNED'
    if e.get('displayFulfillmentStatus')!='FULFILLED':return None,'NOT_COMPLETED'
    try:
        total=svc.money(e['currentTotalPriceSet']['shopMoney']['amount']);received=svc.money(e['totalReceivedSet']['shopMoney']['amount']);outstanding=svc.money(e['totalOutstandingSet']['shopMoney']['amount']);refund=svc.money(e['totalRefundedSet']['shopMoney']['amount'])
        currencies={e[k]['shopMoney']['currencyCode'] for k in ('currentTotalPriceSet','totalReceivedSet','totalOutstandingSet','totalRefundedSet')}
    except (KeyError,TypeError):return None,'PAYMENT_NOT_VERIFIED'
    # Original paid revenue is current price plus any fully verified refunded value.
    status=e.get('displayFinancialStatus')
    if currencies!={'LKR'} or received<=0 or (total==0 and refund<received) or outstanding!=0 or received<total or refund>received or status not in {'PAID','PARTIALLY_REFUNDED','REFUNDED'}:return None,'PAYMENT_NOT_VERIFIED'
    if status=='REFUNDED' and refund!=received or status=='PARTIALLY_REFUNDED' and not 0<refund<received or status=='PAID' and refund!=0:return None,'REFUND_EVIDENCE_UNAVAILABLE'
    return {'paid':received,'refunded':refund,'net':received-refund},None

def remote_history(db,cid,seen,filters=None):
    result=[];minimum=svc.money(svc.rules_config(db)['minimum_purchase'])
    query=db.query(LoyaltyHistoricalOrder).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower(),customer_id=cid)
    for row in query:
        if row.approved_fingerprint!=row.fingerprint:continue
        evidence,error=remote_financial(db,row)
        if error or evidence['net']<minimum:continue
        e=row.evidence;created=svc.stamp(e['createdAt']);key='shopify:'+row.order_gid
        if key in seen:continue
        if filters:
            if filters.get('source') and filters['source']!='SHOPIFY_HISTORY':continue
            if filters.get('start') and created<svc.stamp(filters['start']):continue
            if filters.get('end') and created>=svc.stamp(filters['end']):continue
        seen.add(key);result.append({'order_id':None,'historical_order_id':row.id,'reference':e.get('name',row.order_gid),'key':key,**{k:str(v) for k,v in evidence.items()},'date':e.get('closedAt'),'order_created_at':e['createdAt'],'channel':'SHOPIFY_HISTORY'})
    return result
