"""Read-only database-backed loyalty projections. Monetary values are Decimal strings."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
from sqlalchemy import func
from loyalty_models import *
from models import SalesOrder,Customer
import loyalty_service as svc


def redemption_rows(db,start=None,end=None,channel=None):
    query=db.query(LoyaltyRedemption).order_by(LoyaltyRedemption.id.desc())
    rows=[]
    for r in query:
        if channel and channel!='CRM':continue
        events=db.query(LoyaltyRedemptionEvent).filter_by(redemption_id=r.id).order_by(LoyaltyRedemptionEvent.id).all()
        finalized=next((e for e in events if e.kind=='FINALIZED'),None)
        when=finalized.created_at if finalized else r.created_at
        if start and when<svc.stamp(start) or end and when>=svc.stamp(end):continue
        net=sum((e.amount for e in events),Decimal(0))
        state=('RELEASED' if any(e.kind=='RELEASED' for e in events) else 'PENDING') if not finalized else 'REVERSED' if net==0 else 'PARTIALLY_REVERSED' if net<r.discount else 'FINALIZED'
        evidence,error=svc.financial(db,db.get(SalesOrder,r.order_id))
        expected_reversal=r.discount if error in {'CANCELLED_OR_RETURNED','TEST_ORDER'} else (r.discount*evidence['refunded']/evidence['paid']).quantize(svc.MONEY,rounding=ROUND_HALF_UP) if evidence else None
        recorded_reversal=-sum((e.amount for e in events if e.kind=='REVERSAL'),Decimal(0))
        if finalized and (expected_reversal is None or expected_reversal!=recorded_reversal):state='NEEDS_REVIEW'
        fraction=net/r.discount if finalized else Decimal(0)
        snapshot=r.snapshot;qty=Decimal(snapshot.get('eligible_quantity',snapshot['quantity']))
        cost=Decimal(snapshot['cost']) if snapshot.get('cost') is not None else None
        variable=Decimal(snapshot['variable_cost']) if snapshot.get('variable_cost') is not None else None
        sales=(Decimal(snapshot['loyalty_unit_price'])*qty*fraction).quantize(svc.MONEY)
        gp=(sales-cost*qty*fraction).quantize(svc.MONEY) if cost is not None else None
        contribution=(gp-variable*qty*fraction).quantize(svc.MONEY) if gp is not None and variable is not None else None
        member=db.get(LoyaltyMember,r.member_id);order=db.get(SalesOrder,r.order_id)
        rows.append({'id':r.id,'order_id':r.order_id,'order_reference':order.internal_order_number,'line_id':r.line_id,'customer_id':member.customer_id,'membership_id':member.membership_id,'variant_id':snapshot['variant_id'],'product_id':snapshot.get('product_id'),'rule_id':r.rule_id,'rule_version':r.rule_version,'channel':'CRM','staff':snapshot['staff'],'quantity':str(qty),'regular_unit_price':snapshot['regular_unit_price'],'loyalty_unit_price':snapshot['loyalty_unit_price'],'issued_discount':str(r.discount),'net_discount':str(net),'net_sales':str(sales),'gross_profit':str(gp) if gp is not None else None,'contribution':str(contribution) if contribution is not None else None,'status':state,'date':when.isoformat(),'refund_allocation':'ORDER_TOTAL_PRO_RATA' if any(e.kind=='REVERSAL' for e in events) else None,'events':[{'kind':e.kind,'amount':str(e.amount),'reference':e.detail.get('refund_reference'),'date':e.created_at.isoformat()} for e in events]})
    return rows

def metrics(db,start=None,end=None,channel=None):
    now=datetime.utcnow();month=now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo')).replace(day=1,hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    start=start or month.isoformat();end=end or now.isoformat()
    members=list(db.query(LoyaltyMember));states=[svc.member_summary(db,m.customer_id) for m in members]
    rows=redemption_rows(db,start,end,channel);earned=[r for r in rows if r['status'] in {'FINALIZED','PARTIALLY_REVERSED'}]
    orders={r['order_id'] for r in earned};discount=sum((Decimal(r['net_discount']) for r in earned),Decimal(0));sales=sum((Decimal(r['net_sales']) for r in earned),Decimal(0))
    profit=None if any(r['gross_profit'] is None for r in earned) else sum((Decimal(r['gross_profit']) for r in earned),Decimal(0))
    contribution=None if any(r['contribution'] is None for r in earned) else sum((Decimal(r['contribution']) for r in earned),Decimal(0))
    valid=[];needs_review=0
    for r in db.query(LoyaltyRule).filter(LoyaltyRule.enabled.is_(True),LoyaltyRule.starts_at<=now,LoyaltyRule.ends_at>now):
        good=svc.rule_projection(db,r)['valid']
        if good:valid.append(r)
        else:needs_review+=1
    purchasers=[s for s in states if s['qualifying_count']>0]
    return svc.serial({'programme_enabled':bool(svc.config(db) and svc.config(db).enabled),'total_members':len(members),'active_members':sum(s['status']=='ACTIVE' for s in states),'suspended_members':sum(s['status']=='SUSPENDED' for s in states),'new_members_this_month':sum(bool(m.activated_at and m.activated_at>=month) for m in members),'eligible_variants':len({r.variant_id for r in valid}),'active_rules':len(valid),'rules_needing_review':needs_review,'loyalty_orders':len(orders),'net_loyalty_sales':sales,'net_loyalty_discounts':discount,'average_discount_per_order':(discount/len(orders)).quantize(svc.MONEY) if orders else Decimal('0.00'),'repeat_purchase_rate':(Decimal(sum(s['qualifying_count']>=2 for s in purchasers))/len(purchasers)*100).quantize(svc.MONEY) if purchasers else Decimal('0.00'),'estimated_gross_profit':profit,'estimated_contribution':contribution,'period_start':start,'period_end':end,'pending_recalculations':db.query(LoyaltyRecalculation).count(),'redemptions_needing_review':sum(r['status']=='NEEDS_REVIEW' for r in rows),'refund_allocation_policy':'Order-level verified cumulative refunds allocated pro rata; not a claim of SKU-specific returns','historical_coverage':'Local CRM and reviewed Shopify evidence; complete Shopify historical coverage unverified'})

def report(db,kind,start=None,end=None,channel=None):
    if kind=='migrations':return [{'id':j.id,'status':j.status,'created_at':j.created_at.isoformat(),**j.counts} for j in db.query(LoyaltyMigration).order_by(LoyaltyMigration.created_at.desc()) if (not start or j.created_at>=svc.stamp(start)) and (not end or j.created_at<svc.stamp(end))]
    if kind=='growth':return [{'customer_id':m.customer_id,'membership_id':m.membership_id,'activated_at':m.activated_at.isoformat() if m.activated_at else None,'status':svc.member_summary(db,m.customer_id)['status']} for m in db.query(LoyaltyMember) if m.activated_at and (not start or m.activated_at>=svc.stamp(start)) and (not end or m.activated_at<svc.stamp(end))]
    if kind=='repeat':
        return [{'customer_id':m.customer_id,'membership_id':m.membership_id,'qualifying_purchases':(h:=svc.history(db,m.customer_id,filters={'start':start,'end':end,'source':channel}))['count'],'net_paid':h['net']} for m in db.query(LoyaltyMember)]
    rows=redemption_rows(db,start,end,channel)
    if kind=='refunds':return [r for r in rows if r['status'] in {'REVERSED','PARTIALLY_REVERSED','RELEASED','NEEDS_REVIEW'}]
    if kind in {'discounts','profitability'}:return rows
    if kind in {'products','customers'}:
        key='variant_id' if kind=='products' else 'customer_id';groups={}
        for row in rows:
            if row['status'] in {'PENDING','RELEASED','NEEDS_REVIEW'}:continue
            entry=groups.setdefault(row[key],{key:row[key],'orders':set(),'net_sales':Decimal(0),'net_discounts':Decimal(0)})
            entry['orders'].add(row['order_id']);entry['net_sales']+=Decimal(row['net_sales']);entry['net_discounts']+=Decimal(row['net_discount'])
        return [svc.serial({**r,'orders':len(r['orders'])}) for r in groups.values()]
    svc.fail('Unknown report')
