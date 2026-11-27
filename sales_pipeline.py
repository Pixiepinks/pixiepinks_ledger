"""One canonical Lead status, independent opportunities and scoped commitments.

No customer creation, financial posting, Shopify calls or outbound customer sends.
"""
import hashlib,json,csv,io
from datetime import datetime,date,timedelta,time,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func,select,event
from sqlalchemy.orm import Session
from models import Lead,LeadNote,WhatsAppConversation as C,WhatsAppConversationMessage as M,User,SalesOrder,SalesPayment
from sales_pipeline_models import SalesOpportunity as O,SalesPipelineEvent as E,SalesPipelineReminder as R,SalesPipelineControl as Control
from settings import settings
import conversation_access as access
from staff_access import allowed
TZ=ZoneInfo('Asia/Colombo')
STAGES={'NEW':'NEW','REPLIED':'INTERESTED','AWAITING_CUSTOMER_REPLY':'INTERESTED','NOT_RESPONDING':'INTERESTED','INTERESTED':'INTERESTED','FOLLOW_UP':'INTERESTED','NEGOTIATING':'INTERESTED','OUT_OF_STOCK':'INTERESTED','PAYMENT_PENDING':'PAYMENT','FUTURE_BUYER':'FUTURE','PAID':'PAYMENT','ORDER_CONFIRMED':'CONFIRMED','DISPATCHED':'CONFIRMED','DELIVERED':'CONFIRMED','LOST':'CLOSED','NOT_INTERESTED':'CLOSED','INVALID_SPAM':'CLOSED'}
CANONICAL={'NEW':'NEW','INTERESTED':'INTERESTED','PAYMENT':'PAYMENT_PENDING','FUTURE':'FUTURE_BUYER','CONFIRMED':'ORDER_CONFIRMED','CLOSED':'LOST'}
LABELS={'NEW':'New WhatsApp Inquiries','INTERESTED':'Interested / Follow-up','PAYMENT':'Payment Pending','FUTURE':'Future Buyers','CONFIRMED':'Confirmed Orders','CLOSED':'Closed opportunities'}
FIELDS={'stage','priority','product','note','estimated','agreed','payment_date','purchase_date','followup_date','precision','range_start','range_end','order_id','closed_reason'}
DEFAULTS={'hour':9,'minute':0,'payment_offsets':[-1,0,1],'purchase_offsets':[-3,-1,0]}

def convertible(order):
    return bool(order and order.sales_channel!='POS' and order.order_status in {'CONFIRMED','PROCESSING','DISPATCHED','DELIVERED'}
        and order.shopify_sync_status in {None,'NOT_SYNCED','SYNCED'}
        and order.shopify_lifecycle_status!='RECONCILIATION_REQUIRED')

def today():return datetime.now(TZ).date()
def stamp(v):return v.isoformat() if v else None

def authorize(user):
    if not settings.SALES_PIPELINE_ENABLED:raise HTTPException(404,'Sales Pipeline is disabled')
    if not user.is_active or not allowed(user,'whatsapp'):raise HTTPException(403,'WhatsApp Inbox access required')

def manageable(db,u,c):
    if not access.permissions(db,u,c)['can_manage']:raise HTTPException(403,'Conversation owner or administrator required')

def stage(o,lead):return STAGES.get(lead.status,'CLOSED') if o.active_conversation_id else o.data.get('stage','CLOSED')

def parsed_date(value):
    if value in (None,''):return None
    try:return date.fromisoformat(value).isoformat()
    except (ValueError,TypeError):raise HTTPException(422,'Use a valid calendar date') from None

def money(v):
    if v in (None,''):return None
    try:
        d=Decimal(str(v))
        if not d.is_finite() or d<0 or d>Decimal('999999999999.99') or d!=d.quantize(Decimal('.01')):raise ValueError()
        return str(d.quantize(Decimal('.01')))
    except (InvalidOperation,ValueError,TypeError):raise HTTPException(422,'Use a nonnegative amount with at most two decimal places') from None

def config(db):
    row=db.get(Control,1)
    return {**DEFAULTS,**(row.data if row else {})}

