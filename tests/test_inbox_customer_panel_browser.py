"""Deterministic polling, real Chromium and synthetic audio only."""
import io,json,wave
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from models import Customer,WhatsAppConversation,WhatsAppConversationMessage
from loyalty_models import LoyaltyMember
from test_loyalty import loyalty,inbox,enabled
from test_loyalty_browser import browser
from test_inbox_customer_panel import prepare,url

INIT="""(()=>{const native=setInterval;window.pollIntervals=[];window.setInterval=(fn,ms,...args)=>{window.pollIntervals.push({ms,kind:String(fn).includes('openConversation')?'message':'other'});if(ms===4000&&String(fn).includes('openConversation')){window.messagePoll=fn;return -4000;}if(ms===7000)return -7000;return native(fn,ms,...args);};let offset=0;const clock=performance.now.bind(performance);Object.defineProperty(performance,'now',{value:()=>clock()+offset,configurable:true});window.advancePanelTime=delta=>{offset+=delta;};})();"""


def setup(page,v,cid):
    from playwright.sync_api import expect
    page.add_init_script(INIT);page.goto('https://loyalty.test/crm/whatsapp?conversation='+str(cid))
    expect(page.locator('#chat-name')).not_to_have_text('')
    page.wait_for_function("document.getElementById('customer-panel-notice').textContent!=='Checking customer information…'")


def poll(page,calls,cid):
    before=sum(path=='/crm/whatsapp/api/conversations/'+str(cid) for _,path in calls)
    page.evaluate('window.messagePoll()')
    # Fetch completion and UI reconciliation are drained through a browser sentinel.
    from playwright.sync_api import expect
    page.wait_for_function('typeof window.messagePoll==="function"')
    page.wait_for_timeout(80)
    assert sum(path=='/crm/whatsapp/api/conversations/'+str(cid) for _,path in calls)==before+1


def incoming(v,cid,text='New synthetic incoming message'):
    with v.sessions() as db:
        c=db.get(WhatsAppConversation,cid);row=WhatsAppConversationMessage(phone_number=c.phone_number,direction='inbound',message_text=text,created_at=datetime.utcnow());db.add(row);db.commit();return row.id


@pytest.mark.parametrize('width',[390,1280])
@pytest.mark.parametrize('active',[False,True])
def test_sidebar_stable_polling_requests_and_membership_refresh(loyalty,width,active):
    from playwright.sync_api import expect
    v=loyalty;enabled(v,True);cid=prepare(v) if active else prepare(v,linked=False)
    if not active:
        with v.sessions() as db:db.get(WhatsAppConversation,cid).phone_number='96550012345';db.commit()
    with browser(v,width) as (page,errors,calls):
        setup(page,v,cid);expect(page.locator('#customer-panel-notice')).to_contain_text('Active loyalty member' if active else 'Not registered')
        assert page.locator('#conversation-loyalty').count()==0 and page.locator('.chat-panel #inbox-customer-panel').count()==0
        assert page.locator('#details #inbox-customer-panel').count()==1
        page.evaluate("""()=>{window.keptProfile=document.getElementById('profile-name');window.keptOrder=document.getElementById('order').firstChild;window.keptFields=document.getElementById('customer-panel-fields').firstChild;window.customerMutations=0;new MutationObserver(rows=>window.customerMutations+=rows.length).observe(document.getElementById('inbox-customer-panel'),{childList:true,subtree:true,characterData:true});}""")
        initial=sum(path==url(cid) for _,path in calls)
        for _ in range(5):
            with v.sessions() as db:
                changed=db.get(WhatsAppConversation,cid);changed.unread_count+=1;db.commit()
            poll(page,calls,cid)
        assert sum(path==url(cid) for _,path in calls)==initial==1
        assert page.evaluate("window.keptProfile===document.getElementById('profile-name')&&window.keptOrder===document.getElementById('order').firstChild&&window.keptFields===document.getElementById('customer-panel-fields').firstChild")
        assert page.evaluate('window.customerMutations')==0
        assert page.evaluate("window.pollIntervals.filter(v=>v.ms===4000&&v.kind==='message').length")==1
        assert page.evaluate('window.pollIntervals.filter(v=>v.ms===7000).length')==1
        Path('/tmp/inbox-panel-after-metrics-'+str(width)+'-'+str(active)+'.json').write_text(json.dumps({'polls_after_initial':5,'message_detail_requests':sum(path=='/crm/whatsapp/api/conversations/'+str(cid) for _,path in calls),'customer_requests':sum(path==url(cid) for _,path in calls),'unchanged_panel_mutations':page.evaluate('window.customerMutations'),'stable_profile_order_and_customer_nodes':True},indent=2))
        # The existing responsive details route opens this same sidebar.
        if width<600:page.locator('#details').evaluate("e=>e.classList.add('open')")
        page.screenshot(path=f'/tmp/inbox-panel-{width}-'+('active' if active else 'unregistered')+'.png',full_page=True)
        if active:
            with v.sessions() as db:db.query(LoyaltyMember).filter_by(customer_id=v.cid).one().status='SUSPENDED';db.commit()
            page.evaluate('window.advancePanelTime(12001)');poll(page,calls,cid)
            expect(page.locator('#customer-panel-notice')).to_contain_text('suspended')
            assert sum(path==url(cid) for _,path in calls)==2
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth') and not errors
        if width>=1201:assert page.locator('#details').bounding_box()['x']+page.locator('#details').bounding_box()['width']<=page.locator('#inbox').bounding_box()['x']+page.locator('#inbox').bounding_box()['width']+1


