from uuid import uuid4
from models import Customer,SalesOrder,JournalEntry
from loyalty_models import LoyaltyMember
from test_loyalty import loyalty,inbox,payload

def test_shared_and_multiple_recipients_never_transfer_customer_identity(loyalty):
    v=loyalty
    with v.sessions() as db:
        other=Customer(name='Overseas second purchaser',phone='+96550012345');db.add(other);db.commit();other_id=other.id
    ids=[]
    for cid,name,phone,recipient,local in [(v.cid,'Existing buyer','94771234567','Shared recipient','0771234567'),(other_id,'Overseas second purchaser','96550012345','Shared recipient','0771234567'),(v.cid,'Existing buyer','94771234567','Another recipient','0773322111')]:
        p=payload(cid,customer_name=name,whatsapp_phone=phone,delivery_contact_person=recipient,delivery_phone=local)
        r=v.client.post('/api/sales-orders',json=p,headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':str(uuid4())});assert r.status_code==201,r.text;ids.append(r.json()['order']['id'])
    with v.sessions() as db:
        rows=[db.get(SalesOrder,i) for i in ids]
        assert [o.customer_id for o in rows]==[v.cid,other_id,v.cid]
        assert rows[0].delivery_phone==rows[1].delivery_phone and rows[0].delivery_phone!=rows[2].delivery_phone
        assert db.query(Customer).count()==2 and db.query(LoyaltyMember).count()==0 and db.query(JournalEntry).count()==0
        assert db.get(Customer,other_id).phone=='+96550012345'
    current=v.client.get(f'/api/sales-orders/{ids[1]}/delivery-location').json()
    body={'version':current['version'],'city':'Colombo','district':'Western','postal_code':'','delivery_contact_person':'Corrected recipient','delivery_phone':'0773322111','delivery_notes':'Gift\nLeave at gate'}
    response=v.client.put(f'/api/sales-orders/{ids[1]}/delivery-location',json=body,headers={'X-CSRF-Token':v.csrf});assert response.status_code==200,response.text
    with v.sessions() as db:
        o=db.get(SalesOrder,ids[1]);assert o.customer_id==other_id and o.whatsapp_phone=='96550012345' and o.customer_name=='Overseas second purchaser'
        assert db.query(JournalEntry).count()==0 and db.query(LoyaltyMember).count()==0
    body.update(version=response.json()['version'],customer_id=v.cid)
    assert v.client.put(f'/api/sales-orders/{ids[1]}/delivery-location',json=body,headers={'X-CSRF-Token':v.csrf}).status_code==422
    assert not v.sends
