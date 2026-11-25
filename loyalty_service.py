"""Canonical loyalty eligibility and decimal pricing. No external API or commerce writes."""
from __future__ import annotations
import copy,hashlib,json,re
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func
from models import Customer,SalesOrder,SalesPayment,User
from retail_models import RetailVariant
from loyalty_models import *
from marketing_identity import phone
from settings import settings

MONEY=Decimal('.01')
DEFAULTS={'name':'PixiePinks Loyalty Club','minimum_purchase':'0.01','minimum_margin':'20.00','missing_cost_policy':'BLOCK','combination_policy':'LOWER_PRICE_NO_STACK','default_days':30,'channels':['CRM']}
TEST_REFERENCES={'POS-851C4716B015DF1B0E79','POS-5AB126C311C38CDBD5FA'}


def fail(message,status=422):raise HTTPException(status,message)
def money(value,minimum=Decimal('0'),maximum=Decimal('999999999999.99')):
    try:v=Decimal(str(value)).quantize(MONEY,rounding=ROUND_HALF_UP)
    except (InvalidOperation,ValueError,TypeError):fail('Enter a valid monetary amount')
    if not v.is_finite() or not minimum<=v<=maximum:fail('Monetary amount outside permitted range')
    return v

def quantity(value):
    try:v=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):fail('Invalid quantity')
    if not v.is_finite() or v<=0 or v>Decimal('999999999.999') or v!=v.quantize(Decimal('.001')):fail('Invalid quantity')
    return v

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def serial(value):return json.loads(json.dumps(value,default=str))
def reason(value):
    if not isinstance(value,str) or not 3<=len(value.strip())<=2000:fail('A meaningful management reason is required')
    return value.strip()