def test_failure_retry_coalescing_invalidation_and_no_false_active(loyalty):
    from playwright.sync_api import expect
    v=loyalty;cid=prepare(v)
    with browser(v) as (page,errors,calls):
        pattern='**/customer-panel';page.route(pattern,lambda r:r.abort('failed'))
        setup(page,v,cid);expect(page.locator('#customer-panel-notice')).to_contain_text('temporarily unavailable')
        assert 'Not registered' not in page.locator('#inbox-customer-panel').inner_text()
        page.unroute(pattern);page.locator('#customer-panel-retry').click();expect(page.locator('#customer-panel-notice')).to_contain_text('Active loyalty member')
        page.evaluate("""()=>{const original=fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/customer-panel'))return new Promise(resolve=>{window.releaseIdentity=()=>resolve(response);});return response;};document.getElementById('inbox').dispatchEvent(new CustomEvent('inbox-customer-invalidated',{detail:{id:Number(document.getElementById('inbox').dataset.activeConversation)}}));}""")
        page.wait_for_function('typeof window.releaseIdentity==="function"')
        count=sum(path==url(cid) for _,path in calls)
        for _ in range(4):poll(page,calls,cid)
        assert sum(path==url(cid) for _,path in calls)==count
        page.evaluate('window.releaseIdentity()');expect(page.locator('#customer-panel-notice')).to_contain_text('Active loyalty member')
        assert not errors


def test_invalid_payload_does_not_invent_active_membership(loyalty):
    from playwright.sync_api import expect
    cid=prepare(loyalty)
    with browser(loyalty) as (page,errors,calls):
        page.route('**/customer-panel',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps({'viewer_id':loyalty.uid,'conversation_id':cid,'state':'ACTIVE'})))
        setup(page,loyalty,cid);expect(page.locator('#customer-panel-notice')).to_contain_text('temporarily unavailable')
        assert 'Active loyalty member' not in page.locator('#inbox-customer-panel').inner_text() and not errors


