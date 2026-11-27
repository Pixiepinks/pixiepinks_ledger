"""Admin-only, local order association; never performs external or financial operations."""
import hashlib,json,re
from datetime import datetime
from uuid import uuid4
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import or_,func
from models import (Lead,User,Customer,SalesOrder,SalesOrderLine,SalesPayment,WhatsAppConversation as C,
                    ShopifySyncEvent,ShopifyPaymentSyncOperation,ShopifyFulfillmentOperation)
from sales_pipeline_models import SalesOpportunity as O,SalesPipelineEvent as E
import sales_pipeline as s
import conversation_access as access
from marketing_identity import phone
SOURCE='ADMIN_ORDER_RECONCILIATION'
STATES={'Needs Investigation','Awaiting Supporting Evidence','Ambiguous Existing Orders','Payment Verification Required','Reviewed — No Association Established','Resolved'}
TERMINAL={'SYNCED','SUCCEEDED','COMPLETE','COMPLETED','FAILED_SAFE','CANCELLED','REJECTED','NO_CHANGE'}

def authorize(user):
    s.authorize(user)
    if user.role!='ADMIN':raise HTTPException(403,'Administrator required')

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def snapshot(row):return {col.name:getattr(row,col.name) for col in row.__table__.columns}
def plain(row):return json.loads(json.dumps(snapshot(row),default=str))
def ident(value):
    if type(value) is not int or not 0<value<=2147483647:raise HTTPException(422,'Valid internal record ID required')
    return value

def context(db,user):
    """Batched metadata only: no WhatsApp message body queries, no identity inference."""
    authorize(user)
    past=db.query(E).filter(E.current['source'].as_string()==SOURCE,E.current['action'].as_string()=='ASSOCIATED').all()
    history_ids={v.opportunity_id for v in past}
    rows=s.scope(db,user).join(Lead,Lead.id==O.lead_id).filter(or_((O.active_conversation_id.isnot(None)) & Lead.status.in_(['ORDER_CONFIRMED','DISPATCHED','DELIVERED','PAID']),O.id.in_(history_ids or [-1]))).order_by(O.id).all()
    cs={v.id:v for v in db.query(C).filter(C.id.in_([o.conversation_id for o in rows] or [-1]))}
    ls={v.id:v for v in db.query(Lead).filter(Lead.id.in_([o.lead_id for o in rows] or [-1]))}
    orders=db.query(SalesOrder).filter(or_(SalesOrder.crm_conversation_id.in_(list(cs) or [-1]),SalesOrder.id.in_([o.order_id or o.data.get('order_id') for o in rows] or [-1]))).order_by(SalesOrder.id).all()
    ids=[v.id for v in orders];byid={v.id:v for v in orders};byconv={}
    for v in orders:byconv.setdefault(v.crm_conversation_id,[]).append(v)
    customers={v.id:v for v in db.query(Customer).filter(Customer.id.in_([v.customer_id for v in orders if v.customer_id] or [-1]))}
    users={v.id:v.username for v in db.query(User).filter(User.id.in_([v.assigned_to_user_id for v in cs.values() if v.assigned_to_user_id] or [-1]))}
    paid={}
    for oid,currency,amount in db.query(SalesPayment.sales_order_id,SalesPayment.currency,func.sum(SalesPayment.amount)).filter(SalesPayment.sales_order_id.in_(ids or [-1]),SalesPayment.verification_status=='VERIFIED').group_by(SalesPayment.sales_order_id,SalesPayment.currency):paid[(oid,currency)]=Decimal(amount)
    blockers=set()
    for model in (ShopifySyncEvent,ShopifyPaymentSyncOperation,ShopifyFulfillmentOperation):
        blockers.update(v.sales_order_id for v in db.query(model).filter(model.sales_order_id.in_(ids or [-1]),or_(model.status.notin_(TERMINAL),model.status.is_(None))))
    links={}
    for oid,orderid in db.query(O.id,O.order_id).filter(O.order_id.in_(ids or [-1])):links.setdefault(orderid,[]).append(oid)
    histories={}
    for event in db.query(E).filter(E.opportunity_id.in_([v.id for v in rows] or [-1])).order_by(E.id):
        if event.current.get('source')==SOURCE:histories.setdefault(event.opportunity_id,[]).append(event)
    return {'rows':rows,'cs':cs,'ls':ls,'byid':byid,'byconv':byconv,'customers':customers,'users':users,'paid':paid,'blockers':blockers,'links':links,'histories':histories}

