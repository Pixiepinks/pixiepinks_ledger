from datetime import datetime,timedelta,timezone
import pytest
from models import (WhatsAppConversationMessage as Message,ProcessedWhatsAppMessage as Receipt,WhatsAppManualSend as Manual,
    WhatsAppConversation as Conversation,WhatsAppQuickMessage as Quick,User,CRMAnalyticsPhone as Phone,CRMAnalyticsDelivery as Delivery)
from test_whatsapp_quick_messages import inbox
import crm_dashboard_service as service


def test_empty_dashboard_is_private_and_available(inbox):
    client,sessions,ids,*_=inbox
    response=client.get('/crm/dashboard/api')
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='private, no-store'
    assert response.json()['metrics']['staff_sent']==0
    assert client.get('/crm').status_code==200


def test_sql_dedup_categories_and_first_recorded_history(inbox):
    client,sessions,ids,sends,login,csrf=inbox
    now=datetime.utcnow()-timedelta(seconds=3)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        db.add_all([Message(phone_number=phone,direction='inbound',message_text='old',whatsapp_message_id='old-in',created_at=now-timedelta(days=40)),
            Message(phone_number=phone,direction='inbound',message_text='hello',whatsapp_message_id='in-1',created_at=now),
            Message(phone_number=phone,direction='inbound',message_text='duplicate legacy',whatsapp_message_id='in-1',created_at=now),
            Receipt(message_id='in-1',sender_phone=phone,message_type='text',received_at=now),Receipt(message_id='in-2',sender_phone=phone,message_type='interactive',received_at=now)])
        db.add_all([Message(phone_number=phone,direction='outbound',message_text='text',response_kind='staff',sent_by='quick-staff',send_status='sent',whatsapp_message_id='out-1',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='duplicate',response_kind='staff',sent_by='quick-staff',send_status='sent',whatsapp_message_id='out-1',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='saved',response_kind='staff_saved_voice',sent_by='quick-staff',send_status='read',whatsapp_message_id='voice-1',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='automatic',response_kind='ai',send_status='sent',whatsapp_message_id='ai-1',created_at=now)])
        quick=db.query(Quick).first();db.add(Manual(idempotency_key='test-quick',conversation_id=ids[0],quick_message_id=quick.id,message_text='quick',status='SENT',whatsapp_message_id='quick-1',sent_by='quick-staff',created_at=now))
        db.add(Message(phone_number=phone,direction='outbound',message_text='quick',response_kind='staff',sent_by='quick-staff',send_status='sent',whatsapp_message_id='quick-1',created_at=now))
        db.add(Manual(idempotency_key='failed',conversation_id=ids[0],message_text='failed',status='FAILED',sent_by='quick-staff',created_at=now))
        db.commit()
    response=client.get('/crm/dashboard/api');assert response.status_code==200,response.text
    data=response.json();m=data['metrics']
    assert m['unique_contacts']==1 and m['first_contacts']==0 and m['incoming']==2
    assert m['staff_sent']==3 and m['contacted']==1 and m['ai_sent']==1
    assert m['text']==1 and m['saved_voices']==1 and m['quick_messages']==1 and m['failed_attempts']==1
    assert m['read']==1 and m['delivered']==1
    assert sum(m[key] for key in service.CATEGORIES)==m['staff_sent']
    assert sum(row['total'] for row in data['staff_activity']['rows'])==3
    assert not sends


@pytest.mark.parametrize('preset',['today','yesterday','this_week','last_week','last_7_days','this_month','last_month','last_30_days'])
def test_date_presets(preset):
    now=datetime(2026,11,7,18,45,tzinfo=timezone.utc)  # Sunday 00:15 in Sri Lanka.
    w=service.window(preset,now=now)
    assert w.start<w.end
    if preset=='today':assert w.start==datetime(2026,11,7,18,30)
    if preset=='last_7_days':assert w.end-w.start==timedelta(days=7)
    if preset=='last_week':assert w.start==datetime(2026,10,25,18,30) and w.end==datetime(2026,11,1,18,30)
    if preset=='last_month':assert w.start==datetime(2026,9,30,18,30) and w.end==datetime(2026,10,31,18,30)


def test_custom_dates_inclusive_and_invalid():
    w=service.window('custom','2026-12-31','2027-01-01')
    assert w.start==datetime(2026,12,30,18,30) and w.end==datetime(2027,1,1,18,30)
    from fastapi import HTTPException
    for args in [('custom','',''),('custom','2026-11-08','2026-11-07'),('invalid','','')]:
        with pytest.raises(HTTPException):service.window(*args)