def stamp(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(timezone.utc).replace(tzinfo=None) if datetime.fromisoformat(str(value).replace('Z','+00:00')).tzinfo else datetime.fromisoformat(str(value))
    except (ValueError,TypeError):fail('Use an ISO date and time, with timezone where applicable')
def audit(db,kind,resource,actor,previous=None,value=None,why=None):
    db.add(LoyaltyAudit(kind=kind,resource=str(resource),actor=str(actor),previous=serial(previous) if previous is not None else None,value=serial(value) if value is not None else None,reason=why))
def config(db,lock=False):
    query=db.query(LoyaltyConfig).filter_by(id=1)
    return (query.with_for_update() if lock else query).one_or_none()
def rules_config(db):
    c=config(db);return {**DEFAULTS,**(c.value if c else {})}

def update_config(db,user,payload):
    c=config(db,True)
    if c is None:c=LoyaltyConfig(id=1,enabled=False,version=1,value=DEFAULTS);db.add(c);db.flush()
    if payload.get('version')!=c.version:fail('Settings changed. Refresh before saving.',409)
    previous={'enabled':c.enabled,**c.value}
    values={**c.value,**{k:payload[k] for k in DEFAULTS if k in payload}}
    values['minimum_purchase']=str(money(values['minimum_purchase'],MONEY))
    values['minimum_margin']=str(money(values['minimum_margin'],Decimal('0'),Decimal('100')))
    if values['missing_cost_policy']!='BLOCK' or values['combination_policy']!='LOWER_PRICE_NO_STACK' or values['channels']!=['CRM']:fail('Phase 1/2 permits CRM only, missing-cost blocking and no stacking')
    if not isinstance(values['default_days'],int) or not 1<=values['default_days']<=366:fail('Offer validity must be 1–366 days')
    if not isinstance(values['name'],str) or not 1<=len(values['name'])<=100:fail('Invalid programme name')
    if 'enabled' in payload and type(payload['enabled']) is not bool:fail('Enabled must be a boolean')
    c.value=values;c.enabled=payload.get('enabled',c.enabled);c.version+=1
    audit(db,'PROGRAMME_SETTINGS','programme',user.username,previous,{'enabled':c.enabled,**values},reason(payload.get('reason')))
    return c

def fingerprint(customer):return digest({'id':customer.id,'phone':phone(customer.phone),'name':customer.name})
def identity(db,cid,member=None):
    customer=db.get(Customer,cid)
    if customer is None:return None,'CUSTOMER_NOT_FOUND',[]
    normalized=phone(customer.phone)
    if not normalized:return customer,'MISSING_VALID_MOBILE',[]
    # Never resolve a customer by telephone; collisions require an explicit attestation.
    matches=[c.id for c in db.query(Customer).yield_per(200) if phone(c.phone)==normalized]
    if len(matches)>1 and (not member or member.identity_fingerprint!=fingerprint(customer)):
        return customer,'AMBIGUOUS_CUSTOMER_IDENTITY',matches
    return customer,None,matches

def order_customer(db,order):
    mapping=db.get(LoyaltyOrderIdentity,order.id)
    return mapping.customer_id if mapping else order.customer_id

def financial(db,order):
    """Fully paid means verified local receipts or matching imported Shopify evidence."""
    if order.currency!='LKR' or order.grand_total is None or order.grand_total<=0:return None,'INVALID_CURRENCY_OR_TOTAL'
    if db.get(LoyaltyOrderExclusion,order.id) is not None:return None,'TEST_ORDER'
    if order.internal_order_number in TEST_REFERENCES or (order.sales_channel or '').upper() in {'TEST','SYNTHETIC'} or str(order.idempotency_key).startswith(('test:','pos-test:')):return None,'TEST_ORDER'
    if order.order_status in {'CANCELLED','RETURNED'}:return None,'CANCELLED_OR_RETURNED'
    completed=order.order_status=='DELIVERED'
    if order.sales_channel=='POS':
        from pos_models import POSCheckout
        p=db.query(POSCheckout).filter_by(sales_order_id=order.id).one_or_none()
        completed=bool(p and p.status=='COMPLETE')
        if p and (p.frozen.get('test') or p.frozen.get('is_test')):return None,'TEST_ORDER'
    if not completed:return None,'NOT_COMPLETED'
    total=money(order.grand_total)
    local=money(db.query(func.coalesce(func.sum(SalesPayment.amount),0)).filter_by(sales_order_id=order.id,verification_status='VERIFIED',currency='LKR').scalar())
    remote=(order.shopify_payment_currency=='LKR' and order.shopify_payment_status_refreshed_at is not None and order.shopify_total_received is not None and order.shopify_total_outstanding==0 and order.shopify_total_received>=total and order.shopify_financial_status in {'PAID','PARTIALLY_REFUNDED','REFUNDED'})
    # POS completion has its own exact-order readback and verified accounting/payment contract.
    pos_paid=order.sales_channel=='POS' and completed and order.payment_status=='PAID' and order.amount_paid==total
    if order.payment_status not in {'PAID','PARTIALLY_REFUNDED','REFUNDED'} or not (local>=total or remote or pos_paid):return None,'PAYMENT_NOT_VERIFIED'
    refunded=money(order.shopify_total_refunded or 0)
    if order.shopify_financial_status in {'REFUNDED','PARTIALLY_REFUNDED'} and order.shopify_total_refunded is None:return None,'REFUND_EVIDENCE_UNAVAILABLE'
    if order.sales_channel=='POS':
        from pos_models import POSRefund
        refunds=db.query(POSRefund).join(POSCheckout,POSCheckout.id==POSRefund.checkout_id).filter(POSCheckout.sales_order_id==order.id,POSRefund.status.in_(['AWAITING_PAYOUT','PAID_OUT','COMPLETE'])).all()
        # Canonical POS refund plans are inspected explicitly, never assumed zero if unknown.
        local_refund=Decimal(0)
        for r in refunds:
            value=r.plan.get('amount')
            if value is None:return None,'REFUND_EVIDENCE_UNAVAILABLE'
            local_refund+=money(value)
        refunded=max(refunded,local_refund)
    if refunded>total:return None,'REFUND_TOTAL_INVALID'
    return {'paid':total,'refunded':refunded,'net':total-refunded},None

def history(db,cid,exclude_order=None,filters=None):
    selected=[];seen=set();duplicates=0;rejected=[];minimum=money(rules_config(db)['minimum_purchase'])
    mappings=[v.order_id for v in db.query(LoyaltyOrderIdentity.order_id).filter_by(customer_id=cid)]
    orders=db.query(SalesOrder).filter((SalesOrder.customer_id==cid)|(SalesOrder.id.in_(mappings or [-1]))).order_by(SalesOrder.created_at,SalesOrder.id)
    for order in orders:
        if order.id==exclude_order or order_customer(db,order)!=cid:continue
        if filters:
            if filters.get('start') and order.created_at<stamp(filters['start']):continue
            if filters.get('end') and order.created_at>=stamp(filters['end']):continue
            if filters.get('source') and (order.sales_channel or 'CRM')!=filters['source']:continue
        key=('shopify:'+order.shopify_order_id) if order.shopify_order_id else 'crm:'+str(order.id)
        if key in seen:duplicates+=1;continue
        data,error=financial(db,order)
        if error or data['net']<minimum:
            rejected.append({'order_id':order.id,'reason':error or 'NO_POSITIVE_NET_PURCHASE'});continue
        from models import SalesOrderStatusHistory
        completed=db.query(func.max(SalesOrderStatusHistory.created_at)).filter_by(sales_order_id=order.id,new_status='DELIVERED').scalar()
        if order.sales_channel=='POS':
            from pos_models import POSCheckout
            completed=db.query(POSCheckout.completed_at).filter_by(sales_order_id=order.id).scalar()
        seen.add(key);selected.append({'order_id':order.id,'reference':order.internal_order_number,'key':key,'paid':str(data['paid']),'refunded':str(data['refunded']),'net':str(data['net']),'date':completed.isoformat() if completed else None,'order_created_at':order.created_at.isoformat(),'channel':order.sales_channel or 'CRM'})
    from loyalty_recovery import remote_history
    selected.extend(remote_history(db,cid,seen,filters))
    selected.sort(key=lambda r:(r['date'] or r['order_created_at'],r['order_id'] or 0))
    return {'orders':selected,'count':len(selected),'net':str(sum((Decimal(o['net']) for o in selected),Decimal(0)).quantize(MONEY)),'duplicates':duplicates,'rejected':rejected}

def member_summary(db,cid,exclude_order=None):
    m=db.query(LoyaltyMember).filter_by(customer_id=cid).one_or_none()
    customer,error,candidates=identity(db,cid,m)
    h=history(db,cid,exclude_order) if customer else {'orders':[],'count':0,'net':'0.00','duplicates':0,'rejected':[]}
    status='SUSPENDED' if m and m.status=='SUSPENDED' else 'INELIGIBLE' if error else 'ACTIVE' if h['count'] else 'PENDING'
    return {'customer_id':cid,'name':customer.name if customer else None,'mobile':phone(customer.phone) if customer else None,'membership_id':m.membership_id if m else None,'member_id':m.id if m else None,'status':status,'reason':error or ('QUALIFYING_PREVIOUS_PURCHASE' if h['count'] else 'NO_QUALIFYING_PREVIOUS_PURCHASE'),'activated_at':m.activated_at.isoformat() if m and m.activated_at else None,'qualifying_count':h['count'],'lifetime_net':h['net'],'lifetime_savings':str(m.lifetime_savings) if m else '0.00','last_purchase_at':max((o['date'] for o in h['orders'] if o['date']),default=None),'recalculation_status':(db.get(LoyaltyRecalculation,cid).status if db.get(LoyaltyRecalculation,cid) else None),'history':h,'candidates':candidates}

def refresh_member(db,cid,actor='system:order',source='ORDER_EVENT'):
    customer=db.query(Customer).filter_by(id=cid).with_for_update().one_or_none()
    if customer is None:return None
    m=db.query(LoyaltyMember).filter_by(customer_id=cid).one_or_none()
    before=serial({'status':m.status,'count':m.qualifying_count,'net':m.lifetime_net}) if m else None
    summary=member_summary(db,cid)
    if m is None:
        m=LoyaltyMember(customer_id=cid,source=source,status='PENDING');db.add(m);db.flush()
    if m.status!='SUSPENDED':m.status=summary['status']
    m.qualifying_count=summary['qualifying_count'];m.lifetime_net=Decimal(summary['lifetime_net'])
    m.last_purchase_at=stamp(summary['last_purchase_at']) if summary['last_purchase_at'] else None
    if m.status=='ACTIVE' and not m.activated_at:
        m.activated_at=datetime.utcnow();m.first_order_id=summary['history']['orders'][0]['order_id']
    if summary['reason']=='AMBIGUOUS_CUSTOMER_IDENTITY':
        key=f'customer:{cid}:{fingerprint(customer)}'
        if not db.query(LoyaltyIdentityReview.id).filter_by(key=key).first():db.add(LoyaltyIdentityReview(key=key,customer_id=cid,candidates=summary['candidates'],reason=summary['reason']))
    m.registered_mobile=phone(customer.phone)
    m.updated_at=datetime.utcnow()
    after=serial({'status':m.status,'count':m.qualifying_count,'net':m.lifetime_net})
    if before!=after:audit(db,'MEMBERSHIP_RECALCULATED',m.membership_id,actor,before,after,summary['reason'])
    return m

def change_member(db,user,cid,action,why,notes=None,shopify_customer_id=None):
    config(db,True);m=refresh_member(db,cid,user.username)
    if m is None:fail('Customer not found',404)
    why=reason(why);before={'status':m.status,'notes':m.notes,'shopify_customer_id':m.shopify_customer_id}
    if action=='SUSPEND':m.status='SUSPENDED';m.suspension_reason=why
    elif action=='REINSTATE':
        m.status='PENDING';m.suspension_reason=None;db.flush();refresh_member(db,cid,user.username)
    elif action=='VERIFY_IDENTITY':
        m.identity_fingerprint=fingerprint(db.get(Customer,cid));db.flush();refresh_member(db,cid,user.username)
        for review in db.query(LoyaltyIdentityReview).filter_by(customer_id=cid,status='OPEN'):
            review.status='RESOLVED';review.resolution={'actor_id':user.id,'reason':why,'customer_id':cid,'no_merge':True}
        audit(db,'IDENTITY_RECONCILED',m.membership_id,user.username,None,{'fingerprint':m.identity_fingerprint,'customer_id':cid},why)
    elif action=='NOTES':
        if not isinstance(notes,str) or len(notes)>4000:fail('Notes must be at most 4000 characters')
        m.notes=notes
    elif action=='LINK_SHOPIFY_CUSTOMER':
        if not isinstance(shopify_customer_id,str) or not re.fullmatch(r'gid://shopify/Customer/[0-9]+',shopify_customer_id):fail('Invalid Shopify customer GID')
        other=db.query(LoyaltyMember.id).filter(LoyaltyMember.shopify_customer_id==shopify_customer_id,LoyaltyMember.id!=m.id).first()
        if other:fail('Shopify customer already linked to another internal customer',409)
        m.shopify_customer_id=shopify_customer_id
    else:fail('Unsupported membership action')
    audit(db,'MEMBERSHIP_'+action,m.membership_id,user.username,before,{'status':m.status,'notes':m.notes,'shopify_customer_id':m.shopify_customer_id},why)
    return m

def resolve_order_identity(db,user,review_id,cid,why):
    config(db,True);r=db.query(LoyaltyIdentityReview).filter_by(id=review_id).with_for_update().one_or_none()
    if not r or r.status!='OPEN' or not r.order_id:fail('Open order identity review not found',404)
    if not db.get(Customer,cid):fail('Choose an existing customer',404)
    order=db.get(SalesOrder,r.order_id)
    if order.customer_id is not None and order.customer_id!=cid:fail('An existing authoritative customer association cannot be reassigned by loyalty',409)
    why=reason(why);db.add(LoyaltyOrderIdentity(order_id=order.id,customer_id=cid,actor_id=user.id,reason=why))
    r.status='RESOLVED';r.resolution={'customer_id':cid,'actor_id':user.id,'reason':why};audit(db,'IDENTITY_RECONCILED',r.id,user.username,None,r.resolution,why)
    db.flush()  # Customer attribution only; migration approval creates memberships.


def rule_data(rule):
    return serial({k:getattr(rule,k) for k in ('id','variant_id','channel','method','value','cost','cost_reference','cost_verified_at','variable_cost','starts_at','ends_at','enabled','max_quantity','exception_reason','exception_actor_id','notes','version')})

def calculation(db,rule,regular):
    regular=money(regular,MONEY);value=money(rule.value)
    if rule.method=='PERCENT':
        if value>100:fail('Percentage cannot exceed 100')
        price=(regular*(1-value/100)).quantize(MONEY,rounding=ROUND_HALF_UP)
    elif rule.method=='AMOUNT':price=regular-value
    else:price=value
    if not 0<price<=regular:fail('Loyalty price must be positive and no greater than the current regular price')
    cost=rule.cost if rule.cost is not None and rule.cost_reference and rule.cost_verified_at else None
    gp=(price-cost).quantize(MONEY) if cost is not None else None
    margin=(gp/price*100).quantize(MONEY) if gp is not None else None
    contribution=(gp-rule.variable_cost).quantize(MONEY) if gp is not None and rule.variable_cost is not None else None
    issues=[]
    if cost is None:issues.append('MISSING_RELIABLE_COST')
    if margin is not None and margin<money(rules_config(db)['minimum_margin']):issues.append('BELOW_MINIMUM_MARGIN')
    if gp is not None and gp<0:issues.append('BELOW_COST')
    if contribution is not None and contribution<0:issues.append('NEGATIVE_CONTRIBUTION')
    exception=bool(rule.exception_reason and rule.exception_actor_id)
    return {'regular_unit_price':str(regular),'loyalty_unit_price':str(price),'saving':str(regular-price),'gross_profit':str(gp) if gp is not None else None,'gross_margin':str(margin) if margin is not None else None,'contribution':str(contribution) if contribution is not None else None,'issues':issues,'valid':not issues or exception,'exception':exception}

def save_rules(db,user,payload,can_exception=False):
    config(db,True)
    ids=payload.get('variant_ids') or ([payload['variant_id']] if payload.get('variant_id') else [])
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or len(set(ids))!=len(ids):fail('Choose 1–100 distinct imported variants')
    now=datetime.utcnow();saved=[]
    for rid in ids:
        variant=db.get(RetailVariant,rid)
        if not variant or variant.shop!=(settings.SHOPIFY_SHOP or '').lower() or variant.product_status!='ACTIVE' or variant.missing or variant.currency!='LKR' or variant.price is None:fail('Choose an active LKR variant in the configured imported catalogue')
        rule=db.query(LoyaltyRule).filter_by(id=payload.get('id')).with_for_update().one_or_none() if payload.get('id') else None
        if payload.get('id') and (not rule or len(ids)!=1 or rule.variant_id!=rid):fail('Rule identity cannot change')
        if rule and payload.get('version')!=rule.version:fail('Rule changed. Refresh first.',409)
        previous=rule_data(rule) if rule else None
        if not rule:rule=LoyaltyRule(variant_id=rid,created_by=user.id,updated_by=user.id,version=0)
        rule.method=payload.get('method');rule.channel=payload.get('channel','CRM')
        if rule.method not in {'PERCENT','AMOUNT','PRICE'} or rule.channel!='CRM':fail('Choose a supported CRM discount method')
        rule.value=money(payload.get('value'))
        rule.cost=money(payload['cost']) if payload.get('cost') not in (None,'') else None
        rule.cost_reference=str(payload.get('cost_reference') or '')[:500] or None
        rule.cost_verified_at=stamp(payload['cost_verified_at']) if payload.get('cost_verified_at') else None
        if rule.cost_verified_at and rule.cost_verified_at>now+timedelta(minutes=5):fail('Cost verification cannot be in the future')
        rule.variable_cost=money(payload['variable_cost']) if payload.get('variable_cost') not in (None,'') else None
        rule.starts_at=stamp(payload.get('starts_at') or now.isoformat());rule.ends_at=stamp(payload.get('ends_at') or (now+timedelta(days=rules_config(db)['default_days'])).isoformat())
        if rule.ends_at<=rule.starts_at:fail('Offer expiry must follow its start')
        if type(payload.get('enabled',False)) is not bool:fail('Enabled must be boolean')
        rule.enabled=payload.get('enabled',False);rule.max_quantity=quantity(payload['max_quantity']) if payload.get('max_quantity') not in (None,'') else None
        rule.notes=str(payload.get('notes') or '')[:2000];rule.updated_by=user.id
        exception=payload.get('exception_reason')
        rule.exception_reason=None;rule.exception_actor_id=None
        if exception:
            if not can_exception:fail('Profitability-exception approval required',403)
            rule.exception_reason=reason(exception);rule.exception_actor_id=user.id
        calc=calculation(db,rule,variant.price)
        if rule.enabled and rule.method!='PRICE' and variant.compare_at_price and variant.compare_at_price>variant.price:fail('Possible public promotion: use an explicit fixed loyalty price for lower-price comparison; percentage/amount stacking is blocked')
        if rule.enabled and not calc['valid']:fail('Cannot activate: '+', '.join(calc['issues']))
        overlap=db.query(LoyaltyRule.id).filter(LoyaltyRule.variant_id==rid,LoyaltyRule.channel==rule.channel,LoyaltyRule.enabled.is_(True),LoyaltyRule.starts_at<rule.ends_at,LoyaltyRule.ends_at>rule.starts_at)
        if rule.id:overlap=overlap.filter(LoyaltyRule.id!=rule.id)
        if rule.enabled and overlap.first():fail('An enabled rule overlaps this variant/channel validity period',409)
        rule.version+=1;db.add(rule);db.flush()
        db.add(LoyaltyRuleVersion(rule_id=rule.id,version=rule.version,value=rule_data(rule)))
        audit(db,'PRICING_RULE_SAVED',rule.id,user.username,previous,rule_data(rule),rule.exception_reason or rule.notes)
        saved.append({'rule':rule_data(rule),'calculation':calc})
    return saved


def quote(db,cid,lines,exclude_order=None,order_discount=0,channel='CRM',now=None):
    if not isinstance(lines,list) or not 1<=len(lines)<=100:fail('Choose 1–100 order lines')
    if not isinstance(cid,int) or isinstance(cid,bool):fail('Select the existing internal customer ID')
    summary=member_summary(db,cid,exclude_order);c=config(db);now=now or datetime.utcnow()
    active=bool(c and c.enabled and channel=='CRM' and summary['status']=='ACTIVE')
    gross=[(money(l.get('unit_price'))*quantity(l.get('quantity'))).quantize(MONEY,rounding=ROUND_HALF_UP) for l in lines]
    discounts=[money(l.get('discount_amount',0)) for l in lines]
    if any(d>g for d,g in zip(discounts,gross)):fail('Promotional discount exceeds line total')
    od=money(order_discount);base=sum(g-d for g,d in zip(gross,discounts))
    if od>base:fail('Order promotion exceeds merchandise value')
    allocations=[];remaining=od
    for i,(g,d) in enumerate(zip(gross,discounts)):
        amount=remaining if i==len(lines)-1 else min(remaining,((g-d)/base*od).quantize(MONEY,rounding=ROUND_HALF_UP)) if base else Decimal(0)
        allocations.append(amount);remaining-=amount
    result=[];used_quantity={}
    for i,line in enumerate(lines):
        qty=quantity(line['quantity']);regular=money(line['unit_price']);public=discounts[i]+allocations[i]
        response={'variant_id':line.get('shopify_variant_id'),'quantity':str(qty),'regular_unit_price':str(regular),'loyalty_unit_price':str(regular),'discount_per_unit':'0.00','total_line_discount':str(public),'loyalty_saving':'0.00','final_line_amount':str(gross[i]-public),'pricing_method':'PROMOTION' if public else 'REGULAR','reason':summary['reason'] if not active else 'NO_ACTIVE_VARIANT_OFFER','rule_id':None,'rule_version':None,'profitability_valid':False}
        variant=db.query(RetailVariant).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower(),variant_id=line.get('shopify_variant_id')).one_or_none()
        if active and line.get('currency')=='LKR' and variant and variant.product_status=='ACTIVE' and not variant.missing and not variant.stale and variant.currency=='LKR' and variant.price==regular and variant.product_id==line.get('shopify_product_id'):
            offers=db.query(LoyaltyRule).filter(LoyaltyRule.variant_id==variant.id,LoyaltyRule.enabled.is_(True),LoyaltyRule.channel==channel,LoyaltyRule.starts_at<=now,LoyaltyRule.ends_at>now).all()
            if len(offers)>1:fail('Conflicting loyalty offers require management review',409)
            if offers:
                rule=offers[0]
                if rule.method!='PRICE' and variant.compare_at_price and variant.compare_at_price>variant.price:
                    response['reason']='PUBLIC_PROMOTION_REVIEW_REQUIRED';result.append(response);continue
                try:calc=calculation(db,rule,regular)
                except HTTPException:calc={'valid':False,'issues':['PRICE_CHANGED']}
                response['reason']='PROFITABILITY_REVIEW_REQUIRED' if not calc['valid'] else 'PUBLIC_PRICE_SELECTED'
                if calc['valid']:
                    remaining=max(Decimal(0),rule.max_quantity-used_quantity.get(rule.id,Decimal(0))) if rule.max_quantity else qty
                    eligible=min(qty,remaining)
                    if eligible==0:
                        response['reason']='ORDER_QUANTITY_LIMIT_REACHED';result.append(response);continue
                    saving=((regular-Decimal(calc['loyalty_unit_price']))*eligible).quantize(MONEY,rounding=ROUND_HALF_UP)
                    response['profitability_valid']=True
                    if saving>public:
                        used_quantity[rule.id]=used_quantity.get(rule.id,Decimal(0))+eligible
                        response.update(loyalty_unit_price=calc['loyalty_unit_price'],discount_per_unit=calc['saving'],total_line_discount=str(saving),loyalty_saving=str(saving),final_line_amount=str(gross[i]-saving),pricing_method='LOYALTY',reason='ELIGIBLE_PREVIOUS_PURCHASE',rule_id=rule.id,rule_version=rule.version,eligible_quantity=str(eligible),cost=str(rule.cost) if rule.cost is not None else None,variable_cost=str(rule.variable_cost) if rule.variable_cost is not None else None,profitability=calc)
        elif active and variant:response['reason']='CATALOGUE_PRICE_OR_STATE_CHANGED'
        if c is None or not c.enabled:response['reason']='PROGRAMME_OFF'
        result.append(response)
    return serial({'customer_id':cid,'membership_status':summary['status'],'membership_id':summary['membership_id'],'eligibility':active,'eligibility_reason':'PROGRAMME_OFF' if not c or not c.enabled else summary['reason'],'config_version':c.version if c else 0,'channel':channel,'lines':result,'loyalty_savings':str(sum(Decimal(l['loyalty_saving']) for l in result)),'merchandise_total':str(sum(Decimal(l['final_line_amount']) for l in result))})