def project(ctx,o):
    c=ctx['cs'][o.conversation_id];lead=ctx['ls'][o.lead_id];orders=ctx['byconv'].get(c.id,[])
    selected=o.order_id or o.data.get('order_id') or (orders[0].id if len(orders)==1 else None)
    order=ctx['byid'].get(selected);customer=ctx['customers'].get(order.customer_id) if order else None
    conflicts=[]
    if o.active_conversation_id!=c.id or lead.whatsapp_conversation_id!=c.id:conflicts.append('Current purchase intent / lead identity mismatch')
    if o.order_id!=o.data.get('order_id'):conflicts.append('Stored order association fields disagree')
    if len(orders)>1 and not o.order_id:conflicts.append('Multiple existing orders require separate identity investigation')
    if order:
        if order.crm_conversation_id!=c.id:conflicts.append('Exact conversation identity mismatch')
        if order.crm_lead_id is not None and order.crm_lead_id!=lead.id:conflicts.append('Order lead identity mismatch')
        if not phone(order.whatsapp_phone) or phone(order.whatsapp_phone)!=phone(c.phone_number):conflicts.append('Purchaser identity conflicts with exact conversation')
        if any(v!=o.id for v in ctx['links'].get(order.id,[])):conflicts.append('Order already linked to another opportunity')
        if not s.convertible(order):conflicts.append('Existing authoritative conversion rules reject this order')
        if o.data.get('customer_id') is not None and o.data['customer_id']!=order.customer_id:conflicts.append('Opportunity registered customer association conflicts with order')
        if order.customer_id and (not customer or not phone(customer.phone) or phone(customer.phone)!=phone(order.whatsapp_phone)):conflicts.append('Registered customer conflicts with purchaser')
        if len({v.customer_id for v in orders if v.customer_id})>1:conflicts.append('Conflicting registered customer associations')
        if order.id in ctx['blockers'] or order.shopify_payment_reconciliation_status in {'RECONCILIATION_REQUIRED','REVIEW','UNCERTAIN'}:conflicts.append('Unresolved Shopify operation or reconciliation lock')
    verified=ctx['paid'].get((order.id,order.currency),Decimal(0)) if order else Decimal(0)
    warning=bool((lead.status=='PAID' or order and order.payment_status=='PAID') and (not order or verified<=0 or verified<Decimal(order.grand_total)))
    history=ctx['histories'].get(o.id,[]);reconciled=any(v.current.get('action')=='ASSOCIATED' for v in history)
    already=bool(order and o.order_id==order.id and o.data.get('order_id')==order.id)
    category='history' if reconciled else 'conflict' if conflicts else 'associated' if already else 'missing' if not order else 'ready'
    return {'opportunity_id':o.id,'conversation_id':c.id,'lead_id':lead.id,'name':c.customer_name or lead.customer_name,'phone':c.phone_number,'assigned':ctx['users'].get(c.assigned_to_user_id,'Unassigned'),'version':o.version,'lead_status':lead.status,'stage':s.stage(o,lead),'expected_stage':'CONFIRMED','opportunity':{'data':o.data,'created_at':s.stamp(o.created_at),'updated_at':s.stamp(o.updated_at)},'candidate_ids':[v.id for v in orders],'order':{k:getattr(order,k) for k in ('id','internal_order_number','customer_id','crm_conversation_id','order_status','payment_status','shopify_sync_status','shopify_lifecycle_status','shopify_order_name','grand_total','currency','customer_name','whatsapp_phone','created_at')} if order else None,'verified_amount':str(verified),'outstanding':str(max(Decimal(0),Decimal(order.grand_total)-verified)) if order else None,'payment_warning':warning,'conflicts':conflicts,'eligible':not conflicts and order is not None and not already,'already_associated':already,'category':category,'investigation':o.data.get('investigation'),'history_count':len(history),'evidence':{'exact_conversation':bool(order and order.crm_conversation_id==c.id),'customer_consistency':'Conflict detected' if any('customer' in x.lower() or 'purchaser' in x.lower() for x in conflicts) else 'Verified locally' if customer else 'Not applicable — no registered association','unique_candidate':len(orders)==1,'authoritative_conversion':s.convertible(order),'shopify_safety':'Conflict detected' if order and order.id in ctx['blockers'] else 'Local records checked; live Shopify not verified','freshness':'Revalidated on submission'}}

