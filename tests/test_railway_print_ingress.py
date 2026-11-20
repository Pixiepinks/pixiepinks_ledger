"""Application ingress tests, NOT certification of Railway network isolation."""
from datetime import timedelta
from fastapi.testclient import TestClient
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos
from test_print_agent_proxy import proxied,HOST,PEER,PROXY
import pytest
import main
import hardware_service as h
from settings import settings
from models import User
from railway_ingress import RailwayIsolatedIngress


class Hop:
    def __init__(self,app,peer=PEER):self.app=app;self.peer=peer
    async def __call__(self,scope,receive,send):
        scope=dict(scope);scope['client']=(self.peer,28110)
        await self.app(scope,receive,send)


@pytest.fixture
def ingress(proxied,monkeypatch):
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS','')
    monkeypatch.setattr(settings,'PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED',True)
    proxied.ingress=TestClient(Hop(RailwayIsolatedIngress(main.app)),base_url='http://'+HOST)
    return proxied


def test_approved_ingress_https_reaches_401_with_no_stable_proxy_ip(ingress):
    for peer in ['100.64.0.11','100.64.23.44','fd12::1234']:
        client=TestClient(Hop(RailwayIsolatedIngress(main.app),peer),base_url='http://'+HOST)
        response=client.get('/api/print-agent/config',headers=PROXY)
        assert response.status_code==401
        assert response.json()=={'detail':'Device authentication unavailable'}


def test_pairing_through_existing_origin_requires_single_use_code_and_activation(ingress):
    r=ingress;reply=r.ingress.post('/api/print-agent/pair',headers=PROXY,json={'code':r.code})
    assert reply.status_code==200
    auth={**PROXY,'Authorization':'Bearer '+reply.json()['token']}
    assert r.ingress.post('/api/print-agent/pair',headers=PROXY,json={'code':r.code}).status_code==401
    assert r.ingress.get('/api/print-agent/config',headers=auth).status_code==401
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'activate')
    assert r.ingress.get('/api/print-agent/config',headers=auth).status_code==200
    with r.sessions() as db:
        assert db.query(h.HardwareJob).count()==0
        assert db.query(h.HardwareEvent).filter_by(device_id=r.did,action='PAIRED').count()==1
        h.manage(db,db.get(User,r.uid),r.did,'revoke')
    assert r.ingress.get('/api/print-agent/config',headers=auth).status_code==401
    assert settings.POS_CASH_DRAWER_AUTOMATION_ENABLED is False


@pytest.mark.parametrize('expired',[True,False])
def test_invalid_and_expired_pair_codes_still_fail(ingress,expired):
    r=ingress
    if expired:
        with r.sessions() as db:d=h.device(db,r.did);d.pair_expires=h.now()-timedelta(seconds=1);db.commit()
    code=r.code if expired else r.did+'.invalid'
    assert r.ingress.post('/api/print-agent/pair',headers=PROXY,json={'code':code}).status_code==401


@pytest.mark.parametrize('headers',[
    {},{'X-Forwarded-Proto':'http'},
    {**PROXY,'X-Forwarded-Proto':'https,http'},
    {**PROXY,'X-Forwarded-Host':'other.example'},
    {**PROXY,'Host':'other.example'},
    {**PROXY,'X-Forwarded-Port':'80'},
    {**PROXY,'Forwarded':'proto=https'},
    {**PROXY,'Origin':'null'},
    {**PROXY,'Origin':''},
    {**PROXY,'Origin':'https://'+HOST},
    {**PROXY,'X-PixiePinks-Native-Ingress-Proof':'true'},
    {**PROXY,'X-Print-Agent-Ingress':'true'},
    [('X-Forwarded-Proto','https'),('X-Forwarded-Proto','https')],
])
def test_bad_assertions_and_header_proofs_are_not_authorization(ingress,headers):
    assert ingress.ingress.get('/api/print-agent/config',headers=headers).status_code==403
    assert ingress.ingress.post('/api/print-agent/pair',headers=headers,json={'code':ingress.code}).status_code==403


def test_raw_backend_and_start_command_bypass_cannot_mint_ingress_proof(ingress):
    for scheme in ['http','https']:
        client=TestClient(Hop(main.app,'fd12::9999'),base_url=scheme+'://'+HOST)
        for headers in [PROXY,{**PROXY,'X-PixiePinks-Native-Ingress-Proof':'true'},{}]:
            assert client.get('/api/print-agent/config',headers=headers).status_code==403


def test_unapproved_shared_environment_stays_blocked(proxied,monkeypatch):
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS','')
    monkeypatch.setattr(settings,'PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED',False)
    client=TestClient(Hop(RailwayIsolatedIngress(main.app),'fd12::9999'),base_url='http://'+HOST)
    assert client.get('/api/print-agent/config',headers=PROXY).status_code==403


def test_do_not_mix_ip_allowlist_and_isolated_mode(ingress,monkeypatch):
    monkeypatch.setattr(settings,'PRINT_AGENT_TRUSTED_PROXY_IPS',PEER)
    assert ingress.ingress.get('/api/print-agent/config',headers=PROXY).status_code==403


