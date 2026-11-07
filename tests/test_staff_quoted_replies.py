"""Staff quote selection, scoped sending and durable retry binding; no live traffic."""
import json
import shutil
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import pytest
from sqlalchemy import text
from alembic import command
from alembic.config import Config
import main
import database
import whatsapp_service
from models import WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,WhatsAppManualSend as Manual,GlobalAIReplyControl
from test_whatsapp_quick_messages import inbox,create


def seed(sessions,cid,provider='wamid.customer-original',kind='text'):
    with sessions() as db:
        row=Message(phone_number=db.get(Conversation,cid).phone_number,direction='inbound',message_text='Is this available in pink?',
            whatsapp_message_id=provider,media_id='fixture-audio' if kind=='audio' else None,created_at=datetime.utcnow())
        db.add(row);db.commit();return row.id


def post(client,cid,csrf,mid,key='quoted-send-key',**extra):
    return client.post(f'/crm/whatsapp/api/conversations/{cid}/send',json={'message':'Yes, pink is available.',
        'reply_to_message_id':mid,**extra},headers={'Idempotency-Key':key,'X-CSRF-Token':csrf})


@pytest.mark.parametrize('kind',['text','audio'])
def test_quote_transport_persistence_quick_messages_and_replay(inbox,monkeypatch,kind):
    client,sessions,ids,sends,login,csrf=inbox;mid=seed(sessions,ids[0],kind=kind);calls=[]
    monkeypatch.setattr(main,'send_whatsapp_text',lambda *args,**kw:calls.append((args,kw)) or 'wamid.quoted-staff')
    with sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    assert post(client,ids[0],csrf,mid).status_code==200
    assert post(client,ids[0],csrf,mid).status_code==200 and len(calls)==1
    assert calls[0][1]['reply_to_message_id']=='wamid.customer-original'
    with sessions() as db:
        assert db.query(Manual).one().reply_to_whatsapp_message_id=='wamid.customer-original'
        assert db.query(Message).filter_by(direction='outbound').one().reply_to_whatsapp_message_id=='wamid.customer-original'
        assert db.get(GlobalAIReplyControl,1).enabled is False
    payload=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()
    assert payload['messages'][-1]['reply_context']['message_id']==mid
    assert payload['messages'][0]['can_reply'] is True
    other=seed(sessions,ids[0],provider='wamid.another')
    assert post(client,ids[0],csrf,other).status_code==409
    assert post(client,ids[0],csrf,None).status_code==409
    quick=create(client,csrf).json()['message']['id']
    monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:calls.append((a,kw)) or 'wamid.quick-quoted')
    assert post(client,ids[0],csrf,mid,key='quick-quoted',quick_message_id=quick).status_code==200
    with sessions() as db:assert db.query(Manual).filter_by(idempotency_key='quick-quoted').one().quick_message_id==quick


@pytest.mark.parametrize('case',['other_customer','missing','no_provider','invalid','csrf','window','consent','ambiguous'])
def test_invalid_quotes_cannot_send(inbox,case):
    client,sessions,ids,sends,login,csrf=inbox;mid=seed(sessions,ids[0]);target=mid;token=csrf;extra={}
    if case=='other_customer':target=seed(sessions,ids[1],provider='wamid.other')
    if case=='missing':target=999999
    if case=='no_provider':target=seed(sessions,ids[0],provider=None)
    if case=='invalid':target=True
    if case=='csrf':token='invalid'
    if case=='consent':extra={'marketing_request_id':1}
    with sessions() as db:
        if case=='window':
            db.get(Conversation,ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(days=2)
            db.get(Message,mid).created_at=datetime.utcnow()-timedelta(days=2)
        if case=='ambiguous':db.add(Message(phone_number=db.get(Conversation,ids[0]).phone_number,direction='inbound',message_text='duplicate',whatsapp_message_id='wamid.customer-original'))
        db.commit()
    assert post(client,ids[0],token,target,**extra).status_code in {403,404,409,422}
    assert not sends
    with sessions() as db:assert db.query(Manual).count()==0


def test_other_staff_scope_and_missing_ack_never_fall_back_to_unquoted(inbox,monkeypatch):
    client,sessions,ids,sends,login,csrf=inbox;mid=seed(sessions,ids[0]);token=login('quick-agent')
    assert post(client,ids[0],token,mid).status_code==404 and not sends
    csrf=login();calls=[]
    def mismatch(*args,**kw):calls.append(kw);raise TypeError('simulated transport mismatch')
    monkeypatch.setattr(main,'send_whatsapp_text',mismatch)
    assert post(client,ids[0],csrf,mid).status_code==502 and len(calls)==1
    assert post(client,ids[0],csrf,mid).status_code==409 and len(calls)==1
    monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:True)
    assert post(client,ids[0],csrf,mid,key='uncertain-quote').status_code==503
    assert post(client,ids[0],csrf,mid,key='uncertain-quote').status_code==409