def listing(db,user,params):
    ctx=context(db,user)
    try:page=int(params.get('page',1));size=int(params.get('size',25))
    except (ValueError,TypeError):raise HTTPException(422,'Invalid page') from None
    if page<1 or not 1<=size<=100 or len(params.get('q',''))>255:raise HTTPException(422,'Invalid page or search')
    tab=params.get('tab','ready')
    if tab not in {'ready','missing','payment','conflict','history','all'}:raise HTTPException(422,'Unknown reconciliation filter')
    rows=[];counts={'ready':0,'missing':0,'payment':0,'conflict':0,'history':0,'associated':0}
    for o in ctx['rows']:
        v=project(ctx,o);counts[v['category']]+=1
        if v['payment_warning']:counts['payment']+=1
        text=' '.join(str(x or '') for x in [v['name'],v['phone'],o.id,v['conversation_id'],v['lead_id'],v['assigned'],*([v['order']['id'],v['order']['internal_order_number']] if v['order'] else [])]).casefold()
        if (tab=='all' or tab==v['category'] or tab=='payment' and v['payment_warning']) and params.get('q','').strip().casefold() in text:rows.append(v)
    return {'counts':counts,'overlapping_categories':True,'total':len(rows),'page':page,'size':size,'rows':rows[(page-1)*size:page*size],'local_evidence_only':True}

def evidence(db,user,oid):
    ident(oid);ctx=context(db,user);o=next((v for v in ctx['rows'] if v.id==oid),None)
    if not o:raise HTTPException(404,'Eligible historical opportunity not found')
    v=project(ctx,o);order=ctx['byid'].get(v['order']['id']) if v['order'] else None
    payments=db.query(SalesPayment).filter_by(sales_order_id=order.id).order_by(SalesPayment.id).all() if order else []
    lines=db.query(SalesOrderLine).filter_by(sales_order_id=order.id).order_by(SalesOrderLine.id).all() if order else []
    ops=[]
    if order:
        for model in (ShopifySyncEvent,ShopifyPaymentSyncOperation,ShopifyFulfillmentOperation):ops.extend(db.query(model).filter_by(sales_order_id=order.id).order_by(model.id))
    material={'opportunity':snapshot(o),'conversation':snapshot(ctx['cs'][o.conversation_id]),'lead':snapshot(ctx['ls'][o.lead_id]),'orders':[snapshot(v) for v in ctx['byconv'].get(o.conversation_id,[])],'customer':snapshot(ctx['customers'][order.customer_id]) if order and order.customer_id in ctx['customers'] else None,'payments':[snapshot(v) for v in payments],'operations':[snapshot(v) for v in ops],'lines':[snapshot(v) for v in lines]}
    v['digest']=digest(material);v['items']=[{'product':x.product_name_snapshot,'variant':x.variant_name_snapshot,'quantity':str(x.quantity),'unit_price':str(x.unit_price)} for x in lines]
    v['history']=[{'id':x.id,'actor_id':x.actor_id,'at':s.stamp(x.created_at),'previous':x.previous,'current':x.current} for x in db.query(E).filter_by(opportunity_id=oid).order_by(E.id.desc()).limit(100)]
    return v

def request_items(payload):
    if not isinstance(payload,dict) or set(payload)-{'items','confirmed','note'}:raise HTTPException(422,'Invalid reconciliation request')
    items=payload.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=25:raise HTTPException(422,'Select 1–25 individually reviewed candidates')
    for v in items:
        if not isinstance(v,dict) or set(v)!={'opportunity_id','order_id','version','digest','reviewed'} or v['reviewed'] is not True or type(v['version']) is not int or not isinstance(v['digest'],str):raise HTTPException(422,'Each candidate requires current reviewed evidence')
        ident(v['opportunity_id']);ident(v['order_id'])
    if len({v['opportunity_id'] for v in items})!=len(items) or len({v['order_id'] for v in items})!=len(items):raise HTTPException(409,'Duplicate opportunity or order selection')
    return sorted(items,key=lambda v:v['opportunity_id'])

