"""Reusable fan/product actions, all provider and catalogue IO mocked."""
import copy
import pytest
from fastapi import HTTPException
import customer_questions as q
import question_delivery_details as delivery
import question_greasing_service as voice_service
from models import CustomerQuestionProductSend as Item, WhatsAppConversationMessage as Message, GlobalAIReplyControl
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox
from test_greasing_followup import greasing
from test_customer_questions import settings, act, reply
from test_question_delivery_details import text
from test_question_products import selection


def definition(voice):
    return {'title':'Stand Fan Size 16','steps':[
        {'key':'fan','type':'products','products':[selection(1)],'continue_flow':True},
        {'key':'explain','type':'voice','voice_id':voice['id'],'revision':voice['revision'],'voice_title':voice['title']},
        {'key':'specs','type':'details','body':'Fan details','rows':[{'label':'Brand','value':'ELEVATE'},{'label':'Delivery','value':'FREE'},{'label':'Power','value':'45W'}]},
        {'key':'checkout','type':'checkout','product_key':'fan','address_prompt':'භාණ්ඩය එවිය යුතු සම්පූර්ණ ලිපිනය එවන්න.'}]}


def start(g,monkeypatch,payment=None):
    v,voice,_,value,audio,uploads=g
    flow=definition(voice)
    if payment:flow['steps'][-1]['payment']=payment
    texts=[]
    import whatsapp_service
    monkeypatch.setattr(whatsapp_service,'send_whatsapp_text',lambda phone,body,**kw:texts.append(body) or f'wamid.fan.details.{len(texts)}')
    monkeypatch.setattr(delivery,'send_whatsapp_text',lambda phone,body,**kw:texts.append(body) or f'wamid.fan.delivery.{len(texts)}')
    response=settings(v.client,v.csrf,definition=flow,enabled=True);assert response.status_code==200,response.text
    response=act(v.client,v.ids[0],v.csrf);assert response.status_code==200,response.text
    return flow,texts


def test_single_product_voice_details_checkout_no_greasing(greasing,monkeypatch):
    g=greasing;v=g[0];flow,texts=start(g,monkeypatch)
    for i in range(4):assert q.dispatch_one(v.sessions)
    assert len(g[4])==1 and texts[0]=='Fan details\n\nBrand: ELEVATE\nDelivery: FREE\nPower: 45W'
    assert texts[1]==delivery.PROMPTS['name'] and not g[3][-1]
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);assert run.status=='WAITING' and run.step==3
        assert run.answers['fan']['price']=='45000.00'
        assert 'greasing_service' not in run.answers['checkout']
    c=(g,texts)
    for answer in ['Full Customer Name','123 Main Street Colombo','Colombo','0771234567','skip']:
        assert text(c,answer).status_code==200
        assert q.dispatch_one(v.sessions)
    summary=g[3][-1][-1][1]
    assert '45,000.00' in summary and 'FREE' in summary and 'ග්‍රීස්' not in summary and 'බයිසිකල' not in summary
    assert flow['steps'][-1]['address_prompt'] in texts
    assert reply(g[3],index=0,mid='wamid.fan.confirm').status_code==200
    assert not q.dispatch_one(v.sessions)
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);assert run.status=='COMPLETE'
        assert run.answers['checkout']['delivery_details']['status']=='CONFIRMED'
        assert db.get(GlobalAIReplyControl,1).enabled is False
        assert db.query(Item).filter_by(position=-300,status='SENT').count()==2
        voice=db.query(Message).filter_by(response_kind='question_voice').one();mid=voice.id
    assert v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{mid}/media').status_code==200


def test_voice_uncertain_stops_details_and_checkout_without_replay(greasing,monkeypatch):
    v=greasing[0];_,texts=start(greasing,monkeypatch)
    monkeypatch.setattr(voice_service,'send_whatsapp_audio',lambda *a,**kw:('UNKNOWN',None))
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    assert not q.dispatch_one(v.sessions) and not texts
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'


