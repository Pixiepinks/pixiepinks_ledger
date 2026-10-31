import voice_trace
import hashlib
import global_ai_control
import logging
import json
import re
import time
from urllib.parse import urlparse

import httpx

from settings import settings
from audio_service import validate_voice_audio, VoiceAudioError


logger = logging.getLogger(__name__)

FIXED_AUTO_REPLY = (
    "Thank you for contacting PixiePinks. We have received your message."
)
UNSUPPORTED_MESSAGE_REPLY = (
    "Thank you. At the moment, please send your request as a text message."
)


def is_shopify_image_url(image_url: str) -> bool:
    """Accept only HTTPS URLs served by Shopify's product-image infrastructure."""
    if not isinstance(image_url, str) or not image_url.strip():
        return False
    parsed = urlparse(image_url.strip())
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and bool(parsed.path)
        and (hostname == "cdn.shopify.com" or hostname.endswith(".myshopify.com"))
    )


def _message_id(response: httpx.Response) -> str | None:
    """Extract Meta's outbound wamid without trusting any other response data."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None
    messages = payload.get("messages") if isinstance(payload, dict) else None
    value = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
    return value if isinstance(value, str) and value.strip() else None


@global_ai_control.transport_send
def send_whatsapp_image(
    recipient: str, image_url: str, caption: str | None = None, *, return_message_id: bool = False,
    return_outcome: bool = False,
) -> bool | str | tuple[str, str | None] | None:
    """Send a verified Shopify image through the existing Meta Cloud API."""
    if not is_shopify_image_url(image_url):
        logger.warning("WhatsApp image skipped recipient=%s reason=untrusted_url", recipient)
        return ("FAILED", None) if return_outcome else False
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id:
        logger.error("WhatsApp image skipped recipient=%s reason=missing_configuration", recipient)
        return ("FAILED", None) if return_outcome else False
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    image = {"link": image_url.strip()}
    if caption:
        image["caption"] = caption
    payload = {"messaging_product": "whatsapp", "to": recipient,
               "type": "image", "image": image}
    try:
        response = httpx.post(
            url, headers={"Authorization": f"Bearer {access_token}"},
            json=payload, timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("WhatsApp image failed recipient=%s status_code=%s",
                     recipient, exc.response.status_code)
        state = "FAILED" if 400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 429} else "UNKNOWN"
        return (state, None) if return_outcome else False
    except httpx.RequestError as exc:
        logger.error("WhatsApp image failed recipient=%s error_type=%s",
                     recipient, type(exc).__name__)
        return ("UNKNOWN", None) if return_outcome else False
    logger.info("WhatsApp image succeeded recipient=%s status_code=%s",
                recipient, response.status_code)
    message_id = _message_id(response)
    if return_outcome:
        return ("SENT", message_id) if message_id else ("UNKNOWN", None)
    return message_id if return_message_id else True


@global_ai_control.transport_send
def send_whatsapp_text(
    recipient: str, body: str, *, return_message_id: bool = False
) -> bool | str | None:
    """Send a text through Meta without allowing failures to escape the worker."""
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id:
        logger.error(
            "WhatsApp outbound skipped recipient=%s: access token or phone number ID is not configured",
            recipient,
        )
        return False

    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": body},
    }
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "WhatsApp outbound failed recipient=%s status_code=%s",
            recipient,
            exc.response.status_code,
        )
        return False
    except httpx.RequestError as exc:
        logger.error(
            "WhatsApp outbound failed recipient=%s error_type=%s",
            recipient,
            type(exc).__name__,
        )
        return False

    logger.info(
        "WhatsApp outbound succeeded recipient=%s status_code=%s",
        recipient,
        response.status_code,
    )
    message_id = _message_id(response)
    return message_id if return_message_id else True


def _audio_diagnostic(stage, response):
    code=subcode=None
    try:
        error=response.json().get("error",{})
        if isinstance(error,dict):
            code=error.get("code") if type(error.get("code")) is int else None
            subcode=error.get("error_subcode") if type(error.get("error_subcode")) is int else None
    except (ValueError,AttributeError):pass
    logger.info("WhatsApp audio result stage=%s http_status=%s error_code=%s error_subcode=%s",stage,response.status_code,code,subcode)
    return {"http_status":response.status_code,"error_code":code,"error_subcode":subcode}


def upload_whatsapp_audio(audio: bytes, filename: str = "voice-note.ogg") -> str | None:
    """Upload an OGG/Opus voice recording to Meta and return only its media ID."""
    try:
        info=validate_voice_audio(audio)
    except VoiceAudioError:
        logger.warning("WhatsApp audio upload blocked reason=invalid_ogg_opus")
        return None
    voice_trace.record("upload_validated", voice_trace.fingerprint(audio))
    voice_trace.record("upload_codec", info)
    logger.info("WhatsApp audio upload input codec=opus container=ogg duration_seconds=%s size_bytes=%s", info["duration_seconds"],len(audio))
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id:
        logger.error("WhatsApp audio upload skipped reason=missing_configuration")
        return None
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    voice_trace.record("multipart_file", {**voice_trace.fingerprint(audio),"mime_type":"audio/ogg",
        "phone_id_sha256":hashlib.sha256(phone_number_id.encode()).hexdigest(),"graph_version":voice_trace.graph_version(version)})
    try:
        response = httpx.post(
            f"https://graph.facebook.com/{version}/{phone_number_id}/media",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": (filename, audio, "audio/ogg")}, timeout=30.0,
        )
        voice_trace.record("upload_http", _audio_diagnostic("upload",response))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        voice_trace.record("upload_exception", {"http_status":status,"error_type":type(exc).__name__})
        logger.error("WhatsApp audio upload failed status_code=%s error_type=%s", status, type(exc).__name__)
        return None
    media_id = payload.get("id") if isinstance(payload, dict) else None
    voice_trace.record("upload_result", {"media_id":media_id if isinstance(media_id,str) else None})
    logger.info("WhatsApp audio upload media_id_present=%s",bool(isinstance(media_id,str) and media_id.strip()))
    return media_id if isinstance(media_id, str) and media_id.strip() else None


@global_ai_control.transport_send
def send_whatsapp_audio(recipient: str, media_id: str, *, voice: bool = True,
                        return_outcome: bool = False, owner_callback: str | None = None) -> str | None | tuple[str, str | None]:
    """Send previously uploaded Meta media as an audio message/voice note."""
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id or not media_id:
        logger.error("WhatsApp audio send skipped reason=missing_configuration_or_media")
        return ("FAILED", None) if return_outcome else None
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    audio = {"id": media_id}
    if voice:
        audio["voice"] = True
    payload={"messaging_product":"whatsapp","to":recipient,"type":"audio","audio":audio}
    if owner_callback is not None:
        import re
        if not re.fullmatch(r'exact-control:[0-9a-f]{32}',owner_callback):
            raise ValueError('Invalid owner diagnostic callback')
        payload['biz_opaque_callback_data']=owner_callback
    voice_trace.record("send_payload", {"type":"audio","audio":dict(audio),
        "phone_id_sha256":hashlib.sha256(phone_number_id.encode()).hexdigest(),"graph_version":voice_trace.graph_version(version)})
    try:
        response = httpx.post(
            f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
            timeout=15.0,
        )
        voice_trace.record("send_http", _audio_diagnostic("send",response))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        voice_trace.record("send_exception", {"http_status":status,"error_type":type(exc).__name__})
        logger.error("WhatsApp audio send failed status_code=%s error_type=%s", status, type(exc).__name__)
        state = "FAILED" if status and 400 <= status < 500 and status not in {408, 429} else "UNKNOWN"
        return (state, None) if return_outcome else None
    message_id = _message_id(response)
    voice_trace.record("send_result", {"whatsapp_message_id":message_id})
    logger.info("WhatsApp audio send accepted=%s voice_note=%s",bool(message_id),voice)
    return (("SENT", message_id) if message_id else ("UNKNOWN", None)) if return_outcome else message_id


def download_whatsapp_media(media_id: str) -> tuple[bytes, str] | None:
    """Fetch Meta media server-side so credentials never reach the browser."""
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    if not access_token or not media_id:
        return None
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        metadata = httpx.get(
            f"https://graph.facebook.com/{version}/{media_id}", headers=headers, timeout=10.0
        )
        metadata.raise_for_status()
        payload = metadata.json()
        url = payload.get("url") if isinstance(payload, dict) else None
        mime = payload.get("mime_type") if isinstance(payload, dict) else None
        parsed = urlparse(url) if isinstance(url, str) else None
        hostname = (parsed.hostname or "").casefold() if parsed else ""
        trusted_media_host = (
            hostname == "lookaside.fbsbx.com" or hostname == "graph.facebook.com"
            or hostname.endswith(".fbcdn.net") or hostname.endswith(".facebook.com")
        )
        if not parsed or parsed.scheme != "https" or not trusted_media_host:
            logger.warning("WhatsApp media download rejected reason=untrusted_url")
            return None
        media = httpx.get(url, headers=headers, timeout=30.0)
        media.raise_for_status()
        if len(media.content) > MAX_DOWNLOAD_BYTES:
            logger.warning("WhatsApp media download rejected reason=too_large")
            return None
        return media.content, mime if isinstance(mime, str) else "audio/ogg"
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.error("WhatsApp media download failed error_type=%s", type(exc).__name__)
        return None


MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024


ATTACHMENT_MIME_TYPES = {
    "image": {"image/jpeg", "image/png", "image/webp"},
    "sticker": {"image/webp"},
    "video": {"video/mp4", "video/3gpp"},
    "audio": {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr", "audio/wav"},
    "document": {
        "application/pdf", "text/plain", "text/csv", "application/msword",
        "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
}


class AttachmentMediaError(Exception):
    """Only fixed, browser-safe errors; never expose upstream URLs or tokens."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _bounded_media_body(response: httpx.Response, limit: int, deadline: float | None = None) -> bytes:
    response.raise_for_status()
    length = response.headers.get("content-length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > limit:
                raise AttachmentMediaError(413, "Attachment exceeds the viewing size limit")
        except ValueError:
            raise AttachmentMediaError(502, "Attachment is temporarily unavailable") from None
    content = bytearray()
    for chunk in response.iter_bytes(chunk_size=64 * 1024):
        if deadline is not None and time.monotonic() > deadline:
            raise AttachmentMediaError(504, "Attachment processing timed out")
        if len(content) + len(chunk) > limit:
            raise AttachmentMediaError(413, "Attachment exceeds the viewing size limit")
        content.extend(chunk)
    return bytes(content)


def _attachment_signature_matches(content: bytes, mime: str) -> bool:
    if mime == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if mime in {"video/mp4", "video/3gpp", "audio/mp4"}:
        return content[4:8] == b"ftyp"
    if mime == "application/pdf":
        return content.startswith(b"%PDF-")
    if mime.startswith("application/vnd.openxmlformats-officedocument."):
        return content.startswith(b"PK\x03\x04")
    if mime in {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}:
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if mime in {"text/plain", "text/csv"}:
        try:
            return bool(content) and "\x00" not in content.decode("utf-8")
        except UnicodeDecodeError:
            return False
    # Audio continues to be decoded by the browser, never executed as a document.
    return bool(content)


def download_whatsapp_attachment(media_id: str, media_type: str, *,
                                 max_bytes: int = MAX_DOWNLOAD_BYTES, timeout_seconds: int = 30) -> tuple[bytes, str]:
    """Resolve fresh Meta metadata and fetch bounded media in memory on demand.

    The legacy voice playback downloader remains unchanged. Neither the signed
    download URL nor Authorization headers are returned, logged, or persisted.
    """
    if media_type not in ATTACHMENT_MIME_TYPES or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", media_id):
        raise AttachmentMediaError(415, "Attachment type is not supported")
    token = settings.META_WHATSAPP_ACCESS_TOKEN
    if not token:
        raise AttachmentMediaError(503, "Attachment access is not configured")
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + timeout_seconds
    try:
        limit = min(max_bytes, MAX_DOWNLOAD_BYTES)
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(10, timeout_seconds)), follow_redirects=False) as client:
            with client.stream("GET", f"https://graph.facebook.com/{version}/{media_id}", headers=headers) as response:
                metadata = json.loads(_bounded_media_body(response, 64 * 1024, deadline))
            if not isinstance(metadata, dict):
                raise ValueError("Invalid metadata")
            mime = metadata.get("mime_type")
            if mime not in ATTACHMENT_MIME_TYPES[media_type]:
                raise AttachmentMediaError(415, "Attachment format is not supported")
            declared_size = metadata.get("file_size")
            if type(declared_size) is int and declared_size > limit:
                raise AttachmentMediaError(413, "Attachment exceeds the processing size limit")
            url = metadata.get("url")
            parsed = urlparse(url) if isinstance(url, str) else None
            host = (parsed.hostname or "").lower() if parsed else ""
            if (not parsed or parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in (None, 443) or not parsed.path
                    or not (host in {"lookaside.fbsbx.com", "graph.facebook.com"} or host.endswith(".fbcdn.net"))):
                raise ValueError("Untrusted media destination")
            with client.stream("GET", url, headers=headers) as response:
                # Do not follow redirects with the provider's bearer credential.
                if response.is_redirect:
                    raise ValueError("Media redirect rejected")
                response_mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if response_mime not in {mime, "application/octet-stream"}:
                    raise AttachmentMediaError(415, "Attachment format does not match its metadata")
                content = _bounded_media_body(response, limit, deadline)
            if not _attachment_signature_matches(content, mime):
                raise AttachmentMediaError(415, "Attachment content does not match its format")
            return content, mime
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 410}:
            raise AttachmentMediaError(410, "Attachment is no longer available from WhatsApp") from None
        raise AttachmentMediaError(502, "Attachment is temporarily unavailable") from None
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError):
        raise AttachmentMediaError(502, "Attachment is temporarily unavailable") from None
