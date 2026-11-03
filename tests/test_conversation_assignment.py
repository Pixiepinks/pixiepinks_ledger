"""Private assigned-only staff inbox and administrator ownership workflows."""
from datetime import datetime
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import shutil
from urllib.parse import urlsplit
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
import conversation_access as access
from models import User,WhatsAppConversation,WhatsAppConversationMessage,ConversationAssignmentAudit,GlobalAIReplyControl
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def assignments(inbox):
    admin,sessions,ids,sends,_,csrf=inbox
    with sessions() as db:
        users={u.username:u.id for u in db.query(User)}
        db.get(WhatsAppConversation,ids[0]).assigned_to_user_id=users['quick-staff']
        db.get(WhatsAppConversation,ids[1]).assigned_to_user_id=users['quick-agent']
        third=WhatsAppConversation(phone_number='94770012303',customer_name='Unassigned customer',mode='HUMAN',unread_count=9,last_customer_message_at=datetime.utcnow())
        db.add(third);db.flush();third_id=third.id
        for cid,value in [(ids[0],'OWN MESSAGE'),(ids[1],'OTHER SECRET'),(third.id,'UNASSIGNED SECRET')]:
            convo=db.get(WhatsAppConversation,cid)
            db.add(WhatsAppConversationMessage(phone_number=convo.phone_number,direction='inbound',message_text=value))
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    def login(name):
        client=TestClient(main.app,base_url='https://testserver')
        client.post('/login',data={'username':name,'password':'password'},follow_redirects=False)
        return client
    staff=login('quick-staff');other=login('quick-agent')
    def assign(cid,owner,version=0,client=admin,token=csrf):
        return client.post(f'/crm/whatsapp/api/conversations/{cid}/assignment',json={'owner_id':owner,'expected_version':version},headers={'X-CSRF-Token':token})
    return SimpleNamespace(admin=admin,staff=staff,other=other,sessions=sessions,ids=ids,third=third_id,users=users,sends=sends,csrf=csrf,assign=assign)


def test_scoped_lists_filters_search_counts_and_admin_mailboxes(assignments):
    v=assignments
    payload=v.staff.get('/crm/whatsapp/api/conversations').json()
    assert [c['id'] for c in payload['conversations']]==[v.ids[0]] and payload['unread_total']==2
    assert payload['conversations'][0]['owner']['username']=='quick-staff'
    assert not v.staff.get('/crm/whatsapp/api/conversations?search=Unassigned').json()['conversations']
    assert v.staff.get('/crm/whatsapp/api/conversations?mailbox=all').status_code==403
    assert v.staff.get('/crm/whatsapp/api/conversations?mailbox=unassigned').status_code==403
    assert len(v.admin.get('/crm/whatsapp/api/conversations?mailbox=all').json()['conversations'])==3
    assert not v.admin.get('/crm/whatsapp/api/conversations?mailbox=mine').json()['conversations']
    assert [c['id'] for c in v.admin.get('/crm/whatsapp/api/conversations?mailbox=unassigned').json()['conversations']]==[v.third]
    listing=v.staff.get('/crm/leads/api/whatsapp').json()
    assert listing['total']==1 and listing['items'][0]['conversation_id']==v.ids[0]
    dashboard=v.staff.get('/crm').text
    assert 'Unassigned customer' not in dashboard


@pytest.mark.parametrize('suffix',['','/details','/messages/1/media','/messages/1/attachments/1/media','/saved-voice-sends/unknown','/media-sends/unknown','/assignment-history'])
def test_cannot_fetch_foreign_or_unassigned_conversation(assignments,suffix):
    v=assignments
    for cid in [v.ids[1],v.third]:assert v.staff.get(f'/crm/whatsapp/api/conversations/{cid}'+suffix).status_code==404
    assert v.staff.get('/crm/whatsapp?conversation='+str(v.third)).status_code==404
    assert v.staff.get(f'/crm/leads/api/whatsapp/{v.third}').status_code==404
    assert v.staff.get(f'/crm/leads/api/whatsapp/{v.third}/status').status_code==404


@pytest.mark.parametrize('suffix',['/read','/takeover','/return-to-ai','/send','/send-audio','/send-product','/send-media','/send-saved-voice'])
def test_cannot_mutate_or_send_to_others(assignments,suffix):
    v=assignments
    result=v.staff.post(f'/crm/whatsapp/api/conversations/{v.third}'+suffix,json={'message':'Forbidden'},headers={'Idempotency-Key':'forbidden-owner-attempt','X-CSRF-Token':v.csrf})
    assert result.status_code==404 and not v.sends


def test_admin_assign_reassign_clear_and_audit_without_mode_changes(assignments):
    v=assignments
    assert v.assign(v.third,v.users['quick-staff'],token='bad').status_code==403
    assert v.staff.get('/crm/whatsapp/api/assignees').status_code==403
    assert v.assign(v.ids[0],v.users['quick-agent'],client=v.staff).status_code==403
    assert v.assign(v.third,v.users['quick-staff']).status_code==200
    assert v.staff.get(f'/crm/whatsapp/api/conversations/{v.third}').status_code==200
    assert v.assign(v.third,v.users['quick-agent'],version=0).status_code==409
    assert v.assign(v.third,v.users['quick-agent'],version=1).status_code==200
    assert v.staff.get(f'/crm/whatsapp/api/conversations/{v.third}').status_code==404
    assert v.other.get(f'/crm/whatsapp/api/conversations/{v.third}').status_code==200
    assert v.assign(v.third,None,version=2).status_code==200
    assert v.other.get(f'/crm/whatsapp/api/conversations/{v.third}').status_code==404
    with v.sessions() as db:
        row=db.get(WhatsAppConversation,v.third);assert row.mode=='HUMAN' and row.assignment_version==3
        assert db.query(ConversationAssignmentAudit).count()==3 and db.get(GlobalAIReplyControl,1).enabled is False
    assert len(v.admin.get(f'/crm/whatsapp/api/conversations/{v.third}/assignment-history').json()['history'])==3