def test_reusable_validation_rejects_invalid_details_source_and_routes(greasing):
    flow=definition(greasing[1]);assert q.validate(flow)==flow
    for mutate in [lambda f:f['steps'][2].update(rows=[]),lambda f:f['steps'][-1].update(product_key='missing'),lambda f:f['steps'][0].update(continue_flow=False),lambda f:f['steps'][2].update(rows=[{'label':'x','value':'y','html':'unsafe'}]),lambda f:f['steps'].reverse()]:
        bad=copy.deepcopy(flow);mutate(bad)
        with pytest.raises(HTTPException):q.validate(bad)


def test_saved_definition_is_pinned_for_running_flow(greasing,monkeypatch):
    v=greasing[0];flow,_=start(greasing,monkeypatch)
    changed=copy.deepcopy(flow);changed['steps'][2]['rows'][0]['value']='Other brand'
    assert settings(v.client,v.csrf,definition=changed,enabled=True).status_code==200
    with v.sessions() as db:assert q.latest(db,v.ids[0]).definition['steps'][2]['rows'][0]['value']=='ELEVATE'


def test_frontend_edits_voice_details_checkout_and_reorders(greasing,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=greasing[0];flow,_=start(greasing,monkeypatch)
    # The worker is not running: editing does not send messages.
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':844});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def serve(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='flow.test':route.fulfill(status=200,body='');return
            response=v.client.request(req.method,url.path+('?'+url.query if url.query else ''),content=req.post_data_buffer,headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',serve);page.goto('https://flow.test/crm/quick-messages/customer-questions')
        page.locator('#question-steps > fieldset').nth(3).wait_for()
        product=page.locator('#question-steps > fieldset').nth(0)
        assert product.locator('[data-field=continue_flow]').is_checked()
        voice=page.locator('#question-steps > fieldset').nth(1)
        voice.get_by_role('button',name='Find explanation recordings',exact=True).click()
        voice.locator('[data-action=voice] [role=status]').get_by_text('Preview and select a recording.').wait_for()
        voice.get_by_label('Explanation Saved Voice',exact=True).select_option(str(greasing[1]['id'])+':'+str(greasing[1]['revision']))
        details=page.locator('#question-steps > fieldset').nth(2)
        details.get_by_role('button',name='Add detail row',exact=True).click()
        details.get_by_label('Detail label',exact=True).last.fill('Warranty')
        details.get_by_label('Detail value',exact=True).last.fill('1 year')
        details.get_by_role('button',name='Move up',exact=True).click()
        assert page.locator('#question-steps > fieldset').nth(1).locator('[data-field=key]').input_value()=='specs'
        page.locator('#question-steps > fieldset').nth(1).get_by_role('button',name='Move down',exact=True).click()
        checkout=page.locator('#question-steps > fieldset').nth(3)
        assert checkout.get_by_label('Product step answer key',exact=True).input_value()=='fan'
        checkout.get_by_label('Delivery address question',exact=True).fill('Please send your complete delivery address.')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.get_by_role('button',name='Save settings',exact=True).click()
        page.locator('#question-feedback').get_by_text('Saved.',exact=False).wait_for()
        page.reload();page.locator('#question-steps > fieldset').nth(3).wait_for()
        assert page.locator('#question-steps > fieldset').nth(2).get_by_label('Detail value',exact=True).last.input_value()=='1 year'
        assert page.locator('#question-steps > fieldset').nth(3).get_by_label('Delivery address question',exact=True).input_value()=='Please send your complete delivery address.'
        assert not errors and not greasing[4]
        page.screenshot(path='/tmp/reusable-product-flow-mobile.png',full_page=True)
        page.set_viewport_size({'width':1366,'height':1000})
        page.goto(f'https://flow.test/crm/whatsapp?conversation={v.ids[1]}')
        page.get_by_role('button',name='Customer questions',exact=True).click()
        page.get_by_label('Choose flow',exact=True).select_option('1')
        page.locator('.flow-preview').get_by_text('Fan details',exact=False).wait_for()
        assert 'ELEVATE' in page.locator('.flow-preview').inner_text()
        assert 'Saved Voice' in page.locator('.flow-preview').inner_text()
        assert not errors
        browser.close()


def test_typed_question_waits_for_signed_answer_before_next_action(greasing,monkeypatch):
    v=greasing[0];flow,texts=start(greasing,monkeypatch)
    flow['steps'].insert(0,{'key':'preference','type':'text','body':'What colour do you prefer?'})
    # Replace retained run only in this test setup, before it starts dispatching.
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);run.definition=q.validate(flow);db.commit()
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions)
    assert texts==['What colour do you prefer?']
    assert text((greasing,texts),'Black').status_code==200
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);assert run.answers['preference']=='Black' and run.step==1 and run.status=='QUEUED'
    assert q.dispatch_one(v.sessions)