def prepare_order(db,payload,order=None,actor='staff'):
    data=copy.deepcopy(payload)
    for key in list(data):
        if key.startswith('_loyalty'):data.pop(key,None)
    data['_loyalty_managed']=True
    if order and 'customer_id' not in data:data['customer_id']=order.customer_id
    cid=data.get('customer_id')
    if not cid:
        why=data.get('unidentified_reason')
        if not isinstance(why,str) or not 3<=len(why.strip())<=1000:fail('Select/register a customer, or explicitly provide an authorized unidentified-order reason')
        data['unidentified_reason']=why.strip();return data
    if not isinstance(cid,int) or isinstance(cid,bool) or not db.get(Customer,cid):fail('Choose an existing customer')
    # Identity is explicit, never matched/merged from the WhatsApp phone automatically.
    if phone(data.get('whatsapp_phone'))!=phone(db.get(Customer,cid).phone):fail('Selected customer mobile differs; reconcile identity before applying loyalty',409)
    config(db,True)
    result=quote(db,cid,data['lines'],order.id if order else None,data.get('order_discount',0))
    if Decimal(result['loyalty_savings'])>0:
        member=refresh_member(db,cid,actor)
        result['membership_id']=member.membership_id
        data['_loyalty_base_lines']=copy.deepcopy(data['lines'])
        data['_loyalty_base_order_discount']=str(data.get('order_discount','0.00'))
        for raw,priced in zip(data['lines'],result['lines']):raw['discount_amount']=priced['total_line_discount']
        data['order_discount']='0.00'
        data['_loyalty']=result
    return data