def test_scope_section_grants_shared_access_and_filters(inbox):
    from models import ConversationShare,Lead
    client,sessions,ids,sends,login,csrf=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        staff=db.query(User).filter_by(username='quick-staff').one();staff.section_access=['crm','whatsapp'];sid=staff.id
        other=db.query(User).filter_by(username='quick-agent').one();oid=other.id
        hidden=Conversation(phone_number='442079460123',customer_name='Private overseas',assigned_to_user_id=oid);db.add(hidden);db.flush();hid=hidden.id
        for cid in [ids[0],ids[1],hid]:
            c=db.get(Conversation,cid);db.add(Message(phone_number=c.phone_number,direction='inbound',message_text='private',whatsapp_message_id='scope-'+str(cid),created_at=now))
        db.add(Lead(customer_name='Restricted lead',mobile='442079460123',status='NEGOTIATING',whatsapp_conversation_id=hid,created_at=now));db.commit()
    login('quick-staff')
    response=client.get('/crm/dashboard/api');assert response.status_code==200,response.text
    data=response.json();assert data['metrics']['unique_contacts']==1 and data['staff_activity'] is None and data['pipeline'] is None
    assert data['attention']['counts']['followups']==0 and data['attention']['counts']['payment'] is None
    assert 'GB' not in [c['value'] for c in data['filters']['countries']]
    assert [u['id'] for u in data['filters']['staff']]==[sid]
    assert client.get('/crm/dashboard/api?staff_id='+str(oid)).status_code==403
    rows=client.get('/crm/dashboard/api?detail=incoming').json()['detail']['rows'];assert len(rows)==1 and 'Private overseas' not in str(rows)
    assert client.get('/crm/dashboard/api?detail=pipeline').status_code==403
    with sessions() as db:db.add(ConversationShare(conversation_id=hid,user_id=sid,granted_by=1,can_reply=False));db.commit()
    # Read the model's actual share permission below if fixture fields evolve.
    response=client.get('/crm/dashboard/api');assert response.status_code==200,response.text
    assert response.json()['metrics']['unique_contacts']==2
    with sessions() as db:
        staff=db.get(User,sid);staff.section_access=['crm'];db.commit()
    limited=client.get('/crm/dashboard/api').json();assert limited['metrics'] is None and limited['filters']['countries']==[]
    assert client.get('/crm/dashboard/api?country=GB').status_code==403
    with sessions() as db:db.get(User,sid).section_access=['whatsapp'];db.commit()
    assert client.get('/crm/dashboard/api').status_code==403


def test_aliases_unique_customers_across_staff_and_actor_snapshot_survives_assignment(inbox):
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        c=db.get(Conversation,ids[0]);phone=c.phone_number
        alias=Conversation(phone_number='+'+phone,customer_name='Same contact');db.add(alias)
        db.add_all([Message(phone_number=phone,direction='inbound',message_text='one',whatsapp_message_id='alias-in-1',created_at=now),
            Message(phone_number='+'+phone,direction='inbound',message_text='two',whatsapp_message_id='alias-in-2',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='staff1',whatsapp_message_id='alias-out-1',response_kind='staff',sent_by='quick-staff',send_status='sent',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='staff2',whatsapp_message_id='alias-out-2',response_kind='staff',sent_by='quick-agent',send_status='sent',created_at=now)])
        db.commit();agent=db.query(User).filter_by(username='quick-agent').one();c.assigned_to_user_id=agent.id;agent.is_active=False;db.commit()
    data=client.get('/crm/dashboard/api').json();assert data['metrics']['unique_contacts']==1 and data['metrics']['contacted']==1
    assert sum(r['customers'] for r in data['staff_activity']['rows'])==2
    names={r['name']:r['total'] for r in data['staff_activity']['rows']};assert names=={'quick-staff':1,'quick-agent':1}
    with sessions() as db:sid=db.query(User.id).filter_by(username='quick-staff').scalar()
    filtered=client.get('/crm/dashboard/api?staff_id='+str(sid)).json()
    assert filtered['metrics']['staff_sent']==1 and filtered['metrics']['incoming']==0
    detail=client.get('/crm/dashboard/api?detail=staff_sent&actor_id='+str(sid)).json()['detail'];assert detail['total']==1


