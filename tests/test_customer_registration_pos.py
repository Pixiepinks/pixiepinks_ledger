from uuid import uuid4
from models import Customer,SalesOrder,User,JournalEntry
from loyalty_models import LoyaltyConfig,LoyaltyMember
from pos_models import POSCart,POSCheckout
from test_pos import pos,inv,newcart,cash
from test_retail_phase1 import retail
from test_whatsapp_quick_messages import inbox
import pos_service as service
import customer_registration as registry
import loyalty_events
import loyalty_service as svc


def test_registered_pos_completion_shared_registry_and_next_cart_walk_in(pos):
    r=pos
    with r.sessions() as db:
        if not svc.config(db):db.add(LoyaltyConfig(id=1,enabled=False,version=1,value=svc.DEFAULTS));db.commit()
        user=db.get(User,r.uid);customer=service.create_customer(db,user,'International counter buyer','+96550012345',True,str(uuid4()));cid=customer.id
    while loyalty_events.process_one(r.sessions):pass
    with r.sessions() as db:assert db.query(LoyaltyMember).count()==0
    cartid,version=newcart(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);cart=db.get(POSCart,cartid);p={k:cart.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']};p.update(customer_id=cid,customer_name='Not a profile edit',phone='+96550012345')
        cart=service.edit_cart(db,u,cartid,cart.version,'details',p,r.pos)
        assert cart.payload['customer_name']=='International counter buyer'
        checkout=service.checkout(db,u,cartid,cart.version,uuid4().hex,cash(),r.pos);checkoutid=checkout.id
    service.dispatch_one(r.sessions,r.pos)
    while loyalty_events.process_one(r.sessions):pass
    with r.sessions() as db:
        checkout=db.get(POSCheckout,checkoutid);order=db.get(SalesOrder,checkout.sales_order_id)
        assert checkout.status=='COMPLETE' and order.customer_id==cid and checkout.frozen['cart']['customer_id']==cid
        result=registry.search(db,'96550012345')['rows'][0];assert result['membership_status']=='ACTIVE' and result['qualifying_purchases']==1
        member=db.query(LoyaltyMember).one();assert member.customer_id==cid and not svc.config(db).enabled
        assert member.source=='POS_COMPLETION'
        new=service.new_cart(db,db.get(User,r.uid),r.sid);assert new.payload['customer_id'] is None and new.payload['phone']==''
        assert db.query(Customer).count()==1


def test_pos_selected_customer_id_mismatch_is_rejected_and_walkin_stays_anonymous(pos):
    from fastapi import HTTPException
    import pytest
    r=pos
    with r.sessions() as db:
        u=db.get(User,r.uid);c=service.create_customer(db,u,'Existing profile','0771234567',True,str(uuid4()));cid=c.id
    cartid,version=newcart(r)
    with r.sessions() as db:
        cart=db.get(POSCart,cartid);p={k:cart.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']};p.update(customer_id=cid,phone='+96550012345')
        with pytest.raises(HTTPException):service.edit_cart(db,db.get(User,r.uid),cartid,version,'details',p,r.pos)
        db.rollback();assert db.get(POSCart,cartid).payload['customer_id'] is None
