"""Token-free, isolated retrieval of untrusted advertisement image references."""
from collections import OrderedDict
import hashlib
import io
import ipaddress
import logging
import multiprocessing
import os
import re
import socket
import threading
import time
from urllib.parse import urlsplit, unquote

import av
import httpx

from whatsapp_referral_service import _safe_url

MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 8_000_000
MAX_DIMENSION = 4096
MAX_PREVIEW_DIMENSION = 512
MAX_OUTPUT_BYTES = 1024 * 1024
WORKER_TIMEOUT = 12
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_ENTRIES = 32
SUCCESS_TTL = 300
FAILURE_TTL = 60

_capacity = threading.BoundedSemaphore(2)
_lock = threading.Lock()
_cache = OrderedDict()
_in_flight = set()
_cache_bytes = 0


class PreviewError(Exception):
    def __init__(self, status_code=502):
        self.status_code = status_code
        super().__init__('Advertisement preview unavailable')


def validated_creative_url(value):
    # Unlike capture, retrieval does not accept general facebook.com endpoints.
    url = _safe_url(value, ('fbcdn.net', 'cdninstagram.com', 'fbsbx.com'))
    if not url:
        return None
    parsed = urlsplit(url)
    host = parsed.hostname
    if (parsed.fragment or not re.fullmatch(r'[a-z0-9.-]+', host)
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))
            or any(ord(character) < 32 or ord(character) == 127 or character == '\\'
                   for character in unquote(unquote(url)))):
        return None
    if host == 'lookaside.fbsbx.com' or any(host == suffix or host.endswith('.' + suffix)
                                             for suffix in ('fbcdn.net', 'cdninstagram.com')):
        return url
    return None


def preview_candidate(referral):
    media_type = referral.get('media_type')
    media_type = media_type.casefold() if isinstance(media_type, str) else ''
    if media_type == 'video':
        url = validated_creative_url(referral.get('thumbnail_url'))
        return (url, 'video_thumbnail') if url else None
    if media_type not in ('', 'image'):
        return None
    for field in ('image_url', 'thumbnail_url'):
        url = validated_creative_url(referral.get(field))
        if url:
            return url, 'image'
    return None


def _public_address(host):
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = []
    for answer in answers:
        raw = answer[4][0]
        if '%' in raw:
            raise PreviewError(415)
        address = ipaddress.ip_address(raw)
        if (not address.is_global or address.is_multicast or address.is_reserved
                or address.is_loopback or address.is_link_local or address.is_unspecified
                or (address.version == 6 and (address.sixtofour is not None or address.teredo is not None))):
            raise PreviewError(415)
        addresses.append(address)
    if not addresses:
        raise PreviewError(502)
    # The connection uses this literal address; it never resolves the CDN again.
    return str(sorted(set(addresses), key=lambda address: (address.version, int(address)))[0])


def _normalize_image(content, mime):
    signatures = {
        'image/jpeg': content.startswith(b'\xff\xd8\xff'),
        'image/png': content.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP',
    }
    if not signatures.get(mime):
        raise PreviewError(415)
    try:
        with av.open(io.BytesIO(content), options={'err_detect': 'explode'}) as image:
            if len(image.streams.video) != 1:
                raise PreviewError(415)
            stream = image.streams.video[0]
            if stream.codec_context.name not in {'mjpeg', 'png', 'webp'}:
                raise PreviewError(415)
            width, height = stream.width, stream.height
            if min(width, height) <= 0:
                raise PreviewError(415)
            if max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise PreviewError(413)
            stream.codec_context.thread_count = 1
            frame = next(image.decode(video=0))
            if frame.width != width or frame.height != height:
                raise PreviewError(415)
            scale = min(1, MAX_PREVIEW_DIMENSION / max(width, height))
            scaled = frame.reformat(width=max(1, int(width * scale)), height=max(1, int(height * scale)), format='rgb24')
            # Copy pixels to a new frame rather than forwarding EXIF/ICC/side data.
            frame = av.VideoFrame(scaled.width, scaled.height, 'rgb24')
            pixels = bytearray(frame.planes[0].buffer_size)
            original = bytes(scaled.planes[0])
            for row in range(frame.height):
                start = row * frame.planes[0].line_size
                source = row * scaled.planes[0].line_size
                pixels[start:start + frame.width * 3] = original[source:source + frame.width * 3]
            frame.planes[0].update(pixels)
            output = io.BytesIO()
            with av.open(output, mode='w', format='image2pipe') as container:
                encoder = container.add_stream('png', rate=1)
                encoder.width, encoder.height, encoder.pix_fmt = frame.width, frame.height, 'rgb24'
                encoder.codec_context.thread_count = 1
                for packet in encoder.encode(frame): container.mux(packet)
                for packet in encoder.encode(): container.mux(packet)
            normalized = output.getvalue()
            if not normalized or len(normalized) > MAX_OUTPUT_BYTES:
                raise PreviewError(413)
            return normalized
    except PreviewError:
        raise
    except Exception:
        raise PreviewError(415) from None