def test_audio_scroll_composer_pending_attachment_and_new_messages_survive_polling(loyalty):
    from playwright.sync_api import expect
    from test_whatsapp_outbound_media import png
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:
        phone=db.get(WhatsAppConversation,cid).phone_number;start=datetime.utcnow()-timedelta(minutes=5)
        for i in range(55):db.add(WhatsAppConversationMessage(phone_number=phone,direction='inbound',whatsapp_message_id='wamid.stability-audio-'+str(i),message_text='Synthetic older message '+str(i)+' '+('readable content '*15),created_at=start+timedelta(seconds=i),media_id='synthetic-audio' if i==10 else None,media_duration_seconds=40 if i==10 else None))
        db.commit()
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b'\0\0'*8000*40)
    with browser(v) as (page,errors,calls):
        page.route('**/messages/*/media',lambda r:r.fulfill(status=200,content_type='audio/wav',body=buffer.getvalue()))
        setup(page,v,cid);page.locator('#chat-name').click()
        audio=page.locator('#messages audio');audio.wait_for();audio.evaluate("async e=>{e.currentTime=2;e.muted=true;await e.play();window.playing=e;window.pauseCount=0;e.addEventListener('pause',()=>window.pauseCount++);}")
        page.locator('#messages .voice-playback-speed').click();expect(page.locator('#messages .voice-playback-speed')).to_have_text('1.5×')
        page.locator('#messages .message-reply-action').first.hover();page.locator('#messages .message-reply-action').first.click()
        expect(page.locator('#quoted-reply')).to_be_visible()
        composer=page.locator('#message-input');composer.fill('Unsent synthetic customer response');composer.focus();composer.evaluate('(e)=>e.setSelectionRange(3,9)')
        page.locator('#messages').evaluate('(e)=>e.scrollTop=250')
        snapshot=page.evaluate("({top:document.getElementById('messages').scrollTop,time:playing.currentTime})")
        page.evaluate("window.keptComposer=document.getElementById('message-input')")
        for _ in range(3):poll(page,calls,cid)
        incoming(v,cid);poll(page,calls,cid)
        expect(page.locator('#messages')).to_contain_text('New synthetic incoming message')
        result=page.evaluate("({same:playing===document.querySelector('#messages audio'),paused:playing.paused,time:playing.currentTime,rate:playing.playbackRate,pauses:pauseCount,top:document.getElementById('messages').scrollTop,composer:keptComposer===document.getElementById('message-input'),focus:document.activeElement===keptComposer,start:keptComposer.selectionStart,end:keptComposer.selectionEnd})")
        assert result['same'] and not result['paused'] and result['pauses']==0 and result['rate']==1.5 and result['time']>=snapshot['time']
        assert abs(result['top']-snapshot['top'])<3 and result['composer'] and result['focus'] and result['start']==3 and result['end']==9
        expect(composer).to_have_value('Unsent synthetic customer response');expect(page.locator('#quoted-reply')).to_be_visible()
        page.locator('#quoted-reply-cancel').click()
        expect(page.locator('#attachment-button')).to_be_enabled();page.locator('#attachment-file').set_input_files({'name':'pending.png','mimeType':'image/png','buffer':png()})
        expect(page.locator('#attachment-filename')).to_have_text('pending.png')
        page.evaluate("window.keptPreview=document.getElementById('attachment-preview-media').firstChild")
        for _ in range(3):poll(page,calls,cid)
        assert page.evaluate("window.keptPreview===document.getElementById('attachment-preview-media').firstChild")
        expect(composer).to_have_value('Unsent synthetic customer response');expect(page.locator('#attachment-filename')).to_have_text('pending.png')
        page.locator('#messages').evaluate('(e)=>e.scrollTop=e.scrollHeight');incoming(v,cid,'Near-bottom synthetic incoming');poll(page,calls,cid)
        assert page.locator('#messages').evaluate('(e)=>e.scrollHeight-e.scrollTop-e.clientHeight')<5
        assert not errors and not v.sends


def test_switching_is_immediate_scoped_and_keeps_only_own_text_draft(loyalty):
    from playwright.sync_api import expect
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:
        other=db.query(WhatsAppConversation).filter(WhatsAppConversation.id!=cid).first();other.phone_number='96550012345';other_id=other.id;db.commit()
    with browser(v) as (page,errors,calls):
        setup(page,v,cid);page.locator('#message-input').fill('Draft for first customer')
        page.locator('[data-conversation-id="'+str(other_id)+'"]').first.click()
        expect(page.locator('#message-input')).to_have_value('');expect(page.locator('#customer-panel-notice')).to_contain_text('Not registered')
        page.locator('#message-input').fill('Draft for second customer')
        page.locator('[data-conversation-id="'+str(cid)+'"]').first.click()
        expect(page.locator('#message-input')).to_have_value('Draft for first customer');expect(page.locator('#customer-panel-notice')).to_contain_text('Active loyalty member')
        assert not errors


