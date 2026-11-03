from uuid import uuid4
import pytest
from models import ConversationNote,ConversationMention,ConversationShare,ConversationShareAudit,WhatsAppConversation,WhatsAppConversationMessage,User,GlobalAIReplyControl
from test_conversation_assignment import assignments
from test_whatsapp_quick_messages import inbox

BASE='/crm/whatsapp/api'


def headers(v):return {'X-CSRF-Token':v.csrf}


def grant(v,cid=None,reply=False,revoke=False,version=None):
    cid=cid or v.ids[0]
    if version is None:version=v.admin.get(f'{BASE}/conversations/{cid}/collaboration').json()['version']
    return v.admin.post(f'{BASE}/conversations/{cid}/sharing',json={'user_id':v.users['quick-agent'],'can_reply':reply,'revoke':revoke,'expected_version':version},headers=headers(v))


def token(client):
    import re
    return re.search(r'data-assignment-csrf="([^"]+)"',client.get('/crm/whatsapp').text).group(1)


def note(v,client=None,mentions=None,key=None,body='Private payment check'):
    client=client or v.staff
    return client.post(f'{BASE}/conversations/{v.ids[0]}/notes',json={'body':body,'mentions':mentions or [],'client_key':key or str(uuid4())},headers={'X-CSRF-Token':token(client)})


def test_read_only_sharing_is_explicit_and_revocable(assignments):
    v=assignments;cid=v.ids[0];url=f'{BASE}/conversations/{cid}'
    assert v.other.get(url).status_code==404
    assert grant(v).status_code==200
    assert v.other.get(url).status_code==200
    assert v.other.get(url).json()['conversation']['can_reply'] is False
    assert v.other.get(f'{BASE}/conversations?mailbox=mine').json()['conversations'][0]['id']==v.ids[1]
    assert [c['id'] for c in v.other.get(f'{BASE}/conversations?mailbox=shared').json()['conversations']]==[cid]
    assert v.other.get(f'{BASE}/conversations?mailbox=all').status_code==403
    for suffix in ['send','send-audio','send-media','send-product','send-saved-voice','collection-batches','takeover','return-to-ai','assignment']:
        assert v.other.post(url+'/'+suffix,json={}).status_code==403,suffix
    with v.sessions() as db:unread=db.get(WhatsAppConversation,cid).unread_count
    assert v.other.post(url+'/read').status_code==200
    with v.sessions() as db:assert db.get(WhatsAppConversation,cid).unread_count==unread
    assert note(v,v.other).status_code==200
    assert grant(v,revoke=True).status_code==200
    assert v.other.get(url).status_code==404 and v.other.get(url+'/notes').status_code==404
    assert v.other.get(f'{BASE}/conversations?mailbox=shared').json()['conversations']==[]
    with v.sessions() as db:
        assert db.get(WhatsAppConversation,cid).assigned_to_user_id==v.users['quick-staff']
        assert db.query(ConversationShareAudit).count()==2
    assert not v.sends


def test_reply_grant_can_send_but_not_change_owner_or_lead_mode(assignments):
    v=assignments;url=f'{BASE}/conversations/{v.ids[0]}'
    assert grant(v,reply=True).status_code==200
    assert v.other.get(url).json()['conversation']['can_reply'] is True
    result=v.other.post(url+'/send',json={'message':'Approved staff reply'},headers={'Idempotency-Key':'shared-reply-test'})
    assert result.status_code==200,result.text
    assert len(v.sends)==1
    for suffix in ['takeover','return-to-ai','assignment','sharing']:
        assert v.other.post(url+'/'+suffix,json={}).status_code==403
    assert v.other.post(f'/crm/leads/api/whatsapp/{v.ids[0]}/lead',json={}).status_code==403
    assert grant(v,reply=False).status_code==200
    assert v.other.post(url+'/send',json={'message':'No longer allowed'},headers={'Idempotency-Key':'shared-reply-denied'}).status_code==403
    assert len(v.sends)==1


