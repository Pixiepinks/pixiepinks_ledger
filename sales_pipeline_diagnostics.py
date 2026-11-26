"""Read-only association evidence; never links orders or interprets a lead label as payment."""
from collections import Counter
from sqlalchemy import func
from models import Lead,SalesOrder,SalesPayment
from sales_pipeline_models import SalesOpportunity as O
from sales_pipeline import convertible,STAGES
STATUSES={'ORDER_CONFIRMED','DISPATCHED','DELIVERED','PAID'}

def order_reconciliation(db,*,details=False):
    rows=db.query(O,Lead).join(Lead,Lead.id==O.lead_id).filter(Lead.status.in_(STATUSES),O.active_conversation_id.isnot(None)).all()
    cids=[o.conversation_id for o,l in rows];orders={};by_id={}
    for order in db.query(SalesOrder).filter(SalesOrder.crm_conversation_id.in_(cids or [-1])):
        orders.setdefault(order.crm_conversation_id,[]).append(order);by_id[order.id]=order
    requested=[o.data.get('order_id') for o,l in rows if o.data.get('order_id')]
    for order in db.query(SalesOrder).filter(SalesOrder.id.in_(requested or [-1])):by_id[order.id]=order
    paid={oid:amount for oid,amount in db.query(SalesPayment.sales_order_id,func.sum(SalesPayment.amount)).filter(SalesPayment.sales_order_id.in_(list(by_id) or [-1]),SalesPayment.verification_status=='VERIFIED').group_by(SalesPayment.sales_order_id)}
    counts=Counter();evidence=[]
    for o,lead in rows:
        candidates=orders.get(o.conversation_id,[]);linked=by_id.get(o.data.get('order_id'))
        if linked and linked.crm_conversation_id!=o.conversation_id:reason='LINK_IDENTITY_MISMATCH'
        elif linked and convertible(linked):reason='VERIFIED_LOCAL_ORDER_ASSOCIATION'
        elif linked:reason='LINKED_ORDER_NOT_ELIGIBLE_FOR_CONVERSION'
        elif o.data.get('order_id'):reason='RECORDED_ORDER_MISSING'
        elif len(candidates)>1:reason='AMBIGUOUS_ORDER_CANDIDATES'
        elif len(candidates)==1:reason='UNLINKED_CANDIDATE_REQUIRES_ADMIN_REVIEW'
        else:reason='NO_EXISTING_ORDER_ASSOCIATION'
        counts[reason]+=1
        displayed=bool(linked and linked.crm_conversation_id==o.conversation_id and convertible(linked) and STAGES.get(lead.status)=='CONFIRMED')
        exclusion=None if displayed else 'LEAD_STATUS_REMAINS_PAYMENT_PENDING' if reason=='VERIFIED_LOCAL_ORDER_ASSOCIATION' else reason
        if exclusion:counts['NOT_DISPLAYED_'+exclusion]+=1
        if lead.status=='PAID' and (not linked or not paid.get(linked.id)):counts['PAID_LABEL_WITHOUT_VERIFIED_LOCAL_RECEIPT']+=1
        if details:evidence.append({'opportunity_id':o.id,'conversation_id':o.conversation_id,'lead_id':lead.id,'lead_status':lead.status,'displayed_in_confirmed':displayed,'display_exclusion':exclusion,'reason':reason,'recorded_order_id':o.data.get('order_id'),'candidates':[{'id':v.id,'status':v.order_status,'payment_status':v.payment_status,'shopify_sync_status':v.shopify_sync_status,'shopify_lifecycle_status':v.shopify_lifecycle_status,'authoritative_local_conversion':convertible(v)} for v in candidates]})
    return {'scope':'Current active opportunities with confirmed/dispatched/delivered/paid lead statuses','examined':len(rows),'counts':dict(counts),'association_verification_is_not_shopify_or_payment_verification':True,'details':evidence if details else None,'mutations':False}