def preview(db,user,payload):
    authorize(user);items=request_items(payload);out=[]
    for item in items:
        v=evidence(db,user,item['opportunity_id'])
        if not v['eligible'] or v['order']['id']!=item['order_id'] or v['digest']!=item['digest'] or v['version']!=item['version']:raise HTTPException(409,'Evidence changed or candidate is ineligible; review again')
        out.append(v)
    return {'items':out,'message':'Associate the selected existing CRM orders with their corresponding Sales Pipeline opportunities. No new orders, payments or Shopify transactions will be created.'}

def associate(db,user,payload,key):
    authorize(user);items=request_items(payload)
    if payload.get('confirmed') is not True:raise HTTPException(422,'Explicit administrator confirmation required')
    note=payload.get('note','')
    if not isinstance(note,str) or not 1<=len(note.strip())<=2000:raise HTTPException(422,'Administrator review note required (1–2000 characters)')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,70}',key):raise HTTPException(422,'Idempotency key required')
    request_hash=digest({'actor':user.id,'payload':payload});root='reconcile:'+key
    candidates=db.query(O).filter(O.id.in_([v['opportunity_id'] for v in items])).all()
    # Same conversation -> opportunity ordering as normal edits. Order locks also fence FK insertions.
    for cid in sorted({v.conversation_id for v in candidates}):db.query(C).filter_by(id=cid).populate_existing().with_for_update().one()
    for item in items:db.query(O).filter_by(id=item['opportunity_id']).populate_existing().with_for_update().one_or_none()
    for oid in sorted(v['order_id'] for v in items):db.query(SalesOrder).filter_by(id=oid).populate_existing().with_for_update().one_or_none()
    for lid in sorted({v.lead_id for v in candidates}):db.query(Lead).filter_by(id=lid).populate_existing().with_for_update().one()
    # Freeze existing financial/identity evidence while it is compared; no evidence rows are written.
    orderids=sorted(v['order_id'] for v in items)
    for model in (SalesPayment,SalesOrderLine,ShopifySyncEvent,ShopifyPaymentSyncOperation,ShopifyFulfillmentOperation):
        db.query(model).filter(model.sales_order_id.in_(orderids)).order_by(model.id).populate_existing().with_for_update().all()
    customerids=sorted({v.customer_id for v in db.query(SalesOrder).filter(SalesOrder.id.in_(orderids)) if v.customer_id})
    db.query(Customer).filter(Customer.id.in_(customerids or [-1])).order_by(Customer.id).populate_existing().with_for_update().all()
    db.expire_all()
    prior=db.query(E).filter_by(key=root).one_or_none()
    if prior:
        if prior.actor_id!=user.id or prior.current.get('request_hash')!=request_hash:raise HTTPException(409,'Idempotency key already used for different evidence')
        return {**prior.current['result'],'idempotent':True}
    reviewed=preview(db,user,payload)['items'];batch=str(uuid4());results=[]
    for item,v in zip(items,reviewed):
        o=db.get(O,item['opportunity_id']);lead=db.get(Lead,o.lead_id);c=db.get(C,o.conversation_id);old=dict(o.data);previous_status=lead.status;old_stage=s.stage(o,lead)
        # Explicit confirmation includes this transition; preserve Delivered / Dispatched canonical lifecycle.
        if lead.status=='PAID':lead.status='ORDER_CONFIRMED'
        o.order_id=item['order_id'];o.data={**old,'order_id':item['order_id'],'stage':'CONFIRMED','lead_status':lead.status,'closed_at':datetime.utcnow().isoformat()};o.version+=1;o.updated_at=datetime.utcnow()
        s.schedule(db,o,c)
        db.add(E(opportunity_id=o.id,actor_id=user.id,previous={'order_id':old.get('order_id'),'stage':old_stage,'lead_status':previous_status},current={'source':SOURCE,'action':'ASSOCIATED','conversation_id':c.id,'lead_id':lead.id,'order_id':o.order_id,'stage':s.stage(o,lead),'lead_status':lead.status,'note':note.strip(),'evidence_digest':v['digest'],'evidence':{**v['evidence'],'candidate_order_ids':v['candidate_ids'],'opportunity_version':v['version'],'order_status':v['order']['order_status'],'recorded_payment_status':v['order']['payment_status'],'shopify_sync_status':v['order']['shopify_sync_status'],'shopify_lifecycle_status':v['order']['shopify_lifecycle_status'],'currency':v['order']['currency'],'order_total':str(v['order']['grand_total']),'verified_local_amount':v['verified_amount'],'outstanding':v['outstanding']},'payment_warning':v['payment_warning'],'batch_id':batch,'request_hash':request_hash},key=root+':'+str(o.id)))
        results.append({'opportunity_id':o.id,'order_id':o.order_id,'status':'ASSOCIATED','stage':s.stage(o,lead)})
    result={'batch_id':batch,'results':results,'financial_mutations':False}
    db.add(E(actor_id=user.id,previous={},current={'source':SOURCE,'action':'BATCH_COMMITTED','request_hash':request_hash,'result':result},key=root));db.flush()
    return result

