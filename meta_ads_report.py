"""Evidence-only attribution. Existing CRM rows are read, never modified."""
from collections import defaultdict, Counter
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import re
from fastapi import HTTPException
from models import (WhatsAppMessageReferral as Referral, WhatsAppConversationMessage as Message,
                    WhatsAppConversation as Conversation, CRMAnalyticsPhone as Phone, Lead, SalesOrder, SalesPayment)
from meta_ads_models import MetaAdsDaily, MetaAdsSync
from meta_ads_client import configuration, account_id, AdsError, enabled

WINDOW = timedelta(days=30)
CONFIRMED = {'CONFIRMED','PROCESSING','DISPATCHED','DELIVERED'}
MODEL = 'Last recorded ad referral within 30 days before order creation; order-date reporting. CRM activity uses its latest preceding referral within 30 days.'


def iso(value): return value.isoformat() if value else None


def period(start='', end='', zone='Asia/Colombo'):
    today = datetime.now(ZoneInfo(zone)).date()
    try: a = date.fromisoformat(start) if start else today-timedelta(days=29); b = date.fromisoformat(end) if end else today
    except ValueError: raise HTTPException(422, 'Use ISO reporting dates') from None
    if b < a or (b-a).days > 365 or b > today: raise HTTPException(422, 'Choose up to 366 days ending today or earlier')
    tz = ZoneInfo(zone)
    utc = lambda d: datetime.combine(d,time(),tz).astimezone(timezone.utc).replace(tzinfo=None)
    return a,b,utc(a),utc(b+timedelta(days=1))


def ratio(a,b,percent=False):
    return round(float(a/b)*(100 if percent else 1), 4) if a is not None and b else None


