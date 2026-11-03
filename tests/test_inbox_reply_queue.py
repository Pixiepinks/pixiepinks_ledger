from datetime import datetime, timedelta
import pytest
from models import WhatsAppConversation, WhatsAppConversationMessage as Message, Lead
from test_whatsapp_quick_messages import inbox


def test_reply_turn_failure_restores_oldest_wait_without_changing_lead(inbox):
    client,sessions,ids,_,_,_=inbox
    start=datetime.utcnow()-timedelta(hours=2)
    with sessions() as db:
        phone=db.get(WhatsAppConversation,ids[0]).phone_number
        db.add(Lead(customer_name='Test lead',mobile=phone,status='AWAITING_CUSTOMER_REPLY',whatsapp_conversation_id=ids[0]))
        db.add_all([Message(phone_number=phone,direction='inbound',message_text='Question',created_at=start),
                    Message(phone_number=phone,direction='inbound',message_text='More',created_at=start+timedelta(minutes=20))])
        db.commit()
    url='/crm/whatsapp/api/conversations'
    row=client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations'][0]
    assert row['lead_status']=='AWAITING_CUSTOMER_REPLY'
    assert row['id']==ids[0] and row['waiting_seconds']>=7200
    assert row['waiting_since']==start.isoformat()+'Z'
    with sessions() as db:
        reply=Message(phone_number=phone,direction='outbound',message_text='Answer',send_status='sent',created_at=start+timedelta(minutes=40))
        db.add(reply);db.commit();rid=reply.id
    assert client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations']==[]
    assert client.get(url,params={'reply_queue':'waiting_customer'}).json()['conversations'][0]['id']==ids[0]
    with sessions() as db:db.get(Message,rid).send_status='failed';db.commit()
    restored=client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations'][0]
    assert restored['waiting_since']==row['waiting_since']
    assert restored['lead_status']=='AWAITING_CUSTOMER_REPLY'
    # Reading and reassignment/status metadata cannot reset a historical wait.
    client.post(f'{url}/{ids[0]}/read')
    with sessions() as db:
        c=db.get(WhatsAppConversation,ids[0]);c.mode='AI';c.unread_count=0;db.commit()
    assert client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations'][0]['waiting_since']==row['waiting_since']
    assert client.get(url,params={'reply_queue':'invalid'}).status_code==422


def test_queue_scope_sort_before_limit_and_equal_time_order(inbox):
    client,sessions,ids,_,login,_=inbox
    now=datetime.utcnow()
    with sessions() as db:
        for index,cid in enumerate(ids):
            c=db.get(WhatsAppConversation,cid);c.last_message_at=now+timedelta(minutes=10-index)
            db.add(Message(phone_number=c.phone_number,direction='inbound',message_text='Question',created_at=now-timedelta(hours=index+1)))
        db.commit()
    url='/crm/whatsapp/api/conversations'
    assert client.get(url,params={'reply_queue':'needs_reply','limit':1}).json()['conversations'][0]['id']==ids[1]
    login('quick-staff')
    rows=client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations']
    assert [r['id'] for r in rows]==[ids[0]]
    with sessions() as db:
        phone=db.get(WhatsAppConversation,ids[0]).phone_number
        db.add(Message(phone_number=phone,direction='outbound',message_text='Answer',send_status='SENT',created_at=now));db.commit()
        db.add(Message(phone_number=phone,direction='inbound',message_text='New question same timestamp',created_at=now));db.commit()
    assert client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations'][0]['waiting_since']==now.isoformat()+'Z'


def test_failed_unknown_and_no_history_are_not_waiting_customer(inbox):
    client,sessions,ids,_,_,_=inbox
    with sessions() as db:
        phone=db.get(WhatsAppConversation,ids[0]).phone_number
        for status in ['failed','unknown',None]:
            db.add(Message(phone_number=phone,direction='outbound',message_text='Attempt',send_status=status))
        db.commit()
    rows=client.get('/crm/whatsapp/api/conversations').json()['conversations']
    assert all(r['reply_queue']=='no_activity' for r in rows)


def test_reply_queue_browser_filter_and_badges(inbox):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api')
    chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,_,_,_=inbox
    with sessions() as db:
        phone=db.get(WhatsAppConversation,ids[0]).phone_number
        db.add(Message(phone_number=phone,direction='inbound',message_text='Waiting question',created_at=datetime.utcnow()-timedelta(hours=2)));db.commit()
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/whatsapp')
            page.locator('[data-reply-queue="needs_reply"]').click()
            api.expect(page.locator('.conversation-row')).to_have_count(1)
            api.expect(page.locator('.reply-overdue')).to_contain_text('Waiting 2h')
            page.locator('[data-reply-queue="waiting_customer"]').click()
            api.expect(page.locator('#conversation-list')).to_contain_text('No WhatsApp conversations.')
            assert not errors
        finally:browser.close()


def test_legacy_meta_id_answers_but_failure_restores_and_new_reply_clears(inbox):
    client,sessions,ids,_,_,_=inbox
    url='/crm/whatsapp/api/conversations'
    start=datetime.utcnow()-timedelta(hours=1)
    with sessions() as db:
        phone=db.get(WhatsAppConversation,ids[0]).phone_number
        db.add(Message(phone_number=phone,direction='inbound',message_text='Question',created_at=start))
        old=Message(phone_number=phone,direction='outbound',message_text='Legacy automatic response',whatsapp_message_id='wamid.legacy',created_at=start+timedelta(minutes=1))
        db.add(old);db.commit();mid=old.id
    assert client.get(url,params={'reply_queue':'waiting_customer'}).json()['conversations'][0]['id']==ids[0]
    with sessions() as db:db.get(Message,mid).send_status='failed';db.commit()
    assert client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations'][0]['waiting_since']==start.isoformat()+'Z'
    with sessions() as db:
        db.add(Message(phone_number=phone,direction='outbound',message_text='Replacement delivered',send_status='delivered'));db.commit()
    assert client.get(url,params={'reply_queue':'needs_reply'}).json()['conversations']==[]