from test_question_delivery_details import collecting
from test_question_payment_followup import paying,slip


def test_generic_checkout_payment_slip_and_staff_order_have_zero_service_fee(paying,monkeypatch):
    import main
    import question_payment_review as review
    from test_shopify_collection_sends import product
    from models import SalesOrder, SalesPayment
    g=paying[0][0];v=g[0];flow,texts=start(g,monkeypatch,payment=paying[1])
    for _ in range(4):assert q.dispatch_one(v.sessions)
    for answer in ['Customer Name','123 Main Street Colombo','skip','0771234567','skip']:
        assert text((g,texts),answer).status_code==200;assert q.dispatch_one(v.sessions)
    assert reply(g[3],index=0,mid='wamid.fan.pay.confirm').status_code==200
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    assert len(paying[2])==1 and not q.dispatch_one(v.sessions)
    assert slip(paying).status_code==200
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);rid=run.id
        assert run.answers['checkout']['payment']['status']=='SLIP_REVIEW'
        assert db.query(SalesPayment).count()==0
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    monkeypatch.setattr(main.settings,'SALES_PAYMENT_TRACKING_ENABLED',True)
    monkeypatch.setattr(review,'resolve_inbox_product',lambda *a:product(1))
    url=f'/crm/whatsapp/api/conversations/{v.ids[0]}/customer-questions/{rid}/payment-review'
    data=v.client.get(url);assert data.status_code==200,data.text
    response=v.client.post(url,json={'action':'prepare_order','version':data.json()['version']},headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==200,response.text
    with v.sessions() as db:
        order=db.query(SalesOrder).one();assert float(order.service_total)==0 and float(order.grand_total)==45000
        assert db.query(SalesPayment).count()==0


def test_customer_choice_continues_with_selected_sent_card(greasing,monkeypatch):
    import question_product_service as products
    from test_shopify_collection_sends import product
    v=greasing[0];flow,_=start(greasing,monkeypatch)
    flow['steps'][0].update(products=[selection(1),selection(2)],choose=True)
    with v.sessions() as db:run=q.latest(db,v.ids[0]);run.definition=q.validate(flow);db.commit()
    calls=[]
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:product(int(pid.rsplit('/',1)[-1])))
    monkeypatch.setattr(products,'send_whatsapp_image',lambda *a,**kw:calls.append(1) or ('SENT',f'wamid.fan.option.{len(calls)}'))
    for _ in range(3):assert q.dispatch_one(v.sessions)
    assert reply(greasing[3],index=1,reply_type='list_reply',mid='wamid.fan.selected.2').status_code==200
    with v.sessions() as db:
        run=q.latest(db,v.ids[0]);assert run.step==1 and run.status=='QUEUED'
        assert run.answers['fan']['product_id'].endswith('/2') and run.answers['fan']['number']==2
    for _ in range(3):assert q.dispatch_one(v.sessions)
    with v.sessions() as db:assert q.latest(db,v.ids[0]).answers['checkout']['product_id'].endswith('/2')


def test_unavailable_single_product_stops_before_explanation(greasing,monkeypatch):
    import question_product_service as products
    v=greasing[0];_,texts=start(greasing,monkeypatch)
    monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:None)
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions)
    assert not greasing[4] and not texts
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'


def test_failed_previous_voice_blocks_later_details(greasing,monkeypatch):
    v=greasing[0];_,texts=start(greasing,monkeypatch)
    assert q.dispatch_one(v.sessions) and q.dispatch_one(v.sessions)
    with v.sessions() as db:
        db.query(Message).filter_by(response_kind='question_voice').one().send_status='failed';db.commit()
    assert q.dispatch_one(v.sessions) and not q.dispatch_one(v.sessions)
    assert not texts
    with v.sessions() as db:assert q.latest(db,v.ids[0]).status=='UNCERTAIN'