def schedule(db,o,c):
    db.query(R).filter_by(opportunity_id=o.id,state='PENDING').update({'state':'SUPERSEDED'},synchronize_session=False)
    lead=db.get(Lead,o.lead_id);s=stage(o,lead)
    if s in {'CLOSED','CONFIRMED'} or not c.assigned_to_user_id:return
    conf=config(db)
    for field,offsets in [('payment_date',conf['payment_offsets'] if s=='PAYMENT' else []),('purchase_date',conf['purchase_offsets'] if s=='FUTURE' else []),('followup_date',[0])]:
        if not o.data.get(field):continue
        for offset in offsets:
            d=date.fromisoformat(o.data[field])+timedelta(days=offset)
            if d<today():continue
            when=datetime.combine(d,time(conf['hour'],conf['minute']),TZ).astimezone(timezone.utc).replace(tzinfo=None)
            db.add(R(opportunity_id=o.id,version=o.version,user_id=c.assigned_to_user_id,kind=f'{field}:{offset}',due_at=when))

def ensure(db,lead,actor=None,*,new_intent=False):
    if not lead.whatsapp_conversation_id or lead.status not in STAGES:return None
    c=db.query(C).filter_by(id=lead.whatsapp_conversation_id).with_for_update().one_or_none()
    o=db.query(O).filter_by(active_conversation_id=lead.whatsapp_conversation_id).one_or_none()
    if o:return o
    if not c or lead.status=='INVALID_SPAM':return None
    o=O(created_at=datetime.utcnow() if new_intent else lead.created_at or c.created_at,conversation_id=c.id,active_conversation_id=c.id,lead_id=lead.id,version=0,data={'stage':STAGES[lead.status],'lead_status':lead.status,'priority':'MEDIUM','product':lead.product_interest or '', 'note':lead.notes or '', 'followup_date':stamp(lead.next_followup),'precision':'UNKNOWN'})
    o.followup_date=lead.next_followup
    db.add(o);db.flush();db.add(E(opportunity_id=o.id,actor_id=actor,previous={},current=o.data,key='create:'+str(uuid4())))
    schedule(db,o,c);return o

def scope(db,u):
    ids=access.scope(db.query(C.id),u).subquery()
    return db.query(O).filter(O.conversation_id.in_(select(ids.c.id)))

def due_label(value,now=None):
    if not value:return 'Date not confirmed'
    n=(date.fromisoformat(value)-(now or today())).days
    return f'Overdue by {-n} days' if n<0 else 'Due today' if n==0 else 'Due tomorrow' if n==1 else f'Due in {n} days'