def save_order_pricing(db,order,prepared,actor):
    snapshot=prepared.get('_loyalty');old=db.get(LoyaltyPricing,order.id)
    if old and old.frozen:fail('Finalized loyalty pricing is immutable',409)
    if old:db.delete(old);db.flush()
    if snapshot:
        snapshot={**snapshot,'staff':actor,'order_id':order.id,'totals':{key:str(getattr(order,key)) for key in ('subtotal','discount_total','shipping_total','service_total','tax_total','grand_total')},'base_lines':[{**line,'discount_amount':'0.00'} for line in prepared['lines']]}
        # Original public discounts are retained by prepare_order separately below.
        snapshot['base_lines']=prepared.get('_loyalty_base_lines',snapshot['base_lines'])
        snapshot['base_order_discount']=prepared.get('_loyalty_base_order_discount','0.00')
        db.add(LoyaltyPricing(order_id=order.id,value=serial(snapshot),digest=digest(snapshot),frozen=False))

def freeze_order(db,order,verified_lines,actor):
    config(db,True)
    db.refresh(order,with_for_update=True)
    saved=db.query(LoyaltyPricing).filter_by(order_id=order.id).populate_existing().one_or_none()
    if not saved:return
    if saved.frozen:return
    if digest(saved.value)!=saved.digest:fail('Loyalty pricing snapshot failed integrity verification',409)
    config(db,True)
    for key,value in saved.value['totals'].items():
        if money(getattr(order,key))!=money(value):fail('Order totals changed; reprice the draft before confirmation',409)
    base=copy.deepcopy(saved.value['base_lines'])
    if len(base)!=len(verified_lines):fail('Complete live product verification required',409)
    for raw,verified in zip(base,verified_lines):
        if raw['shopify_product_id']!=verified['shopify_product_id'] or raw['shopify_variant_id']!=verified['shopify_variant_id'] or money(raw['unit_price'])!=money(verified['unit_price']):fail('Prices changed; edit and reprice this draft before confirmation',409)
    fresh=quote(db,order.customer_id,base,order.id,saved.value['base_order_discount'])
    old={k:saved.value[k] for k in fresh}
    if fresh!=old:fail('Loyalty eligibility, price or rule changed. Edit and reprice the draft.',409)
    if len(order.lines)!=len(fresh['lines']):fail('Loyalty order lines changed',409)
    member=refresh_member(db,order.customer_id,actor)
    if not member or member.status!='ACTIVE':fail('Membership is not eligible',409)
    for index,(line,priced) in enumerate(zip(order.lines,fresh['lines'])):
        if line.shopify_product_id!=base[index]['shopify_product_id'] or line.shopify_variant_id!=base[index]['shopify_variant_id']:fail('Order product identity changed; reprice before confirmation',409)
        if quantity(line.quantity)!=quantity(base[index]['quantity']) or money(line.unit_price)!=money(base[index]['unit_price']) or money(line.tax_amount)!=money(base[index].get('tax_amount',0)):fail('Order lines changed; reprice before confirmation',409)
        if money(line.discount_amount)!=money(priced['total_line_discount']):fail('Order discounts differ from the server pricing snapshot',409)
        if priced['pricing_method']=='LOYALTY':
            record=LoyaltyRedemption(order_id=order.id,line_id=line.id,member_id=member.id,rule_id=priced['rule_id'],rule_version=priced['rule_version'],snapshot={**priced,'staff':actor,'channel':'CRM','customer_id':order.customer_id,'product_id':line.shopify_product_id,'line_id':line.id},discount=Decimal(priced['loyalty_saving']))
            db.add(record);db.flush();db.add(LoyaltyRedemptionEvent(redemption_id=record.id,event_key=f'pending:{record.id}',kind='PENDING',amount=0,detail={'order_id':order.id}))
    saved.frozen=True;audit(db,'ORDER_PRICING_FROZEN',order.id,actor,None,{'digest':saved.digest,'loyalty_savings':fresh['loyalty_savings']})


