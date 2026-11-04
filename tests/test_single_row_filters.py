from datetime import datetime,timedelta
from uuid import uuid4
import shutil
from urllib.parse import urlsplit,parse_qs
import pytest
import main
from models import Lead,User,WhatsAppConversation,WhatsAppConversationMessage,ConversationShare,GlobalAIReplyControl
from test_conversation_assignment import assignments
from test_whatsapp_quick_messages import inbox

URL='/crm/whatsapp/api/conversations'


def test_combined_filters_and_statuses_do_not_mutate_data(assignments,monkeypatch):
    v=assignments;uid=v.users['quick-staff'];cid=v.ids[0]
    with v.sessions() as db:
        c=db.get(WhatsAppConversation,cid);phone=c.phone_number
        db.add(Lead(customer_name='Lead',mobile=phone,status='NEGOTIATING',whatsapp_conversation_id=cid));db.commit()
    monkeypatch.setattr(main,'_payment_required_phones',lambda db:{phone})
    params={'owner_id':uid,'reply_queue':'needs_reply','payment_required':True,'unread':True,'mode':'HUMAN','lead_status':'NEGOTIATING'}
    data=v.admin.get(URL,params=params).json()
    assert [r['id'] for r in data['conversations']]==[cid]
    assert v.admin.get(URL,params={**params,'mode':'AI'}).json()['conversations']==[]
    for status in main.LEAD_STATUSES:
        response=v.admin.get(URL,params={'lead_status':status});assert response.status_code==200
        assert len(response.json()['conversations'])==(1 if status=='NEGOTIATING' else 0)
    assert {r['id'] for r in v.admin.get(URL,params={'lead_status':'UNORGANIZED'}).json()['conversations']}=={v.ids[1],v.third}
    for params in [{'owner_id':999999},{'owner_id':-1},{'lead_status':'invented'},{'mode':'anything'}]:assert v.admin.get(URL,params=params).status_code==422
    with v.sessions() as db:
        assert db.query(Lead).one().status=='NEGOTIATING' and db.get(WhatsAppConversation,cid).unread_count==2
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert not v.sends


def test_staff_all_is_authorized_union_and_owner_tampering_is_rejected(assignments):
    v=assignments
    with v.sessions() as db:
        db.add(ConversationShare(conversation_id=v.ids[1],user_id=v.users['quick-staff'],can_reply=False,granted_by=v.users['quick-admin']));db.commit()
    all_rows=v.staff.get(URL,params={'mailbox':'accessible'}).json()['conversations']
    assert {r['id'] for r in all_rows}==set(v.ids)
    assert v.staff.get(URL,params={'mailbox':'accessible','search':'Unassigned'}).json()['conversations']==[]
    assert v.staff.get(URL,params={'mailbox':'accessible','offset':50}).json()['conversations']==[]
    for params in [{'owner_id':v.users['quick-agent']},{'owner_id':v.users['quick-staff']},{'mailbox':'all'},{'mailbox':'unassigned'}]:assert v.staff.get(URL,params=params).status_code==403
    assert v.staff.get(f'{URL}/{v.third}').status_code==404
    with v.sessions() as db:db.query(ConversationShare).delete();db.commit()
    assert [r['id'] for r in v.staff.get(URL,params={'mailbox':'accessible'}).json()['conversations']]==[v.ids[0]]


def seed_filtered(v,count=120):
    now=datetime.utcnow()
    with v.sessions() as db:
        rows=[]
        for i in range(count):
            c=WhatsAppConversation(phone_number=f'filter-test-{i}',customer_name=f'Filter customer {i:03}',assigned_to_user_id=v.users['quick-staff'],mode='HUMAN',unread_count=1,last_message_at=now-timedelta(minutes=i))
            db.add(c);db.flush();rows.append(c.id)
            db.add(Lead(customer_name=c.customer_name,mobile=c.phone_number,status='NEGOTIATING',whatsapp_conversation_id=c.id))
            db.add(WhatsAppConversationMessage(phone_number=c.phone_number,direction='inbound',message_text='Question',created_at=now-timedelta(minutes=i)))
        db.commit()
    return rows