def test_notes_mentions_private_idempotent_and_do_not_answer_customer(assignments):
    v=assignments;cid=v.ids[0];url=f'{BASE}/conversations/{cid}'
    before=v.admin.get(f'{BASE}/conversations?reply_queue=needs_reply').json()
    assert note(v,mentions=[v.users['quick-agent']]).status_code==422
    assert grant(v).status_code==200
    key=str(uuid4());text='<script>bad()</script> @agent Please check payment privately'
    result=note(v,mentions=[v.users['quick-agent']],key=key,body=text)
    assert result.status_code==200 and result.json()['body']==text
    assert note(v,mentions=[v.users['quick-agent']],key=key,body=text).json()['id']==result.json()['id']
    assert note(v,key=key,body='Changed').status_code==409
    mentions=v.other.get(BASE+'/mentions').json()
    assert mentions['unread']==1 and mentions['mentions'][0]['preview']==text
    mid=mentions['mentions'][0]['id']
    assert v.staff.post(f'{BASE}/mentions/{mid}/read',headers={'X-CSRF-Token':token(v.staff)}).status_code==404
    after=v.admin.get(f'{BASE}/conversations?reply_queue=needs_reply').json()
    assert [(r['id'],r['waiting_since']) for r in before['conversations']]==[(r['id'],r['waiting_since']) for r in after['conversations']]
    with v.sessions() as db:
        assert db.query(ConversationNote).count()==1 and db.query(ConversationMention).count()==1
        assert db.query(WhatsAppConversationMessage).filter_by(direction='outbound').count()==0
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert v.other.post(f'{BASE}/mentions/{mid}/read',headers={'X-CSRF-Token':token(v.other)}).status_code==200
    assert v.other.get(BASE+'/mentions').json()['unread']==0
    assert not v.sends


def test_mention_notifications_hidden_after_revoke(assignments):
    v=assignments;grant(v)
    note(v,mentions=[v.users['quick-agent']])
    mid=v.other.get(BASE+'/mentions').json()['mentions'][0]['id']
    grant(v,revoke=True)
    assert v.other.get(BASE+'/mentions').json()=={'unread':0,'mentions':[]}
    assert v.other.post(f'{BASE}/mentions/{mid}/read',headers={'X-CSRF-Token':token(v.other)}).status_code==404


def test_sharing_csrf_versions_eligibility_and_audit_rollback(assignments,monkeypatch):
    v=assignments;url=f'{BASE}/conversations/{v.ids[0]}/sharing'
    body={'user_id':v.users['quick-agent'],'can_reply':False,'revoke':False,'expected_version':0}
    assert v.admin.post(url,json=body).status_code==403
    assert v.staff.post(url,json=body,headers={'X-CSRF-Token':token(v.staff)}).status_code==403
    assert grant(v).status_code==200
    assert grant(v,version=0).status_code==409
    with v.sessions() as db:db.get(User,v.users['quick-agent']).is_active=False;db.commit()
    assert grant(v).status_code==422
    with v.sessions() as db:db.get(User,v.users['quick-agent']).is_active=True;db.commit()
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    def fail(*args):raise SQLAlchemyError('simulated audit failure')
    event.listen(ConversationShareAudit,'before_insert',fail)
    try:assert grant(v,revoke=True).status_code==503
    finally:event.remove(ConversationShareAudit,'before_insert',fail)
    with v.sessions() as db:assert db.query(ConversationShare).count()==1 and db.query(ConversationShareAudit).count()==1


def test_note_csrf_limits_and_pagination(assignments):
    v=assignments;url=f'{BASE}/conversations/{v.ids[0]}/notes'
    assert v.staff.post(url,json={}).status_code==403
    assert note(v,body=' ').status_code==422
    assert note(v,body='x'*4001).status_code==422
    assert note(v,mentions=[True]).status_code==422
    with v.sessions() as db:
        db.add_all([ConversationNote(conversation_id=v.ids[0],author_id=v.users['quick-staff'],client_key=str(uuid4()),body=str(n)) for n in range(52)]);db.commit()
    first=v.staff.get(url).json();assert len(first['notes'])==50 and first['next_before']
    second=v.staff.get(url,params={'before_id':first['next_before']}).json()
    assert len(second['notes'])==2 and second['next_before'] is None
    assert not ({n['id'] for n in first['notes']} & {n['id'] for n in second['notes']})


def test_collaboration_migration_preserves_owner_and_reverts_only_new_tables(tmp_path,monkeypatch):
    from sqlalchemy import create_engine,inspect
    from sqlalchemy.orm import sessionmaker
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from models import Base
    import database
    engine=create_engine('sqlite:///'+str(tmp_path/'migration.db'))
    Base.metadata.create_all(engine)
    for model in [ConversationMention,ConversationNote,ConversationShareAudit,ConversationShare]:model.__table__.drop(engine)
    sessions=sessionmaker(bind=engine)
    with sessions() as db:
        user=User(username='preserved',password_hash='not-a-real-password',role='ADMIN');db.add(user);db.flush()
        db.add(WhatsAppConversation(phone_number='test-phone',assigned_to_user_id=user.id,assignment_version=7,mode='HUMAN',unread_count=3));db.commit()
    monkeypatch.setattr(database,'engine',engine)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261103_23');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        c=db.query(WhatsAppConversation).one();assert c.assignment_version==7 and c.unread_count==3 and c.assigned_to_user_id
        assert db.query(ConversationNote).count()==0 and db.query(ConversationShare).count()==0
    command.downgrade(cfg,'20261103_23')
    assert 'conversation_notes' not in inspect(engine).get_table_names()
    with sessions() as db:assert db.query(WhatsAppConversation).one().assignment_version==7
    engine.dispose()