def test_shared_network_counterexample_requires_infrastructure_isolation(ingress):
    # Deliberately demonstrates the HARD boundary: if the operator enables this
    # mode while a private attacker can still REACH the public entrypoint, that
    # caller can imitate unsigned edge headers. No ASGI marker/HMAC sidecar can
    # repair that. The acceptance probe MUST reject such a deployment.
    client=TestClient(Hop(RailwayIsolatedIngress(main.app),'fd12::9999'),base_url='http://'+HOST)
    response=client.get('/api/print-agent/config',headers=PROXY)
    assert response.status_code==401  # Authentication not bypassed, but TLS origin is ambiguous.


def test_ingress_does_not_rewrite_ordinary_crm_or_lifespan(ingress):
    import asyncio
    seen=[]
    async def capture(scope,receive,send):seen.append(scope)
    wrapped=RailwayIsolatedIngress(capture)
    scope={'type':'http','scheme':'http','path':'/crm/pos','headers':[(b'x-forwarded-proto',b'https')],'client':('fd12::1',123)}
    asyncio.run(wrapped(scope,None,None))
    assert seen[0]['scheme']=='http' and seen[0]['client']==scope['client']
    asyncio.run(wrapped({'type':'lifespan'},None,None))
    assert seen[1]=={'type':'lifespan'}


def test_probe_detects_private_bypass_without_claiming_timeout_proves_isolation():
    import httpx
    from scripts.validate_print_agent_ingress import run_checks
    seen=[]
    def handler(request):
        seen.append(request)
        assert 'authorization' not in request.headers and not request.content
        if request.url.host=='private.railway.internal':
            assert request.headers['x-forwarded-proto']=='https' and request.headers['host']==HOST
            return httpx.Response(401,json={'detail':'Device authentication unavailable'})
        if request.url.scheme=='http':return httpx.Response(308,headers={'location':'https://'+HOST+'/api/print-agent/config'})
        if 'origin' in request.headers:return httpx.Response(403)
        return httpx.Response(401,json={'detail':'Device authentication unavailable'})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    result=run_checks(client,'https://'+HOST,'http://private.railway.internal:8000')
    assert result['status']=='FAILED' and result['checks']['private_network_path']=='FAIL_REACHABLE'
    assert not result['network_isolation_certified'] and len(seen)==4
    def unreachable(request):
        if request.url.host=='private.railway.internal':raise httpx.ConnectError('blocked or incorrect mapping',request=request)
        return handler(request)
    client=httpx.Client(transport=httpx.MockTransport(unreachable))
    result=run_checks(client,'https://'+HOST,'http://private.railway.internal:8000')
    assert result['status']=='OPERATOR_REVIEW_REQUIRED' and not result['network_isolation_certified']


def test_actual_ingress_entrypoint_can_serve_existing_agent_transport(tmp_path):
    import os,shlex,socket,subprocess,time
    from pathlib import Path
    import httpx
    # Import the real entrypoint/wrapper, but substitute a tiny backend to avoid
    # background workers or any Shopify/DB operations in this socket-level test.
    (tmp_path/'main.py').write_text('from fastapi import FastAPI,Request,HTTPException\nfrom print_agent_security import require_native_https\napp=FastAPI()\n@app.get("/api/print-agent/config")\ndef config(request:Request):\n require_native_https(request)\n raise HTTPException(401,"Device authentication unavailable")\n')
    args=shlex.split(Path('Procfile').read_text().removeprefix('web:').strip())
    assert args[1]=='railway_ingress:app' and '--no-proxy-headers' in args
    with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    args[0:1]=[os.sys.executable,'-m','uvicorn']
    args[args.index('--host')+1]='127.0.0.1';args[args.index('--port')+1]=str(port)
    env={**os.environ,'PYTHONPATH':str(tmp_path)+os.pathsep+os.getcwd(),'PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED':'true','PRINT_AGENT_TRUSTED_PROXY_IPS':'','PRINT_AGENT_PUBLIC_ORIGIN':'https://'+HOST,'FORWARDED_ALLOW_IPS':'*'}
    process=subprocess.Popen(args+['--no-access-log'],cwd=tmp_path,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
            for _ in range(100):
                try:client.get('/api/print-agent/config');break
                except httpx.ConnectError:time.sleep(.05)
            assert client.get('/api/print-agent/config',headers={**PROXY,'Host':HOST}).status_code==401
            assert client.get('/api/print-agent/config',headers={'Host':HOST}).status_code==403
    finally:process.terminate();process.wait(timeout=10)


def test_existing_windows_transport_pairs_at_same_https_origin_without_new_headers(ingress):
    import httpx
    from windows_print_agent.transport import Transport
    r=ingress
    def edge(request):
        assert request.url.scheme=='https' and request.url.host==HOST
        assert 'origin' not in request.headers and not any(key.startswith('x-pixiepinks-') for key in request.headers)
        # Model ONLY the administrator-verified sanitized edge inside the
        # isolated boundary. The unchanged native client sends no proof secret.
        headers={**dict(request.headers),**PROXY,'host':HOST}
        response=r.ingress.request(request.method,request.url.path,headers=headers,content=request.content)
        return httpx.Response(response.status_code,headers=dict(response.headers),content=response.content)
    remote=Transport('https://'+HOST,http=httpx.Client(transport=httpx.MockTransport(edge),follow_redirects=False))
    token=remote.pair(r.code);assert token.startswith(r.did+'.')
    with r.sessions() as db:h.manage(db,db.get(User,r.uid),r.did,'activate')
    assert remote.config()['id']==r.did
    with r.sessions() as db:assert db.query(h.HardwareJob).count()==0