def test_filtered_pagination_before_limit_and_queue_order(assignments):
    v=assignments;ids=seed_filtered(v)
    params={'owner_id':v.users['quick-staff'],'lead_status':'NEGOTIATING','reply_queue':'needs_reply','unread':True,'mode':'HUMAN'}
    collected=[];offset=0
    while True:
        data=v.admin.get(URL,params={**params,'offset':offset}).json();collected += [r['id'] for r in data['conversations']]
        if not data['has_more']:break
        offset=data['next_offset']
    assert collected==ids
    assert v.admin.get(URL,params={**params,'search':'119'}).json()['conversations'][0]['id']==ids[-1]


def serve_page(page,client,requests):
    def serve(route):
        req=route.request;url=urlsplit(req.url)
        if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
        if url.path==URL:requests.append(parse_qs(url.query))
        response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    page.route('**/*',serve)
    page.add_init_script('window.setInterval=()=>0')


def test_filter_bar_popup_mobile_combinations_and_reset(assignments):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments;requests=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            serve_page(page,v.admin,requests);page.goto('https://inbox.test/crm/whatsapp?filter=unread')
            row=page.locator('#inbox-filter-row');api.expect(row.locator('[data-filter="unread"]')).to_have_attribute('aria-pressed','true')
            api.expect(page.locator('.conversation-row')).to_have_count(3)
            for width in [320,360,390,430,1366]:
                page.set_viewport_size({'width':width,'height':812})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert row.evaluate('el=>getComputedStyle(el).flexWrap')=='nowrap'
                assert row.bounding_box()['height']<=44
            row.evaluate('el=>el.scrollLeft=0')
            api.expect(page.locator('#filter-scroll-left')).to_be_hidden()
            api.expect(page.locator('#filter-scroll-right')).to_be_visible()
            page.locator('#filter-scroll-right').click()
            page.wait_for_function('document.getElementById("inbox-filter-row").scrollLeft>0')
            page.locator('#inbox-filters-open').click();dialog=page.locator('#inbox-filters-dialog')
            api.expect(dialog).to_be_visible()
            api.expect(page.locator('#inbox-filter-owner option')).to_have_count(5)
            assert page.locator('#inbox-filter-lead option').count()==len(main.LEAD_STATUSES)+2
            page.locator('#inbox-filter-owner').select_option(str(v.users['quick-staff']))
            page.locator('#inbox-filter-lead').select_option('NEGOTIATING')
            count=len(requests);page.keyboard.press('Escape');api.expect(dialog).to_be_hidden()
            api.expect(page.locator('#inbox-filters-open')).to_be_focused();assert len(requests)==count
            page.locator('#inbox-filters-open').click()
            api.expect(page.locator('#inbox-filter-lead')).to_have_value('')
            page.locator('#inbox-filter-owner').select_option(str(v.users['quick-staff']))
            page.locator('#inbox-filter-lead').select_option('NEGOTIATING')
            count=len(requests);page.locator('#inbox-filters-form [type="submit"]').click()
            api.expect(row.locator('[data-remove="ownerId"]')).to_contain_text('quick-staff')
            api.expect(row.locator('[data-remove="leadStatus"]')).to_contain_text('NEGOTIATING')
            assert len(requests)==count+1
            page.locator('[data-filter="payment"]').click();page.locator('[data-reply-queue="needs_reply"]').click()
            page.wait_for_function('new URL(location.href).searchParams.get("payment_required")==="true"')
            assert requests[-1]['payment_required']==['true'] and requests[-1]['reply_queue']==['needs_reply']
            row.locator('[data-remove="leadStatus"]').click();assert row.locator('[data-remove="ownerId"]').count()==1
            page.locator('#search').fill('Quick');page.wait_for_timeout(300)
            row.locator('[data-key="all"]').click()
            api.expect(page.locator('#search')).to_have_value('Quick')
            api.expect(page.locator('.conversation-row')).to_have_count(2)
            assert row.locator('[data-remove]').count()==0
            assert not errors and not v.sends
        finally:browser.close()