def test_sharing_browser_notes_and_revoke(assignments):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=assignments;cid=v.ids[0]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname not in {'admin.inbox.test','shared.inbox.test'}:route.fulfill(status=200,body='');return
                client=v.admin if url.hostname=='admin.inbox.test' else v.other
                response=client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            admin=browser.new_page();admin.route('**/*',serve)
            admin.goto(f'https://admin.inbox.test/crm/whatsapp?conversation={cid}')
            admin.locator('#team-notes-open').click()
            api.expect(admin.locator('#collaboration-panel')).to_be_visible()
            api.expect(admin.locator('#share-user option')).not_to_have_count(0)
            admin.locator('#share-user').select_option(str(v.users['quick-agent']))
            admin.locator('#share-form button').click()
            api.expect(admin.locator('#share-list')).to_contain_text('quick-agent: read & notes')
            page=browser.new_page();page.route('**/*',serve);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'https://shared.inbox.test/crm/whatsapp?conversation={cid}')
            api.expect(page.locator('#shared-permission')).to_contain_text('read and private notes only')
            api.expect(page.locator('#composer')).to_be_hidden()
            page.locator('#team-notes-open').click()
            api.expect(page.locator('#collaboration-panel')).to_be_visible()
            page.keyboard.press('Escape')
            api.expect(page.locator('#collaboration-panel')).not_to_be_visible()
            api.expect(page.locator('#team-notes-open')).to_be_focused()
            page.set_viewport_size({'width':390,'height':760})
            page.locator('#team-notes-open').click()
            api.expect(page.locator('#collaboration-panel')).to_be_visible()
            assert page.locator('#collaboration-panel').bounding_box()['width']<=390
            page.locator('#private-note-body').fill('<img src=x onerror=alert(1)> Payment verified')
            page.locator('#private-note-mentions').select_option(str(v.users['quick-staff']))
            page.locator('#private-note-form button').click()
            api.expect(page.locator('#private-notes')).to_contain_text('Payment verified')
            assert page.locator('#private-notes img').count()==0
            assert not v.sends
            admin.locator('#share-list button').click()
            api.expect(page.locator('#chat')).to_be_hidden(timeout=10000)
            api.expect(page.locator('#collaboration-panel')).not_to_be_visible()
            assert page.locator('#private-notes').inner_text()=='' and not errors
        finally:browser.close()


def test_concurrent_grants_and_duplicate_note_retries(assignments):
    from concurrent.futures import ThreadPoolExecutor
    v=assignments
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:grant(v,version=0).status_code,range(2)))
    assert sorted(results)==[200,409]
    key=str(uuid4());csrf=token(v.staff);url=f'{BASE}/conversations/{v.ids[0]}/notes'
    body={'body':'One private note','mentions':[v.users['quick-agent']],'client_key':key}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:v.staff.post(url,json=body,headers={'X-CSRF-Token':csrf}),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()['id']==results[1].json()['id']
    with v.sessions() as db:
        assert db.query(ConversationNote).count()==1 and db.query(ConversationMention).count()==1
        assert db.query(ConversationShareAudit).count()==1


def test_shared_reply_revoked_during_product_lookup_sends_nothing(assignments,monkeypatch):
    from decimal import Decimal
    import main
    v=assignments;assert grant(v,reply=True).status_code==200
    calls=[]
    def resolve(*args):
        assert grant(v,revoke=True).status_code==200
        return {'id':'p','variant_id':'v','handle':'test','title':'Test product','variant_title':'Default Title',
                'price':Decimal('100'),'currency':'LKR','image':'https://cdn.shopify.com/test.png','url':'https://www.pixiepinks.shop/products/test'}
    monkeypatch.setattr(main,'resolve_inbox_product',resolve)
    monkeypatch.setattr(main,'send_whatsapp_image',lambda *a,**kw:calls.append(a))
    result=v.other.post(f'{BASE}/conversations/{v.ids[0]}/send-product',json={'product_id':'p','variant_id':'v'},headers={'Idempotency-Key':'shared-revoked-product'})
    assert result.status_code in {404,502} and not calls