def batch(db,rows):
    cids={o.conversation_id for o in rows};lids={o.lead_id for o in rows}
    cs={c.id:c for c in db.query(C).filter(C.id.in_(cids or [-1]))};ls={l.id:l for l in db.query(Lead).filter(Lead.id.in_(lids or [-1]))}
    orders={v.id:v for v in db.query(SalesOrder).filter(SalesOrder.id.in_([o.data.get('order_id') for o in rows if o.data.get('order_id')] or [-1]))}
    users={u.id:u.username for u in db.query(User).filter(User.id.in_([c.assigned_to_user_id for c in cs.values()] or [-1]))}
    paid={(oid,currency):Decimal(str(total)) for oid,currency,total in db.query(SalesPayment.sales_order_id,SalesPayment.currency,func.sum(SalesPayment.amount)).filter(SalesPayment.sales_order_id.in_(list(orders) or [-1]),SalesPayment.verification_status=='VERIFIED').group_by(SalesPayment.sales_order_id,SalesPayment.currency)}
    phones=[c.phone_number for c in cs.values()]
    last=db.query(M.id.label('mid'),func.row_number().over(partition_by=M.phone_number,order_by=(M.created_at.desc(),M.id.desc())).label('position')).filter(M.phone_number.in_(phones or ['']),M.direction=='inbound').subquery()
    messages={m.phone_number:m.message_text or '' for m in db.query(M).join(last,M.id==last.c.mid).filter(last.c.position==1)}
    result=[]
    for o in rows:
        c=cs[o.conversation_id];l=ls[o.lead_id];d=dict(o.data);s=stage(o,l);order=orders.get(d.get('order_id'))
        # A status label alone is never evidence of authoritative order conversion.
        valid=bool(order and order.crm_conversation_id==c.id and convertible(order))
        if s=='CONFIRMED' and not valid:s='PAYMENT' if l.status=='PAID' else 'INTERESTED'
        linked=bool(order and order.crm_conversation_id==c.id)
        total=Decimal(str(order.grand_total)) if linked else Decimal(d['agreed']) if d.get('agreed') else None
        verified=paid.get((order.id,order.currency),Decimal(0)) if linked else None
        outstanding=max(Decimal(0),total-verified) if linked and order.currency=='LKR' else total if not order and total is not None else None
        result.append({**d,'id':o.id,'version':o.version,'conversation_id':c.id,'assignment_version':c.assignment_version,'lead_status':l.status if o.active_conversation_id else d.get('archived_lead_status',CANONICAL.get(s,'LOST')),'stage':s,'source_stage':STAGES.get(l.status,'CLOSED') if o.active_conversation_id else s,'label':LABELS[s],'name':c.customer_name or l.customer_name,'phone':c.phone_number,'assigned_id':c.assigned_to_user_id,'assigned':users.get(c.assigned_to_user_id,'Unassigned'),'message':messages.get(c.phone_number,''),'activity_at':(c.last_customer_message_at or c.last_message_at or o.created_at).replace(tzinfo=timezone.utc).astimezone(TZ).isoformat(),'created_at':o.created_at.replace(tzinfo=timezone.utc).astimezone(TZ).isoformat(),'verified':str(verified) if verified is not None else None,'outstanding':str(outstanding) if outstanding is not None else None,'total':str(total) if total is not None else None,'review_required':STAGES.get(l.status)=='CONFIRMED' and not valid,'order':{'id':order.id,'number':order.internal_order_number,'status':order.order_status,'payment':order.payment_status,'shopify':order.shopify_order_name if order.shopify_order_id else None,'customer_id':order.customer_id,'currency':order.currency,'confirmed':valid} if linked else None,'due_label':due_label(d.get('payment_date') if s=='PAYMENT' else d.get('purchase_date')),'current':o.active_conversation_id is not None})
    for value in result:
        value['status_label']=display_status(value['lead_status'],value['verified'])
        value['payment_due']=date_state(value.get('payment_date'))
        value['purchase_due']=date_state(value.get('purchase_date'))
        value['followup_due']=date_state(value.get('followup_date'))
    return result

def filtered(items,p,u,*,include_closed=False):
    now=today();week=now-timedelta(days=now.weekday());end=week+timedelta(days=6)
    for key in ['from','to']:
        if p.get(key):parsed_date(p[key])
    if p.get('from') and p.get('to') and p['from']>p['to']:raise HTTPException(422,'Invalid date range')
    if p.get('date_type','created_at') not in {'created_at','payment_date','purchase_date','followup_date'}:raise HTTPException(422,'Choose a date type')
    def keep(v):
        if p.get('stage') and v['stage']!=p['stage']:return False
        if not include_closed and not p.get('stage') and v['stage']=='CLOSED':return False
        if p.get('priority') and v.get('priority')!=p['priority']:return False
        staff=p.get('staff','')
        if staff=='mine' and v['assigned_id']!=u.id:return False
        if staff=='unassigned' and v['assigned_id'] is not None:return False
        if staff.isdigit() and v['assigned_id']!=int(staff):return False
        q=p.get('q','').casefold()
        if q and q not in ' '.join(str(v.get(k) or '') for k in ['name','phone','product','note','message','order']).casefold():return False
        mode=p.get('due','');field='purchase_date' if mode.startswith('future_') else 'followup_date' if mode.startswith('followup_') else 'payment_date'
        val=v.get(field);d=date.fromisoformat(val) if val else None
        if mode:
            if mode=='unknown' and v.get('precision','UNKNOWN')!='UNKNOWN':return False
            if mode.startswith('payment_') and v['stage']!='PAYMENT':return False
            if mode.startswith('future_') and v['stage']!='FUTURE':return False
            suffix=mode.split('_')[-1]
            if suffix=='overdue' and not (d and d<now):return False
            if suffix in {'today','tomorrow'} and d!=now+timedelta(days=suffix=='tomorrow'):return False
            if suffix=='week' and not(d and week<=d<=end):return False
            if suffix=='month' and not(d and d.year==now.year and d.month==now.month):return False
        dtype=p.get('date_type','created_at');value=str(v.get(dtype) or '')[:10]
        if p.get('from') and (not value or value<p['from']):return False
        if p.get('to') and (not value or value>p['to']):return False
        return True
    return [v for v in items if keep(v)]

