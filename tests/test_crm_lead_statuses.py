"""Additional pipeline states share validation, filters and existing lead workflows."""
from datetime import date
import pytest
import main
from models import Lead,WhatsAppConversation
from test_whatsapp_quick_messages import inbox
from test_crm_whatsapp_leads import BASE,save

ADDITIONAL={
    'AWAITING_CUSTOMER_REPLY':'Awaiting Customer Reply',
    'NOT_RESPONDING':'Not Responding',
    'OUT_OF_STOCK':'Out of Stock',
    'NOT_INTERESTED':'Not Interested',
    'INVALID_SPAM':'Invalid/Spam',
    'FUTURE_BUYER':'Future Buyer',
    'PAID':'Paid',
    'DISPATCHED':'Dispatched',
}


@pytest.mark.parametrize('status,label',list(ADDITIONAL.items()))
def test_new_states_are_saved_filtered_and_rendered_in_both_crm_views(inbox,status,label):
    client,sessions,ids,sends,_,csrf=inbox
    metadata=client.get(BASE).json();assert status in metadata['statuses'] and metadata['status_labels'][status]==label
    response=save(client,ids[0],csrf,{'status':status,'next_followup':str(date.today())})
    assert response.status_code==200
    lid=response.json()['lead']['id']
    assert client.get(BASE,params={'status':status}).json()['items'][0]['lead']['id']==lid
    html=client.get('/crm/leads?view=saved').text
    assert f'value="{status}">{label}</option>' in html and label in client.get(f'/crm/leads/{lid}').text
    with sessions() as db:
        assert db.get(Lead,lid).status==status and db.get(WhatsAppConversation,ids[0]).mode=='HUMAN'
    assert not sends


def test_closed_new_statuses_stop_reminders_but_future_buyers_remain_active(inbox):
    client,sessions,_,_,_,_=inbox
    with sessions() as db:
        for status in ['FUTURE_BUYER','NOT_INTERESTED','INVALID_SPAM','LOST','DELIVERED']:
            db.add(Lead(lead_no='LD-'+status,customer_name='Customer '+status,mobile='94770000000',status=status,next_followup=date.today()))
        db.commit()
    response=client.get('/crm');assert response.status_code==200
    assert response.context['followups_today']==1
    assert [lead.status for lead in response.context['pending_followups']]==['FUTURE_BUYER']


def test_saved_lead_create_and_followup_accept_future_buyer(inbox):
    client,sessions,_,sends,_,_=inbox
    response=client.post('/crm/leads',data={'customer_name':'Future customer','mobile':'44770000000','status':'FUTURE_BUYER'},follow_redirects=False)
    assert response.status_code==303
    with sessions() as db:lead=db.query(Lead).one();assert lead.status=='FUTURE_BUYER';lid=lead.id
    response=client.post(f'/crm/leads/{lid}/followup',data={'task_date':str(date.today()),'status':'AWAITING_CUSTOMER_REPLY','note':'Customer deciding'},follow_redirects=False)
    assert response.status_code==303
    with sessions() as db:assert db.get(Lead,lid).status=='AWAITING_CUSTOMER_REPLY'
    assert not sends
