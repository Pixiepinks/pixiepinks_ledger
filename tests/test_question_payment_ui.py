import shutil
from urllib.parse import urlsplit
import pytest
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_question_delivery_details import collecting
from test_question_payment_followup import paying,instructions,slip
from test_whatsapp_outbound_media import png
from test_customer_questions import settings


def serve(client,route):
    request=route.request;url=urlsplit(request.url)
    if url.hostname!='question.test':route.fulfill(status=200,body='');return
    response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
        headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','range'}},follow_redirects=False)
    route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})


def test_payment_builder_upload_and_preview_persist_desktop_mobile(paying):
    c,config,_,_=paying;v=c[0][0]
    c[0][2]['steps'][0]['service'].pop('payment')
    assert settings(v.client,v.csrf,definition=c[0][2]).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:serve(v.client,route))
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        step=page.locator('#question-steps > fieldset').first;step.wait_for()
        checkbox=step.get_by_label('Send payment voice and bank image after order confirmation',exact=True)
        checkbox.check()
        step.get_by_role('button',name='Find payment recordings',exact=True).click()
        step.locator('.payment-picker select option').nth(2).wait_for(state='attached')
        step.get_by_label('Payment explanation recording',exact=True).select_option(f"{config['voice_id']}:{config['revision']}")
        step.get_by_label('Bank-details image',exact=True).set_input_files({'name':'bank-details.png','mimeType':'image/png','buffer':png()})
        step.get_by_role('button',name='Upload bank image',exact=True).click()
        step.get_by_role('status').filter(has_text='Bank image uploaded.').wait_for()
        step.get_by_label('Bank image caption',exact=True).fill('ගෙවීමෙන් පසු payment slip එක එවන්න.')
        page.get_by_role('button',name='Save settings',exact=True).click()
        page.locator('#question-feedback').get_by_text('Saved.',exact=False).wait_for()
        page.reload();step=page.locator('#question-steps > fieldset').first;step.wait_for()
        assert step.get_by_label('Send payment voice and bank image after order confirmation',exact=True).is_checked()
        assert step.get_by_label('Bank image caption',exact=True).input_value()=='ගෙවීමෙන් පසු payment slip එක එවන්න.'
        assert step.get_by_label('Payment explanation recording',exact=True).input_value()==f"{config['voice_id']}:{config['revision']}"
        assert step.locator('.payment-picker img').get_attribute('src').endswith(config['image_ref'])
        assert 'Payment voice → Bank image → Deposit slip → Staff verification' in page.locator('#question-flow-preview').inner_text()
        page.screenshot(path='/tmp/payment-builder-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        step.locator('.payment-picker').screenshot(path='/tmp/payment-builder-mobile.png')
        assert not errors
        browser.close()


def test_inbox_bank_image_voice_and_unverified_slip_handoff(paying,monkeypatch):
    import main
    instructions(paying);v=paying[0][0][0]
    monkeypatch.setattr(main,'download_whatsapp_attachment',lambda *a:(png(),'image/png'))
    assert slip(paying).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda route:serve(v.client,route))
        page.goto(f'https://question.test/crm/whatsapp?conversation={v.ids[0]}')
        page.locator('#messages audio').nth(1).wait_for()
        page.locator('#messages .attachment-bubble').nth(1).wait_for()
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('Slip received — awaiting staff verification',exact=False).wait_for()
        link=page.get_by_role('link',name='View submitted payment slip',exact=True)
        assert f'/conversations/{v.ids[0]}/messages/' in link.get_attribute('href')
        assert 'Staff must verify the transfer' in page.locator('.question-content').inner_text()
        page.screenshot(path='/tmp/payment-slip-inbox-desktop.png',full_page=True)
        page.get_by_role('button',name='Close',exact=True).click()
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='More message actions',exact=True).click()
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('Slip received — awaiting staff verification',exact=False).wait_for()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/payment-slip-inbox-mobile.png',full_page=True)
        assert not errors
        browser.close()
