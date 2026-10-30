"""Operational global switch: real database admission/locks, mocked providers."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime
from threading import Barrier, Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import main
import global_ai_control as control
import whatsapp_service
import automatic_product_delivery as delivery
from models import (BotAuditEvent, BotConfiguration,
    ProcessedWhatsAppMessage, WhatsAppConversation, WhatsAppConversationMessage)
from test_whatsapp_quick_messages import inbox, send as manual_send, create as create_quick
from test_automatic_product_recommendations import store, PHONE, product, resolved
from test_saved_voices import voices
from test_whatsapp_outbound_media import inbox as media_inbox
from test_ad_campaign_replies import campaign
from ai_reply_test_support import admit


@pytest.fixture
def crm(inbox, monkeypatch):
    monkeypatch.setattr(main.settings, "META_APP_SECRET", None)
    monkeypatch.setattr(main, "generate_customer_reply", lambda *a, **k: "Hari, oyata kohomada udaw karanne?")
    return inbox


def toggle(client, csrf, enabled, generation=0, **extra):
    data = {"expected_generation": generation, "confirm": "CONFIRM", "csrf_token": csrf, **extra}
    if enabled:
        data["enabled"] = "on"
    return client.post("/crm/bot-management/global-ai", data=data, follow_redirects=False)


def set_state(sessions, enabled, generation=None):
    with sessions() as db:
        current = control.state(db)
        control.change(db, enabled=enabled, expected_generation=current["generation"] if generation is None else generation,
                       actor="test-admin")
        db.commit()


def receive(client, message_id, phone="94770012302", body="Hello", kind="text"):
    message = {"id": message_id, "from": phone, "type": kind, "timestamp": str(int(datetime.utcnow().timestamp()))}
    message[kind] = {"body": body} if kind == "text" else {"id": "mock-media", "mime_type": "image/png"}
    return client.post("/webhook", json={"entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]})


def test_on_off_on_preserves_modes_learning_and_configuration(crm):
    client, sessions, ids, sends, _, csrf = crm
    assert client.get("/crm/bot-management/settings").status_code == 200
    with sessions() as db:
        original = copy.deepcopy(db.query(BotConfiguration).one().draft_snapshot)
        published = copy.deepcopy(db.query(BotConfiguration).one().published_snapshot)
        modes = [(c.id, c.mode) for c in db.query(WhatsAppConversation).order_by(WhatsAppConversation.id)]
        from learning_service import configuration
        learning = copy.deepcopy(configuration(db)); db.commit()
    assert receive(client, "on-first").status_code == 200 and len(sends) == 1
    assert toggle(client, csrf, False).status_code == 303
    assert receive(client, "off-message").status_code == 200 and len(sends) == 1
    assert "AI OFFLINE — HUMAN STAFF ONLY" in client.get("/crm/whatsapp").text
    assert "AI OFFLINE — HUMAN STAFF ONLY" in client.get("/crm/bot-management").text
    assert toggle(client, csrf, True, generation=1).status_code == 303
    # An OFF-period receipt cannot be retried once ON.
    main._reply_to_text_message("94770012302", "off-message", "Hello", None)
    assert len(sends) == 1
    assert receive(client, "on-new").status_code == 200 and len(sends) == 2
    # Individual HUMAN mode is still respected after global re-enable.
    assert receive(client, "human-new", phone="94770012301").status_code == 200 and len(sends) == 2
    with sessions() as db:
        assert [(c.id,c.mode) for c in db.query(WhatsAppConversation).order_by(WhatsAppConversation.id)] == modes
        config = db.query(BotConfiguration).one()
        assert config.draft_snapshot == original and config.published_snapshot == published
        assert configuration(db) == learning
        records = {r.message_id:r.ai_reply_generation for r in db.query(ProcessedWhatsAppMessage)}
        assert records["on-first"] == 0 and records["off-message"] is None and records["on-new"] == 2
        audits = db.query(BotAuditEvent).filter_by(action="GLOBAL_AI_AUTO_REPLIES").order_by(BotAuditEvent.id).all()
        assert [json.loads(a.summary) for a in audits] == [
            {"previous":True,"new":False,"generation":1}, {"previous":False,"new":True,"generation":2}]
        assert all(a.actor == "quick-admin" and a.created_at for a in audits)


@pytest.mark.parametrize("role", ["quick-manager", "quick-staff", "quick-agent", "quick-denied"])
def test_only_admin_can_change_state(crm, role):
    client,sessions,_,_,login,_ = crm
    csrf = login(role)
    assert toggle(client,csrf,False).status_code == 403
    response = client.get("/crm/bot-management/settings")
    assert response.status_code == 200 and 'role="switch"' not in response.text
    with sessions() as db:
        assert control.state(db)["enabled"] and db.query(BotAuditEvent).filter_by(action="GLOBAL_AI_AUTO_REPLIES").count() == 0


def test_confirmation_csrf_stale_forms_and_noop(crm):
    client,sessions,_,_,_,csrf = crm
    assert toggle(client,"wrong",False).status_code == 403
    assert toggle(client,csrf,False,confirm="yes").status_code == 400
    assert toggle(client,csrf,True).status_code == 303  # no-op: no generation bump/audit
    with sessions() as db:
        assert control.state(db)["generation"] == 0
    assert toggle(client,csrf,False).status_code == 303
    assert toggle(client,csrf,True,generation=0).status_code == 409
    with sessions() as db:
        assert not control.state(db)["enabled"]
        assert db.query(BotAuditEvent).filter_by(action="GLOBAL_AI_AUTO_REPLIES").count() == 1
    client.cookies.clear()
    assert toggle(client,csrf,True,1).status_code == 303  # login redirect, not state change
    with sessions() as db: assert not control.state(db)["enabled"]


def test_two_conversations_queued_and_retried_jobs_cannot_replay(crm, monkeypatch):
    client,sessions,ids,sends,_,csrf = crm
    with sessions() as db:
        db.get(WhatsAppConversation,ids[0]).mode="AI"; db.commit()
    queued=[]
    monkeypatch.setattr(BackgroundTasks,"add_task",lambda self,fn,*args,**kw:queued.append((fn,args,kw)))
    assert receive(client,"queued-a",phone="94770012301").status_code == 200
    assert receive(client,"queued-b").status_code == 200
    assert len(queued) == 2 and not sends
    assert toggle(client,csrf,False).status_code == 303
    for fn,args,kw in queued: fn(*args,**kw)
    assert not sends
    assert receive(client,"offline-no-job").status_code == 200 and len(queued) == 2
    assert toggle(client,csrf,True,1).status_code == 303
    for fn,args,kw in queued: fn(*args,**kw)  # simulated delayed/retry workers
    assert not sends
    assert receive(client,"fresh-job").status_code == 200 and len(queued) == 3
    fn,args,kw=queued[-1]; fn(*args,**kw)
    assert len(sends) == 1


def test_disable_during_generation_blocks_unsent_reply_even_after_reenable(crm, monkeypatch):
    client,sessions,_,sends,_,_ = crm
    def generate(*a, **k):
        set_state(sessions,False)
        set_state(sessions,True)
        return "This stale reply must never be sent"
    monkeypatch.setattr(main,"generate_customer_reply",generate)
    assert receive(client,"running-model").status_code == 200
    assert not sends
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(direction="inbound").count() == 1
        assert db.query(WhatsAppConversationMessage).filter_by(direction="outbound").count() == 0


def test_off_stores_media_and_status_updates_without_any_ai_work(crm, monkeypatch):
    client,sessions,_,sends,_,csrf=crm
    assert toggle(client,csrf,False).status_code == 303
    monkeypatch.setattr(main,"generate_customer_reply",lambda *a,**k:pytest.fail("AI called while OFF"))
    monkeypatch.setattr(main,"inspect_customer_image",lambda *a,**k:pytest.fail("Vision called while OFF"))
    monkeypatch.setattr(main,"download_whatsapp_media",lambda *a,**k:None)
    for kind in ("text","image","audio","reaction"):
        assert receive(client,f"off-{kind}",kind=kind).status_code == 200
    with sessions() as db:
        assert db.query(ProcessedWhatsAppMessage).count() == 4
        assert all(r.ai_reply_generation is None for r in db.query(ProcessedWhatsAppMessage))
        db.add(WhatsAppConversationMessage(phone_number="94770012302",direction="outbound",message_text="Manual",
            whatsapp_message_id="wamid.manual-status",send_status="sent",response_kind="staff"));db.commit()
    response=client.post("/webhook",json={"entry":[{"changes":[{"value":{"statuses":[
        {"id":"wamid.manual-status","status":"read"} ]}}]}]})
    assert response.status_code == 200 and not sends
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id="wamid.manual-status").one().send_status == "read"


def test_manual_text_quick_message_and_product_cards_while_off(crm, monkeypatch):
    client,sessions,ids,texts,_,csrf=crm
    assert toggle(client,csrf,False).status_code == 303
    for index,cid in enumerate(ids):
        assert manual_send(client,cid,csrf,text="Staff reply",key=f"staff-off-message-{index}").status_code == 200
    quick=create_quick(client,csrf).json()["message"]
    assert manual_send(client,ids[1],csrf,qid=quick["id"],key="staff-off-quick-message").status_code == 200
    assert len(texts) == 3
    images=[]
    monkeypatch.setattr(main,"resolve_inbox_product",lambda *a:resolved())
    monkeypatch.setattr(main,"send_whatsapp_image",lambda *a,**k:images.append(a) or "wamid.staff-card")
    assert client.post(f"/crm/whatsapp/api/conversations/{ids[1]}/send-product",
        json={"product_id":product()["id"],"variant_id":product()["selected_variant_id"]},
        headers={"Idempotency-Key":"manual-off-product-card"}).status_code == 200
    assert len(images) == 1
    with sessions() as db:
        assert not control.state(db)["enabled"]
        assert db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_product").count() == 1


def test_saved_voice_upload_and_send_while_off(voices):
    v=voices;set_state(v.sessions,False)
    response=v.create();assert response.status_code == 201
    assert v.send(response.json()["voice"]).status_code == 200
    assert len(v.sends) == 1


def test_unknown_and_pre_migration_receipts_are_not_admitted(crm):
    _,sessions,_,sends,_,_=crm
    with sessions() as db:
        db.add(ProcessedWhatsAppMessage(message_id="old-null",sender_phone="94770012302",message_type="text",received_at=datetime.utcnow()))
        db.commit()
    for key in ("old-null","never-received"):
        main._reply_to_text_message("94770012302",key,"Hi",None)
    assert not sends
    with pytest.raises(control.AutoReplyBlocked):main._send_text_with_id("94770012302","No durable job")


def test_database_failure_closes_ai_without_disabling_manual_transport(crm, monkeypatch):
    client,sessions,_,sends,_,_=crm
    def unavailable(db):raise OperationalError("setting",{},Exception("unavailable"))
    monkeypatch.setattr(control,"state",unavailable)
    assert receive(client,"db-failure").status_code == 200 and not sends
    result=client.get("/crm/whatsapp/api/global-ai-state")
    assert result.status_code == 503 and result.json()["available"] is False
    assert result.headers["cache-control"] == "private, no-store"
    assert main.send_whatsapp_text("94770012302","Manual still works")
    assert len(sends) == 1


def test_actual_transport_guard_keeps_manual_send_available(crm, monkeypatch):
    _,sessions,_,_,_,_=crm
    admit(sessions,"94770012302","transport-job")
    calls=[]
    monkeypatch.setattr(whatsapp_service.settings,"META_WHATSAPP_ACCESS_TOKEN","fake")
    monkeypatch.setattr(whatsapp_service.settings,"META_WHATSAPP_PHONE_NUMBER_ID","fake")
    def post(url,**kw):
        calls.append(kw["json"])
        return httpx.Response(200,request=httpx.Request("POST",url),json={"messages":[{"id":"wamid.fake"}]})
    monkeypatch.setattr(whatsapp_service.httpx,"post",post)
    @control.automatic_job(lambda:sessions)
    def job(sender,message_id):
        set_state(sessions,False)
        whatsapp_service.send_whatsapp_text(sender,"Must block")
    job("94770012302","transport-job")
    assert not calls
    assert whatsapp_service.send_whatsapp_text("94770012302","Manual",return_message_id=True) == "wamid.fake"
    assert len(calls) == 1


def test_automatic_cards_stop_if_disabled_during_catalog_lookup(store):
    admit(store,PHONE,"cards-job")
    calls=[]
    def lookup(*a):set_state(store,False);return resolved()
    assert delivery.deliver(store,PHONE,"cards-job",[product()],lookup,
        lambda *a:calls.append(a) or ("SENT","wamid.blocked")) == []
    assert not calls
    set_state(store,True)
    delivery.deliver(store,PHONE,"cards-job",[product()],lambda *a:resolved(),lambda *a:calls.append(a))
    assert not calls


def pg_only(sessions):
    if sessions.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Run with task-owned AUTO_PRODUCTS_TEST_DATABASE_URL for PostgreSQL locks")


def test_postgresql_separate_workers_send_concurrently_and_off_waits_for_boundary(store):
    pg_only(store)
    worker2=sessionmaker(bind=store.kw["bind"],expire_on_commit=False)
    other_phone="94770012346"
    with store() as db:
        db.add(WhatsAppConversation(phone_number=other_phone,mode="AI",last_customer_message_at=datetime.utcnow()))
        db.commit()
    for key,phone in (("send-a",PHONE),("send-b",other_phone),("blocked-next",PHONE)):
        admit(store,phone,key)
    inside=Barrier(2); release=Event(); entered=Event(); off_requested=Event(); calls=[]
    @control.automatic_job(lambda:store)
    def first(sender,message_id):
        with control.send_boundary(require_job=True):
            inside.wait(timeout=10); entered.set()
            assert release.wait(timeout=10)
            calls.append(message_id)
    @control.automatic_job(lambda:worker2)
    def second(sender,message_id):
        with control.send_boundary(require_job=True):
            inside.wait(timeout=10)
            assert release.wait(timeout=10)
            calls.append(message_id)
    @control.automatic_job(lambda:worker2)
    def pending(sender,message_id):
        with control.send_boundary(require_job=True):calls.append(message_id)
    def before(conn,cursor,statement,parameters,context,executemany):
        if "FOR UPDATE" in statement and "global_ai_reply_control" in statement:off_requested.set()
    event.listen(store.kw["bind"],"before_cursor_execute",before)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            a=pool.submit(first,PHONE,"send-a");b=pool.submit(second,other_phone,"send-b")
            assert entered.wait(timeout=10)  # Both shared locks coexisted.
            off=pool.submit(set_state,worker2,False)
            assert off_requested.wait(timeout=5)
            with pytest.raises(TimeoutError):off.result(timeout=.15)
            next_job=pool.submit(pending,PHONE,"blocked-next")
            release.set();a.result(timeout=10);b.result(timeout=10);off.result(timeout=10);next_job.result(timeout=10)
        assert sorted(calls) == ["send-a","send-b"]
        with worker2() as db:assert not control.state(db)["enabled"]
    finally:
        release.set();event.remove(store.kw["bind"],"before_cursor_execute",before)


def test_postgresql_concurrent_admin_changes_audit_once_and_invalidate_other_worker(store):
    pg_only(store)
    worker2=sessionmaker(bind=store.kw["bind"],expire_on_commit=False)
    admit(store,PHONE,"queued-other-worker")
    start=Barrier(2)
    def change(factory):
        with factory() as db:
            start.wait(timeout=5)
            try:
                control.change(db,enabled=False,expected_generation=0,actor="admin-instance")
                db.commit();return "changed"
            except ValueError:db.rollback();return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,[store,worker2]))
    assert sorted(results) == ["changed","stale"]
    assert control.message_job(worker2,"queued-other-worker",PHONE) is None
    with store() as db:
        assert db.query(BotAuditEvent).filter_by(action="GLOBAL_AI_AUTO_REPLIES").count() == 1
        assert control.state(db)["generation"] == 1


@pytest.mark.parametrize("orm_first", [False, True])
def test_additive_migration_preserves_receipts_and_never_resets_switch(tmp_path, monkeypatch, orm_first):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    import database
    from models import Base
    engine=create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE global_ai_reply_control"))
        conn.execute(text("ALTER TABLE processed_whatsapp_messages DROP COLUMN ai_reply_generation"))
        conn.execute(text("INSERT INTO processed_whatsapp_messages (message_id,sender_phone,message_type,received_at,processed_at) VALUES ('old-job','94770012302','text',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    if orm_first:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:conn.execute(text("UPDATE global_ai_reply_control SET enabled=false, generation=5 WHERE id=1"))
    monkeypatch.setattr(database,"engine",engine)
    config=Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.stamp(config,"20261027_17");command.upgrade(config,"head");command.upgrade(config,"head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT ai_reply_generation FROM processed_whatsapp_messages WHERE message_id='old-job'")).scalar() is None
        row=conn.execute(text("SELECT enabled,generation FROM global_ai_reply_control")).one()
        assert tuple(row) == ((0,5) if orm_first else (1,0))
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20261030_21"
    engine.dispose()


def test_manual_attachment_send_while_off(media_inbox):
    _,sessions,ids,post,uploads,sends=media_inbox
    set_state(sessions,False)
    assert post(cid=ids[1]).status_code == 200
    assert len(uploads) == len(sends) == 1


@pytest.mark.parametrize("kind", ["image","audio"])
def test_queued_image_and_unsupported_jobs_are_invalidated(crm, monkeypatch, kind):
    client,sessions,_,sends,_,csrf=crm;queued=[]
    monkeypatch.setattr(BackgroundTasks,"add_task",lambda self,fn,*args,**kw:queued.append((fn,args,kw)))
    monkeypatch.setattr(main,"download_whatsapp_media",lambda *a,**k:None)
    assert receive(client,"queued-media",kind=kind).status_code == 200 and len(queued) == 1
    assert toggle(client,csrf,False).status_code == 303
    assert toggle(client,csrf,True,1).status_code == 303
    fn,args,kw=queued[0];fn(*args,**kw)
    assert not sends


def test_ad_followup_in_flight_is_blocked(campaign, monkeypatch):
    from test_ad_campaign_replies import inbound
    sessions,texts=campaign
    inbound(sessions,"ad-entry");admit(sessions,PHONE,"ad-entry")
    main._reply_to_text_message(PHONE,"ad-entry","Hi",None)
    assert len(texts) == 1
    inbound(sessions,"ad-followup","Ow",source=None);admit(sessions,PHONE,"ad-followup")
    def generate(*a,**k):set_state(sessions,False);return "Do not send this follow-up"
    monkeypatch.setattr(main,"generate_customer_reply",generate)
    main._reply_to_text_message(PHONE,"ad-followup","Ow",None)
    assert len(texts) == 1
    with sessions() as db:
        from models import WhatsAppManualSend
        assert db.query(WhatsAppManualSend).filter_by(status="SKIPPED").count() == 1


def test_model_calls_checkpoint_and_disable_sdk_retries_for_customer_jobs(crm, monkeypatch):
    import ai_service
    _,sessions,_,_,_,_=crm
    admit(sessions,"94770012302","model-boundary")
    clients=[];calls=[]
    def create(**kwargs):calls.append(kwargs);return SimpleNamespace(output_text="Hi")
    def client(**kwargs):clients.append(kwargs);return SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(ai_service.settings,"OPENAI_API_KEY","fake")
    monkeypatch.setattr(ai_service,"OpenAI",client)
    monkeypatch.setattr(ai_service,"published_response_preferences",lambda:None)
    monkeypatch.setattr(ai_service.learning_runtime,"retrieve",lambda *a:None)
    @control.automatic_job(lambda:sessions)
    def job(sender,message_id):
        assert ai_service.generate_customer_reply("Hi") == "Hi"
        set_state(sessions,False)
        ai_service.generate_customer_reply("Must not generate")
    job(sender="94770012302",message_id="model-boundary")
    assert len(calls) == 1 and clients[0]["max_retries"] == 0


def test_audit_failure_rolls_back_state_change(crm):
    client,sessions,_,_,_,csrf=crm
    def fail(conn,cursor,statement,params,ctx,many):
        if statement.lstrip().startswith("INSERT INTO bot_audit_events"):
            raise OperationalError(statement,{},Exception("audit outage"))
    engine=sessions.kw["bind"];event.listen(engine,"before_cursor_execute",fail)
    try:assert toggle(client,csrf,False).status_code == 503
    finally:event.remove(engine,"before_cursor_execute",fail)
    with sessions() as db:
        assert control.state(db)["enabled"] and control.state(db)["generation"] == 0
        assert db.query(BotAuditEvent).filter_by(action="GLOBAL_AI_AUTO_REPLIES").count() == 0


def test_chromium_confirmation_status_refresh_and_inbox_layout(crm):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip("playwright.sync_api");chromium=shutil.which("chromium")
    if not chromium:pytest.skip("Chromium unavailable")
    client,sessions,_,sends,_,_=crm
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=["--no-sandbox"])
        try:
            page=browser.new_page(viewport={"width":1280,"height":800});errors=[];posts=[];responses=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.on("requestfailed",lambda request:responses.append(("FAILED",request.url,request.failure)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname != "global-ai.test":route.fulfill(status=200,body="");return
                if request.method == "POST":posts.append(url.path)
                response=client.request(request.method,url.path+("?"+url.query if url.query else ""),
                    content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k in {"content-type","x-csrf-token"}},
                    follow_redirects=False)
                responses.append((request.method,url.path,response.status_code,response.text[:300] if response.status_code >= 400 else ""))
                # Intercepted redirects otherwise escape to the test hostname's
                # network; follow the real app redirect through TestClient.
                if response.status_code == 303:
                    response=client.get(response.headers["location"])
                headers={k:v for k,v in response.headers.items() if k in {"content-type","cache-control"}}
                route.fulfill(status=response.status_code,body=response.content,headers=headers)
            page.route("**/*",serve)
            page.goto("https://global-ai.test/crm/bot-management/settings")
            switch=page.get_by_role("switch");api.expect(switch).to_be_checked();switch.uncheck()
            page.get_by_role("button",name="Apply global AI state").click()
            assert not posts  # Browser blocks absent explicit confirmation.
            page.locator('[name="confirm"]').fill("CONFIRM")
            page.get_by_role("button",name="Apply global AI state").click()
            page.wait_for_load_state("networkidle")
            assert page.locator('[data-global-ai-label]').count(), responses
            api.expect(page.locator('[data-global-ai-label]')).to_have_text("AI OFFLINE — HUMAN STAFF ONLY")
            assert posts == ["/crm/bot-management/global-ai"]
            page.goto("https://global-ai.test/crm/whatsapp")
            api.expect(page.locator('header [data-global-ai-label]')).to_have_text("AI OFFLINE — HUMAN STAFF ONLY")
            page.locator('.conversation-row').first.click()
            api.expect(page.locator('#send-button')).to_be_visible()
            box=page.locator('#send-button').bounding_box();assert box["y"]+box["height"] <= 800
            set_state(sessions,True)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            api.expect(page.locator('[data-global-ai-label]')).to_have_text("AI ONLINE")
            page.set_viewport_size({"width":375,"height":812})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not sends and not errors
        finally:browser.close()


def test_failure_after_entering_send_boundary_is_not_misreported_as_blocked(crm):
    _,sessions,_,_,_,_=crm
    admit(sessions,"94770012302","uncertain-boundary")
    reached=[]
    @control.automatic_job(lambda:sessions)
    def job(sender,message_id):
        with control.send_boundary(require_job=True):
            reached.append(True)
            raise OperationalError("after external acceptance",{},Exception("database outage"))
    with pytest.raises(OperationalError):job("94770012302","uncertain-boundary")
    assert reached == [True]