def report(db,u,p,*,export=False):
    # Bounded, batched projection; explicit limit fails rather than silently truncating totals.
    query=scope(db,u)
    staff=p.get('staff','')
    if staff=='mine':query=query.filter(O.conversation_id.in_(db.query(C.id).filter(C.assigned_to_user_id==u.id)))
    elif staff=='unassigned':query=query.filter(O.conversation_id.in_(db.query(C.id).filter(C.assigned_to_user_id.is_(None))))
    elif staff.isdigit():query=query.filter(O.conversation_id.in_(db.query(C.id).filter(C.assigned_to_user_id==int(staff))))
    if p.get('priority'):query=query.filter(O.data['priority'].as_string()==p['priority'])
    date_column={'payment_date':O.payment_date,'purchase_date':O.purchase_date,'followup_date':O.followup_date,'created_at':O.created_at}.get(p.get('date_type','created_at'))
    if date_column is None:raise HTTPException(422,'Invalid date type')
    if p.get('from'):
        start=date.fromisoformat(parsed_date(p['from']))
        query=query.filter(date_column>=datetime.combine(start,time.min,TZ).astimezone(timezone.utc).replace(tzinfo=None)) if date_column is O.created_at else query.filter(date_column>=start)
    if p.get('to'):
        end=date.fromisoformat(parsed_date(p['to']))
        query=query.filter(date_column<datetime.combine(end+timedelta(days=1),time.min,TZ).astimezone(timezone.utc).replace(tzinfo=None)) if date_column is O.created_at else query.filter(date_column<=end)
    if p.get('conversation_id'):
        try:query=query.filter(O.conversation_id==int(p['conversation_id']))
        except ValueError:raise HTTPException(422,'Invalid conversation') from None
    rows=query.order_by(O.id).limit(10001).all()
    if len(rows)>10000:raise HTTPException(422,'Narrow the dataset: more than 10,000 visible opportunities; use a scoped reporting export')
    projected=batch(db,rows);items=filtered(projected,p,u);cohort=filtered(projected,p,u,include_closed=True);now=today();week=now-timedelta(days=now.weekday())
    def total(vals,key):return str(sum((Decimal(v[key]) for v in vals if v.get(key) is not None),Decimal(0)))
    pending=[v for v in items if v['stage']=='PAYMENT'];future=[v for v in items if v['stage']=='FUTURE']
    kpi={'open':sum(v['stage'] in {'NEW','INTERESTED','PAYMENT','FUTURE'} for v in items),'pending':len(pending),'pending_value':total(pending,'outstanding'),'pending_unknown':sum(v.get('outstanding') is None for v in pending),'week':sum(bool(v.get('payment_date') and week<=date.fromisoformat(v['payment_date'])<=week+timedelta(days=6)) for v in pending),'week_start':str(week),'week_end':str(week+timedelta(days=6)),'future':len(future),'future_value':total(future,'estimated'),'future_unknown':sum(v.get('estimated') is None for v in future)}
    sort_items(items,p.get('sort','default'),now)
    for value in items:value['can_edit']=bool(value['current'] and (u.role=='ADMIN' or value['assigned_id']==u.id))
    if export:return items
    counts={s:sum(v['stage']==s for v in items) for s in LABELS}
    offset=int(p.get('offset','0'));limit=50
    if offset<0:raise HTTPException(422,'Invalid page')
    limits={}
    for name in LABELS:
        try:limits[name]=int(p.get('limit_'+name,limit))
        except (ValueError,TypeError):raise HTTPException(422,'Invalid column page size') from None
        if not 1<=limits[name]<=1000:raise HTTPException(422,'Column page size must be between 1 and 1000')
    offsets={}
    for name in LABELS:
        try:offsets[name]=int(p.get('offset_'+name,offset))
        except (ValueError,TypeError):raise HTTPException(422,'Invalid column offset') from None
        if not 0<=offsets[name]<=100000:raise HTTPException(422,'Invalid column offset')
    board={s:[v for v in items if v['stage']==s][offsets[s]:offsets[s]+limits[s]] for s in LABELS}
    staff={}
    for v in items:
        entry=staff.setdefault(v['assigned'],{'count':0,'pending':'0','overdue_followups':0});entry['count']+=1
        if v['stage']=='PAYMENT' and v.get('outstanding') is not None:entry['pending']=str(Decimal(entry['pending'])+Decimal(v['outstanding']))
        entry['overdue_followups']+=bool(v.get('followup_date') and v['followup_date']<str(now))
    confirmed=[v for v in items if v['order'] and v['order']['confirmed']];unique={v['order']['id']:v for v in confirmed}
    expected={}
    for key,predicate in [('today',lambda d:d==now),('tomorrow',lambda d:d==now+timedelta(days=1)),('week',lambda d:week<=d<=week+timedelta(days=6)),('month',lambda d:d.year==now.year and d.month==now.month)]:
        expected[key]=total([v for v in pending if v.get('payment_date') and predicate(date.fromisoformat(v['payment_date']))],'outstanding')
    future_periods={}
    for v in future:
        if not v.get('purchase_date') or v.get('estimated') is None:continue
        dt=date.fromisoformat(v['purchase_date'])
        for period in [dt.strftime('%Y-%m'),str(dt.isocalendar().year)+'-W'+str(dt.isocalendar().week)]:future_periods[period]=str(Decimal(future_periods.get(period,'0'))+Decimal(v['estimated']))
    return {'columns':board,'counts':counts,'kpi':kpi,'offset':offset,'limit':limit,'limits':limits,'offsets':offsets,'report':{'expected_payments':expected,'potential_purchases_by_period':future_periods,'staff':staff,'confirmed_order_value':total([v for v in unique.values() if v['order']['currency']=='LKR'],'total'),'verified_received_applied':total([v for v in unique.values() if v['order']['currency']=='LKR'],'verified'),'overdue_expected':total([v for v in pending if v.get('payment_date') and v['payment_date']<str(now)],'outstanding'),'unknown_values':sum(v.get('total') is None and v.get('estimated') is None for v in items),'conversion':{'cohort':'Filtered opportunity creation dates and staff scope; closed/lost included unless a specific stage is selected; one purchase intent per opportunity','converted':sum(bool(v['order'] and v['order']['confirmed']) for v in cohort),'opportunities':len(cohort)}},'integration':{'status':'Configured — live delivery not verified' if settings.META_WHATSAPP_PHONE_NUMBER_ID and settings.META_WHATSAPP_ACCESS_TOKEN else 'Not configured'},'today':str(now)}