@pytest.mark.parametrize('kind',['image','video','document'])
def test_pending_file_categories_survive_polls_without_transfer(loyalty,kind):
    from playwright.sync_api import expect
    from test_whatsapp_outbound_media import png
    from test_whatsapp_outbound_media_formats import mp4
    from pypdf import PdfWriter
    v=loyalty;cid=prepare(v)
    if kind=='image':name,mime,raw='pending.png','image/png',png()
    elif kind=='video':name,mime,raw='pending.mp4','video/mp4',mp4()
    else:
        out=io.BytesIO();pdf=PdfWriter();pdf.add_blank_page(width=100,height=100);pdf.write(out);name,mime,raw='pending.pdf','application/pdf',out.getvalue()
    with browser(v) as (page,errors,calls):
        setup(page,v,cid);expect(page.locator('#attachment-button')).to_be_enabled()
        page.locator('#attachment-file').set_input_files({'name':name,'mimeType':mime,'buffer':raw})
        expect(page.locator('#attachment-filename')).to_have_text(name)
        page.locator('#attachment-caption').fill('Unsent attachment caption');page.evaluate("window.keptAttachment=document.getElementById('attachment-preview');window.keptCaption=document.getElementById('attachment-caption')")
        for _ in range(3):poll(page,calls,cid)
        expect(page.locator('#attachment-caption')).to_have_value('Unsent attachment caption')
        assert page.evaluate("keptAttachment===document.getElementById('attachment-preview')&&keptCaption===document.getElementById('attachment-caption')")
        assert not errors and not v.sends


def test_recording_survives_polling_and_cannot_move_to_another_customer(loyalty):
    from playwright.sync_api import expect
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:other_id=db.query(WhatsAppConversation.id).filter(WhatsAppConversation.id!=cid).scalar()
    with browser(v) as (page,errors,calls):
        page.add_init_script("""navigator.mediaDevices.getUserMedia=async()=>{const ctx=new AudioContext(),osc=ctx.createOscillator(),gain=ctx.createGain(),dest=ctx.createMediaStreamDestination();gain.gain.value=0;osc.connect(gain);gain.connect(dest);osc.start();await ctx.resume();window.syntheticAudioContext=ctx;return dest.stream;};""")
        setup(page,v,cid);page.locator('#record-button').click();expect(page.locator('#recording-controls')).to_be_visible()
        page.evaluate("window.keptRecording=document.getElementById('recording-controls')")
        for _ in range(3):poll(page,calls,cid)
        expect(page.locator('#recording-controls')).to_be_visible();assert page.evaluate("keptRecording===document.getElementById('recording-controls')")
        page.locator('[data-conversation-id="'+str(other_id)+'"]').first.click()
        expect(page.locator('#send-error')).to_contain_text('cancel the voice recording')
        assert page.locator('#inbox').get_attribute('data-active-conversation')==str(cid)
        page.locator('#cancel-recording').click();expect(page.locator('#recording-controls')).not_to_be_visible()
        assert not errors and not v.sends


def test_late_identity_reply_and_attachment_do_not_cross_conversations(loyalty):
    from playwright.sync_api import expect
    from test_whatsapp_outbound_media import png
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:c=db.query(WhatsAppConversation).filter(WhatsAppConversation.id!=cid).first();c.phone_number='96550012345';other=c.id;db.commit()
    with browser(v) as (page,errors,calls):
        setup(page,v,cid);page.locator('#attachment-file').set_input_files({'name':'first-only.png','mimeType':'image/png','buffer':png()})
        page.evaluate("""()=>{const original=fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).includes('/conversations/"""+str(cid)+"""/customer-panel'))return new Promise(resolve=>{window.releasePriorCustomer=()=>resolve(response);});return response;};document.getElementById('inbox').dispatchEvent(new CustomEvent('inbox-customer-invalidated',{detail:{id:"""+str(cid)+"""}}));}""")
        page.wait_for_function('typeof window.releasePriorCustomer==="function"')
        page.locator('[data-conversation-id="'+str(other)+'"]').first.click()
        expect(page.locator('#customer-panel-notice')).to_contain_text('Not registered')
        page.evaluate('window.releasePriorCustomer()')
        expect(page.locator('#customer-panel-notice')).to_contain_text('Not registered')
        assert page.locator('#customer-panel-fields').inner_text()==''
        expect(page.locator('#attachment-preview')).not_to_be_visible()
        assert not errors