def sync_redemptions(db,order,actor='system:order'):
    records=db.query(LoyaltyRedemption).filter_by(order_id=order.id).all()
    data,error=financial(db,order)
    for r in records:
        events=db.query(LoyaltyRedemptionEvent).filter_by(redemption_id=r.id).all()
        earned=any(e.kind=='FINALIZED' for e in events)
        if data and not earned:
            db.add(LoyaltyRedemptionEvent(redemption_id=r.id,event_key=f'finalized:{r.id}',kind='FINALIZED',amount=r.discount,detail={'net_paid':str(data['net'])}))
            audit(db,'REDEMPTION_FINALIZED',r.id,actor,None,{'discount':str(r.discount)});earned=True
        if earned and error and error not in {'CANCELLED_OR_RETURNED','TEST_ORDER'}:
            fail('Verified payment/refund evidence unavailable for local loyalty reconciliation',409)
        previous=-sum((e.amount for e in events if e.kind=='REVERSAL'),Decimal(0))
        reverse=r.discount if order.order_status in {'CANCELLED','RETURNED'} or error=='TEST_ORDER' or (data and data['net']==0) else (r.discount*data['refunded']/data['paid']).quantize(MONEY,rounding=ROUND_HALF_UP) if data else previous
        if earned and reverse>previous:
            delta=reverse-previous
            db.add(LoyaltyRedemptionEvent(redemption_id=r.id,event_key=f'reverse:{r.id}:{reverse}',kind='REVERSAL',amount=-delta,detail={'refund_reference':order.shopify_order_id or order.internal_order_number,'basis':'ORDER_TOTAL_PRO_RATA','cumulative_reversed':str(reverse)}))
            audit(db,'REDEMPTION_REVERSED',r.id,actor,{'reversed':str(previous)},{'reversed':str(reverse)},'Verified cumulative refund/cancellation')
        if not earned and (order.order_status in {'CANCELLED','RETURNED'} or error=='TEST_ORDER') and not any(e.kind=='RELEASED' for e in events):db.add(LoyaltyRedemptionEvent(redemption_id=r.id,event_key=f'released:{r.id}',kind='RELEASED',amount=0,detail={'reason':order.order_status}))
    if records:
        db.flush()
        for mid in {r.member_id for r in records}:
            member=db.get(LoyaltyMember,mid)
            member.lifetime_savings=max(Decimal(0),db.query(func.coalesce(func.sum(LoyaltyRedemptionEvent.amount),0)).join(LoyaltyRedemption,LoyaltyRedemption.id==LoyaltyRedemptionEvent.redemption_id).filter(LoyaltyRedemption.member_id==mid).scalar())


