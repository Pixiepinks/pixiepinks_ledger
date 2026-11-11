"""Durable collection sends: no real Shopify/Meta requests."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from models import User, WhatsAppConversation as C, WhatsAppManualSend as Send
import collection_send_queue as queue
from collection_send_queue import CollectionSendJob as Job
from test_shopify_collection_sends import catalog, preview, prepare, BASE, send
from test_whatsapp_quick_messages import inbox


def start(f, bid, cid=None):
    return f[0].post(f'{BASE}/conversations/{cid or f[2][0]}/collection-batches/{bid}/start', json={}, headers={'X-CSRF-Token': f[5]})


def pause(f, bid):
    return f[0].post(f'{BASE}/conversations/{f[2][0]}/collection-batches/{bid}/pause', json={}, headers={'X-CSRF-Token': f[5]})


def batch(f):
    return prepare(f[0], f[2][0], f[5], preview(f[0], f[2][0])).json()['batch_id']


def ready(sessions):
    with sessions() as db: db.query(Job).update({'next_at': datetime.utcnow() - timedelta(seconds=1)}); db.commit()


def test_server_continues_without_browser_and_fifo_second_collection(inbox, catalog):
    f = inbox; a = batch(f)
    assert not catalog[2] and start(f, a).status_code == 200
    b = batch(f); assert start(f, b).status_code == 200
    for n in range(6):
        ready(f[1]); assert queue.dispatch_one(f[1])
        with f[1]() as db:
            if n < 3: assert db.get(Job, b).status == 'QUEUED'
    assert len(catalog[2]) == 6
    with f[1]() as db: assert db.get(Job, a).status == db.get(Job, b).status == 'COMPLETE'
    assert not queue.dispatch_one(f[1])


def test_pause_resume_and_duplicate_start_do_not_resend(inbox, catalog):
    f = inbox; bid = batch(f); assert start(f, bid).status_code == 200
    assert start(f, bid).status_code == 200 and queue.dispatch_one(f[1])
    assert pause(f, bid).json()['background']['status'] == 'PAUSED'
    ready(f[1]); assert not queue.dispatch_one(f[1]) and len(catalog[2]) == 1
    assert start(f, bid).status_code == 200
    for _ in range(2): ready(f[1]); assert queue.dispatch_one(f[1])
    assert len(catalog[2]) == 3
    assert send(f[0], f[2][0], bid, f[5]).status_code == 409


@pytest.mark.parametrize('change', ['window', 'inactive', 'permission', 'owner'])
def test_worker_rechecks_window_and_current_staff_access(inbox, catalog, change):
    f = inbox
    # The initiating staff owns this conversation; admin ownership semantics differ.
    f[4]('quick-staff'); csrf = f[0].get('/crm/whatsapp/api/quick-messages').json()['csrf_token']
    values = (*f[:5], csrf); bid = batch(values); assert start(values, bid).status_code == 200
    with f[1]() as db:
        c = db.get(C, f[2][0]); u = db.query(User).filter_by(username='quick-staff').one()
        if change == 'window': c.last_customer_message_at = datetime.utcnow() - timedelta(hours=25)
        if change == 'inactive': u.is_active = False
        if change == 'permission': u.section_access = ['crm']
        if change == 'owner': c.assigned_to_user_id = None
        db.commit()
    assert queue.dispatch_one(f[1]) and not catalog[2]
    with f[1]() as db: assert db.get(Job, bid).status == ('PAUSED' if change == 'window' else 'CANCELED')


def test_crash_during_claim_blocks_uncertain_products_and_later_queue(inbox, catalog):
    f = inbox; a = batch(f); start(f, a); b = batch(f); start(f, b)
    with f[1]() as db:
        job = db.get(Job, a); job.status = 'RUNNING'; job.lease_until = datetime.utcnow() - timedelta(seconds=1)
        parent = db.get(Send, a); parent.status = 'PROCESSING'
        child = db.query(Send).filter(Send.idempotency_key.like('sci_%')).first(); child.status = 'PROCESSING'; db.commit()
    assert queue.dispatch_one(f[1]) and not queue.dispatch_one(f[1]) and not catalog[2]
    assert start(f, a).status_code == 409
    with f[1]() as db: assert db.get(Job, a).status == 'BLOCKED'


def test_concurrent_workers_claim_one_product(inbox, catalog):
    f = inbox; bid = batch(f); start(f, bid)
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: queue.dispatch_one(f[1]), range(2)))
    assert len(catalog[2]) == 1


def test_active_jobs_are_private_and_do_not_send_on_poll(inbox, catalog):
    f = inbox; bid = batch(f); start(f, bid)
    response = f[0].get(BASE + '/collection-batches/active'); assert len(response.json()['jobs']) == 1 and not catalog[2]
    f[4]('quick-agent'); assert not f[0].get(BASE + '/collection-batches/active').json()['jobs']
    assert f[0].post(f'{BASE}/conversations/{f[2][0]}/collection-batches/{bid}/pause', json={}, headers={'X-CSRF-Token': f[5]}).status_code in (403,404)


def test_paused_customer_does_not_block_another_customer(inbox, catalog):
    f = inbox; a = batch(f); start(f, a); pause(f, a)
    other = prepare(f[0], f[2][1], f[5], preview(f[0], f[2][1])).json()['batch_id']
    assert start(f, other, f[2][1]).status_code == 200
    assert queue.dispatch_one(f[1]) and len(catalog[2]) == 1
    with f[1]() as db:
        assert catalog[2][0][0] == db.get(C, f[2][1]).phone_number
        assert db.get(Job, a).status == 'PAUSED'


def test_pause_during_http_finishes_only_current_product(inbox, catalog, monkeypatch):
    import main
    f = inbox; bid = batch(f); start(f, bid)
    entered, release = Event(), Event(); original = main.send_whatsapp_image
    def image(*args, **kw):
        entered.set(); assert release.wait(10); return original(*args, **kw)
    monkeypatch.setattr(main, 'send_whatsapp_image', image)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sending = pool.submit(queue.dispatch_one, f[1]); assert entered.wait(5)
            result = pause(f, bid); assert result.status_code == 200 and result.json()['background']['status'] == 'PAUSING'
            release.set(); assert sending.result(timeout=10)
    finally: release.set()
    ready(f[1]); assert not queue.dispatch_one(f[1]) and len(catalog[2]) == 1
    with f[1]() as db: assert db.get(Job, bid).status == 'PAUSED'


def test_restart_after_confirmed_child_commit_recovers_parent_without_repeat(inbox, catalog):
    import json
    import shopify_collection_routes as routes
    f = inbox; bid = batch(f); start(f, bid)
    with f[1]() as db:
        parent = db.get(Send, bid); data = json.loads(parent.message_text)
        db.query(Send).filter_by(idempotency_key=routes.item_key(parent, data['items'][0]['id'])).one().status = 'SENT'
        parent.status = 'PROCESSING'
        job = db.get(Job, bid); job.status = 'RUNNING'; job.lease_until = datetime.utcnow() - timedelta(seconds=1); db.commit()
    for _ in range(2): ready(f[1]); assert queue.dispatch_one(f[1])
    assert len(catalog[2]) == 2
    with f[1]() as db: assert db.get(Job, bid).status == 'COMPLETE'


def test_background_selection_and_send_intent_commit_together(inbox, catalog):
    f = inbox; value = preview(f[0], f[2][0]); key = 'atomic-background-send'
    body = {'background': True, 'collection_id': value['collection']['id'], 'preview_token': value['preview_token'],
        'items': [{'product_id': p['id'], 'variant_id': p['variants'][0]['variant_id']} for p in value['products']]}
    path = f'{BASE}/conversations/{f[2][0]}/collection-batches'
    headers = {'X-CSRF-Token': f[5], 'Idempotency-Key': key}
    first = f[0].post(path, json=body, headers=headers)
    assert first.status_code == 200 and first.json()['background']['status'] == 'QUEUED'
    assert f[0].post(path, json=body, headers=headers).json()['batch_id'] == first.json()['batch_id']
    assert not catalog[2] and queue.dispatch_one(f[1]) and len(catalog[2]) == 1
    with f[1]() as db: assert db.query(Job).count() == 1
