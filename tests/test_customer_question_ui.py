import shutil
from urllib.parse import urlsplit
import pytest
from test_customer_questions import inbox,questions


def test_builder_preview_and_mobile_inbox_controls(questions):
    client,sessions,ids,sends,_,csrf,calls=questions
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        page.locator('fieldset').first.wait_for();assert page.locator('fieldset').count()==2
        assert 'Girl' in page.locator('.question-preview').first.inner_text()
        assert not page.locator('fieldset').first.locator('[data-field=list_button]').is_visible()
        age=page.locator('fieldset').nth(1)
        age.locator('[data-field=type]').select_option('list')
        age.locator('[data-field=list_button]').fill('Choose age')
        age.locator('[data-field=options]').fill('\n'.join(f'{i} years' for i in range(1,11)))
        assert 'Choose age' in age.locator('.question-preview').inner_text()
        assert age.locator('.question-preview span').count()==10
        page.locator('#questions-enabled').check();page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();age=page.locator('fieldset').nth(1);age.wait_for()
        assert age.locator('[data-field=type]').input_value()=='list'
        assert age.locator('[data-field=list_button]').input_value()=='Choose age'
        assert age.locator('.question-preview span').count()==10
        age.locator('[data-field=type]').select_option('buttons')
        assert not age.locator('[data-field=options]').evaluate('el=>el.checkValidity()')
        age.locator('[data-field=type]').select_option('list')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/customer-question-list-builder.png',full_page=True)
        page.set_viewport_size({'width':1366,'height':1000})
        page.goto(f'https://question.test/crm/whatsapp?conversation={ids[0]}')
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.get_by_role('button',name='Start questions',exact=True).click()
        page.locator('#customer-question-dialog').get_by_text('Started.',exact=False).wait_for()
        page.get_by_role('button',name='Pause',exact=True).click()
        page.locator('.question-content').get_by_text('PAUSED',exact=False).wait_for()
        page.get_by_role('button',name='Close',exact=True).click()
        for width in [320,390,430]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.get_by_role('button',name='Chat options',exact=True).click()
            page.get_by_role('button',name='Customer questions',exact=True).click()
            page.get_by_role('button',name='Resume',exact=True).wait_for()
            if width==390:page.screenshot(path='/tmp/customer-question-mobile.png',full_page=True)
            page.get_by_role('button',name='Close',exact=True).click()
        assert not errors and not calls and not sends
        browser.close()


def test_answer_route_editor_preview_persistence_and_loop_error(questions):
    from test_customer_questions import settings,branching_flow
    client,_,_,_,_,csrf,_=questions
    assert settings(client,csrf,definition=branching_flow()).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path,content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        page.locator('fieldset').nth(4).wait_for()
        root=page.locator('fieldset').first
        assert root.locator('[data-field=route]').count()==5
        assert 'Scooter → scooter_type' in page.locator('#question-flow-preview').inner_text()
        root.locator('[data-field=route]').first.select_option('__finish')
        assert 'Bicycle → Finish' in page.locator('#question-flow-preview').inner_text()
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();page.locator('fieldset').nth(4).wait_for()
        assert page.locator('fieldset').first.locator('[data-field=route]').first.input_value()=='__finish'
        page.locator('fieldset').first.locator('[data-field=options]').fill('Cycles\nScooter\nTricycle\nJeeps\nGift Packs')
        assert page.locator('fieldset').first.locator('[data-field=route]').first.input_value()=='__finish'
        page.screenshot(path='/tmp/customer-question-branches.png',full_page=True)
        # Bicycle age is still reachable through Tricycle; routing back to product is a real cycle.
        page.locator('fieldset').nth(1).locator('[data-field=route]').first.select_option('product')
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='cannot contain loops').wait_for()
        page.locator('fieldset').nth(1).locator('[data-field=route]').first.select_option('__finish')
        page.locator('fieldset').nth(4).get_by_role('button',name='Remove question').click()
        assert page.locator('fieldset').first.locator('[data-field=route]').nth(4).input_value()=='gift_age'
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='missing question').wait_for()
        assert not errors
        browser.close()
