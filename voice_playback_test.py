"""Explicit admin actions only. No scheduler, AI entry point, retries or live test hook."""
from datetime import datetime, timedelta
import hashlib
import hmac
import os
import re
from uuid import uuid4

from fastapi import HTTPException
from models import SavedVoice, VoicePlaybackTest, VoicePlaybackVariant, WhatsAppConversation
from audio_service import NORMALIZATION_VERSION, validate_voice_audio, VoiceAudioError
from saved_voice_service import read_audio, write_audio, _read_file
from quick_message_service import require_reply_window
import voice_trace
import whatsapp_service


def configuration():
    # Dedicated deployment opt-in and owner-provisioned destination. Never a
    # customer selector, recent conversation, or number embedded in source.
    recipient = os.getenv('VOICE_PLAYBACK_TEST_OWNER_NUMBER', '')
    enabled = os.getenv('VOICE_PLAYBACK_TEST_ENABLED', '').lower() == 'true'
    if not enabled or not re.fullmatch(r'[1-9][0-9]{7,14}', recipient):
        raise HTTPException(409, 'Owner playback tests are disabled or the verified owner number is not configured')
    from settings import settings
    if not settings.META_WHATSAPP_PHONE_NUMBER_ID or not settings.META_WHATSAPP_ACCESS_TOKEN:
        raise HTTPException(409, "WhatsApp business configuration is unavailable")
    return recipient


def binding(recipient):
    from settings import settings
    return hashlib.sha256((recipient + '|' + settings.META_WHATSAPP_PHONE_NUMBER_ID + '|' + settings.META_GRAPH_API_VERSION).encode()).hexdigest()


def authorize(user):
    if user.role != 'ADMIN':
        raise HTTPException(403, 'Administrator access required')


def window(db, recipient):
    conversation = db.query(WhatsAppConversation).filter_by(phone_number=recipient).one_or_none()
    if conversation is None:
        raise HTTPException(409, 'The configured owner must first message this business number')
    require_reply_window(db, conversation)


def prepare(db, user, voice_id, revision, typed_recipient, owner_confirm):
    authorize(user)
    recipient = configuration()
    if not owner_confirm or not re.fullmatch(r'[1-9][0-9]{7,14}', typed_recipient) or not hmac.compare_digest(recipient, typed_recipient):
        raise HTTPException(400, 'Verify the configured owner-controlled number and authorization')
    window(db, recipient)
    voice = db.get(SavedVoice, voice_id)
    if not voice or not voice.active or voice.revision != revision or voice.processing_status != 'READY' or voice.normalization_version != NORMALIZATION_VERSION:
        raise HTTPException(409, 'Select a current ready recording from Saved Voices')
    raw = read_audio(voice)  # Existing normalized bytes, never re-encode between variants.
    from voice_compatibility import inspect
    info = inspect(raw)
    source = _read_file(voice.original_storage_name) if voice.original_storage_name else raw
    trace = voice_trace.new('owner_playback_ab')
    trace.update(source=voice_trace.fingerprint(source), normalized=voice_trace.fingerprint(raw), audio=info,
                 normalization_version=NORMALIZATION_VERSION, mime_type='audio/ogg',
                 source_origin='retained_original' if voice.original_storage_name else 'normalized_file_only_original_unavailable')
    run = VoicePlaybackTest(id=uuid4().hex, staff_id=user.id, voice_id=voice.id, voice_revision=revision,
        storage_name=write_audio(raw), recipient_hash=binding(recipient), status='REVIEW', evidence=trace)
    db.add(run)
    db.flush()
    for variant in ('A', 'B'):
        db.add(VoicePlaybackVariant(run_id=run.id, variant=variant, status='NOT_STARTED'))
    db.commit()
    return run


def get_run(db, user, run_id):
    authorize(user)
    run = db.get(VoicePlaybackTest, run_id)
    if not run or run.staff_id != user.id:
        raise HTTPException(404, 'Owner test not found for this administrator')
    return run