@pytest.mark.parametrize('failure',['forbidden','viewer_changed','invalid_json'])
def test_unavailable_refresh_does_not_leak_authorization_cached_fields(loyalty,failure):
    from playwright.sync_api import expect
    v=loyalty;cid=prepare(v)
    with browser(v) as (page,errors,calls):
        setup(page,v,cid);expect(page.locator('#customer-panel-fields')).to_contain_text('Existing buyer')
        def fail(route):
            if failure=='forbidden':route.fulfill(status=403,content_type='application/json',body='{"detail":"Access denied"}')
            elif failure=='invalid_json':route.fulfill(status=200,content_type='text/plain',body='invalid')
            else:
                data=v.client.get(url(cid)).json();data['viewer_id']=v.uid+1;route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        page.route('**/customer-panel',fail)
        page.evaluate("document.getElementById('inbox').dispatchEvent(new CustomEvent('inbox-customer-invalidated',{detail:{id:"+str(cid)+"}}))")
        expect(page.locator('#customer-panel-notice')).to_contain_text('temporarily unavailable')
        if failure!='invalid_json':assert page.locator('#customer-panel-fields').inner_text()==''
        else:expect(page.locator('#customer-panel-notice')).to_contain_text('Last verified')
        assert not errors


def test_mobile_existing_details_action_opens_same_loyalty_panel(loyalty):
    from playwright.sync_api import expect
    cid=prepare(loyalty)
    with browser(loyalty,390) as (page,errors,calls):
        setup(page,loyalty,cid)
        page.locator('#mobile-chat-menu').click();page.locator('#details-action').click()
        expect(page.locator('#conversation-details-dialog')).to_be_visible()
        page.locator('#conversation-details-order').click()
        expect(page.locator('#details')).to_be_visible();expect(page.locator('#customer-panel-notice')).to_contain_text('Active loyalty member')
        assert not errors


def test_loading_old_reply_preserves_anchor_when_new_message_arrives(loyalty):
    from playwright.sync_api import expect
    v=loyalty;cid=prepare(v)
    with v.sessions() as db:
        phone=db.get(WhatsAppConversation,cid).phone_number;start=datetime.utcnow()-timedelta(hours=1)
        for i in range(130):
            row=WhatsAppConversationMessage(phone_number=phone,direction='inbound',message_text='Older reply anchor' if i==0 else 'History row '+str(i)+' '+('synthetic content '*10),whatsapp_message_id='stability-old-'+str(i),reply_to_whatsapp_message_id='stability-old-0' if i==129 else None,created_at=start+timedelta(seconds=i));db.add(row);db.flush()
            if i==0:old_id=row.id
        db.commit()
    with browser(v) as (page,errors,calls):
        setup(page,v,cid);assert page.locator('[data-message-id="'+str(old_id)+'"]').count()==0
        page.locator('button.message-quote').click();anchor=page.locator('[data-message-id="'+str(old_id)+'"]');expect(anchor).to_be_visible()
        page.evaluate("window.oldAnchor=document.querySelector('[data-message-id=\""+str(old_id)+"\"]')")
        before=page.locator('#messages').evaluate('(e)=>e.scrollTop');incoming(v,cid)
        poll(page,calls,cid)
        assert page.evaluate("oldAnchor===document.querySelector('[data-message-id=\""+str(old_id)+"\"]')")
        assert abs(page.locator('#messages').evaluate('(e)=>e.scrollTop')-before)<3 and not errors