def test_meta_payload_includes_context_only_for_quotes(monkeypatch):
    calls=[]
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_ACCESS_TOKEN','fixture-token')
    monkeypatch.setattr(whatsapp_service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','fixture-phone-id')
    def fake(url,**kw):
        calls.append(kw['json']);return httpx.Response(200,json={'messages':[{'id':'wamid.accepted'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(whatsapp_service.httpx,'post',fake)
    assert whatsapp_service.send_whatsapp_text('94770000000','Reply',return_message_id=True,reply_to_message_id='wamid.original')=='wamid.accepted'
    assert calls[-1]['context']=={'message_id':'wamid.original'}
    whatsapp_service.send_whatsapp_text('94770000000','Ordinary text')
    assert 'context' not in calls[-1] and len(calls)==2


def test_additive_quote_migration_retains_old_attempts(inbox,monkeypatch):
    client,sessions,ids,*_=inbox
    engine=sessions.kw['bind'];monkeypatch.setattr(database,'engine',engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE whatsapp_manual_sends DROP COLUMN reply_to_whatsapp_message_id'))
        conn.execute(text("INSERT INTO whatsapp_manual_sends(idempotency_key,conversation_id,message_text,status,sent_by,created_at) VALUES('old-claim',:cid,'Retained','SENT','quick-admin',CURRENT_TIMESTAMP)"),{'cid':ids[0]})
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261107_26');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        row=db.query(Manual).one();assert row.reply_to_whatsapp_message_id is None and row.message_text=='Retained'
    command.downgrade(cfg,'20261107_26');command.upgrade(cfg,'head')
    with sessions() as db:assert db.query(Manual).one().message_text=='Retained'


def test_browser_quote_preview_cancel_send_and_chat_switch(inbox,monkeypatch):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,login,csrf=inbox;mid=seed(sessions,ids[0]);seed(sessions,ids[1],provider='wamid.second-customer');seed(sessions,ids[0],provider=None)
    calls=[];monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:calls.append(kw) or 'wamid.browser-reply')
    def route(request_route):
        req=request_route.request;url=urlsplit(req.url)
        if url.hostname!='inbox.test':request_route.fulfill(status=200,body='');return
        headers={k:v for k,v in req.headers.items() if k.lower() in {'content-type','idempotency-key','x-csrf-token'}}
        response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers=headers,follow_redirects=False)
        request_route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':900});page.route('**/*',route)
        page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}');page.locator(f'[data-message-id="{mid}"] .message-reply-action').focus();page.keyboard.press("Enter")
        assert page.locator('#quoted-reply').is_visible() and page.locator('#message-input').evaluate('(el)=>el===document.activeElement')
        assert 'pink' in page.locator('#quoted-reply-text').inner_text()
        page.locator('#quoted-reply-cancel').click();assert page.locator('#quoted-reply').is_hidden() and not calls
        page.locator(f'[data-message-id="{mid}"] .message-reply-action').focus();page.keyboard.press("Enter");page.locator('#message-input').fill('Yes, pink is available.')
        page.screenshot(path='/tmp/inbox-quoted-reply-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path='/tmp/inbox-quoted-reply-mobile.png',full_page=True)
        page.locator('#quoted-reply-cancel').click()
        def swipe(dx,dy=0,cancel=False,interactive=False):
            return page.locator(f'[data-message-id="{mid}"]').evaluate("""(el,args)=>{
              let target=el.querySelector('.bubble');
              if(args.interactive){const control=document.createElement('audio');control.controls=true;target.append(control);target=control;}
              const touch=(x,y)=>new Touch({identifier:1,target,clientX:x,clientY:y});
              target.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[touch(200,200)]}));
              const move=new TouchEvent('touchmove',{bubbles:true,cancelable:true,touches:[touch(200+args.dx,200+args.dy)]});
              target.dispatchEvent(move);
              target.dispatchEvent(new TouchEvent(args.cancel?'touchcancel':'touchend',{bubbles:true,touches:[],changedTouches:[touch(200+args.dx,200+args.dy)]}));
              if(args.interactive)target.remove();return move.defaultPrevented;
            }""",{'dx':dx,'dy':dy,'cancel':cancel,'interactive':interactive})
        assert not swipe(-5) and page.locator('#quoted-reply').is_hidden()
        assert not swipe(-15,90) and page.locator('#quoted-reply').is_hidden()
        assert not swipe(90) and page.locator('#quoted-reply').is_hidden()
        swipe(-80,cancel=True);assert page.locator('#quoted-reply').is_hidden()
        assert not swipe(-80,interactive=True) and page.locator('#quoted-reply').is_hidden()
        assert swipe(-80) and page.locator('#quoted-reply').is_visible()
        assert page.locator(f'[data-message-id="{mid}"]').evaluate('(el)=>!el.style.transform && !el.classList.contains("reply-swiping")')
        assert not calls  # A gesture selects the quote; it never sends.
        page.locator('#quoted-reply-cancel').click()
        # Real browser touch delivery, in addition to directional event edge cases.
        target=page.locator(f'[data-message-id="{mid}"] .bubble');target.scroll_into_view_if_needed();box=target.bounding_box()
        cdp=page.context.new_cdp_session(page);cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True})
        x=box['x']+box['width']*.75;y=box['y']+box['height']*.5
        cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
        for distance in [20,45,80]:
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x-distance,'y':y}]})
        cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        page.locator('#quoted-reply').wait_for(state='visible')
        assert not calls
        page.screenshot(path='/tmp/inbox-swipe-reply-mobile.png',full_page=True)
        page.locator('#send-button').click();page.wait_for_function('document.querySelector("#quoted-reply").hidden')
        assert len(calls)==1 and calls[0]['reply_to_message_id']=='wamid.customer-original'
        page.locator(f'[data-message-id="{mid}"] .message-reply-action').focus();page.keyboard.press("Enter")
        page.locator('#back-list').click();assert page.locator('#quoted-reply').is_hidden()
        page.locator(f'.conversation-row[data-conversation-id="{ids[1]}"]').click();assert page.locator('#quoted-reply').is_hidden()
        for width in [320,360,390,430]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.close()


def test_postgresql_quote_migration_and_retry_binding(monkeypatch):
    import os
    from uuid import uuid4
    from sqlalchemy import create_engine,inspect
    from sqlalchemy.orm import sessionmaker
    from models import Base,User
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Isolated local PostgreSQL not configured')
    admin=create_engine(url)
    assert admin.url.host in {'127.0.0.1','localhost'} and admin.url.database=='pixiepinks_test'
    schema='quoted_'+uuid4().hex
    with admin.begin() as conn:conn.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(url,connect_args={'options':'-csearch_path='+schema})
    try:
        Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine,autoflush=False)
        with sessions() as db:
            db.add(User(username='quote-fixture',role='ADMIN',password_hash='fixture'))
            c=Conversation(phone_number='94770012301',customer_name='Fixture',mode='HUMAN');db.add(c);db.flush();cid=c.id
            db.add(Manual(idempotency_key='preserved-attempt',conversation_id=cid,message_text='Old send',status='SENT',sent_by='quote-fixture'));db.commit()
        with engine.begin() as conn:conn.execute(text('ALTER TABLE whatsapp_manual_sends DROP COLUMN reply_to_whatsapp_message_id'))
        monkeypatch.setattr(database,'engine',engine);cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(cfg,'20261107_26');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
        column=next(c for c in inspect(engine).get_columns('whatsapp_manual_sends') if c['name']=='reply_to_whatsapp_message_id')
        assert column['type'].length==255 and column['nullable'] is True
        with sessions() as db:
            row=db.query(Manual).one();assert row.message_text=='Old send' and row.reply_to_whatsapp_message_id is None
            row.reply_to_whatsapp_message_id='wamid.'+'x'*249;db.commit()
        command.downgrade(cfg,'20261107_26');command.upgrade(cfg,'head')
        with sessions() as db:assert len(db.query(Manual).one().reply_to_whatsapp_message_id)==255
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()
