"""Read-only, conversation-scoped identity projection; never infer an association."""
from fastapi import HTTPException
from models import Customer,SalesOrder
from settings import settings
import staff_access
import customer_registration as registry
import loyalty_service as loyalty


def snapshot(db,user,conversation):
    full=staff_access.allowed(user,'loyalty.members.view')
    if not full and not (settings.SALES_ORDERS_ENABLED and staff_access.allowed(user,'sales_orders')):
        raise HTTPException(403,'Customer information access required')
    order=db.query(SalesOrder).filter_by(crm_conversation_id=conversation.id).order_by(SalesOrder.updated_at.desc(),SalesOrder.id.desc()).first() if settings.SALES_ORDERS_ENABLED else None
    cid=loyalty.order_customer(db,order) if order else None
    relationship='CONFIRMED_ORDER' if cid else 'SUGGESTED'
    if cid:
        customer=db.get(Customer,cid)
        if not customer:raise HTTPException(409,'Customer identity requires review')
    else:
        normalized=loyalty.phone(conversation.phone_number)
        if not normalized:raise HTTPException(422,'Customer phone requires review')
        matches=registry.candidates(db,normalized)
        if len(matches)>1:return {'conversation_id':conversation.id,'state':'AMBIGUOUS'}
        if not matches:return {'conversation_id':conversation.id,'state':'UNREGISTERED'}
        customer=matches[0];cid=customer.id
    value=loyalty.member_summary(db,cid)
    state=value['status'] if value['membership_id'] else 'INELIGIBLE' if value['status']=='INELIGIBLE' else 'REGISTERED'
    if relationship=='SUGGESTED':state='SUGGESTED'
    result={'conversation_id':conversation.id,'state':state,'relationship':relationship,'customer_id':cid,
            'name':customer.name,'mobile':value['mobile'],'membership_id':value['membership_id'],
            'membership_status':value['status'] if value['membership_id'] else 'PENDING_FIRST_PURCHASE' if not value['qualifying_count'] else 'PENDING_ACTIVATION',
            'qualifying_count':value['qualifying_count'],'can_view_member':bool(full and value['member_id'])}
    if full:
        result.update(lifetime_net=value['lifetime_net'],lifetime_savings=value['lifetime_savings'],activated_at=value['activated_at'])
        if value['member_id']:result['membership_url']='/crm/loyalty/members/'+str(value['member_id'])
    return result
