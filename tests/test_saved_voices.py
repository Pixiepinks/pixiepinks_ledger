"""Saved recordings: real audio conversion, private storage and mocked Meta sends."""
import io
import json
import shutil
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import main
import saved_voice_service as service
from models import SavedVoice, SavedVoiceSend, WhatsAppConversation, WhatsAppConversationMessage
from settings import settings
from test_whatsapp_quick_messages import inbox

BASE = "/crm/saved-voices/api"


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 8000)
    return buffer.getvalue()


@pytest.fixture
def voices(inbox, tmp_path, monkeypatch):
    client, sessions, ids, texts, login, csrf = inbox
    directory = tmp_path / "persistent-voices"
    monkeypatch.setattr(settings, "SAVED_VOICES_DIR", str(directory))
    uploads, sends = [], []
    monkeypatch.setattr(main, "upload_whatsapp_audio", lambda raw: uploads.append(raw) or "media.saved")
    monkeypatch.setattr(main, "send_whatsapp_audio", lambda *args, **kwargs: sends.append((args, kwargs)) or ("SENT", f"wamid.saved.{len(sends)}"))
    def create(**changes):
        data = {"title": "Delivery explanation", "category": "Delivery", "transcript": "Api islandwide delivery karanawa.", "language": "singlish", **changes}
        return client.post(BASE, data=data, files={"audio": ("recording.wav", wav(), "audio/wav")}, headers={"X-CSRF-Token": csrf})
    def send(row, key="saved-voice-attempt-0001", cid=None):
        return client.post(f"/crm/whatsapp/api/conversations/{cid or ids[0]}/send-saved-voice",
            json={"voice_id": row["id"], "revision": row["revision"]}, headers={"X-CSRF-Token": csrf,"Idempotency-Key": key})
    return SimpleNamespace(client=client, sessions=sessions, ids=ids, texts=texts, login=login, csrf=csrf,
        directory=directory, uploads=uploads, sends=sends, create=create, send=send)


def test_real_upload_private_preview_transcript_search_and_no_customer_send(voices):
    v = voices; response = v.create()
    assert response.status_code == 201, response.text
    row = response.json()["voice"]
    assert 0.25 <= row["duration_seconds"] <= 1 and row["size_bytes"] > 0
    assert set(path.suffix for path in v.directory.iterdir()) == {".ogg"}
    assert "storage_name" not in row and "sha256" not in row
    listed = v.client.get(BASE, params={"q": "islandwide"})
    assert listed.json()["voices"][0]["transcript"] == row["transcript"]
    assert listed.headers["cache-control"] == "private, no-store"
    audio = v.client.get(f"{BASE}/{row['id']}/audio", headers={"Range": "bytes=0-3"})
    assert audio.status_code == 206 and audio.content == b"OggS"
    assert v.client.get(f"{BASE}/{row['id']}/audio", headers={"Range": "bytes=-0"}).status_code == 416
    assert not v.uploads and not v.sends and not v.texts


def test_send_is_voice_only_durable_and_history_plays_from_volume(voices, monkeypatch):
    v = voices; row = v.create().json()["voice"]
    first = v.send(row)
    assert first.status_code == 200 and first.json()["status"] == "SENT"
    assert v.send(row).json()["status"] == "SENT" and len(v.sends) == len(v.uploads) == 1
    assert v.uploads[0].startswith(b"OggS") and v.sends[0][1] == {"voice": True, "return_outcome": True}
    assert not v.texts
    with v.sessions() as db:
        message = db.query(WhatsAppConversationMessage).filter_by(response_kind="staff_saved_voice").one()
        assert row["transcript"] in message.message_text
        mid = message.id
    monkeypatch.setattr(main, "download_whatsapp_media", lambda *a: pytest.fail("Saved recording depended on expired Meta media"))
    played = v.client.get(f"/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{mid}/media")
    assert played.content == v.uploads[0] and played.headers["cache-control"] == "private, no-store"
    key = "saved-voice-attempt-0001"
    assert v.client.get(f"/crm/whatsapp/api/conversations/{v.ids[0]}/saved-voice-sends/{key}").json()["status"] == "SENT"


