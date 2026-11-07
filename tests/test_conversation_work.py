"""Operational state, due work, review snapshots and webhook transaction checks."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from models import (WhatsAppConversation as C,WhatsAppConversationMessage as M,ConversationWorkState as W,
    ConversationWorkEvent as E,ProcessedWhatsAppMessage as Receipt,User,Lead,GlobalAIReplyControl,MarketingConsent)
from test_whatsapp_quick_messages import inbox
import main


def seed(sessions,cid,when=None):
    with sessions() as db:
        c=db.get(C,cid);row=M(phone_number=c.phone_number,direction='inbound',message_text='Question',whatsapp_message_id='in-'+uuid4().hex,created_at=when or datetime.utcnow()-timedelta(minutes=5))
        db.add(row);db.get(GlobalAIReplyControl,1).enabled=False;db.commit();return row.id


def current(client,cid):return client.get(f'/crm/whatsapp/api/conversations/{cid}/work-status').json()
def payload(client,cid,action,**extra):
    w=current(client,cid)['work'];return {'action':action,'version':w['version'],'last_inbound_id':w['last_inbound_id'],'review_token':w['review_token'],'key':uuid4().hex,**extra}
def change(client,cid,body):return client.post(f'/crm/whatsapp/api/conversations/{cid}/work-status',json=body,headers={'X-CSRF-Token':client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']})
def queue(client,name):return client.get('/crm/whatsapp/api/conversations',params={'reply_queue':name,'mailbox':'all'}).json()['conversations']
def store(sessions,cid,when=None,provider=None,kind='text'):
    with sessions() as db:phone=db.get(C,cid).phone_number
    provider=provider or 'new-'+uuid4().hex;when=when or datetime.utcnow()+timedelta(seconds=1)
    return main._store_conversation_message(phone,'inbound','New question',whatsapp_message_id=provider,created_at=when,
        processed_record=Receipt(message_id=provider,sender_phone=phone,message_type=kind,received_at=when,processed_at=datetime.utcnow()))


def test_resolve_reopen_history_counts_and_search(inbox):
    client,sessions,ids,sends,login,csrf=inbox;mid=seed(sessions,ids[0])
    assert len(queue(client,'needs_reply'))==1
    request=payload(client,ids[0],'resolve',reason='Handled through another channel')
    assert change(client,ids[0],request).status_code==200
    assert change(client,ids[0],request).status_code==200
    assert queue(client,'needs_reply')==[] and len(queue(client,'resolved'))==1
    assert client.get('/crm/dashboard/api').json()['attention']['counts']['needs_reply']==0
    assert ids[0] in [r['id'] for r in client.get('/crm/whatsapp/api/conversations?search=Quick+customer+1').json()['conversations']]
    with sessions() as db:
        assert db.get(M,mid).message_text=='Question' and db.query(E).count()==1
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert change(client,ids[0],payload(client,ids[0],'reopen')).status_code==200
    assert len(queue(client,'needs_reply'))==1
    assert len(client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/work-history').json()['events'])==2
    assert not sends


def test_followup_reschedule_due_cancel_deduplicates_legacy(inbox):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0])
    with sessions() as db:
        c=db.get(C,ids[0]);db.add_all([Lead(customer_name='Legacy',mobile=c.phone_number,whatsapp_conversation_id=c.id,status='FOLLOW_UP',next_followup=datetime.utcnow().date()-timedelta(days=1)) for _ in range(1)]);db.commit()
    due=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
    assert change(client,ids[0],payload(client,ids[0],'defer',due_at=due)).status_code==200
    assert not queue(client,'needs_reply') and len(queue(client,'follow_up_later'))==1
    assert client.get('/crm/dashboard/api').json()['attention']['counts']['followups']==0
    assert change(client,ids[0],payload(client,ids[0],'defer',due_at=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat())).status_code==200
    with sessions() as db:db.get(W,ids[0]).due_at=datetime.utcnow()-timedelta(minutes=1);db.commit()
    assert len(queue(client,'followups_due'))==1
    dash=client.get('/crm/dashboard/api?queue=followups').json();assert dash['attention']['counts']['followups']==1
    assert dash['attention']['items'][0]['work']['status']=='FOLLOW_UP_LATER'
    assert change(client,ids[0],payload(client,ids[0],'cancel')).status_code==200
    assert queue(client,'followups_due')==[] and len(queue(client,'needs_reply'))==1
    with sessions() as db:assert db.query(Lead).count()==1 and all(l.status=='FOLLOW_UP' for l in db.query(Lead))


@pytest.mark.parametrize('action',['resolve','defer'])
@pytest.mark.parametrize('kind',['text','audio','image','video','document','sticker','interactive'])
def test_new_inbound_reopens_once_and_stale_selection_conflicts(inbox,action,kind):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0]);before=payload(client,ids[0],'resolve')
    extras={'due_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()} if action=='defer' else {}
    assert change(client,ids[0],payload(client,ids[0],action,**extras)).status_code==200
    provider='same-webhook';assert store(sessions,ids[0],provider=provider,kind=kind)=='stored'
    assert store(sessions,ids[0],provider=provider,kind=kind)=='duplicate'
    assert current(client,ids[0])['work']['status']=='NEEDS_REPLY' and len(queue(client,'needs_reply'))==1
    assert change(client,ids[0],before).status_code==409
    with sessions() as db:assert db.query(E).filter_by(trigger='inbound').count()==1


def test_delayed_history_delivery_and_outbound_do_not_reopen(inbox):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0],datetime.utcnow()-timedelta(days=4))
    assert change(client,ids[0],payload(client,ids[0],'resolve')).status_code==200
    assert store(sessions,ids[0],when=datetime.utcnow()-timedelta(days=2))=='stored'
    assert store(sessions,ids[0],kind='reaction')=='stored'
    with sessions() as db:
        phone=db.get(C,ids[0]).phone_number
        db.add(M(phone_number=phone,direction='outbound',message_text='Staff',send_status='read',whatsapp_message_id='outbound-after',created_at=datetime.utcnow()));db.commit()
    assert main._store_conversation_message(phone,'outbound','Staff outbound',whatsapp_message_id='out-store')=='stored'
    assert current(client,ids[0])['work']['status']=='RESOLVED'
    response=client.post('/webhook',json={'entry':[{'changes':[{'value':{'statuses':[{'id':'outbound-after','status':'read','timestamp':str(int(datetime.now().timestamp()))}]}}]}]})
    assert response.status_code==200 and current(client,ids[0])['work']['status']=='RESOLVED'
    assert not queue(client,'needs_reply')


def test_bulk_explicit_selection_partial_conflict_and_idempotence(inbox):
    client,sessions,ids,*_=inbox
    for cid in ids:seed(sessions,cid)
    selected=[{'id':cid,**{k:current(client,cid)['work'][k] for k in ['version','last_inbound_id','review_token']}} for cid in ids]
    assert store(sessions,ids[1])=='stored'
    body={'items':selected,'key':uuid4().hex,'reason':'Reviewed'}
    r=client.post('/crm/whatsapp/api/work-status/bulk',json=body,headers={'X-CSRF-Token':client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']})
    assert r.status_code==200,r.text
    assert [x['ok'] for x in r.json()['results']]==[True,False]
    assert queue(client,'needs_reply')[0]['id']==ids[1]
    again=client.post('/crm/whatsapp/api/work-status/bulk',json=body,headers={'X-CSRF-Token':client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']})
    assert again.status_code==200
    with sessions() as db:assert db.query(E).filter_by(trigger='manual').count()==1


def test_owner_permissions_csrf_window_and_expiry_are_independent(inbox):
    client,sessions,ids,sends,login,csrf=inbox;seed(sessions,ids[0],datetime.utcnow()-timedelta(days=2))
    with sessions() as db:db.get(C,ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    assert len(queue(client,'window_expired'))==1
    request=payload(client,ids[0],'resolve')
    assert client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/work-status',json=request).status_code==403
    login('quick-agent');assert change(client,ids[0],request).status_code==404
    login('quick-staff');assert change(client,ids[0],payload(client,ids[0],'resolve')).status_code==200
    r=client.post('/crm/whatsapp/api/work-status/bulk',json={'items':[]});assert r.status_code==403
    assert change(client,ids[0],payload(client,ids[0],'reopen')).status_code==200
    send=client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/send',json={'message':'Must not send'},headers={'Idempotency-Key':'expired-window-send'})
    assert send.status_code==409 and not sends


@pytest.mark.parametrize('extra',[{'due_at':'2026-01-01T00:00:00+05:30'},{'due_at':'2099-01-01T00:00:00'},{'due_at':'invalid'}])
def test_invalid_schedule_cannot_change_state(inbox,extra):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0]);assert change(client,ids[0],payload(client,ids[0],'defer',**extra)).status_code==422
    with sessions() as db:assert db.query(E).count()==0


def test_signed_review_and_append_only_history(inbox):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0]);body=payload(client,ids[0],'resolve');bad={**body,'review_token':body['review_token']+'x'}
    assert change(client,ids[0],bad).status_code==409
    assert change(client,ids[0],body).status_code==200
    with sessions() as db:
        event=db.query(E).one();event.reason='Edited'
        with pytest.raises(ValueError):db.commit()
        db.rollback();db.delete(db.query(E).one())
        with pytest.raises(ValueError):db.commit()


def test_new_wait_starts_after_acknowledged_history_and_unselected_is_untouched(inbox):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0],datetime.utcnow()-timedelta(days=5));seed(sessions,ids[1])
    original_owner=None
    with sessions() as db:
        original_owner=db.get(C,ids[0]).assigned_to_user_id
        before_consent=[(x.id,x.status) for x in db.query(MarketingConsent)]
    body=payload(client,ids[0],'resolve');work=body.copy();work['id']=ids[0]
    response=client.post('/crm/whatsapp/api/work-status/bulk',json={'items':[work],'key':uuid4().hex},headers={'X-CSRF-Token':client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']})
    assert response.status_code==200 and response.json()['results'][0]['ok']
    assert [c['id'] for c in queue(client,'needs_reply')]==[ids[1]]
    when=datetime.utcnow()+timedelta(seconds=1);assert store(sessions,ids[0],when=when)=='stored'
    returned=next(c for c in queue(client,'needs_reply') if c['id']==ids[0]);assert returned['waiting_since']==when.isoformat()+'Z'
    with sessions() as db:
        assert db.get(C,ids[0]).assigned_to_user_id==original_owner
        assert [(x.id,x.status) for x in db.query(MarketingConsent)]==before_consent


def test_readonly_share_cannot_mutate_and_followup_assignment_does_not_grant_access(inbox):
    from models import ConversationShare
    client,sessions,ids,sends,login,csrf=inbox;seed(sessions,ids[0])
    with sessions() as db:
        agent=db.query(User).filter_by(username='quick-agent').one();aid=agent.id
        db.add(ConversationShare(conversation_id=ids[0],user_id=aid,granted_by=1,can_reply=True));db.commit()
    login('quick-agent');assert current(client,ids[0])['can_manage'] is False
    assert change(client,ids[0],payload(client,ids[0],'resolve')).status_code==403
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/work-history').status_code==200
    login();assert change(client,ids[0],payload(client,ids[0],'defer',due_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),followup_user_id=aid)).status_code==200
    with sessions() as db:assert db.get(C,ids[0]).assigned_to_user_id!=aid


def test_whole_second_customer_time_after_resolution_is_not_hidden_by_older_reply(inbox):
    client,sessions,ids,*_=inbox;seed(sessions,ids[0])
    with sessions() as db:
        phone=db.get(C,ids[0]).phone_number
        answer=M(phone_number=phone,direction='outbound',message_text='Earlier answer',send_status='sent',whatsapp_message_id='earlier-answer',created_at=datetime.utcnow())
        db.add(answer);db.commit();answer_id=answer.id
    assert change(client,ids[0],payload(client,ids[0],'resolve')).status_code==200
    when=datetime.utcnow().replace(microsecond=0)
    assert store(sessions,ids[0],when=when)=='stored'
    # Meta's inbound whole-second precision can sort below a prior local reply's
    # fractional timestamp. Its older local identity must not hide the reopening.
    with sessions() as db:db.get(M,answer_id).created_at=when+timedelta(microseconds=900000);db.commit()
    assert current(client,ids[0])['work']['status']=='NEEDS_REPLY'
    assert [r['id'] for r in queue(client,'needs_reply')]==[ids[0]]
