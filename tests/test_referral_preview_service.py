"""No real CDN requests: validate DNS pinning, resource bounds and private caching."""
from concurrent.futures import ThreadPoolExecutor
import io
import logging
import socket
import threading
import time

import av
import httpx
import pytest

import referral_preview_service as service

URL = 'https://scontent.example.fbcdn.net/creative.jpg?oh=private-cdn-reference'


def image_bytes(codec='png', pixel='rgb24', width=4, height=4):
    buffer = io.BytesIO()
    with av.open(buffer, mode='w', format='image2pipe') as output:
        stream = output.add_stream(codec, rate=1); stream.width=width; stream.height=height; stream.pix_fmt=pixel
        frame = av.VideoFrame(width,height,'rgb24')
        for plane in frame.planes: plane.update(bytes(plane.buffer_size))
        frame = frame.reformat(format=pixel)
        for packet in stream.encode(frame): output.mux(packet)
        for packet in stream.encode(): output.mux(packet)
    return buffer.getvalue()


@pytest.fixture
def provider(monkeypatch):
    original = httpx.Client
    calls = []
    monkeypatch.setattr(service.socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    def install(handler):
        def streamed(request):
            response = handler(request)
            if response.is_stream_consumed:
                return httpx.Response(response.status_code, headers=response.headers,
                                      stream=httpx.ByteStream(response.content))
            return response
        def client(**kwargs):
            calls.append(kwargs)
            return original(transport=httpx.MockTransport(streamed),**kwargs)
        monkeypatch.setattr(service.httpx,'Client',client)
    return install,calls


@pytest.mark.parametrize('codec,pixel,mime',[('png','rgb24','image/png'),('mjpeg','yuvj420p','image/jpeg'),('libwebp','yuv420p','image/webp')])
def test_supported_images_are_pinned_verified_and_normalized(provider,codec,pixel,mime):
    install,clients = provider; requests=[]
    def handler(request):
        requests.append(request)
        assert request.url.host == '8.8.8.8'
        assert request.extensions['sni_hostname'] == 'scontent.example.fbcdn.net'
        assert request.headers['host'] == 'scontent.example.fbcdn.net'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert request.headers['accept-encoding'] == 'identity'
        return httpx.Response(200,content=image_bytes(codec,pixel),headers={'content-type':mime})
    install(handler)
    content=service._fetch_and_normalize(URL)
    assert content.startswith(b'\x89PNG\r\n\x1a\n') and len(requests)==1
    assert clients[0]['trust_env'] is False and clients[0]['follow_redirects'] is False
    assert 'verify' not in clients[0] or clients[0]['verify'] is True
    with av.open(io.BytesIO(content)) as output:
        frame=next(output.decode(video=0));assert (frame.width,frame.height)==(4,4)


@pytest.mark.parametrize('url',[
    'http://scontent.example.fbcdn.net/a','https://127.0.0.1/a','https://169.254.169.254/a',
    'https://fbcdn.net.evil.test/a','https://graph.facebook.com/a','https://facebook.com/a',
    'https://other.fbsbx.com/a','https://user:pass@scontent.example.fbcdn.net/a',
    'https://scontent.example.fbcdn.net:8443/a','https://scontent.example.fbcdn.net/a?access_token=secret',
    'https://scontent.example.fbcdn.net/a#ctwa_clid=private','https://scontent.example.fbcdn.net/a?token=secret',
    'https://scontent.example.fbcdn.net/a%0d%0aHeader','https://foo%2fbar.fbcdn.net/a',
    'https://scontent.example.fbcdn.net./a','https://scontent.example.fbcdn.net/a\\evil',
])
def test_unsafe_urls_are_rejected_without_dns_or_network(monkeypatch,url):
    monkeypatch.setattr(service.socket,'getaddrinfo',lambda *a,**kw:pytest.fail('Unsafe DNS'))
    assert service.validated_creative_url(url) is None
    with pytest.raises(service.PreviewError):service._fetch_and_normalize(url)


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','0.0.0.0','224.0.0.1','192.0.2.1','::1','fe80::1','::ffff:127.0.0.1','2002:7f00:1::','2002:808:808::'])
def test_private_or_special_dns_results_block_all_connections(monkeypatch,address):
    monkeypatch.setattr(service.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',(address,443))])
    monkeypatch.setattr(service.httpx,'Client',lambda *a,**kw:pytest.fail('Unsafe connection'))
    with pytest.raises(service.PreviewError):service._fetch_and_normalize(URL)


def test_dns_rebinding_cannot_change_the_pinned_destination(provider,monkeypatch):
    install,_=provider;resolved=[]
    def resolve(*args,**kwargs):
        resolved.append(args[0]);return [(2,1,6,'',('8.8.8.8' if len(resolved)==1 else '127.0.0.1',443))]
    monkeypatch.setattr(service.socket,'getaddrinfo',resolve)
    def handler(request):
        assert request.url.host=='8.8.8.8'
        return httpx.Response(200,stream=httpx.ByteStream(image_bytes()),headers={'content-type':'image/png'})
    install(handler);service._fetch_and_normalize(URL)
    assert resolved==['scontent.example.fbcdn.net']


@pytest.mark.parametrize('status,expected',[(301,502),(302,502),(403,410),(404,410),(410,410),(500,502)])
def test_upstream_errors_and_redirects_are_not_followed(provider,status,expected):
    install,_=provider;requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(status,text='secret-url-and-token',headers={'location':'https://127.0.0.1/private'})
    install(handler)
    with pytest.raises(service.PreviewError) as failure:service._fetch_and_normalize(URL)
    assert failure.value.status_code==expected and len(requests)==1
    assert 'secret' not in str(failure.value)


@pytest.mark.parametrize('mime,content',[
    ('text/html',b'<script>private</script>'),('image/svg+xml',b'<svg/>'),('video/mp4',b'0000ftypvideo'),
    ('image/jpeg',b'<html>not JPEG</html>'),('image/png',b'\x89PNG\r\n\x1a\ncorrupt'),
    ('image/png',image_bytes('mjpeg','yuvj420p')),
])
def test_invalid_types_and_contents_are_not_returned(provider,mime,content):
    install,_=provider
    install(lambda request:httpx.Response(200,content=content,headers={'content-type':mime}))
    with pytest.raises(service.PreviewError) as failure:service._fetch_and_normalize(URL)
    assert failure.value.status_code==415


@pytest.mark.parametrize('kind',['length','stream','encoding','timeout','deadline'])
def test_download_limits(provider,monkeypatch,kind):
    install,_=provider
    monkeypatch.setattr(service,'MAX_DOWNLOAD_BYTES',16)
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):yield b'12345678';yield b'123456789'
    def handler(request):
        if kind=='timeout':raise httpx.ReadTimeout('private-url')
        if kind=='deadline':monkeypatch.setattr(service.time,'monotonic',lambda:1e20)
        headers={'content-type':'image/png'}
        if kind=='length':headers['content-length']='17'
        if kind=='encoding':headers['content-encoding']='gzip'
        return httpx.Response(200,stream=Chunks(),headers=headers)
    install(handler)
    with pytest.raises((service.PreviewError,httpx.ReadTimeout)):service._fetch_and_normalize(URL)


def test_dimension_limit_and_resizing():
    normalized=service._normalize_image(image_bytes(width=1024,height=1024),'image/png')
    with av.open(io.BytesIO(normalized)) as image:
        assert image.streams.video[0].width==512 and image.streams.video[0].height==512
    with pytest.raises(service.PreviewError):
        service._normalize_image(image_bytes(width=4098,height=2),'image/png')


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(service,'_cache',service.OrderedDict())
    monkeypatch.setattr(service,'_cache_bytes',0)
    monkeypatch.setattr(service,'_in_flight',set())
    monkeypatch.setattr(service,'_capacity',threading.BoundedSemaphore(2))
    return []


def test_private_cache_success_negative_expiry_and_bounds(cache,monkeypatch):
    monkeypatch.setattr(service,'_run_worker',lambda url:cache.append(url) or b'preview')
    assert service.get_preview(URL,(1,1))==service.get_preview(URL,(1,1))==b'preview'
    assert len(cache)==1
    service.get_preview(URL,(2,1));assert len(cache)==2
    monkeypatch.setattr(service,'MAX_CACHE_ENTRIES',2)
    service.get_preview(URL,(3,1));assert len(service._cache)==2
    now=time.monotonic()
    monkeypatch.setattr(service.time,'monotonic',lambda:now+301)
    service.get_preview(URL,(3,1));assert len(cache)==4
    def fail(url):cache.append(url);raise service.PreviewError(410)
    monkeypatch.setattr(service,'_run_worker',fail)
    for _ in range(2):
        with pytest.raises(service.PreviewError):service.get_preview(URL,(4,1))
    assert len(cache)==5
    monkeypatch.setattr(service,'MAX_CACHE_BYTES',3)
    monkeypatch.setattr(service,'_run_worker',lambda url:b'1234')
    service.get_preview(URL,(5,1));assert service._cache_bytes<=3


def test_concurrency_and_single_flight_do_not_queue(cache,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def worker(url):entered.set();release.wait(5);return b'preview'
    monkeypatch.setattr(service,'_run_worker',worker)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending=executor.submit(service.get_preview,URL,(1,));assert entered.wait(2)
        with pytest.raises(service.PreviewError) as failure:service.get_preview(URL,(1,))
        assert failure.value.status_code==429
        assert service._capacity.acquire(blocking=False)
        try:
            with pytest.raises(service.PreviewError) as failure:service.get_preview(URL,(2,))
            assert failure.value.status_code==429
        finally:service._capacity.release();release.set()
        assert pending.result()==b'preview'


def mocked_preview_worker(url,sender):
    # Runs in a real spawned process, using the real worker/resource/codec path.
    import os, resource
    os.environ['META_WHATSAPP_ACCESS_TOKEN']='do-not-send'
    original=httpx.Client
    service.socket.getaddrinfo=lambda *a,**kw:[(2,1,6,'',('8.8.8.8',443))]
    def handler(request):
        logging.getLogger('httpx').warning('private-signed-url token image-content')
        assert 'META_WHATSAPP_ACCESS_TOKEN' not in os.environ
        assert resource.getrlimit(resource.RLIMIT_CPU)[0]==3
        assert resource.getrlimit(resource.RLIMIT_AS)[0]==512*1024*1024
        assert 'authorization' not in request.headers
        return httpx.Response(200,stream=httpx.ByteStream(image_bytes()),headers={'content-type':'image/png'})
    service.httpx.Client=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)
    service._preview_worker(url,sender)


def hanging_preview_worker(url,sender):
    time.sleep(30)


def test_real_isolated_worker_and_hard_timeout(monkeypatch,capfd):
    monkeypatch.setattr(service,'_preview_worker',mocked_preview_worker)
    assert service._run_worker(URL).startswith(b'\x89PNG')
    captured=capfd.readouterr()
    assert not any(value in captured.out+captured.err for value in ['private-signed-url','image-content','token'])
    monkeypatch.setattr(service,'_preview_worker',hanging_preview_worker)
    monkeypatch.setattr(service,'WORKER_TIMEOUT',0.1)
    with pytest.raises(service.PreviewError) as failure:service._run_worker(URL)
    assert failure.value.status_code==504