def _fetch_and_normalize(url):
    if validated_creative_url(url) != url:
        raise PreviewError(415)
    host = urlsplit(url).hostname
    address = _public_address(host)
    pinned = httpx.URL(url).copy_with(host=address)
    deadline = time.monotonic() + 8
    # No bearer token, cookies, netrc, proxy environment or ambient credentials.
    # TLS verification stays enabled with the original CDN hostname as SNI/Host.
    with httpx.Client(trust_env=False, follow_redirects=False,
                      timeout=httpx.Timeout(2.0, connect=3.0),
                      limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
        with client.stream('GET', pinned, headers={'Host': host, 'Accept': 'image/jpeg,image/png,image/webp',
                                                   'Accept-Encoding': 'identity'},
                           extensions={'sni_hostname': host}) as response:
            if response.status_code in {403, 404, 410}: raise PreviewError(410)
            if response.status_code != 200: raise PreviewError(502)
            mime = response.headers.get('content-type', '').split(';', 1)[0].lower().strip()
            if mime not in {'image/jpeg', 'image/png', 'image/webp'}: raise PreviewError(415)
            if response.headers.get('content-encoding', 'identity').lower() not in {'', 'identity'}:
                raise PreviewError(415)
            length = response.headers.get('content-length')
            if length is not None:
                try:
                    if not 0 < int(length) <= MAX_DOWNLOAD_BYTES: raise PreviewError(413)
                except ValueError:
                    raise PreviewError(502) from None
            content = bytearray()
            for chunk in response.iter_raw():
                if time.monotonic() > deadline: raise PreviewError(504)
                if len(content) + len(chunk) > MAX_DOWNLOAD_BYTES: raise PreviewError(413)
                content.extend(chunk)
    return _normalize_image(bytes(content), mime)


def _preview_worker(url, sender):
    # This is a disposable process: logging/codec limits never affect app workers.
    os.environ.clear()
    logging.disable(logging.CRITICAL)
    av.logging.set_level(av.logging.PANIC)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        result = b'P' + _fetch_and_normalize(url)
    except PreviewError as error:
        result = b'E' + str(error.status_code).encode('ascii')
    except BaseException:
        result = b'E502'
    try:
        sender.send_bytes(result)
    except BaseException:
        pass
    finally:
        sender.close()


def _run_worker(url):
    context = multiprocessing.get_context('spawn')
    reader, sender = context.Pipe(duplex=False)
    process = context.Process(target=_preview_worker, args=(url, sender), daemon=True)
    try:
        process.start()
        sender.close()
        if not reader.poll(WORKER_TIMEOUT): raise PreviewError(504)
        # Never unpickle output from a process decoding untrusted image content.
        message = reader.recv_bytes(maxlength=MAX_OUTPUT_BYTES + 1)
        if message[:1] == b'E':
            status = int(message[1:])
            raise PreviewError(status if status in {410, 413, 415, 504} else 502)
        result = message[1:]
        if message[:1] != b'P' or len(result) > MAX_OUTPUT_BYTES or not result.startswith(b'\x89PNG\r\n\x1a\n'):
            raise PreviewError(502)
        return result
    except PreviewError:
        raise
    except Exception:
        raise PreviewError(502) from None
    finally:
        sender.close(); reader.close()
        if process.pid is not None:
            if process.is_alive(): process.terminate()
            process.join(timeout=1)
            if process.is_alive(): process.kill(); process.join(timeout=1)
            process.close()


def get_preview(url, scope):
    """Private RAM LRU keyed by staff/message scope; authorization is done first."""
    global _cache_bytes
    if validated_creative_url(url) != url: raise PreviewError(415)
    key = (scope, hashlib.sha256(url.encode()).digest())
    with _lock:
        now = time.monotonic()
        for stale in [item for item, entry in _cache.items() if entry[0] <= now]:
            _, content, _ = _cache.pop(stale); _cache_bytes -= len(content or b'')
        if key in _cache:
            _, content, error = _cache[key]; _cache.move_to_end(key)
            if error: raise PreviewError(error)
            return content
        if key in _in_flight: raise PreviewError(429)
        if not _capacity.acquire(blocking=False): raise PreviewError(429)
        _in_flight.add(key)
    content, error = None, None
    try:
        try: content = _run_worker(url)
        except PreviewError as failure: error = failure.status_code
        with _lock:
            ttl = FAILURE_TTL if error else SUCCESS_TTL
            _cache[key] = (time.monotonic() + ttl, content, error)
            _cache_bytes += len(content or b'')
            while len(_cache) > MAX_CACHE_ENTRIES or _cache_bytes > MAX_CACHE_BYTES:
                _, (_, old_content, _) = _cache.popitem(last=False); _cache_bytes -= len(old_content or b'')
        if error: raise PreviewError(error)
        return content
    finally:
        with _lock: _in_flight.discard(key)
        _capacity.release()