def report(db, start='', end='', campaign='', adset='', ad='', interest='', status='', level='ad'):
    if level not in {'campaign','adset','ad'}: raise HTTPException(422,'Invalid reporting level')
    if level=='campaign' and (adset or ad) or level=='adset' and ad:
        raise HTTPException(422,'Choose Advertisement reporting for ad filters, or Ad set reporting for ad set filters')
    account = account_id()
    try: configuration(); configured = True
    except AdsError: configured = False
    sync = db.get(MetaAdsSync, account) if account else None
    zone = sync.timezone_name if sync and sync.timezone_name else 'Asia/Colombo'
    a,b,lo,hi = period(start,end,zone)
    facts = db.query(MetaAdsDaily).filter(MetaAdsDaily.account_id == account,
        MetaAdsDaily.level == level, MetaAdsDaily.report_date.between(a,b)).all() if account else []
    facts = [f for f in facts if (not campaign or f.campaign_id==campaign) and (not adset or f.adset_id==adset) and (not ad or f.ad_id==ad)]
    # Identity cache deliberately keeps unresolved identities unattributed, never guesses by name.
    identities = {p.raw_phone:p.canonical_phone for p in db.query(Phone)}
    from crm_analytics_tracking import identity as normalize_identity
    def canonical(raw):
        # Orders can retain a formatting variant absent from the message identity cache.
        # Reuse the existing pure normalizer; never write identity records from a report.
        if raw in identities:return identities[raw]
        return normalize_identity(raw)['canonical_phone'] if isinstance(raw,str) else None
    conversations = {c.id:c for c in db.query(Conversation)}
    by_phone = defaultdict(list)
    for c in conversations.values():
        if canonical(c.phone_number): by_phone[canonical(c.phone_number)].append(c)
    leads = {l.id:l for l in db.query(Lead)}
    by_conversation = {l.whatsapp_conversation_id:l for l in leads.values() if l.whatsapp_conversation_id}
    events = defaultdict(list); previews = {}; unresolved_referrals = 0
    seen = set()
    for ref,msg in db.query(Referral,Message).join(Message,Referral.message_id==Message.id).filter(
            Message.direction=='inbound', Message.created_at>=lo-WINDOW, Message.created_at<hi).order_by(Message.created_at,Message.id):
        if (ref.source_type or '').lower()!='ad' or not re.fullmatch(r'[0-9]{1,40}',ref.source_id or ''): continue
        key = msg.whatsapp_message_id or 'local:'+str(msg.id)
        if key in seen: continue
        seen.add(key); phone=canonical(msg.phone_number)
        if phone: events[phone].append((msg.created_at,ref.source_id,msg.id))
        elif lo<=msg.created_at<hi: unresolved_referrals += 1
        previews[ref.source_id]={'headline':ref.headline,'conversation_id':next((c.id for c in by_phone.get(phone,[]) if c.phone_number==msg.phone_number),None),'message_id':msg.id,'referral_id':ref.id}
    def touch(phone, at):
        matches=[e for e in events.get(phone,[]) if at-WINDOW<=e[0]<=at]
        if not matches: return None
        latest=max(e[0] for e in matches); ids={e[1] for e in matches if e[0]==latest}
        return next(iter(ids)) if len(ids)==1 else None
    all_ad_facts = db.query(MetaAdsDaily).filter(MetaAdsDaily.account_id==account,MetaAdsDaily.level=='ad',MetaAdsDaily.report_date<=b).order_by(MetaAdsDaily.report_date).all() if account else []
    hierarchy = {f.ad_id:(f.campaign_id,f.adset_id) for f in all_ad_facts}
    def group(ad_id):
        if not ad_id: return None
        cam,aset=hierarchy.get(ad_id,(None,None))
        if ad and ad_id!=ad or campaign and cam!=campaign or adset and aset!=adset: return False
        return ad_id if level=='ad' else cam if level=='campaign' else aset
    def lead_for(phone):
        linked=[by_conversation[c.id] for c in by_phone.get(phone,[]) if c.id in by_conversation]
        return linked[0] if len({l.id for l in linked})==1 else None
    def matches(lead):
        return (not interest or lead and interest.casefold() in (lead.product_interest or '').casefold()) and (not status or lead and lead.status==status)
    fact_currencies={f.currency for f in facts}
    report_currency=next(iter(fact_currencies)) if len(fact_currencies)==1 else sync.currency if sync else None
    facts_by_entity=defaultdict(list)
    for fact in facts:facts_by_entity[fact.entity_id].append(fact)
    rows={}
    def entry(key):
        if key not in rows:
            rows[key]={'id':key,'name':key,'spend':None,'impressions':None,'reach':None,'link_clicks':None,'meta_messaging':None,
                'leads':set(),'starts':set(),'inbound':set(),'staff_replies':set(),'interested':set(),'order_requested':set(),
                'orders':[],'revenue':Decimal(0),'revenue_by_currency':defaultdict(Decimal),'currencies':set(), 'daily':[], 'inquiries':{},
                'lead_statuses':Counter(),'verified_orders':0,'pending_orders':0,'excluded_orders':0,'preview':previews.get(key)}
        return rows[key]
    for f in facts:
        row=entry(f.entity_id); row['name']=getattr(f,level+'_name') or f.entity_id
        row['campaign_id']=f.campaign_id;row['campaign_name']=f.campaign_name;row['adset_id']=f.adset_id;row['adset_name']=f.adset_name
        row['daily'].append({'date':iso(f.report_date),'spend':float(f.spend) if f.spend is not None else None,'reach':f.reach})
        row['currencies'].add(f.currency)
    for key,row in rows.items():
        fs=facts_by_entity[key]
        for output,attr in [('spend','spend'),('impressions','impressions'),('link_clicks','link_clicks'),('meta_messaging','messaging')]:
            vals=[getattr(f,attr) for f in fs]
            row[output]=sum(vals) if vals and all(v is not None for v in vals) else None
        # Reach is unique people, not additive across dates.
        row['reach']=fs[0].reach if a==b and len(fs)==1 else None
        latest=max(fs,key=lambda f:f.synced_at)
        row['delivery_status']=latest.delivery_status
        row['status_as_of']=iso(latest.synced_at)
    for phone,es in events.items():
        lead=lead_for(phone)
        if not matches(lead):continue
        for when,aid,mid in es:
            if not lo<=when<hi:continue
            key=group(aid)
            if not key:continue
            row=entry(key);row['leads'].add(phone)
            # A referral event is not proof of a new WhatsApp session.
            row['starts'].add(mid)
            convs=by_phone.get(phone,[])
            row['inquiries'][phone]={'conversation_id':convs[0].id if len(convs)==1 else None,
                'customer_name':lead.customer_name if lead else convs[0].customer_name if len(convs)==1 else 'WhatsApp contact',
                'lead_id':lead.id if lead else None,'status':lead.status if lead else None}
            if lead and lead.status=='INTERESTED':row['interested'].add(phone)
            if lead and lead.status=='ORDER_REQUESTED':row['order_requested'].add(phone)
    for msg in db.query(Message).filter(Message.created_at>=lo,Message.created_at<hi):
        phone=canonical(msg.phone_number)
        if not phone or not matches(lead_for(phone)):continue
        key=group(touch(phone,msg.created_at))
        if not key:continue
        row=entry(key);identity=msg.whatsapp_message_id or 'local:'+str(msg.id)
        if msg.direction=='inbound':row['inbound'].add(identity)
        elif msg.direction=='outbound' and (msg.response_kind or '').startswith('staff') and msg.whatsapp_message_id:
            row['staff_replies'].add(identity)
    payments=defaultdict(list)
    order_query=db.query(SalesOrder).filter(SalesOrder.created_at>=lo,SalesOrder.created_at<hi)
    for p in db.query(SalesPayment).filter(SalesPayment.verification_status=='VERIFIED',SalesPayment.sales_order_id.in_(order_query.with_entities(SalesOrder.id))): payments[p.sales_order_id].append(p)
    unattributed=[]
    for order in order_query:
        conv=conversations.get(order.crm_conversation_id)
        lead=leads.get(order.crm_lead_id) or by_conversation.get(order.crm_conversation_id)
        if not matches(lead):continue
        # Explicit order/conversation link is required; disagreement is uncertain.
        phone=canonical(conv.phone_number) if conv else None
        aid=touch(phone,order.created_at) if phone and canonical(order.whatsapp_phone)==phone else None
        key=group(aid)
        if key is False:continue
        refunded=(order.shopify_total_refunded or 0)>0 or (order.shopify_financial_status or '').upper() in {'REFUNDED','PARTIALLY_REFUNDED','VOIDED'}
        excluded=order.order_status in {'CANCELLED','RETURNED'} or refunded
        verified=sum((p.amount for p in payments[order.id] if p.currency==order.currency and p.verified_at and p.verified_by),Decimal(0))
        fully_paid=not excluded and order.order_status in CONFIRMED and verified>=order.grand_total and order.grand_total>0
        detail={'id':order.id,'number':order.internal_order_number,'status':order.order_status,'payment_status':order.payment_status,
                'fulfillment_status':order.fulfillment_status,'currency':order.currency,'verified_revenue':float(order.grand_total) if fully_paid else None,
                'excluded':excluded,'customer_id':order.customer_id,'conversation_id':order.crm_conversation_id,'lead_id':order.crm_lead_id}
        if not key:unattributed.append(detail);continue
        row=entry(key);row['orders'].append(detail)
        if excluded:row['excluded_orders']+=1
        elif fully_paid:
            row['verified_orders']+=1;row['revenue_by_currency'][order.currency]+=order.grand_total
        elif order.order_status not in CONFIRMED:row['pending_orders']+=1
    for row in rows.values():
        currency=next(iter(row['currencies'])) if len(row['currencies'])==1 else report_currency
        row['currency']=currency
        # Currency changes are never summed.
        if len(row['currencies'])>1:row['spend']=None
        row['revenue']=row['revenue_by_currency'].get(currency,Decimal(0)) if currency else None
        row['foreign_revenue']={k:float(v) for k,v in row['revenue_by_currency'].items() if k!=currency}
        row.pop('revenue_by_currency');row.pop('currencies')
        row['lead_statuses']=dict(Counter(v['status'] or 'UNLINKED' for v in row['inquiries'].values()))
        row['inquiries']=list(row['inquiries'].values())
        for name in ('leads','starts','inbound','staff_replies','interested','order_requested'):row[name]=len(row[name])
        row['confirmed_orders']=sum(1 for o in row['orders'] if o['status'] in CONFIRMED and not o['excluded'])
        row['attributed_conversations']=len({q['conversation_id'] for q in row['inquiries'] if q['conversation_id']})
        row['cpl']=ratio(row['spend'],row['leads']);row['cpo']=ratio(row['spend'],row['confirmed_orders'])
        row['conversion']=ratio(row['confirmed_orders'],row['leads'],True);row['roas']=ratio(row['revenue'],row['spend'])
        row['meta_cost_per_conversation']=ratio(row['spend'],row['meta_messaging'])
        for k,v in row.items():
            if isinstance(v,Decimal):row[k]=float(v)
    data=list(rows.values())
    # Lead totals must de-duplicate across ads; recover union from inquiry identity keys via event matching.
    unique=set()
    for phone,es in events.items():
        if matches(lead_for(phone)) and any(lo<=e[0]<hi and group(e[1]) for e in es):unique.add(phone)
    def official_total(field):
        values=[getattr(f,field) for f in facts]
        if len({f.currency for f in facts})>1:return None
        return float(sum(values)) if values and all(v is not None for v in values) else None
    summary={'spend':official_total('spend'),'meta_messaging':official_total('messaging'),'leads':len(unique),
        'confirmed_orders':sum(r['confirmed_orders'] for r in data),'verified_orders':sum(r['verified_orders'] for r in data),
        'revenue':sum(r['revenue'] for r in data if r['currency']==report_currency and r['revenue'] is not None) if report_currency else None,'currency':report_currency,'unattributed_orders':len(unattributed),'unresolved_referral_events':unresolved_referrals}
    if len({f.currency for f in facts})>1:summary['spend']=summary['revenue']=None
    summary.update(cpl=ratio(summary['spend'],summary['leads']),cpo=ratio(summary['spend'],summary['confirmed_orders']),
        conversion=ratio(summary['confirmed_orders'],summary['leads'],True),roas=ratio(summary['revenue'],summary['spend']))
    return {'summary':summary,'rows':data,'unattributed':unattributed,'period':{'from':iso(a),'to':iso(b),'timezone':zone},
        'integration':{'configured':configured,'enabled':enabled(),'account_id':account,'currency':sync.currency if sync else None,
            'status':sync.status if sync else 'NOT_CONFIGURED' if not account else 'NEVER','error':sync.error_code if sync else None,
            'last_success':iso(sync.last_success_at) if sync else None,'stale':not sync or not sync.last_success_at or sync.last_success_at<datetime.utcnow()-timedelta(hours=26),
            'coverage_complete':bool(sync and sync.date_from and sync.date_to and a>=sync.date_from and b<=sync.date_to),
            'coverage_from':iso(sync.date_from) if sync else None,'coverage_to':iso(sync.date_to) if sync else None},
        'filters':{'campaign':list({f.campaign_id:{'id':f.campaign_id,'name':f.campaign_name or f.campaign_id} for f in all_ad_facts if f.campaign_id}.values()),
                   'adset':list({f.adset_id:{'id':f.adset_id,'name':f.adset_name or f.adset_id} for f in all_ad_facts if f.adset_id}.values()),
                   'ad':list({**{f.ad_id:{'id':f.ad_id,'name':f.ad_name or f.ad_id} for f in all_ad_facts if f.ad_id},**{k:{'id':k,'name':v['headline'] or k} for k,v in previews.items() if k not in hierarchy}}.values())},
        'attribution':MODEL,'crm_filters_applied':bool(interest or status),
        'limitations':['Reach is available for a single reporting day only; daily reach cannot be summed.',
            'Product interest and lead status filter CRM evidence only; Meta spend remains the full selected ads spend.',
            'Leads use referral dates; orders use order creation dates. Conversion is period activity, not a closed cohort and may exceed 100%.',
            'Verified revenue requires full verified same-currency payment evidence; pending, cancelled, returned and known refunded orders contribute no revenue.',
            'Missing metrics remain unavailable. Referral-free conversations and unresolved identities cannot be attributed. ROAS is not profit.',
            'Lead statuses are current. Ad delivery status is a snapshot at synchronization, not historical daily status. Missing metadata is unavailable. Historical source coverage is limited to retained records.']}
