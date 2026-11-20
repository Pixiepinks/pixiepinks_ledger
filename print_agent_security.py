"""Native-agent TLS verification using the original socket peer, never forwarded IPs.

Uvicorn must run with --no-proxy-headers so scope['client']/scheme are transport
facts. Only this API consumes explicitly approved proxy assertions; no global
request URL, cookie, redirect or client-IP rewriting is performed.
"""
from functools import lru_cache
from ipaddress import ip_address
from urllib.parse import urlsplit
from fastapi import HTTPException
from settings import settings


@lru_cache(maxsize=16)
def proxy_policy(peers, origin):
    # Exact socket addresses only. Neither '*' nor a shared private-network CIDR
    # establishes that its members are authorized TLS-terminating edge proxies.
    values=peers.split(',') if peers else []
    if any(not value.strip() for value in values):raise ValueError('Empty proxy address')
    trusted=frozenset(ip_address(value.strip()) for value in values)
    if any(value.is_unspecified or value.is_multicast for value in trusted):raise ValueError('Invalid proxy address')
    if not trusted:return trusted,None
    return trusted,public_host(origin)


@lru_cache(maxsize=16)
def public_host(origin):
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('Approved HTTPS public origin required')
    return parsed.hostname.lower()


def authority(value):
    parsed=urlsplit('//'+value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.path or parsed.query or parsed.fragment:raise ValueError('Invalid authority')
    return parsed.hostname.lower()


def require_native_https(request):
    def reject():raise HTTPException(403,'Native agent over TLS required')
    # Browser Origins, including empty/multiple headers, are never native clients.
    headers={}
    for key,value in request.scope['headers']:
        headers.setdefault(key.lower(),[]).append(value.decode('latin1'))
    if b'origin' in headers:reject()
    if settings.PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED:
        # An HTTP header cannot create this process-local identity. Only the
        # controlled ASGI entrypoint may attach it after validating ingress.
        if request.scope.get(_INGRESS_SCOPE_KEY) is not _INGRESS_PROOF:reject()
        return
    forwarded={key:values for key,values in headers.items() if key==b'forwarded' or key.startswith(b'x-forwarded-')}
    if not forwarded:
        if request.scope.get('scheme')!='https':reject()
        return
    try:
        trusted,host=proxy_policy(settings.PRINT_AGENT_TRUSTED_PROXY_IPS,settings.PRINT_AGENT_PUBLIC_ORIGIN)
        peer=ip_address(request.scope['client'][0])
        if peer not in trusted or not host:reject()
        # RFC Forwarded/chained proto and duplicate headers are deliberately not
        # interpreted: an ambiguous assertion must not authorize plaintext traffic.
        if b'forwarded' in forwarded or any(len(values)!=1 for values in forwarded.values()):reject()
        if headers.get(b'x-forwarded-proto')!=['https']:reject()
        if len(headers.get(b'host',[]))!=1 or authority(headers[b'host'][0])!=host:reject()
        if b'x-forwarded-host' in headers and authority(headers[b'x-forwarded-host'][0])!=host:reject()
        if b'x-forwarded-port' in headers and headers[b'x-forwarded-port']!=['443']:reject()
        if any(key not in {b'x-forwarded-proto',b'x-forwarded-host',b'x-forwarded-port',b'x-forwarded-for'} for key in forwarded):reject()
        if request.scope.get('scheme') not in {'http','https'}:reject()
    except (ValueError,TypeError,KeyError,IndexError):reject()
    # X-Forwarded-For is intentionally never used for trust or rate-limit identity.


# This is an in-process provenance marker, not an HTTP header/shared secret. It
# proves passage through our entrypoint; it does NOT prove Railway-edge identity.
# That identity depends on independently enforced exclusive NETWORK isolation.
_INGRESS_SCOPE_KEY='pixiepinks.native_ingress_proof'
_INGRESS_PROOF=object()


def isolated_ingress_scope(scope):
    """Validate the approved isolated edge contract; preserve transport facts.

    Must never be enabled on a shared Railway environment. No code can distinguish
    its forged private HTTP request from an unsigned Railway HTTP edge hop.
    """
    def reject():raise HTTPException(403,'Native agent over TLS required')
    if not settings.PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED:reject()
    if settings.PRINT_AGENT_TRUSTED_PROXY_IPS.strip():reject()  # No mixed trust models.
    try:
        host=public_host(settings.PRINT_AGENT_PUBLIC_ORIGIN)
        headers={}
        for key,value in scope['headers']:headers.setdefault(key.lower(),[]).append(value.decode('latin1'))
        if b'origin' in headers or len(headers.get(b'host',[]))!=1 or authority(headers[b'host'][0])!=host:reject()
        # Incoming callers may not submit an entrypoint proof or alternate
        # transport contract. They are never carried into route authorization.
        if any(key.startswith((b'x-pixiepinks-',b'x-print-agent-')) for key in headers):reject()
        forwarded={key:values for key,values in headers.items() if key==b'forwarded' or key.startswith(b'x-forwarded-')}
        if any(len(values)!=1 for values in forwarded.values()):reject()
        if any(key not in {b'x-forwarded-proto',b'x-forwarded-host',b'x-forwarded-port',b'x-forwarded-for'} for key in forwarded):reject()
        if forwarded:
            if headers.get(b'x-forwarded-proto')!=['https']:reject()
            if b'x-forwarded-host' in headers and authority(headers[b'x-forwarded-host'][0])!=host:reject()
            if b'x-forwarded-port' in headers and headers[b'x-forwarded-port']!=['443']:reject()
            if scope.get('scheme') not in {'http','https'}:reject()
        elif scope.get('scheme')!='https':reject()
    except (ValueError,TypeError,KeyError,IndexError):reject()
    verified=dict(scope)
    verified[_INGRESS_SCOPE_KEY]=_INGRESS_PROOF
    return verified