def test_roles_csrf_and_private_files(voices):
    v = voices; row = v.create().json()["voice"]
    assert TestClient(main.app).get(f"{BASE}/{row['id']}/audio", follow_redirects=False).status_code == 303
    assert v.client.post(BASE, data={}, headers={}).status_code == 403
    v.login("quick-staff")
    assert v.client.get(BASE).status_code == 200
    assert v.client.get(f"{BASE}/{row['id']}/audio").status_code == 200
    assert v.create().status_code == 403
    assert v.client.get(BASE, params={"inactive": True}).status_code == 403
    v.login("quick-denied")
    assert v.client.get(BASE).status_code == 403
    assert v.client.get("/crm/saved-voices").status_code == 403


def test_edit_deactivate_and_stale_selection_guard(voices):
    v = voices; row = v.create().json()["voice"]
    values = {key:row[key] for key in ("title", "category", "transcript", "language", "active", "revision")}
    values.update(active=False, transcript="New written words")
    edited = v.client.patch(f"{BASE}/{row['id']}", json=values, headers={"X-CSRF-Token":v.csrf})
    assert edited.status_code == 200 and edited.json()["voice"]["revision"] == 2
    assert v.client.get(BASE).json()["voices"] == []
    assert v.send(row).status_code == 404 and not v.sends
    assert v.client.patch(f"{BASE}/{row['id']}", json=values, headers={"X-CSRF-Token":v.csrf}).status_code == 409
    values.update(active=True, revision=2)
    assert v.client.patch(f"{BASE}/{row['id']}", json=values, headers={"X-CSRF-Token":v.csrf}).status_code == 200
    assert v.send(row).status_code == 409


@pytest.mark.parametrize("raw,mime", [(b"not audio", "audio/wav"), (b"x", "text/plain"), (b"", "audio/mpeg")])
def test_bad_uploads_do_not_create_recordings(voices, raw, mime):
    v = voices
    response = v.client.post(BASE, data={"title":"Test", "category":"Test", "transcript":"Words", "language":"si"},
        files={"audio": ("file.wav", raw, mime)}, headers={"X-CSRF-Token":v.csrf})
    assert response.status_code == 422 and not v.directory.exists()
    with v.sessions() as db: assert db.query(SavedVoice).count() == 0


def test_missing_tampered_or_symlinked_storage_never_sends(voices):
    v = voices; row = v.create().json()["voice"]
    path = next(v.directory.iterdir()); original = path.read_bytes()
    path.write_bytes(b"tampered")
    assert v.send(row).status_code == 503 and not v.uploads
    path.unlink(); outside = v.directory.parent / "outside.ogg"; outside.write_bytes(original)
    path.symlink_to(outside)
    assert v.send(row).status_code == 503 and not v.uploads
    path.unlink()
    assert v.client.get(f"{BASE}/{row['id']}/audio").status_code == 503


def test_closed_window_and_changed_library_during_upload_stop_delivery(voices, monkeypatch):
    v = voices; row = v.create().json()["voice"]
    with v.sessions() as db:
        conversation = db.query(WhatsAppConversation).get(v.ids[0])
        conversation.last_customer_message_at = datetime.utcnow() - timedelta(hours=25); db.commit()
    assert v.send(row).status_code == 409 and not v.uploads
    with v.sessions() as db:
        db.query(WhatsAppConversation).get(v.ids[0]).last_customer_message_at = datetime.utcnow(); db.commit()
    def upload(raw):
        with v.sessions() as db: db.query(SavedVoice).filter_by(id=row["id"]).update({"active":False}); db.commit()
        return "media.id"
    monkeypatch.setattr(main, "upload_whatsapp_audio", upload)
    assert v.send(row).json()["status"] == "FAILED" and not v.sends


