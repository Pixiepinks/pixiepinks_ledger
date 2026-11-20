"""TLS termination tests: isolated DB, fake Shopify; no print/mutation dispatch."""
from datetime import timedelta
from pathlib import Path
import hashlib,hmac,os,shlex,socket,subprocess,time
import httpx,pytest
from fastapi.testclient import TestClient
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos
from models import User
from pos_models import POSSession
import main
import hardware_service as h
from settings import settings
from print_agent_security import proxy_policy

HOST='pixiepinksledger-production.up.railway.app'
PEER='100.64.0.11'
PROXY={'X-Forwarded-Proto':'https','X-Forwarded-Host':HOST,'X-Forwarded-Port':'443','X-Forwarded-For':'203.0.113.70'}

class SocketPeer:
    def __init__(self,peer):self.peer=peer
    async def __call__(self,scope,receive,send):
        scope=dict(scope);scope['client']=(self.peer,28110)
        await main.app(scope,receive,send)

@pytest.fixture
def proxied(pos,monkeypatch):
    monkeypatch.setattr(settings,'POS_LOCAL_PRINT_ENABLED',True)
    monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False)
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS',PEER)
    monkeypatch.setattr(settings,'PRINT_AGENT_PUBLIC_ORIGIN','https://'+HOST)
    pos.proxy=TestClient(SocketPeer(PEER),base_url='http://'+HOST)
    with pos.sessions() as db:
        u=db.get(User,pos.uid);reg=db.get(POSSession,pos.sid).register_id
        d,pos.code=h.enroll(db,u,reg);pos.did=d.id
    return pos


def test_proxy_https_reaches_authentication_without_rewriting_scheme_or_client(proxied,monkeypatch):
    identities=[];original=h.throttle
    def capture(db,identity,*args):identities.append(identity);return original(db,identity,*args)
    monkeypatch.setattr(h,'throttle',capture)
    response=proxied.proxy.get('/api/print-agent/config',headers=PROXY)
    assert response.status_code==401 and response.json()['detail']=='Device authentication unavailable'
    expected=hmac.new(settings.SECRET_KEY.encode(),('agent:'+PEER).encode(),hashlib.sha256).hexdigest()
    assert identities==[expected]  # Never trust X-Forwarded-For for identity/rate limits.


def test_valid_pairing_is_one_time_and_still_requires_admin_activation(proxied):
    r=proxied
    response=r.proxy.post('/api/print-agent/pair',headers=PROXY,json={'code':r.code})
    assert response.status_code==200
    token=response.json()['token'];headers={**PROXY,'Authorization':'Bearer '+token}
    assert r.proxy.post('/api/print-agent/pair',headers=PROXY,json={'code':r.code}).status_code==401
    assert r.proxy.get('/api/print-agent/config',headers=headers).status_code==401
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'activate')
    assert r.proxy.get('/api/print-agent/config',headers=headers).status_code==200
    with r.sessions() as db:
        assert db.query(h.HardwareJob).count()==0
        assert db.query(h.HardwareEvent).filter_by(device_id=r.did,action='PAIRED').count()==1
    assert settings.POS_CASH_DRAWER_AUTOMATION_ENABLED is False


@pytest.mark.parametrize('case',['invalid','expired'])
def test_invalid_or_expired_pairing_remains_rejected(proxied,case):
    r=proxied
    if case=='expired':
        with r.sessions() as db:
            d=h.device(db,r.did);d.pair_expires=h.now()-timedelta(seconds=1);db.commit()
        code=r.code
    else:code=r.did+'.invalid'
    assert r.proxy.post('/api/print-agent/pair',headers=PROXY,json={'code':code}).status_code==401


@pytest.mark.parametrize('headers',[
    {},{'X-Forwarded-Proto':'http'},
    {**PROXY,'X-Forwarded-Proto':'https, http'},
    {**PROXY,'X-Forwarded-Proto':'HTTPS'},
    {**PROXY,'X-Forwarded-Host':'evil.example'},
    {**PROXY,'X-Forwarded-Host':HOST+', evil.example'},
    {**PROXY,'X-Forwarded-Port':'80'},
    {**PROXY,'Forwarded':'for=203.0.113.1;proto=https'},
    {**PROXY,'X-Forwarded-Ssl':'on'},
    {**PROXY,'Origin':'https://evil.example'},
    {**PROXY,'Origin':'null'},
    {**PROXY,'Origin':''},
    [('X-Forwarded-Proto','https'),('X-Forwarded-Proto','http')],
])
def test_direct_http_and_inconsistent_or_browser_assertions_rejected(proxied,headers):
    r=proxied
    assert r.proxy.get('/api/print-agent/config',headers=headers).status_code==403
    assert r.proxy.post('/api/print-agent/pair',headers=headers,json={'code':r.code}).status_code==403


