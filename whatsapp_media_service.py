"""Private Meta media download and S3-compatible persistent storage helpers."""

import hashlib
import importlib
import logging
import re
from pathlib import PurePath
from urllib.parse import urlparse

import httpx

from settings import settings

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES = {
    "image": {"image/jpeg", "image/png", "image/webp"},
    "sticker": {"image/webp"},
    "video": {"video/mp4", "video/3gpp"},
    "audio": {"audio/aac", "audio/amr", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/opus"},
    "document": {
        "application/pdf", "text/plain", "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
}
SAFE_MEDIA_HOST_SUFFIXES = ("facebook.com", "fbcdn.net", "fbsbx.com")


class MediaConfigurationError(RuntimeError):
    pass


class MediaDownloadError(RuntimeError):
    def __init__(self, message: str, *, status: str = "TEMPORARILY_UNAVAILABLE"):
        super().__init__(message)
        self.status = status


def storage_configured() -> bool:
    return all((settings.MEDIA_STORAGE_BUCKET, settings.MEDIA_STORAGE_ACCESS_KEY_ID,
                settings.MEDIA_STORAGE_SECRET_ACCESS_KEY))


def _s3_client():
    if not storage_configured():
        raise MediaConfigurationError("persistent media storage is not configured")
    boto3 = importlib.import_module("boto3")
    return boto3.client(
        "s3", endpoint_url=settings.MEDIA_STORAGE_ENDPOINT_URL or None,
        region_name=settings.MEDIA_STORAGE_REGION,
        aws_access_key_id=settings.MEDIA_STORAGE_ACCESS_KEY_ID,
        aws_secret_access_key=settings.MEDIA_STORAGE_SECRET_ACCESS_KEY,
    )


def sanitize_filename(value: str | None) -> str | None:
    if not value:
        return None
    name = PurePath(value.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")[:180]
    return name or None


def validate_mime(media_type: str, mime_type: str) -> str:
    mime = (mime_type or "").split(";", 1)[0].strip().casefold()
    if mime not in ALLOWED_MIME_TYPES.get(media_type, set()):
        raise MediaDownloadError("unsupported media MIME type", status="UNSUPPORTED")
    return mime


def _authorized_headers() -> dict[str, str]:
    if not settings.META_WHATSAPP_ACCESS_TOKEN:
        raise MediaConfigurationError("Meta media access is not configured")
    return {"Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}"}


def download_meta_media(media_id: str, expected_type: str) -> tuple[bytes, str, str]:
    """Resolve and download one Meta object with bounded memory and safe diagnostics."""
    version = settings.META_GRAPH_API_VERSION.strip().lstrip("/") or "v26.0"
    headers = _authorized_headers()
    try:
        metadata = httpx.get(f"https://graph.facebook.com/{version}/{media_id}",
                             headers=headers, timeout=10.0)
        if metadata.status_code in {400, 404, 410}:
            raise MediaDownloadError("Meta media is no longer available", status="EXPIRED")
        if metadata.status_code == 429 or metadata.status_code >= 500:
            raise MediaDownloadError("Meta media is temporarily unavailable")
        metadata.raise_for_status()
        details = metadata.json()
        url = details.get("url")
        mime = validate_mime(expected_type, str(details.get("mime_type") or ""))
        declared_size = int(details.get("file_size") or 0)
        parsed = urlparse(url) if isinstance(url, str) else None
        hostname = (parsed.hostname or "").casefold() if parsed else ""
        if (not parsed or parsed.scheme != "https" or
                not any(hostname == suffix or hostname.endswith("." + suffix)
                        for suffix in SAFE_MEDIA_HOST_SUFFIXES)):
            raise MediaDownloadError("Meta returned an invalid media URL")
        if declared_size and declared_size > settings.MEDIA_MAX_BYTES:
            raise MediaDownloadError("media exceeds configured size limit", status="UNSUPPORTED")
        chunks, downloaded = [], 0
        with httpx.stream("GET", url, headers=headers, timeout=30.0) as response:
            if response.status_code in {400, 404, 410}:
                raise MediaDownloadError("Meta media is no longer available", status="EXPIRED")
            if response.status_code == 429 or response.status_code >= 500:
                raise MediaDownloadError("Meta media is temporarily unavailable")
            response.raise_for_status()
            response_mime = response.headers.get("content-type", "").split(";", 1)[0].casefold()
            if response_mime:
                validate_mime(expected_type, response_mime)
            content_length = int(response.headers.get("content-length") or 0)
            if content_length and content_length > settings.MEDIA_MAX_BYTES:
                raise MediaDownloadError("media exceeds configured size limit", status="UNSUPPORTED")
            for chunk in response.iter_bytes():
                downloaded += len(chunk)
                if downloaded > settings.MEDIA_MAX_BYTES:
                    raise MediaDownloadError("media exceeds configured size limit", status="UNSUPPORTED")
                chunks.append(chunk)
        content = b"".join(chunks)
    except MediaDownloadError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("Meta media retrieval failed media_id=%s error_type=%s",
                       media_id[-12:], type(exc).__name__)
        raise MediaDownloadError("Meta media retrieval failed") from None
    if len(content) > settings.MEDIA_MAX_BYTES:
        raise MediaDownloadError("media exceeds configured size limit", status="UNSUPPORTED")
    return content, mime, hashlib.sha256(content).hexdigest()


def media_storage_mode() -> str:
    mode = (settings.WHATSAPP_MEDIA_STORAGE_MODE or "meta").strip().casefold()
    return mode if mode in {"meta", "s3"} else "meta"


def put_private_object(key: str, content: bytes, mime_type: str) -> None:
    _s3_client().put_object(Bucket=settings.MEDIA_STORAGE_BUCKET, Key=key, Body=content,
                            ContentType=mime_type, ServerSideEncryption="AES256")


def get_private_object(key: str) -> tuple[bytes, str]:
    result = _s3_client().get_object(Bucket=settings.MEDIA_STORAGE_BUCKET, Key=key)
    return result["Body"].read(settings.MEDIA_MAX_BYTES + 1), result.get("ContentType") or "application/octet-stream"


def delete_private_object(key: str) -> None:
    _s3_client().delete_object(Bucket=settings.MEDIA_STORAGE_BUCKET, Key=key)

