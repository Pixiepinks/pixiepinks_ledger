import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from sqlalchemy import text
from alembic import command
from alembic.config import Config
from test_whatsapp_quick_messages import inbox
from test_meta_ads_ui import configure
from test_meta_ads_sync_investigation import factory,transport_handler
from test_meta_ads import FakeClient
from meta_ads_models import MetaAdsSync as Sync, MetaAdsDaily as Daily
import meta_ads_worker as worker
from models import User


@pytest.mark.parametrize('key,value,code',[
    ('META_ADS_SYNC_ENABLED','false','SYNC_DISABLED'),
    ('META_ADS_SYNC_ENABLED','"true"','INVALID_SYNC_ENABLED'),
    ('META_ADS_ACCOUNT_ID','"109645107"','INVALID_ACCOUNT_ID'),
    ('META_ADS_ACCESS_TOKEN','','MISSING_ACCESS_TOKEN'),
    ('META_ADS_GRAPH_VERSION','"v26.0"','INVALID_GRAPH_VERSION'),
])
def test_409_has_exact_safe_reason_and_never_queues_api_work(inbox,monkeypatch,key,value,code):
    configure(monkeypatch);monkeypatch.setenv(key,value)
    client,sessions,ids,sends,login,csrf=inbox;calls=[]
    monkeypatch.setattr(worker,'tick',lambda *a:calls.append(True))
    response=client.post('/crm/ad-analytics/sync',headers={'X-CSRF-Token':csrf})
    assert response.status_code==409 and response.json()['detail']==code
    assert response.json()['stage']=='PREFLIGHT' and response.json()['api_requests']==0
    assert response.json()['hint'] and not calls and not sends
    assert response.headers['cache-control']=='private, no-store'
    assert 'test-secret' not in response.text


def test_diagnostics_reports_this_runtime_without_meta_calls_and_is_admin_only(inbox,monkeypatch):
    configure(monkeypatch);monkeypatch.setenv('META_ADS_ACCOUNT_ID',' 109645107 ')
    monkeypatch.setenv('META_ADS_SYNC_ENABLED',' TRUE\n');monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA','a'*40)
    client,sessions,ids,sends,login,csrf=inbox
    response=client.get('/crm/ad-analytics/diagnostics');assert response.status_code==200
    data=response.json();assert data['configuration']['enabled'] and data['configuration']['configured']
    assert data['configuration']['account_id']=='act_109645107' and data['revision']=='a'*40
    assert data['sync']['api_attempted'] is None and data['sync']['status']=='NEVER'
    assert 'test-secret' not in response.text and response.headers['cache-control']=='private, no-store'
    with sessions() as db:db.query(User).filter_by(username='quick-manager').one().section_access=['ad_analytics'];db.commit()
    login('quick-manager');assert client.get('/crm/ad-analytics/diagnostics').status_code==403
    assert client.get('/crm/ad-analytics/api').status_code==200
    assert not sends


def test_diagnostics_migration_preserves_existing_cache(inbox,monkeypatch):
    _,sessions,*_=inbox
    configure(monkeypatch)
    assert worker.tick(sessions,force=True,client_factory=FakeClient)=='SUCCESS'
    with sessions() as db:
        db.add(Sync(account_id='act_legacy',status='ERROR',error_code='ADS_PERMISSION_DENIED'));db.commit()
        db.execute(text('ALTER TABLE meta_ads_sync DROP COLUMN diagnostics'));db.commit()
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261109_35');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        row=db.get(Sync,'act_legacy');assert row.error_code=='ADS_PERMISSION_DENIED' and row.diagnostics is None
        assert db.query(Daily).count()==3


def test_browser_sync_surfaces_failure_then_refreshes_success_automatically(inbox,monkeypatch):
    configure(monkeypatch)
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,ids,sends,login,csrf=inbox;mode={'fail':True};calls=[]
    import httpx
    def handler(request):
        calls.append(request.url.path)
        if mode['fail']:return httpx.Response(400,json={'error':{'code':190,'error_subcode':463,'message':'test-secret-do-not-print'}})
        return transport_handler(request)
    real_tick=worker.tick
    monkeypatch.setattr(worker,'tick',lambda sessions:real_tick(sessions,client_factory=factory(handler)))
    def serve(page):
        def handle(route):
            req=route.request;u=urlsplit(req.url)
            response=client.request(req.method,u.path+('?' +u.query if u.query else ''),headers={k:v for k,v in req.headers.items() if k not in {'host','cookie'}},content=req.post_data_buffer,follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',handle)
    errors=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':390,'height':900});serve(page);page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('https://ads.test/crm/ad-analytics')
            api.expect(page.locator('#ads-feedback')).to_contain_text('Report loaded')
            page.locator('#ads-sync').click()
            api.expect(page.locator('#ads-feedback')).to_contain_text('TOKEN_EXPIRED_OR_INVALID')
            api.expect(page.locator('#ads-diagnostics')).to_contain_text('PERMISSIONS')
            assert 'test-secret' not in page.content()
            mode['fail']=False;page.locator('#ads-sync').click()
            api.expect(page.locator('#ads-feedback')).to_contain_text('Synchronization completed')
            api.expect(page.locator('#ads-health')).to_contain_text('Sync SUCCESS')
            api.expect(page.locator('#ads-kpis')).to_contain_text('LKR 100')
            api.expect(page.locator('#ads-sync')).to_be_enabled()
            before=len(calls);page.get_by_text('Sync diagnostics',exact=True).click();page.locator('#ads-check').click()
            api.expect(page.locator('#ads-diagnostics')).to_contain_text('COMPLETE');assert len(calls)==before
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors and not sends
        finally:browser.close()
