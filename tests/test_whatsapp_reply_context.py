"""Quote capture, scoped historical resolution and authenticated UI regressions."""
from ai_reply_test_support import reply_text

import hashlib
import hmac
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import main
from models import WhatsAppConversation,WhatsAppConversationMessage,WhatsAppMessageAttachment,ProcessedWhatsAppMessage
from whatsapp_reply_service import extract_reply_id
from test_whatsapp_quick_messages import inbox
from test_whatsapp_outbound_media import png


def original(sessions,mid='wamid.original',phone='94770012301',kind='image',text='Jeep photo',caption='Jeep requested',product=None):
    with sessions() as db:
        row=WhatsAppConversationMessage(phone_number=phone,direction='outbound',message_text=text,whatsapp_message_id=mid,
            response_kind='staff_media' if kind else 'staff',created_at=datetime.utcnow()-timedelta(days=2),
            product_title=product,product_price=8500 if product else None)
        db.add(row);db.flush();local_id=row.id
        if kind:
            db.add(WhatsAppMessageAttachment(message_id=row.id,media_id='private-original-media',media_type=kind,
                filename='7001007.png' if kind=='image' else 'original-file',caption=caption,mime_type='image/png' if kind=='image' else None));db.commit()
        else:db.commit()
    return local_id


def deliver(client,monkeypatch,kind='text',reference='wamid.original',mid='wamid.customer-reply',phone='94770012301',context=None):
    monkeypatch.setattr(main.settings,'META_APP_SECRET','reply-test-secret')
    message={'id':mid,'from':phone,'type':kind,'timestamp':str(int(datetime.utcnow().timestamp()))}
    message[kind]={'body':'I want this'} if kind=='text' else {'id':'incoming-media-id','mime_type':'audio/ogg' if kind=='audio' else 'image/png'}
    if context is not None:message['context']=context
    elif reference is not None:message['context']={'id':reference,'from':'private-provider-number','unknown':'private-never-store'}
    body=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}).encode()
    signature='sha256='+hmac.new(b'reply-test-secret',body,hashlib.sha256).hexdigest()
    return client.post('/webhook',content=body,headers={'X-Hub-Signature-256':signature,'content-type':'application/json'})


@pytest.fixture
def replies(inbox,monkeypatch):
    calls=[]
    monkeypatch.setattr(main,'_reply_to_text_message',lambda *args:calls.append(args))
    monkeypatch.setattr(main,'_reply_to_image_message',lambda *args:None)
    monkeypatch.setattr(main,'_send_unsupported_reply',lambda *args:None)
    yield (*inbox,calls)


@pytest.mark.parametrize('mode',['AI','HUMAN'])
@pytest.mark.parametrize('kind',['text','image','audio','video','document','sticker'])
def test_all_supported_messages_persist_reply_atomically_and_idempotently(replies,monkeypatch,mode,kind):
    client,sessions,ids,sends,_,_,calls=replies
    with sessions() as db:db.get(WhatsAppConversation,ids[0]).mode=mode;db.commit()
    target=original(sessions)
    assert deliver(client,monkeypatch,kind).status_code==200
    assert deliver(client,monkeypatch,kind).status_code==200
    with sessions() as db:
        message=db.query(WhatsAppConversationMessage).filter_by(direction='inbound').one()
        assert message.reply_to_whatsapp_message_id=='wamid.original'
        assert db.query(ProcessedWhatsAppMessage).count()==1
        assert db.get(WhatsAppConversation,ids[0]).mode==mode
    payload=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()
    quote=payload['messages'][-1]['reply_context']
    assert quote['message_id']==target and quote['type']=='image' and quote['text']=='Jeep requested' and quote['author']=='Staff'
    assert quote['preview_url'].startswith(f'/crm/whatsapp/api/conversations/{ids[0]}/messages/{target}/attachments/')
    for private in ['private-provider-number','private-never-store','private-original-media']:
        assert private not in json.dumps(payload)
    if kind=='text':assert len(calls)==1 and calls[0][4]=='wamid.original'  # Existing AI call contract preserved.
    assert not sends


