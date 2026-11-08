"""Pinned payment explanation, private bank image and unverified slip handoff."""
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import os
import re
import secrets
import threading
from uuid import uuid4
from fastapi import HTTPException
from models import CustomerQuestionProductSend as Item, CustomerQuestionRun as Run
from models import WhatsAppConversationMessage as Message, WhatsAppMessageAttachment as Attachment
from settings import settings
import outbound_media_service as media

image_slots = threading.BoundedSemaphore(2)
VOICE_POSITION = -200
IMAGE_POSITION = -201
DEFAULT_CAPTION = 'බැංකු ගිණුම් විස්තර මෙහි ඇත. මුදල් තැන්පත් කළ පසු payment slip එකේ පැහැදිලි ඡායාරූපයක් හෝ PDF එකක් මේ WhatsApp සංවාදයට එවන්න. අපගේ කාර්ය මණ්ඩලය ගෙවීම තහවුරු කරනු ඇත.'


def image_path(reference):
    if not isinstance(reference, str) or not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg)', reference):
        raise HTTPException(422, 'Choose an uploaded bank-details PNG or JPEG image')
    return Path(settings.SAVED_VOICES_DIR).resolve() / 'payment-images' / reference


def read_image(reference):
    path = image_path(reference)
    try:
        if path.is_symlink():
            raise OSError()
        with path.open('rb') as file:
            raw = file.read(media.size_limit('image') + 1)
    except OSError:
        raise HTTPException(409, 'Bank-details image is unavailable. Upload and select it again.') from None
    if not raw or len(raw) > media.size_limit('image') or hashlib.sha256(raw).hexdigest() != reference.split('.')[0]:
        raise HTTPException(409, 'Bank-details image failed integrity validation')
    return raw, 'image/png' if reference.endswith('.png') else 'image/jpeg'