def preview_migration(db,user,payload,key):
    config(db,True)
    if not isinstance(key,str) or not 8<=len(key)<=100:fail('Provide a unique migration request key')
    filters={k:payload.get(k) for k in ('start','end','source')}
    if filters['source'] not in {None,'','CRM','POS','SHOPIFY_HISTORY'}:fail('Choose CRM, POS, SHOPIFY_HISTORY or all available sources')
    if filters['start']:stamp(filters['start'])
    if filters['end']:stamp(filters['end'])
    if filters['start'] and filters['end'] and stamp(filters['end'])<=stamp(filters['start']):fail('End must follow start')
    prior=db.query(LoyaltyMigration).filter_by(request_key=key).one_or_none()
    if prior:
        if prior.filters!=filters:fail('Request key reused for different filters',409)
        return prior
    query=db.query(SalesOrder).order_by(SalesOrder.id)
    if filters['start']:query=query.filter(SalesOrder.created_at>=stamp(filters['start']))
    if filters['end']:query=query.filter(SalesOrder.created_at<stamp(filters['end']))
    if filters['source']=='POS':query=query.filter(SalesOrder.sales_channel=='POS')
    elif filters['source']=='CRM':query=query.filter((SalesOrder.sales_channel.is_(None))|(SalesOrder.sales_channel=='CRM'))
    elif filters['source']=='SHOPIFY_HISTORY':query=query.filter(SalesOrder.id==-1)
    count=query.count()
    if count>10000:fail('More than 10,000 local orders. Narrow the date/source range; no partial preview will execute.')
    job=LoyaltyMigration(id=str(uuid4()),request_key=key,filters=filters,status='PREVIEW',digest='',counts={},actor_id=user.id);db.add(job);db.flush()
    customers=set();missing=[];seen=set();duplicates=0
    for order in query.yield_per(200):
        orderkey=order.shopify_order_id or f'crm:{order.id}'
        if orderkey in seen:duplicates+=1
        seen.add(orderkey);cid=order_customer(db,order)
        if cid and db.get(Customer,cid):customers.add(cid)
        else:
            candidates=[c.id for c in db.query(Customer) if phone(c.phone) and phone(c.phone)==phone(order.whatsapp_phone)]
            key_review=f'order:{order.id}'
            if not db.query(LoyaltyIdentityReview.id).filter_by(key=key_review).first():db.add(LoyaltyIdentityReview(key=key_review,order_id=order.id,candidates=candidates,reason='AMBIGUOUS_IDENTITY' if len(candidates)>1 else 'EXPLICIT_CUSTOMER_LINK_REQUIRED'))
            missing.append(order.id);db.add(LoyaltyMigrationRow(migration_id=job.id,order_id=order.id,status='REVIEW',detail={'reason':'CUSTOMER_LINK_REQUIRED','candidates':candidates}))
    from loyalty_models import LoyaltyHistoricalOrder
    remote_count=0
    if filters['source'] in {None,'','SHOPIFY_HISTORY'}:
        for r in db.query(LoyaltyHistoricalOrder).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower()).order_by(LoyaltyHistoricalOrder.id):
            created=stamp(r.evidence['createdAt'])
            if filters['start'] and created<stamp(filters['start']) or filters['end'] and created>=stamp(filters['end']):continue
            remote_count+=1
            if count+remote_count>10000:fail('More than 10,000 source orders. Narrow the range; no partial preview will execute.')
            if r.order_gid in seen:duplicates+=1;continue
            seen.add(r.order_gid)
            if r.customer_id and r.approved_fingerprint==r.fingerprint:customers.add(r.customer_id)
            else:
                missing.append('shopify:'+str(r.id));db.add(LoyaltyMigrationRow(migration_id=job.id,status='REVIEW',detail={'reason':'SHOPIFY_CUSTOMER_LINK_REQUIRED','historical_order_id':r.id}))
    eligible=existing=ambiguous=0;manifest=[]
    for cid in sorted(customers):
        summary=member_summary(db,cid);h=history(db,cid,filters=filters)
        if summary['reason']=='AMBIGUOUS_CUSTOMER_IDENTITY':
            review_key=f'customer:{cid}:{fingerprint(db.get(Customer,cid))}'
            if not db.query(LoyaltyIdentityReview.id).filter_by(key=review_key).first():db.add(LoyaltyIdentityReview(key=review_key,customer_id=cid,candidates=summary['candidates'],reason=summary['reason']))
        eligible+=bool(h['count'] and summary['status']=='ACTIVE');existing+=bool(summary['member_id']);ambiguous+=bool(summary['candidates'] and summary['reason']=='AMBIGUOUS_CUSTOMER_IDENTITY')
        detail={'count':h['count'],'net':h['net'],'order_ids':[o['order_id'] for o in h['orders'] if o['order_id'] is not None],'historical_order_ids':[o['historical_order_id'] for o in h['orders'] if o.get('historical_order_id')],'reason':summary['reason'],'fingerprint':digest(h)}
        status='PROPOSED' if h['count'] and summary['status']=='ACTIVE' else 'INELIGIBLE'
        db.add(LoyaltyMigrationRow(migration_id=job.id,customer_id=cid,status=status,detail=detail));manifest.append({'customer_id':cid,'status':status,**detail})
    job.counts={'orders_reviewed':count+remote_count,'local_orders':count,'shopify_evidence_orders':remote_count,'unique_customers':len(customers),'eligible_customers':eligible,'existing_members':existing,'new_proposed':sum(1 for m in manifest if m['status']=='PROPOSED' and not db.query(LoyaltyMember.id).filter_by(customer_id=m['customer_id']).first()),'ineligible_customers':len(customers)-eligible,'duplicate_orders':duplicates,'missing_customer_details':len(missing),'ambiguous_identities':ambiguous,'failed_records':0,'coverage':'LOCAL_CRM_AND_REVIEWED_SHOPIFY_EVIDENCE; complete Shopify history NOT verified','complete_within_local_filters':True}
    job.digest=digest({'filters':filters,'manifest':manifest,'missing_orders':missing,'counts':job.counts});audit(db,'MIGRATION_PREVIEW',job.id,user.username,None,job.counts)
    return job

