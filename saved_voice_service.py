"""Private persistent recordings and durable manual voice sends."""
import voice_trace
import hashlib
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_

from audio_service import (MAX_AUDIO_BYTES, NORMALIZATION_VERSION, VoiceAudioError,
    normalize_voice_audio, validate_voice_audio, source_audio_info)
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
    value = {key: getattr(row, key) for key in ("id", "title", "category", "transcript", "language", "active",
        "revision", "duration_seconds", "size_bytes", "processing_status", "normalization_error", "audio_diagnostics")}
    value["validation_current"] = bool(row.processing_status == "READY" and row.normalization_version == NORMALIZATION_VERSION)
    value["ready_to_send"] = value["validation_current"] and bool(row.active)
    return value


def storage_path(name):
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:ogg|source)", name or ""):
        raise HTTPException(503, "The saved recording is unavailable")
    return Path(settings.SAVED_VOICES_DIR).resolve() / name


def write_audio(raw, *, original=False):
    name = uuid4().hex + (".source" if original else ".ogg")
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


def _read_file(name):
    path = storage_path(name)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            raw = file.read(MAX_AUDIO_BYTES + 1)
    except OSError:
        raise HTTPException(503, "The saved recording is missing from persistent storage") from None
    return raw


def _read_original(row):
    raw = _read_file(row.storage_name)
    if len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
        raise HTTPException(503, "The saved recording failed its integrity check")
    return raw


def read_audio(row):
    raw = _read_original(row)
    try:
        validate_voice_audio(raw)
    except VoiceAudioError as exc:
        raise HTTPException(422, str(exc)) from None
    return raw


def prepare(db, row, *, retry=False):
    """Validate ready files, or claim and normalize a legacy file exactly once.

    Originals are retained permanently. A fencing token prevents a late worker
    overwriting a subsequent prepare after a crash/expired two-minute lease.
    """
    if row.normalization_version == NORMALIZATION_VERSION and row.processing_status == "READY":
        try:
            read_audio(row)
        except HTTPException as exc:
            row.processing_status = "FAILED"
            row.normalization_error = "Stored audio failed validation. Prepare again or upload a replacement."
            db.commit()
            raise exc
        return row
    from audio_service import ffmpeg_encoder_enabled
    if ffmpeg_encoder_enabled():
        raise HTTPException(409,"The FFmpeg rollout does not rewrite historical recordings. Upload a new recording; retained originals are unchanged.")
    if row.processing_status == "FAILED" and not retry:
        raise HTTPException(422, row.normalization_error or "Conversion failed. Prepare the recording before sending.")
    now=datetime.utcnow(); token=uuid4().hex; voice_id=row.id
    changed=db.query(SavedVoice).filter(SavedVoice.id==voice_id, SavedVoice.revision==row.revision,
        SavedVoice.normalization_version==row.normalization_version,
        or_(SavedVoice.processing_status != "PROCESSING", SavedVoice.normalization_started_at < now-timedelta(minutes=2)))        .update({"processing_status":"PROCESSING","normalization_token":token,
                 "normalization_started_at":now,"normalization_error":None},synchronize_session=False)
    if not changed:
        db.rollback();raise HTTPException(409,"Recording is processing. Refresh its status before sending.")
    db.commit();db.refresh(row)
    try:
        raw=_read_file(row.original_storage_name) if retry and row.original_storage_name else _read_original(row)
        expected_source=(row.audio_diagnostics or {}).get("source",{}).get("sha256") if retry and row.original_storage_name else None
        if expected_source and hashlib.sha256(raw).hexdigest() != expected_source:
            raise HTTPException(503,"The retained source failed its integrity check")
        source=source_audio_info(raw)
        source["sha256"]=hashlib.sha256(raw).hexdigest()
        normalized,duration=normalize_voice_audio(raw,"application/octet-stream")
        output=validate_voice_audio(normalized)
        stored=write_audio(normalized)
        original=row.original_storage_name or row.storage_name
        changed=db.query(SavedVoice).filter_by(id=voice_id,normalization_token=token).update({
            "original_storage_name":original,"storage_name":stored,"sha256":hashlib.sha256(normalized).hexdigest(),
            "size_bytes":len(normalized),"duration_seconds":duration,"normalization_version":NORMALIZATION_VERSION,
            "processing_status":"READY","normalization_error":None,"normalization_token":None,
            "normalization_started_at":None,"audio_diagnostics":{"source":source,"normalized":output},
            "revision":SavedVoice.revision+1},synchronize_session=False)
        if not changed:db.rollback();raise HTTPException(409,"Another worker prepared this recording. Refresh before sending.")
        db.commit();db.refresh(row);return row
    except (VoiceAudioError,HTTPException) as exc:
        error=str(exc) if isinstance(exc,VoiceAudioError) else "Stored recording is unavailable or failed integrity validation. Upload a replacement."
        db.query(SavedVoice).filter_by(id=voice_id,normalization_token=token).update({
            "processing_status":"FAILED","normalization_error":error[:200],"normalization_token":None,
            "normalization_started_at":None},synchronize_session=False)
        db.commit()
        if isinstance(exc,HTTPException):raise
        raise HTTPException(422,error) from None