def update(db,u,oid,p,key):
    if not isinstance(p,dict) or set(p)-FIELDS-{'version','owner_id','assignment_version'}:raise HTTPException(422,'Unknown opportunity field')
    if not key or len(key)>80:raise HTTPException(422,'Idempotency key required')
    candidate=scope(db,u).filter(O.id==oid).one_or_none()
    if not candidate:raise HTTPException(404,'Opportunity not found')
    db.query(C).filter_by(id=candidate.conversation_id).populate_existing().with_for_update().one()
    c=access.require_visible(db,u,candidate.conversation_id);manageable(db,u,c)
    o=scope(db,u).filter(O.id==oid).populate_existing().with_for_update().one_or_none()
    if not o:raise HTTPException(404,'Opportunity not found')
    if not o.active_conversation_id:raise HTTPException(409,'Historical opportunity cannot update the current conversation')
    prior=db.query(E).filter_by(key=key).one_or_none()
    requested={k:v for k,v in p.items() if k!='version'}
    if prior:
        if prior.opportunity_id!=oid or prior.current.get('_request')!=requested:raise HTTPException(409,'Idempotency key already used')
        return o
    if type(p.get('version')) is not int or p['version']!=o.version:raise HTTPException(409,'Opportunity changed. Refresh and review.')
    old=dict(o.data);d={**old,**{k:v for k,v in p.items() if k in FIELDS}}
    if old.get('order_id') is not None and d.get('order_id')!=old['order_id']:raise HTTPException(409,'Existing order associations require a separately approved correction workflow')
    if not isinstance(d.get('stage'),str) or d.get('stage') not in LABELS:raise HTTPException(422,'Choose a pipeline stage')
    if not isinstance(d.get('priority','MEDIUM'),str) or d.get('priority','MEDIUM') not in {'HIGH','MEDIUM','LOW'}:raise HTTPException(422,'Choose a priority')
    for k in ['product','note','closed_reason']:
        if d.get(k) is not None and (not isinstance(d[k],str) or len(d[k])>(2000 if k=='note' else 255)):raise HTTPException(422,'Text exceeds allowed size')
    for k in ['payment_date','purchase_date','followup_date','range_start','range_end']:d[k]=parsed_date(d.get(k))
    for k in ['estimated','agreed']:d[k]=money(d.get(k))
    if not isinstance(d.get('precision','UNKNOWN'),str) or d.get('precision','UNKNOWN') not in {'UNKNOWN','EXACT','RANGE'}:raise HTTPException(422,'Invalid date precision')
    if d.get('precision')=='RANGE':
        if not d.get('range_start') or not d.get('range_end') or d['range_start']>d['range_end']:raise HTTPException(422,'Supply a valid approximate date range')
        d['payment_date']=d['purchase_date']=None
    if d.get('precision')=='UNKNOWN':d['payment_date']=d['purchase_date']=None
    if d.get('order_id') is not None:
        linked=db.get(SalesOrder,d['order_id']) if type(d['order_id']) is int else None
        if not linked or linked.crm_conversation_id!=c.id:raise HTTPException(409,'Order/conversation identity mismatch')
        if db.query(O.id).filter(O.order_id==linked.id,O.id!=o.id).first():raise HTTPException(409,'Order already linked to another purchase intent')
    if d.get('precision')=='EXACT' and ((d['stage']=='PAYMENT' and not d.get('payment_date')) or (d['stage']=='FUTURE' and not d.get('purchase_date'))):raise HTTPException(422,'Supply the exact promised date or choose Date Not Confirmed')
    if d.get('stage')=='CLOSED' and not d.get('closed_reason'):d['closed_reason']='Staff marked lost / closed'
    if d.get('stage')=='CONFIRMED':
        order=db.get(SalesOrder,d.get('order_id')) if type(d.get('order_id')) is int else None
        if not order or order.crm_conversation_id!=c.id or not convertible(order):raise HTTPException(409,'Link an authoritative confirmed CRM order for this conversation; use existing Create Order and reconciliation')
    if 'owner_id' in p:
        if u.role!='ADMIN':raise HTTPException(403,'Administrator required for reassignment')
        # Existing assignment service commits; reject combining it with commitment edits.
        raise HTTPException(422,'Use the existing Inbox assignment control; it preserves assignment audits and notifications')
    d['closed_at']=datetime.utcnow().isoformat() if d['stage'] in {'CLOSED','CONFIRMED'} else None
    lead=db.get(Lead,o.lead_id)
    if STAGES.get(lead.status)!=d['stage']:lead.status=CANONICAL[d['stage']]
    d['lead_status']=lead.status
    lead.product_interest=d.get('product');lead.next_followup=date.fromisoformat(d['followup_date']) if d.get('followup_date') else None
    if old.get('note')!=d.get('note') and d.get('note'):db.add(LeadNote(lead_id=lead.id,note=d['note']))
    changed=db.query(O).filter_by(id=oid,version=p['version']).update({'data':d,'order_id':d.get('order_id'),**{k:date.fromisoformat(d[k]) if d.get(k) else None for k in ['payment_date','purchase_date','followup_date']},**{k:Decimal(d[k]) if d.get(k) is not None else None for k in ['estimated','agreed']},'version':p['version']+1,'updated_at':datetime.utcnow()},synchronize_session=False)
    if not changed:raise HTTPException(409,'Concurrent update; refresh')
    db.flush();db.refresh(o);db.add(E(opportunity_id=oid,actor_id=u.id,previous=old,current={**d,'_request':requested},key=key));schedule(db,o,c)
    return o