def test_unknown_send_scope_and_no_automatic_retry(voices, monkeypatch):
    v = voices; row = v.create().json()["voice"]
    calls = []
    monkeypatch.setattr(main, "send_whatsapp_audio", lambda *a, **k: calls.append(a) or ("UNKNOWN",None))
    assert v.send(row).json()["status"] == "UNKNOWN"
    assert v.send(row).json()["status"] == "UNKNOWN" and len(calls) == 1
    assert v.send(row, cid=v.ids[1]).status_code == 409
    v.login("quick-manager")
    assert v.client.get(f"/crm/whatsapp/api/conversations/{v.ids[0]}/saved-voice-sends/saved-voice-attempt-0001").status_code == 409


def test_database_failure_after_provider_send_cannot_duplicate(voices):
    v = voices; row = v.create().json()["voice"]
    with v.sessions() as db: engine = db.get_bind()
    # SQLAlchemy failure handling is exercised with a real SQL exception.
    from sqlalchemy.exc import OperationalError
    def fail(conn, cursor, statement, *args):
        if statement.startswith("INSERT INTO whatsapp_conversation_messages"):
            raise OperationalError(statement, {}, RuntimeError("simulated outage"))
    event.listen(engine, "before_cursor_execute", fail)
    try: assert v.send(row).status_code == 503
    finally: event.remove(engine, "before_cursor_execute", fail)
    assert v.send(row).json()["status"] == "SENDING" and len(v.sends) == 1


def test_concurrent_same_attempt_sends_once(voices):
    v = voices; row = v.create().json()["voice"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: v.send(row, key="saved-voice-concurrent-001"), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert len(v.uploads) == len(v.sends) == 1


def test_management_and_inbox_have_transcripts_and_explicit_send(voices):
    v = voices
    assert v.client.get("/crm/saved-voices").status_code == 200
    html = v.client.get("/crm/whatsapp").text
    assert 'id="saved-voices-open"' in html and 'id="saved-voice-send"' in html


@pytest.mark.parametrize("http_status,expected", [(400,"FAILED"), (403,"FAILED"), (429,"UNKNOWN"), (500,"UNKNOWN"), (200,"SENT")])
def test_meta_voice_outcomes_and_audio_only_payload(monkeypatch, http_status, expected):
    import httpx
    import whatsapp_service
    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "12345")
    def post(url, **kwargs):
        assert kwargs["json"]["audio"] == {"id":"media.saved", "voice":True}
        assert kwargs["json"]["type"] == "audio" and "text" not in kwargs["json"]
        return httpx.Response(http_status, json={"messages":[{"id":"wamid.ok"}]}, request=httpx.Request("POST",url))
    monkeypatch.setattr(whatsapp_service.httpx, "post", post)
    outcome, mid = whatsapp_service.send_whatsapp_audio("94770000001", "media.saved", return_outcome=True)
    assert outcome == expected and (mid == "wamid.ok") == (expected == "SENT")