def test_filtered_scroll_pagination_reset_clear_and_stale_response(assignments):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments;seed_filtered(v,65);requests=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':812});serve_page(page,v.admin,requests)
            page.goto('https://inbox.test/crm/whatsapp')
            page.locator('#inbox-filters-open').click()
            page.locator('#inbox-filter-owner').select_option(str(v.users['quick-staff']))
            page.locator('#inbox-filter-lead').select_option('NEGOTIATING')
            page.locator('#inbox-filters-form [type="submit"]').click()
            api.expect(page.locator('.conversation-row')).to_have_count(50)
            page.locator('#conversation-list').evaluate('el=>el.scrollTop=el.scrollHeight')
            api.expect(page.locator('.conversation-row')).to_have_count(65)
            page.locator('[data-filter="unread"]').click()
            api.expect(page.locator('.conversation-row')).to_have_count(50)
            assert requests[-1]['offset']==['0']
            page.locator('#search').fill('064')
            api.expect(page.locator('.conversation-row')).to_have_count(1)
            page.locator('#inbox-filters-open').click();count=len(requests)
            page.locator('#inbox-filters-clear').click()
            assert len(requests)==count
            page.keyboard.press('Escape')
            assert page.locator('[data-remove="leadStatus"]').count()==1
            page.locator('#inbox-filters-open').click();page.locator('#inbox-filters-clear').click()
            page.locator('#inbox-filters-form [type="submit"]').click()
            api.expect(page.locator('#search')).to_have_value('064')
            assert page.locator('[data-remove]').count()==0
            page.locator('#search').fill('');api.expect(page.locator('.conversation-row')).to_have_count(50)
            # Deliberately release an older unread-only response after a newer AI response.
            held=[]
            def delay(route):
                query=parse_qs(urlsplit(route.request.url).query)
                if query.get('unread')==['true'] and 'mode' not in query:held.append(route)
                else:route.fallback()
            page.route('**/crm/whatsapp/api/conversations?**',delay)
            page.locator('[data-filter="unread"]').click()
            page.wait_for_timeout(100);assert held
            page.locator('[data-filter="ai"]').click()
            api.expect(page.locator('.conversation-row')).to_have_count(1)
            held[0].fulfill(status=200,json={'conversations':[{'id':999999,'customer_name':'STALE RESULT'}],'has_more':False})
            page.wait_for_timeout(100)
            assert 'STALE RESULT' not in page.locator('#conversation-list').inner_text()
            api.expect(page.locator('.conversation-row')).to_have_count(1)
            assert not v.sends
        finally:browser.close()


def test_staff_popup_hides_staff_choices_and_all_stays_scoped(assignments):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments
    with v.sessions() as db:
        db.add(ConversationShare(conversation_id=v.ids[1],user_id=v.users['quick-staff'],can_reply=False,granted_by=v.users['quick-admin']));db.commit()
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':360,'height':812},has_touch=True);requests=[];serve_page(page,v.staff,requests)
            page.goto('https://inbox.test/crm/whatsapp');api.expect(page.locator('.conversation-row')).to_have_count(2)
            assert page.locator('[data-mailbox="unassigned"]').count()==0
            page.locator('[data-mailbox="mine"]').tap();api.expect(page.locator('.conversation-row')).to_have_count(1)
            page.locator('[data-key="all"]').tap();api.expect(page.locator('.conversation-row')).to_have_count(2)
            page.locator('#inbox-filters-open').tap()
            assert page.locator('#inbox-filter-owner').count()==0
            page.keyboard.press('Tab')
            assert page.evaluate('document.activeElement.closest("#inbox-filters-dialog")!==null')
            assert 'Unassigned customer' not in page.locator('#conversation-list').inner_text()
        finally:browser.close()