def approve_migration(db,user,job_id,approval_digest,why):
    job=db.query(LoyaltyMigration).filter_by(id=job_id).with_for_update().one_or_none()
    if not job or job.status!='PREVIEW' or job.digest!=approval_digest:fail('Preview/digest mismatch; review a fresh preview',409)
    job.status='APPROVED';job.approved_by=user.id;job.approved_at=datetime.utcnow();job.approval_reason=reason(why)
    audit(db,'MIGRATION_APPROVED',job.id,user.username,None,{'digest':job.digest},job.approval_reason);return job

def execute_migration_batch(db,user,job_id,approval_digest):
    config(db,True);job=db.query(LoyaltyMigration).filter_by(id=job_id).with_for_update().one_or_none()
    if not job or job.status not in {'APPROVED','RUNNING','COMPLETE'} or job.digest!=approval_digest:fail('Approved preview required',409)
    if job.status=='COMPLETE':return job
    if job.status=='APPROVED' and job.approved_at<datetime.utcnow()-timedelta(minutes=15):fail('Approval expired; generate a fresh preview',409)
    job.status='RUNNING'
    rows=db.query(LoyaltyMigrationRow).filter_by(migration_id=job.id,status='PROPOSED').order_by(LoyaltyMigrationRow.id).limit(100).all()
    for row in rows:
        fresh=history(db,row.customer_id,filters=job.filters)
        if digest(fresh)!=row.detail['fingerprint']:row.status='REVIEW';row.detail={**row.detail,'execution_reason':'SOURCE_CHANGED'};continue
        member=refresh_member(db,row.customer_id,user.username,'HISTORICAL_MIGRATION');row.status='EXECUTED' if member and member.status=='ACTIVE' else 'REVIEW'
    db.flush()
    if not db.query(LoyaltyMigrationRow.id).filter_by(migration_id=job.id,status='PROPOSED').first():job.status='COMPLETE'
    audit(db,'MIGRATION_BATCH',job.id,user.username,None,{'processed':len(rows),'status':job.status,'digest':job.digest})
    return job