def execute(db, user, run_id, confirmed):
    run = get_run(db, user, run_id)
    recipient = configuration()
    if not confirmed:
        raise HTTPException(400, 'Explicit confirmation of two WhatsApp messages is required')
    if not hmac.compare_digest(run.recipient_hash, binding(recipient)):
        raise HTTPException(409, 'Owner destination or business configuration changed; prepare a new review')
    if run.status != 'REVIEW':
        return run  # Repeated submissions never repeat network I/O.
    if datetime.utcnow() - run.created_at > timedelta(minutes=15):
        raise HTTPException(409, 'Review expired; prepare a new test')
    window(db, recipient)
    raw = _read_file(run.storage_name)
    try:
        validate_voice_audio(raw)
    except VoiceAudioError:
        raise HTTPException(422, "Reviewed recording failed validation; nothing was sent") from None
    if voice_trace.fingerprint(raw) != run.evidence['normalized']:
        raise HTTPException(409, 'Reviewed audio integrity check failed; nothing was sent')
    changed = db.query(VoicePlaybackTest).filter_by(id=run.id, status='REVIEW').update(
        {'status':'RUNNING', 'confirmed_at':datetime.utcnow()}, synchronize_session=False)
    db.commit()  # Atomic cross-worker claim BEFORE any Meta I/O. Never reclaim.
    db.refresh(run)
    if not changed:
        return run
    for name in ('A', 'B'):
        variant = db.query(VoicePlaybackVariant).filter_by(run_id=run.id, variant=name).one()
        trace = voice_trace.new('owner_playback_ab_' + name)
        trace.update(run_id=run.id, recording_id=run.voice_id, recording_revision=run.voice_revision)
        trace['stages']['immutable_snapshot'] = voice_trace.fingerprint(raw)
        variant.status = 'UPLOADING'; variant.audio_trace = voice_trace.snapshot(trace); db.commit()
        try:
            # Separate uploads avoid assuming cross-mode media reuse support;
            # the one immutable byte object is passed to both upload calls.
            with voice_trace.capture(trace):
                if binding(configuration()) != run.recipient_hash:
                    raise HTTPException(409, "Test configuration changed")
                window(db, recipient)
                media_id = whatsapp_service.upload_whatsapp_audio(raw)
                variant.media_id = media_id
                variant.audio_trace = voice_trace.snapshot(trace)
                if not media_id:
                    variant.status = 'UPLOAD_FAILED'; db.commit(); break
                variant.status = 'SENDING'; db.commit()  # Durable ambiguous-send fence.
                if binding(configuration()) != run.recipient_hash:
                    raise HTTPException(409, "Test configuration changed")
                window(db, recipient)
                outcome, message_id = whatsapp_service.send_whatsapp_audio(recipient, media_id, voice=name == 'B', return_outcome=True)
                variant.status = outcome
                variant.whatsapp_message_id = message_id
                variant.audio_trace = voice_trace.snapshot(trace)
                db.commit()
                if outcome != 'SENT':
                    break  # Partial/ambiguous results never silently retry or continue.
        except Exception:
            db.rollback()
            # No exception text: it can contain private request data. A crash
            # before persistence remains RUNNING/SENDING, never eligible again.
            db.refresh(variant)
            variant.status = 'UNKNOWN'
            variant.audio_trace = voice_trace.snapshot(trace)
            db.commit()
            break
    statuses = [v.status for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id).order_by(VoicePlaybackVariant.variant)]
    run.status = 'SUBMITTED' if statuses == ['SENT', 'SENT'] else 'INCOMPLETE'
    db.commit()
    return run


def results(db, user, run_id, a, b, platform, description):
    run = get_run(db, user, run_id)
    if run.status == 'REVIEW':
        raise HTTPException(409, 'Run the reviewed test before recording playback')
    if a not in {'plays', 'fails', 'not_tested'} or b not in {'plays', 'fails', 'not_tested'} or platform not in {'Android', 'iPhone', 'Web/Desktop', 'Other'}:
        raise HTTPException(422, 'Choose valid playback results and platform')
    # Arbitrary descriptions may contain phones/secrets. Store only a safe
    # selected error category, never free-form private diagnostic text.
    if description not in {'', 'audio_unavailable', 'other_error'}:
        raise HTTPException(422, 'Choose an error description category')
    variants = {v.variant:v for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id)}
    if any(value != 'not_tested' and variants[name].status != 'SENT' for name,value in [('A',a),('B',b)]):
        raise HTTPException(409, 'Only accepted messages can have a phone playback result')
    run.playback = {'A':a, 'B':b, 'platform':platform, 'error':description}
    run.results_by = user.id; run.results_at = datetime.utcnow(); db.commit()
    return run


def summary(playback):
    pair = ((playback or {}).get('A'), (playback or {}).get('B'))
    return {
        ('plays','fails'):'A: Investigate voice-note-specific encoding, metadata and semantics.',
        ('fails','fails'):'B: Investigate shared encoding, media compatibility and recipient client.',
        ('plays','plays'):'C: Compare the existing production paths with this diagnostic path.',
        ('fails','plays'):'D: Investigate ordinary audio compatibility and payload requirements.',
    }.get(pair, 'Awaiting both actual phone playback results.') + ' This is diagnostic guidance, not proof of root cause.'