@pytest.mark.parametrize('context',[{},[],None,{'id':None},{'id':123},{'id':True},{'id':'x'*256},{'id':'bad id'},{'id':'<script>'}])
def test_malformed_or_missing_context_is_safe(replies,monkeypatch,context):
    client,sessions,ids,_,_,_,_=replies
    assert extract_reply_id({'context':context}) is None
    assert deliver(client,monkeypatch,reference=None,context=context).status_code==200
    with sessions() as db:assert db.query(WhatsAppConversationMessage).one().reply_to_whatsapp_message_id is None
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][0]['reply_context'] is None


def test_correct_photo_wins_over_newer_product_and_outside_loaded_window(replies,monkeypatch):
    client,sessions,ids,_,_,_,_=replies
    target=original(sessions)
    original(sessions,mid='wamid.newer-scooter',kind=None,text='Different scooter product',product='Scooter')
    assert deliver(client,monkeypatch).status_code==200
    payload=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}?limit=1').json()
    assert len(payload['messages'])==1 and payload['messages'][0]['text']=='I want this'
    quote=payload['messages'][0]['reply_context'];assert quote['message_id']==target and quote['text']=='Jeep requested'
    assert 'Scooter' not in json.dumps(quote)


def test_unavailable_other_conversation_self_and_duplicate_originals(replies,monkeypatch):
    client,sessions,ids,_,_,_,_=replies
    original(sessions,phone='94770012302',mid='wamid.private',kind=None,text='Other customer private text')
    for n,reference in enumerate(['wamid.missing','wamid.private','wamid.reply-self']):
        assert deliver(client,monkeypatch,reference=reference,mid='wamid.reply-self' if n==2 else f'wamid.missing-{n}').status_code==200
    original(sessions,mid='wamid.ambiguous',kind=None,text='First');original(sessions,mid='wamid.ambiguous',kind=None,text='Second')
    assert deliver(client,monkeypatch,reference='wamid.ambiguous',mid='wamid.ambiguous-reply').status_code==200
    payload=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()
    assert all(row['reply_context']=={'available':False} for row in payload['messages'] if row['direction']=='inbound')
    assert 'Other customer private text' not in json.dumps(payload)
    anonymous=TestClient(main.app)
    assert anonymous.get(f'/crm/whatsapp/api/conversations/{ids[0]}',follow_redirects=False).status_code==303


@pytest.mark.parametrize('kind',['image','sticker','video','document','audio',None])
def test_quoted_attachment_labels_and_text_escape_data(replies,monkeypatch,kind):
    client,sessions,ids,_,_,_,_=replies
    text='<script>alert(1)</script> original https://www.pixiepinks.shop'
    original(sessions,kind=kind,text=text,caption=None)
    assert deliver(client,monkeypatch).status_code==200
    quote=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][-1]['reply_context']
    assert quote['type']==(kind or 'text')
    assert bool(quote['preview_url'])==(kind in {'image','sticker'})
    if kind is None:assert quote['text']==text


def test_rollback_retry_concurrent_delivery_and_late_original(replies,monkeypatch):
    client,sessions,ids,_,_,_,_=replies
    commit=sessions.class_.commit
    with monkeypatch.context() as patch:
        patch.setattr(sessions.class_,'commit',lambda db:(_ for _ in ()).throw(OperationalError('safe-test',{},Exception('simulated'))))
        assert deliver(client,monkeypatch).status_code==503
    with sessions() as db:assert db.query(ProcessedWhatsAppMessage).count()==db.query(WhatsAppConversationMessage).count()==0
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(lambda _:deliver(client,monkeypatch),range(2)))
    assert all(response.status_code==200 for response in responses)
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count()==db.query(WhatsAppConversationMessage).count()==1
        assert db.query(WhatsAppConversationMessage).one().reply_to_whatsapp_message_id=='wamid.original'
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][0]['reply_context']=={'available':False}
    target=original(sessions)
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][-1]['reply_context']['message_id']==target


