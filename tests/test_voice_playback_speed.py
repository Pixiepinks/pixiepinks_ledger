import io
import wave
import shutil
from urllib.parse import urlsplit
import pytest
from models import WhatsAppConversation,WhatsAppConversationMessage
from test_whatsapp_quick_messages import inbox
from test_single_row_filters import serve_page


@pytest.fixture
def audio_messages(inbox):
    client,sessions,ids,sends,_,_=inbox
    with sessions() as db:
        for cid in ids:
            c=db.get(WhatsAppConversation,cid)
            db.add(WhatsAppConversationMessage(phone_number=c.phone_number,direction='inbound',message_text='Voice message',response_kind='customer_audio',media_id='mock-media',media_mime_type='audio/ogg',media_duration_seconds=10))
        db.commit()
    data=io.BytesIO()
    with wave.open(data,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*160000)
    return client,ids,sends,data.getvalue()


def setup(page,client,audio):
    serve_page(page,client,[])
    page.route('**/messages/*/media*',lambda route:route.fulfill(status=200,body=audio,headers={'content-type':'audio/wav'}))


def test_speed_cycle_preserves_playback_and_remembers_across_chats_reload(audio_messages):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,ids,sends,audio=audio_messages
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});errors=[];posts=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda req:posts.append(urlsplit(req.url).path) if req.method=='POST' else None)
            setup(page,client,audio);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            button=page.locator('#messages .voice-playback-speed');player=page.locator('#messages audio')
            api.expect(button).to_have_text('1×')
            player.evaluate('async audio=>{audio.muted=true;await audio.play();}')
            page.wait_for_timeout(150)
            before=player.evaluate('audio=>audio.currentTime')
            for rate in [1.5,2,3,1]:
                button.click();api.expect(button).to_have_text(f'{rate}×')
                assert player.evaluate('audio=>audio.playbackRate')==rate
                assert not player.evaluate('audio=>audio.paused')
                assert player.evaluate('audio=>audio.currentTime')>=before
                assert player.evaluate('audio=>audio.preservesPitch') is True
            for width in [320,360,390,430,1366]:
                page.set_viewport_size({'width':width,'height':812})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            button.click();button.click();api.expect(button).to_have_text('2×')
            page.reload();api.expect(button).to_have_text('2×')
            assert player.evaluate('audio=>audio.playbackRate')==2
            page.set_viewport_size({'width':390,'height':812})
            page.locator('#back-list').click();page.locator(f'[data-conversation-id="{ids[1]}"]').click()
            api.expect(button).to_have_text('2×')
            assert player.evaluate('audio=>audio.playbackRate')==2
            # Refreshing the same history must not add duplicate controls.
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(100);api.expect(button).to_have_count(1)
            assert not any('/send' in path for path in posts) and not sends and not errors
        finally:browser.close()


@pytest.mark.parametrize('storage',['blocked','invalid'])
def test_speed_works_with_unavailable_or_invalid_preferences(audio_messages,storage):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,ids,sends,audio=audio_messages
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();setup(page,client,audio)
            if storage=='blocked':page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Blocked','SecurityError')}})")
            else:page.add_init_script("localStorage.setItem('pixiepinks.inbox.voicePlaybackSpeed','99')")
            page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            button=page.locator('#messages .voice-playback-speed');player=page.locator('#messages audio')
            api.expect(button).to_have_text('1×');button.focus();page.keyboard.press('Enter')
            api.expect(button).to_have_text('1.5×')
            api.expect(button).to_have_attribute('aria-label','Playback speed 1.5×. Change to 2×')
            assert player.evaluate('audio=>audio.playbackRate')==1.5
            assert player.evaluate('audio=>audio.paused') is True
            assert not sends
        finally:browser.close()
