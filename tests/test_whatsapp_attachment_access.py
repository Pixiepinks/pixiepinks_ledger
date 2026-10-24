"""Authenticated attachment access and bounded provider downloads; no live Meta calls."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import whatsapp_service as service
from models import Base, User, WhatsAppConversation, WhatsAppConversationMessage, WhatsAppMessageAttachment
from utils_auth import hash_password


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'inbox.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    with sessions() as db:
        db.add(User(username="media-staff", password_hash=hash_password("password"), role="STAFF"))
        conversation = WhatsAppConversation(phone_number="94770000000", mode="HUMAN", unread_count=2)
        other = WhatsAppConversation(phone_number="94771111111", mode="AI")
        message = WhatsAppConversationMessage(phone_number="94770000000", direction="inbound", message_text="Image message")
        other_message = WhatsAppConversationMessage(phone_number="94771111111", direction="inbound", message_text="Other")
        db.add_all([conversation, other, message, other_message]); db.flush()
        attachment = WhatsAppMessageAttachment(message_id=message.id, media_id="provider-private-id",
            media_type="image", mime_type="image/png", filename="customer.png", caption="<script>untrusted</script>", file_size_bytes=1024)
        db.add(attachment); db.commit()
        ids = conversation.id, message.id, attachment.id, other.id, other_message.id
    client = TestClient(main.app, base_url="https://testserver")
    assert client.post("/login", data={"username": "media-staff", "password": "password"}).status_code == 200
    yield client, sessions, ids
    engine.dispose()


def attachment_url(ids):
    return f"/crm/whatsapp/api/conversations/{ids[0]}/messages/{ids[1]}/attachments/{ids[2]}/media"


def test_detail_has_safe_attachment_metadata_without_provider_identifiers(inbox):
    client, sessions, ids = inbox
    response = client.get(f"/crm/whatsapp/api/conversations/{ids[0]}")
    attachment = response.json()["messages"][0]["attachments"][0]
    assert attachment["url"] == attachment_url(ids)
    assert (attachment["type"], attachment["filename"], attachment["caption"], attachment["size_bytes"]) == (
        "image", "customer.png", "<script>untrusted</script>", 1024)
    assert "provider-private-id" not in response.text
    with sessions() as db:
        conversation = db.get(WhatsAppConversation, ids[0])
        assert conversation.unread_count == 2 and conversation.mode == "HUMAN"


def test_access_checks_run_before_provider_download(inbox, monkeypatch):
    client, _, ids = inbox
    monkeypatch.setattr(main, "download_whatsapp_attachment", lambda *a: pytest.fail("Unauthorized provider call"))
    anonymous = TestClient(main.app, base_url="https://testserver")
    assert anonymous.get(attachment_url(ids), follow_redirects=False).status_code == 303
    assert anonymous.get(f"/crm/whatsapp/api/conversations/{ids[0]}", follow_redirects=False).status_code == 303
    for wrong in [(ids[3], ids[1], ids[2]), (ids[0], ids[4], ids[2]), (ids[0], ids[1], 999999), (999999, ids[1], ids[2])]:
        assert client.get(attachment_url(wrong)).status_code == 404


def test_inline_media_headers_and_video_ranges(inbox, monkeypatch):
    client, _, ids = inbox
    calls = []
    monkeypatch.setattr(main, "download_whatsapp_attachment", lambda *a: calls.append(a) or (b"0123456789", "video/mp4"))
    response = client.get(attachment_url(ids))
    assert response.content == b"0123456789" and response.headers["content-type"] == "video/mp4"
    assert calls == [("provider-private-id", "image")]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "sandbox" in response.headers["content-security-policy"]
    for value, expected in [("bytes=2-5", b"2345"), ("bytes=8-", b"89"), ("bytes=-3", b"789"), ("bytes=8-99", b"89")]:
        partial = client.get(attachment_url(ids), headers={"Range": value})
        assert partial.status_code == 206 and partial.content == expected
        assert partial.headers["content-range"].endswith("/10")
    for value in ["bytes=10-", "bytes=-0", "bytes=5-2", "bytes=a-b", "bytes=0-1,4-5", "wrong=0-1"]:
        response = client.get(attachment_url(ids), headers={"Range": value})
        assert response.status_code == 416 and response.headers["content-range"] == "bytes */10"


def test_documents_force_download_with_safe_unicode_filename(inbox, monkeypatch):
    client, sessions, ids = inbox
    with sessions() as db:
        attachment = db.get(WhatsAppMessageAttachment, ids[2])
        attachment.media_type = "document"; attachment.filename = '../\\invoice\r\n"සි.pdf'
        db.commit()
    monkeypatch.setattr(main, "download_whatsapp_attachment", lambda *a: (b"%PDF-safe", "application/pdf"))
    response = client.get(attachment_url(ids))
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=UTF-8''invoice")
    assert "\r" not in disposition and "\n" not in disposition and "../" not in disposition


@pytest.mark.parametrize("code", [410, 413, 415, 502, 503])
def test_provider_failures_are_safe_and_do_not_change_metadata(inbox, monkeypatch, code):
    client, sessions, ids = inbox
    def fail(*args):
        raise service.AttachmentMediaError(code, "Attachment unavailable")
    monkeypatch.setattr(main, "download_whatsapp_attachment", fail)
    response = client.get(attachment_url(ids))
    assert response.status_code == code and response.json() == {"detail": "Attachment unavailable"}
    assert "no-store" in response.headers["cache-control"]
    with sessions() as db:
        assert db.query(WhatsAppMessageAttachment).count() == 1
        assert db.get(WhatsAppConversation, ids[0]).mode == "HUMAN"


@pytest.fixture
def provider(monkeypatch):
    original_client = httpx.Client
    monkeypatch.setattr(service.settings, "META_WHATSAPP_ACCESS_TOKEN", "test-only-token")
    def install(handler):
        monkeypatch.setattr(service.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    return install


@pytest.mark.parametrize("kind,mime,body", [
    ("image", "image/png", b"\x89PNG\r\n\x1a\nimage"),
    ("image", "image/jpeg", b"\xff\xd8\xffimage"),
    ("sticker", "image/webp", b"RIFF0000WEBPsticker"),
    ("video", "video/mp4", b"0000ftypvideo"),
    ("audio", "audio/ogg", b"OggSaudio"),
    ("document", "application/pdf", b"%PDF-document"),
    ("document", "text/plain", b"Customer document"),
])
def test_provider_fetches_fresh_metadata_and_bounded_content(provider, kind, mime, body):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-only-token"
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/media?temporary=private", "mime_type": mime})
        return httpx.Response(200, content=body, headers={"content-type": mime})
    provider(handler)
    assert service.download_whatsapp_attachment("12345", kind) == (body, mime)
    assert len(calls) == 2


@pytest.mark.parametrize("url", ["http://lookaside.fbsbx.com/file", "https://127.0.0.1/file", "https://evil.fbcdn.net.attacker.test/file",
    "https://lookaside.fbsbx.com@attacker.test/file", "https://user:pass@lookaside.fbsbx.com/file", "https://lookaside.fbsbx.com:8443/file",
    "https://facebook.com/file", "https://lookaside.fbsbx.com:bad/file", None])
def test_untrusted_destinations_never_receive_credentials(provider, url):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"url": url, "mime_type": "image/png"})
    provider(handler)
    with pytest.raises(service.AttachmentMediaError) as error:
        service.download_whatsapp_attachment("12345", "image")
    assert error.value.status_code == 502 and len(calls) == 1


@pytest.mark.parametrize("status,expected", [(404, 410), (410, 410), (401, 502), (403, 502), (500, 502), (302, 502)])
def test_upstream_failures_do_not_expose_response_or_follow_redirects(provider, status, expected):
    def handler(request):
        return httpx.Response(status, text="private-token-and-url", headers={"location": "https://attacker.test/private"})
    provider(handler)
    with pytest.raises(service.AttachmentMediaError) as error:
        service.download_whatsapp_attachment("12345", "image")
    assert error.value.status_code == expected and "private" not in error.value.detail


@pytest.mark.parametrize("metadata", [[], None, {}, {"mime_type": []}, {"mime_type": "image/svg+xml"}, {"mime_type": "text/html"}])
def test_malformed_metadata_and_active_formats_are_rejected(provider, metadata):
    provider(lambda request: httpx.Response(200, content=json.dumps(metadata).encode()))
    with pytest.raises(service.AttachmentMediaError):
        service.download_whatsapp_attachment("12345", "image")


@pytest.mark.parametrize("mode", ["declared", "header", "stream", "mismatch", "signature", "redirect", "timeout"])
def test_limits_and_invalid_content(provider, monkeypatch, mode):
    monkeypatch.setattr(service, "MAX_DOWNLOAD_BYTES", 16)
    calls = []
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"12345678"
            yield b"123456789"
    def handler(request):
        calls.append(request)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/file", "mime_type": "image/png",
                                            **({"file_size": 17} if mode == "declared" else {})})
        if mode == "timeout":
            raise httpx.ReadTimeout("private-url")
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.test/file"})
        headers = {"content-type": "text/html" if mode == "mismatch" else "image/png"}
        if mode == "header": headers["content-length"] = "17"
        return httpx.Response(200, stream=Chunks(), headers=headers) if mode == "stream" else httpx.Response(200, content=b"not-an-image", headers=headers)
    provider(handler)
    with pytest.raises(service.AttachmentMediaError) as error:
        service.download_whatsapp_attachment("12345", "image")
    assert error.value.status_code == ({"declared": 413, "header": 413, "stream": 413, "mismatch": 415,
                                       "signature": 415, "redirect": 502, "timeout": 502}[mode])
    assert len(calls) == (1 if mode == "declared" else 2)


def test_missing_configuration_and_invalid_provider_ids(provider, monkeypatch):
    provider(lambda request: pytest.fail("Unexpected network call"))
    monkeypatch.setattr(service.settings, "META_WHATSAPP_ACCESS_TOKEN", None)
    with pytest.raises(service.AttachmentMediaError) as error:
        service.download_whatsapp_attachment("12345", "image")
    assert error.value.status_code == 503
    with pytest.raises(service.AttachmentMediaError) as error:
        service.download_whatsapp_attachment("../private", "image")
    assert error.value.status_code == 415