def investigate(db,user,oid,payload,key):
    authorize(user);ident(oid)
    if not isinstance(payload,dict) or set(payload)!={'state','note','version'} or not isinstance(payload['state'],str) or payload['state'] not in STATES or not isinstance(payload['note'],str) or not 1<=len(payload['note'].strip())<=2000:raise HTTPException(422,'Choose an investigation state and provide a note')
    o=s.scope(db,user).filter(O.id==oid).populate_existing().with_for_update().one_or_none()
    if not o:raise HTTPException(404,'Opportunity not found')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,70}',key or ''):raise HTTPException(422,'Idempotency key required')
    h=digest({'actor':user.id,'id':oid,'payload':payload});prior=db.query(E).filter_by(key='investigate:'+key).one_or_none()
    if prior:
        if prior.current.get('request_hash')!=h:raise HTTPException(409,'Idempotency conflict')
        return {'saved':True}
    if type(payload['version']) is not int or payload['version']!=o.version:raise HTTPException(409,'Opportunity changed; review again')
    old=o.data.get('investigation');o.data={**o.data,'investigation':{'state':payload['state'],'note':payload['note'].strip()}};o.version+=1;o.updated_at=datetime.utcnow()
    db.add(E(opportunity_id=oid,actor_id=user.id,previous={'investigation':old},current={'source':SOURCE,'action':'INVESTIGATION','investigation':o.data['investigation'],'request_hash':h},key='investigate:'+key));db.flush()
    return {'saved':True}

def readiness(db):
    """Sanitized metadata and current counts for an already READ ONLY transaction."""
    from sqlalchemy import inspect,text
    inspector=inspect(db.bind);tables=set(inspector.get_table_names());required={'sales_opportunities','sales_pipeline_events','sales_orders'}
    constraints=[]
    if required<=tables:
        constraints=inspector.get_unique_constraints('sales_opportunities')+inspector.get_indexes('sales_opportunities')
    unique=any(x.get('column_names')==['order_id'] and (x.get('unique') or 'unique' not in x) for x in constraints)
    if db.bind.dialect.name=='postgresql':
        trigger=bool(db.execute(text("SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('sales_pipeline_events') AND tgname='pipeline_audit_immutable' AND NOT tgisinternal AND tgenabled IN ('O','A')")).first())
    else:trigger=bool(db.execute(text("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='pipeline_audit_no_update'")).first())
    admins=db.query(User).filter(User.role=='ADMIN',User.is_active.is_(True)).all();allowed=[u for u in admins if __import__('staff_access').allowed(u,'whatsapp')]
    counts=listing(db,allowed[0],{'size':'1'})['counts'] if allowed and s.settings.SALES_PIPELINE_ENABLED else None
    blockers=[]
    if not required<=tables:blockers.append('Required tables missing')
    if not unique:blockers.append('Unique order association constraint missing')
    if not trigger:blockers.append('Immutable pipeline audit trigger missing or disabled')
    if not allowed:blockers.append('No active authorized administrator')
    if not s.settings.SALES_PIPELINE_ENABLED:blockers.append('Existing Sales Pipeline gate disabled')
    return {'feature_gate':'SALES_PIPELINE_ENABLED','pipeline_enabled':s.settings.SALES_PIPELINE_ENABLED,'reminders_enabled':s.settings.SALES_PIPELINE_REMINDERS_ENABLED,'administrator_authorization_ready':bool(allowed),'unique_order_association':unique,'immutable_audit_ready':trigger,'counts':counts,'blockers':blockers,'local_evidence_only':True,'mutations':False}
