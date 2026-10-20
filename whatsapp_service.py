import logging
from urllib.parse import urlparse

import httpx

from settings import settings


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


def send_whatsapp_image(
    recipient: str, image_url: str, caption: str | None = None, *, return_message_id: bool = False
) -> bool | str | None:
    """Send a verified Shopify image through the existing Meta Cloud API."""
    if not is_shopify_image_url(image_url):
        logger.warning("WhatsApp image skipped recipient=%s reason=untrusted_url", recipient)
        return False
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id:
        logger.error("WhatsApp image skipped recipient=%s reason=missing_configuration", recipient)
        return False
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
        return False
    except httpx.RequestError as exc:
        logger.error("WhatsApp image failed recipient=%s error_type=%s",
                     recipient, type(exc).__name__)
        return False
    logger.info("WhatsApp image succeeded recipient=%s status_code=%s",
                recipient, response.status_code)
    message_id = _message_id(response)
    return message_id if return_message_id else True


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


def upload_whatsapp_audio(audio: bytes, filename: str = "voice-note.ogg") -> str | None:
    """Upload an OGG/Opus voice recording to Meta and return only its media ID."""
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id:
        logger.error("WhatsApp audio upload skipped reason=missing_configuration")
        return None
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    try:
        response = httpx.post(
            f"https://graph.facebook.com/{version}/{phone_number_id}/media",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": (filename, audio, "audio/ogg")}, timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.error("WhatsApp audio upload failed status_code=%s error_type=%s", status, type(exc).__name__)
        return None
    media_id = payload.get("id") if isinstance(payload, dict) else None
    return media_id if isinstance(media_id, str) and media_id.strip() else None


def send_whatsapp_audio(recipient: str, media_id: str, *, voice: bool = True) -> str | None:
    """Send previously uploaded Meta media as an audio message/voice note."""
    access_token = settings.META_WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    if not access_token or not phone_number_id or not media_id:
        logger.error("WhatsApp audio send skipped reason=missing_configuration_or_media")
        return None
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    audio = {"id": media_id}
    if voice:
        audio["voice"] = True
    try:
        response = httpx.post(
            f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"messaging_product": "whatsapp", "to": recipient, "type": "audio", "audio": audio},
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.error("WhatsApp audio send failed status_code=%s error_type=%s", status, type(exc).__name__)
        return None
    return _message_id(response)


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
