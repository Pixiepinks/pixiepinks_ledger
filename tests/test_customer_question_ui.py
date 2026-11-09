import shutil
from urllib.parse import urlsplit
import pytest
from test_customer_questions import inbox,questions
from test_saved_voices import voices
from test_greasing_followup import greasing


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
        page.locator('#question-steps > fieldset').first.wait_for();assert page.locator('#question-steps > fieldset').count()==2
        assert 'Girl' in page.locator('.question-preview').first.inner_text()
        assert not page.locator('#question-steps > fieldset').first.locator('[data-field=list_button]').is_visible()
        age=page.locator('#question-steps > fieldset').nth(1)
        age.locator('[data-field=type]').select_option('list')
        age.locator('[data-field=list_button]').fill('Choose age')
        age.locator('[data-field=options]').fill('\n'.join(f'{i} years' for i in range(1,11)))
        assert 'Choose age' in age.locator('.question-preview').inner_text()
        assert age.locator('.question-preview span').count()==10
        page.locator('#questions-enabled').check();page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();age=page.locator('#question-steps > fieldset').nth(1);age.wait_for()
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
        icon=page.locator('#customer-question-button');assert icon.locator('svg').count()==1
        assert icon.get_attribute('title')=='Customer questions'
        assert page.locator('#voice-composer #customer-question-button').count()==1
        assert page.locator('.conversation-owner-line #customer-question-button').count()==0
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.get_by_label('Choose flow',exact=True).select_option('1')
        page.get_by_role('button',name='Start questions',exact=True).click()
        page.locator('#customer-question-dialog').get_by_text('Started.',exact=False).wait_for()
        page.get_by_role('button',name='Pause',exact=True).click()
        page.locator('.question-content').get_by_text('PAUSED',exact=False).wait_for()
        page.get_by_role('button',name='Close',exact=True).click()
        for width in [320,390,430]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.get_by_role('button',name='More message actions',exact=True).click()
            assert page.locator('#mobile-actions-content #customer-question-button').count()==1
            page.get_by_role('button',name='Customer questions',exact=True).click()
            assert not page.locator('#mobile-chat-actions').is_visible()
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
        response=client.request(request.method,url.path+('?'+url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        page.locator('#question-steps > fieldset').nth(4).wait_for()
        root=page.locator('#question-steps > fieldset').first
        assert root.locator('[data-field=route]').count()==5
        assert 'Scooter → scooter_type' in page.locator('#question-flow-preview').inner_text()
        root.locator('[data-field=route]').first.select_option('__finish')
        assert 'Bicycle → Finish' in page.locator('#question-flow-preview').inner_text()
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();page.locator('#question-steps > fieldset').nth(4).wait_for()
        assert page.locator('#question-steps > fieldset').first.locator('[data-field=route]').first.input_value()=='__finish'
        page.locator('#question-steps > fieldset').first.locator('[data-field=options]').fill('Cycles\nScooter\nTricycle\nJeeps\nGift Packs')
        assert page.locator('#question-steps > fieldset').first.locator('[data-field=route]').first.input_value()=='__finish'
        page.screenshot(path='/tmp/customer-question-branches.png',full_page=True)
        # Bicycle age is still reachable through Tricycle; routing back to product is a real cycle.
        page.locator('#question-steps > fieldset').nth(1).locator('[data-field=route]').first.select_option('product')
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='cannot contain loops').wait_for()
        page.locator('#question-steps > fieldset').nth(1).locator('[data-field=route]').first.select_option('__finish')
        page.locator('#question-steps > fieldset').nth(4).get_by_role('button',name='Remove question').click()
        assert page.locator('#question-steps > fieldset').first.locator('[data-field=route]').nth(4).input_value()=='gift_age'
        page.get_by_role('button',name='Save settings').click()
        page.get_by_role('status').filter(has_text='missing question').wait_for()
        assert not errors
        browser.close()


def test_flow_library_create_duplicate_archive_choose_and_start(questions):
    client,sessions,ids,sends,_,csrf,calls=questions
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        page.locator('#question-steps > fieldset').first.wait_for()
        page.get_by_label('New flow title',exact=True).fill('Bicycle enquiry');page.get_by_role('button',name='Create flow',exact=True).click()
        page.wait_for_function('document.querySelector("#question-flow-select").value==="2"')
        assert page.get_by_label('Sequence title',exact=True).input_value()=='Bicycle enquiry'
        page.get_by_label('New flow title',exact=True).fill('Gift packs');page.get_by_role('button',name='Duplicate flow',exact=True).click()
        page.wait_for_function('document.querySelector("#question-flow-select").value==="3"')
        page.get_by_role('button',name='Archive flow',exact=True).click()
        page.get_by_role('button',name='Restore flow',exact=True).wait_for()
        assert page.get_by_label('Sequence title',exact=True).is_disabled()
        page.get_by_role('button',name='Restore flow',exact=True).click()
        page.get_by_role('button',name='Archive flow',exact=True).wait_for()
        page.locator('#question-steps [data-field=body]').first.fill('Unsaved question edit')
        page.get_by_label('Enable Customer Question Automation',exact=True).check()
        page.get_by_role('button',name='Save automation switch',exact=True).click()
        page.get_by_role('status').filter(has_text='Flow library updated.').wait_for()
        assert page.locator('#question-steps [data-field=body]').first.input_value()=='Unsaved question edit'
        page.screenshot(path='/tmp/question-flow-library.png',full_page=True)
        page.goto(f'https://question.test/crm/whatsapp?conversation={ids[0]}')
        page.get_by_role('button',name='Customer questions',exact=True).click()
        start=page.get_by_role('button',name='Start questions',exact=True);start.wait_for();assert start.is_disabled()
        page.get_by_label('Choose flow',exact=True).select_option('3')
        assert 'Gift packs' in page.locator('.flow-preview').inner_text()
        page.screenshot(path='/tmp/question-flow-selection.png',full_page=True)
        start.click();page.get_by_role('status').filter(has_text='Started.').wait_for()
        with sessions() as db:
            import customer_questions as q
            run=q.latest(db,ids[0]);assert run.flow_id==3 and run.definition['title']=='Gift packs'
        assert not errors and not calls and not sends
        browser.close()


def test_tick_any_range_products_reorder_save_and_reload(questions,monkeypatch):
    import shopify_catalog_service as catalogue
    from test_shopify_collection_sends import product
    client,_,_,_,_,_,_=questions
    rows=[]
    for number,range_name in [(1,'Bicycles'),(2,'Gift packs'),(3,'School items')]:
        p=product(number)
        rows.append({'id':p['id'],'title':range_name+' example','status':'ACTIVE','published':True,'product_type':range_name,
            'variants':[{'id':p['variant_id'],'title':'Standard','price':str(p['price']),'available':True}]})
    monkeypatch.setattr(catalogue,'search_products_for_inbox',lambda *a,**kw:{'products':rows,'has_next_page':False,'next_cursor':None})
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions');page.locator('#question-steps > fieldset').nth(1).wait_for()
        page.get_by_role('button',name='Add question',exact=True).click();step=page.locator('#question-steps > fieldset').nth(2)
        step.locator('[data-field=key]').fill('recommendations');step.locator('[data-field=type]').select_option('products')
        step.get_by_role('button',name='Search catalogue',exact=True).click()
        step.get_by_role('checkbox',name='Bicycles example',exact=True).check()
        step.get_by_role('checkbox',name='Gift packs example',exact=True).check()
        assert 'Selected: 2/20' in step.inner_text()
        step.get_by_label('Move product down',exact=True).first.click()
        assert 'Gift packs example' in step.locator('.selected-product').first.inner_text()
        page.get_by_role('button',name='Save settings',exact=True).click();page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();step=page.locator('#question-steps > fieldset').nth(2);step.wait_for()
        assert step.locator('[data-field=type]').input_value()=='products'
        assert 'Selected: 2/20' in step.inner_text() and 'Gift packs example' in step.locator('.selected-product').first.inner_text()
        assert 'Send 2 selected products' in page.locator('#question-flow-preview').inner_text()
        page.screenshot(path='/tmp/question-selected-products.png',full_page=True)
        step.screenshot(path='/tmp/question-product-step.png')
        assert not errors
        browser.close()


def test_select_products_from_collections_preserves_previous_selections(questions,monkeypatch):
    import shopify_catalog_service as catalogue
    import shopify_collection_service as collections
    from test_shopify_collection_sends import product,listing
    from test_customer_questions import settings
    from test_question_products import flow
    client,_,_,_,_,csrf,_=questions
    assert settings(client,csrf,definition=flow(1)).status_code==200
    choices=[{'id':'gid://shopify/Collection/20','title':'Size 12 bicycles'}, {'id':'gid://shopify/Collection/21','title':'Gift packs'}]
    monkeypatch.setattr(catalogue,'discover_collections',lambda *a:choices)
    def load(cid):
        number=2 if cid.endswith('/20') else 3
        result=listing([product(number)]);result['collection']=next(c for c in choices if c['id']==cid);return result
    monkeypatch.setattr(collections,'load_collection',load)
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions');step=page.locator('#question-steps > fieldset').first;step.wait_for()
        step.get_by_label('Search Shopify collections',exact=True).fill('Size')
        step.get_by_role('button',name='Find collections',exact=True).click()
        page.locator('select[aria-label="Shopify collection"] option').nth(2).wait_for(state="attached")
        step.get_by_label('Shopify collection',exact=True).select_option(choices[0]['id'])
        step.get_by_role('checkbox',name='Bicycle 2',exact=True).check()
        step.get_by_label('Shopify collection',exact=True).select_option(choices[1]['id'])
        step.get_by_role('checkbox',name='Bicycle 3',exact=True).check()
        assert 'Selected: 3/20' in step.inner_text()
        step.get_by_label('Shopify collection',exact=True).select_option(choices[0]['id'])
        page.wait_for_function('document.querySelector(".picker-product input[type=checkbox]")?.checked===true')
        assert step.locator('.picker-product').count()==1
        page.get_by_role('button',name='Save settings',exact=True).click();page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();step=page.locator('#question-steps > fieldset').first;step.wait_for()
        assert 'Selected: 3/20' in step.inner_text() and not errors
        browser.close()


def test_product_choice_wording_numbered_cards_and_recorded_choice(questions,monkeypatch):
    import re
    import customer_questions as q
    import question_product_service as products
    from test_product_choice_list import choosing
    from test_customer_questions import settings,act,reply
    from test_shopify_collection_sends import product
    client,sessions,ids,_,_,csrf,_=questions
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:product(int(pid.rsplit('/',1)[-1])))
    sent=[]
    monkeypatch.setattr(products,'send_whatsapp_image',lambda *a,**kw:sent.append(a) or ('SENT',f'wamid.ui.card.{len(sent)}'))
    monkeypatch.setattr(q,'send_buttons',lambda *a,**kw:'wamid.ui.choice')
    assert settings(client,csrf,definition=choosing(2),enabled=True).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions')
        step=page.locator('#question-steps > fieldset').first;step.wait_for()
        choice=step.get_by_role('checkbox',name=re.compile('Ask customer to choose'))
        assert choice.is_checked() and 'Selected: 2/10' in step.inner_text()
        step.get_by_label('Product-choice message',exact=True).fill('ඔබ කැමති කුමන එකටද?')
        step.get_by_label('Product-choice button',exact=True).fill('තෝරන්න')
        page.get_by_role('button',name='Save settings',exact=True).click();page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();step=page.locator('#question-steps > fieldset').first;step.wait_for()
        assert step.get_by_label('Product-choice button',exact=True).input_value()=='තෝරන්න'
        step.get_by_role('checkbox',name=re.compile('Ask customer to choose')).uncheck()
        assert 'Selected: 2/20' in step.inner_text()
        assert act(client,ids[0],csrf).status_code==200
        for _ in range(3):assert q.dispatch_one(sessions)
        page.goto(f'https://question.test/crm/whatsapp?conversation={ids[0]}')
        page.locator('#messages .product-copy strong').nth(1).wait_for()
        assert page.locator('#messages .product-copy strong').all_text_contents()==['1. Bicycle 1','2. Bicycle 2']
        assert reply(questions,index=1,reply_type='list_reply').status_code==200
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('recommendations: 2. Bicycle 2 · Red',exact=True).wait_for()
        assert '[object Object]' not in page.locator('.question-content').inner_text() and not errors
        page.screenshot(path='/tmp/product-choice-recorded.png',full_page=True)
        browser.close()


