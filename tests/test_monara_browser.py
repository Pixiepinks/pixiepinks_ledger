from pathlib import Path
import pytest
from test_monara import setup,rendered,inbox
from test_loyalty_browser import browser

@pytest.mark.parametrize('width,height',[(1648,928),(1366,768),(412,915)])
def test_monara_preview_library_explicit_send_and_admin(setup,monkeypatch,width,height):
    from playwright.sync_api import expect
    import main
    calls=[]
    monkeypatch.setattr(main,'upload_whatsapp_audio',lambda raw:'synthetic-upload')
    def deliver(recipient,media,**kwargs):
        assert kwargs['voice'] is False
        calls.append((recipient,media));return 'SENT','wamid.monara-ui'
    monkeypatch.setattr(main,'send_whatsapp_audio',deliver)
    v=setup;folder=Path('docs/images/monara');folder.mkdir(parents=True,exist_ok=True)
    with browser(v,width) as (page,errors,requests):
        page.set_viewport_size({'width':width,'height':height})
        page.goto('https://loyalty.test/crm/monara?conversation='+str(v.ids[0]))
        expect(page.locator('#monara-context')).to_contain_text('Conversation '+str(v.ids[0]))
        assert not page.locator('#monara-generate').is_disabled()
        assert page.locator('#monara-text').input_value()==''
        page.locator('#monara-text').fill('Synthetic Monara voice acceptance message.')
        page.locator('#monara-generate').click();expect(page.locator('#monara-status')).to_contain_text('Voice ready')
        assert calls==[]
        expect(page.locator('#monara-player')).to_be_visible();assert page.locator('#monara-send').is_disabled()
        original=page.locator('#monara-text').input_value()
        page.locator('#monara-approval').check();assert not page.locator('#monara-send').is_disabled()
        page.locator('#monara-text').fill(original+' Edited');assert page.locator('#monara-send').is_disabled()
        page.locator('#monara-language').select_option('en');assert page.locator('#monara-text').input_value()==original+' Edited'
        page.locator('#monara-language').select_option('si');page.locator('#monara-text').fill(original)
        page.screenshot(path=str(folder/f'{width}-preview.png'),full_page=True)
        page.locator('#monara-title').fill('Synthetic welcome');page.locator('#monara-share-approved').check();page.locator('#monara-save').click()
        expect(page.locator('#monara-library article')).to_have_count(1)
        page.screenshot(path=str(folder/f'{width}-library.png'),full_page=True)
        page.locator('#monara-approval').check();page.locator('#monara-send').click();expect(page.locator('#monara-status')).to_contain_text('SENT')
        assert len(calls)==1
        page.reload();expect(page.locator('#monara-status')).to_contain_text('SENT');assert len(calls)==1
        page.screenshot(path=str(folder/f'{width}-sent-and-usage.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        if width==1648:
            page.locator('#monara-policy button').click();expect(page.locator('#monara-status')).to_contain_text('Reviewed policy saved')
        assert not errors,errors

@pytest.mark.parametrize('width,height',[(1648,928),(412,915)])
def test_inbox_panel_preserves_unsent_composer(setup,width,height):
    from playwright.sync_api import expect
    v=setup
    with browser(v,width) as (page,errors,requests):
        page.set_viewport_size({'width':width,'height':height})
        page.goto('https://loyalty.test/crm/whatsapp?conversation='+str(v.ids[0]))
        expect(page.locator('#monara-open')).to_have_js_property('hidden',False)
        page.locator('#message-input').fill('Unsent synthetic staff draft — preserve me')
        if width<700:page.locator('#mobile-chat-plus').click()
        expect(page.locator('#monara-open')).to_be_visible()
        page.locator('#monara-open').click();expect(page.locator('#monara-iframe-dialog')).to_be_visible()
        frame=page.frame_locator('#monara-frame');expect(frame.locator('#monara-context')).to_contain_text('Conversation '+str(v.ids[0]))
        page.screenshot(path=f'docs/images/monara/{width}-inbox-panel.png',full_page=True)
        page.locator('#monara-close').click();expect(page.locator('#monara-iframe-dialog')).not_to_be_visible()
        assert page.locator('#message-input').input_value()=='Unsent synthetic staff draft — preserve me'
        assert not v.sends and not errors