def test_delivery_chronology_and_failed_attempts_are_separate(inbox,monkeypatch):
    import main
    from models import GlobalAIReplyControl
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(minutes=1)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        db.add(Message(phone_number=phone,direction='outbound',message_text='staff',whatsapp_message_id='delivery-one',response_kind='staff',sent_by='quick-admin',send_status='sent',created_at=now))
        db.add(Manual(idempotency_key='accepted-later-failed',conversation_id=ids[0],sent_by='quick-admin',message_text='staff',status='FAILED',whatsapp_message_id='delivery-one',created_at=now))
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setattr(main.settings,'META_APP_SECRET','')
    def callback(status,at):return client.post('/webhook',json={'entry':[{'changes':[{'field':'messages','value':{'statuses':[{'id':'delivery-one','status':status,'timestamp':str(int(at.timestamp()))}]}}]}]})
    assert callback('read',now+timedelta(seconds=20)).status_code==200
    assert callback('delivered',now+timedelta(seconds=10)).status_code==200
    assert callback('failed',now+timedelta(seconds=5)).status_code==200
    m=client.get('/crm/dashboard/api').json()['metrics']
    assert m['staff_sent']==1 and m['delivered']==1 and m['read']==1 and m['delivery_failed']==0 and m['failed_attempts']==0
    assert callback('failed',now+timedelta(seconds=30)).status_code==200
    m=client.get('/crm/dashboard/api').json()['metrics'];assert m['staff_sent']==1 and m['delivery_failed']==1 and m['failed_attempts']==0
    with sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False


def test_unknown_history_and_acceptance_claim_only_are_visible_without_guessing(inbox):
    from sqlalchemy import text
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        db.add(Message(phone_number=phone,direction='outbound',message_text='ambiguous',whatsapp_message_id='unknown-history',send_status='sent',created_at=now))
        db.add(Manual(idempotency_key='claim-only',conversation_id=ids[0],message_text='legacy',whatsapp_message_id='orphan-accepted',status='SENT',sent_by='former-staff',created_at=now));db.commit()
        db.execute(text("UPDATE whatsapp_conversation_messages SET analytics_actor_id=NULL,analytics_actor_kind=NULL,analytics_category=NULL WHERE whatsapp_message_id='unknown-history'"));db.commit()
    data=client.get('/crm/dashboard/api').json();m=data['metrics']
    assert m['unknown_sent']==1 and m['staff_sent']==1 and m['other']==1 and m['claim_only_sends']==1 and m['unattributed']==1
    assert data['staff_activity']['rows'][0]['total']==1


def test_recorded_referral_and_country_filters_and_drilldown(inbox):
    from models import WhatsAppMessageReferral as Referral
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        msg=Message(phone_number=phone,direction='inbound',message_text='ad inquiry',whatsapp_message_id='ad-in',created_at=now);db.add(msg);db.flush()
        db.add(Referral(message_id=msg.id,source_type='ad',source_id='fixture',captured_at=now))
        other=db.get(Conversation,ids[1]);db.add(Message(phone_number=other.phone_number,direction='inbound',message_text='organic',whatsapp_message_id='organic-in',created_at=now));db.commit()
    ad=client.get('/crm/dashboard/api?source=ad&country=LK');assert ad.status_code==200,ad.text
    assert ad.json()['metrics']['unique_contacts']==1 and ad.json()['metrics']['first_contacts']==1
    detail=client.get('/crm/dashboard/api?source=ad&country=LK&detail=first_contacts').json()['detail'];assert detail['total']==1
    assert client.get('/crm/dashboard/api?source=none').json()['metrics']['incoming']==1
    assert client.get('/crm/dashboard/api?source=arbitrary').status_code==422
    assert client.get('/crm/dashboard/api?period=custom&date_from=2026-11-08&date_to=2026-11-07').status_code==422


def test_deleted_actor_retains_snapshot_and_missing_lookup_fails_honestly(inbox):
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        actor=User(username='former-user',password_hash='unused',role='STAFF');db.add(actor);db.flush();actor_id=actor.id
        db.add(Message(phone_number=phone,direction='outbound',message_text='retained',whatsapp_message_id='deleted-actor',response_kind='staff',send_status='sent',sent_by=actor.username,created_at=now));db.commit()
        db.delete(actor);db.commit()
    data=client.get('/crm/dashboard/api').json();assert data['metrics']['staff_sent']==1
    row=data['staff_activity']['rows'][0];assert row['actor_id']==actor_id and 'former-user' in row['name']
    assert client.get('/crm/dashboard/api?detail=staff_sent&actor_id='+str(actor_id)).json()['detail']['total']==1
    with sessions() as db:db.query(Phone).filter_by(raw_phone=phone).delete();db.commit()
    response=client.get('/crm/dashboard/api');assert response.status_code==503 and 'incomplete' in response.json()['detail']