def store_image(raw, mime):
    if mime not in {'image/png', 'image/jpeg'}:
        raise HTTPException(415, 'Upload a PNG or JPEG bank-details image')
    extension = '.png' if mime == 'image/png' else '.jpg'
    # Reuse the isolated, bounded staff attachment validator; never render publicly.
    media.validate_file(raw, 'bank-details' + extension, '')
    reference = hashlib.sha256(raw).hexdigest() + extension
    path = image_path(reference)
    temporary = path.with_name(uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with temporary.open('xb') as file:
            os.chmod(temporary, 0o600)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError:
        raise HTTPException(503, 'Bank-details image storage is unavailable') from None
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
    return reference


def validate(value):
    if not isinstance(value, dict) or not {'voice_id', 'revision', 'image_ref', 'caption'} <= set(value) or set(value) - {'voice_id', 'revision', 'voice_title', 'image_ref', 'caption'}:
        raise HTTPException(422, 'Choose a payment Saved Voice, bank-details image and caption')
    if any(type(value[key]) is not int or not 1 <= value[key] <= 2147483647 for key in ['voice_id', 'revision']):
        raise HTTPException(422, 'Select the payment recording and its current revision')
    if 'voice_title' in value and (not isinstance(value['voice_title'], str) or len(value['voice_title']) > 100):
        raise HTTPException(422, 'Invalid payment recording title')
    image_path(value['image_ref'])
    if not isinstance(value['caption'], str) or not 1 <= len(value['caption'].strip()) <= 900 or any(ord(c) < 32 and c not in '\n\r\t' for c in value['caption']):
        raise HTTPException(422, 'Payment image caption must be 1–900 characters')


def verify(db, definition):
    from question_greasing_service import recording
    for step in definition['steps']:
        payment = step.get('service', {}).get('payment')
        if payment:
            voice = recording(db, payment)
            payment['voice_title'] = voice.title
            read_image(payment['image_ref'])


def selected(run):
    if run.step >= len(run.definition['steps']):
        return None
    step = run.definition['steps'][run.step]
    answer = run.answers.get(step['key'])
    return answer if step.get('service', {}).get('payment') and isinstance(answer, dict) and answer.get('delivery_details', {}).get('status') == 'CONFIRMED' else None


def pending(run):
    answer = selected(run)
    return bool(answer and answer.get('payment', {}).get('status') in {'VOICE_QUEUED', 'IMAGE_QUEUED', 'AWAITING_SLIP'})


def save(run, state):
    key = run.definition['steps'][run.step]['key']
    run.answers = {**run.answers, key: {**run.answers[key], 'payment': state}}


def begin(run):
    save(run, {'status': 'VOICE_QUEUED'})
    run.status = 'QUEUED'
    run.provider_id = None
    run.token = secrets.token_urlsafe(24)


def claim(db, run):
    position = VOICE_POSITION if selected(run)['payment']['status'] == 'VOICE_QUEUED' else IMAGE_POSITION
    item = db.query(Item).filter_by(run_id=run.id, step=run.step, position=position).one_or_none()
    if item is None:
        db.add(Item(run_id=run.id, step=run.step, position=position, status='SENDING'))
        return True
    if item.status == 'QUEUED':
        item.status = 'SENDING'
        return True
    run.status = 'UNCERTAIN'
    run.version += 1
    return False


def fail(db, run, item, reason):
    from customer_questions import audit
    item.status = 'UNCERTAIN'
    item.reason = reason
    run.status = 'UNCERTAIN'
    run.version += 1
    run.updated_at = datetime.utcnow()
    audit(db, run, 'PAYMENT_FOLLOWUP_REVIEW', 'System', stage=item.position, reason=reason)


def send_claimed(db, run, conversation, item):
    from customer_questions import audit
    from question_greasing_service import send_explanation
    from quick_message_service import require_reply_window
    config = run.definition['steps'][run.step]['service']['payment']
    if item.position == VOICE_POSITION:
        # Verify both configured assets before beginning this follow-up.
        try:
            read_image(config['image_ref'])
        except HTTPException:
            fail(db, run, item, 'Bank image unavailable or changed. Payment follow-up requires staff review.')
            return
        send_explanation(db, run, conversation, item, config=config, source='payment_voice', event='PAYMENT_VOICE_SENT')
        if item.status == 'SENT':
            save(run, {'status': 'IMAGE_QUEUED'})
        return
    explanation = db.query(Item).filter_by(run_id=run.id, step=run.step, position=VOICE_POSITION, status='SENT').one_or_none()
    voice = db.query(Message).filter_by(phone_number=conversation.phone_number, whatsapp_message_id=explanation.provider_id, response_kind='payment_voice').one_or_none() if explanation else None
    if voice is None or voice.send_status == 'failed':
        fail(db, run, item, 'Payment voice was not accepted or failed delivery. Bank image was not sent.')
        return
    try:
        raw, mime = read_image(config['image_ref'])
        data = {'filename': 'bank-details.png' if mime == 'image/png' else 'bank-details.jpg',
            'media_type': 'image', 'mime_type': mime, 'caption': config['caption'], 'file_size_bytes': len(raw)}
        media_id = media.upload_media(raw, data)
        if not isinstance(media_id,str) or not 1<=len(media_id)<=255:
            raise ValueError('Upload did not return a media ID')
        db.refresh(conversation)
        require_reply_window(db, conversation)
        outcome, provider = media.send_media(conversation.phone_number, media_id, data)
    except Exception:
        outcome, provider = 'OUTCOME_UNCERTAIN', None
    if outcome != 'SENT' or not isinstance(provider, str) or not 1 <= len(provider) <= 255:
        fail(db, run, item, 'Bank image upload/send was not confirmed. Check chat; no automatic retry.')
        return
    item.status = 'SENT'
    item.provider_id = provider
    run.status = 'WAITING'
    run.provider_id = provider
    run.updated_at = datetime.utcnow()
    run.version += 1
    save(run, {'status': 'AWAITING_SLIP', 'instructions_sent_at': run.updated_at.isoformat() + 'Z'})
    message = Message(phone_number=conversation.phone_number, direction='outbound', message_text=config['caption'],
        whatsapp_message_id=provider, response_kind='payment_image', send_status='sent', analytics_actor_kind='automatic')
    db.add(message)
    db.flush()
    db.add(Attachment(message_id=message.id, media_type='image', media_id=media_id, mime_type=mime,
        filename=data['filename'], caption=data['caption'], file_size_bytes=len(raw)))
    conversation.last_message_at = max(conversation.last_message_at or run.updated_at, run.updated_at)
    audit(db, run, 'BANK_IMAGE_SENT', 'System', provider_id=provider)


def inbound(db, conversation, message, attachment, *, verified):
    """Link a submitted image/PDF for human review; never assert payment success."""
    from customer_questions import config, latest, audit
    control = config(db, True, optional=True)
    if control is None:
        return False
    run = latest(db, conversation.id, True)
    if not run or run.status in {'STOPPED', 'COMPLETE'} or not pending(run):
        return False
    # Active payment messages stay private to this handoff, even when paused/OFF.
    if not verified or not control.enabled or run.status != 'WAITING' or selected(run)['payment']['status'] != 'AWAITING_SLIP':
        return True
    image = db.query(Item).filter_by(run_id=run.id, step=run.step, position=IMAGE_POSITION, status='SENT').one_or_none()
    if image is None or image.provider_id != run.provider_id or message.created_at < image.created_at.replace(microsecond=0) or message.created_at > datetime.utcnow() + timedelta(minutes=5):
        return True
    if message.reply_to_whatsapp_message_id and message.reply_to_whatsapp_message_id != image.provider_id:
        return True
    if not attachment or not (attachment.media_type == 'image' or attachment.media_type == 'document' and attachment.mime_type == 'application/pdf'):
        return True
    state = dict(selected(run)['payment'])
    state.update(status='SLIP_REVIEW', receipt_message_id=message.id, receipt_attachment_id=attachment.id,
        receipt_received_at=message.created_at.isoformat() + 'Z')
    save(run, state)
    run.status = 'COMPLETE'
    run.step = len(run.definition['steps'])
    run.updated_at = datetime.utcnow()
    run.version += 1
    audit(db, run, 'PAYMENT_SLIP_SUBMITTED', 'Customer', message_id=message.id, attachment_id=attachment.id)
    audit(db, run, 'COMPLETE', 'System', note='Attachment submitted for staff payment verification. This is not proof of payment; Paid status is unchanged.')
    return True


def image_for_message(db, conversation_id, provider):
    item = db.query(Item).join(Run, Run.id == Item.run_id).filter(Run.conversation_id == conversation_id,
        Item.position == IMAGE_POSITION, Item.provider_id == provider, Item.status == 'SENT').one_or_none()
    if item is None:
        raise HTTPException(404, 'Bank image is unavailable')
    run = db.get(Run, item.run_id)
    return read_image(run.definition['steps'][item.step]['service']['payment']['image_ref'])
