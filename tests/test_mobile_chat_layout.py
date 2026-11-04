import shutil
import pytest
from models import WhatsAppConversation,GlobalAIReplyControl
from test_conversation_assignment import assignments
from test_whatsapp_quick_messages import inbox
from test_single_row_filters import serve_page


def test_mobile_chat_history_space_controls_and_desktop_restore(assignments):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments;cid=v.ids[0]
    with v.sessions() as db:
        db.get(WhatsAppConversation,cid).customer_name='💕 🌹 A very long customer name with several words that must never overlap the controls';db.commit()
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});requests=[];errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            serve_page(page,v.admin,requests);page.goto(f'https://inbox.test/crm/whatsapp?conversation={cid}')
            api.expect(page.locator('#messages')).to_contain_text('OWN MESSAGE')
            for width in [320,360,390,430]:
                page.set_viewport_size({'width':width,'height':812})
                api.expect(page.locator('.topbar')).to_be_hidden()
                assert page.locator('#messages').bounding_box()['height']>812*0.65
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                name=page.locator('.chat-identity').bounding_box();badge=page.locator('#mode-badge').bounding_box();menu=page.locator('#mobile-chat-menu').bounding_box()
                assert name['x']+name['width']<=badge['x'] and badge['x']+badge['width']<=menu['x']
                composer=page.locator('#composer').bounding_box();assert composer['y']+composer['height']<=813
                assert page.locator('#text-composer').bounding_box()['height']<55
            page.locator('#mobile-chat-menu').click()
            api.expect(page.locator('#mobile-chat-management')).to_be_visible()
            api.expect(page.locator('#assignment-form')).to_be_visible()
            api.expect(page.locator('#inbox-lead-status')).to_be_visible()
            page.locator('#team-notes-open').click()
            api.expect(page.locator('#mobile-chat-management')).to_be_hidden()
            api.expect(page.locator('#collaboration-panel')).to_be_visible()
            page.keyboard.press('Escape')
            page.locator('#mobile-chat-plus').click()
            api.expect(page.locator('#mobile-chat-actions')).to_be_visible()
            api.expect(page.locator('#saved-voices-open')).to_be_visible()
            page.locator('#saved-voices-open').click()
            api.expect(page.locator('#mobile-chat-actions')).to_be_hidden()
            api.expect(page.locator('#saved-voice-dialog')).to_be_visible()
            page.keyboard.press('Escape')
            page.locator('#attachment-button').click()
            api.expect(page.locator('#attachment-menu')).to_be_visible()
            page.locator('#attachment-button').click()
            page.locator('#message-input').fill('A short mocked staff reply')
            page.locator('#send-button').click()
            api.expect(page.locator('#send-status')).to_contain_text('Message sent')
            assert len(v.sends)==1
            page.locator('#message-input').fill('Line one\nLine two\nLine three\nLine four\nLine five')
            assert page.locator('#message-input').bounding_box()['height']<=96
            page.set_viewport_size({'width':390,'height':430})
            assert page.locator('#messages').bounding_box()['height']>150
            composer=page.locator('#composer').bounding_box();assert composer['y']+composer['height']<=431
            page.set_viewport_size({'width':390,'height':812})
            page.locator('#back-list').click();api.expect(page.locator('.topbar')).to_be_visible()
            page.locator(f'[data-conversation-id="{cid}"]').click()
            page.set_viewport_size({'width':1366,'height':900})
            api.expect(page.locator('.topbar')).to_be_visible()
            api.expect(page.locator('#mobile-chat-menu')).to_be_hidden()
            api.expect(page.locator('.chat-head #mode-action')).to_be_visible()
            api.expect(page.locator('#chat>.conversation-owner-line')).to_be_visible()
            api.expect(page.locator('#chat>#inbox-lead-status')).to_be_visible()
            api.expect(page.locator('#voice-composer #saved-voices-open')).to_be_visible()
            assert page.locator('#attachment-button').count()==1 and page.locator('#assignment-form').count()==1
            assert not errors
            with v.sessions() as db:assert db.get(GlobalAIReplyControl,1).enabled is False
        finally:browser.close()


def test_shared_readonly_mobile_options_do_not_reveal_reply_controls(assignments):
    from models import ConversationShare
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments;cid=v.ids[0]
    with v.sessions() as db:
        db.add(ConversationShare(conversation_id=cid,user_id=v.users['quick-agent'],can_reply=False,granted_by=v.users['quick-admin']));db.commit()
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':360,'height':760});serve_page(page,v.other,[])
            page.goto(f'https://inbox.test/crm/whatsapp?conversation={cid}')
            api.expect(page.locator('#composer')).to_be_hidden()
            page.locator('#mobile-chat-menu').click()
            api.expect(page.locator('#mode-action')).to_be_hidden()
            api.expect(page.locator('#inbox-lead-status')).to_be_hidden()
            assert page.locator('#assignment-form').count()==0
            page.locator('#team-notes-open').click()
            api.expect(page.locator('#collaboration-panel')).to_be_visible()
            assert not v.sends
        finally:browser.close()