def preview(db):
    linked={cid for cid, in db.query(O.active_conversation_id).filter(O.active_conversation_id.isnot(None))}
    rows=db.query(Lead).filter(Lead.whatsapp_conversation_id.isnot(None)).order_by(Lead.id).all()
    distribution={};proposed=[];unmapped=[];excluded=0
    order_candidates={}
    for order in db.query(SalesOrder).filter(SalesOrder.crm_conversation_id.in_([v.whatsapp_conversation_id for v in rows] or [-1])):
        order_candidates.setdefault(order.crm_conversation_id,[]).append({'order_id':order.id,'customer_id':order.customer_id,'status':order.order_status})
    for l in rows:
        distribution[l.status]=distribution.get(l.status,0)+1
        if l.status=='INVALID_SPAM':excluded+=1;continue
        if l.status not in STAGES:unmapped.append(l.id);continue
        if l.whatsapp_conversation_id not in linked:proposed.append({'lead_id':l.id,'conversation_id':l.whatsapp_conversation_id,'stage':STAGES[l.status],'inquiry_at':stamp(l.created_at),'followup_date':stamp(l.next_followup),'commitment_date':None,'order_candidates':order_candidates.get(l.whatsapp_conversation_id,[]),'order_association':'Manual verification required; not auto-linked','customer_association':'Not inferred','fingerprint':hashlib.sha256(json.dumps([l.status,l.product_interest,l.notes,stamp(l.next_followup)],default=str).encode()).hexdigest()})
    body={'excluded_non_sales':excluded,'distribution':distribution,'proposed':proposed,'unmapped_lead_ids':unmapped,'missing_commitment_dates':len(proposed),'unorganized_conversations':db.query(C).filter(~db.query(Lead.id).filter(Lead.whatsapp_conversation_id==C.id).exists()).count(),'unlinked_legacy_leads':db.query(Lead).filter(Lead.whatsapp_conversation_id.is_(None)).count(),'orders_created':False,'customers_created':False}
    return {**body,'digest':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()}