def status(row):
    state = row.status
    if state in {"UPLOADING", "SENDING"} and datetime.utcnow() - row.updated_at > timedelta(minutes=2):
        state = "UNKNOWN"  # Read-only presentation after a crash; never replay the claim.
    return {"id": row.id, "status": state, "voice_id": row.voice_id, "revision": row.voice_revision,
            "ok": row.status == "SENT", "delivery_status":row.delivery_status, "delivery_error_code":row.delivery_error_code}


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
    prepare(db, voice)
    if voice.revision != values.revision:
        raise HTTPException(409, "Recording was normalized. Refresh, preview it, and choose it again before sending")
    raw = read_audio(voice)
    from audio_service import ffmpeg_encoder_enabled
    staff_variant_c=ffmpeg_encoder_enabled()
    if staff_variant_c:
        # read_audio already validates stored OGG and its fingerprint. Re-reading
        # the upload (often seek-dependent M4A) unnecessarily breaks a READY item.
        source_raw=raw
        try:raw,converted_duration=normalize_voice_audio(source_raw)
        except VoiceAudioError as exc:raise HTTPException(422,str(exc)) from None
    title, transcript, duration = voice.title, voice.transcript, voice.duration_seconds
    if staff_variant_c:duration=converted_duration
    trace=voice_trace.new("saved_voice")
    trace["recording_id"]=voice.id
    trace["recording_revision"]=voice.revision
    trace["normalization_version"]=voice.normalization_version
    trace["stages"]["storage_read"]=voice_trace.fingerprint(raw)
    trace["stages"]["normalized_record"]={"sha256":voice.sha256,"size_bytes":voice.size_bytes}
    if staff_variant_c:
        trace['staff_audio_profile']='variant_c_ordinary'
        trace['library_record_unchanged']=True
        trace['stages']['normalized_record']=voice_trace.fingerprint(raw)
    source=(voice.audio_diagnostics or {}).get("source",{})
    trace["stages"]["source_at_upload"]={k:source.get(k) for k in ("sha256","size_bytes","container","codec")}
    attempt = SavedVoiceSend(idempotency_key=key, conversation_id=conversation.id, staff_id=user.id,
        voice_id=voice.id, voice_revision=voice.revision, status="UPLOADING",audio_trace=voice_trace.snapshot(trace))
    db.add(attempt)
    try:
        db.commit()  # Claim before any external I/O; retries can only read status.
    except IntegrityError:
        db.rollback()
        row = scoped_attempt(db, key, conversation.id, user)
        if row and (row.voice_id, row.voice_revision) == (values.voice_id, values.revision):
            return status(row)
        raise HTTPException(409, "Voice send already claimed") from None
    trace["attempt_id"]=attempt.id
    try:
        if staff_variant_c:
            name=write_audio(raw)
            trace['staff_output_snapshot']={'storage_name':name,**voice_trace.fingerprint(raw)}
            attempt.audio_trace=voice_trace.snapshot(trace);db.commit()
        with voice_trace.capture(trace):
            media_id = upload(raw)
    except Exception:
        media_id = None
    trace["returned_media_id"]=media_id if isinstance(media_id,str) else None
    attempt.audio_trace=voice_trace.snapshot(trace)
    if not isinstance(media_id, str) or not media_id.strip():
        attempt.status = "FAILED"  # Media upload alone cannot message a customer.
        db.commit()
        return status(attempt)
    db.expire_all()
    db.refresh(conversation)
    db.refresh(voice)
    attempt.audio_trace=voice_trace.snapshot(trace)
    attempt.media_id=media_id
    try:
        require_reply_window(db, conversation)
    except HTTPException:
        attempt.status = "FAILED"; db.commit(); return status(attempt)
    if not voice.active or voice.revision != values.revision:
        attempt.status = "FAILED"; db.commit(); return status(attempt)
    attempt.media_id, attempt.status = media_id, "SENDING"
    db.commit()  # Ambiguous crash after this point must never be replayed.
    try:
        with voice_trace.capture(trace):
            outcome, message_id = deliver(conversation.phone_number, media_id)
    except Exception:
        outcome, message_id = "UNKNOWN", None
    trace["returned_whatsapp_message_id"]=message_id if isinstance(message_id,str) else None
    attempt.audio_trace=voice_trace.snapshot(trace)
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
