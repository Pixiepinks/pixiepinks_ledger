import logging

import httpx

from settings import settings


logger = logging.getLogger(__name__)

FIXED_AUTO_REPLY = (
    "Thank you for contacting PixiePinks. We have received your message."
)
UNSUPPORTED_MESSAGE_REPLY = (
    "Thank you. At the moment, please send your request as a text message."
)


def send_whatsapp_text(recipient: str, body: str) -> bool:
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
    return True