@pytest.mark.parametrize('state',['inactive','no-access','unknown'])
def test_invalid_owners_rejected(assignments,state):
    v=assignments;uid=v.users['quick-agent']
    with v.sessions() as db:
        if state=='inactive':db.get(User,uid).is_active=False
        if state=='no-access':db.get(User,uid).section_access=[]
        db.commit()
    assert v.assign(v.third,uid if state!='unknown' else 999999).status_code==422
    with v.sessions() as db:assert db.get(WhatsAppConversation,v.third).assigned_to_user_id is None


def test_concurrent_assignment_versions_fence_stale_saves(assignments):
    v=assignments
    def assign(uid):
        with v.sessions() as db:
            try:access.assign(db,db.get(User,v.users['quick-admin']),v.third,uid,0);return 200
            except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(assign,[v.users['quick-staff'],v.users['quick-agent']]))
    assert sorted(results)==[200,409]
    with v.sessions() as db:assert db.query(ConversationAssignmentAudit).count()==1


def test_assignment_audit_failure_rolls_back_owner(assignments,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    v=assignments
    with monkeypatch.context() as patch:
        patch.setattr(v.sessions.class_,'commit',lambda db:(_ for _ in ()).throw(SQLAlchemyError('private fixture error')))
        result=v.assign(v.third,v.users['quick-staff'])
        assert result.status_code==503 and 'private fixture error' not in result.text
    with v.sessions() as db:
        row=db.get(WhatsAppConversation,v.third)
        assert row.assigned_to_user_id is None and row.assignment_version==0 and db.query(ConversationAssignmentAudit).count()==0


def test_owned_manual_send_works_while_ai_off(assignments):
    v=assignments
    result=v.staff.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send',json={'message':'Owner reply'},headers={'Idempotency-Key':'owned-manual-test-001'})
    assert result.status_code==200 and len(v.sends)==1


def test_incoming_webhooks_keep_owner_and_new_customers_are_unassigned(assignments,monkeypatch):
    from test_whatsapp_reply_context import deliver
    v=assignments
    monkeypatch.setattr(main,'_reply_to_text_message',lambda *a:pytest.fail('Global AI OFF must not enqueue a reply'))
    assert deliver(v.admin,monkeypatch,phone='94770012301',reference=None,mid='wamid.owned.incoming').status_code==200
    assert deliver(v.admin,monkeypatch,phone='94770012304',reference=None,mid='wamid.unassigned.incoming').status_code==200
    with v.sessions() as db:
        assert db.get(WhatsAppConversation,v.ids[0]).assigned_to_user_id==v.users['quick-staff']
        fresh=db.query(WhatsAppConversation).filter_by(phone_number='94770012304').one()
        assert fresh.assigned_to_user_id is None and fresh.assignment_version==0
        fresh_id=fresh.id
    assert fresh_id not in [row['id'] for row in v.staff.get('/crm/whatsapp/api/conversations').json()['conversations']]
    assert fresh_id in [row['id'] for row in v.admin.get('/crm/whatsapp/api/conversations?mailbox=unassigned').json()['conversations']]


def test_reassignment_during_product_lookup_blocks_external_send(assignments,monkeypatch):
    from decimal import Decimal
    v=assignments;calls=[]
    def resolve(*args):
        with v.sessions() as db:
            db.get(WhatsAppConversation,v.ids[0]).assigned_to_user_id=v.users['quick-agent'];db.commit()
        return {'id':'p','variant_id':'v','handle':'test','title':'Owner test product','variant_title':'Default Title',
                'price':Decimal('100'),'currency':'LKR','image':'https://cdn.shopify.com/test.png','url':'https://www.pixiepinks.shop/products/test'}
    monkeypatch.setattr(main,'resolve_inbox_product',resolve)
    monkeypatch.setattr(main,'send_whatsapp_image',lambda *a,**kw:calls.append(a))
    result=v.staff.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-product',json={'product_id':'p','variant_id':'v'},headers={'Idempotency-Key':'reassigned-product-test'})
    assert result.status_code in {404,502} and not calls


def test_admin_ui_assigns_and_staff_loses_open_chat_after_reassignment(assignments):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                client=v.admin if url.hostname=='admin.inbox.test' else v.staff
                if url.hostname not in {'admin.inbox.test','staff.inbox.test'}:route.fulfill(status=200,body='');return
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            admin=browser.new_page();admin.route('**/*',serve)
            admin.goto(f'https://admin.inbox.test/crm/whatsapp?conversation={v.third}')
            admin.locator('#assignment-owner').select_option(str(v.users['quick-staff']))
            admin.locator('#assignment-save').click()
            api.expect(admin.locator('#assignment-feedback')).to_have_text('Owner saved.')
            staff=browser.new_page();staff.route('**/*',serve);errors=[];staff.on('pageerror',lambda e:errors.append(str(e)))
            staff.goto(f'https://staff.inbox.test/crm/whatsapp?conversation={v.third}')
            api.expect(staff.locator('#messages')).to_contain_text('UNASSIGNED SECRET')
            assert staff.locator('[data-mailbox="all"]').count()==0 and staff.locator('#assignment-form').count()==0
            assert 'OTHER SECRET' not in staff.locator('#conversation-list').inner_text()
            assert v.assign(v.third,v.users['quick-agent'],version=1).status_code==200
            api.expect(staff.locator('#chat')).to_be_hidden(timeout=10000)
            assert staff.locator('#messages').inner_text()=='' and not errors
        finally:browser.close()
