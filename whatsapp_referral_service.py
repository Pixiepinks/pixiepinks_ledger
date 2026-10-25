"""Pure validation of optional Meta referral metadata. No network or logging."""
import re
from urllib.parse import parse_qsl, unquote, urlsplit

SOURCE_HOSTS = ("facebook.com", "instagram.com", "fb.me", "fb.watch")
MEDIA_HOSTS = ("facebook.com", "fbsbx.com", "fbcdn.net", "cdninstagram.com")
PRIVATE_QUERY_KEYS = {
    "access_token", "token", "auth", "authorization", "password", "passwd", "secret",
    "api_key", "apikey", "client_secret", "appsecret_proof", "signed_request", "ctwa_clid",
    "signature", "sig",
}
SECRET_PATTERN = re.compile(
    r"(?i)\b(?:access[_ -]?token|authorization|client[_ -]?secret|password|api[_ -]?key)\s*[:=]"
    r"|\bbearer\s+\S+|\bEAA[A-Za-z0-9_-]{20,}|\bsk-[A-Za-z0-9_-]{12,}"
)
PRIVATE_URL_PATTERN = re.compile(
    r"(?i)[?&#](?:" + "|".join(key.replace("_", "[-_]") for key in sorted(PRIVATE_QUERY_KEYS)) + r")="
)


def _safe_url(value: object, hosts: tuple[str, ...]) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    # Reject rather than truncate/rewrite a URL, which could change its meaning.
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    decoded = unquote(unquote(value))
    if SECRET_PATTERN.search(decoded) or PRIVATE_URL_PATTERN.search(decoded):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not any(host == allowed or host.endswith("." + allowed) for allowed in hosts)):
            return None
        for query in (urlsplit(decoded).query, urlsplit(decoded).fragment):
            if any(key.casefold().replace("-", "_") in PRIVATE_QUERY_KEYS for key, _ in parse_qsl(query)):
                return None
    except ValueError:
        return None
    return value


def extract_referral(message: dict) -> dict | None:
    referral = message.get("referral")
    if not isinstance(referral, dict):
        return None
    metadata = {}
    for key, pattern in (
        ("source_id", r"[A-Za-z0-9_-]{1,255}"),
        ("source_type", r"[A-Za-z][A-Za-z0-9_-]{0,31}"),
        ("media_type", r"[A-Za-z][A-Za-z0-9_-]{0,31}"),
        ("ctwa_clid", r"[A-Za-z0-9_+/=.-]{1,1024}"),
    ):
        value = referral.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value) and not SECRET_PATTERN.search(value):
            metadata[key] = value
    for key, limit in (("headline", 500), ("body", 4000)):
        value = referral.get(key)
        if isinstance(value, str):
            value = value.encode("utf-8", errors="ignore").decode("utf-8")
            value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()[:limit]
            if value and not SECRET_PATTERN.search(value):
                metadata[key] = value
    for key in ("source_url", "image_url", "video_url", "thumbnail_url"):
        value = _safe_url(referral.get(key), SOURCE_HOSTS if key == "source_url" else MEDIA_HOSTS)
        if value is not None:
            metadata[key] = value
    # captured_at comes from the database clock, never customer-supplied data.
    return metadata or None