def scan(db,now=None):
    if not settings.SALES_PIPELINE_ENABLED or not settings.SALES_PIPELINE_REMINDERS_ENABLED:return
    import staff_notifications as notices
    now=now or datetime.utcnow()
    for r in db.query(R).filter(R.state=='PENDING',R.due_at<=now).order_by(R.id).with_for_update(skip_locked=True).limit(100):
        o=db.get(O,r.opportunity_id);c=db.get(C,o.conversation_id);l=db.get(Lead,o.lead_id)
        if o.version!=r.version or not o.active_conversation_id or stage(o,l) in {'CLOSED','CONFIRMED'} or c.assigned_to_user_id!=r.user_id:r.state='SUPERSEDED';continue
        notices.enqueue(db,c,[r.user_id],'REMINDER',f'pipeline:{o.id}:{r.id}')
        r.state='SENT'

@event.listens_for(Session,'before_commit')
def synchronize_lead_commit(db):
    if not settings.SALES_PIPELINE_ENABLED or db.info.get('pipeline_sync'):return
    changed=set(db.new)|set(db.dirty)|db.info.pop('pipeline_changed',set())
    orders=[v for v in changed if isinstance(v,SalesOrder)]
    leads=[l for l in changed if isinstance(l,Lead)]
    if not leads and not orders:return
    db.info['pipeline_sync']=True
    try:
        db.flush()
        for order in orders:
            if not convertible(order):continue
            for linked in db.query(O).filter(O.active_conversation_id==order.crm_conversation_id):
                if linked.data.get('order_id')!=order.id:continue
                lead=db.get(Lead,linked.lead_id);lead.status='ORDER_CONFIRMED';leads.append(lead)
        for lead in leads:
            o=ensure(db,lead)
            if not o:continue
            new=STAGES.get(lead.status,'CLOSED')
            if o.data.get('stage')==new and o.data.get('lead_status')==lead.status and o.data.get('product')==(lead.product_interest or '') and o.data.get('followup_date')==stamp(lead.next_followup):continue
            old=dict(o.data);o.data={**old,'stage':new,'lead_status':lead.status,'product':lead.product_interest or '', 'followup_date':stamp(lead.next_followup)};o.followup_date=lead.next_followup;o.version+=1;o.updated_at=datetime.utcnow()
            db.add(E(opportunity_id=o.id,actor_id=getattr(db.info.get('inbox_user'),'id',None),previous=old,current=o.data,key='inbox:'+str(uuid4())))
            schedule(db,o,db.get(C,o.conversation_id))
    finally:db.info.pop('pipeline_sync',None)