def test_batch_resolution_does_not_query_per_reply(replies):
    client,sessions,ids,_,_,_,_=replies
    original(sessions)
    with sessions() as db:
        db.add_all([WhatsAppConversationMessage(phone_number='94770012301',direction='inbound',message_text=f'Reply {n}',reply_to_whatsapp_message_id='wamid.original') for n in range(100)]);db.commit();engine=db.get_bind()
    counts=[]
    def statement(conn,cursor,sql,*args):counts.append(sql)
    event.listen(engine,'before_cursor_execute',statement)
    try:
        one=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}?limit=1');first=len(counts);counts.clear()
        many=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}?limit=100');second=len(counts)
    finally:event.remove(engine,'before_cursor_execute',statement)
    assert one.status_code==many.status_code==200 and first==second
    assert all(row['reply_context']['available'] for row in many.json()['messages'])


def test_chromium_photo_quote_missing_text_and_stable_polling(replies,monkeypatch):
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,_,_,_,_=replies
    target=original(sessions);original(sessions,mid='wamid.text',kind=None,text='<img src=x onerror="window.injected=1"> Original text')
    deliver(client,monkeypatch);deliver(client,monkeypatch,reference='wamid.text',mid='wamid.text-reply')
    deliver(client,monkeypatch,reference='wamid.missing',mid='wamid.missing-reply')
    monkeypatch.setattr(main,'download_whatsapp_attachment',lambda *args:(png(),'image/png'))
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                parsed=urlsplit(route.request.url)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(route.request.method,parsed.path+('?' + parsed.query if parsed.query else ''),follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            photo=page.locator('.message-quote').filter(has_text='Jeep requested');photo.locator('img').wait_for()
            page.wait_for_function('document.querySelector(".message-quote img").naturalWidth>0')
            assert f'/messages/{target}/attachments/' in photo.locator('a').get_attribute('href')
            assert 'Replying to Staff' in photo.inner_text()
            assert page.locator('.message-quote').filter(has_text='Original message unavailable').count()==1
            text=page.locator('.message-quote').filter(has_text='Original text');assert text.locator('img,script').count()==0 and page.evaluate('window.injected') is None
            page.evaluate('window.keptQuote=document.querySelector(".message-quote img")');page.wait_for_timeout(4300)
            assert page.evaluate('window.keptQuote===document.querySelector(".message-quote img")') and not errors
            page.set_viewport_size({'width':375,'height':800});assert photo.is_visible()
        finally:browser.close()


def test_automated_text_reply_retains_provider_id_for_future_quote(inbox,monkeypatch):
    from bot_configuration_service import invalidate_cache
    client,sessions,ids,sends,_,_=inbox
    invalidate_cache()
    monkeypatch.setattr(main,'generate_customer_reply',lambda *args:'Automated reply with unchanged content.')
    try:
        reply_text(main, '94770012302','wamid.incoming','Hello','Customer')
        with sessions() as db:
            source=db.query(WhatsAppConversationMessage).filter_by(direction='outbound').one()
            assert source.whatsapp_message_id=='wamid.quick-1' and source.message_text=='Automated reply with unchanged content.'
            target=source.id
        assert deliver(client,monkeypatch,phone='94770012302',reference='wamid.quick-1',mid='wamid.quote-ai').status_code==200
        # Fetch the incoming reply directly; the preserved original ID identifies
        # exactly the automated message, not another nearby answer.
        messages=client.get(f'/crm/whatsapp/api/conversations/{ids[1]}').json()['messages']
        reply=next(row for row in messages if row['text']=='I want this')
        assert reply['reply_context']['message_id']==target
    finally:invalidate_cache()