@pytest.mark.parametrize('peer',['10.0.0.5','100.64.0.12','203.0.113.1','127.0.0.1'])
def test_other_private_services_and_untrusted_headers_cannot_assert_tls(proxied,peer):
    for scheme in ['http','https']:
        client=TestClient(SocketPeer(peer),base_url=scheme+'://'+HOST)
        assert client.get('/api/print-agent/config',headers=PROXY).status_code==403


def test_host_binding_missing_policy_and_direct_tls(proxied,monkeypatch):
    r=proxied
    assert r.proxy.get('/api/print-agent/config',headers={**PROXY,'Host':'internal.railway.internal'}).status_code==403
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS','')
    assert r.proxy.get('/api/print-agent/config',headers=PROXY).status_code==403
    direct=TestClient(SocketPeer('203.0.113.1'),base_url='https://'+HOST)
    assert direct.get('/api/print-agent/config').status_code==401
    assert direct.get('/api/print-agent/config',headers={'Origin':''}).status_code==403


@pytest.mark.parametrize('peers,origin',[
    ('*','https://'+HOST),('100.64.0.0/10','https://'+HOST),
    ('0.0.0.0','https://'+HOST),(PEER+',','https://'+HOST),
    (PEER,''),(PEER,'http://'+HOST),(PEER,'https://'+HOST+'/path'),
    (PEER,'https://user:password@'+HOST),(PEER,'https://'+HOST+':8080'),
])
def test_no_wildcard_network_or_invalid_public_origin_policy(peers,origin):
    with pytest.raises(ValueError):proxy_policy(peers,origin)


def test_actual_uvicorn_startup_preserves_socket_peer_and_limits_tls_to_agent(tmp_path):
    """Exercise the checked-in Procfile flags through a real loopback HTTP hop."""
    source=tmp_path/'proxy_probe.py'
    source.write_text('from fastapi import FastAPI, Request\nfrom print_agent_security import require_native_https\napp=FastAPI()\n@app.get("/api/print-agent/config")\ndef config(request:Request):\n require_native_https(request)\n return {"peer":request.client.host,"scheme":request.url.scheme}\n@app.get("/ordinary")\ndef ordinary(request:Request):\n return {"scheme":request.url.scheme}\n')
    args=shlex.split(Path('Procfile').read_text().removeprefix('web:').strip())
    assert '--no-proxy-headers' in args and '--forwarded-allow-ips' not in args
    with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    args[0:2]=[os.sys.executable,'-m','uvicorn','proxy_probe:app']
    args[args.index('--host')+1]='127.0.0.1';args[args.index('--port')+1]=str(port)
    env={**os.environ,'PYTHONPATH':str(tmp_path)+os.pathsep+os.getcwd(),'PRINT_AGENT_TRUSTED_PROXY_IPS':'127.0.0.1','PRINT_AGENT_PUBLIC_ORIGIN':'https://'+HOST,'FORWARDED_ALLOW_IPS':'*'}
    # Do not log probe requests, and never send credentials/codes to this server.
    process=subprocess.Popen(args+['--no-access-log'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
            for _ in range(100):
                try:client.get('/ordinary');break
                except httpx.ConnectError:time.sleep(.05)
            response=client.get('/api/print-agent/config',headers={**PROXY,'Host':HOST})
            assert response.status_code==200
            assert response.json()=={'peer':'127.0.0.1','scheme':'http'}
            assert client.get('/ordinary',headers=PROXY).json()['scheme']=='http'
            assert client.get('/api/print-agent/config',headers={'Host':HOST}).status_code==403
    finally:process.terminate();process.wait(timeout=10)


def test_pair_rate_limit_does_not_accept_forwarded_ip_rotation(proxied):
    r=proxied
    for index in range(12):
        headers={**PROXY,'X-Forwarded-For':f'203.0.113.{index+1}'}
        assert r.proxy.post('/api/print-agent/pair',headers=headers,json={'code':r.did+'.wrong'}).status_code==401
    assert r.proxy.post('/api/print-agent/pair',headers=PROXY,json={'code':r.code}).status_code==429


def test_existing_uvicorn_default_explains_railway_scheme_loss():
    import asyncio
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    scopes=[]
    async def capture(scope,receive,send):scopes.append(scope)
    scope={'type':'http','scheme':'http','client':(PEER,28110),'headers':[(b'x-forwarded-proto',b'https')]}
    asyncio.run(ProxyHeadersMiddleware(capture)(scope,None,None))
    assert scopes[0]['scheme']=='http'  # Default only trusts 127.0.0.1, not the Railway peer.
