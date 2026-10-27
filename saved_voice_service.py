"""Private persistent recordings and durable manual voice sends."""
import hashlib
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from audio_service import MAX_AUDIO_BYTES
from models import SavedVoice, SavedVoiceSend, WhatsAppConversationMessage
from quick_message_service import authorize, require_reply_window
from settings import settings


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    transcript: str = Field(min_length=1, max_length=2000)
    language: str = Field(pattern=r"^(si|singlish|en)$")


class Edit(Metadata):
    active: bool
    revision: int = Field(ge=1)


class Send(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    voice_id: int = Field(ge=1)
    revision: int = Field(ge=1)


def metadata(values):
    values = Metadata.model_validate(values).model_dump()
    for key in ("title", "category", "transcript"):
        values[key] = values[key].strip()
        if not values[key] or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", values[key]):
            raise ValueError("Use a title, category and written transcript without control characters")
    return values


def public(row):
    return {key: getattr(row, key) for key in ("id", "title", "category", "transcript", "language", "active",
        "revision", "duration_seconds", "size_bytes")}


def storage_path(name):
    if not re.fullmatch(r"[a-f0-9]{32}\.ogg", name or ""):
        raise HTTPException(503, "The saved recording is unavailable")
    return Path(settings.SAVED_VOICES_DIR).resolve() / name


def write_audio(raw):
    name = uuid4().hex + ".ogg"
    path = storage_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as file:
            os.chmod(path, 0o600)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        try: path.unlink(missing_ok=True)
        except OSError: pass
        raise HTTPException(503, "Voice storage is not writable. Check the app's persistent volume mount.") from None
    return name


def read_audio(row):
    path = storage_path(row.storage_name)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            raw = file.read(MAX_AUDIO_BYTES + 1)
    except OSError:
        raise HTTPException(503, "The saved recording is missing from persistent storage") from None
    if len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
        raise HTTPException(503, "The saved recording failed its integrity check")
    return raw


def status(row):
    state = row.status
    if state in {"UPLOADING", "SENDING"} and datetime.utcnow() - row.updated_at > timedelta(minutes=2):
        state = "UNKNOWN"  # Read-only presentation after a crash; never replay the claim.
    return {"id": row.id, "status": state, "voice_id": row.voice_id, "revision": row.voice_revision,
            "ok": row.status == "SENT"}


def scoped_attempt(db, key, cid, user):
    row = db.query(SavedVoiceSend).filter_by(idempotency_key=key).one_or_none()
    if row and (row.conversation_id != cid or row.staff_id != user.id):
        raise HTTPException(409, "This send key belongs to another attempt")
    return row


def send(db, user, conversation, values, key, upload, deliver):
    authorize(user)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", key):
        raise HTTPException(400, "A valid Idempotency-Key is required")
    existing = scoped_attempt(db, key, conversation.id, user)
    if existing:
        if (existing.voice_id, existing.voice_revision) != (values.voice_id, values.revision):
            raise HTTPException(409, "This send key has different recording details")
        return status(existing)
    require_reply_window(db, conversation)
    voice = db.query(SavedVoice).filter_by(id=values.voice_id).one_or_none()
    if voice is None or not voice.active:
        raise HTTPException(404, "This recording is unavailable")
    if voice.revision != values.revision:
        raise HTTPException(409, "This recording changed. Refresh and review it before sending")
    raw = read_audio(voice)
    title, transcript, duration = voice.title, voice.transcript, voice.duration_seconds
    attempt = SavedVoiceSend(idempotency_key=key, conversation_id=conversation.id, staff_id=user.id,
        voice_id=voice.id, voice_revision=voice.revision, status="UPLOADING")
    db.add(attempt)
    try:
        db.commit()  # Claim before any external I/O; retries can only read status.
    except IntegrityError:
        db.rollback()
        row = scoped_attempt(db, key, conversation.id, user)
        if row and (row.voice_id, row.voice_revision) == (values.voice_id, values.revision):
            return status(row)
        raise HTTPException(409, "Voice send already claimed") from None
    try:
        media_id = upload(raw)
    except Exception:
        media_id = None
    if not isinstance(media_id, str) or not media_id.strip():
        attempt.status = "FAILED"  # Media upload alone cannot message a customer.
        db.commit()
        return status(attempt)
    db.expire_all()
    db.refresh(conversation)
    db.refresh(voice)
    try:
        require_reply_window(db, conversation)
    except HTTPException:
        attempt.status = "FAILED"; db.commit(); return status(attempt)
    if not voice.active or voice.revision != values.revision:
        attempt.status = "FAILED"; db.commit(); return status(attempt)
    attempt.media_id, attempt.status = media_id, "SENDING"
    db.commit()  # Ambiguous crash after this point must never be replayed.
    try:
        outcome, message_id = deliver(conversation.phone_number, media_id)
    except Exception:
        outcome, message_id = "UNKNOWN", None
    if outcome != "SENT" or not isinstance(message_id, str) or not message_id.strip():
        attempt.status = "FAILED" if outcome == "FAILED" else "UNKNOWN"
        db.commit()
        return status(attempt)
    message = WhatsAppConversationMessage(phone_number=conversation.phone_number, direction="outbound",
        message_text=f"{title}\n{transcript}", whatsapp_message_id=message_id, response_kind="staff_saved_voice",
        send_status="sent", sent_by=user.username, media_id=media_id, media_mime_type="audio/ogg",
        media_duration_seconds=duration)
    db.add(message); db.flush()
    attempt.status, attempt.whatsapp_message_id, attempt.message_id = "SENT", message_id, message.id
    conversation.last_message_at = datetime.utcnow()
    db.commit()
    return status(attempt)