def reassigned(db,c,actor_id):
    if not settings.SALES_PIPELINE_ENABLED:return
    o=db.query(O).filter_by(active_conversation_id=c.id).with_for_update().one_or_none()
    if not o:return
    o.version+=1
    db.add(E(opportunity_id=o.id,actor_id=actor_id,previous={'assignment':'See canonical assignment audit'},current={'owner_id':c.assigned_to_user_id},key='assignment:'+str(uuid4())))
    schedule(db,o,c)


@event.listens_for(Session,'before_flush')
def remember_flushed_sources(db,context,instances):
    if settings.SALES_PIPELINE_ENABLED and not db.info.get('pipeline_sync'):
        db.info.setdefault('pipeline_changed',set()).update(v for v in set(db.new)|set(db.dirty) if isinstance(v,(Lead,SalesOrder)))

@event.listens_for(Session,'after_rollback')
@event.listens_for(Session,'after_commit')
def clear_sources(db):
    db.info.pop('pipeline_changed',None)


def date_state(value,now=None):
    if not value:return {'kind':'unknown','label':'Date Not Confirmed'}
    n=(date.fromisoformat(value)-(now or today())).days
    kind='overdue' if n<0 else 'today' if n==0 else 'tomorrow' if n==1 else 'upcoming'
    label=f'Overdue by {-n} day'+('s' if n!=-1 else '') if n<0 else 'Due today' if n==0 else 'Due tomorrow' if n==1 else f'Due in {n} days'
    return {'kind':kind,'label':label}


def display_status(status,verified):
    if status=='PAID' and (verified is None or Decimal(str(verified))<=0):return 'Paid — Verification Required'
    return {'NEW':'New Inquiry','FOLLOW_UP':'Follow-up','OUT_OF_STOCK':'Out of Stock','FUTURE_BUYER':'Future Buyer','ORDER_CONFIRMED':'Order Confirmed'}.get(status,status.replace('_',' ').title())


def sort_items(items,mode,now=None):
    now=now or today()
    valid={'default','latest','oldest','payment','purchase','followup','overdue','priority','value','unassigned'}
    if mode not in valid:raise HTTPException(422,'Choose a supported sort order')
    def dt(v,key):return (not bool(v.get(key)),v.get(key) or '9999-12-31')
    def key(v):
        tie=v['id']
        if mode in {'latest','oldest'}:
            value=datetime.fromisoformat(v['activity_at']).timestamp()
            return (-value if mode=='latest' else value,tie)
        if mode in {'payment','purchase','followup'}:return (*dt(v,mode+'_date'),tie)
        if mode=='priority':return ({'HIGH':0,'MEDIUM':1,'LOW':2}.get(v.get('priority'),3),tie)
        if mode=='unassigned':return (v.get('assigned_id') is not None,tie)
        if mode=='value':
            amount=v.get('total') if v.get('total') is not None else v.get('estimated')
            return (amount is None,-Decimal(amount) if amount is not None else Decimal(0),tie)
        if mode=='overdue':
            fields=['followup_date']+(['payment_date'] if v['stage']=='PAYMENT' else ['purchase_date'] if v['stage']=='FUTURE' else [])
            dates=[v[k] for k in fields if v.get(k)]
            earliest=min(dates) if dates else None
            return (not bool(earliest and earliest<str(now)),earliest or '9999-12-31',tie)
        field='payment_date' if v['stage']=='PAYMENT' else 'purchase_date' if v['stage']=='FUTURE' else None
        if field:return (*dt(v,field),tie)
        return (-datetime.fromisoformat(v['activity_at']).timestamp(),tie)
    items.sort(key=key)