def test_service_voice_picker_charge_and_wording_persist(voices):
    from test_product_choice_list import choosing
    from test_customer_questions import settings
    client=voices.client;recording=voices.create().json()['voice']
    assert settings(client,voices.csrf,definition=choosing(1)).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','range'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto('https://question.test/crm/quick-messages/customer-questions');step=page.locator('#question-steps > fieldset').first;step.wait_for()
        step.get_by_role('checkbox',name='After product choice: send Saved Voice, then ask about initial greasing service',exact=True).check()
        step.get_by_role('button',name='Find Saved Voices',exact=True).click()
        step.locator('.greasing-picker select option').nth(1).wait_for(state='attached')
        step.get_by_label('Service explanation recording',exact=True).select_option(f"{recording['id']}:{recording['revision']}")
        step.get_by_label('Service charge (LKR)',exact=True).fill('1000.00')
        step.get_by_label('Collect delivery details after service answer',exact=True).check()
        page.get_by_role('button',name='Save settings',exact=True).click();page.get_by_role('status').filter(has_text='Saved.').wait_for()
        page.reload();step=page.locator('#question-steps > fieldset').first;step.wait_for()
        assert step.get_by_label('Collect delivery details after service answer',exact=True).is_checked()
        assert 'Delivery details → Confirm / Edit' in page.locator('#question-flow-preview').inner_text()
        assert step.get_by_label('Service charge (LKR)',exact=True).input_value()=='1000.00'
        assert step.get_by_label('Service explanation recording',exact=True).locator('option:checked').inner_text()==recording['title']
        assert step.get_by_label('Greasing service question',exact=True).input_value().startswith('ඔබගේ')
        assert step.get_by_label('Preview service recording',exact=True).get_attribute('src').endswith(f"/{recording['id']}/audio")
        step.locator('.greasing-picker').screenshot(path='/tmp/greasing-service-picker.png')
        assert not errors
        browser.close()