def public_quote(value):
    result=copy.deepcopy(value)
    for line in result['lines']:
        for key in ('cost','variable_cost','profitability'):line.pop(key,None)
    return result

def public_order(order):
    from sqlalchemy.orm import object_session
    db=object_session(order)
    if not db or order.sales_channel=='POS' or not order.customer_id:return None
    saved=db.get(LoyaltyPricing,order.id)
    if not saved:return None
    return {'membership_id':saved.value['membership_id'],'loyalty_savings':saved.value['loyalty_savings'],'frozen':saved.frozen,'lines':public_quote(saved.value)['lines'],'base_lines':saved.value['base_lines'],'base_order_discount':saved.value['base_order_discount']}


def rule_projection(db,rule):
    variant=db.get(RetailVariant,rule.variant_id)
    if not variant or variant.product_status!='ACTIVE' or variant.missing or variant.stale or variant.price is None or variant.currency!='LKR':return {'valid':False,'issues':['CATALOGUE_REVIEW_REQUIRED']}
    try:value=calculation(db,rule,variant.price)
    except HTTPException:return {'valid':False,'issues':['CURRENT_PRICE_INVALIDATES_RULE']}
    if rule.method!='PRICE' and variant.compare_at_price and variant.compare_at_price>variant.price:
        value={**value,'valid':False,'issues':[*value['issues'],'PUBLIC_PROMOTION_REVIEW_REQUIRED']}
    return value


def ensure_confirmation_pricing(db,order,actor):
    """Shared confirmation guard for CRM API and existing non-POS order workflows."""
    if order.sales_channel=='POS' or not order.customer_id:return
    saved=db.get(LoyaltyPricing,order.id)
    if not saved or saved.frozen:return
    from shopify_catalog_service import resolve_inbox_product,ShopifyCatalogError
    verified=[]
    for line in order.lines:
        try:product=resolve_inbox_product(line.shopify_product_id,line.shopify_variant_id)
        except ShopifyCatalogError:fail('Live product revalidation unavailable; retry confirmation without resending the order',503)
        if not product or product.get('currency')!='LKR':fail('Current product/currency could not be verified',409)
        verified.append({'shopify_product_id':product['id'],'shopify_variant_id':product['variant_id'],'unit_price':str(product['price'])})
    freeze_order(db,order,verified,actor)