def test_automatic_messages_never_become_staff_from_owner_name(inbox):
    client,sessions,ids,*_=inbox;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        db.add_all([Message(phone_number=phone,direction='outbound',message_text='auto',whatsapp_message_id='auto-kind',response_kind='ai',sent_by='quick-admin',send_status='sent',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='bot',whatsapp_message_id='bot-kind',response_kind='bicycle',send_status='sent',created_at=now),
            Message(phone_number=phone,direction='outbound',message_text='system',whatsapp_message_id='system-kind',response_kind='unsupported',send_status='sent',created_at=now)])
        db.commit()
    m=client.get('/crm/dashboard/api').json()['metrics'];assert m['staff_sent']==0 and m['ai_sent']==1 and m['system_sent']==1 and m['unknown_sent']==1


def test_categories_collection_items_and_pagination(inbox):
    from models import WhatsAppMessageAttachment as Attachment
    client,sessions,ids,*_=inbox
    now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        kinds=[('staff','text'),('staff_saved_voice','saved_voices'),('staff_audio','recorded_voice')]
        kinds += [('staff_product','products')]*3  # Three separately accepted collection items.
        kinds += [('staff','images_files'),('staff','videos'),('staff','consent'),('staff','quick_messages'),('staff','other')]
        for i,(kind,category) in enumerate(kinds):
            message=Message(phone_number=phone,direction='outbound',response_kind=kind,sent_by='quick-admin',send_status='sent',
                message_text='caption or message',whatsapp_message_id=f'category-{i}',analytics_category=category,created_at=now)
            db.add(message);db.flush()
            if category in {'images_files','videos'}:
                db.add(Attachment(message_id=message.id,media_id=f'media-{i}',media_type='video' if category=='videos' else 'image',caption='Same message'))
        db.add_all([Message(phone_number=phone,direction='inbound',message_text='incoming',whatsapp_message_id=f'page-{i}',created_at=now) for i in range(63)])
        db.commit()
    data=client.get('/crm/dashboard/api').json();m=data['metrics']
    assert m['staff_sent']==11 and m['products']==3 and m['incoming']==63
    assert sum(m[key] for key in service.CATEGORIES)==11
    assert all(m[key]==1 for key in service.CATEGORIES if key!='products')
    first=client.get('/crm/dashboard/api?detail=incoming').json()['detail']
    second=client.get('/crm/dashboard/api?detail=incoming&offset=50').json()['detail']
    assert first['total']==second['total']==63 and len(first['rows'])==50 and len(second['rows'])==13
    assert client.get('/crm/dashboard/api?detail=staff_sent&category=products').json()['detail']['total']==3


def test_year_boundary_monday_and_exact_local_midnight():
    now=datetime(2026,1,4,18,30,tzinfo=timezone.utc)  # Monday Jan 5, 00:00 Colombo.
    assert service.window('this_week',now=now).start==datetime(2026,1,4,18,30)
    assert service.window('last_week',now=now).start==datetime(2025,12,28,18,30)
    assert service.window('last_month',now=now).start==datetime(2025,11,30,18,30)
    assert service.window('last_month',now=now).end==datetime(2025,12,31,18,30)
    leap=service.window('custom','2024-02-29','2024-02-29')
    assert leap.end-leap.start==timedelta(days=1)


def test_exclusive_custom_boundary_and_query_count_is_bounded(inbox):
    from sqlalchemy import event
    client,sessions,ids,*_=inbox
    start=datetime(2026,11,6,18,30);end=start+timedelta(days=1)
    with sessions() as db:
        phone=db.get(Conversation,ids[0]).phone_number
        db.add_all([Message(phone_number=phone,direction='inbound',message_text='boundary',whatsapp_message_id=f'boundary-{i}',created_at=at)
            for i,at in enumerate([start-timedelta(microseconds=1),start,end-timedelta(microseconds=1),end])])
        db.commit()
        report=service.Report(db,db.query(User).filter_by(username='quick-admin').one(),service.window('custom','2026-11-07','2026-11-07'))
        queries=[]
        def track(conn,cursor,statement,parameters,context,many):queries.append(statement)
        event.listen(db.bind,'before_cursor_execute',track)
        try:
            assert report.metrics(report.events())['incoming']==2
            assert report.details('incoming',report.events())['total']==2
        finally:event.remove(db.bind,'before_cursor_execute',track)
        assert len(queries)==3  # One aggregate plus count and bounded detail; independent of record count.