def test_postgresql_upgrade_and_concurrent_claim(tmp_path, monkeypatch):
    import os
    import hashlib
    from uuid import uuid4
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from alembic import command
    from alembic.config import Config
    import database
    from models import Base, User
    from audio_service import normalize_voice_audio
    url = os.environ.get("TEST_POSTGRESQL_URL")
    if not url: pytest.skip("Dedicated TEST_POSTGRESQL_URL not configured")
    admin = create_engine(url)
    assert admin.url.database == "pixiepinks_test"
    schema = "saved_voices_" + uuid4().hex
    with admin.begin() as conn: conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    try:
        Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in {"saved_voices", "saved_voice_sends"}])
        sessions = sessionmaker(bind=engine, autoflush=False)
        with sessions() as db:
            db.add(User(id=1, username="voice-test", password_hash="not-a-login", role="STAFF"))
            db.add(WhatsAppConversation(id=1, phone_number="94770000123", mode="HUMAN", last_customer_message_at=datetime.utcnow()))
            db.commit()
        monkeypatch.setattr(database, "engine", engine)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.stamp(config, "20261026_16"); command.upgrade(config, "head"); command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == "20261029_18"
            assert conn.execute(text("select count(*) from whatsapp_conversations")).scalar() == 1
        monkeypatch.setattr(settings, "SAVED_VOICES_DIR", str(tmp_path / "pg-volume"))
        raw, duration = normalize_voice_audio(wav(), "audio/wav")
        name = service.write_audio(raw)
        with sessions() as db:
            row = SavedVoice(title="Greeting", category="Greeting", transcript="Ayubowan", language="singlish", active=True,
                revision=1, storage_name=name, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), duration_seconds=duration,
                created_by="voice-test", updated_by="voice-test")
            db.add(row); db.commit(); voice_id = row.id
        uploads, sends = [], []
        def submit():
            with sessions() as db:
                return service.send(db, db.get(User, 1), db.get(WhatsAppConversation, 1), service.Send(voice_id=voice_id, revision=1),
                    "saved-voice-pg-concurrent-0001", lambda raw: uploads.append(raw) or "media.pg",
                    lambda *args: sends.append(args) or ("SENT", "wamid.pg.saved"))
        with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: submit(), range(2)))
        assert len(sends) == len(uploads) == 1
        with sessions() as db:
            assert db.query(SavedVoiceSend).one().status == "SENT"
            assert service.read_audio(db.get(SavedVoice, voice_id)) == raw
    finally:
        engine.dispose()
        with admin.begin() as conn: conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_chromium_library_preview_selection_and_lost_ack_recovery(voices):
    api = pytest.importorskip("playwright.sync_api"); chromium = shutil.which("chromium")
    if not chromium: pytest.skip("Chromium unavailable")
    v = voices; row = v.create(title="Delivery recording", transcript="Api islandwide delivery karanawa.").json()["voice"]
    with api.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=chromium, headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(); errors = []; requests = []; lost = [True]
            page.on("pageerror", lambda error: errors.append(str(error)))
            def serve(route):
                req = route.request; url = urlsplit(req.url)
                if url.hostname != "voices.test": route.fulfill(status=200, body=""); return
                headers = {key:value for key,value in req.headers.items() if key in {"content-type", "x-csrf-token", "idempotency-key", "range"}}
                requests.append((req.method, url.path))
                response = v.client.request(req.method, url.path + ("?" + url.query if url.query else ""),
                    content=req.post_data_buffer, headers=headers, follow_redirects=False)
                if req.method == "POST" and url.path.endswith("/send-saved-voice") and lost[0]:
                    lost[0] = False; route.abort("failed"); return
                route.fulfill(status=response.status_code, body=response.content,
                    headers={key:value for key,value in response.headers.items() if key not in {"set-cookie", "content-length", "content-encoding"}})
            page.route("**/*", serve)
            page.goto(f"https://voices.test/crm/whatsapp?conversation_id={v.ids[0]}")
            page.locator(f'[data-conversation-id="{v.ids[0]}"]').click()
            page.locator("#saved-voices-open").click()
            page.locator("#saved-voice-list button").filter(has_text="Choose recording").click()
            assert "94770012301" in page.locator("#saved-voice-recipient").inner_text()
            assert page.locator("#saved-voice-selected-transcript").inner_text() == row["transcript"]
            assert not v.uploads and not v.sends
            page.locator("#saved-voice-send").click()
            page.locator("#saved-voice-status").filter(has_text="Check send status").wait_for()
            assert len(v.sends) == 1
            page.locator("#saved-voice-check").click()
            page.locator("#saved-voice-status").filter(has_text="Voice message sent").wait_for()
            assert len(v.sends) == 1
            assert sum(method == "POST" and path.endswith("/send-saved-voice") for method,path in requests) == 1
            page.locator("#saved-voice-close").click()
            page.goto("https://voices.test/crm/saved-voices")
            page.locator("#saved-voice-list h3").filter(has_text="Delivery recording").wait_for()
            assert page.locator("#saved-voice-list").inner_text().find(row["transcript"]) >= 0
            assert not errors
        finally: browser.close()
