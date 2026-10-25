"""Staff-only media validation and durable upload/send orchestration.

No customer send is retried automatically after a durable SENDING claim.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from image_understanding_service import private_image_processing
from models import WhatsAppMediaSend, WhatsAppConversationMessage, WhatsAppMessageAttachment
from settings import settings

FORMATS = {
    '.jpg': ('image', 'image/jpeg'), '.jpeg': ('image', 'image/jpeg'),
    '.png': ('image', 'image/png'), '.pdf': ('document', 'application/pdf'),
    '.mp4': ('video', 'video/mp4'),
    '.doc': ('document', 'application/msword'),
    '.docx': ('document', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    '.xls': ('document', 'application/vnd.ms-excel'),
    '.xlsx': ('document', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '.txt': ('document', 'text/plain'),
}
STAFF_ROLES = {'ADMIN', 'SALES_MANAGER', 'SALES_AGENT', 'STAFF'}
logger = logging.getLogger(__name__)


class ValidationUnavailable(Exception):
    """The local validator could not start; no upload or customer send occurred."""


def _validator_environment():
    # Nixpacks provides PyAV's C++ runtime via this loader path. Keep only that
    # trusted deployment setting, never application credentials or Python hooks.
    environment = {'PATH': '/usr/bin:/bin'}
    if os.environ.get('LD_LIBRARY_PATH'):
        environment['LD_LIBRARY_PATH'] = os.environ['LD_LIBRARY_PATH']
    return environment


def authorize(user):
    if (user.role or 'STAFF') not in STAFF_ROLES:
        raise HTTPException(403, 'You are not authorized to send attachments')


def policy():
    return {'formats': {extension: {'type': kind, 'mime': mime, 'max_bytes': size_limit(kind)}
                        for extension, (kind, mime) in FORMATS.items()}, 'caption_max_length': 1024}


def size_limit(kind):
    if kind == 'image':
        return min(settings.WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES, 5_000_000)
    if kind == 'video':
        return min(settings.WHATSAPP_OUTBOUND_VIDEO_MAX_BYTES, 16_000_000)
    return min(settings.WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES, 16_000_000)


def safe_filename(filename):
    name = re.split(r'[/\\]', filename or '')[-1]
    name = ''.join(c for c in name if c.isprintable() and c not in '<>:"|?*' and not 0x202a <= ord(c) <= 0x202e and not 0x2066 <= ord(c) <= 0x2069).strip(' .')
    if not name or len(name) > 200:
        raise HTTPException(422, 'Use a filename between 1 and 200 characters')
    return name


def validate_file(raw, filename, caption):
    filename = safe_filename(filename)
    extension = Path(filename).suffix.lower()
    if extension not in FORMATS:
        raise HTTPException(422, 'Unsupported attachment format')
    kind, mime = FORMATS[extension]
    if not raw or len(raw) > size_limit(kind):
        raise HTTPException(413, 'Attachment exceeds the configured size limit or is empty')
    if not isinstance(caption, str) or len(caption) > 1024 or '\x00' in caption:
        raise HTTPException(422, 'Caption must contain at most 1024 characters')
    try:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('outbound_media_validation.py'))],
            input=extension.encode('ascii') + b'\n' + raw, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=True, env=_validator_environment())
        metadata = json.loads(result.stdout)
        if not isinstance(metadata, dict) or set(metadata) - {"duration_seconds"}:
            raise ValueError()
    except subprocess.CalledProcessError as error:
        if error.returncode == 70:
            logger.warning('Outbound attachment validator unavailable: runtime dependency')
            raise ValidationUnavailable() from None
        raise HTTPException(422, 'The attachment is corrupt, unsupported, encrypted or exceeds processing limits') from None
    except OSError:
        logger.warning('Outbound attachment validator unavailable: process launch')
        raise ValidationUnavailable() from None
    except (subprocess.SubprocessError, ValueError):
        raise HTTPException(422, 'The attachment is corrupt, unsupported, encrypted or exceeds processing limits') from None
    return {'filename': filename, 'media_type': kind, 'mime_type': mime, 'caption': caption.strip(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'file_size_bytes': len(raw), **metadata}


def _meta_request(route, **kwargs):
    """Fixed trusted Graph route, bounded response, no redirects or upstream diagnostics."""
    token, phone = settings.META_WHATSAPP_ACCESS_TOKEN, settings.META_WHATSAPP_PHONE_NUMBER_ID
    version = settings.META_GRAPH_API_VERSION
    if not token or not phone or not re.fullmatch(r'\d+', phone) or not re.fullmatch(r'v\d+\.\d+', version):
        return None, None
    with private_image_processing():
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=5, pool=5), follow_redirects=False) as client:
                with client.stream('POST', f'https://graph.facebook.com/{version}/{phone}/{route}',
                                   headers={'Authorization': f'Bearer {token}'}, **kwargs) as response:
                    deadline = time.monotonic() + 35
                    body = bytearray()
                    for chunk in response.iter_raw():
                        if time.monotonic() > deadline:
                            return None, None
                        body.extend(chunk)
                        if len(body) > 65536:
                            return response.status_code, None
                    try:
                        payload = json.loads(body)
                        return response.status_code, payload if isinstance(payload, dict) else None
                    except (ValueError, UnicodeError):
                        return response.status_code, None
        except Exception:
            # Network errors can contain URLs, authorization headers or uploaded contents.
            return None, None


def upload_media(raw, data):
    status, payload = _meta_request('media', data={'messaging_product': 'whatsapp', 'type': data['mime_type']},
        files={'file': (data['filename'], raw, data['mime_type'])})
    media_id = payload.get('id') if payload else None
    return media_id if status and 200 <= status < 300 and isinstance(media_id, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,255}', media_id) else None


def send_media(phone, media_id, data):
    content = {'id': media_id}
    if data['caption']:
        content['caption'] = data['caption']
    if data['media_type'] == 'document':
        content['filename'] = data['filename']
    status, payload = _meta_request('messages', json={'messaging_product': 'whatsapp', 'to': phone,
        'type': data['media_type'], data['media_type']: content})
    messages = payload.get('messages') if payload else None
    message_id = messages[0].get('id') if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
    if status and 200 <= status < 300 and isinstance(message_id, str) and re.fullmatch(r'[A-Za-z0-9_.:=+/-]{1,255}', message_id):
        return 'SENT', message_id
    # Only an explicit client rejection is a definitive failure. All other results
    # might have been accepted remotely and must never trigger a blind retry.
    return ('SEND_FAILED' if status and 400 <= status < 500 else 'OUTCOME_UNCERTAIN'), None


def public_attempt(attempt):
    return {'ok': attempt.status == 'SENT', 'status': attempt.status, 'message_id': attempt.message_id,
            'retry_safe': attempt.status in {'UPLOAD_FAILED', 'SEND_FAILED'}}


def scoped_attempt(db, key, conversation_id, staff_id):
    row = db.query(WhatsAppMediaSend).filter_by(idempotency_key=key).one_or_none()
    if row and (row.conversation_id != conversation_id or row.staff_id != staff_id):
        raise HTTPException(409, 'Idempotency key belongs to another request')
    return row


def perform_send(db, conversation, user, key, raw, data):
    fingerprint = hashlib.sha256(json.dumps([conversation.id, user.id, data], sort_keys=True).encode()).hexdigest()
    existing = scoped_attempt(db, key, conversation.id, user.id)
    if existing:
        if existing.fingerprint != fingerprint:
            raise HTTPException(409, 'Idempotency key cannot be reused for different attachment contents')
        return public_attempt(existing)
    if not conversation.last_customer_message_at or datetime.utcnow() - conversation.last_customer_message_at >= timedelta(hours=24):
        raise HTTPException(409, 'The WhatsApp 24-hour reply window has closed. Ask the customer to message again.')
    attempt = WhatsAppMediaSend(idempotency_key=key, conversation_id=conversation.id,
        staff_id=user.id, fingerprint=fingerprint, status='UPLOADING')
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = scoped_attempt(db, key, conversation.id, user.id)
        if not existing or existing.fingerprint != fingerprint:
            raise HTTPException(409, 'Idempotency key cannot be reused for another request')
        return public_attempt(existing)
    # This committed unique claim is the only code path allowed to call Meta.
    media_id = upload_media(raw, data)
    if not media_id:
        attempt.status = 'UPLOAD_FAILED'
        db.commit()
        return public_attempt(attempt)
    attempt.media_id = media_id
    attempt.status = 'SENDING'
    db.commit()  # Must succeed before a customer message can be sent.
    status, message_id = send_media(conversation.phone_number, media_id, data)
    attempt.status = status
    if status == 'SENT':
        message = WhatsAppConversationMessage(phone_number=conversation.phone_number, direction='outbound',
            message_text=data['caption'] or data['filename'], whatsapp_message_id=message_id,
            response_kind='staff_media', send_status='sent', sent_by=user.username)
        db.add(message)
        db.flush()
        db.add(WhatsAppMessageAttachment(message_id=message.id, media_id=media_id, **data))
        attempt.message_id = message.id
        conversation.last_message_at = datetime.utcnow()
    db.commit()  # Atomically persist history, attachment and success receipt.
    return public_attempt(attempt)