def test_greasing_decision_and_quoted_total_visible_in_inbox(greasing):
    import customer_questions as q
    from test_greasing_followup import selected
    from test_customer_questions import reply
    v,_,_,value,_,_=selected(greasing)
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    assert reply(value,index=0).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=v.client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:val for k,val in request.headers.items() if k in {'content-type','x-csrf-token','range'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:val for k,val in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto(f'https://question.test/crm/whatsapp?conversation={v.ids[0]}')
        page.locator('#messages audio').first.wait_for()
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('Quoted total LKR 46000.00',exact=False).wait_for()
        assert 'Greasing: Yes' in page.locator('.question-content').inner_text() and not errors
        page.screenshot(path='/tmp/greasing-service-decision.png',full_page=True)
        browser.close()


from test_question_delivery_details import collecting

def test_confirmed_delivery_details_visible_to_staff_desktop_and_mobile(collecting):
    from test_question_delivery_details import begin,complete_fields
    from test_customer_questions import reply
    c=begin(collecting);v=c[0][0];complete_fields(c,phone2='skip')
    assert reply(c[0][3],index=0).status_code==200
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    def serve(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='question.test':route.fulfill(status=200,body='');return
        response=v.client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
            headers={k:val for k,val in request.headers.items() if k in {'content-type','x-csrf-token','range'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:val for k,val in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',serve)
        page.goto(f'https://question.test/crm/whatsapp?conversation={v.ids[0]}')
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('Delivery: CONFIRMED',exact=False).wait_for()
        content=page.locator('.question-content').inner_text()
        assert 'Phone 1: +94771234567' in content and 'Phone 2: Not provided' in content and 'City: Colombo' in content
        assert '123, Temple Road, Colombo 05' in content and 'Quoted total LKR 46800.00' in content
        assert 'Ready for staff to prepare the invoice' in content
        page.screenshot(path='/tmp/delivery-confirmed-desktop.png',full_page=True)
        page.get_by_role('button',name='Close',exact=True).click()
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='More message actions',exact=True).click()
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.locator('.question-content').get_by_text('Delivery: CONFIRMED',exact=False).wait_for()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/delivery-confirmed-mobile.png',full_page=True)
        assert not errors
        browser.close()
